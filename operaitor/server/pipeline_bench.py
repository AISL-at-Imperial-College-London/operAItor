"""Benchmark the running pipeline from the console: the benchmark scenarios, scored like the published benchmark.

Each scenario is replayed on the session's plant exactly as in the benchmark and the human test
(server/human_test.py): the same faults at the same times, lifted at the same times, the same shift
operator (SIF reset every 5 min after the last lift) and the same score (margin + change in liquid
inventory - plant-stop penalty over the scenario window). The session's agents operate, autonomously,
with whatever pipeline runs, and the plant runs flat out (it is held while the agents decide, as the
benchmark's frozen clock does).

The results are compared, scenario by scenario, with the published runs (benchmark/reference.py): no
agents, and the pipeline's baseline configuration with every model that was benchmarked.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
from typing import TYPE_CHECKING

from operaitor.benchmark.profit_campaign import BY_ID, SCENARIOS
from operaitor.benchmark.reference import CONFIG_OF_PRESET

from .human_test import HumanTest, benchmark_rows

if TYPE_CHECKING:
    from .sessions import Session

# A quick look before the full 20: a single fault, a coupled pair and three coupled faults.
QUICK = ["S01", "C01", "T03"]


def scenario_ids(choice) -> list[str]:
    if choice in (None, "", "all"):
        return [s["id"] for s in SCENARIOS]
    if choice == "quick":
        return list(QUICK)
    ids = [choice] if isinstance(choice, str) else list(choice)
    bad = [i for i in ids if i not in BY_ID]
    if bad or not ids:
        raise ValueError(f"unknown scenario(s) {bad or ids}; choose all, quick or ids like {QUICK[0]}")
    return ids


class PipelineBenchmark:
    def __init__(self, session: "Session", ids: list[str]):
        self.session = session
        self.ids = ids
        self.pipeline = session.pipeline_info()
        self.model = session.model if session.chat is not None and session.chat.mode == "llm" else None
        self.config = CONFIG_OF_PRESET.get(self.pipeline["base"], "")
        self.state = "running"
        self.started = datetime.now().isoformat(timespec="seconds")
        self.results: list[dict] = []
        self.current: HumanTest | None = None
        self.report_path: str | None = None
        self.task: asyncio.Task | None = None

    # ------------------------------------------------------------ run
    def start(self) -> None:
        self.task = asyncio.create_task(self.run(), name="pipeline-benchmark", context=self.session.llm_ctx)

    async def run(self) -> None:
        svc = self.session.service
        pace = (svc.tick_delay_s, svc.pace_every, svc.pace_sleep_s)
        svc.tick_delay_s, svc.pace_every = 0.0, 0  # flat out; the agents hold the plant while they decide
        sup = self.session.supervisor
        mode = sup.mode if sup is not None else None
        if sup is not None:
            sup.mode = "autonomous"
        try:
            for sid in self.ids:
                if self.state != "running":
                    break
                test = HumanTest(svc, sid, blind=False, random_pick=False, who="agents")
                await svc.load({"scenario": "nominal", "horizon_min": test.window + 1.0, "stop_on_trip": True})
                svc.human_test, self.current = test, test
                await self.broadcast()
                svc.set_running(True)
                last = 0.0
                while test.state == "running":
                    await asyncio.sleep(0.25)
                    if self.state != "running":
                        await test.finish("stopped early")
                        break
                    if test.status()["human_test"]["time_min"] - last >= 5:
                        last = test.status()["human_test"]["time_min"]
                        await self.broadcast()
                self.results.append(self.row(test))
                svc.human_test, self.current = None, None
                await self.broadcast()
            if self.state == "running":
                self.state = "complete"
        except Exception as exc:  # report, never leave the session half-configured
            self.state = f"failed: {type(exc).__name__}: {exc}"[:300]
        finally:
            svc.set_running(False)
            svc.human_test, self.current = None, None
            svc.tick_delay_s, svc.pace_every, svc.pace_sleep_s = pace
            if sup is not None and mode is not None and sup is self.session.supervisor:
                sup.mode = mode
            self.write_report()
            await self.broadcast()
            done = len([r for r in self.results if r["complete"]])
            await svc.notify(f"[pipeline benchmark] {self.state}: {done} of {len(self.ids)} scenarios scored", "warn")

    def stop(self) -> None:
        if self.state == "running":
            self.state = "stopped early"

    # ------------------------------------------------------------ results
    def row(self, test: HumanTest) -> dict:
        scn = BY_ID[test.sid]
        res = test.result or {}
        refs = benchmark_rows(test.sid)
        passive = next((r for r in refs if r["config"] == "passive"), None)
        same = {r["model"]: {"profit": r["profit"], "stopped": r["stopped"]} for r in refs
                if r["config"] == self.config and r.get("model")}
        return {"id": test.sid, "group": scn["group"], "faults": [f["fault"] for f in scn["faults"]],
                "complete": bool(res.get("complete")), "profit": round(test.profit, 2),
                "stopped": test.stop_min is not None, "stop_min": test.stop_min, "stop_kind": test.stop_kind,
                "no_agents": None if passive is None else {"profit": passive["profit"], "stopped": passive["stopped"]},
                "baseline": same}

    def summary(self) -> dict:
        done = [r for r in self.results if r["complete"]]
        out = {"scenarios": len(done), "stops": sum(r["stopped"] for r in done),
               "profit": round(sum(r["profit"] for r in done), 2), "reference": {}}
        if done and all(r["no_agents"] for r in done):
            out["reference"]["No agents"] = {"stops": sum(r["no_agents"]["stopped"] for r in done),
                                             "profit": round(sum(r["no_agents"]["profit"] for r in done), 2)}
        models = sorted({m for r in done for m in r["baseline"]})
        for m in models:
            if all(m in r["baseline"] for r in done):
                out["reference"][m] = {"stops": sum(r["baseline"][m]["stopped"] for r in done),
                                       "profit": round(sum(r["baseline"][m]["profit"] for r in done), 2)}
        return out

    def status(self) -> dict:
        cur = self.current.status()["human_test"] if self.current is not None else None
        return {"state": self.state, "started": self.started, "pipeline": self.pipeline, "model": self.model,
                "config": self.config, "total": len(self.ids), "done": len(self.results),
                "current": None if cur is None else {"id": cur["scenario"], "time_min": cur["time_min"],
                                                     "window_min": cur["window_min"], "profit": cur["profit"],
                                                     "stop_min": cur["stop_min"]},
                "results": self.results, "summary": self.summary(), "report_path": self.report_path}

    async def broadcast(self) -> None:
        await self.session.service.broadcast({"type": "pipeline_bench", "bench": self.status()})

    def write_report(self) -> None:
        folder = self.session.service.campaign_dir
        if folder is None or not self.results:
            return
        folder.mkdir(parents=True, exist_ok=True)
        tag = self.pipeline.get("fingerprint") or self.pipeline.get("base")
        path = folder / f"pipeline_bench_{tag}_{datetime.now():%Y%m%d_%H%M%S}.json"
        path.write_text(json.dumps({"pipeline_benchmark": {**self.status(), "report_path": str(path)}}, indent=1,
                                   default=str), encoding="utf-8")
        self.report_path = str(path)
