"""20-scenario fault benchmark with profit accounting: passive plant (BPCS + SIF only) vs agents.

Scenario groups (SCENARIOS below)
  single         10 x one fault
  pair_indep      4 x two faults that do not influence each other (different units / loops)
  pair_coupled    3 x two faults that act on the same variables
  triple_none     1 x three faults, no interaction
  triple_2plus1   1 x three faults, two interact + one isolated
  triple_all      1 x three faults, all interact

Protocol per scenario (same plant, same seeds, same economics for every run)
  1. probe    : faults injected and never lifted, nobody acts (only the BPCS loops and the SIFs).
                Gives the time T_trip at which the plant would trip (plant-trip SIF latches).
  2. lift     : each fault is lifted (cleared) at T_trip + 10 min (never earlier than 10 min after
                its own onset). If the probe does not trip within 240 min, each fault is lifted
                30 min after its onset.
  3. passive  : faults with that lift schedule, nobody acts             -> baseline profit
     lucky / judge / llmtwin : the same faults with the LLM agents (gpt-5-nano by default) in
                autonomous mode, validated by nothing / an LLM judge / the digital twin
  A nominal run (no fault) gives the reference profit for the same window.
  After the faults are lifted a "shift operator" requests a SIF reset every 5 min (it only resets
  latches whose variable is back inside the reset band); plant-trip latches end the episode.

    py -3 -m acetone_plant.benchmark.profit_campaign probe   --jobs 21      # phase 1 (+ nominal reference)
    py -3 -m acetone_plant.benchmark.profit_campaign compare --jobs 20      # phase 3: agents + passive (needs phase 1)
    py -3 -m acetone_plant.benchmark.profit_campaign report                 # tables -> benchmark_results/
    py -3 -m acetone_plant.benchmark.profit_campaign one S01 agents         # a single run, live log
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import os
from pathlib import Path
import sys
import time

from .economics import PRICES, hourly_margin, inventory_value, prices_dict

ROOT = Path.cwd()
OUT = Path(os.environ.get("PLANT_RESULTS_DIR", ROOT / "benchmark_results"))
RUNS = OUT / "runs"
ONSET = 10.0             # first fault onset [min]
LIFT_AFTER_TRIP = 10.0   # lift faults this long after the time the plant would have tripped
LIFT_NO_TRIP = 30.0      # ... or this long after onset if the plant would not trip
PROBE_WINDOW = 240.0
MIN_WINDOW = 120.0       # scenario window [min]
TAIL = 60.0              # run at least this long after the last lift
# run mode -> agent preset. Every action comes from the LLM (PLANT_LLM_MODEL, default
# gpt-5-nano; --model / --base-url select a local model).
AGENT_MODES = {"lucky": "llm_lucky", "judge": "llm_judge", "llmtwin": "llm", "admit": "llm_admit",
               "admit_nosim": "llm_admit_nosim"}
MODE_LABEL = {"passive": "No agents", "lucky": "LLM, no validation (feeling lucky)", "judge": "LLM + LLM judge",
              "llmtwin": "LLM + digital twin",
              "admit": "LLM planning + ADMIT gates (T0-T4, simulated)",
              "admit_nosim": "LLM planning + ADMIT gates (T0-T3, no simulation)"}


def F(fault: str, at: float = ONSET, **kw) -> dict:
    return {"fault": fault, "at_min": at, **kw}


SCENARIOS: list[dict] = [
    # ---------------------------------------------------------------- 10 single faults
    {"id": "S01", "group": "single", "faults": [F("reflux_pump_trip")]},
    {"id": "S02", "group": "single", "faults": [F("bottoms_pump_trip")]},
    {"id": "S03", "group": "single", "faults": [F("distillate_pump_trip")]},
    {"id": "S04", "group": "single", "faults": [F("feed_pump_trip")]},
    {"id": "S05", "group": "single", "faults": [F("cstr_outlet_capacity_loss")]},
    {"id": "S06", "group": "single", "faults": [F("h2_pcv_capacity_degradation")]},
    {"id": "S07", "group": "single", "faults": [F("reactor_heater_stuck_high")]},
    {"id": "S08", "group": "single", "faults": [F("condenser_cooling_loss")]},
    {"id": "S09", "group": "single", "faults": [F("reflux_drum_leak")]},
    {"id": "S10", "group": "single", "faults": [F("reactor_pressure_sensor_low_bias")]},
    # ---------------------------------------------------------------- 4 pairs, no interaction
    {"id": "D01", "group": "pair_indep",
     "faults": [F("reactor_pressure_sensor_low_bias"), F("bottoms_pump_trip", ONSET + 3)],
     "interaction": "reactor pressure loop vs reboiler level loop: different units, no shared loop or alarm"},
    {"id": "D02", "group": "pair_indep",
     "faults": [F("reactor_heater_stuck_high"), F("distillate_pump_trip")],
     "interaction": "reactor temperature (heater) vs reflux-drum level (distillate pump); simultaneous"},
    {"id": "D03", "group": "pair_indep",
     "faults": [F("h2_pcv_capacity_degradation"), F("reflux_pump_trip", ONSET + 5)],
     "interaction": "reactor H2 vent capacity vs column reflux: reactor gas side vs column top"},
    {"id": "D04", "group": "pair_indep",
     "faults": [F("reactor_pressure_sensor_low_bias"), F("reflux_drum_leak", ONSET + 2)],
     "interaction": "reactor pressure transmitter vs reflux-drum inventory"},
    # ---------------------------------------------------------------- 3 pairs that interact
    {"id": "C01", "group": "pair_coupled",
     "faults": [F("condenser_cooling_loss"), F("reflux_pump_trip", ONSET + 3)],
     "interaction": "both act on the column top: less condensation and no reflux -> column pressure and drum level"},
    {"id": "C02", "group": "pair_coupled",
     "faults": [F("h2_pcv_capacity_degradation"), F("reactor_pressure_sensor_low_bias", ONSET + 2)],
     "interaction": "both raise reactor pressure: the low-reading transmitter makes R_PIC close the degrading PCV"},
    {"id": "C03", "group": "pair_coupled",
     "faults": [F("reflux_drum_leak"), F("distillate_pump_trip", ONSET + 4)],
     "interaction": "both act on the reflux-drum level in opposite directions (leak drains, lost distillate fills)"},
    # ---------------------------------------------------------------- 3 triples
    {"id": "T01", "group": "triple_none",
     "faults": [F("h2_pcv_capacity_degradation"), F("distillate_pump_trip", ONSET + 2), F("bottoms_pump_trip", ONSET + 4)],
     "interaction": "reactor vent, drum level and reboiler level: three different loops"},
    {"id": "T02", "group": "triple_2plus1",
     "faults": [F("condenser_cooling_loss"), F("reactor_pressure_sensor_low_bias", ONSET + 1), F("reflux_pump_trip", ONSET + 3)],
     "interaction": "condenser loss + reflux trip interact at the column top; the reactor pressure sensor bias is isolated"},
    {"id": "T03", "group": "triple_all",
     "faults": [F("condenser_cooling_loss"), F("reflux_pump_trip", ONSET + 2), F("reflux_drum_leak", ONSET + 4)],
     "interaction": "condenser, reflux and drum leak all act on the column-top pressure / drum inventory"},
]
BY_ID = {s["id"]: s for s in SCENARIOS}

# key true process variables and the size of a "significant" deviation (interaction footprint)
FOOTPRINT_SCALE = {"cstr_pressure_bar": 0.10, "cstr_temperature_K": 1.0, "cstr_holdup_kmol": 2.0,
                   "column_pressure_bar": 0.02, "drum_holdup_kmol": 2.0, "reboiler_holdup_kmol": 2.0,
                   "top_temperature_K": 1.0, "bottom_temperature_K": 1.0, "distillate_x_acetone": 0.01,
                   "bottoms_x_acetone": 0.01, "column_min_tray_holdup_kmol": 0.2}


def _utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def lift_schedule(scn: dict, probe: dict | None) -> dict:
    """onset/lift per fault index from the probe's trip time (see module doc); probe=None: never lifted."""
    trip = (probe or {}).get("trip_min")
    out = {}
    for i, f in enumerate(scn["faults"]):
        if probe is None:
            lift = None
        elif trip is not None:
            lift = round(max(trip + LIFT_AFTER_TRIP, f["at_min"] + 10.0), 2)
        else:
            lift = f["at_min"] + LIFT_NO_TRIP
        out[i] = {"onset_min": f["at_min"], "lift_min": lift}
    return out


def window_for(sched: dict) -> float:
    return max(MIN_WINDOW, max(v["lift_min"] for v in sched.values()) + TAIL)


async def run_scenario(scn: dict, mode: str, sched: dict, window: float, verbose: bool = False) -> dict:
    """mode: 'nominal' | 'probe' | 'passive' | 'agents' (only 'agents' runs the agents)."""
    from acetone_plant.server.campaigns import FAULT_LIBRARY
    from acetone_plant.server.plant_service import PlantService

    service = PlantService()
    service.pace_every = 0  # no UI pacing
    from collections import deque
    service.agent_log = deque(maxlen=200_000)  # keep every agent event (the console keeps the last 600)
    service.monitor = deque(maxlen=200_000)
    service.messages = deque(maxlen=10_000)
    await service.load({"scenario": "nominal", "horizon_min": window + 1.0, "stop_on_trip": True})
    sup = None
    usage0 = None
    if mode in AGENT_MODES:
        from acetone_plant.agents.llm import DEFAULT_MODEL, USAGE
        from acetone_plant.agents.recovery_graph import make_config
        from acetone_plant.agents.supervisor import AsyncSupervisor
        model = os.environ.get("PLANT_LLM_MODEL") or DEFAULT_MODEL
        extra = {"use_kg": False} if os.environ.get("PLANT_USE_KG") == "0" else {}  # KG ablation
        if os.environ.get("PLANT_DIAGNOSER"):
            extra["diagnoser"] = os.environ["PLANT_DIAGNOSER"]
        sup = AsyncSupervisor(service, make_config(AGENT_MODES[mode], llm_model=model, **extra), mode="autonomous")
        usage0 = USAGE.summary()["total"]
        sup.start()
    injections = []
    for i, f in enumerate(scn["faults"]):
        params = {k: v for k, v in FAULT_LIBRARY[f["fault"]].items() if k != "vary"}
        params.update({k: v for k, v in f.items() if k not in ("fault", "at_min")})
        lift = sched[i]["lift_min"]
        params["duration_min"] = 0 if lift is None else lift - sched[i]["onset_min"]  # 0 = persistent
        injections.append({"t": sched[i]["onset_min"], "lift": lift, "params": params, "fault": f["fault"],
                           "done": False})
    lifts = [j["lift"] for j in injections if j["lift"] is not None]
    last_lift = max(lifts, default=None)

    env = service.env
    dt_h = service.TIMING.decision_interval_min / 60.0
    ticks, sif_seen, sif_first = [], set(), {}
    profit = {"acetone": 0.0, "ipa_credit": 0.0, "h2": 0.0, "feed": 0.0, "steam": 0.0, "heating": 0.0, "margin": 0.0}
    on_spec_kmol, off_spec_min, trip_min = 0.0, 0.0, None
    wall0 = time.time()

    async def wait_for_agents(t_now: float) -> None:
        """Lock-step: the next plant step waits until the agents have processed this observation."""
        if sup is None:
            return
        t0 = time.time()
        while ((sup.last_cycle_t is None or sup.last_cycle_t + 1e-6 < t_now or service.held)
               and time.time() - t0 < 1800 and not sup._task.done()):
            await asyncio.sleep(0.01)

    t = 0.0
    while t < window - 1e-9 and not env.done:
        for j in injections:
            if not j["done"] and t + 1e-9 >= j["t"]:
                await service._inject(j["params"])
                j["done"] = True
                if verbose:
                    print(f"{t:6.1f} inject {j['fault']} until {j['lift']}", flush=True)
        since = None if last_lift is None else t - last_lift
        if since is not None and since > -1e-9 and abs(since / 5.0 - round(since / 5.0)) < 1e-6 \
                and env.last_safety_result.latched_causes:
            service._pending_reset_sif = True  # shift operator: reset SIFs that are back in their band
        await service.step(service.TIMING.decision_interval_min)
        t = 60.0 * env.t_h
        summary = env.plant.operating_summary(env.y, env.last_actual)
        summary["feed_kmol_h"] = env.last_actual.fresh_ipa_feed
        m = hourly_margin(summary)
        for k in profit:
            profit[k] += m[k] * dt_h
        on_spec_kmol += m["on_spec_kmol_h"] * dt_h
        off_spec_min += 0.0 if m["on_spec"] else 60 * dt_h
        sr = env.last_safety_result
        for c in sr.latched_causes:
            if c not in sif_seen:
                sif_seen.add(c)
                sif_first[c] = round(t, 2)
        if sr.plant_trip_latched and trip_min is None:
            trip_min = round(t, 2)
        true = env.plant.measurements(env.y)
        ticks.append({"t": round(t, 2), "margin_usd_h": round(m["margin"], 2), "on_spec": m["on_spec"],
                      **{k: round(float(true[k]), 5) for k in FOOTPRINT_SCALE},
                      "sif": list(sr.latched_causes),
                      "alarms_listed": sum(1 for s in env.alarms.status.values() if s.state.value != "NORMAL")})
        await wait_for_agents(t)
        if verbose and abs(t / 10.0 - round(t / 10.0)) < 1e-6:
            print(f"{t:6.1f} margin {m['margin']:8.1f} $/h  on-spec {m['on_spec']}  SIF {list(sr.latched_causes)}  "
                  f"alarms listed {ticks[-1]['alarms_listed']}", flush=True)

    status = env.outcomes.state.status.value
    end_t = 60.0 * env.t_h
    tripped = trip_min is not None or status in ("SAFE_TRIP", "DAMAGE", "MODEL_LIMIT", "NUMERICAL_FAILURE")
    if tripped and trip_min is None:
        trip_min = round(end_t, 2)
    # SIF trip vs unplanned shutdown because the plant left the model's valid region (README
    # "MODEL_LIMIT"; tray dry-out is now caught first by the COL_L_LL SIF). Both stop production and
    # pay the shutdown/startup penalty.
    stop_kind = None
    if tripped:
        stop_kind = "SIF" if status == "SAFE_TRIP" or sr_trip(env) else status
    penalty = PRICES.trip_penalty_usd if tripped else 0.0
    alarms_hist = [{"t": e.time_min, "id": e.alarm_id, "transition": e.transition, "by": e.by,
                    "priority": e.priority} for e in env.alarms.history]
    listed_end = [{"id": s.definition.alarm_id, "state": s.state.value} for s in env.alarms.status.values()
                  if s.state.value != "NORMAL"]
    agent = None
    if sup is not None:
        log = list(service.agent_log)
        mon = [m_ for m_ in service.monitor if m_["fault"]]
        applied = [e for e in log if e.get("stage") == "applied"]
        from acetone_plant.agents.llm import USAGE
        u1 = USAGE.summary()["total"]
        uses_llm = sup.cfg.proposer == "llm" or sup.cfg.planner == "llm" or sup.cfg.judge
        agent = {
            "preset": sup.cfg.name,
            "llm_model": sup.cfg.llm_model if uses_llm else None,
            "llm_usage": {k: round(u1[k] - usage0[k], 6) for k in u1} if uses_llm else None,
            "llm_errors": [str(e.get("error"))[:200] for e in log if e.get("stage") == "llm_error"],
            "judge": [{"t": e["time_min"], "cycle": e.get("cycle"), "accept": e.get("accept"),
                       "confidence": e.get("confidence"), "reason": e.get("reason"),
                       "commands": (e.get("plan") or {}).get("commands")} for e in log if e.get("stage") == "judge"],
            "admit": [{"t": e["time_min"], "cycle": e.get("cycle"), "admissible": e.get("admissible"),
                       "hazard_id": e.get("hazard_id"), "wall_s": e.get("wall_s"),
                       "codes": [v["code"] for r in e.get("records") or [] for v in r.get("violations") or []],
                       "violations": [v["message"][:200] for r in e.get("records") or [] for v in r.get("violations") or []],
                       "commands": (e.get("plan") or {}).get("commands")} for e in log if e.get("stage") == "admit"],
            "proposals": sum(1 for e in log if e.get("stage") == "proposal"),
            "diagnoses": [{"t": round(e["time_min"], 2), "cycle": e.get("cycle"), "ok": e.get("ok"),
                           "most_likely": e.get("most_likely"), "fault_class": e.get("fault_class"),
                           "hypotheses": [(h["fault"], h["probability"]) for h in e.get("hypotheses") or []],
                           "summary": e.get("summary"), "latency_s": e.get("latency_s"), "tokens": e.get("tokens")}
                          for e in log if e.get("stage") == "llm_diagnosis"],
            "timing": _timing(log),
            "first_detection_min": mon[0]["time_min"] if mon else None,
            "fault_cycles": len(mon),
            "decisions": len({e.get("cycle") for e in log if e.get("stage") == "node"}),
            "actions": [{"t": e["time_min"], "kind": e.get("kind"), "commands": (e.get("plan") or {}).get("commands"),
                         "diagnosis": e.get("diagnosis")} for e in applied],
            "validations": [{"t": e["time_min"], "cycle": e.get("cycle"), "accepted": e.get("accepted"),
                             "commands": (e.get("plan") or {}).get("commands"),
                             "failed": [c["check"] + ": " + str(c.get("detail"))[:120]
                                        for c in e.get("checks", []) if not c["ok"]]}
                            for e in log if e.get("stage") == "validation"],
            "reprompts": sum(1 for e in log if e.get("stage") == "reprompt"),
            "fallbacks": sum(1 for e in log if e.get("stage") == "fallback"),
            "alarm_acks": [{"t": e["time_min"], "alarms": e.get("alarms"), "reason": e.get("reason")}
                           for e in log if e.get("stage") == "alarm_ack"],
            "verification": [{"t": e["time_min"], "reason": e.get("reason")}
                             for e in log if e.get("stage") == "verification"],
            "errors": [m_["text"] for m_ in service.messages if m_.get("level") == "error"],
        }
        sup._task.cancel()
    return {
        "id": scn["id"], "group": scn["group"], "mode": mode, "faults": [f["fault"] for f in scn["faults"]],
        "interaction": scn.get("interaction"), "schedule": sched, "window_min": window,
        "status": status, "status_reason": env.outcomes.state.reason, "end_min": round(end_t, 2),
        "tripped": tripped, "stop_kind": stop_kind, "trip_min": trip_min, "sifs": sorted(sif_seen), "sif_first_min": sif_first,
        "profit_usd": {k: round(v, 2) for k, v in profit.items()} | {"trip_penalty": -penalty,
                                                                     "total": round(profit["margin"] - penalty, 2)},
        "on_spec_acetone_kmol": round(on_spec_kmol, 3), "off_spec_min": round(off_spec_min, 2),
        "alarm_history": alarms_hist, "alarms_listed_at_end": listed_end, "agent": agent,
        "wall_s": round(time.time() - wall0, 1), "ticks": ticks,
    }


def sr_trip(env) -> bool:
    return bool(env.last_safety_result.plant_trip_latched)


def _timing(log: list[dict]) -> dict:
    """Wall-clock overheads of the agents (the plant is held while they decide, so these add to
    the run time but not to the simulated time): each LLM call, each twin rollout, each decision."""
    agent_of = {"judge": "judge", "proposal": "action", "llm_diagnosis": "diagnosis"}
    calls = [{"t": round(e["time_min"], 2), "cycle": e.get("cycle"), "agent": agent_of[e["stage"]],
              "latency_s": round(e["latency_s"], 3), "tokens": e.get("tokens")}
             for e in log if e.get("stage") in agent_of and e.get("latency_s") is not None]
    # plant projections: twin rollouts, and ADMIT's T4 (null + plan) on the llm_admit runs
    twins = [{"t": round(e["time_min"], 2), "cycle": e.get("cycle"), "wall_s": e["wall_s"],
              "counterfactual": bool(e.get("counterfactual")), "admit": e.get("stage") == "admit"}
             for e in log if e.get("stage") in ("twin", "admit") and e.get("wall_s") is not None]
    spans: dict = {}
    for e in log:  # a decision runs from its first to its last graph event (later acks / resets excluded)
        c, w = e.get("cycle"), e.get("wall")
        if c is None or w is None or e.get("stage") in ("alarm_ack", "sif_reset"):
            continue
        d = spans.setdefault(c, {"cycle": c, "t": round(e["time_min"], 2), "w0": w, "w1": w})
        d["w0"], d["w1"] = min(d["w0"], w), max(d["w1"], w)
    decisions = [{"cycle": d["cycle"], "t": d["t"], "wall_s": round(d["w1"] - d["w0"], 3)} for d in spans.values()]

    def total(xs, k):
        return round(sum(x[k] or 0.0 for x in xs), 3)
    return {"llm_calls": calls, "twin_rollouts": twins, "decisions": decisions,
            "llm_s": total(calls, "latency_s"), "twin_s": total(twins, "wall_s"), "decision_s": total(decisions, "wall_s"),
            "n_llm_calls": len(calls), "n_twin_rollouts": len(twins), "n_decisions": len(decisions)}


def _run_job(args: tuple) -> str:
    sid, mode, sched, window = args
    _utf8()
    scn = BY_ID[sid] if sid != "NOMINAL" else {"id": "NOMINAL", "group": "reference", "faults": []}
    res = asyncio.run(run_scenario(scn, mode, {int(k): v for k, v in sched.items()}, window))
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"{sid}_{mode}.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    p = res["profit_usd"]["total"]
    return (f"{sid:8s} {mode:8s} {res['status']:12s} trip={res['trip_min']} SIF={res['sifs']} "
            f"profit=${p:,.0f} ({res['wall_s']:.0f}s)")


def _load(sid: str, mode: str) -> dict | None:
    p = RUNS / f"{sid}_{mode}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _pool(jobs: list[tuple], n: int) -> None:
    with ProcessPoolExecutor(max_workers=n) as ex:
        futs = {ex.submit(_run_job, j): j for j in jobs}
        for fut in as_completed(futs):
            try:
                print(fut.result(), flush=True)
            except Exception as exc:  # report and keep going
                print(f"{futs[fut][0]} {futs[fut][1]} FAILED: {type(exc).__name__}: {exc}", flush=True)


def phase_probe(n: int, ids: list[str]) -> None:
    jobs = [(sid, "probe", lift_schedule(BY_ID[sid], None), PROBE_WINDOW) for sid in ids]
    jobs.append(("NOMINAL", "nominal", {}, 300.0))
    _pool(jobs, n)


def phase_compare(n: int, ids: list[str], modes=("lucky", "judge", "llmtwin", "passive")) -> None:
    jobs = []
    for mode in modes:  # agents first: they take longest
        for sid in ids:
            probe = _load(sid, "probe")
            if probe is None:
                print(f"{sid}: run the probe phase first")
                continue
            sched = lift_schedule(BY_ID[sid], probe)
            jobs.append((sid, mode, sched, window_for(sched)))
    _pool(jobs, n)


def _window_profit(run: dict, window: float, parts: bool = False):
    """Profit of a run truncated to ``window`` minutes: margin integral + change in liquid
    inventory value + trip penalty (see economics.py)."""
    dt = 0.5 / 60.0
    ticks = [tk for tk in run["ticks"] if tk["t"] <= window + 1e-6]
    margin = sum(tk["margin_usd_h"] * dt for tk in ticks)
    inventory = inventory_value(ticks[-1]) - inventory_value(run["ticks"][0]) if ticks else 0.0
    tripped = run["tripped"] and (run["trip_min"] or 0) <= window
    penalty = PRICES.trip_penalty_usd if tripped else 0.0
    total = margin + inventory - penalty
    return {"margin": margin, "inventory": inventory, "penalty": -penalty, "total": total} if parts else total


def footprint(run: dict, onset: float, span: float = 15.0) -> set[str]:
    """Key variables that deviate significantly from the nominal run within ``span`` min of onset."""
    nominal = _load("NOMINAL", "nominal")
    ref = {tk["t"]: tk for tk in nominal["ticks"]}
    out = set()
    for tk in run["ticks"]:
        if onset <= tk["t"] <= onset + span and tk["t"] in ref:
            for k, s in FOOTPRINT_SCALE.items():
                if abs(tk[k] - ref[tk["t"]][k]) > s:
                    out.add(k)
    return out


def report() -> dict:
    nominal = _load("NOMINAL", "nominal")
    single_fp = {}
    for scn in SCENARIOS:
        if scn["group"] == "single" and (p := _load(scn["id"], "probe")):
            single_fp[scn["faults"][0]["fault"]] = footprint(p, ONSET)
    rows = []
    for scn in SCENARIOS:
        p, pr = _load(scn["id"], "passive"), _load(scn["id"], "probe")
        if p is None:
            continue
        window = p["window_min"]
        nom, pp = _window_profit(nominal, window), _window_profit(p, window)
        fps = [single_fp.get(f["fault"], set()) for f in scn["faults"]]
        overlaps = [sorted(fps[i] & fps[j]) for i in range(len(fps)) for j in range(i + 1, len(fps))]
        rows.append({"id": scn["id"], "group": scn["group"], "faults": " + ".join(f["fault"] for f in scn["faults"]),
                     "window_min": window, "lift_min": [v["lift_min"] for v in p["schedule"].values()],
                     "probe_trip_min": pr["trip_min"] if pr else None, "probe_sifs": pr["sifs"] if pr else None,
                     "passive_trip_min": p["trip_min"], "passive_sifs": p["sifs"], "passive_status": p["status"],
                     "nominal_profit": round(nom, 0), "passive_profit": round(pp, 0),
                     "passive_off_spec_min": p["off_spec_min"],
                     "alarms_listed_at_end_passive": [x["id"] for x in p["alarms_listed_at_end"]],
                     "footprint_overlap": overlaps, "interaction": scn.get("interaction"), "configs": {}})
    totals = {"scenarios": len(rows),
              "passive_sif_trips": sum(r["passive_status"] == "SAFE_TRIP" for r in rows),
              "passive_dry_outs": sum(r["passive_status"] == "MODEL_LIMIT" for r in rows),
              "probe_trips": sum(r["probe_trip_min"] is not None for r in rows),
              "passive_trips": sum(r["passive_trip_min"] is not None for r in rows),
              "nominal_profit": sum(r["nominal_profit"] for r in rows),
              "passive_profit": sum(r["passive_profit"] for r in rows)}
    # every agent configuration that has runs: profit, stops and saving vs no agents, per scenario
    configs = {}
    for mode in AGENT_MODES:
        per = {}
        for r in rows:
            run = _load(r["id"], mode)
            if run is None:
                continue
            prof = _window_profit(run, r["window_min"])
            ag = run.get("agent") or {}
            per[r["id"]] = {"profit": round(prof, 0), "saving": round(prof - r["passive_profit"], 0),
                            "trip_min": run["trip_min"], "status": run["status"], "sifs": run["sifs"],
                            "actions": len(ag.get("actions", [])), "proposals": ag.get("proposals"),
                            "reprompts": ag.get("reprompts"), "llm_usage": ag.get("llm_usage"),
                            "judge_rejections": sum(1 for j in ag.get("judge", []) if not j.get("accept")),
                            "judge_calls": len(ag.get("judge", [])), "llm_errors": len(ag.get("llm_errors", [])),
                            "admit_calls": len(ag.get("admit", [])),
                            "admit_rejections": sum(1 for j in ag.get("admit", []) if not j.get("admissible")),
                            "alarms_listed_at_end": [x["id"] for x in run["alarms_listed_at_end"]],
                            "timing": {k: v for k, v in (ag.get("timing") or {}).items()
                                       if k in ("llm_s", "twin_s", "decision_s", "n_llm_calls", "n_twin_rollouts", "n_decisions")},
                            "wall_s": run.get("wall_s")}
            r["configs"][mode] = per[r["id"]]
        if per:
            usage = [v["llm_usage"] for v in per.values() if v.get("llm_usage")]
            configs[mode] = {"label": MODE_LABEL[mode], "preset": AGENT_MODES[mode], "n": len(per),
                             "complete": len(per) == len(rows),
                             "trips": sum(v["trip_min"] is not None for v in per.values()),
                             "sif_trips": sum(v["status"] == "SAFE_TRIP" for v in per.values()),
                             "profit": sum(v["profit"] for v in per.values()),
                             "passive_profit_same": sum(r["passive_profit"] for r in rows if r["id"] in per),
                             "saving": sum(v["saving"] for v in per.values()),
                             "llm_calls": sum(u["calls"] for u in usage), "llm_cost_usd": round(sum(u["cost_usd"] for u in usage), 4),
                             "llm_tokens": sum(u["input_tokens"] + u["output_tokens"] for u in usage),
                             **_timing_totals([v for v in per.values()])}
    out = {"prices": prices_dict(), "configs": configs, "mode_labels": MODE_LABEL,
           "protocol": {"onset_min": ONSET, "lift_after_trip_min": LIFT_AFTER_TRIP, "lift_no_trip_min": LIFT_NO_TRIP,
                        "probe_window_min": PROBE_WINDOW, "min_window_min": MIN_WINDOW, "tail_min": TAIL,
                        "agent_modes": AGENT_MODES},
           "single_fault_footprints": {k: sorted(v) for k, v in single_fp.items()},
           "rows": rows, "totals": totals}
    OUT.mkdir(exist_ok=True)
    (OUT / "profit_campaign.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    _write_markdown(out)
    return out


def _timing_totals(per: list[dict]) -> dict:
    t = [v["timing"] for v in per if v.get("timing")]
    n_dec, n_llm = sum(x.get("n_decisions", 0) for x in t), sum(x.get("n_llm_calls", 0) for x in t)
    dec, llm, twin = (sum(x.get(k, 0.0) for x in t) for k in ("decision_s", "llm_s", "twin_s"))
    return {"timed_runs": len(t), "decision_s": round(dec, 1), "llm_s": round(llm, 1), "twin_s": round(twin, 1),
            "n_decisions": n_dec, "n_llm_calls_timed": n_llm, "n_twin_rollouts": sum(x.get("n_twin_rollouts", 0) for x in t),
            "mean_decision_s": round(dec / n_dec, 2) if n_dec else None,
            "mean_llm_latency_s": round(llm / n_llm, 2) if n_llm else None,
            "run_wall_s": round(sum(v.get("wall_s") or 0.0 for v in per), 1)}


def _money(x: float | None) -> str:
    return "—" if x is None else (f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}")


_TRIP_SIFS = {"R_P_HHH", "RB_L_HH", "DR_L_LL", "DR_L_HH", "COL_P_HH", "COL_L_LL"}


def _trip(t, sifs, status="SAFE_TRIP") -> str:
    if t is not None:
        if status == "SAFE_TRIP":
            trip = [c for c in sifs if c in _TRIP_SIFS]
            other = [c for c in sifs if c not in _TRIP_SIFS]
            return f"SIF trip {', '.join(trip)} at {t:.1f} min" + (f" (also {', '.join(other)})" if other else "")
        what = {"MODEL_LIMIT": "column dry-out (MODEL_LIMIT, no SIF)",
                "NUMERICAL_FAILURE": "solver failure"}.get(status, status)
        return f"{what} at {t:.1f} min" + (f" (SIF {', '.join(sifs)})" if sifs else "")
    return "no trip" + (f" (SIF {', '.join(sifs)} latched, reset)" if sifs else "")


def _write_markdown(out: dict) -> None:
    modes = list(out.get("configs", {}))
    L = ["# Fault campaign: profit with and without the agents", "",
         "Every agent action comes from the LLM (no rule-based agent). Configurations: "
         + "; ".join(f"`{m}` = {MODE_LABEL[m]}" for m in modes) + ".",
         f"Faults start at {ONSET:g} min. Each fault is lifted {LIFT_AFTER_TRIP:g} min after the time the plant would "
         f"have tripped (probe run, faults never lifted, nobody acting), or {LIFT_NO_TRIP:g} min after its onset if "
         f"the probe does not trip. Window = max({MIN_WINDOW:g} min, last lift + {TAIL:g} min).",
         f"Trip penalty (shutdown + startup): {_money(PRICES.trip_penalty_usd)}; after a trip the plant earns nothing "
         "for the rest of the window. Profit includes the change in liquid hold-up valued at the IPA price.", "",
         "| id | faults | would trip at | no agents | " + " | ".join(MODE_LABEL[m] for m in modes) + " | nominal |",
         "|---|---|---|---|" + "---|" * len(modes) + "---:|"]
    for r in out["rows"]:
        probe = "no trip" if r["probe_trip_min"] is None else f"{r['probe_trip_min']:.1f} min"
        cells = []
        for m in modes:
            c = r["configs"].get(m)
            cells.append("—" if c is None else
                         f"{_money(c['profit'])} · {'stop ' + format(c['trip_min'], '.1f') if c['trip_min'] is not None else 'no stop'}")
        passive = f"{_money(r['passive_profit'])} · " + _trip(r["passive_trip_min"], r["passive_sifs"], r["passive_status"])
        L.append(f"| {r['id']} | {r['faults']} | {probe} | {passive} | " + " | ".join(cells) + f" | {_money(r['nominal_profit'])} |")
    t = out["totals"]
    L += ["", f"**Without agents: {t['passive_trips']}/{t['scenarios']} plant stops ({t['passive_sif_trips']} SIF trips + "
              f"{t['passive_dry_outs']} model-limit stops with no SIF), profit {_money(t['passive_profit'])}; nominal "
              f"{_money(t['nominal_profit'])}.**", ""]
    if modes:
        L += ["## Agent configurations", "",
              "| configuration | runs | plant stops | profit | saving vs no agents | LLM calls | LLM cost | "
              "decision time | mean per decision | mean LLM latency | twin time |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for mode, c in out["configs"].items():
            L.append(f"| {c['label']} | {c['n']}/{t['scenarios']} | {c['trips']} | {_money(c['profit'])} | "
                     f"{_money(c['saving'])} | {c['llm_calls']} | ${c['llm_cost_usd']:.2f} | "
                     f"{c['decision_s'] / 60:.1f} min | {c['mean_decision_s'] or 0:.1f} s | "
                     f"{c['mean_llm_latency_s'] or 0:.1f} s | {c['twin_s'] / 60:.1f} min |")
        L.append("")
    (OUT / "PROFIT_CAMPAIGN.md").write_text("\n".join(L), encoding="utf-8")


def main() -> None:
    _utf8()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=["probe", "compare", "report", "one", "list"])
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--jobs", type=int, default=10)
    ap.add_argument("--ids", default="", help="comma-separated scenario ids (default: all)")
    ap.add_argument("--model", default="", help="LLM for the lucky / judge / llmtwin modes (default gpt-5-nano)")
    ap.add_argument("--base-url", default="", help="OpenAI-compatible local server, e.g. http://localhost:1234/v1")
    a = ap.parse_args()
    ids = [s for s in a.ids.split(",") if s] or [s["id"] for s in SCENARIOS]
    if a.model:  # inherited by the worker processes
        os.environ["PLANT_LLM_MODEL"] = a.model
    if a.base_url:
        os.environ["PLANT_LLM_BASE_URL"] = a.base_url
    if a.phase == "list":
        for s in SCENARIOS:
            print(s["id"], s["group"], [(f["fault"], f["at_min"]) for f in s["faults"]])
    elif a.phase == "probe":
        phase_probe(a.jobs, ids)
    elif a.phase == "compare":
        phase_compare(a.jobs, ids, tuple(a.rest) or ("lucky", "judge", "llmtwin", "passive"))
    elif a.phase == "report":
        out = report()
        print((OUT / "PROFIT_CAMPAIGN.md").read_text(encoding="utf-8"))
        print(json.dumps(out["totals"], indent=1))
    else:
        sid, mode = a.rest[0], a.rest[1] if len(a.rest) > 1 else "llmtwin"
        sched = lift_schedule(BY_ID[sid], None if mode == "probe" else _load(sid, "probe"))
        window = PROBE_WINDOW if mode == "probe" else window_for(sched)
        res = asyncio.run(run_scenario(BY_ID[sid], mode, sched, window, verbose=True))
        res.pop("ticks")
        print(json.dumps(res, indent=1, default=str)[:8000])


if __name__ == "__main__":
    main()
