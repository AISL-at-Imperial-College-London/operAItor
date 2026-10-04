"""Quick closed-loop check for the v0.5.2 column pressure controller.

Runs 30 simulated minutes with a +2 K cooling-water temperature disturbance
from t=5 to 20 min.  The pressure PIC should open the condenser cooling-water
valve and keep column pressure close to its 1 bar setpoint.
"""
from __future__ import annotations

from integrated_plant import EpisodeRunner, EpisodeTiming, IntegratedPlantDAE, PassiveActor, nominal_episode
from integrated_plant.runtime import RuntimeInputEvent

plant = IntegratedPlantDAE()
env = EpisodeRunner(
    plant,
    nominal_episode(30.0),
    timing=EpisodeTiming(plant_step_min=0.1, decision_interval_min=5.0, control_interval_min=1.0),
)
env.runtime_inputs.add(RuntimeInputEvent(
    name="cw_plus_2K",
    kind="disturbance",
    target="cooling_water_temperature_K",
    start_h=5.0/60.0,
    end_h=20.0/60.0,
    mode="add",
    value=2.0,
))

actor = PassiveActor()
while not env.done:
    env.step(actor.act(env.observation()))

p = [float(r["true.column_pressure_bar"]) for r in env.records]
v = [float(r["act.condenser_cooling_valve"]) for r in env.records]
print(f"Column pressure setpoint : {env.controllers.sp.column_pressure_bar:.4f} bar")
print(f"Maximum column pressure  : {max(p):.4f} bar")
print(f"Maximum CW valve opening : {100*max(v):.1f}%")
print(f"Final column pressure    : {p[-1]:.4f} bar")
print(f"Final CW valve opening   : {100*v[-1]:.1f}%")
print(f"Episode status           : {env.outcomes.state.status.value}")
