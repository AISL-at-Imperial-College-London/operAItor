from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import csv
import numpy as np

from .plant import IntegratedPlantDAE, PlantInputs
from .control import RegulatoryControlSystem
from .measurement import MeasurementModel, default_sensor_specs
from .scenario import Scenario, ActuatorFaultEngine


def _with_derived_levels(plant: IntegratedPlantDAE, m: dict[str, float]) -> dict[str, float]:
    out = dict(m)
    out["cstr_level_percent"] = 100.0 * m["cstr_holdup_kmol"] / plant.cstr.cfg.liquid_holdup_sp
    out["reboiler_level_percent"] = 100.0 * m["reboiler_holdup_kmol"] / plant.column.cfg.reboiler_holdup_sp
    out["drum_level_percent"] = 100.0 * m["drum_holdup_kmol"] / plant.column.cfg.drum_holdup_sp
    return out


def _derived_process_values(plant: IntegratedPlantDAE, y: np.ndarray, u: PlantInputs) -> dict[str, float]:
    yc, ycol = plant.split_state(y)
    rb = ycol[plant.column.reboiler_offset : plant.column.reboiler_offset + 8]
    top = plant.column.tray(ycol, 0)
    Tsteam = plant.th.water_saturation_temperature(u.steam_pressure_bar)
    Qreb = plant.column.cfg.UA_reboiler * u.steam_factor * plant.column._positive(Tsteam - rb[3])
    Qj = plant.cstr.cfg.UA_jacket_kJ_hK * u.cstr_heater_factor * plant.cstr._positive(
        u.cstr_heating_utility_temperature_K - yc[plant.cstr.T]
    )
    reflux_ratio = u.reflux_flow / max(u.distillate_flow, 1e-8)
    cap = plant.column.condenser_capacity(ycol, plant._column_inputs(u, yc))
    Qcond = cap["available_duty_kJ_h"]
    return {
        "reflux_ratio": float(reflux_ratio),
        "reboiler_duty_MW": float(Qreb / 3.6e6),
        "cstr_heater_duty_MW": float(Qj / 3.6e6),
        "condenser_duty_MW": float(Qcond / 3.6e6),
    }


def _build_record(
    plant: IntegratedPlantDAE,
    t_h: float,
    y: np.ndarray,
    truth: dict[str, float],
    measured: dict[str, float],
    requested: PlantInputs,
    actual: PlantInputs,
    scenario: Scenario,
    nominal: PlantInputs,
    labels: dict[str, object] | None = None,
) -> dict[str, object]:
    truth2 = _with_derived_levels(plant, truth)
    meas2 = _with_derived_levels(plant, measured)
    row: dict[str, object] = {"time_h": float(t_h), "time_min": float(60.0 * t_h)}
    row.update({f"true.{k}": float(v) for k, v in truth2.items()})
    row.update({f"meas.{k}": float(v) for k, v in meas2.items()})
    row.update({f"req.{k}": float(v) for k, v in asdict(requested).items()})
    row.update({f"act.{k}": float(v) for k, v in asdict(actual).items()})
    row.update({f"derived.{k}": float(v) for k, v in _derived_process_values(plant, y, actual).items()})
    row.update({
        "scenario.throughput_fraction": float(scenario.throughput_fraction.value(t_h)),
        "scenario.feed_temperature_K": float(scenario.feed_temperature_K.value(t_h)),
        "scenario.cooling_water_temperature_K": float(scenario.cooling_water_temperature_K.value(t_h)),
        "scenario.steam_pressure_bar": float(scenario.steam_pressure_bar.value(t_h)),
        "scenario.cstr_heating_utility_temperature_K": float(scenario.cstr_heating_utility_temperature_K.value(t_h)),
        "scenario.ambient_temperature_K": float(scenario.ambient_temperature_K.value(t_h)),
        "scenario.catalyst_activity": float(scenario.catalyst_activity.value(t_h)),
        "scenario.condenser_cooling_effectiveness": float(
            scenario.condenser_cooling_effectiveness.value(t_h)
        ),
        "scenario.condenser_pressure_bar": float(scenario.condenser_pressure_bar.value(t_h)),
        "scenario.cstr_liquid_leak_kmol_h": float(scenario.cstr_liquid_leak_kmol_h.value(t_h)),
        "scenario.reboiler_liquid_leak_kmol_h": float(scenario.reboiler_liquid_leak_kmol_h.value(t_h)),
        "scenario.drum_liquid_leak_kmol_h": float(scenario.drum_liquid_leak_kmol_h.value(t_h)),
    })
    if labels:
        row.update(labels)
    return row


def simulate_scenario(
    plant: IntegratedPlantDAE,
    y0: np.ndarray,
    controllers: RegulatoryControlSystem,
    scenario: Scenario,
    control_interval_h: float = 1.0 / 30.0,
    nominal_inputs: PlantInputs | None = None,
    measurement_model: MeasurementModel | None = None,
    actuator_faults: ActuatorFaultEngine | None = None,
    label_provider=None,
    progress_every_steps: int | None = None,
):
    """Run a sampled-control scenario using the transparent implicit DAE stepper.

    The scenario trajectories are continuous-time objects and are independent of
    integration step size. This reference driver currently uses one implicit
    backward-Euler DAE step per control interval. A future adaptive IDA driver can
    reuse the same Scenario.value(t), controller, fault and measurement layers.
    """
    nominal = plant.default_inputs() if nominal_inputs is None else nominal_inputs
    faults = ActuatorFaultEngine() if actuator_faults is None else actuator_faults
    faults.reset()
    controllers.reset()

    if measurement_model is None:
        nominal_truth = plant.measurements(y0)
        measurement_model = MeasurementModel(default_sensor_specs(nominal_truth))
    measurement_model.reset()

    nsteps = int(round(scenario.duration_h / control_interval_h))
    if not np.isclose(nsteps * control_interval_h, scenario.duration_h, atol=1e-10):
        raise ValueError("scenario.duration_h must be an integer multiple of control_interval_h in the reference driver")
    t = np.linspace(0.0, scenario.duration_h, nsteps + 1)
    Y = np.zeros((nsteps + 1, len(y0)))
    Y[0] = y0
    records: list[dict[str, float]] = []

    last_requested = nominal
    last_actual = nominal
    for k in range(nsteps):
        tk = float(t[k])
        truth = plant.measurements(Y[k])
        measured = measurement_model.observe(truth)

        exogenous = scenario.evaluate(tk, nominal)
        requested = controllers.update(
            measured,
            control_interval_h,
            base_inputs=exogenous,
            throughput_setpoint=exogenous.fresh_ipa_feed,
        )
        actual = faults.apply(tk, requested, nominal)

        labels = None if label_provider is None else label_provider(tk)
        records.append(_build_record(
            plant, tk, Y[k], truth, measured, requested, actual, scenario, nominal, labels
        ))

        sol = plant.backward_euler_step(Y[k], control_interval_h, actual, guess=Y[k])
        if not sol.success:
            raise RuntimeError(f"DAE step failed at t={tk:.6f} h: {sol.message}")
        if np.linalg.norm(sol.fun) > 1e-5:
            raise RuntimeError(f"DAE residual too large at t={tk:.6f} h: {np.linalg.norm(sol.fun):.3e}")
        Y[k + 1] = sol.x
        last_requested, last_actual = requested, actual
        if progress_every_steps and (k + 1) % progress_every_steps == 0:
            print(f"  simulated {60.0*t[k+1]:.0f} / {60.0*scenario.duration_h:.0f} min")

    truth = plant.measurements(Y[-1])
    measured = measurement_model.observe(truth)
    final_labels = None if label_provider is None else label_provider(float(t[-1]))
    records.append(_build_record(
        plant, float(t[-1]), Y[-1], truth, measured,
        last_requested, last_actual, scenario, nominal, final_labels
    ))
    return t, Y, records


def write_records_csv(records: list[dict[str, object]], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(records[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(records)


# Backwards-compatible wrapper retained from v0.2.
def simulate_closed_loop(
    plant: IntegratedPlantDAE,
    y0: np.ndarray,
    controllers: RegulatoryControlSystem,
    t_final_h: float,
    dt_h: float,
    base_inputs: PlantInputs | None = None,
    throughput_profile=None,
):
    from .scenario import Scenario, Constant

    class _CallableTrajectory:
        def value(self, t_h):
            if throughput_profile is None:
                return 1.0
            nominal = (plant.default_inputs() if base_inputs is None else base_inputs).fresh_ipa_feed
            return float(throughput_profile(t_h) / nominal)
        def breakpoints(self):
            return []

    scenario = Scenario(
        duration_h=t_final_h,
        throughput_fraction=_CallableTrajectory() if throughput_profile is not None else Constant(1.0),
    )
    return simulate_scenario(
        plant, y0, controllers, scenario,
        control_interval_h=dt_h,
        nominal_inputs=base_inputs,
    )
