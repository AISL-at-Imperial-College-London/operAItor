"""LangGraph research scaffold for the local CSTR/distillation simulator.

The baseline uses rules, not an LLM. Replace diagnose/planner callables to
implement the paper's method without granting access to simulator ground truth.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from integrated_plant.actions import SupervisoryAction


class FrameworkState(TypedDict, total=False):
    observation: dict
    diagnosis: dict
    knowledge: list[dict]
    proposal: dict
    action: SupervisoryAction
    validation_errors: list[str]
    next_observation: dict
    evaluation: dict


class LocalKnowledge:
    """Bounded lexical retrieval of actual JSON-LD nodes and their relations.

    This is a simple baseline, not SPARQL inference or semantic retrieval.
    """

    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.nodes = [node for graph in data for node in graph.get("@graph", [])]

    def retrieve(self, terms: list[str], limit: int = 6) -> list[dict]:
        terms = [term.lower() for term in terms if term]
        scored = [(sum(term in json.dumps(node).lower() for term in terms), node)
                  for node in self.nodes]
        scored.sort(key=lambda item: (-item[0], item[1].get("@id", "")))
        return [copy.deepcopy(node) for score, node in scored[:limit] if score]


def baseline_diagnosis(observation: dict) -> dict:
    failed = [train for train, info in observation["pump_trains"].items()
              if info["selected_failed"]]
    alarms = [tag for tag, status in observation["alarm_states"].items()
              if status in ("ACTIVE_UNACK", "ACTIVE_ACK")]
    return {
        "hypothesis": "Selected pump unavailable" if failed else
                      "Abnormal indication; cause unresolved" if alarms else
                      "No active abnormal indication",
        "failed_trains": failed,
        "active_alarms": alarms,
        "retrieval_terms": failed + alarms + ["standby" if failed else "operator"],
    }


def baseline_planner(observation: dict, diagnosis: dict, knowledge: list[dict]) -> dict:
    # The baseline makes no causal claims from lexical retrieval. A research
    # planner can use the evidence nodes supplied in `knowledge`.
    for train in diagnosis["failed_trains"]:
        info = observation["pump_trains"][train]
        if info["active_pump"] != info["spare"] and not info["spare_failed"]:
            return {"type": "START_SPARE", "target": train}
    return {"type": "NO_ACTION"}


def validate_proposal(proposal: dict, observation: dict, catalog: dict):
    """Validate a deliberately small action schema without mutating the plant.

    Bounds validate operator authority, not predicted process safety. The
    simulator's independent SIF arbitration remains authoritative.
    """
    try:
        if not isinstance(proposal, dict):
            raise ValueError("Proposal must be a dictionary")
        kind = proposal.get("type")
        supported = {"NO_ACTION", "START_SPARE", "OPERATE_AUXILIARY",
                     "SET_THROUGHPUT", "SET_SETPOINT"}
        if kind not in supported or kind not in catalog["actions"]:
            raise ValueError(f"Unsupported action: {kind}")
        fields = {"type"} if kind == "NO_ACTION" else {"type", "value"} if kind == "SET_THROUGHPUT" else {"type", "target"} if kind == "START_SPARE" else {"type", "target", "value"}
        if set(proposal) != fields:
            raise ValueError(f"Expected exactly these fields: {sorted(fields)}")
        if kind == "NO_ACTION":
            return SupervisoryAction.no_action(), []
        target = proposal.get("target")
        if kind == "START_SPARE":
            if target not in observation["pump_trains"]:
                raise ValueError("Unknown pump train")
            info = observation["pump_trains"][target]
            if info["spare_failed"]:
                raise ValueError("Standby pump unavailable")
            return SupervisoryAction(start_spares=(target,)), []
        value = proposal["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Value must be a finite number")
        if kind == "SET_THROUGHPUT":
            lo, hi = catalog["throughput_bounds_kmol_h"]
            action = SupervisoryAction(throughput_target=value)
        elif kind == "SET_SETPOINT":
            if target not in catalog["controllers"]:
                raise ValueError("Unknown controller")
            loop = catalog["controllers"][target]
            lo, hi = loop["sp_min"], loop["sp_max"]
            action = SupervisoryAction(setpoints={target: value})
        else:
            if target not in catalog["auxiliaries"]:
                raise ValueError("Unknown auxiliary")
            bounds = catalog["auxiliaries"][target]
            lo, hi = bounds["min"], bounds["max"]
            action = SupervisoryAction(auxiliary_commands={target: value})
        if not lo <= value <= hi:
            raise ValueError(f"Value outside [{lo}, {hi}]")
        return action, []
    except (ValueError, TypeError, KeyError) as exc:
        return SupervisoryAction.no_action(), [str(exc)]


def build_framework(env, knowledge: LocalKnowledge,
                    diagnose: Callable = baseline_diagnosis,
                    planner: Callable = baseline_planner):
    """Compile one decision cycle. Caller loops until env.done.

    Process time advances only in execute. No checkpointer is attached: graph
    checkpoints alone cannot restore the mutable physical simulator.
    """
    def observe(state):
        return {"observation": env.observation()}

    def diagnosis(state):
        return {"diagnosis": diagnose(copy.deepcopy(state["observation"]))}

    def retrieve(state):
        return {"knowledge": knowledge.retrieve(state["diagnosis"]["retrieval_terms"])}

    def plan(state):
        proposal = planner(copy.deepcopy(state["observation"]),
                           copy.deepcopy(state["diagnosis"]),
                           copy.deepcopy(state["knowledge"]))
        return {"proposal": proposal}

    def validate(state):
        action, errors = validate_proposal(state["proposal"], state["observation"],
                                           env.action_catalog())
        return {"action": action, "validation_errors": errors}

    def execute(state):
        obs, outcome = env.step(state["action"])
        return {"next_observation": obs, "evaluation": {
            "status": outcome.status.value, "reason": outcome.reason,
            "terminated": env.done,
        }}

    def evaluate(state):
        before, after = state["observation"], state["next_observation"]
        evaluation = dict(state["evaluation"])
        evaluation.update({
            "start_min": before["time_min"], "end_min": after["time_min"],
            "active_alarms_before": before["active_alarm_count"],
            "active_alarms_after": after["active_alarm_count"],
            "action_result": after["last_action_result"],
            "measurement_change": {tag: value - before["measurements"][tag]
                for tag, value in after["measurements"].items()
                if tag in before["measurements"]},
        })
        return {"evaluation": evaluation}

    graph = StateGraph(FrameworkState)
    nodes = {"observe": observe, "diagnose": diagnosis, "retrieve": retrieve,
             "plan": plan, "validate": validate, "execute": execute,
             "evaluate": evaluate}
    for name, node in nodes.items():
        graph.add_node(name, node)
    graph.add_edge(START, "observe")
    graph.add_conditional_edges("observe", lambda state: "end" if env.done else "continue",
                                {"end": END, "continue": "diagnose"})
    sequence = list(nodes)[1:]
    for source, target in zip(sequence, sequence[1:]):
        graph.add_edge(source, target)
    graph.add_edge("evaluate", END)
    return graph.compile()
