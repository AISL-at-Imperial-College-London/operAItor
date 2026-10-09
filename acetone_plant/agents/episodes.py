"""Run one closed-loop episode (plant + monitoring + recovery graph) under a simulated clock
and score it. Used by the tests; the benchmark lives in ``acetone_plant.benchmark``."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
import json
import math
import time
from typing import Any

from acetone_plant.agents.kg import KnowledgeGraph
from acetone_plant.agents.llm import DEFAULT_MODEL
from acetone_plant.agents.monitoring import MonitoringAgent
from acetone_plant.agents.recovery_graph import (PRESETS, AgentConfig, RunContext, build_recovery_graph, clone_env,
                                   make_action_agent, make_config, make_planner)
from acetone_plant.fdd import DEFAULT_MODEL_PATH, PCAMonitor
from acetone_plant.plant import (
    CURATED_EPISODES, EpisodeRunner, EpisodeTiming, IntegratedPlantDAE, MeasurementModel,
    SafetyConfig, SupervisoryAction, curated_episode, default_sensor_specs, surprise_episode,
)

TIMING = EpisodeTiming(plant_step_min=0.1, decision_interval_min=0.5, control_interval_min=1.0)
ON_SPEC_X_D = 0.90
FAILED_STATUSES = {"SAFE_TRIP", "DAMAGE", "MODEL_LIMIT", "NUMERICAL_FAILURE", "ERROR"}


# =============================================================================
# Episodes: build, run under a clock, score
# =============================================================================

def build_episode(scenario: str, horizon_min: float, onset_min: float, seed: int) -> EpisodeRunner:
    if scenario.startswith("surprise"):
        spec = surprise_episode(horizon_min, seed)
    else:
        spec = curated_episode(scenario, horizon_min, onset_min)
    spec = replace(spec, allow_recovery_termination=False, terminate_on_plant_trip=True)
    plant = IntegratedPlantDAE()
    nominal = plant.default_inputs()
    y0 = plant.nominal_state(nominal)
    noise = MeasurementModel(default_sensor_specs(plant.measurements(y0)), seed=seed)
    return EpisodeRunner(plant, spec, nominal_inputs=nominal, y0=y0, timing=TIMING, measurement_model=noise)


def ground_truth(env: EpisodeRunner) -> tuple[list[str], float | None]:
    """Fault-library names (same vocabulary as the diagnosis/KG) and earliest onset."""
    names, onsets = [], []
    for ev in env.spec.scenario.events:
        if ev.kind == "disturbance":
            continue
        names.append(ev.name.removeprefix("surprise_"))
        onsets.append(60 * ev.start_h)
    if env.spec.name in CURATED_EPISODES:  # curated event names are equipment tags, e.g. P-201A_trip
        names = [env.spec.name]
    return names, (min(onsets) if onsets else None)


async def run_episode(scenario: str, cfg: AgentConfig, *, horizon_min: float = 60.0, onset_min: float = 15.0,
                      seed: int = 0, pca: PCAMonitor | None = None, kg: KnowledgeGraph | None = None,
                      verbose: bool = False, action_agent: Any = None,
                      planner: Any = None) -> tuple[dict, list[dict], EpisodeRunner]:
    env = build_episode(scenario, horizon_min, onset_min, seed)
    trace: list[dict] = []

    def emit(e: dict) -> None:
        if e["event"] == "monitor":  # keep traces compact
            det, diag = e["detection"], e["diagnosis"]
            e = {"event": "monitor", "t": e["t"], "fault": det["fault"], "should_act": det["should_act"],
                 "detectors": det["detectors"], "t2": (det["pca"] or {}).get("t2"),
                 "spe": (det["pca"] or {}).get("spe"), "hypotheses": [h["fault"] for h in diag["hypotheses"]],
                 "alarms": det["rules"]["active_alarms"]}
        trace.append(e)
        if verbose and (e["event"] != "monitor" or e["fault"]):
            body = {k: v for k, v in e.items() if k not in ("event", "t")}
            print(f"{e.get('t', 0):6.1f} min  {e['event']:9s} {json.dumps(body, default=str)[:220]}", flush=True)

    lock = asyncio.Lock()
    scheduled: list[tuple[float, SupervisoryAction, float]] = []  # delay clock

    async def snapshot_env() -> EpisodeRunner:
        async with lock:
            return await asyncio.to_thread(clone_env, env)

    async def dispatch(action: SupervisoryAction, info: dict) -> dict:
        t_obs = info["t_obs"]
        if cfg.clock == "delay" and cfg.delay_min > 0:
            scheduled.append((t_obs + cfg.delay_min, action, t_obs))
            emit({"event": "scheduled", "t": t_obs, "apply_at": t_obs + cfg.delay_min})
            return {"status": "scheduled"}
        async with lock:
            result = env.apply_action(action)
            t_apply = 60 * env.t_h
        emit({"event": "applied", "t": t_apply, "t_obs": t_obs, "sim_delay_min": t_apply - t_obs,
              "action": {k: v for k, v in asdict(action).items() if v}, "result": result.summary})
        return {"status": "applied"}

    monitor = MonitoringAgent(pca if cfg.use_pca else None, persistence=cfg.persistence,
                              cooldown_min=cfg.cooldown_min, rules_trigger=cfg.rules_trigger, quality_needs_pca=cfg.quality_needs_pca)
    ctx = RunContext(cfg, monitor, kg if cfg.use_kg else None, planner or make_planner(cfg),
                     action_agent or make_action_agent(cfg), snapshot_env, dispatch, emit)
    graph = build_recovery_graph(ctx)

    async def decide() -> None:
        await graph.ainvoke({"observation": env.observation(), "catalog": env.action_catalog()})

    def apply_due() -> None:
        now = 60 * env.t_h
        for item in [s for s in scheduled if s[0] <= now + 1e-9]:
            scheduled.remove(item)
            result = env.apply_action(item[1])
            emit({"event": "applied", "t": now, "t_obs": item[2], "sim_delay_min": now - item[2],
                  "action": {k: v for k, v in asdict(item[1]).items() if v}, "result": result.summary})

    wall0 = time.perf_counter()
    if cfg.clock in ("frozen", "delay"):
        while not env.done:
            apply_due()
            await decide()
            await asyncio.to_thread(env.step, SupervisoryAction.no_action())
    else:  # async: plant and agents run concurrently
        latest: asyncio.Queue = asyncio.Queue(maxsize=1)
        finished = asyncio.Event()

        step_wall_s = 60.0 * TIMING.decision_interval_min / cfg.speedup if cfg.speedup > 0 else 0.0
        loop = asyncio.get_running_loop()

        async def plant_loop():
            while not env.done:
                t0 = loop.time()
                async with lock:
                    await asyncio.to_thread(env.step, SupervisoryAction.no_action())
                    item = (env.observation(), env.action_catalog())
                if latest.full():
                    latest.get_nowait()
                latest.put_nowait(item)
                await asyncio.sleep(max(0.0, step_wall_s - (loop.time() - t0)))  # real-time pacing
            finished.set()

        async def agent_loop():
            while not finished.is_set():
                try:
                    obs, catalog = await asyncio.wait_for(latest.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                await graph.ainvoke({"observation": obs, "catalog": catalog})

        agent = asyncio.create_task(agent_loop())
        await plant_loop()
        agent.cancel()
        try:
            await agent
        except asyncio.CancelledError:
            pass

    metrics = score_episode(env, trace, ctx, cfg, scenario=scenario, seed=seed,
                            wall_s=time.perf_counter() - wall0)
    return metrics, trace, env


def score_episode(env: EpisodeRunner, trace: list[dict], ctx: RunContext, cfg: AgentConfig, *,
                  scenario: str, seed: int, wall_s: float) -> dict:
    truth, onset = ground_truth(env)
    sc = SafetyConfig()
    limits = {"cstr_temperature_K": (-math.inf, sc.cstr_temperature_hh_K),
              "cstr_pressure_bar": (-math.inf, sc.cstr_pressure_hh_bar),
              "cstr_holdup_kmol": (sc.cstr_level_ll_kmol, sc.cstr_level_hh_kmol),
              "reboiler_holdup_kmol": (sc.reboiler_level_ll_kmol, sc.reboiler_level_hh_kmol),
              "drum_holdup_kmol": (sc.drum_level_ll_kmol, sc.drum_level_hh_kmol),
              "column_pressure_bar": (-math.inf, sc.column_pressure_hh_bar)}
    dt_min = TIMING.plant_step_min
    unsafe_min = product_kmol = 0.0
    trip = False
    for r in env.records:  # records stop at termination (no horizon padding here)
        bad = any(not lo < float(r[f"safety_meas.{k}"]) < hi for k, (lo, hi) in limits.items())
        unsafe_min += dt_min if bad else 0.0
        trip = trip or bool(r["sif.plant_trip_latched"])
        x_d = float(r["true.distillate_x_acetone"])
        if x_d >= ON_SPEC_X_D:  # only on-spec product counts (DIST_X_L alarm limit)
            product_kmol += float(r["act.distillate_flow"]) * x_d * dt_min / 60

    mon = [e for e in trace if e["event"] == "monitor"]
    pre = [e for e in mon if onset is not None and e["t"] < onset]
    post = [e for e in mon if onset is None or e["t"] >= onset]
    first = next((e for e in post if e["fault"]), None)
    hyps = first["hypotheses"] if first else []
    applied = [e for e in trace if e["event"] == "applied"]
    proposals = [e for e in trace if e["event"] == "proposal"]
    twins = [e for e in trace if e["event"] == "twin"]
    validations = [e for e in trace if e["event"] == "validation" and e.get("criterion") != "none"]
    plans = [e for e in trace if e["event"] == "plan"]
    lat = [u.get("latency_s", 0.0) for u in ctx.llm_usage]
    return {
        "agent": cfg.name, "clock": cfg.clock, "delay_min": cfg.delay_min,
        "speedup": cfg.speedup if cfg.clock == "async" else None, "scenario": scenario, "seed": seed,
        "truth_faults": ";".join(truth), "onset_min": onset,
        # detection & diagnosis
        "false_alarm_pre_onset": int(any(e["fault"] for e in pre)),
        "detected": int(first is not None), "detection_delay_min": (first["t"] - onset) if first and onset is not None else None,
        "diag_top1": int(bool(truth) and bool(hyps) and hyps[0] in truth),
        "diag_top3": int(bool(truth) and any(h in truth for h in hyps[:3])),
        "first_hypothesis": hyps[0] if hyps else "",
        "plan_target": plans[0]["brief"].get("target_fault", "") if plans else "",
        "plan_target_correct": int(bool(plans) and plans[0]["brief"].get("target_fault") in truth),
        # actions
        "n_proposals": len(proposals), "n_actions": len(applied),
        "first_action_min": applied[0]["t"] if applied else None,
        "mean_sim_delay_min": (sum(a["sim_delay_min"] for a in applied) / len(applied)) if applied else None,
        "twin_runs": len(twins), "twin_rejections": sum(1 for v in validations if not v["accepted"]),
        "twin_restored": sum(1 for t in twins if t.get("restored")),
        "mitigations": sum(1 for e in trace if e["event"] == "fallback"),
        "kg_cited": int(any(p.get("kg_citations") for p in proposals)),
        "actions": " | ".join(json.dumps(a["action"]) for a in applied),
        # plant outcome (ground truth)
        "final_status": env.outcomes.state.status.value, "terminal_reason": env.outcomes.state.reason,
        "end_min": 60 * env.t_h, "plant_trip": int(trip),
        "failed": int(env.outcomes.state.status.value in FAILED_STATUSES),
        "unsafe_min": unsafe_min, "onspec_acetone_kmol": product_kmol,
        # cost
        "llm_calls": len(ctx.llm_usage) if cfg.proposer == "llm" else 0,
        "llm_latency_mean_s": (sum(lat) / len(lat)) if lat and cfg.proposer == "llm" else None,
        "llm_tokens": sum(int(u.get("total_tokens") or 0) for u in ctx.llm_usage),
        "llm_cost_usd": sum(float(u.get("cost_usd") or 0.0) for u in ctx.llm_usage) if cfg.proposer == "llm" else 0.0,
        "wall_s": wall_s,
    }


# =============================================================================
# Benchmark runner and summaries
# =============================================================================

_PCA_CACHE: PCAMonitor | None = None
_KG_CACHE: KnowledgeGraph | None = None
