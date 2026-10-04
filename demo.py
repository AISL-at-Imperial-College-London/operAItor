from dataclasses import replace
import numpy as np

from integrated_plant import IntegratedPlantDAE, RegulatoryControlSystem


def main():
    plant = IntegratedPlantDAE()
    u0 = plant.default_inputs()

    y_ss = plant.nominal_state(u0)
    print("scaled residual norm:", np.linalg.norm(plant.scaled_residual(0.0, y_ss, np.zeros_like(y_ss), u0)))
    print()
    for k, v in plant.operating_summary(y_ss, u0).items():
        print(f"{k:38s}: {v:.6g}")

    controllers = RegulatoryControlSystem.from_steady_state(plant, y_ss, u0)
    measurements = plant.measurements(y_ss)
    u_control = controllers.update(measurements, dt_h=1.0/3600.0, base_inputs=u0)
    print("\ncontroller outputs at nominal steady state:")
    print(u_control)

    # Smoke test: one implicit step with a +2% throughput change and feedback controls.
    u_step = replace(u_control, fresh_ipa_feed=102.0)
    step = plant.backward_euler_step(y_ss, dt_h=0.001, inputs=u_step)
    print("\none DAE step success:", step.success)
    print("CSTR holdup after step:", plant.measurements(step.x)["cstr_holdup_kmol"])


if __name__ == "__main__":
    main()
