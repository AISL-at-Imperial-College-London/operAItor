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
     agents   : faults with that lift schedule, agents in autonomous mode -> agent profit
  A nominal run (no fault) gives the reference profit for the same window.
  After the faults are lifted a "shift operator" requests a SIF reset every 5 min (it only resets
  latches whose variable is back inside the reset band); plant-trip latches end the episode.

    py -3 -m benchmark.profit_campaign probe   --jobs 21      # phase 1 (+ nominal reference)
    py -3 -m benchmark.profit_campaign compare --jobs 20      # phase 3: agents + passive (needs phase 1)
    py -3 -m benchmark.profit_campaign report                 # tables -> benchmark_results/
    py -3 -m benchmark.profit_campaign one S01 agents         # a single run, live log
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import sys
import time

from .economics import PRICES, hourly_margin, prices_dict

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "benchmark_results"
RUNS = OUT / "runs"
ONSET = 10.0             # first fault onset [min]
LIFT_AFTER_TRIP = 10.0   # lift faults this long after the time the plant would have tripped
LIFT_NO_TRIP = 30.0      # ... or this long after onset if the plant would not trip
PROBE_WINDOW = 240.0
MIN_WINDOW = 120.0       # scenario window [min]
TAIL = 60.0              # run at least this long after the last lift
AGENT_PRESET = "rule_twin"  # offline proposer + digital twin + validate/reprompt (no OpenAI calls)


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
                   "bottoms_x_acetone": 0.01}


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
    from server.campaigns import FAULT_LIBRARY
    from server.plant_service import PlantService

    service = PlantService()
    service.pace_every = 0  # no UI pacing
    from collections import deque
    service.agent_log = deque(maxlen=200_000)  # keep every agent event (the console keeps the last 600)
    service.monitor = deque(maxlen=200_000)
    service.messages = deque(maxlen=10_000)
    await service.load({"scenario": "nominal", "horizon_min": window + 1.0, "stop_on_trip": True})
    sup = None
    if mode == "agents":
        from agents.recovery_graph import make_config
        from agents.supervisor import AsyncSupervisor
        sup = AsyncSupervisor(service, make_config(AGENT_PRESET), mode="autonomous")
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
               and time.time() - t0 < 900 and not sup._task.done()):
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
    # SIF trip vs unplanned shutdown because the plant left the model's valid region (e.g. top tray
    # dry-out after loss of reflux: README "MODEL_LIMIT"; there is no SIF for it, the operator would
    # have to shut the column down). Both stop production and pay the shutdown/startup penalty.
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
        agent = {
            "preset": AGENT_PRESET,
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


def phase_compare(n: int, ids: list[str], modes=("agents", "passive")) -> None:
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


def _window_profit(run: dict, window: float) -> float:
    """Profit of a run truncated to ``window`` minutes (margin integral + trip penalty)."""
    dt = 0.5 / 60.0
    margin = sum(tk["margin_usd_h"] * dt for tk in run["ticks"] if tk["t"] <= window + 1e-6)
    tripped = run["tripped"] and (run["trip_min"] or 0) <= window
    return margin - (PRICES.trip_penalty_usd if tripped else 0.0)


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
        p, a, pr = _load(scn["id"], "passive"), _load(scn["id"], "agents"), _load(scn["id"], "probe")
        if p is None or a is None:
            continue
        window = a["window_min"]
        nom = _window_profit(nominal, window)
        pp, ap = _window_profit(p, window), _window_profit(a, window)
        fps = [single_fp.get(f["fault"], set()) for f in scn["faults"]]
        overlaps = [sorted(fps[i] & fps[j]) for i in range(len(fps)) for j in range(i + 1, len(fps))]
        ag = a["agent"] or {}
        rows.append({"id": scn["id"], "group": scn["group"], "faults": " + ".join(f["fault"] for f in scn["faults"]),
                     "window_min": window, "lift_min": [v["lift_min"] for v in a["schedule"].values()],
                     "probe_trip_min": pr["trip_min"] if pr else None, "probe_sifs": pr["sifs"] if pr else None,
                     "passive_trip_min": p["trip_min"], "passive_sifs": p["sifs"], "passive_status": p["status"],
                     "agents_trip_min": a["trip_min"], "agents_sifs": a["sifs"], "agents_status": a["status"],
                     "nominal_profit": round(nom, 0), "passive_profit": round(pp, 0), "agents_profit": round(ap, 0),
                     "saving": round(ap - pp, 0), "agents_loss_vs_nominal": round(nom - ap, 0),
                     "passive_off_spec_min": p["off_spec_min"], "agents_off_spec_min": a["off_spec_min"],
                     "agent_detection_min": ag.get("first_detection_min"),
                     "agent_actions": [(x["t"], x["kind"], x["commands"]) for x in ag.get("actions", [])],
                     "agent_reprompts": ag.get("reprompts"), "agent_fallbacks": ag.get("fallbacks"),
                     "agent_alarm_acks": sum(len(x["alarms"] or []) for x in ag.get("alarm_acks", [])),
                     "alarms_listed_at_end_agents": [x["id"] for x in a["alarms_listed_at_end"]],
                     "alarms_listed_at_end_passive": [x["id"] for x in p["alarms_listed_at_end"]],
                     "footprint_overlap": overlaps, "interaction": scn.get("interaction")})
    totals = {"scenarios": len(rows),
              "passive_sif_trips": sum(r["passive_status"] == "SAFE_TRIP" for r in rows),
              "passive_dry_outs": sum(r["passive_status"] == "MODEL_LIMIT" for r in rows),
              "agents_sif_trips": sum(r["agents_status"] == "SAFE_TRIP" for r in rows),
              "agents_dry_outs": sum(r["agents_status"] == "MODEL_LIMIT" for r in rows),
              "probe_trips": sum(r["probe_trip_min"] is not None for r in rows),
              "passive_trips": sum(r["passive_trip_min"] is not None for r in rows),
              "agents_trips": sum(r["agents_trip_min"] is not None for r in rows),
              "nominal_profit": sum(r["nominal_profit"] for r in rows),
              "passive_profit": sum(r["passive_profit"] for r in rows),
              "agents_profit": sum(r["agents_profit"] for r in rows)}
    totals["saving"] = totals["agents_profit"] - totals["passive_profit"]
    by_group = {}
    for r in rows:
        g = by_group.setdefault(r["group"], {"n": 0, "passive_trips": 0, "agents_trips": 0, "passive": 0, "agents": 0})
        g["n"] += 1
        g["passive_trips"] += r["passive_trip_min"] is not None
        g["agents_trips"] += r["agents_trip_min"] is not None
        g["passive"] += r["passive_profit"]
        g["agents"] += r["agents_profit"]
    out = {"prices": prices_dict(),
           "protocol": {"onset_min": ONSET, "lift_after_trip_min": LIFT_AFTER_TRIP, "lift_no_trip_min": LIFT_NO_TRIP,
                        "probe_window_min": PROBE_WINDOW, "min_window_min": MIN_WINDOW, "tail_min": TAIL,
                        "agent_preset": AGENT_PRESET},
           "single_fault_footprints": {k: sorted(v) for k, v in single_fp.items()},
           "rows": rows, "totals": totals, "by_group": by_group}
    OUT.mkdir(exist_ok=True)
    (OUT / "profit_campaign.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    _write_markdown(out)
    return out


def _money(x: float | None) -> str:
    return "—" if x is None else (f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}")


_TRIP_SIFS = {"R_P_HHH", "RB_L_HH", "DR_L_LL", "DR_L_HH", "COL_P_HH"}


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
    L = ["# Fault campaign: profit with and without the agents", "",
         f"Agents: preset `{AGENT_PRESET}` (offline rule proposer + digital twin + validate/reprompt; PCA-only trigger).",
         f"Faults start at {ONSET:g} min. Each fault is lifted {LIFT_AFTER_TRIP:g} min after the time the plant would "
         f"have tripped (probe run, faults never lifted, nobody acting), or {LIFT_NO_TRIP:g} min after its onset if "
         f"the probe does not trip. Window = max({MIN_WINDOW:g} min, last lift + {TAIL:g} min).",
         f"Trip penalty (shutdown + startup): {_money(PRICES.trip_penalty_usd)}; after a trip the plant earns nothing "
         "for the rest of the window.", "",
         "| id | group | faults | would trip at (probe) | lifted at | window | passive: trip (SIF) | agents: trip (SIF) "
         "| nominal | passive | agents | saving |",
         "|---|---|---|---|---|---:|---|---|---:|---:|---:|---:|"]
    for r in out["rows"]:
        probe = "no trip" if r["probe_trip_min"] is None else f"{r['probe_trip_min']:.1f} min"
        lifts = ", ".join(f"{x:g}" for x in r["lift_min"])
        L.append(f"| {r['id']} | {r['group']} | {r['faults']} | {probe} | {lifts} | {r['window_min']:.0f} | "
                 f"{_trip(r['passive_trip_min'], r['passive_sifs'], r['passive_status'])} | "
                 f"{_trip(r['agents_trip_min'], r['agents_sifs'], r['agents_status'])} | "
                 f"{_money(r['nominal_profit'])} | {_money(r['passive_profit'])} | {_money(r['agents_profit'])} | "
                 f"{_money(r['saving'])} |")
    t = out["totals"]
    L += ["", "| group | n | passive trips | agent trips | passive profit | agents profit | saving |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for g, v in out["by_group"].items():
        L.append(f"| {g} | {v['n']} | {v['passive_trips']} | {v['agents_trips']} | {_money(v['passive'])} | "
                 f"{_money(v['agents'])} | {_money(v['agents'] - v['passive'])} |")
    L += ["", f"**Plant stops: without agents {t['passive_trips']}/{t['scenarios']} ({t['passive_sif_trips']} SIF trips + "
              f"{t['passive_dry_outs']} column dry-outs with no SIF); with agents {t['agents_trips']}/{t['scenarios']} "
              f"({t['agents_sif_trips']} SIF trip, {t['agents_dry_outs']} dry-outs).**",
          f"**Campaign profit: nominal {_money(t['nominal_profit'])}, passive {_money(t['passive_profit'])}, "
          f"agents {_money(t['agents_profit'])}; saving from the agents {_money(t['saving'])}.**", ""]
    (OUT / "PROFIT_CAMPAIGN.md").write_text("\n".join(L), encoding="utf-8")


def main() -> None:
    _utf8()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=["probe", "compare", "report", "one", "list"])
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--jobs", type=int, default=10)
    ap.add_argument("--ids", default="", help="comma-separated scenario ids (default: all)")
    a = ap.parse_args()
    ids = [s for s in a.ids.split(",") if s] or [s["id"] for s in SCENARIOS]
    if a.phase == "list":
        for s in SCENARIOS:
            print(s["id"], s["group"], [(f["fault"], f["at_min"]) for f in s["faults"]])
    elif a.phase == "probe":
        phase_probe(a.jobs, ids)
    elif a.phase == "compare":
        phase_compare(a.jobs, ids, tuple(a.rest) or ("agents", "passive"))
    elif a.phase == "report":
        out = report()
        print((OUT / "PROFIT_CAMPAIGN.md").read_text(encoding="utf-8"))
        print(json.dumps(out["totals"], indent=1))
    else:
        sid, mode = a.rest[0], a.rest[1] if len(a.rest) > 1 else "agents"
        sched = lift_schedule(BY_ID[sid], None if mode == "probe" else _load(sid, "probe"))
        window = PROBE_WINDOW if mode == "probe" else window_for(sched)
        res = asyncio.run(run_scenario(BY_ID[sid], mode, sched, window, verbose=True))
        res.pop("ticks")
        print(json.dumps(res, indent=1, default=str)[:8000])


if __name__ == "__main__":
    main()
