"""Physics / specification consistency checks against docs/plant-model.md,
plus the agents' alarm acknowledgement.

    pytest tests/test_physics_consistency.py -q
"""
from __future__ import annotations

import asyncio
import unittest

from acetone_plant.plant import EpisodeRunner, IntegratedPlantDAE, nominal_episode
from acetone_plant.plant.alarms import AlarmManager, AlarmState, default_alarm_definitions
from acetone_plant.plant.safety import SafetyConfig, SafetySystem
from acetone_plant.plant.scenario import DISTURBANCE_STEPS, disturbance_step

PLANT = IntegratedPlantDAE()

# docs/plant-model.md "SIF / interrupt layer" table
README_SIF = {"R_T_HH": 348.0, "R_P_HH": 4.00, "R_P_HHH": 4.35, "R_L_HH": 72.0, "R_L_LL": 20.0,
              "RB_L_LL": 12.08, "RB_L_HH": 51.76, "DR_L_LL": 10.59, "DR_L_HH": 45.37, "COL_P_HH": 1.35, "COL_L_LL": 0.30}
README_PLANT_TRIP = {"R_P_HHH", "RB_L_HH", "DR_L_LL", "DR_L_HH", "COL_P_HH", "COL_L_LL"}
SIF_TAG = {"R_T_HH": ("cstr_temperature_K", "HIGH"), "R_P_HH": ("cstr_pressure_bar", "HIGH"),
           "R_L_HH": ("cstr_holdup_kmol", "HIGH"), "R_L_LL": ("cstr_holdup_kmol", "LOW"),
           "RB_L_HH": ("reboiler_holdup_kmol", "HIGH"), "RB_L_LL": ("reboiler_holdup_kmol", "LOW"),
           "DR_L_HH": ("drum_holdup_kmol", "HIGH"), "DR_L_LL": ("drum_holdup_kmol", "LOW"),
           "COL_P_HH": ("column_pressure_bar", "HIGH"),
           "COL_L_LL": ("column_min_tray_holdup_kmol", "LOW")}


def _sif_measurements(**over) -> dict:
    m = {"cstr_temperature_K": 335.7, "cstr_pressure_bar": 3.0, "cstr_holdup_kmol": 50.0,
         "reboiler_holdup_kmol": 34.5, "drum_holdup_kmol": 30.2, "column_pressure_bar": 1.0}
    m.update(over)
    return m


class SpecConsistencyTests(unittest.TestCase):
    def test_sif_thresholds_match_readme_table(self):
        c = SafetyConfig()
        code = {"R_T_HH": c.cstr_temperature_hh_K, "R_P_HH": c.cstr_pressure_hh_bar, "R_P_HHH": c.cstr_pressure_hhh_bar,
                "R_L_HH": c.cstr_level_hh_kmol, "R_L_LL": c.cstr_level_ll_kmol, "RB_L_LL": c.reboiler_level_ll_kmol,
                "RB_L_HH": c.reboiler_level_hh_kmol, "DR_L_LL": c.drum_level_ll_kmol, "DR_L_HH": c.drum_level_hh_kmol,
                "COL_P_HH": c.column_pressure_hh_bar, "COL_L_LL": c.column_tray_holdup_ll_kmol}
        self.assertEqual(code, README_SIF)

    def test_plant_trip_latches_match_readme(self):
        trips = set()
        for cause, limit in README_SIF.items():
            sif = SafetySystem()
            tag = {"R_P_HHH": "cstr_pressure_bar"}.get(cause) or SIF_TAG[cause][0]
            direction = "HIGH" if cause == "R_P_HHH" else SIF_TAG[cause][1]
            m = _sif_measurements(**{tag: limit + (0.01 if direction == "HIGH" else -0.01)})
            if sif.apply(m, PLANT.default_inputs(), PLANT.default_inputs()).plant_trip_latched:
                trips.add(cause)
        self.assertEqual(trips, README_PLANT_TRIP)

    def test_operator_alarms_sit_inside_the_sif_limits(self):
        """README: alarms are 'intentionally inside the SIF limits' (operator response window)."""
        alarms = default_alarm_definitions()
        for cause, (tag, direction) in SIF_TAG.items():
            same = [a for a in alarms if a.tag == tag and a.direction == direction]
            self.assertTrue(same, f"no operator alarm before SIF {cause}")
            for a in same:
                if direction == "HIGH":
                    self.assertLess(a.limit, README_SIF[cause], (a.alarm_id, cause))
                else:
                    self.assertGreater(a.limit, README_SIF[cause], (a.alarm_id, cause))

    def test_pressure_hh_keeps_feed_and_hhh_cuts_it(self):
        """README: P-HH maximises H2 removal WITHOUT cutting feed (endothermic); P-HHH reduces feed to <=40 %."""
        u = PLANT.default_inputs()
        hh = SafetySystem().apply(_sif_measurements(cstr_pressure_bar=4.05), u, u).commanded
        self.assertEqual(hh.fresh_ipa_feed, u.fresh_ipa_feed)
        self.assertEqual((hh.cstr_vent_valve, hh.cstr_bdv_valve, hh.cstr_heater_factor), (1.0, 1.0, 0.0))
        hhh = SafetySystem().apply(_sif_measurements(cstr_pressure_bar=4.40), u, u).commanded
        self.assertLessEqual(hhh.fresh_ipa_feed, 0.40 * u.fresh_ipa_feed + 1e-9)

    def test_column_p_hh_does_not_restore_lost_cooling_utility(self):
        """README: column P-HH forces the CW valve to 100 % but does not restore lost cooling effectiveness."""
        from dataclasses import replace
        u = replace(PLANT.default_inputs(), condenser_cooling_effectiveness=0.3)
        out = SafetySystem().apply(_sif_measurements(column_pressure_bar=1.36), u, PLANT.default_inputs()).commanded
        self.assertEqual(out.condenser_cooling_valve, 1.0)
        self.assertEqual(out.condenser_cooling_effectiveness, 0.3)
        self.assertEqual((out.steam_factor, out.fresh_ipa_feed, out.cstr_outflow), (0.0, 0.0, 0.0))


class DesignPointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = EpisodeRunner(PLANT, nominal_episode(30))
        cls.s = PLANT.operating_summary(cls.env.y, cls.env.last_actual)

    def test_design_point(self):
        """README: 100 kmol/h feed, 70 % conversion, 3 bar reactor, 1 bar column, CW valve 60 %."""
        s, u = self.s, self.env.last_actual
        self.assertAlmostEqual(u.fresh_ipa_feed, 100.0)
        self.assertAlmostEqual(s["cstr.conversion"], 0.70, places=3)
        self.assertAlmostEqual(s["cstr.pressure_bar"], 3.0, places=3)
        self.assertAlmostEqual(s["column.condenser_pressure_bar"], 1.0, places=3)
        self.assertAlmostEqual(u.condenser_cooling_valve, 0.60)

    def test_steady_state_component_balance_closes(self):
        """IPA -> acetone + H2: H2 vented = reaction rate = acetone leaving in distillate + bottoms."""
        s = self.s
        rate = s["cstr.reaction_rate_kmol_h"]
        acetone_out = (s["column.distillate_flow_kmol_h"] * s["column.distillate_x_acetone"]
                       + s["column.bottoms_flow_kmol_h"] * s["column.bottoms_x_acetone"])
        self.assertAlmostEqual(s["cstr.hydrogen_vent_kmol_h"], rate, delta=0.01 * rate)
        self.assertAlmostEqual(acetone_out, rate, delta=0.01 * rate)
        # liquid moles are conserved by the reaction: feed = CSTR outflow = distillate + bottoms
        self.assertAlmostEqual(s["column.distillate_flow_kmol_h"] + s["column.bottoms_flow_kmol_h"], 100.0, delta=0.5)

    def test_normal_disturbances_are_physical_steps(self):
        """Normal variability: +/-3 K cooling water / ambient, +/-5 K feed and heating utility, +/-10 % steam."""
        self.assertEqual(disturbance_step("cooling_water_temperature_K", 1.0)[:2], ("add", 3.0))
        self.assertEqual(disturbance_step("steam_pressure_bar", -1.0)[:2], ("multiply", 0.9))
        for target, (mode, size) in DISTURBANCE_STEPS.items():
            self.assertTrue(size <= 5.0 if mode == "add" else size <= 0.10, target)


class AlarmAcknowledgementTests(unittest.TestCase):
    def test_ack_records_who_and_returned_alarm_leaves_the_list(self):
        am = AlarmManager(default_alarm_definitions())
        am.update(1.0, {"cstr_pressure_bar": 3.6})
        self.assertEqual(am.status["R_P_H"].state, AlarmState.ACTIVE_UNACK)
        am.update(2.0, {"cstr_pressure_bar": 3.0})
        self.assertEqual(am.status["R_P_H"].state, AlarmState.RTN_UNACK)       # still listed
        am.acknowledge("R_P_H", 2.5, by="action-agent")
        self.assertEqual(am.status["R_P_H"].state, AlarmState.NORMAL)          # gone from the list
        self.assertEqual(am.history[-1].by, "action-agent")
        # acknowledged while active -> leaves the list by itself when the variable returns
        am.update(3.0, {"cstr_pressure_bar": 3.6})
        am.acknowledge("R_P_H", 3.0, by="action-agent")
        am.update(4.0, {"cstr_pressure_bar": 3.0})
        self.assertEqual(am.status["R_P_H"].state, AlarmState.NORMAL)

    def test_agent_acknowledges_alarms_it_handled(self):
        try:
            from acetone_plant.agents.recovery_graph import make_config
            from acetone_plant.agents.supervisor import AsyncSupervisor
        except ImportError as exc:  # langgraph not installed
            self.skipTest(str(exc))
        from acetone_plant.plant import SupervisoryAction
        from acetone_plant.server.plant_service import PlantService

        async def scenario():
            s = PlantService()
            await s.load({"scenario": "nominal", "horizon_min": 30})
            sup = AsyncSupervisor(s, make_config("passive"), mode="autonomous")
            am = s.env.alarms
            am.update(1.0, {"cstr_pressure_bar": 3.6, "drum_holdup_kmol": 13.0})   # R_P_H + DR_L_LL active
            # dispatching an action takes ownership: standing alarms are acknowledged
            await sup._dispatch(SupervisoryAction(throughput_target=95.0),
                                {"t_obs": 1.0, "plan": {"commands": []}, "diagnosis": {"summary": "x"}})
            self.assertEqual(am.status["R_P_H"].state, AlarmState.ACTIVE_ACK)
            self.assertEqual(am.status["DR_L_LL"].state, AlarmState.ACTIVE_ACK)
            am.update(2.0, {"cstr_pressure_bar": 3.0, "drum_holdup_kmol": 30.0})   # back to normal
            self.assertEqual(am.status["R_P_H"].state, AlarmState.NORMAL)          # cleared from the list
            # an alarm that came and went during the incident (never acknowledged) is acknowledged too
            am.update(3.0, {"column_pressure_bar": 1.2})
            am.update(4.0, {"column_pressure_bar": 1.0})
            self.assertEqual(am.status["COL_P_H"].state, AlarmState.RTN_UNACK)
            await sup._acknowledge_returned_alarms()
            self.assertEqual(am.status["COL_P_H"].state, AlarmState.NORMAL)
            self.assertIn("action-agent", {e.by for e in am.history})
            self.assertTrue(any(e.get("stage") == "alarm_ack" for e in s.agent_log))
        asyncio.run(scenario())

    def test_sif_reset_request_counts_as_agent_involvement(self):
        try:
            from acetone_plant.agents.recovery_graph import make_config
            from acetone_plant.agents.supervisor import AsyncSupervisor
        except ImportError as exc:
            self.skipTest(str(exc))
        from dataclasses import replace
        from acetone_plant.server.plant_service import PlantService

        async def scenario():
            s = PlantService()
            await s.load({"scenario": "nominal", "horizon_min": 30})
            sup = AsyncSupervisor(s, make_config("passive"), mode="autonomous")
            s.env.last_safety_result = replace(s.env.last_safety_result, latched_causes=("R_T_HH",),
                                               plant_trip_latched=False)
            await sup._request_sif_reset()
            self.assertTrue(s._pending_reset_sif)               # reset requested (non-trip latch)
            am = s.env.alarms
            am.update(1.0, {"cstr_temperature_K": 343.0})
            am.update(2.0, {"cstr_temperature_K": 336.0})
            await sup._acknowledge_returned_alarms()
            self.assertEqual(am.status["R_T_H"].state, AlarmState.NORMAL)
        asyncio.run(scenario())

    def test_returned_alarm_without_agent_involvement_is_left_for_the_operator(self):
        try:
            from acetone_plant.agents.recovery_graph import make_config
            from acetone_plant.agents.supervisor import AsyncSupervisor
        except ImportError as exc:
            self.skipTest(str(exc))
        from acetone_plant.server.plant_service import PlantService

        async def scenario():
            s = PlantService()
            await s.load({"scenario": "nominal", "horizon_min": 30})
            sup = AsyncSupervisor(s, make_config("passive"), mode="autonomous")
            am = s.env.alarms
            am.update(1.0, {"cstr_pressure_bar": 3.6})
            am.update(2.0, {"cstr_pressure_bar": 3.0})
            await sup._acknowledge_returned_alarms()          # agents never acted
            self.assertEqual(am.status["R_P_H"].state, AlarmState.RTN_UNACK)
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
