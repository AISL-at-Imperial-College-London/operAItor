"""Phase 3 of custom pipelines: your own wiring (BUILD_GRAPH) and benchmarking a pipeline from the console,
scored like the published benchmark and compared with it (benchmark/reference.py)."""
from __future__ import annotations

import asyncio
import json
from unittest import mock

import pytest

pytest.importorskip("langgraph")

from operaitor import plugins as P  # noqa: E402
from operaitor.agents import pipeline as pl  # noqa: E402
from operaitor.benchmark import reference  # noqa: E402
from operaitor.server import pipeline_bench as PB  # noqa: E402


# ----------------------------------------------------------------------------- the shipped reference

@pytest.mark.skipif(not reference.repo_available(), reason="needs the repository's benchmark_results/")
def test_the_shipped_reference_is_up_to_date():
    """Regenerate with `python -m operaitor.benchmark.reference` after a new benchmark run."""
    shipped = json.loads(reference.SHIPPED.read_text(encoding="utf-8"))
    assert shipped == json.loads(json.dumps(reference.build())), "operaitor/data/benchmark_reference.json is stale"


def test_without_the_repository_the_shipped_copy_is_used():
    sid = "S01"
    with mock.patch.object(reference, "repo_available", return_value=False):
        sched, window = reference.schedule(sid)
        rows = reference.rows(sid)
    assert window >= 60 and set(sched) == {0} and isinstance(next(iter(sched)), int)
    assert rows[0]["config"] == "passive" and {r["model"] for r in rows[1:]} >= {"gpt-5-nano"}
    if reference.repo_available():
        assert (sched, window) == reference.schedule(sid) and rows == reference.rows(sid)


def test_scenario_choices():
    assert len(PB.scenario_ids("all")) == 20 and PB.scenario_ids("quick") == PB.QUICK and len(PB.QUICK) == 3
    assert PB.scenario_ids(["S02", "S01"]) == ["S02", "S01"] and PB.scenario_ids("S03") == ["S03"]
    for bad in (["S01", "nope"], []):
        with pytest.raises(ValueError):
            PB.scenario_ids(bad)


# ----------------------------------------------------------------------------- benchmark runs

def _session():
    from operaitor.server.sessions import Session, Settings
    from operaitor.agents.kg import KnowledgeGraph
    from operaitor.fdd import DEFAULT_MODEL_PATH, PCAMonitor
    settings = Settings(hosted=True, llm_mode="off", preset="", model="gpt-5-nano", chat_model="", diagnoser="",
                        max_sessions=2, idle_min=15, max_age_min=180, models=("gpt-5-nano",))
    s = Session("t", settings, KnowledgeGraph(), PCAMonitor.load(DEFAULT_MODEL_PATH))
    s.service.campaign_dir = None  # no report files from tests (one test writes to a temporary folder)
    return s


def test_a_scenario_scores_like_the_published_benchmark():
    """With the agents only monitoring (no LLM), the console benchmark reproduces the benchmark's no-agents run."""
    async def run():
        s = _session()
        await s.start()
        bench = PB.PipelineBenchmark(s, ["S01"])
        bench.start()
        await asyncio.wait_for(bench.task, 300)
        await s.close()
        return bench
    bench = asyncio.run(run())
    assert bench.state == "complete"
    row = bench.results[0]
    assert row["complete"] and row["id"] == "S01" and row["group"] and row["faults"]
    ref = row["no_agents"]
    assert row["stopped"] == ref["stopped"]
    assert row["profit"] == pytest.approx(ref["profit"], rel=0.02, abs=500), (row, ref)
    s = bench.summary()
    assert s["scenarios"] == 1 and s["reference"]["No agents"]["stops"] == int(ref["stopped"])
    assert row["baseline"] and "gpt-5-nano" in s["reference"]  # the base pipeline's runs, per model
    assert bench.status()["config"] == reference.CONFIG_OF_PRESET[bench.pipeline["base"]]


def test_a_benchmark_can_be_stopped_and_restores_the_session(tmp_path, monkeypatch):
    # The plant is fast (S01 trips 17 sim-min in): pace it, so the first scenario is still running when Stop comes.
    monkeypatch.setenv(PB.PipelineBenchmark.TICK_DELAY_ENV, "0.1")
    async def run():
        s = _session()
        s.service.campaign_dir = tmp_path
        await s.start()
        speed = (s.service.tick_delay_s, s.service.pace_every, s.service.pace_sleep_s)
        bench = PB.PipelineBenchmark(s, PB.scenario_ids("quick"))
        bench.start()
        while bench.current is None:          # the first scenario has started
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.3)
        bench.stop()
        await asyncio.wait_for(bench.task, 120)
        after = (s.service.tick_delay_s, s.service.pace_every, s.service.pace_sleep_s)
        held = s.service.human_test
        await s.close()
        return bench, speed, after, held
    bench, speed, after, held = asyncio.run(run())
    assert bench.state == "stopped early" and len(bench.results) == 1 and not bench.results[0]["complete"]
    assert bench.summary()["scenarios"] == 0           # an incomplete scenario is not scored
    assert after == speed and held is None
    report = json.loads(open(bench.report_path, encoding="utf-8").read())["pipeline_benchmark"]
    assert report["state"] == "stopped early" and report["pipeline"]["base"] and report["results"][0]["id"] == "S01"


# ----------------------------------------------------------------------------- your own wiring

def test_the_wiring_example_and_broken_wiring():
    plugin = P.load_plugin(P.TEMPLATE + P.GRAPH_TEMPLATE)
    assert plugin.summary()["graph"] is True and P.check_graph(plugin) == ["no_throughput_changes"]
    cases = {
        "def BUILD_GRAPH(g, nodes, routes):\n    raise KeyError('x')\n": "BUILD_GRAPH: line 2: KeyError",
        "BUILD_GRAPH = 3": "must be a function",
        ("from operaitor.plugins import START\ndef BUILD_GRAPH(g, nodes, routes):\n    g.add_node('monitoring', nodes['monitoring'])\n"
         "    g.add_edge(START, 'monitoring')\n    g.add_edge('monitoring', 'nowhere')\n"): "unknown node `nowhere`",
        "def BUILD_GRAPH(g, nodes, routes):\n    g.add_node('x', len)\n    g.add_node('x', len)\n": "added twice",
    }
    for code, why in cases.items():
        with pytest.raises(P.PluginError, match=why):
            P.check_graph(P.load_plugin(code))
    assert P.check_graph(P.load_plugin(P.TEMPLATE)) == []  # no wiring: the reference one


def test_a_custom_step_sends_a_proposal_back():
    from test_agents import KG, _service_with_pump_trip, fake_llm_agent
    from operaitor.agents.supervisor import AsyncSupervisor
    throughput = {"commands": [{"type": "SET_THROUGHPUT", "value": 10.0}], "reasoning": "cut the feed"}
    spare = {"commands": [{"type": "START_SPARE", "target": "REFLUX"}], "reasoning": "start the spare"}
    code = P.GRAPH_TEMPLATE + """
def watch(observation):
    return observation["measurements"].get("pump_REFLUX_failed", 0) > 0.5
DETECTORS = [watch]
"""

    async def scenario():
        s = await _service_with_pump_trip()
        spec, _ = pl.parse_pipeline({"base": "llm_lucky", "code": code, "settings": {"reprompt_max": 2}})
        plugin = P.load_plugin(spec.code)
        sup = AsyncSupervisor(s, pl.config_for(spec), mode="autonomous", kg=KG, action_agent=fake_llm_agent(throughput, spare),
                              plugin=plugin)
        assert sup.info()["plugin"]["graph"] is True and sup.info()["plugin"]["steps"] == ["no_throughput_changes"]
        sup.start()
        await s.step(0.5)
        applied = None
        for _ in range(200):
            await asyncio.sleep(0.25)
            applied = next((e for e in s.agent_log if e.get("stage") == "applied"), None)
            if applied:
                break
        await sup.stop()
        return s, applied
    s, applied = asyncio.run(scenario())
    assert applied is not None, [e.get("stage") for e in s.agent_log]
    steps = [e for e in s.agent_log if e.get("stage") == "custom_step"]
    assert [e["ok"] for e in steps] == [True, True] and steps[0]["node"] == "no_throughput_changes"
    nodes = [e["node"] for e in s.agent_log if e.get("stage") == "node" and e.get("cycle") == applied["cycle"]]
    assert nodes[:4] == ["kg_retrieval", "planning", "action_propose", "no_throughput_changes"]
    rejected = next(e for e in s.agent_log if e.get("stage") == "validation" and not e.get("accepted"))
    assert rejected["fail_reason"] == "plan executable" and "does not change the throughput" in rejected["checks"][0]["detail"]
    assert any(e.get("stage") == "reprompt" for e in s.agent_log)
    assert s.snapshot()["pumps"]["REFLUX"]["active_pump"] == "P-201B"
