from __future__ import annotations

from dataclasses import replace
import numpy as np

from .episode import EpisodeSpec
from .measurement import SensorFault, SensorFaultEngine
from .equipment import PumpFault, PumpFaultEngine
from .scenario import (
    ActuatorFault, ActuatorFaultEngine, Constant, PiecewiseConstant,
    ReactionRateFault, CatalystActivityTrajectory,
    Scenario, ScenarioEvent, nominal_hold_scenario,
    randomized_step_disturbance_scenario,
)


def _permanent_step(nominal: float, start_h: float, value: float) -> PiecewiseConstant:
    return PiecewiseConstant([(0.0, nominal), (float(start_h), float(value))])


def _event(name: str, kind: str, target: str, start_min: float,
           magnitude: float | None = None, end_min: float | None = None,
           metadata: str = "") -> ScenarioEvent:
    return ScenarioEvent(
        name=name, kind=kind, target=target,
        start_h=start_min / 60.0,
        end_h=None if end_min is None else end_min / 60.0,
        magnitude=magnitude, metadata=metadata,
    )


def nominal_episode(duration_min: float = 180.0) -> EpisodeSpec:
    s = nominal_hold_scenario(duration_min)
    return EpisodeSpec(scenario=s, name="nominal_hold", allow_recovery_termination=False, horizon_is_completion=True)


def normal_variability_episode(duration_min: float = 180.0, seed: int = 20261004) -> EpisodeSpec:
    s = randomized_step_disturbance_scenario(duration_min=duration_min, seed=seed)
    return EpisodeSpec(scenario=s, name=s.name, allow_recovery_termination=False, horizon_is_completion=True)


def curated_episode(name: str, duration_min: float = 180.0, onset_min: float = 30.0) -> EpisodeSpec:
    start = onset_min / 60.0
    base = Scenario(duration_h=duration_min / 60.0, name=name)


    if name == "h2_pcv_capacity_degradation":
        # Incipient valve-capacity loss intended for operator/agent recovery
        # studies.  The maximum achievable PCV opening degrades from 100% to
        # 20% over ten simulated minutes; the normal PIC continues to demand
        # whatever opening it requires.
        af = ActuatorFaultEngine([
            ActuatorFault(
                name="H2_PCV_capacity_degradation", target="cstr_vent_valve",
                start_h=start, mode="capacity_ramp", capacity_fraction=0.20,
                capacity_reference_value=1.0, ramp_duration_h=10.0/60.0,
            )
        ])
        s = replace(base, events=(_event(
            "H2_PCV_capacity_degradation", "actuator_fault", "cstr_vent_valve",
            onset_min, 0.20, metadata="maximum opening ramps from 100% to 20% over 10 min"
        ),))
        return EpisodeSpec(s, actuator_faults=af, name=name, allow_recovery_termination=False)

    if name == "h2_pcv_stuck_closed":
        af = ActuatorFaultEngine([
            ActuatorFault(
                name="H2_PCV_stuck_closed", target="cstr_vent_valve",
                start_h=start, mode="fixed", fixed_value=0.0,
            )
        ])
        s = replace(base, events=(_event("H2_PCV_stuck_closed", "actuator_fault", "cstr_vent_valve", onset_min, 0.0),))
        return EpisodeSpec(s, actuator_faults=af, name=name, allow_recovery_termination=False)

    if name == "reactor_heater_stuck_high":
        af = ActuatorFaultEngine([
            ActuatorFault(
                name="reactor_heater_stuck_high", target="cstr_heater_factor",
                start_h=start, mode="fixed", fixed_value=1.0,
            )
        ])
        s = replace(base, events=(_event("reactor_heater_stuck_high", "actuator_fault", "cstr_heater_factor", onset_min, 1.0),))
        return EpisodeSpec(s, actuator_faults=af, name=name, allow_recovery_termination=False)

    if name == "cstr_outlet_capacity_loss":
        af = ActuatorFaultEngine([
            ActuatorFault(
                name="cstr_outlet_capacity_loss", target="cstr_outflow",
                start_h=start, mode="capacity", capacity_fraction=0.20,
            )
        ])
        s = replace(base, events=(_event("cstr_outlet_capacity_loss", "actuator_fault", "cstr_outflow", onset_min, 0.20),))
        return EpisodeSpec(s, actuator_faults=af, name=name, allow_recovery_termination=False)


    if name == "reflux_pump_trip":
        pf = PumpFaultEngine([
            PumpFault(name="P-201A_trip", pump_id="P-201A", start_h=start)
        ])
        s = replace(base, events=(_event(
            "P-201A_trip", "pump_fault", "P-201A", onset_min, 0.0,
            metadata="duty reflux pump trips; P-201B available as standby"
        ),))
        return EpisodeSpec(s, pump_faults=pf, name=name, allow_recovery_termination=False)

    if name == "reflux_drum_leak":
        leak = 100.0
        tr = _permanent_step(0.0, start, leak)
        s = replace(
            base,
            drum_liquid_leak_kmol_h=tr,
            events=(_event("reflux_drum_leak", "process_fault", "drum_liquid_leak_kmol_h", onset_min, leak),),
        )
        return EpisodeSpec(s, name=name, allow_recovery_termination=False)

    if name == "reactor_pressure_sensor_low_bias":
        sf = SensorFaultEngine([
            SensorFault(
                name="reactor_PT_low_bias", target="cstr_pressure_bar",
                start_h=start, mode="bias", bias=-1.20,
            )
        ])
        s = replace(base, events=(_event("reactor_PT_low_bias", "sensor_fault", "cstr_pressure_bar", onset_min, -1.20),))
        return EpisodeSpec(s, sensor_faults=sf, name=name, allow_recovery_termination=False)

    if name == "condenser_cooling_loss":
        tr = _permanent_step(1.0, start, 0.20)
        s = replace(
            base,
            condenser_cooling_effectiveness=tr,
            events=(_event("condenser_cooling_loss", "utility_fault", "condenser_cooling_effectiveness", onset_min, 0.20),),
        )
        return EpisodeSpec(s, name=name, allow_recovery_termination=False)

    raise ValueError(f"Unknown curated episode '{name}'")


CURATED_EPISODES = (
    "h2_pcv_capacity_degradation",
    "h2_pcv_stuck_closed",
    "reactor_heater_stuck_high",
    "cstr_outlet_capacity_loss",
    "reflux_pump_trip",
    "reflux_drum_leak",
    "reactor_pressure_sensor_low_bias",
    "condenser_cooling_loss",
)


SURPRISE_FAULT_LIBRARY = (
    "h2_pcv_capacity_degradation",
    "reactor_heater_stuck_high",
    "cstr_outlet_capacity_loss",
    "reflux_pump_trip",
    "reflux_drum_leak",
    "reactor_pressure_sensor_low_bias",
    "condenser_cooling_loss",
    "reflux_valve_stiction",
    "top_temperature_sensor_freeze",
    "catalyst_activity_loss",
    "cstr_liquid_leak",
    "reboiler_liquid_leak",
)


def surprise_episode(duration_min: float = 180.0, seed: int = 20261004) -> EpisodeSpec:
    """Seeded demo episode with +/-10% disturbances and 0-2 whitelisted faults."""
    rng = np.random.default_rng(int(seed))
    dist = randomized_step_disturbance_scenario(duration_min=duration_min, seed=seed + 101)
    scenario = dist
    actuator_faults: list[ActuatorFault] = []
    sensor_faults: list[SensorFault] = []
    pump_faults: list[PumpFault] = []
    events = list(dist.events)

    n_faults = int(rng.integers(0, 3))
    choices = [str(x) for x in rng.choice(SURPRISE_FAULT_LIBRARY, size=n_faults, replace=False)] if n_faults else []
    for j, fault_name in enumerate(choices):
        onset_min = float(rng.uniform(30.0 + 15.0 * j, max(31.0 + 15.0 * j, duration_min - 35.0)))
        start = onset_min / 60.0
        if fault_name == "h2_pcv_capacity_degradation":
            final_frac = float(rng.uniform(0.15, 0.35))
            ramp_min = float(rng.uniform(6.0, 12.0))
            actuator_faults.append(ActuatorFault(
                name=f"surprise_{fault_name}", target="cstr_vent_valve",
                start_h=start, mode="capacity_ramp", capacity_fraction=final_frac,
                capacity_reference_value=1.0, ramp_duration_h=ramp_min/60.0,
            ))
            events.append(_event(
                f"surprise_{fault_name}", "actuator_fault", "cstr_vent_valve",
                onset_min, final_frac, metadata=f"capacity ramp over {ramp_min:.1f} min"
            ))
        elif fault_name == "h2_pcv_stuck_closed":
            actuator_faults.append(ActuatorFault(
                name=f"surprise_{fault_name}", target="cstr_vent_valve",
                start_h=start, mode="fixed", fixed_value=0.0,
            ))
            events.append(_event(f"surprise_{fault_name}", "actuator_fault", "cstr_vent_valve", onset_min, 0.0))
        elif fault_name == "reactor_heater_stuck_high":
            actuator_faults.append(ActuatorFault(
                name=f"surprise_{fault_name}", target="cstr_heater_factor",
                start_h=start, mode="fixed", fixed_value=1.0,
            ))
            events.append(_event(f"surprise_{fault_name}", "actuator_fault", "cstr_heater_factor", onset_min, 1.0))
        elif fault_name == "cstr_outlet_capacity_loss":
            frac = float(rng.uniform(0.15, 0.45))
            actuator_faults.append(ActuatorFault(
                name=f"surprise_{fault_name}", target="cstr_outflow",
                start_h=start, mode="capacity", capacity_fraction=frac,
            ))
            events.append(_event(f"surprise_{fault_name}", "actuator_fault", "cstr_outflow", onset_min, frac))
        elif fault_name == "reactor_pressure_sensor_low_bias":
            bias = -float(rng.uniform(0.8, 1.3))
            sensor_faults.append(SensorFault(
                name=f"surprise_{fault_name}", target="cstr_pressure_bar",
                start_h=start, mode="bias", bias=bias,
            ))
            events.append(_event(f"surprise_{fault_name}", "sensor_fault", "cstr_pressure_bar", onset_min, bias))
        elif fault_name == "reflux_pump_trip":
            pump_faults.append(PumpFault(
                name="surprise_P-201A_trip", pump_id="P-201A", start_h=start
            ))
            events.append(_event(
                "surprise_P-201A_trip", "pump_fault", "P-201A", onset_min, 0.0,
                metadata="duty reflux pump trip"
            ))
        elif fault_name == "reflux_drum_leak":
            leak = float(rng.uniform(75.0, 110.0))
            scenario = replace(scenario, drum_liquid_leak_kmol_h=_permanent_step(0.0, start, leak))
            events.append(_event(f"surprise_{fault_name}", "process_fault", "drum_liquid_leak_kmol_h", onset_min, leak))
        elif fault_name == "condenser_cooling_loss":
            factor = float(rng.uniform(0.1, 0.4))
            scenario = replace(
                scenario, condenser_cooling_effectiveness=_permanent_step(1.0, start, factor)
            )
            events.append(_event(
                f"surprise_{fault_name}", "utility_fault", "condenser_cooling_effectiveness", onset_min, factor
            ))
        elif fault_name == "reflux_valve_stiction":
            band = float(rng.uniform(0.05, 0.12))
            actuator_faults.append(ActuatorFault(
                name=f"surprise_{fault_name}", target="reflux_flow",
                start_h=start, mode="stiction", stiction_band_fraction=band,
            ))
            events.append(_event(f"surprise_{fault_name}", "actuator_fault", "reflux_flow", onset_min, band))
        elif fault_name == "top_temperature_sensor_freeze":
            sensor_faults.append(SensorFault(
                name=f"surprise_{fault_name}", target="top_temperature_K",
                start_h=start, mode="freeze",
            ))
            events.append(_event(f"surprise_{fault_name}", "sensor_fault", "top_temperature_K", onset_min, None))
        elif fault_name == "catalyst_activity_loss":
            loss = float(rng.uniform(0.25, 0.50))
            rf = ReactionRateFault(
                name=f"surprise_{fault_name}", start_h=start, final_loss_fraction=loss,
                ramp_duration_h=10.0/60.0, full_duration_h=20.0/60.0,
            )
            scenario = replace(scenario, catalyst_activity=CatalystActivityTrajectory([rf]))
            events.append(ScenarioEvent(
                name=f"surprise_{fault_name}", kind="process_fault", target="catalyst_activity",
                start_h=start, end_h=rf.end_h, magnitude=loss, metadata="10 min ramp then full loss",
            ))
        elif fault_name == "cstr_liquid_leak":
            leak = float(rng.uniform(20.0, 45.0))
            scenario = replace(scenario, cstr_liquid_leak_kmol_h=_permanent_step(0.0, start, leak))
            events.append(_event(f"surprise_{fault_name}", "process_fault", "cstr_liquid_leak_kmol_h", onset_min, leak))
        elif fault_name == "reboiler_liquid_leak":
            leak = float(rng.uniform(20.0, 45.0))
            scenario = replace(scenario, reboiler_liquid_leak_kmol_h=_permanent_step(0.0, start, leak))
            events.append(_event(f"surprise_{fault_name}", "process_fault", "reboiler_liquid_leak_kmol_h", onset_min, leak))

    scenario = replace(
        scenario,
        name=f"surprise_seed_{seed}",
        events=tuple(sorted(events, key=lambda e: e.start_h)),
    )
    return EpisodeSpec(
        scenario=scenario,
        actuator_faults=ActuatorFaultEngine(actuator_faults),
        sensor_faults=SensorFaultEngine(sensor_faults),
        pump_faults=PumpFaultEngine(pump_faults),
        name=scenario.name,
        allow_recovery_termination=False,
    )
