"""Train (and optionally evaluate) the PCA fault-detection model.

Training data are generated with the *current* simulator so feature names and
the nominal operating point always match what the monitoring agent sees:

    py -3 train_pca.py                 # train, ~3 min
    py -3 train_pca.py --evaluate      # + curated fault test
    py -3 train_pca.py --csv some_episode.csv  # train on a CSV

Normal data = one nominal hold plus several randomised ±10 % disturbance
episodes. Episodes run in separate processes because the DAE solver is CPU
bound (~1 s per simulated minute).
"""
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from acetone_plant.fdd import DEFAULT_MODEL_PATH, PCAMonitor, matrix_from_feature_rows, record_features

SAMPLE_EVERY = 5  # plant ticks (0.1 min) -> one sample per 0.5 min


def _simulate(job: tuple[str, float, int, float]) -> list[dict]:
    kind, duration_min, seed, onset_min = job
    from acetone_plant.plant import (EpisodeRunner, EpisodeTiming, IntegratedPlantDAE, SupervisoryAction,
                                  curated_episode, nominal_episode, normal_variability_episode)
    load = step = None
    if kind.startswith("load:"):  # normal operation at reduced throughput, e.g. load:0.7
        load, kind = float(kind.split(":")[1]), "variability"
    elif kind.startswith("step:"):  # one normal disturbance step, e.g. step:cooling_water_temperature_K:+1
        _, target, sign = kind.split(":")
        step, kind = (target, float(sign)), "nominal"
    if kind == "nominal":
        spec = nominal_episode(duration_min)
    elif kind == "variability":
        spec = normal_variability_episode(duration_min, seed)
    else:
        spec = curated_episode(kind, duration_min, onset_min)
    env = EpisodeRunner(IntegratedPlantDAE(), spec,
                        timing=EpisodeTiming(plant_step_min=0.1, decision_interval_min=0.5))
    if step is not None:  # nominal plant, one 15-min disturbance step starting at 5 min
        from acetone_plant.plant import RuntimeInputEvent
        from acetone_plant.plant.scenario import disturbance_step
        mode, value, _ = disturbance_step(step[0], step[1])
        env.reset()
        env.runtime_inputs.add(RuntimeInputEvent(f"train_{step[0]}", "disturbance", step[0], 5 / 60, 20 / 60, mode, value))
        while not env.done:
            env.step()
        settle = 0.0
    elif load is None:
        env.run(pad_to_horizon=False)
        settle = 0.0
    else:
        env.reset()
        env.apply_action(SupervisoryAction(throughput_target=100.0 * load))
        while not env.done:
            env.step()
        settle = 20.0  # skip the load-change transient
    rows = [r for r in env.records if int(r["episode.active"]) == 1 and float(r["time_min"]) >= settle]
    return [dict(record_features(r), time_min=float(r["time_min"])) for r in rows[::SAMPLE_EVERY]]


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return [record_features({k: v for k, v in row.items() if k.startswith(("meas.", "loop."))
                                 and not k.endswith(".mode")}) for row in csv.DictReader(f)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", default=DEFAULT_MODEL_PATH)
    ap.add_argument("--csv", nargs="*", help="train on existing episode CSVs instead of simulating")
    ap.add_argument("--duration-min", type=float, default=120.0)
    ap.add_argument("--seeds", type=int, nargs="*", default=[11, 22, 33])
    ap.add_argument("--loads", type=float, nargs="*", default=[0.7, 0.85],
                    help="extra normal-operation runs at these throughput fractions (agents cut load)")
    ap.add_argument("--cpv", type=float, default=0.95, help="cumulative variance retained")
    ap.add_argument("--alpha", type=float, default=0.99)
    ap.add_argument("--limits", choices=["max", "theory", "empirical"], default="max",
                    help="control limits: max(theory, empirical) [default] or the more sensitive theory")
    ap.add_argument("--evaluate", action="store_true", help="score curated fault episodes")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    if args.csv:
        rows = [r for p in args.csv for r in _read_csv(Path(p))]
    else:
        jobs = [("nominal", args.duration_min / 2, 0, 0.0)]
        jobs += [("variability", args.duration_min, s, 0.0) for s in args.seeds]
        jobs += [(f"load:{f:g}", args.duration_min, 100 + i, 0.0) for i, f in enumerate(args.loads)]
        # Every normal disturbance step, both directions, so PCA learns them as normal.
        from acetone_plant.plant.scenario import DISTURBANCE_STEPS
        targets = [t for t in DISTURBANCE_STEPS if t != "feed_temperature_K"]  # same variable as fresh_feed_*
        jobs += [(f"step:{t}:{s:+d}", 90.0, 0, 0.0) for t in targets for s in (1, -1)]  # incl. the slow recovery tail
        print(f"simulating {len(jobs)} normal-operation episodes ...", flush=True)
        with ProcessPoolExecutor(args.workers) as pool:
            rows = [r for batch in pool.map(_simulate, jobs) for r in batch]
    for r in rows:
        r.pop("time_min", None)

    X, variables = matrix_from_feature_rows(rows)
    model = PCAMonitor.fit(X, variables, cpv=args.cpv, alpha=args.alpha, limits=args.limits)
    model.save(args.output)
    scores = [model.score_vector(x) for x in X]
    fa = np.mean([s.violation for s in scores])
    print(f"saved {args.output}: {len(variables)} variables, {model.meta['n_components']} PCs "
          f"({100*model.meta['explained_variance']:.1f}% var), {X.shape[0]} samples")
    print(f"T2 limit {model.t2_limit:.2f}, SPE limit {model.spe_limit:.2f}, "
          f"training false-alarm rate {100*fa:.2f}% per sample")

    if args.evaluate:
        faults = ["reflux_pump_trip", "condenser_cooling_loss", "h2_pcv_capacity_degradation",
                  "reactor_pressure_sensor_low_bias", "reflux_drum_leak", "reactor_heater_stuck_high"]
        onset = 15.0
        print(f"\nevaluating {len(faults)} curated faults (onset {onset:g} min, 45 min horizon) ...", flush=True)
        with ProcessPoolExecutor(args.workers) as pool:
            results = list(pool.map(_simulate, [(f, 45.0, 0, onset) for f in faults]))
        print(f"{'fault':36s} {'pre-onset FAR':>13s} {'delay [min]':>12s}")
        for name, rows in zip(faults, results):
            t = np.array([r["time_min"] for r in rows])
            v = np.array([model.score(r).violation for r in rows])
            pre = v[t < onset]
            # Detection = 3 consecutive violations after onset (as in the monitoring agent).
            post = np.where(t >= onset)[0]
            delay = None
            for i in post[:-2]:
                if v[i] and v[i + 1] and v[i + 2]:
                    delay = t[i] - onset
                    break
            print(f"{name:36s} {100*pre.mean():12.1f}% {'not detected' if delay is None else f'{delay:12.1f}':>12s}")


if __name__ == "__main__":
    main()
