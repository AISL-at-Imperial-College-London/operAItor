"""Build the fault-campaign dashboard: one self-contained HTML page with the run data embedded.

    py -3 -m acetone_plant.benchmark.build_dashboard        # -> benchmark_results/fault_campaign_dashboard.html

Reads benchmark_results/profit_campaign.json (run `py -3 -m acetone_plant.benchmark.profit_campaign report` first),
the per-run files in benchmark_results/runs/, loop_tests.json. Trajectories are downsampled to 1-min points.
"""
from __future__ import annotations

import json
from pathlib import Path

from .economics import PRICES
from .profit_campaign import AGENT_MODES, FOOTPRINT_SCALE, MODE_LABEL, OUT, RUNS, SCENARIOS

TEMPLATE = Path(__file__).with_name("dashboard_template.html")
TARGET = OUT / "fault_campaign_dashboard.html"
VARS = list(FOOTPRINT_SCALE)

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


def build(status: dict | None = None) -> Path:
    camp = json.loads((OUT / "profit_campaign.json").read_text(encoding="utf-8"))
    nominal = json.loads((RUNS / "NOMINAL_nominal.json").read_text(encoding="utf-8"))
    scen = {}
    for s in SCENARIOS:
        sid = s["id"]
        runs = {m: json.loads((RUNS / f"{sid}_{m}.json").read_text(encoding="utf-8"))
                for m in ("probe", "passive", *AGENT_MODES) if (RUNS / f"{sid}_{m}.json").exists()}
        scen[sid] = {"id": sid, "group": s["group"], "interaction": s.get("interaction"),
                     "faults": [{"fault": f["fault"], "at_min": f["at_min"]} for f in s["faults"]],
                     "probe": {k: runs["probe"][k] for k in ("status", "trip_min", "sifs", "sif_first_min")},
                     "runs": {m: _run_summary(r) for m, r in runs.items() if m != "probe"}}
    loops = json.loads((OUT / "loop_tests.json").read_text(encoding="utf-8")) if (OUT / "loop_tests.json").exists() else {}
    if (OUT / "loop_series.json").exists():
        loops["series"] = json.loads((OUT / "loop_series.json").read_text(encoding="utf-8"))
    data = {"campaign": camp, "scenarios": scen, "order": [s["id"] for s in SCENARIOS], "mode_labels": MODE_LABEL,
            "status": status or {},
            "nominal": _series(nominal), "loops": loops,
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
