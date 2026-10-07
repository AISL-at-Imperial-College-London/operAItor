"""Runs the recovery graph continuously next to the live plant (server mode).

    PlantService._run_loop  --latest observation-->  AsyncSupervisor.run → recovery graph
           ^                                                │ monitoring → KG → planning → action → twin
           +-------------- apply_action (locked) <----------+

A slow decision (LLM call, twin rollout) never pauses the simulation; when the
agents are busy, intermediate observations are dropped and the newest is used.

Modes
    off         no monitoring
    advisory    monitor, plan and propose; the operator approves or dismisses in the UI
    autonomous  monitor, plan, propose and apply validated actions
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, replace
import itertools
from pathlib import Path

from fdd import DEFAULT_MODEL_PATH, PCAMonitor
from integrated_plant.actions import SupervisoryAction
from integrated_plant.alarms import AlarmState
from server.plant_service import PlantService

from .kg import KnowledgeGraph
from .llm import USAGE
from .monitoring import MonitoringAgent
from .recovery_graph import (AgentConfig, RunContext, build_recovery_graph, clone_env, make_action_agent,
                             make_config, make_planner)

MODES = ("off", "advisory", "autonomous")
_STAGE = {"reject": "rejected"}


class AsyncSupervisor:
    def __init__(self, service: PlantService, cfg: AgentConfig | None = None, mode: str = "autonomous",
                 kg: KnowledgeGraph | None = None, pca: PCAMonitor | None = None,
                 action_agent=None, planner=None):
        # Live sessions can run many faults (campaigns), so do not cap the action count.
        self.cfg = replace(cfg or make_config("passive"), max_actions=10**9)
        self.service = service
        self.mode = mode
        self.kg = kg or KnowledgeGraph()
        if pca is None and Path(DEFAULT_MODEL_PATH).exists():
            pca = PCAMonitor.load(DEFAULT_MODEL_PATH)
        self.monitor = MonitoringAgent(pca if self.cfg.use_pca else None, persistence=self.cfg.persistence,
                                       cooldown_min=self.cfg.cooldown_min, rules_trigger=self.cfg.rules_trigger, quality_needs_pca=self.cfg.quality_needs_pca)
        self.action_agent = action_agent or make_action_agent(self.cfg)
        self.planner = planner or make_planner(self.cfg)
        self.pending: dict[int, dict] = {}
        self._ids = itertools.count(1)
        self.ctx = RunContext(self.cfg, self.monitor, self.kg if self.cfg.use_kg else None, self.planner,
                              self.action_agent, self._snapshot_env, self._dispatch, self._emit,
                              on_decision_start=lambda: service.set_agent_hold(True),
                              on_decision_end=lambda: service.set_agent_hold(False))
        self.graph = build_recovery_graph(self.ctx)
        self._task: asyncio.Task | None = None
        self.last_cycle_t: float | None = None     # sim time of the last finished monitoring cycle
        self._last_applied_t: float | None = None  # sim time of the last action the agents applied

        service.command_handlers.update({"agent_mode": self._cmd_mode, "agent_approve": self._cmd_approve,
                                         "agent_dismiss": self._cmd_dismiss, "diagnose_now": self._cmd_diagnose})
        self._last_monitor: dict | None = None  # latest monitoring result, for on-demand LLM diagnosis
        self._last_obs: dict | None = None
        self._diagnoser = None
        service.on_load.append(self._on_load)
        service.hello_extras.append(self._hello)

    # ------------------------------------------------------------ loop
    def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name="agent-supervisor")

    async def run(self) -> None:
        last_t = None
        while True:
            obs, catalog = await self.service.next_observation()
            if self.mode == "off" or obs["time_min"] == last_t:
                continue
            last_t = obs["time_min"]
            self._last_obs = obs
            try:
                await self.graph.ainvoke({"observation": obs, "catalog": catalog})
                if self.mode == "autonomous":
                    await self._acknowledge_returned_alarms()
                    await self._request_sif_reset()
            except Exception as exc:  # keep supervising even if one cycle fails
                await self.service.notify(f"[agent] cycle failed: {type(exc).__name__}: {exc}", "error")
            finally:
                await self.service.set_agent_hold(False)
                self.last_cycle_t = last_t

    # ------------------------------------------------------------ SIF reset
    async def _request_sif_reset(self) -> None:
        """While non-trip SIFs are latched, request a reset every cycle (README: SIF reset is an
        operator/agent action). The SIF logic only resets a latch once its variable is back inside the
        reset band, and re-trips immediately if the cause is still there. A plant-trip latch is left
        for the operator (restart)."""
        env = self.service.env
        if not self.cfg.sif_reset or env is None or env.done:
            return
        sr = env.last_safety_result
        latched = tuple(sr.latched_causes)
        if latched and not sr.plant_trip_latched:
            self.service._pending_reset_sif = True
            self._last_applied_t = self.service.status()["time_min"]  # the agents are handling this incident
            if latched != getattr(self, "_reset_reported", ()):
                await self.service.broadcast({"type": "agent", "stage": "sif_reset", "cycle": self.ctx.cycle,
                                              "time_min": self.service.status()["time_min"], "alarms": list(latched),
                                              "reason": "SIF reset requested (resets once back inside the band)"})
        self._reset_reported = latched

    # ------------------------------------------------------------ alarm acknowledgement
    def _alarms_in(self, state: AlarmState) -> list:
        env = self.service.env
        return [] if env is None else [s for s in env.alarms.status.values() if s.state == state]

    async def _acknowledge_returned_alarms(self) -> None:
        """Acknowledge alarms that returned to normal while the agents were handling the plant.

        An alarm that returns to normal stays on the list as RTN_UNACK until someone acknowledges
        it. If the agents applied an action while it was present (or up to one twin look-ahead
        before it came on, i.e. it belongs to the incident being handled), the agents acknowledge
        it, which clears it from the Alarms page. Returned alarms with no agent involvement are
        left for the operator.
        """
        if self._last_applied_t is None:
            return
        ids = [s.definition.alarm_id for s in self._alarms_in(AlarmState.RTN_UNACK)
               if s.first_active_min is not None
               and self._last_applied_t >= s.first_active_min - self.cfg.twin_horizon_min]
        if ids:
            await self._acknowledge(ids, "returned to normal after the agents' recovery action")

    async def _acknowledge(self, ids: list[str], reason: str) -> None:
        result = await self.service.apply_action(
            SupervisoryAction(acknowledge_alarm_ids=tuple(ids), note="agent alarm acknowledgement"),
            source="action-agent")
        await self.service.broadcast({"type": "agent", "stage": "alarm_ack", "cycle": self.ctx.cycle,
                                      "time_min": self.service.status()["time_min"], "alarms": ids,
                                      "reason": reason, "result": result})

    # ------------------------------------------------------------ graph callbacks
    async def _emit(self, event: dict) -> None:
        """Translate graph events into the console's 'monitor' / 'agent' messages."""
        kind = event["event"]
        if kind == "monitor":
            self._last_monitor = event
            await self.service.broadcast({"type": "monitor", **event["detection"], "diagnosis": event["diagnosis"]})
            return
        body = {k: v for k, v in event.items() if k not in ("event", "t")}
        body["wall"] = time.time()  # wall-clock stamp: decision / LLM / twin overheads
        await self.service.broadcast({"type": "agent", "stage": _STAGE.get(kind, kind), "time_min": event["t"], **body})
        if kind in ("plan", "proposal") and (self.cfg.proposer == "llm" or self.cfg.planner == "llm"):
            await self.service.broadcast({"type": "llm_usage", **USAGE.summary()})

    async def _snapshot_env(self):
        async with self.service.lock:
            return await asyncio.to_thread(clone_env, self.service.env)

    async def _dispatch(self, action: SupervisoryAction, info: dict) -> dict:
        base = {"type": "agent", "time_min": self.service.status()["time_min"], "t_obs": info["t_obs"],
                "cycle": self.ctx.cycle,
                "plan": info["plan"], "diagnosis": info["diagnosis"]["summary"],
                "objective": (info.get("brief") or {}).get("objective"), "kind": info.get("kind", "restoring")}
        if self.mode == "autonomous":
            # The agents take ownership of the incident: acknowledge the alarms that are standing.
            standing = tuple(s.definition.alarm_id for s in self._alarms_in(AlarmState.ACTIVE_UNACK))
            action = replace(action, acknowledge_alarm_ids=tuple(action.acknowledge_alarm_ids) + standing,
                             note=f"agent {action.note}".strip())
            result = await self.service.apply_action(action, source="action-agent")
            self._last_applied_t = self.service.status()["time_min"]
            await self.service.broadcast({**base, "stage": "applied", "result": result})
            if standing:
                await self.service.broadcast({"type": "agent", "stage": "alarm_ack", "cycle": self.ctx.cycle,
                                              "time_min": self._last_applied_t, "alarms": list(standing),
                                              "reason": "taken over by the action agent (the alarm leaves "
                                                        "the list once its variable is back within limits)"})
            return {"status": "applied", **result}
        pid = next(self._ids)
        self.pending[pid] = {"id": pid, "action": action, **base}
        await self.service.broadcast({**base, "stage": "pending", "proposal_id": pid, "action": asdict(action)})
        return {"status": "pending", "proposal_id": pid}

    # ------------------------------------------------------------ UI commands
    async def _cmd_mode(self, msg: dict) -> dict:
        mode = msg.get("mode")
        if mode not in MODES:
            return {"ok": False, "summary": f"mode must be one of {MODES}"}
        self.mode = mode
        await self.service.broadcast({"type": "agent_mode", "mode": mode})
        return {"ok": True, "summary": f"agent mode {mode}"}

    async def _cmd_diagnose(self, msg: dict) -> dict:
        """Ask the LLM diagnosis agent to explain the latest PCA / rule evidence now."""
        if self._last_monitor is None:
            return {"ok": False, "summary": "no monitoring result yet"}
        if self._diagnoser is None:
            try:
                from .diagnosis_llm import LLMDiagnoser
                self._diagnoser = LLMDiagnoser(self.cfg.llm_model, self.cfg.llm_timeout_s, kg=self.kg)
            except Exception as exc:  # no LLM configured
                return {"ok": False, "summary": f"LLM diagnosis unavailable: {exc}"}
        det, diag = self._last_monitor["detection"], self._last_monitor["diagnosis"]
        verdict = await self._diagnoser.diagnose(det, diag.get("heuristic") or diag, self._last_obs)
        await self.service.broadcast({"type": "agent", "stage": "llm_diagnosis", "time_min": det["time_min"],
                                      "on_demand": True, "cycle": self.ctx.cycle, **verdict})
        await self.service.broadcast({"type": "llm_usage", **USAGE.summary()})
        return {"ok": verdict.get("ok", False),
                "summary": verdict.get("summary") or verdict.get("error") or "diagnosis done"}

    async def _cmd_approve(self, msg: dict) -> dict:
        p = self.pending.pop(int(msg["proposal_id"]), None)
        if p is None:
            return {"ok": False, "summary": "proposal no longer pending"}
        result = await self.service.apply_action(p["action"], source="operator-approved agent")
        self._last_applied_t = self.service.status()["time_min"]
        await self.service.broadcast({"type": "agent", "stage": "approved", "proposal_id": p["id"],
                                      "time_min": self.service.status()["time_min"], "result": result})
        return result

    async def _cmd_dismiss(self, msg: dict) -> dict:
        p = self.pending.pop(int(msg["proposal_id"]), None)
        await self.service.broadcast({"type": "agent", "stage": "dismissed", "proposal_id": int(msg["proposal_id"]),
                                      "time_min": self.service.status()["time_min"]})
        return {"ok": p is not None, "summary": "dismissed"}

    async def _on_load(self) -> None:
        self._last_applied_t = None
        self.last_cycle_t = None
        self.monitor.memory.reset()
        self.pending.clear()
        self.ctx.actions_taken = 0

    def info(self) -> dict:
        uses_llm = self.cfg.proposer == "llm" or self.cfg.planner == "llm"
        return {"mode": self.mode, "preset": self.cfg.name, "planner": type(self.planner).__name__,
                "llm_model": self.cfg.llm_model if uses_llm else None, "llm_usage": USAGE.summary()["total"],
                "actor": type(self.action_agent).__name__ if self.action_agent else None,
                "twin": self.cfg.twin, "twin_horizon_min": self.cfg.twin_horizon_min,
                "reprompt_max": self.cfg.reprompt_max, "kg": self.cfg.use_kg,
                "acceptance": self.cfg.acceptance, "fallback_to_safe": self.cfg.fallback_to_safe,
                "pca_loaded": self.monitor.pca is not None}

    def _hello(self) -> dict:
        return {"agent": {**self.info(), "modes": list(MODES),
                          "pca_meta": self.monitor.pca.meta if self.monitor.pca else None,
                          "pending": [{k: v for k, v in p.items() if k != "action"} | {"proposal_id": p["id"]}
                                      for p in self.pending.values()]}}
