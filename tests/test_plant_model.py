import unittest
import numpy as np

from operaitor.plant import (
    IntegratedPlantDAE, RegulatoryControlSystem,
    base_production_scenario, ActuatorFault, ActuatorFaultEngine,
)


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.plant = IntegratedPlantDAE()
        self.u = self.plant.default_inputs()
        self.y = self.plant.nominal_state(self.u)

    def test_nominal_residual_closes(self):
        r = self.plant.scaled_residual(0.0, self.y, np.zeros_like(self.y), self.u)
        self.assertLess(np.linalg.norm(r), 1e-9)

    def test_nominal_reactor_matches_column_feed(self):
        m = self.plant.measurements(self.y)
        self.assertAlmostEqual(m["cstr_x_acetone"], 0.70, places=8)
        self.assertAlmostEqual(self.u.cstr_outflow, 100.0, places=8)

    def test_controllers_return_bias_at_nominal(self):
        ctl = RegulatoryControlSystem.from_steady_state(self.plant, self.y, self.u)
        u = ctl.update(self.plant.measurements(self.y), 1.0 / 3600.0, self.u)
        self.assertAlmostEqual(u.cstr_outflow, self.u.cstr_outflow, places=10)
        self.assertAlmostEqual(u.cstr_heater_factor, self.u.cstr_heater_factor, places=10)
        self.assertAlmostEqual(u.reflux_flow, self.u.reflux_flow, places=10)
        self.assertAlmostEqual(u.steam_factor, self.u.steam_factor, places=10)
        self.assertAlmostEqual(u.bottoms_flow, self.u.bottoms_flow, places=10)
        self.assertAlmostEqual(u.distillate_flow, self.u.distillate_flow, places=10)

    def test_implicit_step(self):
        from dataclasses import replace
        u = replace(self.u, fresh_ipa_feed=102.0)
        sol = self.plant.backward_euler_step(self.y, 0.001, u)
        self.assertTrue(sol.success)
        self.assertGreater(self.plant.measurements(sol.x)["cstr_holdup_kmol"], 50.0)

    def test_nominal_steam_pressure_reproduces_430K(self):
        Ts = self.plant.th.water_saturation_temperature(self.u.steam_pressure_bar)
        self.assertAlmostEqual(Ts, 430.0, places=5)

    def test_base_scenario_sequence(self):
        s = base_production_scenario()
        self.assertAlmostEqual(s.throughput_fraction.value(0.5), 1.00)
        self.assertAlmostEqual(s.throughput_fraction.value(2.5), 0.85)
        self.assertAlmostEqual(s.throughput_fraction.value(4.5), 0.70)
        self.assertAlmostEqual(s.throughput_fraction.value(6.5), 0.85)
        self.assertAlmostEqual(s.throughput_fraction.value(8.5), 1.00)

    def test_pressure_valve_is_atmospheric_and_nominally_balanced(self):
        self.assertAlmostEqual(self.plant.cstr.cfg.vent_backpressure_bar, 1.01325, places=5)
        m = self.plant.measurements(self.y)
        self.assertAlmostEqual(m["cstr_h2_vent_kmol_h"], 70.0, places=6)

    def test_capacity_fault_clamps_requested_actuator(self):
        from dataclasses import replace
        req = replace(self.u, reflux_flow=120.0)
        engine = ActuatorFaultEngine([
            ActuatorFault(target="reflux_flow", start_h=1.0, mode="capacity", capacity_fraction=0.5)
        ])
        act = engine.apply(2.0, req, self.u)
        self.assertAlmostEqual(act.reflux_flow, 0.5 * self.u.reflux_flow)


if __name__ == "__main__":
    unittest.main()
