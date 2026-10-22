"""Tuning of the 8 PI loops on the long setpoint test (loop_tests: step at 5 min, back at 125 min, end 245 min).

For every loop, a grid of gain x integral time x PV filter is run from the current tuning (PLANT_TUNING, default
v1.2). The deadband follows the filter: two standard deviations of the filtered sensor noise (no filter: the
loop's current deadband), so the output does not chase noise. Each candidate is measured on the true PV:
  iae       mean |PV - SP| over the run, as a fraction of the step (speed and offset together)
  offset    mean |PV - SP| over the last 20 min of each hold, as a fraction of the step
  os        overshoot, up and back [%]
  travel    total output movement, in full strokes of the MV range (valve wear, noise chasing)
  sat       fraction of time the output is saturated
A candidate is admissible if it passes the loop test (right direction, settles, no SIF or priority-1 alarm),
overshoots at most 25 % and saturates at most 2 % of the time, and ranked by
  score = iae + offset + 0.002 x (os up + os back) + 0.02 x travel.

Better tracking alone made the plant less fault-tolerant (faster loops push their valves harder and carry a fault
into the next unit sooner), so the tuning is chosen by a second, constrained search ("robust"): starting from v1.1,
loop by loop, the best-ranked candidates are tried with every other loop at its current choice, and one is kept
only if, in the probe phase of the 20-scenario fault benchmark (faults never lifted, nobody acting), no scenario
trips more than 1 min earlier than with v1.1 and none trips that did not, and the loop's own score beats v1.1's.
Two passes over the loops catch interactions.

    py -3 -m operaitor.benchmark.tune_loops sweep --jobs 4    # step-test sweep -> benchmark_results/loop_tuning.json
    py -3 -m operaitor.benchmark.tune_loops robust --jobs 4   # constrained choice -> loop_tuning_robust.json
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import math

import numpy as np

from .loop_tests import OUT, STEPS

KP_MULT = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0)
TI_MULT = (0.5, 1.0, 2.0, 4.0)
FILTER_MIN = (0.0, 2.0, 4.0)
CONTROL_DT_MIN = 1.0   # the BPCS executes once a minute (PlantService.TIMING)


def noise_sigma(loop: str) -> float:
    """Standard deviation of the loop's measured PV at the design point (measurement.default_sensor_specs)."""
    from operaitor.plant import IntegratedPlantDAE
    from operaitor.plant.control import RegulatoryControlSystem
    from operaitor.plant.measurement import default_sensor_specs
    p = IntegratedPlantDAE()
    truth = p.measurements(p.nominal_state())
    return default_sensor_specs(truth)[RegulatoryControlSystem.LOOP_SPECS[loop].pv_tag].sigma


def filtered_sigma(sigma: float, filter_min: float) -> float:
    """White noise through the sampled first-order filter of PIController: sigma * sqrt(a / (2 - a))."""
    if filter_min <= 0:
        return sigma
    a = CONTROL_DT_MIN / (filter_min + CONTROL_DT_MIN)
    return sigma * math.sqrt(a / (2 - a))


def measure(r: dict, u_lim: tuple[float, float]) -> dict:
    s = r["series"]
    t, pv, sp, mv = (np.array(s[k]) for k in ("t", "pv", "sp", "mv"))
    step = abs(r["sp1"] - r["sp0"])
    dt = np.diff(t, prepend=t[0])
    err = np.abs(pv - sp)
    tail = lambda t1: float(err[(t > t1 - 20) & (t <= t1 + 1e-9)].mean() / step)  # noqa: E731
    span = u_lim[1] - u_lim[0]
    sat = float(np.mean((mv <= u_lim[0] + 1e-9 * span) | (mv >= u_lim[1] - 1e-9 * span)))
    return {"iae": float((err * dt).sum() / (t[-1] - t[0]) / step), "offset": 0.5 * (tail(r["t_back"]) + tail(r["t_end"])),
            "os_up": r["step_up"]["overshoot_pct"], "os_back": r["step_back"]["overshoot_pct"],
            "settle_up": r["step_up"]["settling_min"], "settle_back": r["step_back"]["settling_min"],
            "travel": float(np.abs(np.diff(mv)).sum() / span), "sat": sat, "pass": r["pass"], "sifs": r["sifs"]}


def admissible(m: dict) -> bool:
    return m["pass"] and max(m["os_up"], m["os_back"]) <= 25.0 and m["sat"] <= 0.02


def score(m: dict) -> float:
    return m["iae"] + m["offset"] + 0.002 * (m["os_up"] + m["os_back"]) + 0.02 * m["travel"]


def _job(args: tuple) -> dict:
    loop, kp, ti_h, db, filt_h = args
    from operaitor.plant import EpisodeRunner, IntegratedPlantDAE, nominal_episode
    from operaitor.plant.control import RegulatoryControlSystem
    from operaitor.server.plant_service import PlantService
    from . import loop_tests
    attr = RegulatoryControlSystem.LOOP_SPECS[loop].controller_attr
    RegulatoryControlSystem.TUNING = {**RegulatoryControlSystem.TUNING, attr: (kp, ti_h, db, filt_h)}
    r = loop_tests.step_test(loop, plot=False)
    c = getattr(EpisodeRunner(IntegratedPlantDAE(), nominal_episode(5), timing=PlantService.TIMING).controllers, attr)
    m = measure(r, (c.u_min, c.u_max))
    return {"loop": loop, "tuning": [kp, ti_h, db, filt_h], **m, "admissible": admissible(m), "score": round(score(m), 5)}


def grid(loop: str) -> list[tuple]:
    from operaitor.plant.control import RegulatoryControlSystem
    attr = RegulatoryControlSystem.LOOP_SPECS[loop].controller_attr
    kp0, ti0, db0, _ = RegulatoryControlSystem.TUNING[attr]
    sigma = noise_sigma(loop)
    jobs = []
    for f in FILTER_MIN:
        db = db0 if f == 0 else round(2 * filtered_sigma(sigma, f), 6)
        jobs += [(loop, kp0 * km, ti0 * tm, db, f / 60.0) for km in KP_MULT for tm in TI_MULT]
    return jobs


def sweep(a) -> None:
    jobs = [j for loop in a.loops for j in grid(loop)]
    with ProcessPoolExecutor(a.jobs) as ex:
        res = list(ex.map(_job, jobs, chunksize=4))
    best = {}
    for r in res:
        if r["admissible"] and (r["loop"] not in best or r["score"] < best[r["loop"]]["score"]):
            best[r["loop"]] = r
    for loop in a.loops:
        rows = sorted((r for r in res if r["loop"] == loop), key=lambda r: (not r["admissible"], r["score"]))
        print(f"\n{loop}: {len(rows)} candidates, {sum(r['admissible'] for r in rows)} admissible")
        for r in rows[:5]:
            kp, ti, db, f = r["tuning"]
            print(f"  kp {kp:+9.4g} Ti {60 * ti:5.1f} min db {db:.4g} filter {60 * f:3.0f} min | iae {r['iae']:.3f} "
                  f"offset {r['offset']:.3f} OS {r['os_up']:4.0f}/{r['os_back']:4.0f} % travel {r['travel']:6.2f} "
                  f"sat {r['sat']:.2f} score {r['score']:.4f}{'' if r['admissible'] else '  (not admissible)'}")
    print("\nbest:", json.dumps({k: v["tuning"] for k, v in best.items()}))
    OUT.mkdir(exist_ok=True)
    (OUT / "loop_tuning.json").write_text(json.dumps({"results": res, "best": best}, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- robust: no loss of fault tolerance
ROBUST_ORDER = ["R_TIC", "R_PIC", "BOT_TIC", "R_LIC", "RB_LIC", "DR_LIC", "TOP_TIC", "COL_PIC"]
TRIP_TOLERANCE_MIN = 1.0


def _probe_job(args: tuple) -> tuple:
    sid, tuning = args
    import asyncio
    from operaitor.plant.control import RegulatoryControlSystem
    from .profit_campaign import BY_ID, PROBE_WINDOW, lift_schedule, run_scenario
    RegulatoryControlSystem.TUNING = {k: tuple(v) for k, v in tuning.items()}
    scn = BY_ID[sid]
    r = asyncio.run(run_scenario(scn, "probe", lift_schedule(scn, None), PROBE_WINDOW))
    return sid, r["trip_min"], r["profit_usd"]["total"]


def _step_job(args: tuple) -> tuple:
    loop, tuning = args
    from operaitor.plant import EpisodeRunner, IntegratedPlantDAE, nominal_episode
    from operaitor.plant.control import RegulatoryControlSystem
    from operaitor.server.plant_service import PlantService
    from . import loop_tests
    RegulatoryControlSystem.TUNING = {k: tuple(v) for k, v in tuning.items()}
    r = loop_tests.step_test(loop, plot=False)
    c = getattr(EpisodeRunner(IntegratedPlantDAE(), nominal_episode(5), timing=PlantService.TIMING).controllers,
                RegulatoryControlSystem.LOOP_SPECS[loop].controller_attr)
    m = measure(r, (c.u_min, c.u_max))
    return loop, m


def robust(a) -> None:
    from operaitor.plant.control import RegulatoryControlSystem as R
    from .profit_campaign import SCENARIOS
    ids = [s["id"] for s in SCENARIOS]
    sweep_res = json.loads((OUT / "loop_tuning.json").read_text(encoding="utf-8"))["results"]
    base = {k: list(v) for k, v in R.TUNINGS["v1.1"].items()}
    attr = {loop: R.LOOP_SPECS[loop].controller_attr for loop in ROBUST_ORDER}
    with ProcessPoolExecutor(a.jobs) as ex:
        def probe(tuning: dict) -> dict:
            return {sid: (t, p) for sid, t, p in ex.map(_probe_job, [(sid, tuning) for sid in ids])}

        def loop_score(loop: str, tuning: dict) -> tuple[float, dict]:
            _, m = next(iter(ex.map(_step_job, [(loop, tuning)])))
            return (score(m) if admissible(m) else float("inf")), m

        ref = probe(base)
        print("v1.1 probe:", {k: v[0] for k, v in ref.items()}, flush=True)

        def robust_ok(res: dict) -> tuple[bool, str]:
            for sid, (t, _) in res.items():
                t0 = ref[sid][0]
                if t is not None and (t0 is None or t < t0 - TRIP_TOLERANCE_MIN):
                    return False, f"{sid} trips at {t} min (v1.1: {t0})"
            return True, ""

        chosen, log = dict(base), []
        for pass_no in (1, 2):
            for loop in ROBUST_ORDER:
                own, _ = loop_score(loop, base)
                cands = sorted((r for r in sweep_res if r["loop"] == loop and r["admissible"]), key=lambda r: r["score"])
                current = chosen[attr[loop]]
                for c in cands[:a.candidates]:
                    if list(c["tuning"]) == list(current):
                        break                                   # the current choice ranks higher than what is left
                    trial = {**chosen, attr[loop]: list(c["tuning"])}
                    sc, m = loop_score(loop, trial)
                    if sc >= own:
                        verdict = f"no better than v1.1 here (score {sc:.4f} vs {own:.4f})"
                        ok = False
                    else:
                        ok, why = robust_ok(probe(trial))
                        verdict = "accepted" if ok else f"rejected: {why}"
                    log.append({"pass": pass_no, "loop": loop, "tuning": c["tuning"], "score": sc, "verdict": verdict})
                    print(f"pass {pass_no} {loop:8s} {c['tuning']} -> {verdict}", flush=True)
                    if ok:
                        chosen = trial
                        break
        final = probe(chosen)
    print("\nchosen:", json.dumps(chosen))
    (OUT / "loop_tuning_robust.json").write_text(json.dumps({"chosen": chosen, "v1_1_probe": ref, "probe": final, "log": log},
                                                            indent=1), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["sweep", "robust"], nargs="?", default="sweep")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--loops", nargs="*", default=list(STEPS))
    ap.add_argument("--candidates", type=int, default=36, help="robust: candidates tried per loop and pass")
    a = ap.parse_args()
    sweep(a) if a.phase == "sweep" else robust(a)


if __name__ == "__main__":
    main()
