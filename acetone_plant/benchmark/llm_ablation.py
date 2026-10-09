"""Is it the model or the context? Ablation of the LLM action agent on the hard scenarios.

Mode "lucky" (no validator), so the outcome reflects the LLM's own decisions. Every combination
of model x context level x repeat runs on the same faults, lift times and economics as the main
campaign (fault schedule from benchmark_results/runs/<id>_probe.json).

Context levels (agents/action.py, PLANT_ACTION_CONTEXT):
  base    the prompt used in the campaign
  loops   + what each loop's setpoint physically does, brief levers as suggestions, no
          "single change" rule
  manual  + a short generic operating manual (column / drum balances, biased transmitters)

    py -3 -m acetone_plant.benchmark.llm_ablation run --jobs 16
    py -3 -m acetone_plant.benchmark.llm_ablation report          # -> benchmark_results/ablation/ABLATION.md
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import os

from .profit_campaign import BY_ID, OUT, _load, _utf8, _window_profit, lift_schedule, run_scenario, window_for

ABL = OUT / "ablation"
SCENARIOS = ["S08", "C01", "D04", "S09", "S05", "S07"]   # where gpt-5-nano failed (stops) in the campaign
MODELS = ["gpt-5-nano", "gpt-5-mini"]
CONTEXTS = ["base", "loops", "manual", "dynamics"]
# knowledge-graph ablation: the same context levels with the KG switched off (no KG evidence; the
# planning brief then has no KG levers either), on the drum / condenser scenarios
NOKG_CONTEXTS = ["base-nokg", "dynamics-nokg"]
NOKG_SCENARIOS = ["S09", "D04", "S08", "C01"]
REPEATS = 2


def _job(args: tuple) -> str:
    sid, model, ctx, rep = args
    _utf8()
    os.environ["PLANT_LLM_MODEL"] = model
    os.environ["PLANT_ACTION_CONTEXT"] = ctx.removesuffix("-nokg")
    os.environ["PLANT_USE_KG"] = "0" if ctx.endswith("-nokg") else "1"
    sched = lift_schedule(BY_ID[sid], _load(sid, "probe"))
    res = asyncio.run(run_scenario(BY_ID[sid], "lucky", sched, window_for(sched)))
    res["ablation"] = {"model": model, "context": ctx, "repeat": rep}
    ABL.mkdir(parents=True, exist_ok=True)
    (ABL / f"{sid}_{model}_{ctx}_r{rep}.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    u = (res.get("agent") or {}).get("llm_usage") or {}
    return f"{sid} {model:11s} {ctx:6s} r{rep} {res['status']:12s} trip={res['trip_min']} cost=${u.get('cost_usd', 0):.3f} ({res['wall_s']:.0f}s)"


def run(jobs: int) -> None:
    todo = [(s, m, c, r) for r in range(1, REPEATS + 1) for m in MODELS for c in CONTEXTS for s in SCENARIOS]
    todo += [(s, m, c, r) for r in range(1, REPEATS + 1) for m in MODELS for c in NOKG_CONTEXTS for s in NOKG_SCENARIOS]
    todo = [j for j in todo if not (ABL / f"{j[0]}_{j[1]}_{j[2]}_r{j[3]}.json").exists()]
    with ProcessPoolExecutor(jobs) as ex:
        futs = {ex.submit(_job, j): j for j in todo}
        for f in as_completed(futs):
            try:
                print(f.result(), flush=True)
            except Exception as exc:
                print(f"{futs[f]} FAILED: {type(exc).__name__}: {exc}", flush=True)


def _cmd(c: dict) -> str:
    return c["type"] + (f" {c['target']}" if c.get("target") else "") + (f" {c['value']}" if c.get("value") is not None else "")


def report() -> dict:
    rows, levers = [], {}
    for f in sorted(p for p in ABL.glob("*.json") if p.name != "ablation.json"):
        r = json.loads(f.read_text(encoding="utf-8"))
        a, ab = r.get("agent") or {}, r["ablation"]
        cmds = [c for v in a.get("validations", []) for c in v["commands"]]
        key = (ab["model"], ab["context"])
        lv = levers.setdefault(key, {})
        for c in cmds:
            k = c["type"] + (":" + c["target"] if c.get("target") else "")
            lv[k] = lv.get(k, 0) + 1
        top_wrong = sum(1 for c in cmds if c.get("target") == "TOP_TIC" and c["value"] < 331.72 and "reflux_drum_leak" in r["faults"])
        top_all = sum(1 for c in cmds if c.get("target") == "TOP_TIC" and "reflux_drum_leak" in r["faults"])
        rows.append({"id": r["id"], **ab, "status": r["status"], "trip_min": r["trip_min"], "stop": r["tripped"],
                     "profit": round(_window_profit(r, r["window_min"]), 0), "proposals": len(cmds),
                     "uses_BOT_TIC": any(c.get("target") == "BOT_TIC" for c in cmds),
                     "top_tic_wrong": top_wrong, "top_tic_all": top_all,
                     "cost": ((a.get("llm_usage") or {}).get("cost_usd") or 0.0),
                     "mean_llm_s": ((a.get("timing") or {}).get("llm_s", 0) / max(1, (a.get("timing") or {}).get("n_llm_calls", 1))),
                     "applied": [(round(x["t"], 1), [_cmd(c) for c in x["commands"]]) for x in a.get("actions", [])][:6]})
    summary = {}
    for m in MODELS:
        for c in CONTEXTS + NOKG_CONTEXTS:
            rs = [r for r in rows if r["model"] == m and r["context"] == c]
            if not rs:
                continue
            cond = [r for r in rs if r["id"] in ("S08", "C01")]
            summary[f"{m}|{c}"] = {"runs": len(rs), "stops": sum(r["stop"] for r in rs),
                                   "saved": sum(not r["stop"] for r in rs), "profit": sum(r["profit"] for r in rs),
                                   "cost": round(sum(r["cost"] for r in rs), 3),
                                   "mean_llm_s": round(sum(r["mean_llm_s"] for r in rs) / len(rs), 1),
                                   "condenser_runs_using_BOT_TIC": f"{sum(r['uses_BOT_TIC'] for r in cond)}/{len(cond)}",
                                   "drum_TOP_TIC_wrong_direction": f"{sum(r['top_tic_wrong'] for r in rs)}/{sum(r['top_tic_all'] for r in rs)}",
                                   "by_scenario": {s: [("stop" if r["stop"] else "ok") for r in rs if r["id"] == s] for s in SCENARIOS}}
    out = {"scenarios": SCENARIOS, "summary": summary, "rows": rows,
           "levers": {f"{m}|{c}": dict(sorted(v.items(), key=lambda kv: -kv[1])) for (m, c), v in levers.items()}}
    (ABL / "ablation.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    L = ["# LLM ablation: model size vs context (feeling-lucky mode, no validator)", "",
         f"Scenarios: {', '.join(SCENARIOS)} (where gpt-5-nano stopped the plant in the campaign); {REPEATS} repeats each.", "",
         "| model | context | runs | plant saved | stops | profit | condenser runs using BOT_TIC | drum TOP_TIC wrong way | cost |"
         + "".join(f" {s} |" for s in SCENARIOS),
         "|---|---|---:|---:|---:|---:|---:|---:|---:|" + "---|" * len(SCENARIOS)]
    for k, v in summary.items():
        m, c = k.split("|")
        L.append(f"| {m} | {c} | {v['runs']} | {v['saved']} | {v['stops']} | ${v['profit']:,.0f} | "
                 f"{v['condenser_runs_using_BOT_TIC']} | {v['drum_TOP_TIC_wrong_direction']} | ${v['cost']:.2f} |"
                 + "".join(f" {'/'.join(v['by_scenario'][s]) or '—'} |" for s in SCENARIOS))
    (ABL / "ABLATION.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return out


def main() -> None:
    _utf8()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "report"])
    ap.add_argument("--jobs", type=int, default=16)
    a = ap.parse_args()
    if a.cmd == "run":
        run(a.jobs)
    report()
    print((ABL / "ABLATION.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
