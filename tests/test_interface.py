from acetone_plant.plant import (
    EpisodeRunner, EpisodeTiming, IntegratedPlantDAE, PumpFault,
    RuntimeInputEvent, SupervisoryAction, curated_episode, nominal_episode,
)


def _env(spec=None):
    plant = IntegratedPlantDAE()
    return EpisodeRunner(
        plant,
        nominal_episode(60) if spec is None else spec,
        timing=EpisodeTiming(plant_step_min=0.1, decision_interval_min=0.5, control_interval_min=1.0),
    )


def test_runtime_disturbance_is_applied_next_tick():
    env = _env()
    base = env.nominal.cooling_water_temperature_K
    env.runtime_inputs.add(RuntimeInputEvent(
        name="cw_up", kind="disturbance", target="cooling_water_temperature_K",
        start_h=env.t_h, end_h=env.t_h + 15/60, mode="multiply", value=1.10,
    ))
    env.step()
    assert abs(env.last_actual.cooling_water_temperature_K - 1.10 * base) < 1e-9


def test_operator_setpoint_and_manual_output_are_persistent():
    env = _env()
    old = env.controllers.sp.cstr_temperature_K
    result = env.apply_action(SupervisoryAction(
        setpoints={"R_TIC": old - 2.0},
        modes={"R_TIC": "MANUAL"},
        manual_outputs={"R_TIC": 0.25},
    ))
    assert result.ok
    env.step()
    snap = env.controllers.access_snapshot(env.last_measured)["R_TIC"]
    assert snap["mode"] == "MANUAL"
    assert abs(snap["setpoint"] - (old - 2.0)) < 1e-12
    assert abs(env.last_controller_request.cstr_heater_factor - 0.25) < 1e-9
    env.step()
    assert abs(env.last_controller_request.cstr_heater_factor - 0.25) < 1e-9


def test_operator_throughput_target_persists():
    env = _env()
    assert env.apply_action(SupervisoryAction(throughput_target=55.0)).ok
    env.step()
    assert abs(env.last_actual.fresh_ipa_feed - 55.0) < 1e-9
    env.step()
    assert abs(env.last_actual.fresh_ipa_feed - 55.0) < 1e-9


def test_reflux_pump_trip_and_spare_restore_flow():
    env = _env(curated_episode("reflux_pump_trip", duration_min=70, onset_min=1.0))
    # Advance through the pump trip.
    for _ in range(3):
        env.step()
    assert env.t_h * 60 >= 1.5
    assert env.last_actual.reflux_flow == 0.0
    status = env.pumps.snapshot(env.t_h, env.spec.pump_faults)["REFLUX"]
    assert status["active_pump"] == "P-201A"
    assert status["selected_failed"]

    result = env.apply_action(SupervisoryAction(start_spares=("REFLUX",)))
    assert result.ok
    env.step()
    status = env.pumps.snapshot(env.t_h, env.spec.pump_faults)["REFLUX"]
    assert status["active_pump"] == "P-201B"
    assert not status["selected_failed"]
    assert env.last_actual.reflux_flow > 0.0


def test_auxiliary_bdv_command_is_restricted_and_persistent():
    env = _env()
    good = env.apply_action(SupervisoryAction(auxiliary_commands={"H2_BDV": 0.3}))
    assert good.ok
    env.step()
    assert abs(env.last_actual.cstr_bdv_valve - 0.3) < 1e-9
    bad = env.apply_action(SupervisoryAction(auxiliary_commands={"H2_BDV": 1.5}))
    assert not bad.ok
    env.step()
    assert abs(env.last_actual.cstr_bdv_valve - 0.3) < 1e-9

def test_pump_trip_generates_operator_alarm():
    env = _env(curated_episode("reflux_pump_trip", duration_min=20, onset_min=1.0))
    for _ in range(3):
        env.step()
    assert env.alarms.status["P201_TRIP"].active
