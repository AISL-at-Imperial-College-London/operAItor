"""Asyncio service that owns the live ``EpisodeRunner``.

It is the single place where the simulator is mutated, so the UI (WebSocket),
the agent supervisor and scripts all go through it:

* the run loop steps the plant on a worker thread (the DAE solve is CPU bound),
  so the event loop stays free for UI messages and agent work;
* an ``asyncio.Lock`` serialises plant steps, operator actions, agent actions
  and fault injections;
* every tick is broadcast to subscribers (WebSocket clients) and the latest
  observation is offered to the agents with "latest wins" semantics;
* in operator mode "human" the agents stand down and a human test (server/human_test.py)
  replays a benchmark scenario for a person to answer, scored like the benchmark.

No FastAPI imports here: the benchmark and the tests use it without a server.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, is_dataclass, replace
from enum import Enum
import math
import os
from pathlib import Path
import time
from typing import Any, Awaitable, Callable

from operaitor.plant import (
    ActuatorFault, CURATED_EPISODES, EpisodeRunner, EpisodeTiming, IntegratedPlantDAE,
    PUMP_IDS, PUMP_TRAINS, PumpFault, RuntimeInputEvent, SensorFault, SupervisoryAction,
    curated_episode, nominal_episode, normal_variability_episode, surprise_episode,
)
from operaitor.plant.scenario import disturbance_step

SCENARIOS = ["nominal", "variability", "surprise", *CURATED_EPISODES]
# What the console's New episode offers: faults are injected from the Faults drawer, the benchmark scenarios
# run as a human test or a pipeline benchmark. The single-fault episodes above stay loadable (tests, scripts).
CONSOLE_SCENARIOS = ["nominal", "variability", "surprise"]
DISTURBANCES = {
    "fresh_feed_temperature_K": "Feed temperature",
    "cooling_water_temperature_K": "Cooling-water temperature",
    "steam_pressure_bar": "Steam-header pressure",
    "cstr_heating_utility_temperature_K": "CSTR heating-utility temperature",
    "ambient_temperature_K": "Ambient temperature",
}
ACTUATOR_TARGETS = ["fresh_ipa_feed", "cstr_outflow", "cstr_heater_factor", "cstr_vent_valve",
                    "cstr_bdv_valve", "reflux_flow", "steam_factor", "bottoms_flow",
                    "distillate_flow", "condenser_cooling_valve"]
SENSOR_TARGETS = ["cstr_pressure_bar", "cstr_temperature_K", "cstr_holdup_kmol", "column_pressure_bar",
                  "top_temperature_K", "bottom_temperature_K", "reboiler_holdup_kmol", "drum_holdup_kmol"]
# Actuators that are valve openings (0-1): a capacity ramp degrades their travel, not their nominal value.
VALVE_TARGETS = {"cstr_vent_valve", "cstr_bdv_valve", "condenser_cooling_valve", "steam_factor"}
PROCESS_FAULTS = ["catalyst_activity_loss", "cstr_liquid_leak", "reboiler_liquid_leak",
                  "drum_liquid_leak", "condenser_cooling_loss"]


def jsonable(obj: Any) -> Any:
    """JSON-safe copy: NaN/inf -> None, tuples -> lists, enums/dataclasses unpacked."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        return jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, deque)):
        return [jsonable(v) for v in obj]
    if hasattr(obj, "item"):  # numpy scalar
        return jsonable(obj.item())
    return str(obj)


# Bump when the WebSocket messages change; the console warns if the backend is older.
API_VERSION = 9


class PlantService:
    TIMING = EpisodeTiming(plant_step_min=0.1, decision_interval_min=0.5, control_interval_min=1.0)

    def __init__(self, history_len: int = 1440):
        self.plant = IntegratedPlantDAE()
        self.env: EpisodeRunner | None = None
        self.lock = asyncio.Lock()
        self.running = False
        self.tick_delay_s = 0.0
        # Pacing: sleep pace_sleep_s after every pace_every plant iterations (0.5 sim-min each).
        self.pace_every, self.pace_sleep_s, self._iterations = 10, 0.05, 0
        # Hold the plant while the agents are mid-decision (KG → plan → act → twin → reprompt),
        # so their action lands on the state they analysed. Released by the supervisor or a timeout.
        self.hold_for_agents = True
        # LLM calls + several 30-min twin rollouts can take minutes; release the plant only
        # after this long (and say so), so a stuck decision never freezes it forever.
        self.hold_timeout_s = 600.0
        self._hold_timeout_reported = False
        self._agent_hold_since: float | None = None
        self.config = {"scenario": "nominal", "horizon_min": 360.0, "seed": 20261004, "stop_on_trip": True}
        self.trend: deque = deque(maxlen=history_len)
        self.history: deque = deque(maxlen=history_len)   # measurements + loop OP/SP (chat agent trends)
        self.monitor: deque = deque(maxlen=history_len)
        # "agents": the agent supervisor runs; "human": agents off, a person operates the plant.
        self.operator_mode = "agents"
        self.human_test = None  # HumanTest while one is set up (human_test.py)
        # Reports (human tests, pipeline benchmarks) are written here (None: not written, e.g. on the hosted app).
        _dir = os.environ.get("PLANT_CAMPAIGN_DIR", "campaign_results")
        self.campaign_dir: Path | None = Path(_dir) if _dir else None
        self.agent_log: deque = deque(maxlen=600)
        self.messages: deque = deque(maxlen=50)
        self._subscribers: set[asyncio.Queue] = set()
        self._obs_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._wake = asyncio.Event()
        self._pending_reset_sif = False
        self._task: asyncio.Task | None = None
        # Extension points used by the agent supervisor.
        self.command_handlers: dict[str, Callable[[dict], Awaitable[dict]]] = {}
        self.on_load: list[Callable[[], Awaitable[None]]] = []
        self.hello_extras: list[Callable[[], dict]] = []

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self.env is None:
            await self.load(self.config)
        self._task = asyncio.create_task(self._run_loop(), name="plant-loop")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _build_spec(self, cfg: dict):
        mode, horizon, seed = cfg["scenario"], float(cfg["horizon_min"]), int(cfg["seed"])
        if mode == "nominal":
            spec = nominal_episode(horizon)
        elif mode == "variability":
            spec = normal_variability_episode(horizon, seed)
        elif mode == "surprise":
            spec = surprise_episode(horizon, seed)
        elif mode in CURATED_EPISODES:
            spec = curated_episode(mode, horizon)
        else:
            raise ValueError(f"unknown scenario {mode!r}")
        return replace(spec, terminate_on_plant_trip=bool(cfg["stop_on_trip"]),
                       allow_recovery_termination=False)

    async def load(self, cfg: dict) -> None:
        cfg = {**self.config, **{k: v for k, v in cfg.items() if k in self.config}}
        spec = self._build_spec(cfg)
        async with self.lock:
            self.running = False
            self.config = cfg
            self.env = await asyncio.to_thread(EpisodeRunner, self.plant, spec, timing=self.TIMING)
            self.trend.clear()
            self.history.clear()
            self.monitor.clear()
            self.agent_log.clear()
            self._pending_reset_sif = False
            self.human_test = None  # a new episode cancels a human test
            snap = self.snapshot()
            self.trend.append(snap["trend"])
            self.history.append(self._history_row(snap))
        for hook in self.on_load:
            await hook()
        await self.broadcast({"type": "reset", "hello": self.hello()})
        self._offer_observation()
        await self.notify(f"Loaded episode {spec.name}")

    # ------------------------------------------------------------ pub/sub
    def subscribe(self, maxsize: int = 256) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def broadcast(self, event: dict) -> None:
        event = jsonable(event)
        if event.get("type") == "monitor":
            self.monitor.append(event)
        elif event.get("type") == "agent":
            self.agent_log.append(event)
        for q in list(self._subscribers):
            if q.full():  # slow client: drop its oldest message rather than block the plant
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(event)

    async def notify(self, text: str, level: str = "info") -> None:
        msg = {"type": "message", "level": level, "text": text,
               "time_min": 60.0 * self.env.t_h if self.env else 0.0}
        self.messages.append(msg)
        await self.broadcast(msg)

    def _offer_observation(self) -> None:
        """Latest-wins hand-off to the agents: a stale observation is replaced."""
        if self.env is None:
            return
        item = (self.env.observation(), self.env.action_catalog())
        if self._obs_queue.full():
            self._obs_queue.get_nowait()
        self._obs_queue.put_nowait(item)

    async def next_observation(self) -> tuple[dict, dict]:
        return await self._obs_queue.get()

    # ------------------------------------------------------------ stepping
    async def _tick(self) -> None:
        async with self.lock:
            env = self.env
            if env is None or env.done:
                self.running = False
                return
            action = SupervisoryAction(reset_sifs=self._pending_reset_sif,
                                       note="console_sif_reset" if self._pending_reset_sif else "")
            self._pending_reset_sif = False
            await asyncio.to_thread(env.step, action)
            snap = self.snapshot()
            self.trend.append(snap["trend"])
            self.history.append(self._history_row(snap))
            if env.done:
                self.running = False
        await self.broadcast({"type": "snapshot", **snap})
        self._offer_observation()
        if env.done:
            await self.notify(f"Episode ended: {env.outcomes.state.status.value} ({env.outcomes.state.reason})", "warn")
        if self.human_test is not None:
            await self.human_test.tick()

    @staticmethod
    def _history_row(snap: dict) -> dict:
        row = {"t": snap["time_min"], **snap["measurements"]}
        for loop, c in snap["controllers"].items():
            row[f"op.{loop}"], row[f"sp.{loop}"] = c["last_output"], c["setpoint"]
        for k in ("feed", "outflow", "reflux", "distillate", "bottoms", "heater", "steam", "cooling", "pcv", "bdv", "h2"):
            row[k] = snap["trend"][k]
        return row

    async def _run_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            if not self.running:
                self._wake.clear()
                await self._wake.wait()
                continue
            if self.held:
                await asyncio.sleep(0.1)
                continue
            if self._agent_hold_since is not None and self.hold_for_agents and not self._hold_timeout_reported:
                self._hold_timeout_reported = True
                await self.notify(f"Agents still deciding after {self.hold_timeout_s:.0f} s: plant released; "
                                  "it runs on while the decision finishes", "warn")
            t0 = loop.time()
            await self._tick()
            self._iterations += 1
            pace = self.pace_sleep_s if self.pace_every > 0 and self._iterations % self.pace_every == 0 else 0.0
            await asyncio.sleep(max(0.0, self.tick_delay_s - (loop.time() - t0)) + pace)

    @property
    def held(self) -> bool:
        if not self.hold_for_agents or self._agent_hold_since is None:
            return False
        if time.monotonic() - self._agent_hold_since > self.hold_timeout_s:
            return False  # never let a stuck decision freeze the plant forever
        return True

    async def set_agent_hold(self, on: bool) -> None:
        was = self.held
        self._agent_hold_since = time.monotonic() if on else None
        self._hold_timeout_reported = False
        if self.held != was:
            await self.broadcast({"type": "status", **self.status()})

    def set_running(self, running: bool) -> None:
        self.running = bool(running) and self.env is not None and not self.env.done
        if self.running:
            self._wake.set()

    async def step(self, minutes: float) -> None:
        self.running = False
        n = max(1, int(round(minutes / self.TIMING.decision_interval_min)))
        for _ in range(n):
            if self.env is None or self.env.done:
                break
            await self._tick()

    # ------------------------------------------------------------ actions
    async def apply_action(self, action: SupervisoryAction, source: str = "operator") -> dict:
        async with self.lock:
            if self.env is None:
                return {"ok": False, "summary": "no episode loaded"}
            result = self.env.apply_action(action)
            snap = self.snapshot()
        if self.human_test is not None and source == "operator":
            self.human_test.record_action(snap["time_min"], action.note or "action", result.summary, result.ok)
        await self.broadcast({"type": "snapshot", **snap})
        await self.notify(f"[{source}] {result.summary}", "info" if result.ok else "error")
        return {"ok": result.ok, "summary": result.summary,
                "accepted": list(result.accepted), "rejected": list(result.rejected)}

    async def handle_command(self, msg: dict) -> dict:
        """Dispatch a UI/remote command. Returns an ack dict."""
        kind = msg.get("type")
        try:
            if kind == "load":
                await self.load(msg)
            elif kind == "run":
                if self.env is None or self.env.done:
                    await self.load(self.config)
                self.set_running(True)
            elif kind == "pause":
                self.set_running(False)
            elif kind == "step":
                await self.step(float(msg.get("minutes", 1.0)))
            elif kind == "speed":
                self.tick_delay_s = max(0.0, float(msg.get("delay_ms", 0)) / 1000.0)
            elif kind == "pace":
                self.pace_every = max(0, int(msg.get("every", self.pace_every)))
                self.pace_sleep_s = max(0.0, float(msg.get("sleep_s", self.pace_sleep_s)))
            elif kind == "hold_for_agents":
                self.hold_for_agents = bool(msg.get("on", True))
            elif kind == "ack_all":
                return await self.apply_action(SupervisoryAction(acknowledge_all_alarms=True, note="acknowledge all"))
            elif kind == "reset_sif":
                self._pending_reset_sif = True
                if self.human_test is not None:
                    self.human_test.record_action(self.status()["time_min"], "SIF reset", "SIF reset requested", True)
                await self.notify("SIF reset requested; applied on next plant tick")
            elif kind == "action":
                return await self.apply_action(self._parse_action(msg.get("action", {})))
            elif kind == "inject":
                if self._blind():
                    return {"ok": False, "summary": "manual injections are off during a blind human test"}
                return await self._inject(msg)
            elif kind == "clear_injections":
                if self._blind():
                    return {"ok": False, "summary": "injections cannot be cleared during a blind human test"}
                await self._clear_injections()
            elif kind == "operator_mode":
                return await self.set_operator_mode(msg.get("mode"))
            elif kind == "human_test_start":
                return await self.start_human_test(msg)
            elif kind == "human_test_stop":
                return await self.stop_human_test()
            elif kind in ("agent_mode", "agent_approve", "diagnose_now") and self.operator_mode == "human":
                return {"ok": False, "summary": "agents are off in human-operator mode; switch to Agents first"}
            elif kind in self.command_handlers:
                return await self.command_handlers[kind](msg)
            else:
                return {"ok": False, "summary": f"unknown command {kind!r}"}
        except Exception as exc:
            await self.notify(f"{kind}: {exc}", "error")
            return {"ok": False, "summary": str(exc)}
        await self.broadcast({"type": "status", **self.status()})
        return {"ok": True, "summary": kind}

    @staticmethod
    def _parse_action(d: dict) -> SupervisoryAction:
        allowed = {"throughput_target", "setpoints", "modes", "manual_outputs", "auxiliary_commands",
                   "start_spares", "select_primaries", "acknowledge_alarm_ids", "note"}
        bad = set(d) - allowed
        if bad:
            raise ValueError(f"fields not available to operators: {sorted(bad)}")
        kw = dict(d)
        for k in ("start_spares", "select_primaries", "acknowledge_alarm_ids"):
            if k in kw:
                kw[k] = tuple(kw[k])
        return SupervisoryAction(**kw)

    # ------------------------------------------------------------ master injections
    async def _inject(self, m: dict, quiet: bool = False) -> dict:
        """quiet: a blind human test; nothing about the fault reaches the console."""
        async with self.lock:
            env = self.env
            if env is None or env.done:
                raise RuntimeError("load a live episode before injecting")
            t = env.t_h
            end = None if float(m.get("duration_min", 0)) <= 0 else t + float(m["duration_min"]) / 60.0
            kind, stamp = m["kind"], f"{60 * t:.1f}min"
            if kind == "disturbance":
                target = m["target"]
                if target not in DISTURBANCES:
                    raise ValueError(target)
                if "multiplier" in m:  # explicit custom scaling (legacy)
                    mode, value, label = "multiply", float(m["multiplier"]), f"×{float(m['multiplier']):g}"
                else:  # standard normal-disturbance step (+3 K cooling water, +10 % steam pressure, ...)
                    mode, value, label = disturbance_step(target, 1.0 if float(m.get("direction", 1)) >= 0 else -1.0)
                env.runtime_inputs.add(RuntimeInputEvent(
                    f"manual_{target}_{label}_{stamp}", "disturbance", target, t,
                    end if end is not None else t + 0.25, mode, value))
                text = f"{DISTURBANCES[target]} {label}"
            elif kind == "actuator":
                # The console sends severity_pct; the fault library gives absolute values instead.
                target, mode, frac = m["target"], m.get("mode", "freeze"), float(m.get("severity_pct", 50)) / 100
                kw: dict = dict(name=f"manual_{target}_{mode}_{stamp}", target=target, start_h=t, end_h=end, mode=mode)
                if mode in ("capacity", "capacity_ramp"):
                    kw["capacity_fraction"] = float(m.get("capacity_fraction", frac))
                    if mode == "capacity_ramp":
                        kw["capacity_reference_value"] = 1.0 if target in VALVE_TARGETS else None
                        kw["ramp_duration_h"] = float(m.get("ramp_min", 10.0)) / 60.0
                elif mode == "fixed":
                    kw["fixed_value"] = (float(m["fixed_value"]) if "fixed_value" in m
                                         else float(getattr(env.nominal, target)) * frac)
                elif mode == "stiction":
                    kw["stiction_band_fraction"] = float(m.get("stiction_band_fraction", frac))
                env.spec.actuator_faults.faults.append(ActuatorFault(**kw))
                text = f"actuator {target} {mode}"
            elif kind == "pump":
                pid = m["pump_id"]
                if pid not in PUMP_IDS:
                    raise ValueError(pid)
                env.spec.pump_faults.faults.append(PumpFault(pid, t, end, f"manual_{pid}_trip_{stamp}"))
                text = f"pump {pid} trip"
            elif kind == "sensor":
                target, mode = m["target"], m.get("mode", "bias")
                bias = (float(m["bias"]) if "bias" in m
                        else float(self.plant.measurements(env.y)[target]) * float(m.get("bias_pct", -20)) / 100)
                env.spec.sensor_faults.faults.append(SensorFault(
                    target, t, end, mode, bias if mode == "bias" else 0.0, f"manual_{target}_{mode}_{stamp}"))
                text = f"sensor {target} {mode}"
            elif kind == "process":
                ftype, sev = m["fault"], float(m.get("severity", 50))
                if ftype == "catalyst_activity_loss":
                    target, value = "catalyst_activity", max(0.0, 1 - sev / 100)
                elif ftype == "condenser_cooling_loss":
                    target, value = "condenser_cooling_effectiveness", max(0.0, 1 - sev / 100)
                else:
                    target, value = f"{ftype.removesuffix('_leak')}_leak_kmol_h", max(0.0, sev)
                env.runtime_inputs.add(RuntimeInputEvent(f"manual_{ftype}_{stamp}", "process_fault",
                                                         target, t, end, "set", value))
                text = f"process fault {ftype}"
            else:
                raise ValueError(f"unknown injection kind {kind!r}")
        if quiet:
            return {"ok": True, "summary": "injected"}
        await self.notify(f"[master] injected {text}", "warn")
        await self.broadcast({"type": "status", **self.status()})
        return {"ok": True, "summary": f"injected {text}"}

    async def _clear_injections(self) -> None:
        async with self.lock:
            env = self.env
            t = env.t_h
            env.clear_runtime_injections()
            for eng in (env.spec.actuator_faults, env.spec.sensor_faults, env.spec.pump_faults):
                eng.faults = [replace(f, end_h=t) if f.active(t) and f.name.startswith("manual_") else f
                              for f in eng.faults]
        await self.notify("[master] all active manual injections ended", "warn")

    # ------------------------------------------------------------ human operator
    def _blind(self) -> bool:
        return self.human_test is not None and self.human_test.blind and self.human_test.state == "running"

    async def set_operator_mode(self, mode: str | None) -> dict:
        from .human_test import OPERATOR_MODES
        if mode not in OPERATOR_MODES:
            return {"ok": False, "summary": f"operator mode must be one of {OPERATOR_MODES}"}
        if self.human_test is not None and self.human_test.state == "running":
            if mode == "agents" and self.human_test.who == "human":
                return {"ok": False, "summary": "stop the human test before handing over to the agents"}
            if mode == "human" and self.human_test.who == "agents":
                return {"ok": False, "summary": "stop the pipeline benchmark before taking the controls"}
        self.operator_mode = mode
        if mode == "human":
            await self.set_agent_hold(False)
        await self.broadcast({"type": "status", **self.status()})
        await self.notify("Human-operator mode: the agents are off, you run the plant" if mode == "human"
                          else "Agents mode: the agents supervise the plant again")
        return {"ok": True, "summary": f"operator mode {mode}"}

    async def start_human_test(self, msg: dict) -> dict:
        from .human_test import HumanTest, pick
        if self.human_test is not None and self.human_test.state == "running" and self.human_test.who == "agents":
            return {"ok": False, "summary": "a pipeline benchmark is running; stop it first"}
        sid, random_pick = pick(msg.get("scenario", "random"))
        if self.operator_mode != "human":
            await self.set_operator_mode("human")
        blind = bool(msg.get("blind", True))
        test = HumanTest(self, sid, blind, random_pick)
        await self.load({"scenario": "nominal", "horizon_min": test.window + 1.0, "stop_on_trip": True})
        self.human_test = test
        await self.broadcast({"type": "human_test", **test.status()})
        what = "a random scenario" if random_pick else sid
        await self.notify(f"[human test] {what}, {test.window:.0f} min{', blind' if blind else ''}: "
                          "faults are coming and you are the operator", "warn")
        if msg.get("run", True):
            self.set_running(True)
        return {"ok": True, "summary": "human test started"}

    async def stop_human_test(self) -> dict:
        if self.human_test is None or self.human_test.state != "running":
            return {"ok": False, "summary": "no human test running"}
        await self.human_test.finish("stopped early")
        return {"ok": True, "summary": "human test stopped"}

    def human_test_status(self) -> dict:
        if self.human_test is None or self.human_test.who != "human":  # agent runs report as a pipeline benchmark
            return {"human_test": None}
        return self.human_test.status()


    # ------------------------------------------------------------ views
    def status(self) -> dict:
        env = self.env
        return {"running": self.running, "done": bool(env and env.done),
                "time_min": 60.0 * env.t_h if env else 0.0,
                "episode": env.spec.name if env else None,
                "episode_status": env.outcomes.state.status.value if env else None,
                "config": self.config, "tick_delay_ms": 1000 * self.tick_delay_s,
                "pace": {"every": self.pace_every, "sleep_s": self.pace_sleep_s},
                "hold_for_agents": self.hold_for_agents, "held": self.held,
                "operator_mode": self.operator_mode}

    def _truth(self) -> dict:
        env, t = self.env, self.env.t_h
        if self._blind():  # the person under test must not see what was injected
            return {"schedule": [], "runtime": [], "actuator": [], "sensor": [], "pump": [], "hidden": True}
        events = []
        for ev in env.spec.scenario.events:
            phase = "active" if ev.active(t) else "pending" if t < ev.start_h else "done"
            events.append({"name": ev.name, "phase": phase, "start_min": 60 * ev.start_h,
                           "end_min": None if ev.end_h is None else 60 * ev.end_h})
        names = lambda xs: [x.name for x in xs]
        return {"schedule": events,
                "runtime": names(env.runtime_inputs.active_events(t)),
                "actuator": names(env.spec.actuator_faults.active_faults(t)),
                "sensor": names(env.spec.sensor_faults.active_faults(t)),
                "pump": names(env.spec.pump_faults.active_faults(t))}

    def snapshot(self) -> dict:
        env, plant = self.env, self.plant
        m, u, sp = env.last_measured, env.last_actual, env.controllers.sp
        yc, _ = plant.split_state(env.y)
        h2 = plant.cstr.h2_flow_components(yc, plant._cstr_inputs(u))
        t = 60.0 * env.t_h
        trend = dict(
            t=t,
            r_temp=m["cstr_temperature_K"], r_temp_sp=sp.cstr_temperature_K,
            r_press=m["cstr_pressure_bar"], r_press_sp=sp.cstr_pressure_bar,
            r_level=m["cstr_holdup_kmol"], r_level_sp=sp.cstr_holdup_kmol, r_x=m["cstr_x_acetone"],
            col_press=m["column_pressure_bar"], col_press_sp=sp.column_pressure_bar,
            top_temp=m["top_temperature_K"], top_temp_sp=sp.top_temperature_K,
            bottom_temp=m["bottom_temperature_K"], bottom_temp_sp=sp.bottom_temperature_K,
            rb_level=m["reboiler_holdup_kmol"], rb_level_sp=sp.reboiler_holdup_kmol,
            dr_level=m["drum_holdup_kmol"], dr_level_sp=sp.drum_holdup_kmol,
            dist_x=m["distillate_x_acetone"], btm_x=m["bottoms_x_acetone"],
            feed=u.fresh_ipa_feed, outflow=u.cstr_outflow, reflux=u.reflux_flow,
            distillate=u.distillate_flow, bottoms=u.bottoms_flow,
            heater=u.cstr_heater_factor, steam=u.steam_factor, cooling=u.condenser_cooling_valve,
            cool_eff=u.condenser_cooling_effectiveness, pcv=u.cstr_vent_valve, bdv=u.cstr_bdv_valve,
            psv=h2["psv_open_fraction"], h2=h2["total_kmol_h"], feed_temp=u.fresh_feed_temperature_K,
            # column feed = reactor outlet: leaves at reactor temperature (as measured), at the outlet-pump flow
            col_feed_temp=m["cstr_temperature_K"], col_feed=u.cstr_outflow,
        )
        alarms = []
        for s in env.alarms.status.values():
            d = s.definition
            alarms.append({"id": d.alarm_id, "tag": d.tag, "state": s.state.value, "value": s.value,
                           "limit": d.limit, "unit": d.unit, "priority": d.priority,
                           "description": d.description})
        history = [{"time_min": e.time_min, "id": e.alarm_id, "transition": e.transition,
                    "priority": e.priority, "value": e.value, "description": e.description, "by": e.by}
                   for e in env.alarms.history[-60:][::-1]]
        return {
            **self.status(),
            "trend": trend,
            "measurements": {k: v for k, v in m.items() if not k.startswith("pump_")},
            "controllers": env.controllers.access_snapshot(m),
            "throughput_target": env.observation()["throughput_target_kmol_h"],
            "auxiliaries": dict(env.operator_auxiliary),
            "pumps": env.pumps.snapshot(env.t_h, env.spec.pump_faults),
            "alarms": alarms, "alarm_history": history,
            "sif_latched": list(env.last_safety_result.latched_causes),
            "plant_trip": bool(env.last_safety_result.plant_trip_latched),
            "last_action": env.last_action_result.summary,
            "truth": self._truth(),
        }

    def meta(self) -> dict:
        return {"scenarios": CONSOLE_SCENARIOS,
                "loops": {k: {"sp_min": v.sp_min, "sp_max": v.sp_max, "sp_unit": v.sp_unit,
                              "mv_unit": v.mv_unit, "pv_tag": v.pv_tag}
                          for k, v in self.env.controllers.LOOP_SPECS.items()},
                "auxiliaries": {k: list(v) for k, v in EpisodeRunner.AUXILIARY_BOUNDS.items()},
                "throughput_bounds": list(EpisodeRunner.THROUGHPUT_BOUNDS),
                "pump_trains": {k: {"primary": v.primary_id, "spare": v.spare_id, "description": v.description}
                                for k, v in PUMP_TRAINS.items()},
                "pump_ids": list(PUMP_IDS),
                "injections": {"disturbances": DISTURBANCES,
                               "disturbance_steps": {k: disturbance_step(k, 1.0)[2].lstrip("+")
                                                     for k in DISTURBANCES},
                               "actuator_targets": ACTUATOR_TARGETS,
                               "actuator_modes": ["freeze", "capacity", "fixed", "stiction"],
                               "sensor_targets": SENSOR_TARGETS, "process_faults": PROCESS_FAULTS}}

    def hello(self) -> dict:
        from .fault_library import FAULT_LIBRARY
        from .human_test import scenario_list
        out = {"api_version": API_VERSION,
               "meta": {**self.meta(), "fault_library": sorted(FAULT_LIBRARY), "human_scenarios": scenario_list()},
               **self.human_test_status(), "snapshot": self.snapshot(), "trend": list(self.trend),
               "monitor": list(self.monitor), "agent_log": list(self.agent_log),
               "messages": list(self.messages)}
        for extra in self.hello_extras:
            out.update(extra())
        return jsonable(out)
