"""Monitoring/propose/twin-evaluate/reprompt/apply adaptation of cstr_case.py.

This module uses the integrated plant's units and operator actions. Rollout
validation clones the full simulator, including scheduled faults (oracle twin).
The proposer receives only operator observations and retrieved KG evidence.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, replace
import json
import math
import time
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from integrated_plant.actions import SupervisoryAction
from jpc_langgraph import baseline_diagnosis, baseline_planner, validate_proposal


@dataclass(frozen=True)
class RecoveryConfig:
    grace_min: float = 1.0
    safe_hold_min: float = 1.0
    deadline_min: float = 10.0
    unsafe_fraction_max: float = 0.20
    reprompt_max: int = 5
    max_steps: int = 10000
    one_shot: bool = True

    def __post_init__(self):
        for name in ("grace_min", "safe_hold_min", "deadline_min"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.safe_hold_min <= 0 or self.deadline_min <= 0:
            raise ValueError("Hold and deadline must be positive")
        if not 0 <= self.unsafe_fraction_max <= 1:
            raise ValueError("unsafe_fraction_max must be in [0, 1]")
        if self.reprompt_max < 0 or self.max_steps < 1:
            raise ValueError("Invalid retry or step limit")


class RecoveryState(TypedDict, total=False):
    observation: dict
    monitoring: dict
    knowledge: list[dict]
    plan: dict
    action: SupervisoryAction
    errors: list[str]
    eval_pass: bool
    eval_metrics: dict
    feedback: str
    reprompt_count: int
    steps: int
    applied: bool
    previous_abnormal: bool
    abort_reason: str


def inspect_operation(env, observation: dict, *, truth: bool = False) -> dict:
    """Integrated-model safety envelope + alarm/control-margin monitoring.

    Numeric limits come from this plant's SIF configuration, not the old CSTR.
    Saturation is judged against each loop's actual MV bounds.
    """
    m = env.plant.measurements(env.y) if truth else observation["measurements"]
    c = env.safety.cfg
    intervals = {
        "cstr_temperature_K": (-math.inf, c.cstr_temperature_hh_K),
        "cstr_pressure_bar": (-math.inf, c.cstr_pressure_hh_bar),
        "cstr_holdup_kmol": (c.cstr_level_ll_kmol, c.cstr_level_hh_kmol),
        "reboiler_holdup_kmol": (c.reboiler_level_ll_kmol, c.reboiler_level_hh_kmol),
        "drum_holdup_kmol": (c.drum_level_ll_kmol, c.drum_level_hh_kmol),
        "column_pressure_bar": (-math.inf, c.column_pressure_hh_bar),
    }
    unsafe = [tag for tag, (lo, hi) in intervals.items()
              if not math.isfinite(m[tag]) or not lo < m[tag] < hi]
    saturated = []
    invalid = []
    for loop, info in observation["controllers"].items():
        output, lo, hi = info["last_output"], info["mv_min"], info["mv_max"]
        if not math.isfinite(output) or not lo <= output <= hi:
            invalid.append(loop)
        elif hi > lo and (output - lo) / (hi - lo) >= 0.95:
            # High demand with significant PV error indicates depleted margin.
            span = info["sp_max"] - info["sp_min"]
            if abs(info.get("pv", info["setpoint"]) - info["setpoint"]) > 0.05 * span:
                saturated.append(loop)
    failed = [train for train, info in observation["pump_trains"].items()
              if info["selected_failed"]]
    alarms = [tag for tag, status in observation["alarm_states"].items()
              if status in ("ACTIVE_UNACK", "ACTIVE_ACK")]
    safe = not (unsafe or invalid or failed or observation["sif_latched"])
    zone = "UNSAFE" if not safe else "WARNING" if saturated or alarms else "SAFE"
    return {"safe_now": safe, "control_zone": zone, "unsafe_tags": unsafe,
            "invalid_controller_outputs": invalid, "saturated_loops": saturated,
            "failed_trains": failed, "active_alarms": alarms,
            "abnormal": bool(unsafe or invalid or failed or saturated or alarms or
                             observation["sif_latched"])}


def compile_plan(plan, observation, catalog):
    """Validate all commands before combining them into one operator action."""
    if (not isinstance(plan, dict) or set(plan) != {"commands", "reasoning"}
            or not isinstance(plan["reasoning"], str)
            or not isinstance(plan["commands"], list) or not plan["commands"]):
        return SupervisoryAction.no_action(), ["Expected nonempty commands list and reasoning string"]
    combined = SupervisoryAction()
    seen = set()
    for proposal in plan["commands"]:
        action, errors = validate_proposal(proposal, observation, catalog)
        if errors:
            return SupervisoryAction.no_action(), errors
        identity = (proposal["type"], proposal.get("target"))
        if identity in seen or (proposal["type"] == "NO_ACTION" and len(plan["commands"]) != 1):
            return SupervisoryAction.no_action(), ["Duplicate command or mixed NO_ACTION"]
        seen.add(identity)
        combined = replace(combined,
            throughput_target=action.throughput_target if action.throughput_target is not None else combined.throughput_target,
            setpoints={**combined.setpoints, **action.setpoints},
            auxiliary_commands={**combined.auxiliary_commands, **action.auxiliary_commands},
            start_spares=combined.start_spares + action.start_spares)
    return combined, []


def rollout_validate(env, action: SupervisoryAction, config: RecoveryConfig) -> dict:
    """Full remaining-horizon rollout at the plant clock; live plant stays frozen.

    The whole EpisodeRunner is deep-copied, preserving integrator/controller,
    noise RNG, fault schedules, SIF latches and persistent operator state.
    """
    twin = copy.deepcopy(env)
    twin.records.clear()
    twin.timing = replace(twin.timing, decision_interval_min=twin.timing.plant_step_min)
    start_min = 60 * twin.t_h
    unsafe_min = judged_min = consecutive_min = 0.0
    time_to_safe = None
    trajectory = []
    rejection = ""
    first = True
    while not twin.done:
        before = 60 * twin.t_h
        obs, outcome = twin.step(action if first else SupervisoryAction.no_action())
        if first and twin.last_action_result.rejected:
            rejection = "action_rejected"
        first = False
        elapsed = obs["time_min"] - start_min
        delta = obs["time_min"] - before
        check = inspect_operation(twin, obs, truth=True)
        trajectory.append({"time_min": obs["time_min"], "status": outcome.status.value,
                           "measurements": dict(obs["measurements"]), **check})
        if check["invalid_controller_outputs"]:
            rejection = "invalid_controller_outputs"
        # Count only time after grace, using elapsed physical time rather than
        # including ignored startup/grace intervals in the denominator.
        judged_delta = max(0.0, min(delta, elapsed - config.grace_min))
        if judged_delta:
            judged_min += judged_delta
            if check["safe_now"]:
                consecutive_min += judged_delta
                if time_to_safe is None and consecutive_min + 1e-9 >= config.safe_hold_min:
                    time_to_safe = elapsed
            else:
                unsafe_min += judged_delta
                consecutive_min = 0
        if delta <= 0:
            rejection = "no_clock_progress"
            break
    fraction = unsafe_min / judged_min if judged_min else 1.0
    terminal = twin.outcomes.state.status.value
    # Curated persistent faults often end TIMEOUT even with safe operation;
    # the graph records validation separately from benchmark recovery outcome.
    if terminal in {"SAFE_TRIP", "DAMAGE", "MODEL_LIMIT", "NUMERICAL_FAILURE"}:
        rejection = terminal
    if not rejection:
        if time_to_safe is None:
            rejection = "no_persistent_safe_state"
        elif time_to_safe > config.deadline_min:
            rejection = "slow_recovery"
        elif fraction > config.unsafe_fraction_max:
            rejection = "unsafe_fraction"
        elif not trajectory or not trajectory[-1]["safe_now"]:
            rejection = "unsafe_at_end"
    return {"accepted": not rejection, "fail_reason": rejection,
            "time_to_safe_min": time_to_safe, "unsafe_fraction": fraction,
            "judged_min": judged_min, "terminal_status": terminal,
            "trajectory": trajectory, "validation_model": "oracle_full_simulator_clone"}


def rule_proposer(context: dict) -> dict:
    obs = context["observation"]
    diagnosis = baseline_diagnosis(obs)
    return {"commands": [baseline_planner(obs, diagnosis, context["knowledge"])],
            "reasoning": "Offline smoke-test policy: select a healthy spare for a failed duty pump."}


def make_llm_proposer(llm):
    """Provider-neutral LangChain chat-model adapter, including evaluation feedback."""
    def propose(context):
        system = (
            "You propose corrective operator actions for the integrated acetone CSTR and column. "
            "Return JSON only: {\"commands\": [command, ...], \"reasoning\": \"brief justification\"}. "
            "Each command must be one of: {\"type\":\"NO_ACTION\"}, "
            "{\"type\":\"START_SPARE\",\"target\":train}, "
            "{\"type\":\"SET_THROUGHPUT\",\"value\":number}, "
            "{\"type\":\"SET_SETPOINT\",\"target\":loop,\"value\":number}, "
            "{\"type\":\"OPERATE_AUXILIARY\",\"target\":device,\"value\":number}. "
            "Use supplied KG relationships and cite node IDs in your justification. "
            "Respect catalog bounds and units. This reactor is endothermic; use its KG "
            "rather than assuming that feed reduction improves temperature or pressure. "
            "After a failed rollout, use feedback and choose a different proposal. "
            "Never change fault schedules, utility variables, safety thresholds or bypasses."
        )
        response = llm.invoke([("system", system), ("human", json.dumps(context))])
        return json.loads(response.content)
    return propose


def build_recovery_framework(env, knowledge, proposer: Callable = rule_proposer,
                             config: RecoveryConfig = RecoveryConfig(), on_event: Callable | None = None):
    """Whole-episode graph. Attach an event sink for proposals/rollouts/live rows."""
    emit = on_event or (lambda event: None)

    def initializing(state):
        return {"steps": 0, "applied": False, "previous_abnormal": False,
                "abort_reason": "", "reprompt_count": 0, "feedback": ""}

    def plant(state):
        if not env.done:
            env.step(SupervisoryAction.no_action())
        return {"steps": state["steps"] + 1}

    def monitoring(state):
        obs = env.observation()
        check = inspect_operation(env, obs)
        should_act = (check["abnormal"] and not state["previous_abnormal"] and
                      not env.done and not (config.one_shot and state["applied"]))
        emit({"event": "monitoring", "observation": obs, "monitoring": check})
        return {"observation": obs, "monitoring": {**check, "should_act": should_act},
                "previous_abnormal": check["abnormal"],
                "reprompt_count": 0, "feedback": ""}

    def action_propose(state):
        diagnosis = baseline_diagnosis(state["observation"])
        evidence = knowledge.retrieve(diagnosis["retrieval_terms"], limit=12)
        context = {"observation": state["observation"], "monitoring": state["monitoring"],
                   "knowledge": evidence, "action_catalog": env.action_catalog(),
                   "acceptance_config": asdict(config),
                   "feedback": state["feedback"], "attempt": state["reprompt_count"],
                   "previous_plan": state.get("plan") if state["reprompt_count"] else None}
        start = time.perf_counter()
        try:
            plan = proposer(copy.deepcopy(context))
            action, errors = compile_plan(plan, state["observation"], env.action_catalog())
        except Exception as exc:
            emit({"event": "proposal_failure", "failure_type": type(exc).__name__,
                  "message": str(exc), "context": context})
            raise
        emit({"event": "proposal", "context": context, "plan": plan,
              "validation_errors": errors, "latency_s": time.perf_counter() - start})
        return {"plan": plan, "action": action, "errors": errors, "knowledge": evidence}

    def evaluation(state):
        result = ({"accepted": False, "fail_reason": "invalid_proposal",
                   "errors": state["errors"]} if state["errors"] else
                  rollout_validate(env, state["action"], config))
        emit({"event": "evaluation", "plan": state["plan"], **result})
        return {"eval_pass": result["accepted"], "eval_metrics": result}

    def reprompting(state):
        feedback = {key: value for key, value in state["eval_metrics"].items() if key != "trajectory"}
        return {"reprompt_count": state["reprompt_count"] + 1,
                "feedback": json.dumps(feedback)}

    def action_apply(state):
        result = env.apply_action(state["action"])
        emit({"event": "action_apply", "time_min": 60 * env.t_h,
              "action": asdict(state["action"]), "result": asdict(result)})
        return {"applied": result.ok,
                "abort_reason": "live_action_rejected" if not result.ok else ""}

    def rejected(state):
        emit({"event": "retry_limit", "reason": state["eval_metrics"].get("fail_reason")})
        return {"abort_reason": "reprompt_limit"}

    def check_end(state):
        reason = state["abort_reason"]
        if not reason and env.done:
            reason = env.outcomes.state.status.value
        if not reason and state["steps"] >= config.max_steps:
            reason = "max_steps"
        return {"abort_reason": reason}

    graph = StateGraph(RecoveryState)
    for name, node in {"initializing": initializing, "plant": plant, "monitoring": monitoring,
                       "action_propose": action_propose, "evaluation": evaluation,
                       "reprompting": reprompting, "action_apply": action_apply,
                       "rejected": rejected, "check_end": check_end}.items():
        graph.add_node(name, node)
    graph.add_edge(START, "initializing")
    graph.add_edge("initializing", "monitoring")
    graph.add_edge("plant", "monitoring")
    graph.add_conditional_edges("monitoring", lambda s: "act" if s["monitoring"]["should_act"] else "check",
                                {"act": "action_propose", "check": "check_end"})
    graph.add_edge("action_propose", "evaluation")
    graph.add_conditional_edges("evaluation", lambda s: "apply" if s["eval_pass"] else
                                "reject" if s["reprompt_count"] >= config.reprompt_max else "retry",
                                {"apply": "action_apply", "reject": "rejected", "retry": "reprompting"})
    graph.add_edge("reprompting", "action_propose")
    graph.add_edge("action_apply", "check_end")
    graph.add_edge("rejected", "check_end")
    graph.add_conditional_edges("check_end", lambda s: "end" if s["abort_reason"] else "next",
                                {"end": END, "next": "plant"})
    return graph.compile()
