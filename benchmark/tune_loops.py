"""Grid search of PI tunings for the slow loops (R_LIC, RB_LIC, DR_LIC, COL_PIC).

Each candidate (gain multiplier x integral time) runs the loop_tests setpoint test (step up at
5 min, back at 45 min, 85 min total). Score = settling time up + back (not settled = 80 min)
+ 0.5 x (overshoot up + back, %), plus a penalty for controller-output saturation. The best
candidate per loop is printed; apply it in integrated_plant/control.py (TUNING).

    py -3 -m benchmark.tune_loops --jobs 6       # -> benchmark_results/loop_tuning.json
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json

from .loop_tests import OUT

ATTR = {"R_LIC": "cstr_level", "RB_LIC": "reboiler_level", "DR_LIC": "drum_level", "COL_PIC": "column_pressure"}
GRID = {
    "R_LIC": ([1.0, 2.0, 3.0], [0.2, 0.5, 1.0, 2.0]),
    "RB_LIC": ([1.0, 2.0, 3.0], [0.25, 0.5, 1.0, 2.0]),
    "DR_LIC": ([1.0, 2.0, 3.0], [0.2, 0.5, 1.0, 2.0]),
    "COL_PIC": ([0.5, 1.0, 2.0, 3.0], [0.1, 0.2, 0.4]),
}


def _score(r: dict) -> float:
    up, bk = r["step_up"], r["step_back"]
    s = (up["settling_min"] if up["settling_min"] is not None else 80.0) + \
        (bk["settling_min"] if bk["settling_min"] is not None else 80.0)
    s += 0.5 * (up["overshoot_pct"] + bk["overshoot_pct"])
    mv = r["series"]["mv"]
    lo, hi = min(mv), max(mv)
    sat = sum(1 for m in mv if m <= r["mv_lim"][0] + 1e-9 or m >= r["mv_lim"][1] - 1e-9) / len(mv)
    return round(s + 100 * sat + (0 if r["pass"] else 50), 2)


_BASE: dict | None = None  # the design tunings, captured once per worker (workers are reused)


def _job(args):
    global _BASE
    loop, mult, ti = args
    from integrated_plant.control import RegulatoryControlSystem
    from . import loop_tests
    if _BASE is None:
        _BASE = dict(RegulatoryControlSystem.TUNING)
    attr = ATTR[loop]
    kp0, _ti0, db = _BASE[attr]
    RegulatoryControlSystem.TUNING = {**_BASE, attr: (kp0 * mult, ti, db)}
    loop_tests._plot_step = lambda res, title=None: ""  # no plots during the sweep
    r = loop_tests.step_test(loop)
    from integrated_plant import EpisodeRunner, IntegratedPlantDAE, nominal_episode
    from server.plant_service import PlantService
    env = EpisodeRunner(IntegratedPlantDAE(), nominal_episode(5), timing=PlantService.TIMING)
    c = getattr(env.controllers, attr)
    r["mv_lim"] = (c.u_min, c.u_max)
    return {"loop": loop, "kp": kp0 * mult, "kp_mult": mult, "ti_h": ti, "pass": r["pass"],
            "up": {k: r["step_up"][k] for k in ("overshoot_pct", "settling_min")},
            "back": {k: r["step_back"][k] for k in ("overshoot_pct", "settling_min")},
            "sifs": r["sifs"], "score": _score(r)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    jobs = [(loop, m, ti) for loop, (ms, tis) in GRID.items() for m in ms for ti in tis]
    with ProcessPoolExecutor(a.jobs) as ex:
        res = list(ex.map(_job, jobs))
    best = {}
    for r in res:
        if r["loop"] not in best or r["score"] < best[r["loop"]]["score"]:
            best[r["loop"]] = r
    for r in sorted(res, key=lambda x: (x["loop"], x["score"])):
        print(f"{r['loop']:8s} kp={r['kp']:+8.3f} (x{r['kp_mult']:g}) Ti={r['ti_h']:.2f} h  pass={r['pass']!s:5s} "
              f"up OS {r['up']['overshoot_pct']:5.1f}% settle {r['up']['settling_min']}  "
              f"back OS {r['back']['overshoot_pct']:5.1f}% settle {r['back']['settling_min']}  score {r['score']}")
    print("\nbest:", json.dumps({k: (v["kp"], v["ti_h"], v["score"]) for k, v in best.items()}))
    (OUT / "loop_tuning.json").write_text(json.dumps({"results": res, "best": best}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
