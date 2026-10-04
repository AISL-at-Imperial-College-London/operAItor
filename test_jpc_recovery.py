import unittest
from pathlib import Path

import numpy as np

from integrated_plant import EpisodeRunner, IntegratedPlantDAE, curated_episode, nominal_episode
from integrated_plant.actions import SupervisoryAction
from jpc_langgraph import LocalKnowledge
from jpc_recovery_langgraph import RecoveryConfig, build_recovery_framework, compile_plan, rollout_validate, make_llm_proposer


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plant = IntegratedPlantDAE()
        cls.kg = LocalKnowledge(Path(__file__).parent / "knowledge_graph" /
                               "Integrated_CSTR_Distillation_Knowledge_Graph.jsonld")
        cls.config = RecoveryConfig(grace_min=0, safe_hold_min=0.1, deadline_min=1, reprompt_max=1)

    def pump_env(self):
        return EpisodeRunner(self.plant, curated_episode("reflux_pump_trip", 0.5, 0))

    def test_clone_acceptance_does_not_mutate_live_state(self):
        env = self.pump_env()
        before, y = env.observation(), env.y.copy()
        result = rollout_validate(env, SupervisoryAction(start_spares=("REFLUX",)), self.config)
        self.assertTrue(result["accepted"], result["fail_reason"])
        self.assertEqual(before, env.observation())
        np.testing.assert_array_equal(y, env.y)
        self.assertEqual(env.records, [])
        self.assertEqual(env.pumps.active_pump["REFLUX"], "P-201A")
        self.assertTrue(result["trajectory"])

    def test_no_action_with_unavailable_pump_fails_rollout(self):
        result = rollout_validate(self.pump_env(), SupervisoryAction.no_action(), self.config)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["fail_reason"], "no_persistent_safe_state")

    def test_retry_uses_feedback_and_applies_only_accepted_plan(self):
        env = self.pump_env()
        attempts = []
        def proposer(context):
            self.assertEqual(env.t_h, 0)
            attempts.append(context)
            if context["attempt"] == 0:
                return {"commands": [{"type": "SET_THROUGHPUT", "value": -1}], "reasoning": "invalid test"}
            self.assertIn("invalid_proposal", context["feedback"])
            self.assertEqual(context["previous_plan"]["commands"][0]["value"], -1)
            return {"commands": [{"type": "START_SPARE", "target": "REFLUX"}], "reasoning": "healthy spare"}
        events = []
        graph = build_recovery_framework(env, self.kg, proposer, self.config, events.append)
        graph.invoke({})
        self.assertEqual(len(attempts), 2)
        self.assertEqual(env.pumps.active_pump["REFLUX"], "P-201B")
        evaluations = [e for e in events if e["event"] == "evaluation"]
        self.assertEqual([e["accepted"] for e in evaluations], [False, True])
        self.assertEqual(len([e for e in events if e["event"] == "action_apply"]), 1)

    def test_retry_exhaustion_leaves_live_plant_frozen(self):
        env = self.pump_env()
        proposer = lambda context: {"commands": [{"type": "NO_ACTION"}], "reasoning": "test rejection"}
        result = build_recovery_framework(env, self.kg, proposer, self.config).invoke({})
        self.assertEqual(result["abort_reason"], "reprompt_limit")
        self.assertEqual(env.t_h, 0)
        self.assertEqual(env.pumps.active_pump["REFLUX"], "P-201A")

    def test_normal_episode_does_not_call_proposer(self):
        env = EpisodeRunner(self.plant, nominal_episode(0.2))
        def proposer(context):
            self.fail("Nominal trajectory should not call proposer")
        state = build_recovery_framework(env, self.kg, proposer, self.config).invoke({})
        self.assertEqual(state["abort_reason"], "COMPLETED")

    def test_compound_plan_is_validated_before_mutation(self):
        env = self.pump_env()
        plan = {"commands": [{"type": "START_SPARE", "target": "REFLUX"},
                             {"type": "SET_THROUGHPUT", "value": 200}], "reasoning": "test"}
        action, errors = compile_plan(plan, env.observation(), env.action_catalog())
        self.assertTrue(errors)
        self.assertEqual(action.start_spares, ())
        self.assertEqual(env.pumps.active_pump["REFLUX"], "P-201A")

    def test_llm_adapter_receives_kg_and_retry_feedback(self):
        captured = []
        class Response:
            content = '{"commands": [{"type": "NO_ACTION"}], "reasoning": "test"}'
        class Model:
            def invoke(self, messages):
                captured.extend(messages)
                return Response()
        plan = make_llm_proposer(Model())({"knowledge": [{"@id": "KG_NODE"}], "feedback": "slow_recovery"})
        self.assertEqual(plan["commands"][0]["type"], "NO_ACTION")
        self.assertIn("KG_NODE", captured[1][1])
        self.assertIn("slow_recovery", captured[1][1])


if __name__ == "__main__":
    unittest.main()
