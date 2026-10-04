"""Examples showing how to configure faults without changing the plant equations.

These are not run by the base scenario. They are templates for later dataset
production.
"""

from integrated_plant.scenario import (
    Scenario, PiecewiseConstant, ActuatorFault, ActuatorFaultEngine
)


def catalyst_poisoning_scenario():
    return Scenario(
        name="catalyst_poisoning",
        duration_h=5.0,
        throughput_fraction=PiecewiseConstant([(0.0, 1.0)]),
        catalyst_activity=PiecewiseConstant([
            (0.0, 1.0),
            (2.0, 0.70),
        ]),
    )


def reflux_capacity_loss():
    return ActuatorFaultEngine([
        ActuatorFault(
            target="reflux_flow",
            start_h=2.0,
            mode="capacity",
            capacity_fraction=0.60,
        )
    ])


def steam_capacity_loss():
    return ActuatorFaultEngine([
        ActuatorFault(
            target="steam_factor",
            start_h=2.0,
            mode="capacity",
            capacity_fraction=0.65,
        )
    ])


def frozen_cstr_outflow():
    return ActuatorFaultEngine([
        ActuatorFault(
            target="cstr_outflow",
            start_h=2.0,
            mode="freeze",
        )
    ])


def frozen_reflux():
    return ActuatorFaultEngine([
        ActuatorFault(
            target="reflux_flow",
            start_h=2.0,
            mode="freeze",
        )
    ])
