"""Regression tests for the master-console episode loading/injection workflow."""
import operator_console as oc


def test_episode_dropdown_loads_curated_episode_immediately():
    oc.scenario_select.value = "reflux_pump_trip"
    assert oc.env.spec.name == "reflux_pump_trip"
    assert len(oc.env.spec.scenario.events) == 1
    assert oc.env.spec.scenario.events[0].name == "P-201A_trip"


def test_variability_and_surprise_dropdowns_build_non_nominal_schedules():
    oc.scenario_select.value = "variability"
    assert oc.env.spec.name.startswith("random_step_disturbances_seed_")
    assert len(oc.env.spec.scenario.events) >= 1

    oc.scenario_select.value = "surprise"
    assert oc.env.spec.name.startswith("surprise_seed_")
    # The surprise generator always includes one or more normal disturbances;
    # faults may independently be zero, one, or two.
    assert any(e.kind == "disturbance" for e in oc.env.spec.scenario.events)


def test_master_injection_resets_a_completed_episode_before_arming_event():
    oc.running = False
    oc.scenario_select.value = "nominal"
    from integrated_plant.outcome import EpisodeStatus
    oc.env.outcomes.state.status = EpisodeStatus.COMPLETED
    oc.env.outcomes.state.reason = "test_complete"
    assert oc.env.done

    oc.disturbance_select.value = "Cooling-water temperature"
    oc.disturbance_direction.value = "1.10"
    oc._inject_disturbance()

    assert not oc.env.done
    assert oc.env.t_h == 0.0
    assert any(e.target == "cooling_water_temperature_K" for e in oc.env.runtime_inputs.events)
