"""Custom agent pipelines (agents/pipeline.py): validation, settings, prompts, knowledge graphs, and the
guarantee that the reference pipelines (the published baselines) stay exactly as they are."""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
from unittest import mock

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage  # noqa: E402

from operaitor.agents import pipeline as pl  # noqa: E402
from operaitor.agents.kg import KG_PATH, KnowledgeGraph  # noqa: E402
from operaitor.agents.recovery_graph import make_config  # noqa: E402

REF_KG_TEXT = KG_PATH.read_text(encoding="utf-8")


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


# Fingerprints of the reference pipelines as published. A change here changes the baseline every custom
# pipeline is compared with: update these only on purpose (and say so in the changelog).
REFERENCE_CONFIGS = {"llm_admit": "fee9d93026f9f26b", "llm_admit_nosim": "a00323053ac61887", "llm": "86689d8c7916c02a",
                     "llm_judge": "ec964e6d589907af", "llm_lucky": "cd40f92cfd7bbddc"}
REFERENCE_PROMPTS = {"action": "bddfab98cc8f805a", "planning": "85a5de10112db78c", "judge": "996ec10694c6012b",
                     "diagnosis": "9b6198037472d0df"}


@pytest.fixture(autouse=True)
def _reference_protocol():
    with mock.patch.dict(os.environ):
        os.environ.pop("PLANT_PROTOCOL", None)
        os.environ.pop("PLANT_ACTION_CONTEXT", None)
        yield


class FakeLLM:
    """Records the messages it is sent; answers with a fixed JSON reply."""
    model, temperature = "fake", 0.0

    def __init__(self, reply: dict):
        self.reply, self.sent = reply, []

    async def ainvoke(self, messages):
        self.sent.append(messages)
        return AIMessage(content=json.dumps(self.reply),
                         usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})


# ----------------------------------------------------------------------------- the baseline is locked

def test_reference_pipelines_are_unchanged():
    for preset, fp in REFERENCE_CONFIGS.items():
        assert _h(json.dumps(dataclasses.asdict(make_config(preset)), sort_keys=True)) == fp, preset
    from operaitor.agents.action import action_system_prompt
    from operaitor.agents.diagnosis_llm import DIAGNOSIS_SYSTEM_PROMPT
    from operaitor.agents.judge import JUDGE_SYSTEM_PROMPT
    from operaitor.agents.planning import PLANNER_PROMPT
    texts = {"action": action_system_prompt("base"), "planning": PLANNER_PROMPT, "judge": JUDGE_SYSTEM_PROMPT,
             "diagnosis": DIAGNOSIS_SYSTEM_PROMPT}
    assert {k: _h(v) for k, v in texts.items()} == REFERENCE_PROMPTS


def test_a_reference_spec_runs_exactly_the_reference_config():
    for preset in pl.REFERENCE_PIPELINES:
        spec = pl.reference_spec(preset)
        assert spec.reference and pl.config_for(spec) == make_config(preset)
    assert pl.resolve_prompt("REF", None) == "REF" and pl.resolve_prompt("REF", {"mode": "replace", "text": ""}) == "REF"


def test_reference_settings_describe_each_preset():
    assert pl.reference_settings("llm")["validator"] == "twin"
    assert pl.reference_settings("llm_admit")["validator"] == "admit"
    assert pl.reference_settings("llm_admit_nosim")["validator"] == "admit_nosim"
    assert pl.reference_settings("llm_judge")["validator"] == "judge"
    assert pl.reference_settings("llm_lucky")["validator"] == "none"
    assert set(pl.reference_prompts()) == set(pl.PROMPT_AGENTS)


# ----------------------------------------------------------------------------- custom pipelines

def test_parse_rejects_what_it_cannot_run():
    spec, errors = pl.parse_pipeline({"base": "nope", "settings": {"reprompt_max": 9, "validator": "magic", "x": 1},
                                      "prompts": {"bad": {}, "action": {"mode": "shout", "text": "hi"}}, "kg": 5})
    assert spec is None
    text = " ".join(errors)
    for why in ("base:", "reprompt_max: must be between", "validator: choose", "unknown setting 'x'",
                "unknown agent 'bad'", "prompts.action", "kg: give"):
        assert why in text, why
    assert pl.parse_pipeline([])[1] == ["a pipeline is a JSON object"]
    assert "at most" in pl.parse_pipeline({"base": "llm", "prompts": {"action": {"text": "x" * 20_001}}})[1][0]
    assert pl.parse_pipeline({"base": "llm", "settings": {"use_kg": "yes"}})[1] == ["settings.use_kg: must be true or false"]


def test_settings_map_onto_the_agent_config():
    expect = {"twin": (True, False, False, True), "judge": (False, True, False, True),
              "admit": (False, False, True, True), "admit_nosim": (False, False, True, False),
              "none": (False, False, False, True)}
    for validator, (twin, judge, admit, admit_sim) in expect.items():
        spec, errors = pl.parse_pipeline({"base": "llm_admit_nosim", "settings": {"validator": validator}})
        cfg = pl.config_for(spec)
        assert not errors and (cfg.twin, cfg.judge, cfg.admit) == (twin, judge, admit), validator
        if admit:
            assert cfg.admit_sim == admit_sim
    spec, _ = pl.parse_pipeline({"name": "  mine ", "base": "llm", "settings": {"reprompt_max": 1, "planner": "llm",
                                                                                "twin_horizon_min": 20, "use_kg": False}})
    cfg = pl.config_for(spec, llm_model="m")
    assert (spec.name, cfg.name, cfg.reprompt_max, cfg.planner, cfg.twin_horizon_min, cfg.use_kg, cfg.llm_model) == \
        ("mine", "llm", 1, "llm", 20.0, False, "m")
    assert not spec.reference and len(spec.fingerprint()) == 10
    assert spec.fingerprint() != pl.parse_pipeline({"base": "llm", "settings": {"reprompt_max": 2}})[0].fingerprint()


def test_prompts_reach_the_model():
    ctx = {"diagnosis": {"hypotheses": [], "contributors": []}, "detection": {"rules": {}}, "knowledge": [], "snapshot": {}}
    from operaitor.agents.planning import PLANNER_PROMPT, LLMPlanner
    fake = FakeLLM({"target_fault": "x", "objective": "y", "candidate_levers": [], "constraints": []})
    asyncio.run(LLMPlanner(llm=fake, prompt={"mode": "append", "text": "Always list a constraint."}).plan(ctx))
    system = fake.sent[0][0][1]
    assert system.startswith(PLANNER_PROMPT.rstrip()) and system.rstrip().endswith("Always list a constraint.")
    fake = FakeLLM({"target_fault": "x"})
    asyncio.run(LLMPlanner(llm=fake, prompt={"mode": "replace", "text": "Only this."}).plan(ctx))
    assert fake.sent[0][0][1] == "Only this."

    from operaitor.agents.diagnosis_llm import LLMDiagnoser
    fake = FakeLLM({"hypotheses": [], "summary": "s"})
    out = asyncio.run(LLMDiagnoser(llm=fake, prompt={"mode": "replace", "text": "Diagnose briefly."})
                      .diagnose({"time_min": 1.0, "rules": {}}, {"hypotheses": []}))
    assert out["ok"] and fake.sent[0][0][1] == "Diagnose briefly."


def test_the_action_prompt_keeps_the_admit_record_format():
    """A replaced action prompt cannot drop the action-record instructions ADMIT checks."""
    from operaitor.agents import action as A
    captured = {}

    async def fake_ainvoke(llm, messages):
        captured["system"] = messages[0][1]
        return AIMessage(content='{"commands": []}', usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})

    agent = A.LLMActionAgent(llm=FakeLLM({}), prompt={"mode": "replace", "text": "Mine."})
    with mock.patch.object(A, "ainvoke", fake_ainvoke), mock.patch.object(A, "slim_observation", lambda o: {}), \
            mock.patch.object(A, "_command_targets", lambda: {}), mock.patch.object(A, "_loop_alarm_limits", lambda c: {}):
        ctx = {"catalog": {"throughput_bounds_kmol_h": [0, 1], "auxiliaries": {}, "controllers": {}, "pump_trains": {}},
               "observation": {}, "detection": {"pca": None, "rules": {}, "detectors": {}}, "diagnosis": {}}
        asyncio.run(agent.propose(ctx))
        assert captured["system"] == "Mine."
        asyncio.run(agent.propose({**ctx, "admit": {"evidence": []}}))
        assert captured["system"].startswith("Mine.") and A.ADMIT_ADDENDUM in captured["system"]


# ----------------------------------------------------------------------------- knowledge graphs

def test_the_reference_graph_loads_from_text_with_a_clean_report():
    ref = KnowledgeGraph()
    kg = pl.load_kg(REF_KG_TEXT)
    assert set(kg.nodes) == set(ref.nodes)
    report = pl.kg_report(kg, ref)
    assert report["warnings"] == [] and report["faults_covered"] == report["reference_faults"] > 0
    assert report["families"]["operator actions"] > 0 and report["mapped_to_plant"] > 0


def test_a_partial_graph_loads_with_warnings():
    ref = KnowledgeGraph()
    data = json.loads(REF_KG_TEXT)
    for g in data:  # drop the fault modes and operator actions
        g["@graph"] = [n for n in g.get("@graph", []) if not n["@id"].split("#")[-1].startswith(("fault_", "action_"))]
    report = pl.kg_report(pl.load_kg(json.dumps(data)), ref)
    assert report["faults_covered"] == 0 and len(report["warnings"]) >= 1
    assert "reference fault modes" in report["warnings"][0]


@pytest.mark.parametrize("text, why", [("{nope", "not valid JSON"), ("[1, 2]", "expected JSON-LD"),
                                       ('[{"x": 1}]', "no \"@graph\""), ('[{"@graph": []}]', "no nodes"),
                                       ('[{"@graph": [{"label": "no id"}]}]', "malformed")])
def test_bad_graphs_are_refused_with_a_reason(text, why):
    with pytest.raises(ValueError, match=why):
        pl.load_kg(text)
