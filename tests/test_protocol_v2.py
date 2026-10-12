"""Campaign protocol v2: event-triggered decisions, plain-language feedback, ADMIT record dedup, LLM record/replay."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from operaitor.agents import monitoring
from operaitor.agents.llm import ainvoke
from operaitor.agents.monitoring import MonitoringAgent
from operaitor.agents.recovery_graph import attempt_key, make_config, plain_verdict


def _rules(alarms=(), trains=()):
    return {"active_alarms": list(alarms), "failed_trains": list(trains), "saturated_loops": [],
            "sif_latched": [], "plant_trip": False}


class EventTriggerTests(unittest.TestCase):
    def run_seq(self, steps):
        """steps: (t, alarms); returns the times at which the agents would decide."""
        agent = MonitoringAgent(None, trigger="event", episode_clear_min=5.0)
        cur = {}

        async def rules_detector(obs):
            return {"name": "rules", "alarm": bool(cur["rules"]["active_alarms"]), "rules": cur["rules"]}
        agent.detectors = [rules_detector]
        acted = []
        with mock.patch.object(monitoring, "diagnose", lambda score, rules: {"summary": "x"}):
            for t, alarms in steps:
                cur["rules"] = _rules(alarms)
                out = asyncio.run(agent({"observation": {"time_min": t, "measurements": {}}}))
                if out["detection"]["should_act"]:
                    acted.append(t)
                    agent.mark_acted(t, "x")
        return acted

    def test_once_per_fault_safety_alarm_and_new_episode(self):
        steps = [(0, []), (1, ["DR_L_L"]), (2, ["DR_L_L"]), (6, ["DR_L_L"]), (7, ["DR_L_L", "COL_P_H"]),
                 (8, ["DR_L_L", "COL_P_H", "COL_P_HH"]), (9, []), (12, []), (14, []), (15, ["DR_L_L"])]
        # decides at the first alarm; not every 5 min, not for a further priority-2 alarm of the same
        # cascade (COL_P_H), again for a new priority-1 alarm (COL_P_HH); after 5 quiet minutes the
        # episode ends and the same alarm opens a new decision
        self.assertEqual(self.run_seq(steps), [1, 8, 15])

    def test_no_alarm_no_decision(self):
        self.assertEqual(self.run_seq([(t, []) for t in range(10)]), [])

    def test_v1_default_unchanged(self):
        with mock.patch.dict(os.environ, {"PLANT_PROTOCOL": ""}):
            c = make_config("llm_admit")
        self.assertEqual((c.trigger, c.feedback, c.admit_dedup), ("cooldown", "v1", "commands"))
        with mock.patch.dict(os.environ, {"PLANT_PROTOCOL": "v2"}):
            c = make_config("llm_admit")
        self.assertEqual((c.trigger, c.feedback, c.admit_dedup), ("event", "v2", "record"))


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        with mock.patch.dict(os.environ, {"PLANT_PROTOCOL": "v2"}):
            self.cfg = make_config("llm_admit")

    def test_admit_dedup_includes_the_record(self):
        from operaitor.agents.recovery_graph import is_repeat
        plan = {"commands": [{"type": "START_SPARE", "target": "REFLUX"}]}
        old, new = [{"cited_evidence": ["ev_a"]}], [{"cited_evidence": ["ev_b"]}]
        # rejected for the record (T0-T3): same commands with a corrected record are checked again
        tried = [attempt_key(plan, old, self.cfg, with_record=True)]
        self.assertFalse(is_repeat({"plan": plan, "records": new, "tried": tried}, self.cfg))
        self.assertTrue(is_repeat({"plan": plan, "records": old, "tried": tried}, self.cfg))
        # rejected for the commands (T4 or another validator): the commands are blocked whatever the record
        tried = [attempt_key(plan, old, self.cfg)]
        self.assertTrue(is_repeat({"plan": plan, "records": new, "tried": tried}, self.cfg))
        v1 = make_config("llm_admit")
        self.assertEqual(attempt_key(plan, [{"a": 1}], v1, True), attempt_key(plan, [{"a": 2}], v1, True))

    def test_plain_verdicts(self):
        base = {"attempts": 1, "evaluation": {"fail_reason": "x"}, "errors": None}
        malformed = plain_verdict({**base, "twin": {"rollout": "invalid_plan"},
                                   "plan": {"commands": [], "reasoning": "[LLM call failed: JSONDecodeError]"}}, self.cfg)
        self.assertTrue(malformed.startswith("REJECTED: attempt 1 of 4."))
        self.assertIn("not a valid JSON object", malformed)
        empty = plain_verdict({**base, "twin": {"rollout": "invalid_plan"}, "plan": {"commands": [], "reasoning": "r"}}, self.cfg)
        self.assertIn("no commands", empty)
        admit = plain_verdict({**base, "plan": {"commands": [1]}, "twin": {"rollout": "admit", "records": [
            {"admissible": False, "violations": [{"code": "AAS-T3-STEP-MISSING", "message": "verify_pump_failed missing"}]}]}}, self.cfg)
        self.assertIn("AAS-T3-STEP-MISSING", admit)
        self.assertIn("resubmit them with the record fixed", admit)
        t4 = plain_verdict({**base, "plan": {"commands": [1]}, "twin": {"rollout": "admit", "records": [
            {"admissible": False, "violations": [{"code": "AAS-T4-ENVELOPE", "message": "crosses the safe envelope"}]}]}}, self.cfg)
        self.assertIn("choose different commands", t4)
        self.assertNotIn("record fixed", t4)
        dup = plain_verdict({**base, "plan": {"commands": [1]}, "twin": {"rollout": "duplicate"}}, self.cfg)
        self.assertIn("repeats a proposal", dup)

    def test_failure_types(self):
        from operaitor.agents.recovery_graph import failure_type
        bad = {"commands": [], "reasoning": "[LLM call failed: JSONDecodeError]"}
        self.assertEqual(failure_type({"plan": bad}, {"rollout": "invalid_plan"}), "malformed_reply")
        self.assertEqual(failure_type({"plan": {"commands": []}}, {"rollout": "invalid_plan"}), "no_commands")
        self.assertEqual(failure_type({"plan": {"commands": [1]}}, {"rollout": "invalid_plan"}), "not_executable")
        self.assertEqual(failure_type({"plan": {"commands": [1]}}, {"rollout": "duplicate"}), "repeat")
        self.assertEqual(failure_type({"plan": {"commands": [1]}}, {"rollout": "judged"}), "judge_rejected")
        self.assertEqual(failure_type({"plan": {"commands": [1]}}, {"rollout": "admit"}), "admit_rejected")
        self.assertEqual(failure_type({"plan": {"commands": [1]}}, {"rollout": "ok", "terminal_status": "SAFE_TRIP"}), "twin_trip")
        self.assertEqual(failure_type({"plan": {"commands": [1]}}, {"rollout": "ok", "terminal_status": "RUNNING"}), "twin_not_restored")


class ReplayTests(unittest.TestCase):
    def test_record_then_replay_without_calling_the_model(self):
        from langchain_core.messages import AIMessage

        class FakeLLM:
            model, temperature, calls = "fake", 0.0, 0

            async def ainvoke(self, messages):
                FakeLLM.calls += 1
                return AIMessage(content='{"commands": []}', usage_metadata={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8})

        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"PLANT_LLM_CACHE": d}):
            msgs = [("system", "s"), ("human", "h")]
            first = asyncio.run(ainvoke(FakeLLM(), msgs))
            second = asyncio.run(ainvoke(FakeLLM(), msgs))
            other = asyncio.run(ainvoke(FakeLLM(), [("system", "s"), ("human", "different")]))
        self.assertEqual(FakeLLM.calls, 2)                 # second identical request was replayed
        self.assertEqual(first.content, second.content)
        self.assertTrue(second.response_metadata.get("replayed"))
        self.assertEqual(second.usage_metadata["total_tokens"], 8)
        self.assertEqual(other.content, first.content)


if __name__ == "__main__":
    unittest.main()
