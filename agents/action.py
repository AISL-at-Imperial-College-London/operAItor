"""Action agents: turn the planning brief into concrete, validatable commands.

Both return the plan format used by ``jpc_recovery_langgraph.compile_plan``:

    {"commands": [{"type": "START_SPARE", "target": "REFLUX"}, ...], "reasoning": "..."}

``RuleActionAgent`` is the offline baseline. ``LLMActionAgent`` calls a chat
model with ``ainvoke`` (so it never blocks the plant loop) and records latency
and token usage in ``last_usage``.
"""
from __future__ import annotations

import json
import time
from typing import Any, Protocol

from .llm import DEFAULT_MODEL, USAGE, make_llm, message_text, parse_json_object


class ActionAgent(Protocol):
    async def propose(self, context: dict) -> dict: ...


class RuleActionAgent:
    """Conservative heuristics; NO_ACTION (operator advisory) when unsure.

    The rules give an ordered list of candidate plans. The first is proposed; when
    validation or the digital twin rejects it and the reprompter asks again, the
    next untried candidate is proposed (e.g. a deeper throughput cut), so the
    reprompt loop also works offline.
    """

    def __init__(self, min_throughput: float = 60.0, nominal_throughput: float = 100.0):
        self.min_throughput = min_throughput
        self.nominal_throughput = nominal_throughput
        self.last_usage: dict = {}
        self._tried: list = []

    async def propose(self, context: dict) -> dict:
        if context.get("previous_plan") is None:  # first attempt of a new decision cycle
            self._tried = []
        for plan in self.candidates(context):
            if plan["commands"] not in self._tried:
                if self._tried:
                    reason = json.loads(context.get("feedback") or "{}").get("fail_reason")
                    plan = {**plan, "reasoning": f"{plan['reasoning']} (previous proposal rejected: {reason})"}
                self._tried.append(plan["commands"])
                return plan
        return {"commands": [{"type": "NO_ACTION"}],
                "reasoning": "Every rule candidate was rejected by the digital twin; operator advised."}

    def candidates(self, context: dict) -> list[dict]:
        obs, diag = context["observation"], context["diagnosis"]
        rules = context["detection"]["rules"]
        hyps = [h["fault"] for h in diag["hypotheses"]]
        alarms = set(rules["active_alarms"])
        saturated = set(rules["saturated_loops"])
        feed = obs["throughput_target_kmol_h"]
        out: list[dict] = []

        def cuts(why: str, factors: tuple[float, ...]) -> None:
            for f in factors:
                target = round(max(0.5 * self.min_throughput, f * feed), 1)
                out.append({"commands": [{"type": "SET_THROUGHPUT", "value": target}],
                            "reasoning": f"{why}: cut throughput to {target:g} kmol/h ({100 * (1 - f):.0f}% lower)."})

        for train in rules["failed_trains"]:
            info = obs["pump_trains"][train]
            if info["active_pump"] != info["spare"] and not info["spare_failed"]:
                out.append({"commands": [{"type": "START_SPARE", "target": train}],
                            "reasoning": f"Duty pump on {train} failed; standby {info['spare']} is healthy."})

        if ({"R_P_H", "R_P_HH"} & alarms or "R_PIC" in saturated) and obs["auxiliaries"]["H2_BDV"] < 0.3:
            for v in (0.3, 0.6):
                out.append({"commands": [{"type": "OPERATE_AUXILIARY", "target": "H2_BDV", "value": v}],
                            "reasoning": f"Reactor pressure high with vent PCV out of range: open H2 blowdown to {v:g}."})

        if ("condenser_cooling_loss" in hyps[:2] or {"COL_P_H", "COL_P_HH"} & alarms) and feed > self.min_throughput:
            cuts("Column pressure rising / condenser limited", (0.8, 0.65, 0.5))

        # Load cuts need hard evidence (alarms / saturated loops): a PCA-only hypothesis can be
        # caused by the reduced-load operating point itself and would keep the plant cut back.
        if ({"R_L_H", "R_L_HH"} & alarms or ("cstr_outlet_capacity_loss" in hyps[:1] and "R_LIC" in saturated))                 and feed > self.min_throughput:
            cuts("Reactor inventory rising (outlet limited)", (0.7, 0.5, 0.35))
        if out:
            return out

        # Unwind earlier emergency moves once their reason has gone (otherwise the plant
        # stays at an abnormal operating point after the fault clears).
        r_pic = obs["controllers"]["R_PIC"]
        if (obs["auxiliaries"]["H2_BDV"] > 0 and not {"R_P_H", "R_P_HH"} & alarms
                and (r_pic["pv"] or 0.0) < r_pic["setpoint"]):
            return [{"commands": [{"type": "OPERATE_AUXILIARY", "target": "H2_BDV", "value": 0.0}],
                     "reasoning": "Reactor pressure back below setpoint: close the H2 blowdown again."}]
        col = obs["controllers"]["COL_PIC"]
        # Hysteresis: restore load only when the alarms and loops that justified the cut are
        # quiet and column pressure is at or below setpoint (otherwise cut/restore oscillates).
        if (feed < self.nominal_throughput - 1e-6 and not {"COL_P_H", "COL_P_HH", "R_L_H", "R_L_HH"} & alarms
                and not {"COL_PIC", "R_LIC"} & saturated
                and (col["pv"] or 0.0) <= col["setpoint"]):
            target = round(min(self.nominal_throughput, 1.2 * feed), 1)
            return [{"commands": [{"type": "SET_THROUGHPUT", "value": target}],
                     "reasoning": "Plant back under control: step throughput back towards nominal."}]

        return [{"commands": [{"type": "NO_ACTION"}],
                 "reasoning": f"Fault '{diag['summary']}' detected but no safe automatic response; operator advised."}]


ACTION_SYSTEM_PROMPT = """
You are the **Corrective Action Agent** for an integrated isopropanol→acetone plant:
an endothermic liquid-phase CSTR (R-101) with H2 vent/blowdown, feeding a 10-tray
distillation column (T-201) with condenser, reflux drum and reboiler, and five
duty/standby pump trains.

The monitoring agent detected a fault (PCA Hotelling T²/SPE + alarm/pump/controller
rules) and ranked hypotheses; the planning agent turned them into a brief with a
target fault, an objective, KG-grounded candidate levers and constraints. You also get
the live operator snapshot, the action catalog with bounds and the KG evidence.

# How to use the plan and KG (MANDATORY)
- Work towards the brief's objective using its candidate levers; deviate only if the
  snapshot contradicts the brief, and say why.
- FaultMode nodes (fault_*) list symptoms and affected parameters; controller_* nodes
  say what a loop controls; alarm_*/SIF_* give limits and safety overrides; action_*
  nodes say what operators may act on. Cite the node ids you rely on in `kg_evidence`.

# Output (STRICT JSON, no markdown)
{"commands": [command, ...], "reasoning": "<brief KG-grounded justification>",
 "kg_evidence": ["node_id", ...]}
Each command is one of:
  {"type": "NO_ACTION"}
  {"type": "START_SPARE", "target": <pump train>}
  {"type": "SET_THROUGHPUT", "value": <kmol/h>}
  {"type": "SET_SETPOINT", "target": <loop id>, "value": <number>}
  {"type": "OPERATE_AUXILIARY", "target": "H2_BDV" | "EMERGENCY_COOLING", "value": <0..1>}

# Hard rules
1. Stay inside the catalog bounds and units; never touch faults, utilities or SIFs.
2. A failed duty pump with a healthy standby → START_SPARE that train.
3. Do not push a loop whose output is already saturated; relieve the load instead.
4. The reactor is endothermic: lowering feed does NOT cool it.
5. Prefer small, reversible changes. After a rejected proposal, use the feedback and
   propose something DIFFERENT.
6. If no lever addresses the fault (e.g. a sensor fault), return NO_ACTION and say what
   the operator should check.
""".strip()


def slim_observation(obs: dict) -> dict:
    ctrl = {k: {f: v[f] for f in ("mode", "setpoint", "pv", "last_output", "sp_min", "sp_max",
                                  "sp_unit", "mv_min", "mv_max", "mv_unit")}
            for k, v in obs["controllers"].items()}
    return {"time_min": round(obs["time_min"], 2),
            "measurements": {k: round(v, 4) for k, v in obs["measurements"].items() if not k.startswith("pump_")},
            "controllers": ctrl, "throughput_target_kmol_h": obs["throughput_target_kmol_h"],
            "auxiliaries": obs["auxiliaries"], "pump_trains": obs["pump_trains"],
            "active_alarms": {a: s for a, s in obs["alarm_states"].items() if s != "NORMAL"},
            "sif_latched": list(obs["sif_latched"])}


class LLMActionAgent:
    def __init__(self, model: str = DEFAULT_MODEL, timeout: float = 120.0, llm: Any = None):
        self.model = model
        self.llm = llm if llm is not None else make_llm(model, timeout, json_mode=True)
        self.last_usage: dict = {}

    async def propose(self, context: dict) -> dict:
        catalog = context["catalog"]
        user = {
            "plan_brief": context.get("brief"),
            "snapshot": slim_observation(context["observation"]),
            "monitoring": {k: context["detection"][k] for k in ("pca", "rules", "detectors")},
            "diagnosis": {k: context["diagnosis"].get(k) for k in ("hypotheses", "contributors", "summary")},
            "kg_evidence": context.get("knowledge") or [],
            "action_catalog": {"throughput_bounds_kmol_h": catalog["throughput_bounds_kmol_h"],
                               "auxiliaries": catalog["auxiliaries"],
                               "setpoint_bounds": {k: [v["sp_min"], v["sp_max"], v["sp_unit"]]
                                                   for k, v in catalog["controllers"].items()},
                               "pump_trains": list(catalog["pump_trains"])},
            "previous_proposal": context.get("previous_plan"),
            "evaluation_feedback": context.get("feedback") or None,
        }
        t0 = time.perf_counter()
        msg = await self.llm.ainvoke([("system", ACTION_SYSTEM_PROMPT), ("human", json.dumps(user, default=str))])
        text = message_text(msg)
        self.last_usage = {**USAGE.record("action", self.model, msg),
                           "latency_s": time.perf_counter() - t0, "raw": text[:4000]}
        return parse_json_object(text)


def normalise_plan(plan: Any) -> tuple[dict, list[str]]:
    """Strip LLM extras (kg_evidence, null fields) into the compile_plan schema."""
    if not isinstance(plan, dict):
        return {"commands": [], "reasoning": ""}, []
    cites = [str(x) for x in plan.get("kg_evidence", []) or []]
    cmds = [{k: v for k, v in c.items() if v is not None} for c in plan.get("commands", []) or []
            if isinstance(c, dict)]
    return {"commands": cmds, "reasoning": str(plan.get("reasoning", ""))}, cites
