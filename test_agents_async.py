"""Live-server agents: supervisor modes, chat agent (offline + LLM tool loop), fault campaigns."""
import asyncio
import unittest

from langchain_core.messages import AIMessage

from agents.chat import ChatAgent
from agents.kg import KnowledgeGraph
from agents.recovery_graph import make_config
from agents.supervisor import AsyncSupervisor
from server.plant_service import PlantService

KG = KnowledgeGraph()


async def _service_with_pump_trip(minutes: float = 1.5) -> PlantService:
    s = PlantService()
    await s.load({"scenario": "nominal", "horizon_min": 30})
    await s.step(1.0)
    await s.handle_command({"type": "inject", "kind": "pump", "pump_id": "P-201A"})
    return s


async def _wait_for(service: PlantService, pred, steps: int = 6):
    for _ in range(steps):
        await service.step(0.5)
        await asyncio.sleep(0.2)  # let the supervisor task run its cycle
        hits = [e for e in service.agent_log if pred(e)]
        if hits:
            return hits[0]
    return None


class FakeToolModel:
    """Chat model stub: asks for one tool call, then answers."""

    def __init__(self):
        self.calls, self.history_lengths = 0, []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        self.history_lengths.append(len(messages))
        if self.calls % 2 == 1:
            return AIMessage("", tool_calls=[{"name": "trend", "args": {"variable": "reactor pressure", "minutes": 5},
                                              "id": f"call{self.calls}"}])
        return AIMessage("Reactor pressure is roughly steady.")


class SupervisorTests(unittest.TestCase):
    def test_advisory_mode_waits_for_operator_approval(self):
        async def scenario():
            s = await _service_with_pump_trip()
            sup = AsyncSupervisor(s, make_config("rule"), mode="advisory", kg=KG)
            sup.start()
            pending = await _wait_for(s, lambda e: e.get("stage") == "pending")
            self.assertIsNotNone(pending, "no pending proposal")
            self.assertIn("plan", [e.get("stage") for e in s.agent_log])  # planning agent ran first
            self.assertEqual(s.snapshot()["pumps"]["REFLUX"]["active_pump"], "P-201A")  # nothing applied yet
            result = await s.handle_command({"type": "agent_approve", "proposal_id": pending["proposal_id"]})
            self.assertTrue(result["ok"])
            self.assertEqual(s.snapshot()["pumps"]["REFLUX"]["active_pump"], "P-201B")
        asyncio.run(scenario())


class PipelineTests(unittest.TestCase):
    def test_rule_agent_escalates_when_reprompted(self):
        from agents.action import RuleActionAgent
        obs = {"throughput_target_kmol_h": 100.0, "auxiliaries": {"H2_BDV": 0.0}, "pump_trains": {},
               "controllers": {"R_PIC": {"pv": 3.0, "setpoint": 3.0}, "COL_PIC": {"pv": 1.2, "setpoint": 1.0}}}
        ctx = {"observation": obs, "previous_plan": None, "feedback": "",
               "detection": {"rules": {"failed_trains": [], "active_alarms": ["COL_P_H"], "saturated_loops": ["COL_PIC"]}},
               "diagnosis": {"hypotheses": [{"fault": "condenser_cooling_loss"}], "summary": "condenser_cooling_loss"}}
        agent = RuleActionAgent()
        first = asyncio.run(agent.propose(ctx))
        second = asyncio.run(agent.propose({**ctx, "previous_plan": first,
                                            "feedback": '{"fail_reason": "no_persistent_safe_state"}'}))
        self.assertEqual(first["commands"][0]["value"], 80.0)
        self.assertEqual(second["commands"][0]["value"], 65.0)       # deeper cut on retry
        self.assertIn("no_persistent_safe_state", second["reasoning"])

    def test_live_decision_runs_twin_and_holds_plant(self):
        async def scenario():
            s = await _service_with_pump_trip()
            holds = []
            original = s.set_agent_hold

            async def spy(on):
                holds.append(on)
                await original(on)
            s.set_agent_hold = spy
            sup = AsyncSupervisor(s, make_config("rule_twin", twin_horizon_min=6.0), mode="autonomous", kg=KG)
            sup.start()
            await s.step(1.0)
            applied = None
            for _ in range(120):  # the twin rollout runs on a worker thread; give it time
                await asyncio.sleep(0.5)
                applied = next((e for e in s.agent_log if e.get("stage") == "applied"), None)
                if applied:
                    break
            self.assertIsNotNone(applied)
            stages = [e["stage"] for e in s.agent_log if e.get("cycle") == applied["cycle"]]
            for st in ("node", "kg", "plan", "proposal", "twin", "validation", "applied"):
                self.assertIn(st, stages)
            nodes = [e["node"] for e in s.agent_log if e.get("stage") == "node" and e.get("cycle") == applied["cycle"]]
            self.assertEqual(nodes[:6], ["kg_retrieval", "planning", "action_propose",
                                         "twin_evaluate", "validate", "dispatch"])
            twin = next(e for e in s.agent_log if e.get("stage") == "twin" and e.get("cycle") == applied["cycle"])
            self.assertTrue(twin["safe_reached"])                         # starting the spare keeps the plant safe
            i = holds.index(True)                                         # held when the decision started...
            self.assertFalse(holds[i + 1])                                # ...and released when it ended
            self.assertFalse(s.held)
        asyncio.run(scenario())


class ChatTests(unittest.TestCase):
    def test_offline_chat_answers_kg_trend_and_diagnosis_questions(self):
        async def scenario():
            s = await _service_with_pump_trip()
            sup = AsyncSupervisor(s, make_config("passive"), kg=KG)
            sup.start()
            await _wait_for(s, lambda e: False, steps=3)
            chat = ChatAgent(s, KG, agent_info=sup.info)
            self.assertEqual(chat.mode, "offline")
            what = (await chat.ask("what is R_PIC?"))["reply"]
            self.assertIn("R_PIC", what)
            trend = (await chat.ask("how is the reactor pressure moving over the last 5 min?"))["reply"]
            self.assertIn("cstr_pressure_bar", trend)
            why = (await chat.ask("why is there a fault?"))["reply"]
            self.assertIn("reflux_pump_trip", why)
        asyncio.run(scenario())

    def test_llm_chat_uses_tools_and_keeps_session_memory(self):
        async def scenario():
            s = await _service_with_pump_trip()
            fake = FakeToolModel()
            chat = ChatAgent(s, KG, llm=fake)
            first = await chat.ask("How is reactor pressure doing?", session="a")
            self.assertEqual(first["tools"], ["trend"])
            self.assertIn("steady", first["reply"])
            await chat.ask("And now?", session="a")
            # second turn sees the first turn's 4 messages + new question (+ system prompt)
            self.assertGreater(fake.history_lengths[2], fake.history_lengths[0])
            await chat.ask("Fresh session", session="b")
            self.assertEqual(fake.history_lengths[4], fake.history_lengths[0])
        asyncio.run(scenario())


class CampaignTests(unittest.TestCase):
    def test_campaign_injects_clears_and_reports(self):
        async def scenario():
            s = PlantService()
            ack = await s.handle_command({"type": "campaign_start", "run": False, "tail_min": 0.5, "campaign": {
                "name": "t", "items": [{"at_min": 0.5, "fault": "reflux_pump_trip", "duration_min": 1.0}]}})
            self.assertTrue(ack["ok"])
            await s.step(1.0)
            self.assertEqual(s.campaign["items"][0]["status"], "active")
            self.assertTrue(s.snapshot()["truth"]["pump"])
            await s.step(1.5)
            self.assertEqual(s.campaign["items"][0]["status"], "cleared")
            await s.step(1.0)
            self.assertEqual(s.campaign["state"], "complete")
            report = s.campaign_report()
            self.assertEqual(report[0]["fault"], "reflux_pump_trip")
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()


class ValidateTests(unittest.TestCase):
    def test_validate_requires_restoration(self):
        from agents.recovery_graph import AgentConfig, validate_outcome
        base = {"rollout": "ok", "horizon_min": 20.0, "terminal_status": "RUNNING", "safe_reached": True,
                "time_to_safe_min": 2.0, "unsafe_fraction": 0.05,
                "final": {"active_alarms": ["COL_P_H"], "saturated_loops": [], "t2_ratio": 1.4, "spe_ratio": 0.8,
                          "safe": True, "targets": [{"name": "COL_P_H", "ok": False, "value": 1.19, "limit": "< 1.15 bar"}],
                          "new_alarms_blocking": [], "new_alarms_noted": []}}
        v = validate_outcome({**base, "restored": False, "time_to_restore_min": None}, AgentConfig())
        self.assertFalse(v["accepted"])
        self.assertTrue(v["safe_ok"])                                     # eligible as a safe mitigation
        self.assertIn("COL_P_H", v["checks"][-1]["detail"])
        ok = validate_outcome({**base, "restored": True, "time_to_restore_min": 6.0}, AgentConfig())
        self.assertTrue(ok["accepted"])
        lenient = validate_outcome({**base, "restored": False, "time_to_restore_min": None}, AgentConfig(acceptance="safe"))
        self.assertTrue(lenient["accepted"])


class AlarmValidationTests(unittest.TestCase):
    def test_targets_come_from_active_alarms_and_failed_pumps(self):
        from agents.recovery_graph import faulty_targets
        det = {"rules": {"active_alarms": ["R_L_H", "P201_TRIP"], "failed_trains": ["REFLUX"]}, "pca_persistent": False}
        t = faulty_targets(det, {"contributors": []}, None)
        self.assertEqual([(x["name"], x["kind"]) for x in t], [("R_L_H", "alarm"), ("REFLUX pump", "pump")])
        self.assertEqual((t[0]["tag"], t[0]["direction"], t[0]["limit"]), ("cstr_holdup_kmol", "HIGH", 62.0))

    def test_llm_failure_falls_back_to_rule_agent(self):
        class Broken:
            last_usage = {}

            async def propose(self, context):
                raise RuntimeError("Error code: 400 - temperature does not support 0.7")

        from recovery_agents_langgraph import run_episode
        cfg = make_config("llm_notwin")
        metrics, trace, _ = asyncio.run(run_episode("reflux_pump_trip", cfg, kg=KG, action_agent=Broken(),
                                                    horizon_min=6.0, onset_min=2.0, seed=0))
        self.assertIn("llm_error", [e["event"] for e in trace])
        self.assertEqual(metrics["n_actions"], 1)                     # the rule fallback started the spare
        prop = next(e for e in trace if e["event"] == "proposal")
        self.assertIn("rule fallback", prop["plan"]["reasoning"])
