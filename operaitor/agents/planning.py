"""Planning agent: turns the monitoring agent's hypotheses + KG evidence into a brief.

The brief is what the action agent works from:

    {"target_fault": "condenser_cooling_loss", "fault_label": "...",
     "symptoms": [...], "objective": "...",
     "candidate_levers": [{"type": "SET_SETPOINT", "target": "COL_PIC", "why": "..."}, ...],
     "constraints": ["COL_PIC output saturated: do not push this loop", ...],
     "kg_nodes": [...]}

``KGPlanner`` is deterministic: it reads levers off the KG (OperatorAction
--actsOn--> pump train / controller / actuatable parameter). ``LLMPlanner``
lets a chat model write the brief from the same inputs.
"""
from __future__ import annotations

import json
from typing import Protocol

from .llm import DEFAULT_MODEL, USAGE, ainvoke, make_llm, message_text, parse_json_object

_LEVER_FOR_ACTION = {
    "action_START_SPARE": "START_SPARE",
    "action_SET_SETPOINT": "SET_SETPOINT",
    "action_SET_THROUGHPUT": "SET_THROUGHPUT",
    "action_OPERATE_AUXILIARY": "OPERATE_AUXILIARY",
}
_AUX_FOR_PARAM = {"act_param_cstr_bdv_valve": "H2_BDV",
                  "act_param_cstr_emergency_cooling_factor": "EMERGENCY_COOLING"}


class Planner(Protocol):
    async def plan(self, context: dict) -> dict: ...


def _ids(evidence: list[dict]) -> set[str]:
    return {n["id"] for n in evidence}


class KGPlanner:
    """Deterministic planner grounded in the retrieved KG neighbourhood."""

    async def plan(self, context: dict) -> dict:
        diag, det = context["diagnosis"], context["detection"]
        evidence = context.get("knowledge") or []
        by_id = {n["id"]: n for n in evidence}
        hyps = diag.get("hypotheses", [])
        target = hyps[0]["fault"] if hyps else "unknown"
        fault_node = by_id.get(f"fault_{target}")
        symptoms = [by_id[s]["label"] for s in (fault_node or {}).get("relations", {}).get("causesSymptom", [])
                    if s in by_id]

        relevant = _ids(evidence)
        levers: list[dict] = []
        for node in evidence:
            kind = _LEVER_FOR_ACTION.get(node["id"])
            if not kind:
                continue
            for tgt in node.get("relations", {}).get("actsOn", []):
                if kind == "SET_THROUGHPUT":
                    levers.append({"type": kind, "why": f"{node['id']} actsOn {tgt}"})
                elif tgt in relevant and kind == "START_SPARE" and tgt.startswith("pump_train_"):
                    levers.append({"type": kind, "target": tgt.removeprefix("pump_train_"),
                                   "why": f"{node['id']} actsOn {tgt}"})
                elif tgt in relevant and kind == "SET_SETPOINT" and tgt.startswith("controller_"):
                    levers.append({"type": kind, "target": tgt.removeprefix("controller_"),
                                   "why": f"{node['id']} actsOn {tgt}"})
                elif kind == "OPERATE_AUXILIARY" and tgt in _AUX_FOR_PARAM and target.startswith(("h2_", "reactor_pressure")):
                    levers.append({"type": kind, "target": _AUX_FOR_PARAM[tgt], "why": f"{node['id']} actsOn {tgt}"})
        if not any(l["type"] == "SET_THROUGHPUT" for l in levers):
            levers.append({"type": "SET_THROUGHPUT", "why": "plant load is always an operator lever"})

        rules = det["rules"]
        constraints = [f"{loop} output saturated: do not push this loop; relieve its load instead"
                       for loop in rules.get("saturated_loops", [])]
        constraints += [f"SIF {c} latched: its override holds until reset conditions are met"
                        for c in rules.get("sif_latched", [])]
        if target.startswith("sensor_fault:") or "sensor" in target:
            constraints.append("Suspected sensor fault: the reading may be wrong; avoid acting on that tag alone")

        label = (fault_node or {}).get("label") or target
        objective = (f"Mitigate '{label}'" + (f" (symptoms: {', '.join(symptoms)})" if symptoms else "")
                     + "; keep temperatures, pressures and levels inside SIF limits and restore on-spec production.")
        return {"target_fault": target, "fault_label": label, "symptoms": symptoms,
                "alternatives": [h["fault"] for h in hyps[1:3]], "objective": objective,
                "candidate_levers": levers, "constraints": constraints,
                "kg_nodes": [n["id"] for n in evidence]}


PLANNER_PROMPT = """
You are the **Planning Agent** for an integrated isopropanol→acetone plant (endothermic
CSTR R-101 with H2 vent, 10-tray column T-201, five duty/standby pump trains).
The monitoring agent sends ranked fault hypotheses (PCA T²/SPE contributions and
alarm/pump/controller rules). You also get Knowledge Graph evidence and the live snapshot.

Decide WHAT the action agent should achieve, not the exact numbers. Return JSON only:
{"target_fault": "<hypothesis you plan against>", "fault_label": "<short name>",
 "symptoms": ["..."], "objective": "<one sentence>",
 "candidate_levers": [{"type": "START_SPARE|SET_SETPOINT|SET_THROUGHPUT|OPERATE_AUXILIARY",
                       "target": "<train/loop/device if needed>", "why": "<KG node ids>"}],
 "constraints": ["..."], "kg_nodes": ["node ids you used"]}
Only list levers the KG connects to the hypothesis. If the evidence points to a sensor
fault, say so in constraints.
""".strip()


class LLMPlanner:
    def __init__(self, model: str = DEFAULT_MODEL, timeout: float = 120.0, llm=None, prompt: dict | None = None):
        from .pipeline import resolve_prompt
        self.model = model
        self.llm = llm if llm is not None else make_llm(model, timeout, json_mode=True)
        self.system_prompt = resolve_prompt(PLANNER_PROMPT, prompt)

    async def plan(self, context: dict) -> dict:
        payload = {"hypotheses": context["diagnosis"]["hypotheses"],
                   "contributors": context["diagnosis"].get("contributors"),
                   "rules": context["detection"]["rules"],
                   "kg_evidence": context.get("knowledge") or [],
                   "snapshot": context.get("snapshot")}
        msg = await ainvoke(self.llm, [("system", self.system_prompt),("human", json.dumps(payload, default=str))])
        self.last_usage = USAGE.record("planning", self.model, msg)
        return parse_json_object(message_text(msg))
