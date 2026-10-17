"""Python agents in custom pipelines (operaitor/plugins.py): loading, safety wrappers, where code may run,
and a live decision with a Python detector and validator."""
from __future__ import annotations

import asyncio
import json
import os
from unittest import mock

import pytest

pytest.importorskip("langgraph")

from operaitor import plugins as P  # noqa: E402
from operaitor.agents import pipeline as pl  # noqa: E402


# ----------------------------------------------------------------------------- loading

def test_the_example_loads():
    plugin = P.load_plugin(P.TEMPLATE)
    assert plugin.summary() == {"detectors": ["reactor_pressure_rise"], "validator": "simulate_then_check", "graph": False}


@pytest.mark.parametrize("code, why", [
    ("def f(:\n  pass", "line 1: invalid syntax"),
    ("x = 1\nraise RuntimeError('boom')", "line 2: RuntimeError: boom"),
    ("DETECTORS = 5", "DETECTORS must be a list"),
    ("class D:\n  async def detect(self, o): return True\nDETECTORS = [D]", "put an instance in the list"),
    ("DETECTORS = [3]", "needs a detect(observation) method"),
    ("class D:\n  name = 'pca'\n  def detect(self, o): return True\nDETECTORS = [D()]", "'pca' is taken"),
    ("def a(o): return 1\ndef b(o): return 1\nb.__name__ = 'a'\nDETECTORS = [a, b]", "'a' is taken"),
    ("class V:\n  def validate(self, p): return True\nVALIDATOR = V", "use an instance"),
    ("VALIDATOR = 3", "needs a validate(proposal) method"),
    ("x = 1", "define DETECTORS"),
    ("BUILD_GRAPH = 1", "must be a function"),
])
def test_unusable_code_is_refused_with_the_reason(code, why):
    with pytest.raises(P.PluginError, match=why.replace("(", r"\(").replace(")", r"\)").replace(".", r"\.")):
        P.load_plugin(code)


def test_too_long_code_is_refused():
    with pytest.raises(P.PluginError, match="longer than"):
        P.load_plugin("#" * (P.MAX_CODE_CHARS + 1))


def test_detectors_never_raise_into_the_agents():
    code = """
import time
def ok(o): return {"alarm": True, "detail": "x" * 1000}
def plain(o): return False
async def broken(o): return 1 / 0
def wrong(o): return "yes"
def slow(o): time.sleep(2); return True
DETECTORS = [ok, plain, broken, wrong, slow]
"""
    plugin = P.load_plugin(code)
    async def run_all():
        return await asyncio.gather(*(d({}) for d in plugin.detectors))
    with mock.patch.object(P, "DETECTOR_TIMEOUT_S", 0.3):
        out = {r["name"]: r for r in asyncio.run(run_all())}
    assert plugin.detector_names == ["ok", "plain", "broken", "wrong", "slow"]
    assert out["ok"]["alarm"] is True and len(out["ok"]["detail"]) == 300
    assert out["plain"]["alarm"] is False and "error" not in out["plain"]
    assert out["broken"]["alarm"] is False and "line 5: ZeroDivisionError" in out["broken"]["error"]
    assert "return True/False" in out["wrong"]["error"]
    assert "longer than" in out["slow"]["error"]


def test_a_failing_validator_rejects():
    proposal = P.Proposal([{"type": "NO_ACTION"}], "", 1, {}, {}, {}, None, [], {})
    run = lambda v: asyncio.run(P.run_validator(v, "v", proposal))  # noqa: E731
    assert run(P.load_plugin("class V:\n  def validate(self, p):\n    return p.missing\nVALIDATOR = V()").validator)["reason"] \
        .startswith("v failed: line 3: AttributeError")
    assert run(lambda p: True) == {"accept": True, "reason": "", "checks": []}
    assert run(lambda p: {"ok": 1})["accept"] is False
    out = run(lambda p: {"accept": True, "reason": "fine", "checks": [{"check": "c", "ok": 1, "detail": 2}, "junk"]})
    assert out == {"accept": True, "reason": "fine", "checks": [{"check": "c", "ok": True, "detail": "2"}]}


# ----------------------------------------------------------------------------- where code may run

@pytest.mark.parametrize("hosted, flag, host, allowed", [
    (True, "1", "127.0.0.1", False),       # a public server never runs visitors' code
    (False, "", "127.0.0.1", True),        # your own computer
    (False, "", "localhost", True),
    (False, "0", "127.0.0.1", False),      # switched off
    (False, "", "0.0.0.0", False),         # on the network: not by default...
    (False, "1", "0.0.0.0", True),         # ...only when the owner says so
])
def test_where_python_agents_may_run(hosted, flag, host, allowed):
    with mock.patch.dict(os.environ, {"PLANT_ALLOW_PLUGINS": flag, "PLANT_BIND_HOST": host}):
        ok, why = P.plugins_allowed(hosted)
    assert ok is allowed and (ok or why)


def test_pipelines_carry_code_and_the_custom_validator_needs_it():
    spec, errors = pl.parse_pipeline({"base": "llm", "code": P.TEMPLATE, "settings": {"validator": "custom"}})
    assert not errors and spec.code and not spec.reference and spec.public()["python"] is True
    cfg = pl.config_for(spec)
    assert not (cfg.twin or cfg.judge or cfg.admit)
    other = pl.parse_pipeline({"base": "llm", "code": P.TEMPLATE + "\n# changed"})[0]
    assert other.fingerprint() != spec.fingerprint()
    assert "needs Python code" in pl.parse_pipeline({"base": "llm", "settings": {"validator": "custom"}})[1][0]
    assert "code: give" in pl.parse_pipeline({"base": "llm", "code": 5})[1][0]
    assert pl.parse_pipeline({"base": "llm", "code": "   "})[0].reference  # empty code is no code


# ----------------------------------------------------------------------------- in a live decision

PLUGIN = """
from operaitor.plugins import Proposal

class ReflexPump:
    name = "reflux_pump_watch"
    def detect(self, observation):
        failed = observation["measurements"].get("pump_REFLUX_failed", 0) > 0.5
        return {"alarm": failed, "detail": "reflux duty pump failed" if failed else "ok"}

class SecondTime:
    name = "second_time"
    async def validate(self, proposal: Proposal) -> dict:
        if proposal.attempt < 2:
            return {"accept": False, "reason": "try once more", "checks": [{"check": "second look", "ok": False}]}
        result = await proposal.simulate(minutes=5)
        checks = proposal.standard_checks(result)
        return {"accept": True, "reason": f"simulated: {result['terminal_status']}", "checks": checks[:1]}

DETECTORS = [ReflexPump()]
VALIDATOR = SecondTime()
"""


def test_a_python_detector_triggers_and_a_python_validator_decides():
    from test_agents import KG, _service_with_pump_trip, fake_llm_agent
    from operaitor.agents.recovery_graph import make_config
    from operaitor.agents.supervisor import AsyncSupervisor
    first = {"commands": [{"type": "SET_SETPOINT", "target": "DR_LIC", "value": 25.0}], "reasoning": "first try"}
    second = {"commands": [{"type": "START_SPARE", "target": "REFLUX"}], "reasoning": "start the spare"}

    async def scenario():
        s = await _service_with_pump_trip()
        spec, _ = pl.parse_pipeline({"base": "llm", "code": PLUGIN, "settings": {"validator": "custom", "reprompt_max": 2}})
        plugin = P.load_plugin(spec.code)
        sup = AsyncSupervisor(s, pl.config_for(spec), mode="autonomous", kg=KG, action_agent=fake_llm_agent(first, second),
                              plugin=plugin, custom_validator=True)
        assert sup.info()["validator"] == "custom" and sup.info()["plugin"]["validator"] == "second_time"
        sup.start()
        await s.step(0.5)  # the Python detector sees the failed pump at once (PCA would need 3 samples)
        applied = None
        for _ in range(480):
            await asyncio.sleep(0.25)
            applied = next((e for e in s.agent_log if e.get("stage") == "applied"), None)
            if applied:
                break
        assert applied is not None, [(e.get("stage"), e.get("node"), e.get("accept"), e.get("reason"), e.get("errors"), e.get("fail_reason")) for e in s.agent_log]
        verdicts = [e for e in s.agent_log if e.get("stage") == "custom_validator"]
        assert [v["accept"] for v in verdicts] == [False, True] and verdicts[0]["name"] == "second_time"
        assert verdicts[1]["reason"].startswith("simulated: ") and verdicts[1]["checks"]
        assert any(e.get("stage") == "reprompt" for e in s.agent_log)
        rejected = next(e for e in s.agent_log if e.get("stage") == "validation" and not e.get("accepted"))
        assert rejected["failure_type"] == "custom_rejected" and rejected["checks"][0]["check"] == "second look"
        assert s.snapshot()["pumps"]["REFLUX"]["active_pump"] == "P-201B"
        await sup.stop()
    asyncio.run(scenario())


def test_monitoring_reports_python_detectors():
    from operaitor.agents.monitoring import MonitoringAgent
    plugin = P.load_plugin(PLUGIN)
    mon = MonitoringAgent(None, extra_detectors=plugin.detectors)
    obs = {"time_min": 1.0, "measurements": {"pump_REFLUX_failed": 1.0}, "pump_trains": {}, "controllers": {},
           "alarm_states": {}, "sif_latched": [], "plant_trip_latched": False}
    with mock.patch("operaitor.agents.monitoring.rule_checks", return_value={
            "active_alarms": [], "failed_trains": [], "saturated_loops": [], "sif_latched": [], "plant_trip": False,
            "abnormal": False}), mock.patch("operaitor.agents.monitoring.diagnose", return_value={"hypotheses": [], "summary": ""}):
        out = asyncio.run(mon({"observation": obs}))
    det = out["detection"]
    assert det["fault"] and det["should_act"]
    assert det["custom_detectors"] == {"reflux_pump_watch": {"alarm": True, "detail": "reflux duty pump failed", "error": None}}
    assert json.dumps(det["custom_detectors"])  # sent to the console as is
