"""Run the cstr_case.py recovery architecture on the integrated simulator."""
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path

from integrated_plant import EpisodeRunner, EpisodeTiming, IntegratedPlantDAE, write_records_csv
from jpc_langgraph import LocalKnowledge
from jpc_recovery_langgraph import RecoveryConfig, build_recovery_framework, make_llm_proposer, rule_proposer
from run_v05_episode import build_spec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["nominal", "variability", "curated", "surprise"], default="nominal")
    parser.add_argument("--scenario", default="reflux_pump_trip")
    parser.add_argument("--duration-min", type=float, default=180)
    parser.add_argument("--onset-min", type=float, default=30)
    parser.add_argument("--seed", type=int, default=20261004)
    parser.add_argument("--decision-interval-min", type=float, default=5)
    parser.add_argument("--grace-min", type=float, default=1)
    parser.add_argument("--safe-hold-min", type=float, default=1)
    parser.add_argument("--deadline-min", type=float, default=10)
    parser.add_argument("--unsafe-fraction-max", type=float, default=0.2)
    parser.add_argument("--reprompt-max", type=int, default=5)
    parser.add_argument("--max-steps", type=int, default=10000)
    parser.add_argument("--ollama-model", help="Optional installed Ollama model; omitted uses offline rule smoke test")
    parser.add_argument("--output-dir", default="jpc_recovery_results")
    args = parser.parse_args()
    for value in (args.duration_min, args.decision_interval_min):
        if not math.isfinite(value) or value <= 0:
            parser.error("Duration and decision interval must be finite and positive")
    config = RecoveryConfig(grace_min=args.grace_min, safe_hold_min=args.safe_hold_min,
                            deadline_min=args.deadline_min, unsafe_fraction_max=args.unsafe_fraction_max,
                            reprompt_max=args.reprompt_max, max_steps=args.max_steps)
    proposer = rule_proposer
    if args.ollama_model:
        from langchain_ollama import ChatOllama
        proposer = make_llm_proposer(ChatOllama(model=args.ollama_model, temperature=0,
                                              format="json", timeout=120))
    env = EpisodeRunner(IntegratedPlantDAE(), build_spec(args),
                        timing=EpisodeTiming(decision_interval_min=args.decision_interval_min))
    knowledge = LocalKnowledge(Path(__file__).parent / "knowledge_graph" /
                               "Integrated_CSTR_Distillation_Knowledge_Graph.jsonld")
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    trace_path = output / f"{env.spec.name}_trace.jsonl"
    with trace_path.open("w", encoding="utf-8") as stream:
        def record(event):
            stream.write(json.dumps(event, allow_nan=False) + "\n")
            stream.flush()
            if event["event"] == "evaluation":
                print(f'Twin validation: accepted={event["accepted"]} reason={event["fail_reason"]}')
            elif event["event"] == "action_apply":
                print(f'Applied at {event["time_min"]:.1f} min: {event["action"]}')
            elif event["event"] == "monitoring":
                print(f'{event["observation"]["time_min"]:6.1f} min {event["monitoring"]["control_zone"]}')
        record({"event": "configuration", "args": vars(args), "recovery_config": asdict(config),
                "proposer": "ollama" if args.ollama_model else "offline_rule",
                "validation_model": "oracle_full_simulator_clone"})
        graph = build_recovery_framework(env, knowledge, proposer, config, record)
        try:
            state = graph.invoke({}, config={"recursion_limit": 4 * config.max_steps + 5 * (config.reprompt_max + 1) + 20})
        except Exception as exc:
            record({"event": "run_failure", "failure_type": type(exc).__name__, "message": str(exc)})
            write_records_csv(env.records, output / f"{env.spec.name}_plant.csv")
            raise
        record({"event": "run_end", "abort_reason": state["abort_reason"],
                "time_min": 60 * env.t_h, "plant_status": env.outcomes.state.status.value})
    write_records_csv(env.records, output / f"{env.spec.name}_plant.csv")
    print(f'Graph end: {state["abort_reason"]}; plant status: {env.outcomes.state.status.value}')
    print(f"Trace: {trace_path.resolve()}")


if __name__ == "__main__":
    main()
