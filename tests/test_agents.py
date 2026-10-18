"""Live-server agents: supervisor modes, chat agent (offline + LLM tool loop)."""
import asyncio
import json
import unittest

from langchain_core.messages import AIMessage

from operaitor.agents.chat import ChatAgent
from operaitor.agents.kg import KnowledgeGraph
from operaitor.agents.recovery_graph import make_config
from operaitor.agents.supervisor import AsyncSupervisor
from operaitor.server.plant_service import PlantService

KG = KnowledgeGraph()
SPARE_PLAN = {"commands": [{"type": "START_SPARE", "target": "REFLUX"}], "reasoning": "reflux duty pump failed",
              "kg_evidence": []}


def fake_llm_agent(*plans):
    """LLM action agent with scripted replies (no API calls); there is no rule-based agent."""
    from langchain_core.language_models import FakeListChatModel
    from operaitor.agents.action import LLMActionAgent
    return LLMActionAgent("fake", llm=FakeListChatModel(responses=[json.dumps(p) for p in plans]))


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
            sup = AsyncSupervisor(s, make_config("llm", twin=False), mode="advisory", kg=KG,
                                  action_agent=fake_llm_agent(SPARE_PLAN))
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
            sup = AsyncSupervisor(s, make_config("llm"), mode="autonomous", kg=KG, action_agent=fake_llm_agent(SPARE_PLAN))
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
            # (a reflux trip drains the trays: COL_TRAY_L, a new P1 alarm, may start a second decision)
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


if __name__ == "__main__":
    unittest.main()


class ValidateTests(unittest.TestCase):
    def test_validate_requires_restoration(self):
        from operaitor.agents.recovery_graph import AgentConfig, validate_outcome
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
        from operaitor.agents.recovery_graph import faulty_targets
        det = {"rules": {"active_alarms": ["R_L_H", "P201_TRIP"], "failed_trains": ["REFLUX"]}, "pca_persistent": False}
        t = faulty_targets(det, {"contributors": []}, None)
        self.assertEqual([(x["name"], x["kind"]) for x in t], [("R_L_H", "alarm"), ("REFLUX pump", "pump")])
        self.assertEqual((t[0]["tag"], t[0]["direction"], t[0]["limit"]), ("cstr_holdup_kmol", "HIGH", 62.0))

    def test_llm_failure_means_no_action(self):
        """There is no rule fallback: a failed LLM call is logged and nothing is applied."""
        class Broken:
            last_usage = {}

            async def propose(self, context):
                raise RuntimeError("Error code: 400 - temperature does not support 0.7")

        from operaitor.agents.episodes import run_episode
        cfg = make_config("llm_notwin")
        metrics, trace, _ = asyncio.run(run_episode("reflux_pump_trip", cfg, kg=KG, action_agent=Broken(),
                                                    horizon_min=6.0, onset_min=2.0, seed=0))
        self.assertIn("llm_error", [e["event"] for e in trace])
        self.assertEqual(metrics["n_actions"], 0)
        prop = next(e for e in trace if e["event"] == "proposal")
        self.assertIn("LLM call failed", prop["plan"]["reasoning"])


class AlarmRegressionTests(unittest.TestCase):
    """Failures seen in a live gpt-5-nano campaign (2026-10-05)."""

    def test_kg_ids_are_mapped_to_catalog_names(self):
        from operaitor.agents.action import normalise_plan
        plan, _ = normalise_plan({"commands": [{"type": "SET_SETPOINT", "target": "controller_R_PIC", "value": 3.2}],
                                  "reasoning": "r"})
        self.assertEqual(plan["commands"][0]["target"], "R_PIC")

    def test_unknown_names_get_actionable_feedback(self):
        from operaitor.agents.action import explain_plan_errors
        catalog = {"controllers": {"R_PIC": {"sp_min": 2.5, "sp_max": 3.4}}, "pump_trains": {"REFLUX": {}},
                   "auxiliaries": {"H2_BDV": {}}}
        errs = explain_plan_errors({"commands": [{"type": "SET_SETPOINT", "target": "XYZ", "value": 1}]}, catalog)
        self.assertIn("R_PIC", errs[0])

    def test_no_pump_trip_hypothesis_when_pump_is_running(self):
        from operaitor.fdd import diagnose
        d = diagnose(None, {"failed_trains": [], "active_alarms": ["DIST_X_L"], "saturated_loops": [], "sif_latched": []})
        self.assertNotIn("reflux_pump_trip", [h["fault"] for h in d["hypotheses"]])

    def test_fallback_excludes_actions_that_raise_safety_alarms(self):
        from operaitor.agents.recovery_graph import AgentConfig, validate_outcome
        m = {"rollout": "ok", "horizon_min": 30.0, "terminal_status": "RUNNING", "safe_reached": True,
             "time_to_safe_min": 2.0, "unsafe_fraction": 0.0, "restored": False, "time_to_restore_min": None,
             "final": {"safe": True, "targets": [], "new_alarms_blocking": ["R_L_H"], "new_alarms_noted": []}}
        self.assertFalse(validate_outcome(m, AgentConfig())["safe_ok"])

    def test_repeated_proposal_is_rejected_without_rollout(self):
        from langchain_core.language_models import FakeListChatModel
        from operaitor.agents.action import LLMActionAgent
        from operaitor.agents.episodes import run_episode
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
        from operaitor.plant import IntegratedPlantDAE
        from operaitor.agents.kg_map import VariableMap
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
        from operaitor.agents.recovery_graph import _target_status, faulty_targets
        from operaitor.fdd import DEFAULT_MODEL_PATH, PCAMonitor
        pca = PCAMonitor.load(DEFAULT_MODEL_PATH)
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
        from operaitor.agents.action import normalise_plan
        plan, _ = normalise_plan({"commands": [{"type": "SET_THROUGHPUT", "value": 90}, {"type": "NO_ACTION"}],
                                  "reasoning": "r"})
        self.assertEqual(plan["commands"], [{"type": "SET_THROUGHPUT", "value": 90}])


class QualityGateTests(unittest.TestCase):
    def _obs(self, alarms):
        env = __import__("operaitor.agents.episodes", fromlist=["x"]).build_episode("reflux_pump_trip", 5, 30, 0)
        obs = env.observation()
        obs["alarm_states"] = {a: ("ACTIVE_UNACK" if a in alarms else "NORMAL") for a in obs["alarm_states"]}
        return obs

    def test_quality_alarm_alone_does_not_trigger(self):
        from operaitor.agents.monitoring import MonitoringAgent
        mon = MonitoringAgent(None)
        out = asyncio.run(mon({"observation": self._obs(["BTM_X_H"])}))
        self.assertFalse(out["detection"]["fault"])
        self.assertEqual(out["detection"]["advisory"], ["BTM_X_H"])

    def test_safety_alarm_still_triggers(self):
        from operaitor.agents.monitoring import MonitoringAgent
        out = asyncio.run(MonitoringAgent(None)({"observation": self._obs(["R_L_H"])}))
        self.assertTrue(out["detection"]["fault"])


class JudgeTests(unittest.TestCase):
    """LLM-as-judge validator and the three LLM presets (fake LLM, no API calls)."""

    def _ctx(self):
        obs = {"time_min": 12.0, "throughput_target_kmol_h": 100.0, "measurements": {}, "controllers": {},
               "auxiliaries": {}, "pump_trains": {}, "sif_latched": [], "alarm_states": {}}
        return {"observation": obs, "detection": {"pca": None, "rules": {}}, "diagnosis": {"summary": "x"},
                "brief": None, "knowledge": []}

    def test_judge_verdict_is_parsed(self):
        from langchain_core.language_models import FakeListChatModel
        from operaitor.agents.judge import LLMJudge
        fake = FakeListChatModel(responses=[json.dumps({"accept": False, "confidence": 0.7, "reason": "wrong loop",
                                                        "risks": ["DR_L_LL"], "suggestion": "start the spare"})])
        v = asyncio.run(LLMJudge("fake", llm=fake).judge(self._ctx(), {"commands": [{"type": "NO_ACTION"}], "reasoning": "r"}))
        self.assertFalse(v["accept"])
        self.assertEqual(v["suggestion"], "start the spare")

    def test_failed_judge_call_rejects(self):
        from operaitor.agents.judge import LLMJudge

        class Broken:
            async def ainvoke(self, _):
                raise TimeoutError("no answer")
        v = asyncio.run(LLMJudge("fake", llm=Broken()).judge(self._ctx(), {"commands": [], "reasoning": ""}))
        self.assertFalse(v["accept"])
        self.assertIn("judge unavailable", v["reason"])

    def test_llm_presets(self):
        lucky, judge, twin = make_config("llm_lucky"), make_config("llm_judge"), make_config("llm")
        self.assertEqual((lucky.proposer, lucky.twin, lucky.judge), ("llm", False, False))
        self.assertEqual((judge.proposer, judge.twin, judge.judge), ("llm", False, True))
        self.assertEqual((twin.proposer, twin.twin, twin.judge), ("llm", True, False))


class LLMDiagnosisTests(unittest.TestCase):
    """LLM diagnosis agent (fake LLM, no API calls)."""

    def test_catalogue_covers_kg_and_library_faults(self):
        from operaitor.agents.diagnosis_llm import fault_catalogue
        names = {c["fault"] for c in fault_catalogue(KG)}
        for f in ("condenser_cooling_loss", "reflux_drum_leak", "bottoms_pump_trip", "feed_pump_trip",
                  "reactor_pressure_sensor_low_bias"):
            self.assertIn(f, names)

    def test_verdict_is_restricted_to_the_catalogue(self):
        from langchain_core.language_models import FakeListChatModel
        from operaitor.agents.diagnosis_llm import LLMDiagnoser, as_diagnosis
        reply = {"summary": "drum draining", "most_likely": "fault_reflux_drum_leak", "fault_class": "process",
                 "hypotheses": [{"fault": "reflux_drum_leak", "probability": 0.7, "evidence": ["SPE on drum level"],
                                 "against": []},
                                {"fault": "made_up_fault", "probability": 0.2, "evidence": [], "against": []}],
                 "t2_spe_reading": "SPE high", "operator_checks": ["inspect V-201"]}
        d = LLMDiagnoser("fake", llm=FakeListChatModel(responses=[json.dumps(reply)]), kg=KG)
        det = {"time_min": 20.0, "pca": {"t2": 90, "t2_limit": 68, "spe": 9, "spe_limit": 5, "top_contributors": []},
               "rules": {"active_alarms": ["DR_L_LL"], "failed_trains": [], "saturated_loops": [], "sif_latched": []}}
        heur = {"hypotheses": [{"fault": "DR_LIC_out_of_range", "score": 2.0, "evidence": ["x"]}], "summary": "DR_LIC_out_of_range"}
        v = asyncio.run(d.diagnose(det, heur))
        self.assertTrue(v["ok"])
        self.assertEqual([h["fault"] for h in v["hypotheses"]], ["reflux_drum_leak"])   # unknown name dropped
        diag = as_diagnosis(v, heur)
        self.assertEqual(diag["summary"], "reflux_drum_leak")
        self.assertEqual(diag["heuristic"]["summary"], "DR_LIC_out_of_range")

    def test_failed_llm_keeps_the_heuristic(self):
        from operaitor.agents.diagnosis_llm import LLMDiagnoser, as_diagnosis

        class Broken:
            async def ainvoke(self, _):
                raise TimeoutError("no answer")
        heur = {"hypotheses": [{"fault": "reflux_pump_trip", "score": 10, "evidence": []}], "summary": "reflux_pump_trip"}
        v = asyncio.run(LLMDiagnoser("fake", llm=Broken(), kg=KG).diagnose({"pca": None, "rules": {}}, heur))
        self.assertFalse(v["ok"])
        self.assertEqual(as_diagnosis(v, heur)["summary"], "reflux_pump_trip")
