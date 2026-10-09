"""Write the acetone-plant ADMIT cartridge (acetone_plant/admit/cartridge/).

    acetone-plant admit build --jobs 8      # simulate the 20 benchmark scenarios -> cases
    acetone-plant admit validate            # admitbench's T0 compile stage on the cartridge
    acetone-plant admit run --provider stub --model oracle    # any `admitbench run` options

One case per benchmark scenario, at the moment PCA confirms the fault (3 samples over the
limit, nobody acting). The hazard is the SIF that trips the plant in the probe run (faults never lifted); the
oracle is found by projecting candidate actions on the plant (``AcetoneWorld``) and keeping the
admissible one with the largest safety margin. Scenarios with no admissible action are listed
in the manifest notes, not turned into cases (an oracle must pass the gates).
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ProcessPoolExecutor
import json
import math
from pathlib import Path
import sys

import yaml

from .spec import FAULT_HAZARD, PLANT_TRIP_SIFS, PROCEDURES, SIF_HAZARD, manifest, safety_case, system_graph

OUT = Path(__file__).resolve().parent / "cartridge"
RESPONSE_S = 300.0


def _probe(sid: str) -> dict:
    from acetone_plant.benchmark.profit_campaign import RUNS
    p = RUNS / f"{sid}_probe.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


async def _decision_point(idx: int):
    """Run scenario ``idx`` with nobody acting until PCA confirms the fault."""
    from acetone_plant.agents.monitoring import MonitoringAgent
    from acetone_plant.benchmark.profit_campaign import SCENARIOS
    from acetone_plant.server.campaigns import FAULT_LIBRARY
    from acetone_plant.server.plant_service import PlantService
    s = PlantService()
    s.pace_every = 0
    await s.load({"scenario": "nominal", "horizon_min": 300, "stop_on_trip": True})
    mon = MonitoringAgent.from_default_model(persistence=3, rules_trigger=False)
    faults = sorted(SCENARIOS[idx]["faults"], key=lambda f: f["at_min"])
    while 60 * s.env.t_h < 120 and not s.env.done:
        t = 60 * s.env.t_h
        for f in faults:
            if abs(t - f["at_min"]) < 1e-6:
                p = {k: v for k, v in FAULT_LIBRARY[f["fault"]].items() if k != "vary"}
                p.update({k: v for k, v in f.items() if k not in ("fault", "at_min")})
                await s._inject({**p, "duration_min": 0})
        await s.step(0.5)
        obs = s.env.observation()
        out = await mon({"observation": obs})
        if out["detection"]["pca_persistent"]:
            return s.env, obs, out["detection"], out["diagnosis"]
    return None, None, None, None


def _candidates(obs: dict, faults: list[str]) -> list[tuple[str, dict]]:
    c: list[tuple[str, dict]] = []
    for train, info in obs["pump_trains"].items():
        if info["selected_failed"] and not info.get("spare_failed"):
            c.append(("start_standby_pump", {"tag": train}))
    c += [("set_throughput", {"value": 70.0}), ("set_throughput", {"value": 50.0}),
          ("open_blowdown", {"value": 0.5}), ("apply_emergency_cooling", {"value": 1.0}),
          ("adjust_setpoint", {"tag": "TOP_TIC", "value": round(obs["controllers"]["TOP_TIC"]["setpoint"] + 2.0, 2)}),
          ("adjust_setpoint", {"tag": "BOT_TIC", "value": round(obs["controllers"]["BOT_TIC"]["setpoint"] - 2.0, 2)})]
    return c


def _case(idx: int) -> dict:
    """Build one case (runs in a worker process)."""
    from acetone_plant.benchmark.profit_campaign import SCENARIOS
    from .gate import evidence_entries
    from .world import AcetoneWorld
    scn = SCENARIOS[idx]
    sid, faults = scn["id"], [f["fault"] for f in scn["faults"]]
    env, obs, det, diag = asyncio.run(_decision_point(idx))
    if env is None:
        return {"id": sid, "skip": "PCA never confirmed a fault"}
    t_dec = obs["time_min"]
    probe = _probe(sid)
    trip_min = probe.get("trip_min")
    sifs = probe.get("sifs") or []
    # The hazard is what trips the plant; a protective (non-trip) SIF only if nothing trips.
    first = sorted(sifs, key=lambda c: (c not in PLANT_TRIP_SIFS, (probe.get("sif_first_min") or {}).get(c, math.inf)))
    hazard = next((SIF_HAZARD[c][0] for c in first if c in SIF_HAZARD), None) or FAULT_HAZARD.get(faults[0])
    world = AcetoneWorld(env=env)
    state = world.initial_state({})
    null = world.project(state, None, None, horizon_s=1800)
    scored = []
    for action, params in _candidates(obs, faults):
        try:
            tr = world.project(state, action, params, horizon_s=1800)
        except ValueError:
            continue
        ok = not tr.crossed and not (not null.crossed and not null.diverging and tr.min_margin < null.min_margin - 0.2)
        scored.append({"action": action, "params": params, "admissible": ok, "min_margin": round(tr.min_margin, 3),
                       "crossed": tr.crossed, "crossed_var": tr.crossed_var})
    good = sorted((s for s in scored if s["admissible"]), key=lambda s: -s["min_margin"])
    window = world.project(state, None, None, horizon_s=RESPONSE_S)
    if good:
        oracle = {"action": good[0]["action"], "params": good[0]["params"]}
    elif not window.crossed:
        oracle = {"action": "escalate_to_operator", "params": {}}
    else:
        return {"id": sid, "skip": "no admissible action (every candidate crosses the envelope)", "scored": scored}
    tags = [t["id"] for t in system_graph() if t.get("kind") == "tag"]
    evidence = evidence_entries(obs, det, None, None, 60.0 * t_dec, tags)
    load_bearing = ["ev_pumps"] if oracle["action"] == "start_standby_pump" else ["ev_pca"]
    phrases = [f.replace("_", " ") for f in faults]
    if hazard:
        phrases += [p for (h, p, _, _) in SIF_HAZARD.values() if h == hazard][:1]
    tth = None if trip_min is None else round(60.0 * (trip_min - t_dec), 1)
    return {"kind": "case", "id": f"AP-{sid}", "title": f"{scn['group']}: {' + '.join(faults)}",
            "mode": "upset", "hazard_id": hazard, "diagnosis_accept": [p for p in phrases if len(p.split()) >= 2],
            "initial_state": {"scenario": idx, "decision_min": t_dec}, "evidence": evidence,
            "load_bearing": load_bearing, "oracle": oracle,
            "acceptable_actions": sorted({s["action"] for s in good} | {oracle["action"]}),
            "escalation_ok": tth is None or tth > 2 * RESPONSE_S,
            "decision_time_s": round(60.0 * t_dec, 1), "time_to_hazard_s": tth,
            "notes": (f"PCA confirmed the fault at {t_dec:.1f} min; probe run: "
                      + (f"plant trip at {trip_min:.1f} min ({', '.join(sifs)})" if trip_min else "no trip")
                      + f". Candidates projected on the plant: {json.dumps(scored)}")}


def build(jobs: int) -> None:
    from acetone_plant.agents.diagnosis_llm import fault_catalogue
    from acetone_plant.agents.kg import KnowledgeGraph
    from acetone_plant.benchmark.profit_campaign import SCENARIOS
    OUT.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(jobs) as ex:
        results = list(ex.map(_case, range(len(SCENARIOS))))
    cases = [r for r in results if r.get("kind") == "case"]
    skipped = [{"id": r["id"], "reason": r["skip"]} for r in results if "skip" in r]
    man = manifest()
    man["notes"] = {"cases": len(cases), "skipped": skipped,
                    "generated_by": "acetone-plant admit build (do not edit by hand)"}
    (OUT / "manifest.yaml").write_text(yaml.safe_dump(man, sort_keys=False, allow_unicode=True), encoding="utf-8")
    _jsonl(OUT / "system_graph.jsonl", system_graph())
    _jsonl(OUT / "safety_case_graph.jsonl", safety_case(fault_catalogue(KnowledgeGraph())))
    _jsonl(OUT / "procedures_cases.jsonl", PROCEDURES + cases)
    print(f"wrote {OUT}: {len(cases)} cases, skipped {[s['id'] for s in skipped]}")


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    from . import world  # noqa: F401  registers the acetone_plant world with admitbench
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "run":  # pass through to admitbench, defaulting to this cartridge
        from admitbench.cli import main as admit_main
        rest = argv[1:]
        if "--cartridge" not in rest:
            rest = ["--cartridge", str(OUT)] + rest
        return admit_main(["run", *rest]) or 0
    ap = argparse.ArgumentParser(prog="acetone-plant admit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--jobs", type=int, default=8)
    sub.add_parser("validate")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        build(a.jobs)
    else:
        from admitbench.cartridge import load_cartridge
        c = load_cartridge(str(OUT))
        print(f"cartridge {c.manifest['id']}: {len(c.cases)} cases, {len(c.rulebook.actions())} actions, "
              f"warnings: {c.warnings or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
