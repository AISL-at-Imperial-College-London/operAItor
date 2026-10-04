from pathlib import Path
import numpy as np

from integrated_plant import IntegratedPlantDAE, RegulatoryControlSystem
from integrated_plant.measurement import MeasurementModel, default_sensor_specs
from integrated_plant.scenario import base_production_scenario, ActuatorFaultEngine
from integrated_plant.simulate import simulate_scenario, write_records_csv
from integrated_plant.plotting import plot_key_measurements, plot_manipulated_and_disturbances


def main():
    out = Path("results")
    out.mkdir(exist_ok=True)

    plant = IntegratedPlantDAE()
    nominal = plant.default_inputs()
    y0 = plant.nominal_state(nominal)
    r0 = np.linalg.norm(plant.scaled_residual(0.0, y0, np.zeros_like(y0), nominal))
    print(f"nominal scaled residual norm: {r0:.3e}")

    controllers = RegulatoryControlSystem.from_steady_state(plant, y0, nominal)
    sensors = MeasurementModel(default_sensor_specs(plant.measurements(y0)), seed=20260928)
    scenario = base_production_scenario()

    t, Y, records = simulate_scenario(
        plant,
        y0,
        controllers,
        scenario,
        control_interval_h=1.0/60.0,  # 1 min controller / measurement interval
        nominal_inputs=nominal,
        measurement_model=sensors,
        actuator_faults=ActuatorFaultEngine(),
    )

    write_records_csv(records, out / "base_scenario.csv")
    plot_key_measurements(records, out / "key_measurements.png")
    plot_manipulated_and_disturbances(records, out / "manipulated_and_disturbances.png")

    print(f"completed {scenario.name}: {60.0*t[-1]:.0f} min, {len(t)-1} implicit DAE steps")
    print(f"wrote {out / 'base_scenario.csv'}")
    print(f"wrote {out / 'key_measurements.png'}")
    print(f"wrote {out / 'manipulated_and_disturbances.png'}")


if __name__ == "__main__":
    main()
