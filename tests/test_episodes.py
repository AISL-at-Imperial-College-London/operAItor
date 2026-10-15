import unittest
from dataclasses import replace
import numpy as np

from operaitor.plant import (
    IntegratedPlantDAE, SafetySystem, SafetyConfig,
    SensorFault, SensorFaultEngine, ActuatorFault, ActuatorFaultEngine,
    SupervisoryAction, PassiveActor,
    randomized_step_disturbance_scenario,
    curated_episode, EpisodeRunner, EpisodeTiming, RegulatoryControlSystem,
)


class V05Tests(unittest.TestCase):
    def setUp(self):
        self.plant = IntegratedPlantDAE()
        self.nominal = self.plant.default_inputs()
        self.y0 = self.plant.nominal_state(self.nominal)
        self.truth = self.plant.measurements(self.y0)


    def test_effective_h2_gas_capacitance_is_about_25_m3(self):
        self.assertAlmostEqual(self.plant.cstr.reactor_headspace_volume(50.0), 2.25, places=8)
        self.assertAlmostEqual(self.plant.cstr.gas_volume(50.0), 25.0, places=8)

    def test_capacity_ramp_fault_degrades_actuator_limit(self):
        eng = ActuatorFaultEngine([
            ActuatorFault(
                target='cstr_vent_valve', start_h=0.0, mode='capacity_ramp',
                capacity_fraction=0.2, capacity_reference_value=1.0, ramp_duration_h=10.0/60.0
            )
        ])
        req = replace(self.nominal, cstr_vent_valve=1.0)
        a0 = eng.apply(0.0, req, self.nominal)
        a5 = eng.apply(5.0/60.0, req, self.nominal)
        a10 = eng.apply(10.0/60.0, req, self.nominal)
        self.assertAlmostEqual(a0.cstr_vent_valve, 1.0, places=8)
        self.assertAlmostEqual(a5.cstr_vent_valve, 0.6, places=8)
        self.assertAlmostEqual(a10.cstr_vent_valve, 0.2, places=8)

    def test_default_episode_clocks_are_multirate(self):
        timing = EpisodeTiming()
        self.assertAlmostEqual(timing.plant_step_min, 0.1)
        self.assertAlmostEqual(timing.control_interval_min, 1.0)
        self.assertAlmostEqual(timing.decision_interval_min, 5.0)
        self.assertEqual(timing.strides(), (50, 10))

    def test_nominal_residual_still_closes(self):
        r = self.plant.scaled_residual(0.0, self.y0, np.zeros_like(self.y0), self.nominal)
        self.assertLess(np.linalg.norm(r), 1e-9)

    def test_random_disturbances_are_15_min_physical_steps(self):
        from operaitor.plant.scenario import DISTURBANCE_STEPS
        s = randomized_step_disturbance_scenario(duration_min=180, seed=20261004)
        self.assertGreaterEqual(len(s.events), 1)
        for e in s.events:
            self.assertAlmostEqual((e.end_h - e.start_h) * 60.0, 15.0, places=10)
            self.assertAlmostEqual(abs(e.magnitude), DISTURBANCE_STEPS[e.target][1], places=10)

    def test_legacy_ten_percent_disturbances_still_available(self):
        s = randomized_step_disturbance_scenario(duration_min=180, seed=20261004, magnitude_fraction=0.10)
        for e in s.events:
            self.assertAlmostEqual(abs(e.magnitude), 0.10, places=10)

    def test_passive_actor_no_action(self):
        a = PassiveActor().act({})
        self.assertEqual(a, SupervisoryAction.no_action())

    def _fresh_sif(self):
        return SafetySystem(SafetyConfig())

    def test_sif_reactor_temperature_hh(self):
        m = dict(self.truth); m['cstr_temperature_K'] = 349.0
        s = self._fresh_sif().apply(m, self.nominal, self.nominal)
        self.assertEqual(s.commanded.cstr_heater_factor, 0.0)
        self.assertEqual(s.commanded.cstr_emergency_cooling_factor, 1.0)
        self.assertAlmostEqual(s.commanded.fresh_ipa_feed, self.nominal.fresh_ipa_feed)

    def test_sif_reactor_pressure_hh_retains_feed(self):
        m = dict(self.truth); m['cstr_pressure_bar'] = 4.1
        s = self._fresh_sif().apply(m, self.nominal, self.nominal)
        self.assertEqual(s.commanded.cstr_vent_valve, 1.0)
        self.assertEqual(s.commanded.cstr_bdv_valve, 1.0)
        self.assertEqual(s.commanded.cstr_heater_factor, 0.0)
        self.assertAlmostEqual(s.commanded.fresh_ipa_feed, self.nominal.fresh_ipa_feed)
        self.assertFalse(s.plant_trip_latched)

    def test_sif_reactor_pressure_hhh_reduces_feed(self):
        m = dict(self.truth); m['cstr_pressure_bar'] = 4.5
        s = self._fresh_sif().apply(m, self.nominal, self.nominal)
        self.assertLessEqual(s.commanded.fresh_ipa_feed, 0.4 * self.nominal.fresh_ipa_feed + 1e-12)
        self.assertTrue(s.plant_trip_latched)

    def test_sif_reactor_level_high_and_low(self):
        m = dict(self.truth); m['cstr_holdup_kmol'] = 73.0
        s = self._fresh_sif().apply(m, self.nominal, self.nominal)
        self.assertEqual(s.commanded.fresh_ipa_feed, 0.0)
        m = dict(self.truth); m['cstr_holdup_kmol'] = 19.0
        s = self._fresh_sif().apply(m, self.nominal, self.nominal)
        self.assertEqual(s.commanded.cstr_outflow, 0.0)
        self.assertEqual(s.commanded.cstr_heater_factor, 0.0)

    def test_sif_reboiler_low_and_high(self):
        m = dict(self.truth); m['reboiler_holdup_kmol'] = 10.0
        s = self._fresh_sif().apply(m, self.nominal, self.nominal)
        self.assertEqual(s.commanded.steam_factor, 0.0)
        self.assertEqual(s.commanded.bottoms_flow, 0.0)
        m = dict(self.truth); m['reboiler_holdup_kmol'] = 55.0
        s = self._fresh_sif().apply(m, self.nominal, self.nominal)
        self.assertTrue(s.plant_trip_latched)
        self.assertEqual(s.commanded.cstr_outflow, 0.0)

    def test_sif_drum_low_and_high(self):
        m = dict(self.truth); m['drum_holdup_kmol'] = 9.0
        s = self._fresh_sif().apply(m, self.nominal, self.nominal)
        self.assertEqual(s.commanded.reflux_flow, 0.0)
        self.assertEqual(s.commanded.distillate_flow, 0.0)
        self.assertTrue(s.plant_trip_latched)
        m = dict(self.truth); m['drum_holdup_kmol'] = 50.0
        s = self._fresh_sif().apply(m, self.nominal, self.nominal)
        self.assertEqual(s.commanded.steam_factor, 0.0)
        self.assertTrue(s.plant_trip_latched)

    def test_sif_column_pressure_high(self):
        m = dict(self.truth); m['column_pressure_bar'] = 1.40
        s = self._fresh_sif().apply(m, self.nominal, self.nominal)
        self.assertEqual(s.commanded.steam_factor, 0.0)
        self.assertAlmostEqual(s.commanded.condenser_cooling_valve, 1.0)
        self.assertTrue(s.plant_trip_latched)

    def test_sensor_bias_and_freeze(self):
        eng = SensorFaultEngine([
            SensorFault(target='cstr_pressure_bar', start_h=0.0, mode='bias', bias=-1.0),
            SensorFault(target='cstr_temperature_K', start_h=0.0, mode='freeze'),
        ])
        a = eng.apply(0.0, self.truth)
        self.assertAlmostEqual(a['cstr_pressure_bar'], self.truth['cstr_pressure_bar'] - 1.0)
        first_t = a['cstr_temperature_K']
        changed = dict(self.truth); changed['cstr_temperature_K'] += 10.0
        b = eng.apply(0.1, changed)
        self.assertAlmostEqual(b['cstr_temperature_K'], first_t)

    def test_actuator_stiction(self):
        eng = ActuatorFaultEngine([
            ActuatorFault(target='reflux_flow', start_h=0.0, mode='stiction', stiction_band_fraction=0.05)
        ])
        a = eng.apply(0.0, self.nominal, self.nominal)
        b = eng.apply(0.1, replace(self.nominal, reflux_flow=self.nominal.reflux_flow + 2.0), self.nominal)
        self.assertAlmostEqual(a.reflux_flow, b.reflux_flow)
        c = eng.apply(0.2, replace(self.nominal, reflux_flow=self.nominal.reflux_flow + 10.0), self.nominal)
        self.assertAlmostEqual(c.reflux_flow, self.nominal.reflux_flow + 10.0)

    def test_nominal_condenser_valve_has_upward_authority(self):
        self.assertAlmostEqual(self.nominal.condenser_cooling_valve, 0.60, places=8)
        self.assertAlmostEqual(self.nominal.condenser_cooling_effectiveness, 1.0, places=8)
        # Headroom at the design point: the condenser can take more than it condenses, and the heat it
        # removes never exceeds what the cooling water can take (one energy per kmol for both).
        col = self.plant.column
        _, ycol = self.plant.split_state(self.y0)
        cap = col.condenser_capacity(ycol, self.plant._column_inputs(self.nominal, self.plant.split_state(self.y0)[0]))
        self.assertGreater(cap["capacity_kmol_h"], 1.01 * cap["condensed_flow_kmol_h"])
        self.assertLessEqual(cap["condensed_flow_kmol_h"] * col.cfg.condenser_energy_per_kmol_kJ,
                             cap["available_duty_kJ_h"] * (1 + 1e-9))

    def test_column_pressure_controller_opens_cooling_valve_on_high_pressure(self):
        ctl = RegulatoryControlSystem.from_steady_state(self.plant, self.y0, self.nominal)
        m = dict(self.truth)
        m['column_pressure_bar'] += 0.05
        u = ctl.update(m, 1.0/60.0, base_inputs=self.nominal)
        self.assertGreater(u.condenser_cooling_valve, self.nominal.condenser_cooling_valve)
        self.assertLessEqual(u.condenser_cooling_valve, 1.0)

    def test_column_pressure_rises_when_cooling_effectiveness_is_lost(self):
        u = replace(self.nominal, condenser_cooling_effectiveness=0.2)
        sol = self.plant.backward_euler_step(self.y0, 1.0/60.0, u)
        self.assertTrue(sol.success)
        m = self.plant.measurements(sol.x)
        self.assertGreater(m['column_pressure_bar'], self.truth['column_pressure_bar'])

    def test_episode_runner_is_turn_based(self):
        spec = curated_episode('h2_pcv_stuck_closed', duration_min=40, onset_min=30)
        env = EpisodeRunner(self.plant, spec, timing=EpisodeTiming(plant_step_min=1.0, decision_interval_min=5.0))
        env.reset()
        t0 = env.t_h
        env.step(SupervisoryAction.no_action())
        self.assertAlmostEqual((env.t_h - t0) * 60.0, 5.0, places=8)


if __name__ == '__main__':
    unittest.main()
