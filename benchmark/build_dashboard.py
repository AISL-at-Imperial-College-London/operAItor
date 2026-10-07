"""Build the fault-campaign dashboard: one self-contained HTML page with the run data embedded.

    py -3 -m benchmark.build_dashboard        # -> benchmark_results/fault_campaign_dashboard.html

Reads benchmark_results/profit_campaign.json (run `py -3 -m benchmark.profit_campaign report` first),
the per-run files in benchmark_results/runs/, the earlier agent rounds in runs_v*_agents/ and
loop_tests.json. Trajectories are downsampled to 1-min points.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

from .economics import PRICES
from .profit_campaign import BY_ID, FOOTPRINT_SCALE, OUT, RUNS, SCENARIOS

TEMPLATE = Path(__file__).with_name("dashboard_template.html")
TARGET = OUT / "fault_campaign_dashboard.html"
VARS = list(FOOTPRINT_SCALE)

# fixes made after each agent round (from WORKLOG_2026-10-06.md)
ROUND_NOTES = {
    "v1": "First run. NO_ACTION accepted without a twin check; SIF latches never reset; spares tried one at a time.",
    "v2": "Twin checks NO_ACTION; counterfactual fallback; agents request SIF resets; twin knows only the present; all spares at once.",
    "v3": "Condenser-limited: cut boilup, reflux and feed together; unwind setpoints after the fault; stricter drum rule.",
    "v4": "Unwind allowed within 0.03 bar of the column-pressure setpoint; new priority-1 alarms override the wait.",
    "v5": "Unwind ignores the pinned COL_PIC valve; throughput back in 20 % steps.",
    "v6": "Unwind offered first when quiet; condenser cuts need hard evidence; temperature setpoints back 3 K per step.",
    "v7": "Condenser gate relaxed (pressure > setpoint + 0.03 bar). All 20 complete, 19 without a stop.",
    "final": "Same code as v7 plus full agent logs and SIF-reset alarm acknowledgement. Profits identical to v7.",
}


def _series(run: dict) -> dict:
    ticks = [tk for tk in run["ticks"] if abs(tk["t"] - round(tk["t"])) < 1e-6]  # 1-min points
    out = {"t": [round(tk["t"], 1) for tk in ticks], "m": [tk["margin_usd_h"] for tk in ticks],
           "spec": [1 if tk["on_spec"] else 0 for tk in ticks], "al": [tk["alarms_listed"] for tk in ticks]}
    for v in VARS:
        out[v] = [tk[v] for tk in ticks]
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
        }
    return out


def _rounds() -> list[dict]:
    rows = []
    dirs = sorted(glob.glob(str(OUT / "runs_v*_agents"))) + [str(RUNS)]
    for d in dirs:
        name = Path(d).name.replace("runs_", "").replace("_agents", "") if d != str(RUNS) else "final"
        runs = [json.loads(Path(f).read_text(encoding="utf-8")) for f in glob.glob(d + "/*_agents.json")]
        if not runs:
            continue
        stops = [r["id"] for r in runs if r["tripped"]]
        rows.append({"round": name, "finished": len(runs), "stops": len(stops), "stopped": sorted(stops),
                     "survived": sorted(r["id"] for r in runs if not r["tripped"]), "note": ROUND_NOTES.get(name, "")})
    return rows


def build() -> Path:
    camp = json.loads((OUT / "profit_campaign.json").read_text(encoding="utf-8"))
    nominal = json.loads((RUNS / "NOMINAL_nominal.json").read_text(encoding="utf-8"))
    scen = {}
    for s in SCENARIOS:
        sid = s["id"]
        runs = {m: json.loads((RUNS / f"{sid}_{m}.json").read_text(encoding="utf-8"))
                for m in ("probe", "passive", "agents") if (RUNS / f"{sid}_{m}.json").exists()}
        scen[sid] = {"id": sid, "group": s["group"], "interaction": s.get("interaction"),
                     "faults": [{"fault": f["fault"], "at_min": f["at_min"]} for f in s["faults"]],
                     "probe": {k: runs["probe"][k] for k in ("status", "trip_min", "sifs", "sif_first_min")},
                     "passive": _run_summary(runs["passive"]), "agents": _run_summary(runs["agents"])}
    loops = json.loads((OUT / "loop_tests.json").read_text(encoding="utf-8")) if (OUT / "loop_tests.json").exists() else {}
    data = {"campaign": camp, "scenarios": scen, "order": [s["id"] for s in SCENARIOS],
            "nominal": _series(nominal), "rounds": _rounds(), "loops": loops,
            "prices": {"trip_penalty": PRICES.trip_penalty_usd, "acetone_usd_t": PRICES.acetone_usd_t,
                       "ipa_usd_t": PRICES.ipa_usd_t, "spec": PRICES.product_spec_x,
                       "steam_usd_GJ": PRICES.steam_usd_GJ, "h2_usd_kg": PRICES.h2_fuel_usd_kg,
                       "recycle": PRICES.recycle_credit, "shutdown": PRICES.shutdown_cost_usd,
                       "startup": PRICES.startup_cost_usd}}
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")))
    TARGET.write_text(html, encoding="utf-8")
    return TARGET


if __name__ == "__main__":
    p = build()
    print(f"wrote {p} ({p.stat().st_size / 1e6:.2f} MB)")
