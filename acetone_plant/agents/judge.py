"""LLM-as-judge validator: reviews a proposed action without a simulator.

Used by the ``llm_judge`` preset in place of the digital twin. The judge sees what the
action agent saw (snapshot, monitoring, diagnosis, plan brief, KG evidence) plus the
proposed commands and the alarm / SIF limits, and returns a verdict:

    {"accept": true|false, "confidence": 0..1, "reason": "...", "risks": [...], "suggestion": "..."}

A rejection is fed back to the action agent through the reprompter, exactly like a twin
rejection. If the judge call fails the proposal is rejected (no unreviewed action).
"""
from __future__ import annotations

import json
import time
from typing import Any

from acetone_plant.plant.alarms import default_alarm_definitions
from acetone_plant.plant.safety import SafetyConfig

from .action import slim_observation
from .llm import DEFAULT_MODEL, USAGE, make_llm, message_text, parse_json_object

JUDGE_SYSTEM_PROMPT = """
You are the **Validation Agent (judge)** for an integrated isopropanol→acetone plant: an
endothermic liquid-phase CSTR (R-101) with H2 vent (PCV), blowdown (BDV) and relief, feeding a
10-tray distillation column (T-201) with condenser, reflux drum and reboiler, and five
duty/standby pump trains. Eight PI loops (R_LIC, R_TIC, R_PIC, RB_LIC, DR_LIC, COL_PIC, TOP_TIC,
BOT_TIC) run under the proposed supervisory commands.

A fault has been detected. The action agent proposed supervisory commands. You have NO
simulator: judge the proposal from process knowledge, the live snapshot, the diagnosis, the
planning brief and the knowledge-graph evidence.

Accept only if ALL of these hold:
1. It addresses the diagnosed fault (or, for a sensor fault / transient, NO_ACTION is right).
2. It does not push any variable toward a SIF limit (table provided); a plant trip costs far
   more than lost production.
3. It changes nothing unrelated to the fault (every extra setpoint move disturbs the plant).
4. It is proportionate: the smallest change that should bring the faulty variables back within
   their alarm limits. Prefer starting a healthy standby pump over load cuts when a pump failed.
5. Directions are right: e.g. TOP_TIC is reverse acting on reflux (higher setpoint = less
   reflux), BOT_TIC drives steam (lower setpoint = less boilup), R_PIC opens the vent PCV,
   the BDV removes H2 directly.

Reject otherwise, and say concretely what should change.

Return ONE JSON object only:
{"accept": true|false, "confidence": number between 0 and 1, "reason": "short", "risks": ["..."],
 "suggestion": "what the action agent should propose instead (if rejecting)"}
"""


def _limits() -> dict:
    c = SafetyConfig()
    sif = {"R_T_HH": [c.cstr_temperature_hh_K, "K", "high"], "R_P_HH": [c.cstr_pressure_hh_bar, "bar", "high"],
           "R_P_HHH": [c.cstr_pressure_hhh_bar, "bar", "high, plant trip"], "R_L_HH": [c.cstr_level_hh_kmol, "kmol", "high"],
           "R_L_LL": [c.cstr_level_ll_kmol, "kmol", "low"], "RB_L_LL": [c.reboiler_level_ll_kmol, "kmol", "low"],
           "RB_L_HH": [c.reboiler_level_hh_kmol, "kmol", "high, plant trip"], "DR_L_LL": [c.drum_level_ll_kmol, "kmol", "low, plant trip"],
           "DR_L_HH": [c.drum_level_hh_kmol, "kmol", "high, plant trip"], "COL_P_HH": [c.column_pressure_hh_bar, "bar", "high, plant trip"],
           "COL_L_LL": [c.column_tray_holdup_ll_kmol, "kmol (lowest tray holdup)", "low, plant trip"]}
    alarms = {d.alarm_id: [d.tag, d.direction, d.limit, d.unit] for d in default_alarm_definitions()
              if not d.tag.startswith("pump_")}
    return {"sif_limits": sif, "alarm_limits": alarms}


class LLMJudge:
    def __init__(self, model: str = DEFAULT_MODEL, timeout: float = 120.0, llm: Any = None):
        self.model = model
        self.llm = llm if llm is not None else make_llm(model, timeout, json_mode=True)
        self.last_usage: dict = {}

    async def judge(self, context: dict, plan: dict) -> dict:
        user = {
            "proposed_commands": plan.get("commands"),
            "action_agent_reasoning": plan.get("reasoning"),
            "plan_brief": context.get("brief"),
            "snapshot": slim_observation(context["observation"]),
            "monitoring": {k: context["detection"][k] for k in ("pca", "rules")},
            "diagnosis": {k: context["diagnosis"].get(k) for k in ("hypotheses", "contributors", "summary")},
            "kg_evidence": context.get("knowledge") or [],
            **_limits(),
        }
        t0 = time.perf_counter()
        try:
            msg = await self.llm.ainvoke([("system", JUDGE_SYSTEM_PROMPT), ("human", json.dumps(user, default=str))])
            text = message_text(msg)
            self.last_usage = {**USAGE.record("judge", self.model, msg), "latency_s": time.perf_counter() - t0}
            out = parse_json_object(text)
            return {"accept": bool(out.get("accept")), "confidence": out.get("confidence"),
                    "reason": str(out.get("reason", ""))[:600], "risks": out.get("risks") or [],
                    "suggestion": str(out.get("suggestion", ""))[:600], "latency_s": self.last_usage["latency_s"],
                    "tokens": self.last_usage.get("total_tokens")}
        except Exception as exc:  # no unreviewed action: a failed review is a rejection
            return {"accept": False, "confidence": None, "reason": f"judge unavailable: {type(exc).__name__}: {exc}"[:600],
                    "risks": [], "suggestion": "", "latency_s": time.perf_counter() - t0, "error": True}
