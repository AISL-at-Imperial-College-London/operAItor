"""ADMIT consequence world for the acetone plant: T4 projections run on the full simulator.

ADMIT's T4 gate asks whether the projected trajectory under an action stays inside the safe
envelope, and whether it is worse than doing nothing. Its built-in worlds are small ODE models;
this one projects on a clone of the real plant (CSTR + 10-tray column, 8 PI loops, SIFs, pump
trains), with the faults that are active now kept active and no knowledge of future events
(``clone_env(present_only=True)``, the same rule as the digital twin).

Envelope = the SIF trip limits (``SafetyConfig``) on the true process state. A projection that
ends in a plant trip or leaves the model's valid region counts as crossing the envelope.

Two ways to use it:
  * live gating: ``AcetoneWorld(env=<live plant clone>, plan=<SupervisoryAction>)``. Every
    commit/protective record of one decision is projected as the whole plan (the plan is
    applied atomically), so T4 is evaluated once per decision.
  * cartridge cases: ``AcetoneWorld()``; ``initial_state({"scenario": i, "decision_min": t})``
    rebuilds scenario ``i`` of the benchmark up to ``t`` and projects grammar actions from there.
"""
from __future__ import annotations

import asyncio
import math
from typing import Optional

from admitbench.physics import WORLDS, Trajectory, World

from operaitor.agents.plan_compiler import compile_plan
from operaitor.agents.recovery_graph import FAILED_TERMINAL, clone_env
from operaitor.plant import EpisodeRunner, SafetyConfig, SupervisoryAction

STEP_MIN = 0.5  # plant decision interval

# variable -> (SIF cause, kind); limits come from SafetyConfig.
_ENVELOPE = {
    "cstr_temperature_K": ("R_T_HH", "max", "cstr_temperature_hh_K"),
    "cstr_pressure_bar": ("R_P_HHH", "max", "cstr_pressure_hhh_bar"),
    "cstr_holdup_kmol": ("R_L_HH", "max", "cstr_level_hh_kmol"),
    "cstr_holdup_low_kmol": ("R_L_LL", "min", "cstr_level_ll_kmol"),
    "reboiler_holdup_kmol": ("RB_L_HH", "max", "reboiler_level_hh_kmol"),
    "reboiler_holdup_low_kmol": ("RB_L_LL", "min", "reboiler_level_ll_kmol"),
    "drum_holdup_kmol": ("DR_L_HH", "max", "drum_level_hh_kmol"),
    "drum_holdup_low_kmol": ("DR_L_LL", "min", "drum_level_ll_kmol"),
    "column_pressure_bar": ("COL_P_HH", "max", "column_pressure_hh_bar"),
    "column_min_tray_holdup_kmol": ("COL_L_LL", "min", "column_tray_holdup_ll_kmol"),
}
NOMINAL = {"cstr_temperature_K": 335.7, "cstr_pressure_bar": 3.0, "cstr_holdup_kmol": 50.0,
           "reboiler_holdup_kmol": 34.5, "drum_holdup_kmol": 30.2, "column_pressure_bar": 1.0,
           "column_min_tray_holdup_kmol": 2.24}
SIF_VAR = {cause: var for var, (cause, _, _) in _ENVELOPE.items()}


def _state(env: EpisodeRunner) -> dict:
    """Flat float state: true process values, plus the two-sided levels as separate keys."""
    m = env.plant.measurements(env.y)
    s = {k: float(m[k]) for k in NOMINAL}
    for k in ("cstr_holdup", "reboiler_holdup", "drum_holdup"):
        s[f"{k}_low_kmol"] = s[f"{k}_kmol"]
    return s


# grammar action -> catalog command (see cartridge/manifest.yaml)
def grammar_to_command(action: str, params: dict) -> dict | None:
    p = params or {}
    if action == "adjust_setpoint":
        return {"type": "SET_SETPOINT", "target": str(p.get("tag", "")).upper(), "value": p.get("value")}
    if action == "start_standby_pump":
        return {"type": "START_SPARE", "target": str(p.get("tag", "")).upper()}
    if action == "set_throughput":
        return {"type": "SET_THROUGHPUT", "value": p.get("value")}
    if action == "open_blowdown":
        return {"type": "OPERATE_AUXILIARY", "target": "H2_BDV", "value": float(p.get("value", 1.0))}
    if action == "close_blowdown":
        return {"type": "OPERATE_AUXILIARY", "target": "H2_BDV", "value": 0.0}
    if action == "apply_emergency_cooling":
        return {"type": "OPERATE_AUXILIARY", "target": "EMERGENCY_COOLING", "value": float(p.get("value", 1.0))}
    if action == "stop_emergency_cooling":
        return {"type": "OPERATE_AUXILIARY", "target": "EMERGENCY_COOLING", "value": 0.0}
    return None  # observe / hold / escalate: no plant command


def command_to_grammar(cmd: dict) -> tuple[str, dict]:
    kind, target = cmd.get("type"), cmd.get("target")
    if kind == "SET_SETPOINT":
        return "adjust_setpoint", {"tag": target, "value": cmd.get("value")}
    if kind == "START_SPARE":
        return "start_standby_pump", {"tag": target}
    if kind == "SET_THROUGHPUT":
        return "set_throughput", {"value": cmd.get("value")}
    if kind == "OPERATE_AUXILIARY" and target == "H2_BDV":
        return ("open_blowdown", {"value": cmd.get("value")}) if (cmd.get("value") or 0) > 0 else ("close_blowdown", {})
    if kind == "OPERATE_AUXILIARY" and target == "EMERGENCY_COOLING":
        return (("apply_emergency_cooling", {"value": cmd.get("value")}) if (cmd.get("value") or 0) > 0
                else ("stop_emergency_cooling", {}))
    return "hold_and_monitor", {}


def _build_case_env(scenario: int, decision_min: float) -> EpisodeRunner:
    """Benchmark scenario ``scenario`` (index into SCENARIOS), faults never lifted, up to ``decision_min``."""
    from operaitor.benchmark.profit_campaign import SCENARIOS
    from operaitor.server.campaigns import FAULT_LIBRARY
    from operaitor.server.plant_service import PlantService

    async def build() -> EpisodeRunner:
        s = PlantService()
        s.pace_every = 0
        await s.load({"scenario": "nominal", "horizon_min": 300, "stop_on_trip": True})
        faults = sorted(SCENARIOS[int(scenario)]["faults"], key=lambda f: f["at_min"])
        while 60 * s.env.t_h < decision_min - 1e-9 and not s.env.done:
            t = 60 * s.env.t_h
            for f in faults:
                if abs(t - f["at_min"]) < 1e-6:
                    p = {k: v for k, v in FAULT_LIBRARY[f["fault"]].items() if k != "vary"}
                    p.update({k: v for k, v in f.items() if k not in ("fault", "at_min")})
                    await s._inject({**p, "duration_min": 0})
            await s.step(STEP_MIN)
        return s.env
    return asyncio.run(build())


class AcetoneWorld(World):
    name = "acetone_plant"
    default_horizon_s = 1800.0
    sample_every_s = 60.0

    def __init__(self, env: EpisodeRunner | None = None, plan: SupervisoryAction | None = None,
                 null_cache: dict | None = None):
        self._env = env
        self._plan = plan
        self._cache: dict = {}
        # "do nothing" projections can be shared by every attempt of one decision (same plant state)
        self._null_cache = null_cache if null_cache is not None else {}
        self._cfg = SafetyConfig()

    # ------------------------------------------------------------ hooks
    def initial_state(self, overrides: Optional[dict] = None) -> dict:
        o = overrides or {}
        if self._env is None:
            if "scenario" not in o:
                raise ValueError("operaitor cases need initial_state {scenario, decision_min}")
            key = (int(o["scenario"]), float(o.get("decision_min", 12.0)))
            if key not in self._cache:
                self._cache[key] = _build_case_env(*key)
            self._env = self._cache[key]
        return _state(self._env)

    def envelope(self) -> dict[str, dict]:
        out = {}
        for var, (_, kind, attr) in _ENVELOPE.items():
            base = var.replace("_low_kmol", "_kmol")
            out[var] = {"limit": float(getattr(self._cfg, attr)), "kind": kind, "nominal": NOMINAL[base]}
        return out

    def apply_action(self, state: dict, action: Optional[str], params: Optional[dict]) -> dict:
        return state  # actions are applied to the simulator in project()

    def derivatives(self, state: dict) -> dict:  # pragma: no cover - project() is overridden
        raise NotImplementedError("AcetoneWorld integrates with the plant simulator, not RK4")

    # ------------------------------------------------------------ projection
    def _supervisory(self, action: Optional[str], params: Optional[dict], env: EpisodeRunner) -> SupervisoryAction | None:
        if action is None:
            return None
        if self._plan is not None:      # live gating: the decision's whole plan
            return self._plan
        cmd = grammar_to_command(action, params or {})
        if cmd is None:
            return None
        sup, errors = compile_plan({"commands": [cmd], "reasoning": ""}, env.observation(), env.action_catalog())
        if errors:
            raise ValueError("; ".join(errors))
        return sup

    def _run(self, twin: EpisodeRunner, minutes: float, traj: Trajectory, t0_s: float = 0.0) -> float:
        """Advance ``twin`` and update ``traj``; returns the last margin."""
        end = 60 * twin.t_h + minutes
        last_sample = -math.inf
        margin = 1.0
        while True:
            s = _state(twin)
            margin, var = self.margin(s)
            t_s = t0_s + 60.0 * (60 * twin.t_h - (end - minutes))
            if twin.done and twin.outcomes.state.status.value in FAILED_TERMINAL:
                causes = list(twin.last_safety_result.latched_causes) or []
                var = next((SIF_VAR[c] for c in causes if c in SIF_VAR), var)
                margin = min(margin, 0.0)
            traj.min_margin = min(traj.min_margin, margin)
            if margin <= 0.0 and not traj.crossed:
                traj.crossed, traj.crossed_at, traj.crossed_var = True, round(t_s, 1), var
            if t_s - last_sample >= self.sample_every_s:
                traj.times.append(round(t_s, 1))
                traj.samples.append({k: round(v, 4) for k, v in s.items()})
                last_sample = t_s
            if twin.done or 60 * twin.t_h >= end - 1e-9:
                break
            sr = twin.last_safety_result
            reset = bool(sr.latched_causes and not sr.plant_trip_latched)
            twin.step(SupervisoryAction(reset_sifs=True, note="admit_sif_reset") if reset
                      else SupervisoryAction.no_action())
        traj.final_state = _state(twin)
        return margin

    def project(self, state: dict, action: Optional[str] = None, params: Optional[dict] = None,
                horizon_s: Optional[float] = None) -> Trajectory:
        horizon_min = float(horizon_s if horizon_s is not None else self.default_horizon_s) / 60.0
        key = ("project", action is None, None if self._plan is not None else (action, repr(params)), horizon_min)
        cache = self._null_cache if action is None else self._cache
        if key in cache:
            return cache[key]
        twin = clone_env(self._env)
        sup = self._supervisory(action, params, twin)
        if sup is not None:
            res = twin.apply_action(sup)
            if res.rejected:
                raise ValueError("plant rejected the action: " + "; ".join(res.rejected))
        traj = Trajectory(min_margin=math.inf)
        prev = self.margin(_state(twin))[0]
        end_margin = self._run(twin, horizon_min, traj)
        traj.diverging = end_margin < prev - 1e-9 and end_margin < 0.5
        if traj.min_margin is math.inf:
            traj.min_margin = 1.0
        cache[key] = traj
        return traj

    def rehearse_recovery(self, state: dict, action: str, params: dict, recovery_action: Optional[str],
                          recovery_params: Optional[dict] = None, act_for_s: float = 300.0,
                          recover_for_s: float = 900.0) -> dict:
        twin = clone_env(self._env)
        sup = self._supervisory(action, params, twin)
        if sup is not None:
            twin.apply_action(sup)
        traj = Trajectory(min_margin=math.inf)
        self._run(twin, act_for_s / 60.0, traj)
        if not twin.done:
            cmd = grammar_to_command(recovery_action or "", recovery_params or {})
            if cmd is not None:
                back, errors = compile_plan({"commands": [cmd], "reasoning": ""}, twin.observation(), twin.action_catalog())
                if not errors:
                    twin.apply_action(back)
            rec = Trajectory(min_margin=math.inf)
            end_margin = self._run(twin, recover_for_s / 60.0, rec)
            crossed = rec.crossed or traj.crossed
        else:
            end_margin, crossed = 0.0, True
        return {"recovered": (not crossed) and end_margin > 0.2, "end_margin": round(end_margin, 4),
                "crossed_during_recovery": crossed}


WORLDS[AcetoneWorld.name] = AcetoneWorld  # lets admitbench load the acetone plant cartridge
