"""The recovery LangGraph shared by the live server and the benchmark.

One ``graph.ainvoke`` is one decision cycle on one operator observation:

    START → monitoring ──(no fault / cooldown)────────────────────────────► END
               │  hypotheses (PCA T²/SPE contributions + alarm/pump/loop rules)
               ▼
          kg_retrieval      typed KG walk seeded by the hypotheses
               ▼
          planning          planning agent: target fault, objective, KG levers, constraints
               ▼
          action_propose ◄──────────────── reprompt ◄──────┐
               ▼                                           │ not restored (or plan not
          twin_evaluate     digital twin: simulate the     │ executable), retries left
               ▼            action, measure safety + restoration
          validate          validate agent: is the plant restored? ┘
               │ yes                    └── retries exhausted ──► reject ─(best safe candidate?)─┐
               ▼                                                    │ none → END              │
           dispatch  (apply, schedule, or ask the operator) ◄──────────────── mitigation ─────────┘

Adapted from ctrl-alt-recover/case_studies/cstr_case/cstr_case.py (monitor →
propose → twin evaluate → reprompt → apply). Every node is ``async``; CPU-bound
work (PCA, KG walk, twin rollout) runs on worker threads and LLM calls are awaited.
"""
from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field, replace
import json
import time
from typing import Any, Awaitable, Callable, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from acetone_plant.plant import EpisodeRunner, SupervisoryAction
from .plan_compiler import compile_plan, inspect_operation

from acetone_plant.fdd import PCAMonitor, rule_checks
from acetone_plant.plant.alarms import default_alarm_definitions

from .action import LLMActionAgent, explain_plan_errors, normalise_plan, slim_observation
from .llm import DEFAULT_MODEL
from .kg import KnowledgeGraph
from .monitoring import MonitoringAgent
from .planning import KGPlanner, LLMPlanner


# =============================================================================
# Configuration and presets
# =============================================================================

@dataclass(frozen=True)
class AgentConfig:
    name: str = "llm"
    proposer: Literal["none", "llm"] = "llm"
    planner: Literal["kg", "llm"] = "kg"
    use_pca: bool = True
    rules_trigger: bool = False      # False: only PCA triggers the framework (rules inform diagnosis)
    use_kg: bool = True
    twin: bool = False
    judge: bool = False               # without the twin: an LLM judge validates each proposal
    admit: bool = False               # ADMIT hard gates validate the agent's action records
    admit_sim: bool = True            # True: T0-T4 (T4 projected on the plant); False: T0-T3 only, no simulation
    diagnoser: Literal["heuristic", "llm"] = "heuristic"  # "llm": agents/diagnosis_llm.py explains the PCA/rule evidence
    llm_model: str = DEFAULT_MODEL
    llm_timeout_s: float = 120.0
    # monitoring
    persistence: int = 3
    cooldown_min: float = 5.0
    max_actions: int = 3
    # proposal / twin / validation (minutes)
    reprompt_max: int = 3
    twin_horizon_min: float = 30.0   # column faults need ~15+ min for pressure to clear its alarm
    # validate criterion: "alarms" = the variables that are faulty now return within their alarm limits
    # (default); "restore" = fully normal (no alarms, no saturated loops, PCA within limits); "safe" = SIF envelope only
    acceptance: Literal["alarms", "restore", "safe"] = "alarms"
    pca_band_sigma: float = 3.0       # soft alarm level for PCA-only faulty variables (mean ± kσ of normal data)
    quality_needs_pca: bool = True    # quality-only alarms (priority 3) trigger the agents only if PCA confirms
    blocking_priority: int = 2        # new alarms of priority ≤ this block acceptance; 3 = quality alarms only noted
    restore_hold_min: float = 2.0     # the criterion must hold this long to count as restored
    twin_confirm_min: float = 5.0     # keep simulating this long after recovery, then stop the rollout early
    fallback_to_safe: bool = True     # retries exhausted: apply the best candidate if it beats doing nothing
    mitigation_wait_min: float = 10.0 # after a mitigation (no full recovery predicted) re-decide after this long
    sif_reset: bool = True            # agents request a SIF reset while non-trip SIFs are latched (README:
                                      # operator/agent action); the SIF only resets latches back in their band
    grace_min: float = 1.0
    safe_hold_min: float = 1.0
    deadline_min: float = 10.0
    unsafe_fraction_max: float = 0.20
    # clock (used by the benchmark episode runner)
    clock: Literal["frozen", "async", "delay"] = "frozen"
    delay_min: float = 0.0
    speedup: float = 10.0   # async: simulated seconds per wall second (0 = flat out)


PRESETS: dict[str, dict] = {
    "passive":    dict(proposer="none"),                                   # monitor only
    "llm":        dict(proposer="llm", use_kg=True, twin=True),            # full framework
    "llm_plan":   dict(proposer="llm", planner="llm", use_kg=True, twin=True),
    "llm_nokg":   dict(proposer="llm", use_kg=False, twin=True),
    "llm_notwin": dict(proposer="llm", use_kg=True, twin=False),
    # "I'm feeling lucky": the first executable LLM proposal is applied, nothing validates it
    "llm_lucky":  dict(proposer="llm", use_kg=True, twin=False),
    # LLM as judge: no simulator; a second LLM accepts / rejects each proposal (rejections reprompt)
    "llm_judge":  dict(proposer="llm", use_kg=True, twin=False, judge=True, reprompt_max=3),
    # ADMIT: the agent files an action record per command; ADMIT's gates (evidence, hazard, authority /
    # SOP steps, consequence projected on the plant) decide admissibility (needs acetone-plant[admit])
    "llm_admit":  dict(proposer="llm", planner="llm", use_kg=True, twin=False, admit=True, reprompt_max=3),
    # ADMIT without the simulation layer: record, evidence, hazard, authority / mode / SOP steps (T0-T3)
    "llm_admit_nosim": dict(proposer="llm", planner="llm", use_kg=True, twin=False, admit=True, admit_sim=False,
                            reprompt_max=3),
}


def make_config(preset: str, **overrides) -> AgentConfig:
    if preset not in PRESETS:
        raise ValueError(f"unknown agent preset {preset!r}; choose from {sorted(PRESETS)}")
    return AgentConfig(name=preset, **{**PRESETS[preset], **overrides})


def make_action_agent(cfg: AgentConfig):
    if cfg.proposer == "llm":
        return LLMActionAgent(cfg.llm_model, cfg.llm_timeout_s)
    return None


def make_planner(cfg: AgentConfig):
    return LLMPlanner(cfg.llm_model, cfg.llm_timeout_s) if cfg.planner == "llm" else KGPlanner()


# =============================================================================
# Digital twin: simulate the candidate action and measure safety + restoration
# =============================================================================

FAILED_TERMINAL = {"SAFE_TRIP", "DAMAGE", "MODEL_LIMIT", "NUMERICAL_FAILURE"}


def clone_env(env: EpisodeRunner, present_only: bool = True) -> EpisodeRunner:
    """Deep copy of the plant for twin rollouts.

    ``present_only``: the agents know which faults are active now, not when they will clear or
    what will happen next. Active injected faults/disturbances are therefore made persistent in
    the copy and future injections are removed (otherwise the twin would 'know' the schedule).
    """
    records, env.records = env.records, []
    try:
        twin = copy.deepcopy(env)
    finally:
        env.records = records
    if present_only:
        t = twin.t_h

        def present(items):
            return [replace(f, end_h=None) if f.active(t) else f for f in items if f.start_h <= t + 1e-12]
        twin.runtime_inputs.events = present(twin.runtime_inputs.events)
        for eng in (twin.spec.actuator_faults, twin.spec.sensor_faults, twin.spec.pump_faults):
            eng.faults = present(eng.faults)
    return twin


def consequence_key(m: dict) -> tuple:
    """Ranking of twin outcomes, lower is better: no trip (or a later one), more faulty targets back
    within limits, less time unsafe, closer to normal (PCA). Used to compare candidates against the
    counterfactual 'do nothing' rollout when nothing restores the plant (e.g. several faults at once)."""
    if m.get("rollout") != "ok":
        return (2, 0.0, 99, 1.0, 1e9)
    f = m.get("final") or {}
    failed = m.get("terminal_status") in FAILED_TERMINAL
    not_met = sum(1 for s in f.get("targets", []) if not s["ok"])
    pca = (f.get("t2_ratio") or 0.0) + (f.get("spe_ratio") or 0.0)
    return (int(failed), -(m.get("terminal_min") or 0.0) if failed else 0.0, not_met,
            round(float(m.get("unsafe_fraction", 1.0)), 2), round(pca, 3))


ALARM_DEFS = {d.alarm_id: d for d in default_alarm_definitions()}


def faulty_targets(detection: dict, diagnosis: dict, pca: PCAMonitor | None, sigma: float = 3.0,
                   observation: dict | None = None) -> list[dict]:
    """The metrics that are faulty right now, each with the limit it must return within.

    Active alarms → their alarm limit; failed duty pumps → running again; a fault
    confirmed by PCA → Hotelling T² and SPE back under their control limits (the PCA
    statistic is itself the faulty metric, so "recovered" means "PCA says normal").
    """
    rules = detection["rules"]
    targets = []
    for alarm in rules.get("active_alarms", []):
        d = ALARM_DEFS.get(alarm)
        if d is None or d.tag.startswith("pump_"):
            continue
        targets.append({"name": alarm, "kind": "alarm", "tag": d.tag, "direction": d.direction,
                        "limit": d.limit, "unit": d.unit, "label": d.description})
    for train in rules.get("failed_trains", []):
        targets.append({"name": f"{train} pump", "kind": "pump", "train": train, "label": f"{train} duty pump failed"})
    if pca is not None and detection.get("pca_persistent"):
        targets.append({"name": "PCA T²", "kind": "pca_stat", "stat": "t2", "direction": "HIGH",
                        "limit": float(pca.t2_limit), "unit": "", "label": "Hotelling T² above its limit"})
        targets.append({"name": "PCA SPE", "kind": "pca_stat", "stat": "spe", "direction": "HIGH",
                        "limit": float(pca.spe_limit), "unit": "", "label": "SPE (Q) above its limit"})
    return targets


def _target_status(t: dict, obs: dict, score=None) -> dict:
    if t["kind"] == "pump":
        failed = bool(obs["pump_trains"][t["train"]]["selected_failed"])
        return {"name": t["name"], "ok": not failed, "value": "failed" if failed else "running", "limit": "running"}
    if t["kind"] == "pca_stat":
        v = float("nan") if score is None else float(getattr(score, t["stat"]))
    else:
        v = float(obs["measurements"].get(t["tag"], float("nan")))
    ok = v < t["limit"] if t["direction"].upper() == "HIGH" else v > t["limit"]
    limit = f"{'<' if t['direction'].upper() == 'HIGH' else '>'} {t['limit']:.4g} {t['unit']}".strip()
    return {"name": t["name"], "ok": bool(ok), "value": round(v, 4), "limit": limit}


def twin_rollout(twin: EpisodeRunner, action: SupervisoryAction, cfg: AgentConfig,
                 pca: PCAMonitor | None = None, targets: list[dict] | None = None) -> dict:
    """Apply ``action`` to a cloned plant, simulate ``twin_horizon_min`` and measure the outcome.

    Measures only; the validate agent turns these numbers into a verdict.
      safe          true T/P/levels inside the SIF envelope, no SIF latch, no failed pump
      alarms clear  safe AND every faulty variable (``targets``) back within its alarm
                    limit AND no new safety-relevant alarm (priority ≤ cfg.blocking_priority)
      normal        alarms clear AND no saturated loops AND PCA T²/SPE within limits
    ``restored`` follows ``cfg.acceptance``: "alarms" → alarms clear, otherwise normal.
    """
    targets = targets or []
    start = 60 * twin.t_h
    end = min(start + cfg.twin_horizon_min, 60 * twin.spec.scenario.duration_h)
    result = twin.apply_action(action)
    if result.rejected:
        return {"rollout": "action_rejected", "detail": list(result.rejected), "horizon_min": 0.0,
                "terminal_status": "RUNNING", "safe_reached": False, "restored": False}
    unsafe = judged = safe_hold = normal_hold = 0.0
    time_to_safe = time_to_restore = None
    pred, last = [], {}
    while not twin.done and 60 * twin.t_h < end - 1e-9:
        before = 60 * twin.t_h
        sr = twin.last_safety_result
        reset = bool(cfg.sif_reset and sr.latched_causes and not sr.plant_trip_latched)
        obs, _ = twin.step(SupervisoryAction(reset_sifs=True, note="twin_sif_reset") if reset
                           else SupervisoryAction.no_action())
        check = inspect_operation(twin, obs, truth=True)
        rules = rule_checks(obs)
        score = pca.score_observation(obs) if pca is not None else None
        status = [_target_status(t, obs, score) for t in targets]
        target_names = {t["name"] for t in targets}
        new_alarms = [a for a in rules["active_alarms"] if a not in target_names]
        blocking = [a for a in new_alarms
                    if a in ALARM_DEFS and ALARM_DEFS[a].priority <= cfg.blocking_priority]
        alarms_clear = (check["safe_now"] and all(s["ok"] for s in status)
                        and not blocking and not rules["failed_trains"])
        normal = (check["safe_now"] and not rules["abnormal"]
                  and (score is None or not score.violation))
        met = alarms_clear if cfg.acceptance == "alarms" else normal
        elapsed, dt = obs["time_min"] - start, obs["time_min"] - before
        judged_dt = max(0.0, min(dt, elapsed - cfg.grace_min))
        if judged_dt:
            judged += judged_dt
            if check["safe_now"]:
                safe_hold += judged_dt
                if time_to_safe is None and safe_hold + 1e-9 >= cfg.safe_hold_min:
                    time_to_safe = elapsed
            else:
                unsafe += judged_dt
                safe_hold = 0.0
        normal_hold = normal_hold + dt if met else 0.0
        if time_to_restore is None and met and normal_hold + 1e-9 >= cfg.restore_hold_min:
            time_to_restore = elapsed
        if time_to_restore is not None and elapsed >= time_to_restore + cfg.twin_confirm_min and met:
            pred.append([round(obs["time_min"], 2), None if score is None else round(score.t2, 3),
                         None if score is None else round(score.spe, 3), int(check["safe_now"]), int(met)])
            last = {"unsafe_tags": check["unsafe_tags"], "active_alarms": rules["active_alarms"],
                    "saturated_loops": rules["saturated_loops"], "failed_trains": rules["failed_trains"],
                    "sif_latched": rules["sif_latched"],
                    "t2_ratio": None if score is None else round(score.t2 / score.t2_limit, 3),
                    "spe_ratio": None if score is None else round(score.spe / score.spe_limit, 3),
                    "normal": normal, "alarms_clear": alarms_clear, "met": met, "safe": check["safe_now"],
                    "targets": status, "new_alarms_blocking": blocking,
                    "new_alarms_noted": [a for a in new_alarms if a not in blocking]}
            break  # recovered and confirmed: the rest of the horizon would not change the verdict
        pred.append([round(obs["time_min"], 2),
                     None if score is None else round(score.t2, 3), None if score is None else round(score.spe, 3),
                     int(check["safe_now"]), int(met)])
        last = {"unsafe_tags": check["unsafe_tags"], "active_alarms": rules["active_alarms"],
                "saturated_loops": rules["saturated_loops"], "failed_trains": rules["failed_trains"],
                "sif_latched": rules["sif_latched"],
                "t2_ratio": None if score is None else round(score.t2 / score.t2_limit, 3),
                "spe_ratio": None if score is None else round(score.spe / score.spe_limit, 3),
                "normal": normal, "alarms_clear": alarms_clear, "met": met, "safe": check["safe_now"],
                "targets": status, "new_alarms_blocking": blocking,
                "new_alarms_noted": [a for a in new_alarms if a not in blocking]}
    return {"rollout": "ok", "horizon_min": end - start, "terminal_status": twin.outcomes.state.status.value,
            "terminal_min": round(60 * twin.t_h - start, 2) if twin.done else None,
            "safe_reached": time_to_safe is not None, "time_to_safe_min": time_to_safe,
            "restored": time_to_restore is not None and bool(last.get("met")), "criterion": cfg.acceptance,
            "time_to_restore_min": time_to_restore,
            "unsafe_fraction": unsafe / judged if judged else 1.0, "final": last,
            "pred": pred[::2]}  # predicted trajectory (every 1 min): t, T², SPE, safe, normal


def validate_outcome(m: dict, cfg: AgentConfig) -> dict:
    """Validate agent: judge the twin's measurements against explicit checks."""
    if m.get("rollout") == "action_rejected":
        checks = [{"check": "plant accepts the action", "ok": False, "detail": "; ".join(m.get("detail", []))}]
    else:
        f = m.get("final") or {}
        tts, ttr = m.get("time_to_safe_min"), m.get("time_to_restore_min")
        checks = [
            {"check": "no trip / damage / model failure", "ok": m["terminal_status"] not in FAILED_TERMINAL,
             "detail": m["terminal_status"]},
            {"check": f"safe within {cfg.deadline_min:g} min", "ok": tts is not None and tts <= cfg.deadline_min,
             "detail": "never safe" if tts is None else f"safe after {tts:.1f} min"},
            {"check": f"unsafe ≤ {100 * cfg.unsafe_fraction_max:.0f}% of the time",
             "ok": m["unsafe_fraction"] <= cfg.unsafe_fraction_max, "detail": f"{100 * m['unsafe_fraction']:.0f}%"},
        ]
        if cfg.acceptance == "alarms":
            bad = [f"{s['name']} = {s['value']} (needs {s['limit']})" for s in f.get("targets", []) if not s["ok"]]
            blocking, noted = f.get("new_alarms_blocking", []), f.get("new_alarms_noted", [])
            names = ", ".join(s["name"] for s in f.get("targets", [])) or "no alarmed variables"
            note = f"; note: quality alarms {noted} active (not blocking)" if noted else ""
            detail = ((f"back within limits after {ttr:.1f} min ({names})" if m.get("restored")
                       else "; ".join(bad + ([f"new safety alarms: {blocking}"] if blocking else [])
                                      + (["unsafe at the end"] if not f.get("safe", True) else []))
                       or "limits not held long enough") + note)
            checks.append({"check": f"faulty variables back within alarm limits within {m['horizon_min']:.0f} min",
                           "ok": bool(m.get("restored")), "detail": detail})
        elif cfg.acceptance == "restore":
            remaining = [f"alarms {f['active_alarms']}" if f.get("active_alarms") else "",
                         f"saturated {f['saturated_loops']}" if f.get("saturated_loops") else "",
                         f"failed pumps {f['failed_trains']}" if f.get("failed_trains") else "",
                         f"SIF {f['sif_latched']}" if f.get("sif_latched") else "",
                         f"T²={f['t2_ratio']}×limit" if (f.get("t2_ratio") or 0) > 1 else "",
                         f"SPE={f['spe_ratio']}×limit" if (f.get("spe_ratio") or 0) > 1 else ""]
            remaining = ", ".join(x for x in remaining if x)
            checks.append({"check": f"plant restored to normal within {m['horizon_min']:.0f} min",
                           "ok": bool(m.get("restored")),
                           "detail": f"restored after {ttr:.1f} min" if m.get("restored")
                           else f"not restored: {remaining or 'normal state not held'}"})
    failed = [c for c in checks if not c["ok"]]
    safe_ok = all(c["ok"] for c in checks
                  if not c["check"].startswith(("plant restored", "faulty variables")))
    # A fallback mitigation must not create new safety alarms (e.g. a level setpoint next to its alarm).
    safe_ok = safe_ok and not (m.get("final") or {}).get("new_alarms_blocking")
    return {"accepted": not failed, "fail_reason": failed[0]["check"] if failed else "",
            "criterion": cfg.acceptance, "checks": checks, "safe_ok": safe_ok}


# =============================================================================
# Graph
# =============================================================================

def split_records(raw_plan: Any) -> tuple[Any, list[dict]]:
    """Take the ADMIT action records (``"record"`` per command) out of an LLM plan."""
    if not isinstance(raw_plan, dict) or not isinstance(raw_plan.get("commands"), list):
        return raw_plan, []
    cmds, recs = [], []
    for c in raw_plan["commands"]:
        if isinstance(c, dict):
            c = dict(c)
            recs.append((c.get("type"), c.get("target"), c.pop("record", None)))
        cmds.append(c)
    return {**raw_plan, "commands": cmds}, recs


def align_records(plan: dict, recs: list) -> list[dict | None]:
    """One record per normalised command (normalisation may drop NO_ACTION / rename targets)."""
    out, used = [], set()
    for c in plan.get("commands", []):
        idx = next((i for i, (t, tg, _) in enumerate(recs) if i not in used and str(t).upper() == c.get("type")
                    and (tg is None or c.get("target") is None or str(tg).upper().endswith(str(c.get("target"))))), None)
        if idx is None:
            idx = next((i for i in range(len(recs)) if i not in used), None)
        if idx is None:
            out.append(None)
        else:
            used.add(idx)
            out.append(recs[idx][2] if isinstance(recs[idx][2], dict) else None)
    return out


class RecoveryState(TypedDict, total=False):
    observation: dict
    catalog: dict
    detection: dict
    diagnosis: dict
    knowledge: list[dict]
    brief: dict
    plan: dict
    kg_citations: list[str]
    records: list            # ADMIT action records filed with the plan (llm_admit)
    action: SupervisoryAction
    errors: list[str]
    twin: dict
    evaluation: dict
    feedback: str
    attempts: int
    safe_candidates: list   # every rolled-out candidate with its consequence key
    baseline: dict          # counterfactual NO_ACTION rollout of this decision
    tried: list
    outcome: str


@dataclass
class RunContext:
    """What the graph needs from whoever owns the plant (benchmark episode or live server)."""
    cfg: AgentConfig
    monitor: MonitoringAgent
    kg: KnowledgeGraph | None
    planner: Any
    action_agent: Any
    snapshot_env: Callable[[], Awaitable[EpisodeRunner]]
    dispatch: Callable[[SupervisoryAction, dict], Awaitable[dict]]
    emit: Callable[[dict], Any]
    actions_taken: int = 0
    llm_usage: list[dict] = field(default_factory=list)
    # Optional: called when a decision starts (fault confirmed) and when it ends.
    # The live server uses them to hold the plant while the agents decide.
    on_decision_start: Callable[[], Awaitable[None]] | None = None
    on_decision_end: Callable[[], Awaitable[None]] | None = None
    cycle: int = 0


def build_recovery_graph(ctx: RunContext):
    cfg = ctx.cfg
    diagnoser = None
    if cfg.diagnoser == "llm":
        from .diagnosis_llm import LLMDiagnoser
        diagnoser = LLMDiagnoser(cfg.llm_model, cfg.llm_timeout_s, kg=ctx.kg)
    judge = None
    if cfg.judge and not cfg.twin:
        from .judge import LLMJudge
        judge = LLMJudge(cfg.llm_model, cfg.llm_timeout_s)
    gate = None
    if cfg.admit:
        from acetone_plant.admit.gate import AdmitGate
        gate = AdmitGate(simulate=cfg.admit_sim)

    async def emit(event: dict) -> None:
        if event["event"] != "monitor":
            event = {**event, "cycle": ctx.cycle}
        out = ctx.emit(event)
        if asyncio.iscoroutine(out):
            await out

    async def node(name: str, state: RecoveryState, **extra) -> None:
        """Pipeline progress for the console: which node of the decision is running."""
        await emit({"event": "node", "t": state["detection"]["time_min"], "node": name, **extra})

    async def end_decision() -> None:
        if ctx.on_decision_end:
            await ctx.on_decision_end()

    async def monitoring(state: RecoveryState) -> dict:
        out = await ctx.monitor(state)
        det, diag = out["detection"], out["diagnosis"]
        if ctx.action_agent is None or ctx.actions_taken >= cfg.max_actions:
            det = {**det, "should_act": False}
        if det["should_act"] and ctx.on_decision_start:
            # Hold the plant from the moment the agents engage (before the LLM diagnosis call), so the
            # action is decided and applied on the state it was decided for.
            await ctx.on_decision_start()
        if det["should_act"] and diagnoser is not None:
            from .diagnosis_llm import as_diagnosis
            verdict = await diagnoser.diagnose(det, diag, state["observation"])
            diag = as_diagnosis(verdict, diag)
            await emit({"event": "llm_diagnosis", "t": det["time_min"], **verdict})
        await emit({"event": "monitor", "t": det["time_min"], "detection": det, "diagnosis": diag})
        return {"detection": det, "diagnosis": diag, "attempts": 0, "feedback": "", "outcome": "no_action",
                "safe_candidates": [], "tried": []}

    async def kg_retrieval(state: RecoveryState) -> dict:
        ctx.cycle += 1  # a new decision cycle starts here (fault confirmed, agents engaged; plant already held)
        await node("kg_retrieval", state, diagnosis=state["diagnosis"]["summary"],
                   reprompt_max=cfg.reprompt_max, twin=cfg.twin, acceptance=cfg.acceptance)
        if ctx.kg is None or not cfg.use_kg:
            return {"knowledge": []}
        det = state["detection"]
        # PCA contributors only seed the walk when the PCA violation is persistent.
        ev = await asyncio.to_thread(ctx.kg.retrieve, state["diagnosis"], det["rules"], det["pca_persistent"])
        await emit({"event": "kg", "t": det["time_min"], "nodes": [n["id"] for n in ev]})
        return {"knowledge": ev}

    async def planning(state: RecoveryState) -> dict:
        await node("planning", state)
        context = {**state, "snapshot": slim_observation(state["observation"])}
        t0 = time.perf_counter()
        try:
            brief = await ctx.planner.plan(context)
            if getattr(ctx.planner, "last_usage", None):  # LLM planner: count its tokens/cost too
                ctx.llm_usage.append({**ctx.planner.last_usage, "agent": "planning",
                                      "latency_s": time.perf_counter() - t0})
        except Exception as exc:  # LLM planner failure → fall back to the KG planner
            brief = {**await KGPlanner().plan(context), "planner_error": f"{type(exc).__name__}: {exc}"}
        await emit({"event": "plan", "t": state["detection"]["time_min"], "brief": brief})
        return {"brief": brief}

    async def action_propose(state: RecoveryState) -> dict:
        await node("action_propose", state, attempt=state["attempts"])
        context = {**state, "previous_plan": state.get("plan") if state["attempts"] else None}
        if gate is not None:
            context["admit"] = gate.brief(state["observation"], state["detection"], state["diagnosis"],
                                          state.get("knowledge"))
        t0 = time.perf_counter()
        try:
            raw_plan = await ctx.action_agent.propose(context)
            usage = dict(getattr(ctx.action_agent, "last_usage", {}) or {})
        except Exception as exc:  # provider error / unparsable output
            usage = {"error": f"{type(exc).__name__}: {exc}"}
            # No rule fallback: a failed LLM call is an empty (non-executable) proposal, which the
            # validator rejects; the next attempt asks the LLM again.
            raw_plan = {"commands": [], "reasoning": f"[LLM call failed: {usage['error'][:200]}]"}
            await emit({"event": "llm_error", "t": state["detection"]["time_min"], "error": usage["error"][:400]})
        usage.setdefault("latency_s", time.perf_counter() - t0)
        ctx.llm_usage.append(usage)
        raw_plan, records = split_records(raw_plan)
        plan, cites = normalise_plan(raw_plan)
        records = align_records(plan, records)
        # Turn the plan into a plant command (catalog names, bounds). Not a pipeline step:
        # a plan that cannot be executed is rejected by the validate agent and reprompted.
        errors = explain_plan_errors(plan, state["catalog"])
        action, compile_errors = compile_plan(plan, state["observation"], state["catalog"])
        errors = errors or compile_errors
        await emit({"event": "proposal", "t": state["detection"]["time_min"], "attempt": state["attempts"],
                    "plan": plan, "kg_citations": cites, "latency_s": usage["latency_s"],
                    "tokens": usage.get("total_tokens"), "errors": errors or None})
        if errors:
            await emit({"event": "invalid", "t": state["detection"]["time_min"], "errors": errors})
        return {"plan": plan, "kg_citations": cites, "attempts": state["attempts"] + 1,
                "action": action, "errors": errors, "records": records}

    async def twin_evaluate(state: RecoveryState) -> dict:
        if state["plan"]["commands"] in (state.get("tried") or []):
            reason = "identical to a proposal already rejected in this decision"
            await emit({"event": "twin_skipped", "t": state["detection"]["time_min"], "reason": reason})
            return {"twin": {"rollout": "duplicate", "reason": reason}}
        if state.get("errors"):
            reason = "plan not executable: " + "; ".join(state["errors"])
            await emit({"event": "twin_skipped", "t": state["detection"]["time_min"], "reason": reason})
            return {"twin": {"rollout": "invalid_plan", "reason": reason}}
        if judge is not None:
            await node("judge", state)
            context = {k: state.get(k) for k in ("observation", "detection", "diagnosis", "brief", "knowledge")}
            verdict = await judge.judge(context, state["plan"])
            await emit({"event": "judge", "t": state["detection"]["time_min"], "plan": state["plan"], **verdict})
            return {"twin": {"rollout": "judged", **verdict}}
        if gate is not None:
            await node("twin_evaluate", state, validator="admit" if gate.simulate else "admit_nosim",
                       horizon_min=gate.horizon_s / 60 if gate.simulate else 0)
            twin = await ctx.snapshot_env()
            w0 = time.perf_counter()
            verdict = await asyncio.to_thread(gate.evaluate, twin, state["action"], state["plan"]["commands"],
                                              state.get("records") or [], state["observation"], state["detection"],
                                              state["diagnosis"], state.get("knowledge"))
            verdict["wall_s"] = round(time.perf_counter() - w0, 3)
            await emit({"event": "admit", "t": state["detection"]["time_min"], "plan": state["plan"],
                        "attempt": state["attempts"] - 1,
                        **{k: verdict[k] for k in ("admissible", "mode", "hazard_id", "records", "wall_s", "simulate",
                                                   "context", "ledger", "ledger_head", "remedies")}})
            return {"twin": {"rollout": "admit", **verdict}}
        if not cfg.twin:
            reason = "twin disabled in this preset"
            await emit({"event": "twin_skipped", "t": state["detection"]["time_min"], "reason": reason})
            return {"twin": {"rollout": "skipped", "reason": reason}}
        # NO_ACTION is rolled out as well: doing nothing is only acceptable if the twin shows the
        # plant recovering by itself (e.g. a transient); otherwise it is the counterfactual baseline.
        await node("twin_evaluate", state, horizon_min=cfg.twin_horizon_min)
        twin = await ctx.snapshot_env()
        targets = faulty_targets(state["detection"], state["diagnosis"], ctx.monitor.pca, cfg.pca_band_sigma,
                                 state["observation"])
        w0 = time.perf_counter()
        m = await asyncio.to_thread(twin_rollout, twin, state["action"], cfg, ctx.monitor.pca, targets)
        m["wall_s"] = round(time.perf_counter() - w0, 3)
        await emit({"event": "twin", "t": state["detection"]["time_min"], "plan": state["plan"], **m})
        return {"twin": m}

    async def validate(state: RecoveryState) -> dict:
        """Validate agent: does the twin show the plant restored? If not, feedback → reprompter."""
        await node("validate", state)
        m = state["twin"]
        if m.get("rollout") == "duplicate":
            v = {"accepted": False, "fail_reason": "new proposal", "criterion": cfg.acceptance, "safe_ok": False,
                 "checks": [{"check": "new proposal", "ok": False,
                             "detail": "same commands as a rejected attempt; propose something different"}]}
        elif m.get("rollout") == "invalid_plan":
            v = {"accepted": False, "fail_reason": "plan executable", "criterion": cfg.acceptance, "safe_ok": False,
                 "checks": [{"check": "plan executable", "ok": False, "detail": "; ".join(state["errors"])}]}
        elif m.get("rollout") == "judged":
            detail = m.get("reason", "") + (f" Suggestion: {m['suggestion']}" if m.get("suggestion") and not m.get("accept") else "")
            v = {"accepted": bool(m.get("accept")), "fail_reason": "" if m.get("accept") else "LLM judge rejected",
                 "criterion": "llm_judge", "safe_ok": bool(m.get("accept")),
                 "checks": [{"check": "LLM judge", "ok": bool(m.get("accept")), "detail": detail[:900]}]}
        elif m.get("rollout") == "admit":
            checks = [{"check": f"ADMIT {r['action']}", "ok": r["admissible"],
                       "detail": "; ".join(f"{v['code']}: {v['message']}" for v in r["violations"])[:900] or "all hard gates pass"}
                      for r in m["records"]]
            first = next((r["aas_code"] for r in m["records"] if not r["admissible"]), "")
            v = {"accepted": bool(m["admissible"]), "fail_reason": "" if m["admissible"] else f"ADMIT gate: {first}",
                 "criterion": "admit", "safe_ok": bool(m["admissible"]), "checks": checks}
        elif m.get("rollout") == "skipped":
            v = {"accepted": True, "fail_reason": "", "criterion": "none", "safe_ok": True,
                 "checks": [{"check": "digital twin", "ok": True, "detail": m["reason"]}]}
        else:
            v = validate_outcome(m, cfg)
        cands = list(state.get("safe_candidates") or [])
        out = {}
        if not v["accepted"] and m.get("rollout") == "ok":
            if state["action"] == SupervisoryAction.no_action():
                out["baseline"] = {"key": consequence_key(m), "terminal_status": m.get("terminal_status")}
            else:
                cands.append({"plan": state["plan"], "action": state["action"], "key": consequence_key(m),
                              "safe_ok": v["safe_ok"]})
        await emit({"event": "validation", "t": state["detection"]["time_min"], "plan": state["plan"],
                    "attempt": state["attempts"], **v})
        tried = list(state.get("tried") or [])
        if not v["accepted"]:
            tried.append(state["plan"]["commands"])
        return {"evaluation": v, "safe_candidates": cands, "tried": tried, **out}

    async def reprompt(state: RecoveryState) -> dict:
        ev, m = state["evaluation"], state.get("twin") or {}
        fb = {"fail_reason": ev.get("fail_reason"), "errors": state.get("errors") or None,
              "failed_checks": [c for c in ev.get("checks", []) if not c["ok"]],
              **{k: m.get(k) for k in ("time_to_safe_min", "time_to_restore_min", "unsafe_fraction",
                                       "terminal_status") if k in m},
              "twin_final_state": m.get("final")}
        if m.get("rollout") == "admit":
            fb["admit_violations"] = [{"command": r["command"], "action": r["action"], "violations": r["violations"]}
                                      for r in m["records"] if not r["admissible"]]
            fb["admit_remedies"] = m.get("remedies")
        await emit({"event": "reprompt", "t": state["detection"]["time_min"], "attempt": state["attempts"],
                    "reprompt_max": cfg.reprompt_max, "feedback": fb, "rejected_plan": state["plan"]})
        return {"feedback": json.dumps(fb, default=str)}

    async def dispatch(state: RecoveryState) -> dict:
        await node("dispatch", state)
        t_obs = state["detection"]["time_min"]
        ttr = (state.get("twin") or {}).get("time_to_restore_min")
        mitigation = state.get("outcome") == "mitigation"
        accepted = not mitigation and (state.get("evaluation") or {}).get("accepted")
        if accepted and ttr is not None:
            wait = ttr + cfg.restore_hold_min
            reason = f"twin predicts recovery in {ttr:.1f} min; no new decision until t={t_obs + wait:.1f}"
        elif mitigation:
            # No candidate recovers the plant; the twin's prediction for this action covers its
            # whole look-ahead, so re-deciding earlier would contradict it.
            wait = cfg.mitigation_wait_min
            reason = (f"mitigation (no candidate fully recovers the plant): re-evaluate after "
                      f"{wait:.0f} min, at t={t_obs + wait:.1f}")
        else:
            wait, reason = None, ""
        ctx.monitor.mark_acted(t_obs, state["diagnosis"]["summary"], wait)
        if wait:
            await emit({"event": "verification", "t": t_obs, "until": t_obs + wait, "reason": reason})
        try:
            if state["action"] == SupervisoryAction.no_action():
                await emit({"event": "advisory", "t": state["detection"]["time_min"], "plan": state["plan"],
                            "brief": state.get("brief")})
                return {"outcome": "advisory"}
            ctx.actions_taken += 1
            result = await ctx.dispatch(state["action"], {"t_obs": state["detection"]["time_min"],
                                                          "plan": state["plan"], "brief": state.get("brief"),
                                                          "diagnosis": state["diagnosis"],
                                                          "kind": "mitigation" if state.get("outcome") == "mitigation"
                                                          else "restoring"})
            return {"outcome": result.get("status", "applied")}
        finally:
            await end_decision()

    async def reject(state: RecoveryState) -> dict:
        await node("reject", state)
        cands = state.get("safe_candidates") or []
        if cfg.fallback_to_safe and cands:
            base = state.get("baseline")
            if base is None and cfg.twin:  # counterfactual: what happens if nobody acts?
                twin = await ctx.snapshot_env()
                targets = faulty_targets(state["detection"], state["diagnosis"], ctx.monitor.pca,
                                         cfg.pca_band_sigma, state["observation"])
                w0 = time.perf_counter()
                m = await asyncio.to_thread(twin_rollout, twin, SupervisoryAction.no_action(), cfg,
                                            ctx.monitor.pca, targets)
                m["wall_s"] = round(time.perf_counter() - w0, 3)
                await emit({"event": "twin", "t": state["detection"]["time_min"], "counterfactual": True,
                            "plan": {"commands": [{"type": "NO_ACTION"}], "reasoning": "counterfactual: do nothing"},
                            **m})
                base = {"key": consequence_key(m), "terminal_status": m.get("terminal_status")}
            better = [c for c in cands if base is None or c["key"] < base["key"]]
            if better:
                best = min(better, key=lambda c: c["key"])
                await emit({"event": "fallback", "t": state["detection"]["time_min"], "plan": best["plan"],
                            "reason": "no candidate restores the plant; applying the candidate that does best "
                                      "against doing nothing (no/later trip, more faulty variables back, less "
                                      "time unsafe)", "n_safe": len(better), "key": best["key"],
                            "baseline_key": base["key"] if base else None})
                return {"plan": best["plan"], "action": best["action"], "outcome": "mitigation"}
        ctx.monitor.mark_acted(state["detection"]["time_min"], state["diagnosis"]["summary"])
        await emit({"event": "reject", "t": state["detection"]["time_min"],
                    "reason": state["evaluation"].get("fail_reason")})
        await end_decision()
        return {"outcome": "rejected"}

    def retry_or_reject(state: RecoveryState) -> str:
        return "reprompt" if state["attempts"] <= cfg.reprompt_max else "reject"

    g = StateGraph(RecoveryState)
    for name, fn in [("monitoring", monitoring), ("kg_retrieval", kg_retrieval), ("planning", planning),
                     ("action_propose", action_propose),
                     ("twin_evaluate", twin_evaluate), ("validate", validate), ("reprompt", reprompt),
                     ("dispatch", dispatch), ("reject", reject)]:
        g.add_node(name, fn)
    g.add_edge(START, "monitoring")
    g.add_conditional_edges("monitoring", lambda s: "act" if s["detection"]["should_act"] else "end",
                            {"act": "kg_retrieval", "end": END})
    g.add_edge("kg_retrieval", "planning")
    g.add_edge("planning", "action_propose")
    g.add_edge("action_propose", "twin_evaluate")
    g.add_edge("twin_evaluate", "validate")
    g.add_conditional_edges("validate", lambda s: "dispatch" if s["evaluation"]["accepted"] else retry_or_reject(s),
                            {"dispatch": "dispatch", "reprompt": "reprompt", "reject": "reject"})
    g.add_edge("reprompt", "action_propose")
    g.add_conditional_edges("reject", lambda s: "dispatch" if s.get("outcome") == "mitigation" else "end",
                            {"dispatch": "dispatch", "end": END})
    g.add_edge("dispatch", END)
    return g.compile()
