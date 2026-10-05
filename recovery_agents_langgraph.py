"""Benchmark harness for the monitoring → KG → planning → action recovery graph.

The graph itself lives in ``agents/recovery_graph.py`` (shared with the live
server); this file runs it on simulated episodes and scores the outcome:

    monitoring (PCA T²/SPE + rules) → hypotheses → kg_retrieval → planning agent
      → action agent → admissibility → digital twin → validate (restored? else reprompt) → dispatch

It is adapted from ctrl-alt-recover/case_studies/cstr_case/cstr_case.py.

Clocks (how the plant relates to decision time):
  frozen  plant paused while agents decide (cstr_case.py behaviour; upper bound)
  async   plant keeps stepping in its own task, paced at ``--speedup`` × real
          time; the action lands whenever the agents finish. The simulated
          delay (t_apply − t_obs) is logged. The DAE needs ~1.2 s CPU per
          simulated minute, so speedups above ~40 are CPU bound.
  delay   plant paused while deciding, but the action is applied a fixed
          ``--delay-min`` of simulated time later (reproducible latency ablation)

Commands (``py -3 recovery_agents_langgraph.py <command> -h`` for options):
  smoke      one short pump-trip episode with the rule agent (sanity check)
  run        one episode, verbose event log
  bench      faults × seeds × agents sweep → runs.csv, summary.csv, summary.md
  summarize  rebuild summary.csv / summary.md from an existing runs.csv

Agent presets (``--agents``): passive, rule, rule_twin, llm, llm_plan
(LLM planning agent too), llm_nokg, llm_notwin, pca_only. LLM presets use ``--llm-model`` (names starting with
``gpt`` → langchain-openai, otherwise → langchain-ollama, as in cstr_case.py; the
default is gpt-5-nano, the cheapest OpenAI model, see agents/llm.py).

The twin is an *oracle* clone of the live simulator (true state and fault
schedule); proposers only ever see operator observations and KG evidence.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ProcessPoolExecutor
import csv
from dataclasses import asdict, replace
from datetime import datetime
import json
import math
from pathlib import Path
import time
from typing import Any

from agents.kg import KnowledgeGraph
from agents.llm import DEFAULT_MODEL
from agents.monitoring import MonitoringAgent
from agents.recovery_graph import (PRESETS, AgentConfig, RunContext, build_recovery_graph, clone_env,
                                   make_action_agent, make_config, make_planner)
from fdd import DEFAULT_MODEL_PATH, PCAMonitor
from integrated_plant import (
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
                              cooldown_min=cfg.cooldown_min, rules_trigger=cfg.rules_trigger)
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


def _resources() -> tuple[PCAMonitor | None, KnowledgeGraph]:
    global _PCA_CACHE, _KG_CACHE
    if _PCA_CACHE is None and Path(DEFAULT_MODEL_PATH).exists():
        _PCA_CACHE = PCAMonitor.load(DEFAULT_MODEL_PATH)
    if _KG_CACHE is None:
        _KG_CACHE = KnowledgeGraph()
    return _PCA_CACHE, _KG_CACHE


def _run_job(job: dict) -> dict:
    """Process-pool entry: run one episode, write its trace, return metrics."""
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pca, kg = _resources()
    cfg = make_config(job["agent"], **job["overrides"])
    try:
        metrics, trace, _ = asyncio.run(run_episode(job["scenario"], cfg, horizon_min=job["horizon"],
                                                    onset_min=job["onset"], seed=job["seed"], pca=pca, kg=kg))
    except Exception as exc:
        metrics, trace = {"agent": job["agent"], "scenario": job["scenario"], "seed": job["seed"],
                          "final_status": "ERROR", "failed": 1,
                          "terminal_reason": f"{type(exc).__name__}: {exc}"}, []
    out = Path(job["out"]) / "traces"
    out.mkdir(parents=True, exist_ok=True)
    with (out / f"{job['agent']}_{job['scenario']}_s{job['seed']}.jsonl").open("w", encoding="utf-8") as f:
        f.write(json.dumps({"event": "config", "job": job, "config": asdict(cfg)}, default=str) + "\n")
        for e in trace:
            f.write(json.dumps(e, default=str) + "\n")
    print(f"  done {job['agent']:<10s} {job['scenario']:<34s} seed={job['seed']} "
          f"→ {metrics.get('final_status')} detected={metrics.get('detected')} "
          f"actions={metrics.get('n_actions')} unsafe={metrics.get('unsafe_min', 0):.1f} min", flush=True)
    return metrics


def _mean_ci(xs: list[float]) -> str:
    xs = [x for x in xs if x is not None and x == x]
    if not xs:
        return "–"
    m = sum(xs) / len(xs)
    if len(xs) < 2:
        return f"{m:.2f}"
    sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
    return f"{m:.2f} ± {1.96 * sd / len(xs) ** 0.5:.2f}"


def summarize(runs_csv: Path) -> Path:
    rows = list(csv.DictReader(runs_csv.open(encoding="utf-8")))
    num = lambda r, k: float(r[k]) if r.get(k) not in (None, "", "None") else None
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        clock = r.get("clock", "")
        clock += f" {r['delay_min']} min" if clock == "delay" else f" ×{r.get('speedup')}" if clock == "async" else ""
        groups.setdefault((r["agent"], clock), []).append(r)
    cols = [("n", None), ("detected", "detected"), ("delay [min]", "detection_delay_min"),
            ("diag top-1", "diag_top1"), ("diag top-3", "diag_top3"), ("actions", "n_actions"),
            ("failed", "failed"), ("trip", "plant_trip"), ("unsafe [min]", "unsafe_min"),
            ("on-spec product [kmol]", "onspec_acetone_kmol"),
            ("LLM s/call", "llm_latency_mean_s"), ("LLM $/episode", "llm_cost_usd")]
    summary_rows = []
    lines = ["# Benchmark summary", "", f"Source: `{runs_csv.name}` ({len(rows)} runs). Mean ± 95 % CI over runs.", "",
             "| agent | clock | " + " | ".join(c for c, _ in cols) + " |", "|" + "---|" * (len(cols) + 2)]
    for (agent, clock), rs in sorted(groups.items()):
        cells, srow = [], {"agent": agent, "clock": clock}
        for label, key in cols:
            if key is None:
                val = str(len(rs))
            else:
                val = _mean_ci([num(r, key) for r in rs])
            cells.append(val)
            srow[label] = val
        summary_rows.append(srow)
        lines.append(f"| {agent} | {clock} | " + " | ".join(cells) + " |")
    lines += ["", "## Per fault (failure rate / unsafe minutes)", "",
              "| scenario | " + " | ".join(sorted({a for a, _ in groups})) + " |",
              "|---|" + "---|" * len({a for a, _ in groups})]
    agents = sorted({a for a, _ in groups})
    for sc in sorted({r["scenario"] for r in rows}):
        cells = []
        for a in agents:
            rs = [r for r in rows if r["scenario"] == sc and r["agent"] == a]
            cells.append(f"{_mean_ci([num(r, 'failed') for r in rs])} / "
                         f"{_mean_ci([num(r, 'unsafe_min') for r in rs])}" if rs else "–")
        lines.append(f"| {sc} | " + " | ".join(cells) + " |")
    md = runs_csv.with_name("summary.md")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with runs_csv.with_name("summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0]))
        w.writeheader()
        w.writerows(summary_rows)
    return md


def bench(args) -> Path:
    faults = list(CURATED_EPISODES) if args.faults == ["all"] else args.faults
    out = Path(args.output) / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    overrides = {"clock": args.clock, "delay_min": args.delay_min, "speedup": args.speedup,
                 "llm_model": args.llm_model}
    jobs = [{"agent": a, "scenario": f, "seed": s, "horizon": args.horizon, "onset": args.onset,
             "overrides": overrides, "out": str(out)}
            for a in args.agents for f in faults for s in range(args.seeds)]
    (out / "config.json").write_text(json.dumps({"args": vars(args), "n_jobs": len(jobs)}, indent=2, default=str))
    print(f"{len(jobs)} episodes → {out}", flush=True)
    if args.workers > 1:
        with ProcessPoolExecutor(args.workers) as pool:
            results = list(pool.map(_run_job, jobs))
    else:
        results = [_run_job(j) for j in jobs]
    keys = list(dict.fromkeys(k for r in results for k in r))
    runs_csv = out / "runs.csv"
    with runs_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(results)
    md = summarize(runs_csv)
    print(md.read_text(encoding="utf-8"))
    return out


# =============================================================================
# CLI
# =============================================================================

def main() -> None:
    import sys
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, horizon=60.0, onset=15.0):
        p.add_argument("--horizon", type=float, default=horizon, help="episode length [min]")
        p.add_argument("--onset", type=float, default=onset, help="fault onset for curated faults [min]")
        p.add_argument("--clock", choices=["frozen", "async", "delay"], default="frozen")
        p.add_argument("--delay-min", type=float, default=0.0)
        p.add_argument("--speedup", type=float, default=10.0, help="async clock: sim seconds per wall second")
        p.add_argument("--llm-model", default=DEFAULT_MODEL)

    p = sub.add_parser("smoke", help="short pump-trip sanity check with the rule agent")
    common(p, horizon=25.0, onset=5.0)
    p = sub.add_parser("run", help="one verbose episode")
    common(p)
    p.add_argument("--scenario", default="reflux_pump_trip", help=f"one of {list(CURATED_EPISODES)} or 'surprise'")
    p.add_argument("--agent", default="rule", choices=sorted(PRESETS))
    p.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("bench", help="faults × seeds × agents sweep")
    common(p)
    p.add_argument("--faults", nargs="+", default=["all"])
    p.add_argument("--agents", nargs="+", default=["passive", "rule", "rule_twin"], choices=sorted(PRESETS))
    p.add_argument("--seeds", type=int, default=3)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--output", default="benchmark_results")
    p = sub.add_parser("summarize", help="rebuild summary from runs.csv")
    p.add_argument("runs_csv")
    args = ap.parse_args()

    if args.cmd == "summarize":
        print(summarize(Path(args.runs_csv)).read_text(encoding="utf-8"))
        return
    if args.cmd == "bench":
        bench(args)
        return
    pca, kg = _resources()
    if pca is None:
        print("NOTE: no PCA model at fdd_results/pca_model.json — run `py -3 train_pca.py`; rules only.")
    scenario, agent, seed = ("reflux_pump_trip", "rule", 0) if args.cmd == "smoke" else (args.scenario, args.agent, args.seed)
    cfg = make_config(agent, clock=args.clock, delay_min=args.delay_min, speedup=args.speedup,
                      llm_model=args.llm_model)
    print(f"{scenario} | agent={agent} | clock={cfg.clock} | horizon={args.horizon} min | onset={args.onset} min")
    metrics, _, _ = asyncio.run(run_episode(scenario, cfg, horizon_min=args.horizon, onset_min=args.onset,
                                            seed=seed, pca=pca, kg=kg, verbose=True))
    print("\nmetrics:")
    for k, v in metrics.items():
        print(f"  {k:24s} {v}")


if __name__ == "__main__":
    main()
