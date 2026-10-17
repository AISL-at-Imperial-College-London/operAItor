"""Human-operator test: a person, not the agents, answers the benchmark faults.

The console's operator mode "human" switches the agents off (no monitoring, proposals or
actions). A human test then replays one benchmark scenario (operaitor/benchmark/profit_campaign.py)
on the live plant: the same faults at the same times, lifted at the same times, the same shift
operator (SIF reset every 5 min after the last lift) and the same score (margin + change in
liquid inventory − plant-stop penalty over the scenario window). With ``blind`` on, the fault
identity is hidden from the console until the test ends.

At the end the result is compared with the benchmark runs of the same scenario (no agents and
each agent configuration, protocol v2) and a report is written to the campaign directory.
"""
from __future__ import annotations

from datetime import datetime
import json
import random
from typing import TYPE_CHECKING

from operaitor.benchmark import reference
from operaitor.benchmark.economics import INVENTORY_TAGS, PRICES, hourly_margin, inventory_value
from operaitor.benchmark.profit_campaign import BY_ID, SCENARIOS

if TYPE_CHECKING:
    from .plant_service import PlantService

OPERATOR_MODES = ("agents", "human")
STOP_STATUSES = ("SAFE_TRIP", "DAMAGE", "MODEL_LIMIT", "NUMERICAL_FAILURE")
SIF_RESET_EVERY_MIN = 5.0


def schedule(sid: str) -> tuple[dict, float]:
    """Onset/lift per fault and the scoring window, as in the benchmark (benchmark/reference.py)."""
    return reference.schedule(sid)


def benchmark_rows(sid: str) -> list[dict]:
    """The benchmark's results for this scenario: no agents, then every model x configuration (v2)."""
    return reference.rows(sid)


class HumanTest:
    """One scored run of a benchmark scenario on a PlantService; ``tick`` is called after every plant step.

    ``who="human"``: a person operates (human test). ``who="agents"``: the session's agents operate, one
    scenario of a pipeline benchmark (server/pipeline_bench.py); then nothing is broadcast or written per
    scenario, the benchmark reports."""

    def __init__(self, service: "PlantService", sid: str, blind: bool, random_pick: bool, who: str = "human"):
        from operaitor.server.campaigns import FAULT_LIBRARY
        self.service, self.sid, self.blind, self.random_pick = service, sid, blind, random_pick
        self.who = who
        scn = BY_ID[sid]
        self.sched, self.window = schedule(sid)
        self.injections = []
        for i, f in enumerate(scn["faults"]):
            params = {k: v for k, v in FAULT_LIBRARY[f["fault"]].items() if k != "vary"}
            params.update({k: v for k, v in f.items() if k not in ("fault", "at_min")})
            lift = self.sched[i]["lift_min"]
            params["duration_min"] = 0 if lift is None else lift - self.sched[i]["onset_min"]
            self.injections.append({"fault": f["fault"], "onset_min": self.sched[i]["onset_min"],
                                    "lift_min": lift, "params": params, "done": False})
        lifts = [j["lift_min"] for j in self.injections if j["lift_min"] is not None]
        self.last_lift = max(lifts, default=None)
        self.state = "running"
        self.margin = 0.0
        self.inv0: float | None = None
        self.inv = 0.0
        self.stop_min: float | None = None
        self.stop_kind: str | None = None
        self.actions: list[dict] = []
        self.started = datetime.now().isoformat(timespec="seconds")
        self.report_path: str | None = None
        self.result: dict | None = None

    # ------------------------------------------------------------ live
    def record_action(self, t: float, kind: str, summary: str, ok: bool) -> None:
        if self.state == "running":
            self.actions.append({"t": round(t, 2), "kind": kind, "summary": summary, "ok": ok})

    async def tick(self) -> None:
        """Score the step just taken, then inject what is due and ask for a SIF reset (applied next step)."""
        if self.state != "running":
            return
        svc, env = self.service, self.service.env
        t = 60.0 * env.t_h
        if t > 1e-9:  # after a plant step (not the freshly loaded state)
            dt_h = svc.TIMING.decision_interval_min / 60.0
            summary = env.plant.operating_summary(env.y, env.last_actual)
            summary["feed_kmol_h"] = env.last_actual.fresh_ipa_feed
            self.margin += hourly_margin(summary)["margin"] * dt_h
            true = env.plant.measurements(env.y)
            inv = inventory_value({k: float(true[k]) for k in INVENTORY_TAGS})
            if self.inv0 is None:
                self.inv0 = inv
            self.inv = inv - self.inv0
            status = env.outcomes.state.status.value
            if self.stop_min is None and (env.last_safety_result.plant_trip_latched or status in STOP_STATUSES):
                self.stop_min = round(t, 2)
                self.stop_kind = "SIF" if env.last_safety_result.plant_trip_latched else status
        if t >= self.window - 1e-9 or env.done:
            await self.finish("complete")
            return
        for j in self.injections:
            if not j["done"] and t + 1e-9 >= j["onset_min"]:
                await svc._inject(j["params"], quiet=self.blind)
                j["done"] = True
        since = None if self.last_lift is None else t - self.last_lift
        if (since is not None and since > -1e-9
                and abs(since / SIF_RESET_EVERY_MIN - round(since / SIF_RESET_EVERY_MIN)) < 1e-6
                and env.last_safety_result.latched_causes):
            svc._pending_reset_sif = True  # shift operator, as in the benchmark
        if self.who == "human":
            await svc.broadcast({"type": "human_test", **self.status()})

    @property
    def profit(self) -> float:
        return self.margin + self.inv - (PRICES.trip_penalty_usd if self.stop_min is not None else 0.0)

    async def finish(self, reason: str) -> None:
        if self.state != "running":
            return
        svc = self.service
        self.state = reason
        svc.set_running(False)
        bench = benchmark_rows(self.sid)
        # the benchmark scores the whole window, so only a complete run compares like for like
        passive = next((r["profit"] for r in bench if r["config"] == "passive"), None) if reason == "complete" else None
        self.result = {"profit": round(self.profit, 2), "margin": round(self.margin, 2), "inventory": round(self.inv, 2),
                       "penalty": -PRICES.trip_penalty_usd if self.stop_min is not None else 0.0,
                       "saving_vs_no_agents": None if passive is None else round(self.profit - passive, 2),
                       "benchmark": bench, "complete": reason == "complete"}
        if self.who != "human":
            return
        if svc.campaign_dir is not None:
            svc.campaign_dir.mkdir(parents=True, exist_ok=True)
            path = svc.campaign_dir / f"human_{self.sid}_{datetime.now():%Y%m%d_%H%M%S}.json"
            from .plant_service import jsonable
            path.write_text(json.dumps(jsonable({"human_test": self.status(reveal=True), "messages": list(svc.messages)}),
                                       indent=1), encoding="utf-8")
            self.report_path = str(path)
        await svc.broadcast({"type": "human_test", **self.status()})
        await svc.notify(f"[human test] {self.sid} {reason}: profit ${self.profit:,.0f}, "
                         f"{'plant stopped at %.1f min' % self.stop_min if self.stop_min is not None else 'no plant stop'}, "
                         f"{len(self.actions)} operator actions", "warn")

    # ------------------------------------------------------------ views
    def status(self, reveal: bool = False) -> dict:
        svc = self.service
        t = 60.0 * svc.env.t_h if svc.env else 0.0
        hidden = self.blind and self.state == "running" and not reveal
        injected = sum(j["done"] for j in self.injections)
        out = {"scenario": "hidden" if hidden and self.random_pick else self.sid,
               "blind": self.blind, "state": self.state, "started": self.started,
               "time_min": round(t, 2), "window_min": self.window,
               "profit": round(self.profit, 2), "margin": round(self.margin, 2), "inventory": round(self.inv, 2),
               "stop_min": self.stop_min, "stop_kind": self.stop_kind,
               "faults_injected": injected, "faults_total": len(self.injections),
               "actions": self.actions[-200:], "n_actions": len(self.actions),
               "report_path": self.report_path, "result": self.result}
        if not hidden:
            scn = BY_ID[self.sid]
            out["faults"] = [{"fault": j["fault"], "onset_min": j["onset_min"], "lift_min": j["lift_min"],
                              "injected": j["done"]} for j in self.injections]
            out["group"], out["interaction"] = scn["group"], scn.get("interaction")
        return {"human_test": out}


def scenario_list() -> list[dict]:
    return [{"id": s["id"], "group": s["group"], "faults": [f["fault"] for f in s["faults"]]} for s in SCENARIOS]


def pick(sid: str, rng: random.Random | None = None) -> tuple[str, bool]:
    if sid in (None, "", "random"):
        return (rng or random.Random()).choice(SCENARIOS)["id"], True
    if sid not in BY_ID:
        raise ValueError(f"unknown scenario {sid!r}")
    return sid, False

