"""How well is each fault diagnosed? Rule-based heuristic vs the LLM diagnosis agent.

For each of the 20 campaign scenarios the plant runs with nobody acting. When the PCA monitor first
confirms a fault (3 samples over the limit) and again 5 min later, the same evidence (T² / SPE,
contributions, rule checks, trends) goes to:
  * the heuristic (``fdd.diagnose``, used by the pipeline so far), and
  * the LLM diagnosis agent (``agents/diagnosis_llm.py``) with each model in MODELS.
Truth = the faults injected by that time. Scored: top-1 (most likely is a true fault) and top-3
(a true fault among the first three hypotheses).

    py -3 -m benchmark.diagnosis_eval --jobs 10      # -> benchmark_results/diagnosis_eval.json / .md
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ProcessPoolExecutor
import json
import os

from .profit_campaign import OUT, SCENARIOS, _utf8

MODELS = ["gpt-5-nano", "gpt-5-mini"]
LATER_MIN = 5.0


async def _snapshots(scn: dict) -> list[dict]:
    from agents.monitoring import MonitoringAgent
    from server.campaigns import FAULT_LIBRARY
    from server.plant_service import PlantService
    s = PlantService()
    s.pace_every = 0
    await s.load({"scenario": "nominal", "horizon_min": 120, "stop_on_trip": True})
    mon = MonitoringAgent.from_default_model(persistence=3, rules_trigger=False)
    faults = sorted(scn["faults"], key=lambda f: f["at_min"])
    shots, first = [], None
    while 60 * s.env.t_h < 100 and not s.env.done:
        t = 60 * s.env.t_h
        for f in faults:
            if abs(t - f["at_min"]) < 1e-6:
                p = {k: v for k, v in FAULT_LIBRARY[f["fault"]].items() if k != "vary"}
                p.update({k: v for k, v in f.items() if k not in ("fault", "at_min")})
                await s._inject({**p, "duration_min": 0})
        await s.step(0.5)
        obs = s.env.observation()
        out = await mon({"observation": obs})
        det = out["detection"]
        if det["pca_persistent"] and first is None:
            first = det["time_min"]
        if first is not None and (abs(det["time_min"] - first) < 1e-6 or abs(det["time_min"] - first - LATER_MIN) < 1e-6):
            truth = [f["fault"] for f in faults if f["at_min"] <= det["time_min"] + 1e-6]
            shots.append({"t": det["time_min"], "at": "detection" if not shots else f"+{LATER_MIN:g} min",
                          "truth": truth, "detection": det, "heuristic": out["diagnosis"], "observation": obs})
            if len(shots) == 2:
                break
    return shots


def _score(truth: list[str], ranked: list[str]) -> dict:
    return {"top1": bool(ranked) and ranked[0] in truth, "top3": any(f in truth for f in ranked[:3]),
            "found": sorted(set(truth) & set(ranked[:3]))}


def _job(sid: str) -> dict:
    _utf8()
    from agents.diagnosis_llm import LLMDiagnoser
    from agents.kg import KnowledgeGraph
    from server.plant_service import jsonable
    scn = next(s for s in SCENARIOS if s["id"] == sid)
    shots = asyncio.run(_snapshots(scn))
    kg = KnowledgeGraph()
    rows = []
    for sh in shots:
        heur = [h["fault"] for h in sh["heuristic"]["hypotheses"]]
        row = {"id": sid, "group": scn["group"], "t": sh["t"], "at": sh["at"], "truth": sh["truth"],
               "pca": {k: (sh["detection"]["pca"] or {}).get(k) for k in ("t2", "t2_limit", "spe", "spe_limit")},
               "heuristic": {"ranked": heur[:4], **_score(sh["truth"], heur)}, "llm": {}}
        for m in MODELS:
            d = LLMDiagnoser(m, kg=kg)
            v = asyncio.run(d.diagnose(sh["detection"], sh["heuristic"], sh["observation"]))
            ranked = [h["fault"] for h in v.get("hypotheses", [])]
            if v.get("most_likely") and v["most_likely"] in ranked:
                ranked = [v["most_likely"]] + [f for f in ranked if f != v["most_likely"]]
            row["llm"][m] = {"ranked": ranked[:4], **_score(sh["truth"], ranked), "summary": v.get("summary"),
                             "fault_class": v.get("fault_class"), "t2_spe_reading": v.get("t2_spe_reading"),
                             "ok": v.get("ok"), "error": v.get("error"), "latency_s": v.get("latency_s"),
                             "tokens": v.get("tokens")}
        rows.append(jsonable(row))
    return {"id": sid, "rows": rows, "detected": bool(shots)}


def main() -> None:
    _utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=10)
    a = ap.parse_args()
    with ProcessPoolExecutor(a.jobs) as ex:
        results = list(ex.map(_job, [s["id"] for s in SCENARIOS]))
    rows = [r for res in results for r in res["rows"]]
    summ = {}
    for at in ("detection", f"+{LATER_MIN:g} min"):
        rs = [r for r in rows if r["at"] == at]
        summ[at] = {"n": len(rs), "heuristic": {k: sum(r["heuristic"][k] for r in rs) for k in ("top1", "top3")},
                    **{m: {k: sum(r["llm"][m][k] for r in rs) for k in ("top1", "top3")} for m in MODELS}}
    out = {"models": MODELS, "summary": summ, "rows": rows,
           "undetected": [res["id"] for res in results if not res["detected"]]}
    (OUT / "diagnosis_eval.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    L = ["# Diagnosis: rule-based heuristic vs LLM diagnosis agent", "",
         "Plant runs with nobody acting; diagnosis at PCA confirmation and 5 min later. top-1 = most likely is a "
         "true fault; top-3 = a true fault among the first three.", "",
         "| moment | n | heuristic top-1 | heuristic top-3 | " + " | ".join(f"{m} top-1 | {m} top-3" for m in MODELS) + " |",
         "|---|---:|---:|---:|" + "---:|---:|" * len(MODELS)]
    for at, v in summ.items():
        L.append(f"| {at} | {v['n']} | {v['heuristic']['top1']} | {v['heuristic']['top3']} | "
                 + " | ".join(f"{v[m]['top1']} | {v[m]['top3']}" for m in MODELS) + " |")
    L += ["", "| id | at | truth | heuristic | " + " | ".join(MODELS) + " |", "|---|---|---|---|" + "---|" * len(MODELS)]
    for r in rows:
        mark = lambda x: ("✓ " if x["top1"] else "◐ " if x["top3"] else "✗ ") + ", ".join(x["ranked"][:3])  # noqa: E731
        L.append(f"| {r['id']} | {r['at']} | {', '.join(r['truth'])} | {mark(r['heuristic'])} | "
                 + " | ".join(mark(r["llm"][m]) for m in MODELS) + " |")
    (OUT / "DIAGNOSIS_EVAL.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:8]))


if __name__ == "__main__":
    main()
