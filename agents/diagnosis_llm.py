"""LLM diagnosis agent: reads the PCA statistics and the rule evidence and says what is likely wrong.

Inputs (all from the monitoring agent, no ground truth):
  * Hotelling T² and SPE (Q) against their 99 % limits, and the variables that contribute most to
    each (``meas.<tag>`` = a measurement, ``op.<loop>`` = a controller output);
  * the rule checks: active alarms, failed duty pumps, saturated loops, latched SIFs;
  * the rule-based (heuristic) hypotheses from ``fdd.diagnose``;
  * recent trends (rate of change, minutes to the next alarm / SIF);
  * the fault catalogue: every fault mode in the knowledge graph and the fault library, with its
    symptoms, so hypotheses use names the planner and KG understand.

Output (JSON):
  {"summary": "<one sentence>", "most_likely": "<catalogue fault>",
   "hypotheses": [{"fault", "probability", "evidence": [...], "against": [...]}],
   "fault_class": "sensor" | "actuator" | "equipment" | "process" | "unclear",
   "t2_spe_reading": "<what the T² / SPE pattern says>", "operator_checks": [...]}

``as_diagnosis`` turns it into the pipeline's diagnosis dict (hypotheses restricted to the
catalogue, the heuristic result kept under ``heuristic``).
"""
from __future__ import annotations

import json
import time
from typing import Any

from .llm import DEFAULT_MODEL, USAGE, make_llm, message_text, parse_json_object

DIAGNOSIS_SYSTEM_PROMPT = """
You are the **Fault Diagnosis Agent** for an integrated isopropanol→acetone plant: an endothermic
liquid-phase CSTR (R-101) with H2 vent (PCV), blowdown (BDV) and relief; a 10-tray distillation
column (T-201) with condenser (cooling-water valve), reflux drum and reboiler (steam); five
duty/standby pump trains (FEED, CSTR_OUTLET, REFLUX, DISTILLATE, BOTTOMS); eight PI loops
(R_LIC, R_TIC, R_PIC, RB_LIC, DR_LIC, COL_PIC, TOP_TIC, BOT_TIC).

A PCA monitor trained on normal operation flags an abnormal state. Explain what is most likely
wrong, using ONLY fault names from `fault_catalogue`.

How to read the evidence
- Hotelling T² measures how far the plant has moved ALONG normal correlation directions; SPE (Q)
  measures how much the normal correlations are BROKEN. High T² with normal SPE: a large but
  "coherent" process upset (load, utility or equipment change propagating normally). High SPE:
  a new correlation, typical of sensor faults (one variable disagrees with the others), leaks and
  actuator faults.
- Contributions: `meas.<tag>` is a measurement, `op.<loop>` a controller output. A loop output
  moving while its PV stays at setpoint means the loop is compensating (look upstream of it).
  One measurement explaining most of the SPE, with its loop's output moving the opposite way to
  what the physics needs, points at that transmitter.
- Rule evidence is hard evidence: a failed pump is certain; an alarm says a variable is beyond its
  limit; a saturated loop has run out of authority; a latched SIF has already acted.
- `rule_based_hypotheses` come from fixed rules; use them as one input, not as the answer.
- Trends: the direction and speed of key variables, and minutes to the next alarm / SIF.
- Several faults can be active at once; say so if the evidence needs more than one.

Return ONE JSON object only:
{"summary": "...", "most_likely": "<fault from catalogue>",
 "hypotheses": [{"fault": "<from catalogue>", "probability": 0..1, "evidence": ["..."], "against": ["..."]}],
 "fault_class": "sensor"|"actuator"|"equipment"|"process"|"unclear",
 "t2_spe_reading": "...", "operator_checks": ["..."]}
Give 1-4 hypotheses, most likely first; probabilities should sum to at most 1.
"""


def fault_catalogue(kg: Any = None) -> list[dict]:
    """Fault modes from the knowledge graph plus the fault library (pump trips are library-only)."""
    from server.campaigns import FAULT_LIBRARY
    cat: dict[str, dict] = {}
    if kg is not None:
        for nid in kg.nodes:
            if "FaultMode" not in kg.types(nid):
                continue
            name = nid.removeprefix("fault_")
            symptoms = [kg.label(o) for p, o in kg.out.get(nid, []) if p == "causesSymptom"]
            cat[name] = {"fault": name, "label": kg.label(nid), "comment": kg.comment(nid), "symptoms": symptoms,
                         "affects": [kg.label(o) for p, o in kg.out.get(nid, []) if p == "affectsParameter"]}
    for name, t in FAULT_LIBRARY.items():
        if t["kind"] == "disturbance" or name in cat:
            continue
        what = {"pump": f"duty pump {t.get('pump_id')} trips (no flow from that train)",
                "actuator": f"actuator {t.get('target')} {t.get('mode')}",
                "sensor": f"transmitter {t.get('target')} {t.get('mode')}",
                "process": f"process fault {t.get('fault')}"}[t["kind"]]
        cat[name] = {"fault": name, "label": name.replace("_", " "), "comment": what, "symptoms": [], "affects": []}
    return sorted(cat.values(), key=lambda c: c["fault"])


class LLMDiagnoser:
    def __init__(self, model: str = DEFAULT_MODEL, timeout: float = 120.0, llm: Any = None, kg: Any = None):
        self.model = model
        self.llm = llm if llm is not None else make_llm(model, timeout, json_mode=True)
        self.catalogue = fault_catalogue(kg)
        self.names = {c["fault"] for c in self.catalogue}
        self.last_usage: dict = {}

    def payload(self, detection: dict, heuristic: dict, observation: dict | None = None) -> dict:
        pca = detection.get("pca") or {}
        out = {
            "time_min": detection.get("time_min"),
            "pca": {k: pca.get(k) for k in ("t2", "t2_limit", "spe", "spe_limit", "t2_violation", "spe_violation")},
            "pca_persistent_samples": detection.get("consecutive_pca"),
            "top_contributors": [{"variable": c["variable"], "share_score": round(c["score"], 3),
                                  "t2_part": round(c["t2"], 3), "spe_part": round(c["spe"], 3)}
                                 for c in (pca.get("top_contributors") or [])],
            "rules": detection.get("rules"),
            "rule_based_hypotheses": [{"fault": h["fault"], "score": round(h["score"], 2), "evidence": h["evidence"]}
                                      for h in (heuristic or {}).get("hypotheses", [])],
            "trends": detection.get("trends") or {},
            "fault_catalogue": self.catalogue,
        }
        if observation is not None:
            out["controllers"] = {k: {f: v.get(f) for f in ("mode", "setpoint", "pv", "last_output")}
                                  for k, v in observation.get("controllers", {}).items()}
            out["pump_trains"] = observation.get("pump_trains")
        return out

    async def diagnose(self, detection: dict, heuristic: dict, observation: dict | None = None) -> dict:
        t0 = time.perf_counter()
        try:
            msg = await self.llm.ainvoke([("system", DIAGNOSIS_SYSTEM_PROMPT),
                                          ("human", json.dumps(self.payload(detection, heuristic, observation), default=str))])
            text = message_text(msg)
            self.last_usage = {**USAGE.record("diagnosis", self.model, msg), "latency_s": time.perf_counter() - t0}
            out = parse_json_object(text)
            hyps = []
            for h in out.get("hypotheses") or []:
                name = str(h.get("fault", "")).removeprefix("fault_")
                if name in self.names:
                    hyps.append({"fault": name, "probability": float(h.get("probability") or 0.0),
                                 "evidence": [str(e) for e in (h.get("evidence") or [])][:6],
                                 "against": [str(e) for e in (h.get("against") or [])][:4]})
            most = str(out.get("most_likely", "")).removeprefix("fault_")
            return {"ok": True, "summary": str(out.get("summary", ""))[:600],
                    "most_likely": most if most in self.names else (hyps[0]["fault"] if hyps else None),
                    "hypotheses": hyps, "fault_class": out.get("fault_class"),
                    "t2_spe_reading": str(out.get("t2_spe_reading", ""))[:600],
                    "operator_checks": [str(c) for c in (out.get("operator_checks") or [])][:5],
                    "latency_s": round(self.last_usage["latency_s"], 2), "tokens": self.last_usage.get("total_tokens")}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300], "hypotheses": [],
                    "latency_s": round(time.perf_counter() - t0, 2)}


def as_diagnosis(llm: dict, heuristic: dict) -> dict:
    """Pipeline diagnosis dict from an LLM verdict; falls back to the heuristic if the LLM failed."""
    if not llm.get("ok") or not llm.get("hypotheses"):
        return {**heuristic, "llm": llm}
    hyps = [{"fault": h["fault"], "score": h["probability"], "evidence": h["evidence"]} for h in llm["hypotheses"]]
    return {**heuristic, "hypotheses": hyps, "summary": llm.get("most_likely") or hyps[0]["fault"],
            "source": "llm", "llm": llm, "heuristic": {k: heuristic.get(k) for k in ("hypotheses", "summary")}}
