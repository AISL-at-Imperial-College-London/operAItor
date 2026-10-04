import unittest

from integrated_plant import (
    AlarmManager, AlarmState, EpisodeRunner, EpisodeTiming, IntegratedPlantDAE,
    RuntimeInputEngine, RuntimeInputEvent, default_alarm_definitions,
    nominal_episode,
)


class OperatorLayerTests(unittest.TestCase):
    def test_alarm_ack_and_clear(self):
        alarms = AlarmManager(default_alarm_definitions())
        m = {d.tag: 0.0 for d in alarms.definitions}
        # Populate values that are safely normal for all configured alarms.
        m.update({
            "cstr_pressure_bar": 3.0,
            "cstr_temperature_K": 335.0,
            "cstr_holdup_kmol": 50.0,
            "reboiler_holdup_kmol": 34.5,
            "drum_holdup_kmol": 30.0,
            "column_pressure_bar": 1.0,
            "distillate_x_acetone": 0.94,
            "bottoms_x_acetone": 0.04,
        })
        alarms.update(0.0, m)
        self.assertEqual(alarms.status["R_P_H"].state, AlarmState.NORMAL)
        m["cstr_pressure_bar"] = 3.6
        alarms.update(1.0, m)
        self.assertEqual(alarms.status["R_P_H"].state, AlarmState.ACTIVE_UNACK)
        alarms.acknowledge("R_P_H", 1.0)
        self.assertEqual(alarms.status["R_P_H"].state, AlarmState.ACTIVE_ACK)
        m["cstr_pressure_bar"] = 3.3
        alarms.update(2.0, m)
        self.assertEqual(alarms.status["R_P_H"].state, AlarmState.NORMAL)

    def test_alarm_return_before_ack(self):
        alarms = AlarmManager(default_alarm_definitions())
        m = {
            "cstr_pressure_bar": 3.6, "cstr_temperature_K": 335.0,
            "cstr_holdup_kmol": 50.0, "reboiler_holdup_kmol": 34.5,
            "drum_holdup_kmol": 30.0, "column_pressure_bar": 1.0,
            "distillate_x_acetone": 0.94, "bottoms_x_acetone": 0.04,
        }
        alarms.update(0.0, m)
        m["cstr_pressure_bar"] = 3.3
        alarms.update(1.0, m)
        self.assertEqual(alarms.status["R_P_H"].state, AlarmState.RTN_UNACK)
        alarms.acknowledge("R_P_H", 1.0)
        self.assertEqual(alarms.status["R_P_H"].state, AlarmState.NORMAL)

    def test_runtime_step_disturbance(self):
        engine = RuntimeInputEngine()
        plant = IntegratedPlantDAE()
        u = plant.default_inputs()
        engine.add(RuntimeInputEvent("cw_up", "disturbance", "cooling_water_temperature_K",
                                     0.5, 0.75, "multiply", 1.10))
        self.assertAlmostEqual(engine.apply(0.4, u).cooling_water_temperature_K, u.cooling_water_temperature_K)
        self.assertAlmostEqual(engine.apply(0.6, u).cooling_water_temperature_K, 1.10*u.cooling_water_temperature_K)
        self.assertAlmostEqual(engine.apply(0.8, u).cooling_water_temperature_K, u.cooling_water_temperature_K)

    def test_episode_exposes_alarm_counts(self):
        plant = IntegratedPlantDAE()
        env = EpisodeRunner(plant, nominal_episode(10), timing=EpisodeTiming(1, 1))
        obs = env.observation()
        self.assertIn("active_alarm_count", obs)
        self.assertIn("alarm_states", obs)


if __name__ == "__main__":
    unittest.main()
