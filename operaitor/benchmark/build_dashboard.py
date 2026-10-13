"""Build the fault-campaign dashboard: one self-contained HTML page with the run data embedded.

    py -3 -m operaitor.benchmark.build_dashboard        # -> benchmark_results/fault_campaign_dashboard.html

Reads benchmark_results/profit_campaign.json (run `py -3 -m operaitor.benchmark.profit_campaign report` first),
the per-run files in benchmark_results/runs/, loop_tests.json. Trajectories are downsampled to 1-min points.
Other LLMs run on the same protocol (benchmark_results/open_models/<model>/runs/) appear in a Models tab,
including models whose campaign is still running.
"""
from __future__ import annotations

import json
from pathlib import Path

from .economics import PRICES
from .profit_campaign import AGENT_MODES, FOOTPRINT_SCALE, MODE_LABEL, OUT, RUNS, SCENARIOS

TEMPLATE = Path(__file__).with_name("dashboard_template.html")
TARGET = OUT / "fault_campaign_dashboard.html"
VARS = list(FOOTPRINT_SCALE)
OPEN_MODELS = OUT / "open_models"
OPEN_MODELS_V2 = OUT / "open_models_v2"   # protocol v2: event-triggered, one decision per fault
# model name as the agents saw it -> display name, parameters, where to get it, licence
MODEL_INFO = {
    "gpt-5-nano": ("GPT-5 nano", "not disclosed", "OpenAI API", "proprietary"),
    "llama3.2:latest": ("Llama 3.2 3B Instruct", "3.2B", "meta-llama/Llama-3.2-3B-Instruct", "Llama 3.2 Community"),
    "qwen3:8b": ("Qwen3 8B, thinking off", "8.2B", "Qwen/Qwen3-8B", "Apache-2.0"),
    "mistral:7b": ("Mistral 7B Instruct v0.3", "7.2B", "mistralai/Mistral-7B-Instruct-v0.3", "Apache-2.0"),
    "cas/ministral-8b-instruct-2410_q4km:latest": ("Ministral 8B Instruct 2410", "8.0B",
                                                    "mistralai/Ministral-8B-Instruct-2410", "Mistral Research"),
}

def _series(run: dict) -> dict:
    ticks = [tk for tk in run["ticks"] if abs(tk["t"] - round(tk["t"])) < 1e-6]  # 1-min points
    out = {"t": [round(tk["t"], 1) for tk in ticks], "m": [tk["margin_usd_h"] for tk in ticks],
           "spec": [1 if tk["on_spec"] else 0 for tk in ticks], "al": [tk["alarms_listed"] for tk in ticks]}
    for v in VARS:
        out[v] = [tk[v] for tk in ticks]
    return out


def _admit(events: list[dict]) -> list[dict]:
    """ADMIT audit trail for the dashboard: the ledger once per decision point, every attempt's records."""
    out, seen = [], set()
    for e in events:
        key = (e.get("cycle"), round(e.get("t", 0), 2))
        ledger = None
        if key not in seen and e.get("ledger"):
            seen.add(key)
            ledger = [{"id": x["id"], "source": x["source"], "trust": x["trust"], "content": x["content"][:140]}
                      for x in e["ledger"]]
        out.append({"t": round(e["t"], 1), "cycle": e.get("cycle"), "attempt": e.get("attempt"),
                    "admissible": e.get("admissible"), "simulate": e.get("simulate"), "context": e.get("context"),
                    "ledger": ledger, "records": [
                        {"action": r.get("action"), "params": r.get("params"), "admissible": r.get("admissible"),
                         "first_failing": r.get("first_failing"), "aas_code": r.get("aas_code"),
                         "record": r.get("record"), "rule": {k: v for k, v in (r.get("rule") or {}).items() if k != "description"},
                         "gates": [{"tier": g["tier"], "passed": g["passed"], "skipped": g.get("skipped"),
                                    "violations": [v["message"][:220] for v in g.get("violations") or []]}
                                   for g in r.get("gates") or []],
                         "t4": {k: {kk: v.get(kk) for kk in ("crossed", "crossed_var", "crossed_at_s", "min_margin")}
                                for k, v in (r.get("trajectories") or {}).items()
                                if isinstance(v, dict) and k.endswith("trajectory")}}
                        for r in e.get("records") or []]})
    return out


def _run_summary(run: dict) -> dict:
    keep = ("status", "status_reason", "end_min", "tripped", "trip_min", "sifs", "sif_first_min", "profit_usd",
            "on_spec_acetone_kmol", "off_spec_min", "alarms_listed_at_end", "window_min", "schedule", "wall_s")
    out = {k: run.get(k) for k in keep}
    out["alarm_history"] = [{k: (round(v, 1) if k == "t" else v) for k, v in h.items()} for h in run["alarm_history"]]
    out["series"] = _series(run)
    a = run.get("agent")
    if a:
        out["agent"] = {
            "first_detection_min": a["first_detection_min"], "fault_cycles": a["fault_cycles"],
            "decisions": a["decisions"], "reprompts": a["reprompts"], "fallbacks": a["fallbacks"],
            "actions": [{"t": round(x["t"], 1), "kind": x["kind"], "commands": x["commands"],
                         "diagnosis": x["diagnosis"]} for x in a["actions"]],
            "validations": [{"t": round(v["t"], 1), "cycle": v["cycle"], "accepted": v["accepted"],
                             "commands": v["commands"], "failed": v["failed"][:2]} for v in a["validations"]],
            "alarm_acks": [{"t": round(x["t"], 1), "alarms": x["alarms"], "reason": x["reason"]} for x in a["alarm_acks"]],
            "verification": [{"t": round(x["t"], 1), "reason": x["reason"]} for x in a["verification"]],
            "preset": a.get("preset"), "llm_model": a.get("llm_model"), "llm_usage": a.get("llm_usage"),
            "judge": [{"t": round(j["t"], 1), "cycle": j.get("cycle"), "accept": j.get("accept"),
                       "reason": j.get("reason"), "commands": j.get("commands")} for j in a.get("judge", [])],
            "admit": _admit(a.get("admit") or []),
            "proposals": a.get("proposals"), "llm_errors": a.get("llm_errors", []),
            "timing": {k: v for k, v in (a.get("timing") or {}).items() if not isinstance(v, list)} or None,
        }
    return out


def _model_entry(root: Path, camp: dict | None = None, protocol: str = "v1") -> dict | None:
    """One model's results on the campaign protocol, from the run files in ``root``/runs (finished runs only)."""
    from . import profit_campaign as pc
    runs = root / "runs"
    if camp is None:
        if not (runs / "NOMINAL_nominal.json").exists():
            return None
        saved = pc.OUT, pc.RUNS
        pc.OUT, pc.RUNS = root, runs
        try:
            camp = pc.report(write=False)
        finally:
            pc.OUT, pc.RUNS = saved
    model = None
    for f in sorted(runs.glob("*.json")):
        if f.stem.split("_", 1)[1] in AGENT_MODES:
            model = (json.loads(f.read_text(encoding="utf-8")).get("agent") or {}).get("llm_model")
            if model:
                break
    if model is None or not camp.get("configs"):
        return None
    name, params, source, licence = MODEL_INFO.get(model, (model, "?", "?", "?"))
    configs = {}
    for m, c in camp["configs"].items():
        per = {r["id"]: r["configs"][m] for r in camp["rows"] if m in r["configs"]}
        configs[m] = {"n": c["n"], "complete": c["complete"], "trips": c["trips"], "saving": c["saving"], "profit": c["profit"],
                      "llm_calls": c["llm_calls"], "llm_errors": sum(v["llm_errors"] for v in per.values()),
                      "llm_cost_usd": c["llm_cost_usd"], "mean_decision_s": c.get("mean_decision_s"),
                      "failure_types": c.get("failure_types") or {}, "decisions": c.get("n_decisions"),
                      "mean_llm_latency_s": c.get("mean_llm_latency_s"),
                      "runs": {sid: {"trip_min": v["trip_min"], "saving": v["saving"]} for sid, v in per.items()}}
    if protocol != "v1":
        name += f" · {protocol}"
    return {"model": model, "protocol": protocol, "name": name, "params": params, "source": source, "licence": licence,
            "runs_done": sum(c["n"] for c in configs.values()), "configs": configs}


def _models(camp: dict) -> list[dict]:
    """This campaign's model first, then every other model run on the same protocol."""
    out = [e for e in [_model_entry(OUT, camp)] if e]
    for root, proto in ((OPEN_MODELS, "v1"), (OPEN_MODELS_V2, "v2")):
        if root.is_dir():
            for d in sorted(root.iterdir()):
                e = _model_entry(d, protocol=proto) if d.is_dir() else None
                if e and all((e["model"], e["protocol"]) != (x["model"], x["protocol"]) for x in out):
                    out.append(e)
    order = list(MODEL_INFO)
    return out[:1] + sorted(out[1:], key=lambda e: (e["protocol"], order.index(e["model"]) if e["model"] in order else len(order)))


def _scenarios(runs_dir: Path) -> dict:
    scen = {}
    for s in SCENARIOS:
        sid = s["id"]
        runs = {m: json.loads((runs_dir / f"{sid}_{m}.json").read_text(encoding="utf-8"))
                for m in ("probe", "passive", *AGENT_MODES) if (runs_dir / f"{sid}_{m}.json").exists()}
        scen[sid] = {"id": sid, "group": s["group"], "interaction": s.get("interaction"),
                     "faults": [{"fault": f["fault"], "at_min": f["at_min"]} for f in s["faults"]],
                     "probe": {k: runs["probe"][k] for k in ("status", "trip_min", "sifs", "sif_first_min")},
                     "runs": {m: _run_summary(r) for m, r in runs.items() if m != "probe"}}
    return scen


# The main dashboard's Overview / Scenarios show the main LLM under the current protocol (v2) by default,
# and the original campaign (v1) on request; both are embedded.
MAIN_V2 = OUT / "open_models_v2" / "gpt-5-nano"


def build(status: dict | None = None) -> Path:
    camp = json.loads((OUT / "profit_campaign.json").read_text(encoding="utf-8"))
    nominal = json.loads((RUNS / "NOMINAL_nominal.json").read_text(encoding="utf-8"))
    scen = _scenarios(RUNS)
    views = {}
    if (MAIN_V2 / "profit_campaign.json").exists():
        v2 = json.loads((MAIN_V2 / "profit_campaign.json").read_text(encoding="utf-8"))
        views = {"v2": {"campaign": v2, "scenarios": _scenarios(MAIN_V2 / "runs"), "label": "protocol v2 (current)"},
                 "v1": {"campaign": camp, "scenarios": scen, "label": "protocol v1"}}
    loops = json.loads((OUT / "loop_tests.json").read_text(encoding="utf-8")) if (OUT / "loop_tests.json").exists() else {}
    if (OUT / "loop_series.json").exists():
        loops["series"] = json.loads((OUT / "loop_series.json").read_text(encoding="utf-8"))
    # with views, the campaigns live only in "views" (the page picks one); top level stays for single-view builds
    data = {"campaign": None if views else camp, "scenarios": None if views else scen, "views": views,
            "default_view": "v2" if views else None,
            "order": [s["id"] for s in SCENARIOS], "mode_labels": MODE_LABEL,
            "status": status or {},
            "nominal": _series(nominal), "loops": loops, "models": _models(camp),
            "prices": {"trip_penalty": PRICES.trip_penalty_usd, "acetone_usd_t": PRICES.acetone_usd_t,
                       "ipa_usd_t": PRICES.ipa_usd_t, "spec": PRICES.product_spec_x,
                       "steam_usd_GJ": PRICES.steam_usd_GJ, "h2_usd_kg": PRICES.h2_fuel_usd_kg,
                       "recycle": PRICES.recycle_credit, "shutdown": PRICES.shutdown_cost_usd,
                       "startup": PRICES.startup_cost_usd}}
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")))
    TARGET.write_text(html, encoding="utf-8")
    return TARGET


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", default="", help="progress note shown at the top of the page")
    args = ap.parse_args()
    p = build({"note": args.status} if args.status else None)
    print(f"wrote {p} ({p.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
