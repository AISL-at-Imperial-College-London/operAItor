"""Run the offline LangGraph baseline and save a decision audit trail."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path

from integrated_plant import EpisodeRunner, EpisodeTiming, IntegratedPlantDAE, write_records_csv
from jpc_langgraph import LocalKnowledge, build_framework
from run_v05_episode import build_spec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["nominal", "variability", "curated", "surprise"], default="nominal")
    parser.add_argument("--scenario", default="reflux_pump_trip")
    parser.add_argument("--duration-min", type=float, default=180)
    parser.add_argument("--onset-min", type=float, default=30)
    parser.add_argument("--seed", type=int, default=20261004)
    parser.add_argument("--decision-interval-min", type=float, default=5)
    parser.add_argument("--output-dir", default="jpc_results")
    args = parser.parse_args()
    if not math.isfinite(args.duration_min) or args.duration_min <= 0:
        parser.error("--duration-min must be positive and finite")
    if not math.isfinite(args.decision_interval_min) or args.decision_interval_min <= 0:
        parser.error("--decision-interval-min must be positive and finite")
    env = EpisodeRunner(IntegratedPlantDAE(), build_spec(args),
                        timing=EpisodeTiming(decision_interval_min=args.decision_interval_min))
    kg_path = Path(__file__).parent / "knowledge_graph" / "Integrated_CSTR_Distillation_Knowledge_Graph.jsonld"
    graph = build_framework(env, LocalKnowledge(kg_path))
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    # Distinct names per scenario; repeated runs of the same scenario overwrite.
    with (output / f"{env.spec.name}_decisions.jsonl").open("w", encoding="utf-8") as stream:
        while not env.done:
            state = graph.invoke({})
            row = {key: value for key, value in state.items() if key != "action"}
            row["action"] = asdict(state["action"])
            stream.write(json.dumps(row, allow_nan=False) + "\n")
            stream.flush()
            print(f'{state["evaluation"]["end_min"]:6.1f} min '
                  f'{state["proposal"]} -> {state["evaluation"]["status"]}')
    write_records_csv(env.records, output / f"{env.spec.name}_plant.csv")
    print(f"Outcome: {env.outcomes.state.status.value}; {env.outcomes.state.reason}")
    print(f"Results: {output.resolve()}")


if __name__ == "__main__":
    main()
