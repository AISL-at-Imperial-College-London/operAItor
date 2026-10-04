import unittest
from pathlib import Path

from integrated_plant import EpisodeRunner, IntegratedPlantDAE, curated_episode, nominal_episode
from jpc_langgraph import LocalKnowledge, build_framework, validate_proposal


class FrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plant = IntegratedPlantDAE()
        cls.knowledge = LocalKnowledge(Path(__file__).parent / "knowledge_graph" /
                                       "Integrated_CSTR_Distillation_Knowledge_Graph.jsonld")

    def test_rejects_invalid_or_master_actions_without_mutation(self):
        env = EpisodeRunner(self.plant, nominal_episode(1))
        before = env.observation()
        for proposal in ({"type": "SET_THROUGHPUT", "value": float("nan")},
                         {"type": "SET_THROUGHPUT", "value": 151},
                         {"type": "NO_ACTION", "input_overrides": {}},
                         {"type": "RESET_SIF"},
                         {"type": "SET_SETPOINT", "target": "UNKNOWN", "value": 1}):
            with self.subTest(proposal=proposal):
                action, errors = validate_proposal(proposal, before, env.action_catalog())
                self.assertTrue(errors)
                self.assertFalse(action.input_overrides)
                self.assertIsNone(action.throughput_target)
        self.assertEqual(before, env.observation())

    def test_spare_selection_and_clock_freeze_during_planning(self):
        env = EpisodeRunner(self.plant, curated_episode("reflux_pump_trip", 1, 0))

        def planner(observation, diagnosis, knowledge):
            self.assertEqual(env.t_h, 0)
            self.assertEqual(diagnosis["failed_trains"], ["REFLUX"])
            self.assertTrue(knowledge)
            return {"type": "START_SPARE", "target": "REFLUX"}

        result = build_framework(env, self.knowledge, planner=planner).invoke({})
        self.assertEqual(env.pumps.active_pump["REFLUX"], "P-201B")
        self.assertFalse(result["validation_errors"])
        self.assertGreater(env.t_h, 0)
        self.assertTrue(result["evaluation"]["action_result"]["accepted"])

    def test_invalid_plan_falls_back_and_terminal_graph_does_not_step(self):
        env = EpisodeRunner(self.plant, nominal_episode(0.1))
        graph = build_framework(env, self.knowledge, planner=lambda *args:
                                {"type": "SET_THROUGHPUT", "value": -1})
        result = graph.invoke({})
        self.assertTrue(result["validation_errors"])
        self.assertTrue(env.done)
        terminal_time = env.t_h
        self.assertNotIn("evaluation", graph.invoke({}))
        self.assertEqual(env.t_h, terminal_time)


if __name__ == "__main__":
    unittest.main()
