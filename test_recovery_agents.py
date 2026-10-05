import asyncio
import json
import unittest

from langchain_core.language_models import FakeListChatModel

from agents.action import LLMActionAgent, normalise_plan
from agents.kg import KnowledgeGraph
from agents.recovery_graph import make_config
from recovery_agents_langgraph import run_episode

# Short pump-trip episodes: fault at 2 min, 6 min horizon (~6 s each).
EPISODE = dict(horizon_min=6.0, onset_min=2.0, seed=0)


class RecoveryAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kg = KnowledgeGraph()

    def test_kg_retrieval_links_pump_fault_to_spare_action(self):
        diagnosis = {"hypotheses": [{"fault": "reflux_pump_trip"}], "contributors": []}
        rules = {"active_alarms": ["P201_TRIP"], "saturated_loops": [], "failed_trains": ["REFLUX"]}
        ids = [n["id"] for n in self.kg.retrieve(diagnosis, rules)]
        for expected in ("fault_reflux_pump_trip", "alarm_P201_TRIP", "pump_train_REFLUX", "action_START_SPARE"):
            self.assertIn(expected, ids)

    def test_normalise_plan_strips_llm_extras(self):
        plan, cites = normalise_plan({"commands": [{"type": "START_SPARE", "target": "REFLUX", "value": None}],
                                      "reasoning": "r", "kg_evidence": ["fault_reflux_pump_trip"]})
        self.assertEqual(plan, {"commands": [{"type": "START_SPARE", "target": "REFLUX"}], "reasoning": "r"})
        self.assertEqual(cites, ["fault_reflux_pump_trip"])

    def test_llm_path_reprompts_after_invalid_plan_then_applies(self):
        fake = FakeListChatModel(responses=[
            json.dumps({"commands": [{"type": "RESET_SIF"}], "reasoning": "bad", "kg_evidence": []}),
            json.dumps({"commands": [{"type": "START_SPARE", "target": "REFLUX"}],
                        "reasoning": "fault_reflux_pump_trip → pump_train_REFLUX; action_START_SPARE acts on it",
                        "kg_evidence": ["fault_reflux_pump_trip", "action_START_SPARE"]}),
        ])
        cfg = make_config("llm_notwin")
        metrics, trace, _ = asyncio.run(run_episode("reflux_pump_trip", cfg, kg=self.kg,
                                                    action_agent=LLMActionAgent("fake", llm=fake), **EPISODE))
        events = [e["event"] for e in trace]
        self.assertIn("invalid", events)
        self.assertEqual((metrics["n_proposals"], metrics["n_actions"], metrics["kg_cited"]), (2, 1, 1))
        self.assertEqual(metrics["diag_top1"], 1)
        brief = next(e["brief"] for e in trace if e["event"] == "plan")
        self.assertEqual(brief["target_fault"], "reflux_pump_trip")
        self.assertIn({"type": "START_SPARE", "target": "REFLUX"},
                      [{k: v for k, v in l.items() if k != "why"} for l in brief["candidate_levers"]])

    def test_passive_agent_detects_but_never_acts(self):
        metrics, _, _ = asyncio.run(run_episode("reflux_pump_trip", make_config("passive"), kg=self.kg, **EPISODE))
        self.assertEqual((metrics["detected"], metrics["n_actions"]), (1, 0))

    def test_delay_clock_applies_action_later_in_sim_time(self):
        cfg = make_config("rule", clock="delay", delay_min=1.0)
        metrics, trace, _ = asyncio.run(run_episode("reflux_pump_trip", cfg, kg=self.kg, **EPISODE))
        applied = [e for e in trace if e["event"] == "applied"]
        self.assertEqual(len(applied), 1)
        self.assertAlmostEqual(applied[0]["sim_delay_min"], 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
