# Plant model

Reference for the simulator in `operaitor/plant/`: equations, control and safety layers,
faults and episode outcomes. The SIF table below is checked by `tests/test_physics_consistency.py`.

The design goal is **causal realism rather than high-fidelity equipment modelling**: faults should produce believable process signatures, the BPCS should react, independent SIFs should be able to override commands, final-element faults should still be able to defeat those commands, and an external actor should receive discrete decision opportunities.

## Stateful episode environment

`EpisodeRunner` provides a turn-based interface:

```python
obs = env.reset()
while not env.done:
    action = actor.act(obs)
    obs, outcome = env.step(action)
```

Default multi-rate timing:

- physical integration / SIF / operator-alarm update: **0.1 min = 6 s**
- regulatory BPCS controller update: **1 min**
- actor decision interval: **5 min**
- master operator-console display step: **0.5 min**
- default episode horizon: **180 min**

The three clocks are independent configuration values.  The process clock is still
frozen while an external human or agent is deciding what to do.

The simulated process clock advances only inside `step()`. It is therefore frozen while an LLM or human decides what to do.

The baseline actor is `PassiveActor`, which always returns `NO_ACTION`. This is the baseline against which later autonomous or human actions can be compared.




## Duty/standby pumps

Five liquid-flow services now have A/B duty/standby pumps:

- `FEED`: P-101A / P-101B
- `CSTR_OUTLET`: P-102A / P-102B
- `REFLUX`: P-201A / P-201B
- `DISTILLATE`: P-202A / P-202B
- `BOTTOMS`: P-203A / P-203B

A failed selected pump transmits zero flow while the controller continues to demand
its requested flow.  The operator/agent can start the healthy standby.  Pump trips
are independent equipment faults and generate priority-1 operator alarms.  The new
curated episode `reflux_pump_trip` trips P-201A while P-201B remains available.

## Episode types

The prototype provides:

- `nominal_episode()` — constant 100% production + sensor noise only
- `normal_variability_episode()` — constant production plus randomized normal disturbances
- `curated_episode(name)` — one designed abnormal-operation scenario
- `surprise_episode(seed=...)` — seeded, hidden combination of disturbances and 0–2 whitelisted faults

The legacy 100% -> 85% -> 70% -> 85% -> 100% production campaign is retained for PCA/FDD data generation and is no longer the default autonomous-operation episode.

## Random normal disturbances

The first disturbance model follows a deliberately simple rule:

- randomly select a subset of disturbance variables
- each selected disturbance is a **15 min rectangular step**
- step magnitude is a **physical step** (since 2026-10-05): +/-3 K cooling-water and ambient
  temperature, +/-5 K fresh-feed and CSTR heating-utility temperature, +/-10 % steam-header
  pressure (`integrated_plant.scenario.DISTURBANCE_STEPS`). The earlier +/-10 % of the nominal
  value meant +/-30 K on absolute temperatures; `magnitude_fraction=0.10` reproduces it.
- start time is randomized
- different disturbances are initialized independently, so **overlap is allowed**
- the random seed makes the run reproducible

Current disturbance candidates:

- fresh-feed temperature
- cooling-water temperature
- steam-header pressure
- CSTR heating-utility temperature
- ambient temperature

Sensor noise remains a separate Gaussian measurement layer.

## Physical additions

### CSTR heat loss

A simple environmental loss is added:

`Q_loss = UA_loss * max(T_reactor - T_ambient, 0)`

The nominal heater bias is recalculated so the original 100 kmol/h / 70% conversion / 3 bar design point is preserved.

### H2 pressure capacitance and protection hierarchy

The H2 pressure state now uses an **effective gas-system capacitance** rather than
only the small reactor headspace.  At the nominal 50 kmol liquid inventory the
physical reactor headspace is 2.25 m3; the model adds 22.75 m3 of connected gas-system
volume, giving an effective pressure volume of about **25 m3**.  This is a low-order
representation of connected vent/disengagement/piping volume and does not change
the 6 m3 liquid-vessel geometry used for reactor level.

The old single H2 vent path is separated conceptually into:

- **PCV** — normal pressure-control valve, commanded by the reactor pressure PI loop
- **BDV** — independent actively commanded emergency blowdown valve
- **PSV** — passive mechanical pressure-relief surrogate

The total H2 removal is the sum of the three paths. The PSV model is only a low-order benchmark surrogate; it is not a relief-sizing calculation.

### Lumped column pressure

A single differential condenser/top-pressure state has been added. Pressure changes according to the imbalance between top vapour generation and available condensation. Condenser capacity is limited by available cooling duty.

This is sufficient to create the intended causal chain:

`cooling loss -> condensation capacity loss -> column pressure rise -> column PSHH`

without adding compressible vapour inventories to every tray.


### Column pressure regulatory control

The lumped column-pressure state now has a normal BPCS pressure controller.  The
controller manipulates a **condenser cooling-water valve** rather than directly
changing an abstract heat-transfer multiplier:

`column pressure -> PIC -> condenser cooling-water valve -> condenser duty`

The nominal valve position is **60%**, leaving upward cooling authority.  The
condenser UA was rescaled from 135000 to **225000 kJ/h/K**, so 60% valve opening
reproduces the previous nominal condenser duty.  Condenser cooling effectiveness
is now a separate exogenous/fault multiplier.  This cleanly distinguishes:

- cooling-water temperature disturbances, which the PIC should reject when it has authority;
- cooling-valve actuator faults, which affect the requested/actual valve position;
- cooling-utility or condenser degradation, represented by reduced cooling effectiveness.

The column P-HH SIF forces the cooling-water valve to 100%, trips steam, and stops
feed.  It does **not** magically restore lost utility effectiveness.

### Vessel leaks

Physical liquid leak terms are available for:

- CSTR liquid inventory
- reboiler liquid inventory
- reflux-drum liquid inventory

Leaks remove mass, component inventory and corresponding enthalpy through the physical balances rather than directly perturbing the state vector.

## Requested -> safety-commanded -> actual actuator chain

Every episode now distinguishes:

1. BPCS/controller request (`ctrl.*`)
2. supervisory/actor request (`sup.*`)
3. safety-arbitrated command (`safe.*`)
4. actual plant input after actuator faults (`act.*`)

The fault layer is downstream of the SIF layer. A safety system can therefore correctly demand that a valve open while a stuck final element still fails to move.

## SIF / interrupt layer

The first safety layer is deterministic, independent and latched. Current synthetic trip thresholds are benchmark values, not engineered SIL settings.

| Cause | Initial threshold | Main effects |
|---|---:|---|
| Reactor T-HH | 348 K | heater OFF, emergency cooling ON; feed retained |
| Reactor P-HH | 4.00 bar | heater OFF, PCV 100%, BDV OPEN; feed retained |
| Reactor P-HHH | 4.35 bar | above actions + feed reduced to <=40%; plant-trip latch |
| Reactor L-HH | 72 kmol | fresh feed OFF, heater OFF |
| Reactor L-LL | 20 kmol | heater OFF, reactor outlet stopped |
| Reboiler L-LL | 12.08 kmol | steam OFF, bottoms withdrawal stopped |
| Reboiler L-HH | 51.76 kmol | column feed, fresh feed and reactor heater stopped (boil-up kept); plant-trip latch |
| Drum L-LL | 10.59 kmol | reflux/distillate stopped, steam/feed stopped; plant-trip latch |
| Drum L-HH | 45.37 kmol | steam/feed stopped; plant-trip latch |
| Column P-HH | 1.35 bar | steam OFF, condenser CW valve forced to 100%, feed stopped; plant-trip latch |
| Column L-LL (dry tray) | 0.30 kmol lowest tray holdup | steam OFF, column feed / fresh feed / reactor heater stopped; plant-trip latch |

The pressure logic deliberately reflects the endothermic benchmark reaction: the first pressure-high action maximizes gas removal **without cutting feed**. Feed reduction is reserved for the escalated P-HHH stage.

SIF tags are independent of BPCS tags in the software architecture. For now they read the same underlying true process state; later they can receive independent sensor faults/common-cause faults.

For the current prototype, SIFs designated as `plant_trip_latched` terminate the active physical episode as `SAFE_TRIP`. The output data are then padded to the requested fixed horizon with `episode.active = 0`. This avoids pushing the present nominal-range column model through a shutdown region it cannot yet represent reliably. `EpisodeSpec.terminate_on_plant_trip=False` can be used when later shutdown/recovery physics are added.

## Fault mechanisms implemented

Actuator faults:

- `freeze`
- `capacity`
- `capacity_ramp` — gradual loss of achievable actuator capacity
- `fixed`
- `stiction`

BPCS sensor faults:

- `bias`
- `freeze`

Process/utility faults available through scenario trajectories include:

- catalyst/reaction-activity loss
- CSTR/reboiler/reflux-drum liquid leaks
- condenser cooling degradation/loss

The seeded surprise generator uses a bounded whitelist and never generates more than two simultaneous faults .

## Curated prototype episodes

`CURATED_EPISODES` currently contains eight cases:

1. `h2_pcv_capacity_degradation` — PCV achievable opening degrades from 100% to 20% over 10 min; intended as the main operator/agent intervention case
2. `h2_pcv_stuck_closed` — instantaneous full closure; retained as a severe protection-system test
3. `reactor_heater_stuck_high`
4. `cstr_outlet_capacity_loss`
5. `reflux_pump_trip` — duty reflux pump P-201A trips; P-201B is available as standby
6. `reflux_drum_leak`
7. `reactor_pressure_sensor_low_bias`
8. `condenser_cooling_loss` — cooling utility/condenser effectiveness loss; the pressure PIC still drives its valve

They all start from the same nominal steady state and, by default, introduce the initiating fault at 30 min.

## Episode outcomes

The outcome evaluator distinguishes:

- `RUNNING`
- `COMPLETED` — nominal/normal-variability horizon reached
- `RECOVERED`
- `SAFE_TRIP`
- `DAMAGE`
- `MODEL_LIMIT`
- `TIMEOUT`
- `NUMERICAL_FAILURE`

Initial consequence checks include reactor overpressure exposure, column overpressure exposure, reboiler low-inventory + heat-on exposure, CSTR overflow, and a minimum tray-holdup model-validity threshold.

The dry-tray / phase-disappearance problem is **not** solved . If a future non-trip scenario drives tray holdup outside the defensible region, the environment should terminate as `MODEL_LIMIT` rather than treating the resulting equilibrium-stage solution as physically valid.

## Current model boundary

The model is not intended to be a rigorous startup/shutdown simulator. Deferred items include:

- rigorous liquid/vapour phase disappearance on dry trays
- flooding/weeping/entrainment
- detailed pump curves, NPSH and cavitation physics
- rigorous relief sizing/choked flow
- full cold startup from empty equipment
- detailed electrical/instrument-air systems
- a complete human operator model
- a real LLM/action agent

The physical DAE remains compact and transparent, and the existing backward-Euler nonlinear solve is retained as the reference integrator.

