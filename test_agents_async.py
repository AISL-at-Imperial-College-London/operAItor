"""Live-server agents: supervisor modes, chat agent (offline + LLM tool loop), fault campaigns."""
import asyncio
import json
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
        # condenser limited: first cut boilup + reflux + throughput together, then a milder version
        self.assertEqual(first["commands"][0], {"type": "SET_THROUGHPUT", "value": 50.0})
        self.assertIn({"type": "SET_SETPOINT", "target": "BOT_TIC", "value": 345.0}, first["commands"])
        self.assertEqual(second["commands"][0]["value"], 65.0)       # next candidate on retry
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
            # default 30-min look-ahead: PCA needs ~20 min after the pump swap to return to normal
            sup = AsyncSupervisor(s, make_config("rule_twin"), mode="autonomous", kg=KG)
            sup.start()
            await s.step(4.0)  # PCA (the only trigger) needs 3 persistent samples
            applied = None
            for _ in range(400):  # the twin rollout runs on a worker thread; give it time
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


class AlarmRegressionTests(unittest.TestCase):
    """Failures seen in a live gpt-5-nano campaign (2026-10-05)."""

    def test_kg_ids_are_mapped_to_catalog_names(self):
        from agents.action import normalise_plan
        plan, _ = normalise_plan({"commands": [{"type": "SET_SETPOINT", "target": "controller_R_PIC", "value": 3.2}],
                                  "reasoning": "r"})
        self.assertEqual(plan["commands"][0]["target"], "R_PIC")

    def test_unknown_names_get_actionable_feedback(self):
        from agents.action import explain_plan_errors
        catalog = {"controllers": {"R_PIC": {"sp_min": 2.5, "sp_max": 3.4}}, "pump_trains": {"REFLUX": {}},
                   "auxiliaries": {"H2_BDV": {}}}
        errs = explain_plan_errors({"commands": [{"type": "SET_SETPOINT", "target": "XYZ", "value": 1}]}, catalog)
        self.assertIn("R_PIC", errs[0])

    def test_no_pump_trip_hypothesis_when_pump_is_running(self):
        from fdd import diagnose
        d = diagnose(None, {"failed_trains": [], "active_alarms": ["DIST_X_L"], "saturated_loops": [], "sif_latched": []})
        self.assertNotIn("reflux_pump_trip", [h["fault"] for h in d["hypotheses"]])

    def test_fallback_excludes_actions_that_raise_safety_alarms(self):
        from agents.recovery_graph import AgentConfig, validate_outcome
        m = {"rollout": "ok", "horizon_min": 30.0, "terminal_status": "RUNNING", "safe_reached": True,
             "time_to_safe_min": 2.0, "unsafe_fraction": 0.0, "restored": False, "time_to_restore_min": None,
             "final": {"safe": True, "targets": [], "new_alarms_blocking": ["R_L_H"], "new_alarms_noted": []}}
        self.assertFalse(validate_outcome(m, AgentConfig())["safe_ok"])

    def test_repeated_proposal_is_rejected_without_rollout(self):
        from langchain_core.language_models import FakeListChatModel
        from agents.action import LLMActionAgent
        from recovery_agents_langgraph import run_episode
        same = json.dumps({"commands": [{"type": "SET_THROUGHPUT", "value": 90.0}], "reasoning": "x", "kg_evidence": []})
        fake = FakeListChatModel(responses=[same] * 4)
        cfg = make_config("llm", twin_horizon_min=3.0)
        _, trace, _ = asyncio.run(run_episode("condenser_cooling_loss", cfg, kg=KG, action_agent=LLMActionAgent("fake", llm=fake),
                                              horizon_min=12.0, onset_min=1.0, seed=0))
        first = min(e["cycle"] for e in trace if e["event"] == "proposal")
        twins = [e for e in trace if e["event"] == "twin" and e["cycle"] == first
                 and not e.get("counterfactual")]  # the do-nothing baseline is a separate rollout
        dups = [e for e in trace if e["event"] == "twin_skipped" and e["cycle"] == first and "identical" in e["reason"]]
        self.assertLessEqual(len(twins), 1)
        self.assertGreaterEqual(len(dups), 1)


class KgMapTests(unittest.TestCase):
    def test_kg_names_resolve_to_command_targets(self):
        vm = KG.vmap
        cases = {("SET_SETPOINT", "controller_R_PIC"): "R_PIC", ("SET_SETPOINT", "PCV-101"): "R_PIC",
                 ("SET_SETPOINT", "obs_param_cstr_pressure_bar"): "R_PIC", ("SET_SETPOINT", "CV-201"): "COL_PIC",
                 ("SET_SETPOINT", "drum_holdup_kmol"): "DR_LIC", ("START_SPARE", "P-201A"): "REFLUX",
                 ("START_SPARE", "obs_param_pump_REFLUX_failed"): "REFLUX",
                 ("OPERATE_AUXILIARY", "BDV-101"): "H2_BDV", ("OPERATE_AUXILIARY", "E-102"): "EMERGENCY_COOLING"}
        for (cmd, name), target in cases.items():
            self.assertEqual(vm.command_target(cmd, name), target, (cmd, name))

    def test_every_simulator_variable_has_a_kg_node(self):
        from integrated_plant import IntegratedPlantDAE
        from agents.kg_map import VariableMap
        plant = IntegratedPlantDAE()
        tags = [k for k in plant.measurements(plant.nominal_state(plant.default_inputs())) if not k.startswith("pump_")]
        missing = VariableMap(KG, measurement_tags=tags).coverage()["simulator_without_kg_node"]
        self.assertEqual({k: v for k, v in missing.items() if v}, {})

    def test_evidence_carries_simulator_names(self):
        node = KG.compact("controller_DR_LIC")
        self.assertEqual(node["sim"]["command"], {"type": "SET_SETPOINT", "target": "DR_LIC"})
        self.assertFalse(KG.vmap.lookup("exo_param_catalyst_activity")["commandable"])


class TargetTests(unittest.TestCase):
    def test_pca_confirmed_fault_targets_are_t2_and_spe(self):
        from agents.recovery_graph import _target_status, faulty_targets
        from fdd import PCAMonitor
        pca = PCAMonitor.load("fdd_results/pca_model.json")
        det = {"rules": {"active_alarms": [], "failed_trains": []}, "pca_persistent": True}
        t = faulty_targets(det, {"contributors": []}, pca)
        self.assertEqual([x["name"] for x in t], ["PCA T²", "PCA SPE"])

        class S:  # minimal score stand-in
            t2, spe = pca.t2_limit * 3, pca.spe_limit * 0.5
        self.assertFalse(_target_status(t[0], {}, S)["ok"])
        self.assertTrue(_target_status(t[1], {}, S)["ok"])
        det_no_pca = {"rules": {"active_alarms": ["R_L_H"], "failed_trains": []}, "pca_persistent": False}
        self.assertEqual([x["kind"] for x in faulty_targets(det_no_pca, {}, pca)], ["alarm"])

    def test_mixed_no_action_is_dropped(self):
        from agents.action import normalise_plan
        plan, _ = normalise_plan({"commands": [{"type": "SET_THROUGHPUT", "value": 90}, {"type": "NO_ACTION"}],
                                  "reasoning": "r"})
        self.assertEqual(plan["commands"], [{"type": "SET_THROUGHPUT", "value": 90}])


class QualityGateTests(unittest.TestCase):
    def _obs(self, alarms):
        env = __import__("recovery_agents_langgraph").build_episode("reflux_pump_trip", 5, 30, 0)
        obs = env.observation()
        obs["alarm_states"] = {a: ("ACTIVE_UNACK" if a in alarms else "NORMAL") for a in obs["alarm_states"]}
        return obs

    def test_quality_alarm_alone_does_not_trigger(self):
        from agents.monitoring import MonitoringAgent
        mon = MonitoringAgent(None)
        out = asyncio.run(mon({"observation": self._obs(["BTM_X_H"])}))
        self.assertFalse(out["detection"]["fault"])
        self.assertEqual(out["detection"]["advisory"], ["BTM_X_H"])

    def test_safety_alarm_still_triggers(self):
        from agents.monitoring import MonitoringAgent
        out = asyncio.run(MonitoringAgent(None)({"observation": self._obs(["R_L_H"])}))
        self.assertTrue(out["detection"]["fault"])
