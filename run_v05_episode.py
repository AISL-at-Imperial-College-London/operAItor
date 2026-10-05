from __future__ import annotations

import argparse
from pathlib import Path

from integrated_plant import (
    CURATED_EPISODES,
    EpisodeRunner, EpisodeTiming, IntegratedPlantDAE, PassiveActor,
    curated_episode, nominal_episode, normal_variability_episode, surprise_episode,
    write_records_csv,
)


def build_spec(args):
    if args.mode == "nominal":
        return nominal_episode(args.duration_min)
    if args.mode == "variability":
        return normal_variability_episode(args.duration_min, args.seed)
    if args.mode == "surprise":
        return surprise_episode(args.duration_min, args.seed)
    if args.mode == "curated":
        return curated_episode(args.scenario, args.duration_min, args.onset_min)
    raise ValueError(args.mode)


def main():
    ap = argparse.ArgumentParser(description="Run one v0.5 autonomous-operation prototype episode")
    ap.add_argument("--mode", choices=["nominal", "variability", "curated", "surprise"], default="nominal")
    ap.add_argument("--scenario", choices=CURATED_EPISODES, default="h2_pcv_stuck_closed")
    ap.add_argument("--duration-min", type=float, default=180.0)
    ap.add_argument("--onset-min", type=float, default=30.0)
    ap.add_argument("--seed", type=int, default=20261004)
    ap.add_argument("--plant-step-min", type=float, default=0.1)
    ap.add_argument("--control-interval-min", type=float, default=1.0)
    ap.add_argument("--decision-interval-min", type=float, default=5.0)
    ap.add_argument("--output-dir", default="episode_results")
    args = ap.parse_args()

    spec = build_spec(args)
    plant = IntegratedPlantDAE()
    env = EpisodeRunner(
        plant,
        spec,
        timing=EpisodeTiming(args.plant_step_min, args.decision_interval_min, args.control_interval_min),
    )
    _, records, state = env.run(PassiveActor(), pad_to_horizon=True, progress=True)

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{spec.name}.csv"
    write_records_csv(records, path)

    print("\nEpisode outcome")
    print(f"  name:          {spec.name}")
    print(f"  status:        {state.status.value}")
    print(f"  reason:        {state.reason}")
    print(f"  terminal time: {None if state.terminal_time_h is None else 60*state.terminal_time_h:.1f} min")
    print(f"  saved:         {path}")
    if spec.scenario.events:
        print("  scheduled events:")
        for e in spec.scenario.events:
            end = "persistent" if e.end_h is None else f"{60*e.end_h:.1f} min"
            print(f"    {e.name}: {60*e.start_h:.1f} -> {end}")


if __name__ == "__main__":
    main()
