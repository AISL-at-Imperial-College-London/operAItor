# Integrated CSTR + distillation autonomous-operation simulator — v0.5.6 prototype

v0.5 keeps the compact liquid-CSTR + binary-column benchmark from v0.4, but adds the minimum infrastructure needed for abnormal-operation and autonomous-operation experiments.

> **New:** async LangGraph monitoring/action agents with PCA fault detection and a
> React web console connected to the live plant — see
> [ASYNC_AGENTS_AND_CONSOLE.md](ASYNC_AGENTS_AND_CONSOLE.md).

The design goal is **causal realism rather than high-fidelity equipment modelling**: faults should produce believable process signatures, the BPCS should react, independent SIFs should be able to override commands, final-element faults should still be able to defeat those commands, and an external actor should receive discrete decision opportunities.

## What is new in v0.5 / v0.5.1 / v0.5.2 / v0.5.3 / v0.5.4 / v0.5.6

### Stateful episode environment

`EpisodeRunner` provides a turn-based interface:

```python
obs = env.reset()
while not env.done:
    action = actor.act(obs)
    obs, outcome = env.step(action)
```

Default multi-rate timing in v0.5.3:

- physical integration / SIF / operator-alarm update: **0.1 min = 6 s**
- regulatory BPCS controller update: **1 min**
- actor decision interval: **5 min**
- master operator-console display step: **0.5 min**
- default episode horizon: **180 min**

The three clocks are independent configuration values.  The process clock is still
frozen while an external human or agent is deciding what to do.

The simulated process clock advances only inside `step()`. It is therefore frozen while an LLM or human decides what to do.

The initial v0.5 actor is `PassiveActor`, which always returns `NO_ACTION`. This is the baseline against which later autonomous or human actions can be compared.




### Console workflow fix (v0.5.6)

The episode selector now **loads the selected episode immediately**.  The older
console required a second click on *Load / reset selected episode*, which made it
easy to leave `nominal_hold` running after choosing Variability, Surprise, or a
curated case.  The load/reset button remains available for an explicit restart.

The master ground-truth panel now shows the **complete scheduled event list**
with pending/active/done state and event times.  This is particularly useful for
random normal-variability runs where the first 15-min disturbance may begin well
after the start of the episode.

Master fault/disturbance injection into a completed episode now automatically
resets the currently selected episode before arming the injection, so an event can
never be silently added to a simulation whose clock can no longer advance.

The Overview/trend tabs are now at the top of the main workspace.  The status
banner, reactor/column scalar faceplates, ground-truth summary and pump status
have been moved **below the tabbed displays** to make the live graphics easier to
see.

### Live process overview graphic (v0.5.4)

The web operator console now includes an **Overview** tab ahead of Reactor, Column, Controls/MVs and Utilities. It embeds the process faceplate graphic in `static/process_overview.png` and overlays live simulator values, operator-alarm lamps, executive SIF lamps and duty/standby pump states.

- Scalar values on the graphic update from the same measured/actual signals used elsewhere in the console.
- Ordinary alarm lamps are white when normal and flash yellow while the corresponding operator alarm is active.
- Executive SIF lamps are white when clear and flash red while the corresponding SIF latch is active.
- Pump A/B badges show RUN/STBY and flash red when the selected pump is failed.
- The overview is display-only: all operator actions and master fault/disturbance injections remain in the existing side panels.

### Operator/agent action interface (v0.5.3)

The generic supervisory override has been replaced, for normal operator/agent use,
by a persistent restricted action interface.  The actor can now:

- take `NO_ACTION`;
- set the plant throughput target;
- change the setpoint of any named regulatory loop;
- transfer loops between `AUTO` and `MANUAL`;
- set the manual output of a loop while it is in MANUAL;
- command the H2 emergency BDV and emergency reactor cooling;
- acknowledge alarms and request SIF reset;
- start/select a standby pump or return to a healthy primary pump.

AUTO/MANUAL transfer uses PI tracking/back-calculation for bumpless transfer.
Setpoints, modes, manual outputs, throughput targets and auxiliary commands persist
until explicitly changed.  The machine-readable `env.action_catalog()` and
`env.observation()["controllers"]` expose the allowed loops, bounds and modes for
agent tool/system-prompt construction.  Fault/environment variables are not part
of this authority model.

### Duty/standby pumps (v0.5.3)

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

### Master-console injection fixes (v0.5.3)

The sidebar now clearly separates **operator actions** from **master ground-truth
injections**.  Manual disturbances are applied through the runtime event engine and
are shown immediately in the ground-truth panel; the message reports the exact
value change and notes that the process must advance before the trend changes.
A dedicated `Load / reset selected episode` button makes scenario changes explicit.
Pump trips can also be injected from the master panel.

### Episode types

The prototype provides:

- `nominal_episode()` — constant 100% production + sensor noise only
- `normal_variability_episode()` — constant production plus randomized normal disturbances
- `curated_episode(name)` — one designed abnormal-operation scenario
- `surprise_episode(seed=...)` — seeded, hidden combination of disturbances and 0–2 whitelisted faults

The legacy 100% -> 85% -> 70% -> 85% -> 100% production campaign is retained for PCA/FDD data generation and is no longer the default autonomous-operation episode.

### Random normal disturbances

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

### Physical additions

#### CSTR heat loss

A simple environmental loss is added:

`Q_loss = UA_loss * max(T_reactor - T_ambient, 0)`

The nominal heater bias is recalculated so the original 100 kmol/h / 70% conversion / 3 bar design point is preserved.

#### H2 pressure capacitance and protection hierarchy

The H2 pressure state now uses an **effective gas-system capacitance** rather than
only the small reactor headspace.  At the nominal 50 kmol liquid inventory the
physical reactor headspace is 2.25 m3; v0.5.1 adds 22.75 m3 of connected gas-system
volume, giving an effective pressure volume of about **25 m3**.  This is a low-order
representation of connected vent/disengagement/piping volume and does not change
the 6 m3 liquid-vessel geometry used for reactor level.

The old single H2 vent path is separated conceptually into:

- **PCV** — normal pressure-control valve, commanded by the reactor pressure PI loop
- **BDV** — independent actively commanded emergency blowdown valve
- **PSV** — passive mechanical pressure-relief surrogate

The total H2 removal is the sum of the three paths. The PSV model is only a low-order benchmark surrogate; it is not a relief-sizing calculation.

#### Lumped column pressure

A single differential condenser/top-pressure state has been added. Pressure changes according to the imbalance between top vapour generation and available condensation. Condenser capacity is limited by available cooling duty.

This is sufficient to create the intended causal chain:

`cooling loss -> condensation capacity loss -> column pressure rise -> column PSHH`

without adding compressible vapour inventories to every tray.


#### Column pressure regulatory control (v0.5.2)

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

#### Vessel leaks

Physical liquid leak terms are available for:

- CSTR liquid inventory
- reboiler liquid inventory
- reflux-drum liquid inventory

Leaks remove mass, component inventory and corresponding enthalpy through the physical balances rather than directly perturbing the state vector.

### Requested -> safety-commanded -> actual actuator chain

Every episode now distinguishes:

1. BPCS/controller request (`ctrl.*`)
2. supervisory/actor request (`sup.*`)
3. safety-arbitrated command (`safe.*`)
4. actual plant input after actuator faults (`act.*`)

The fault layer is downstream of the SIF layer. A safety system can therefore correctly demand that a valve open while a stuck final element still fails to move.

### SIF / interrupt layer

The first safety layer is deterministic, independent and latched. Current synthetic trip thresholds are benchmark values, not engineered SIL settings.

| Cause | Initial threshold | Main effects |
|---|---:|---|
| Reactor T-HH | 348 K | heater OFF, emergency cooling ON; feed retained |
| Reactor P-HH | 4.00 bar | heater OFF, PCV 100%, BDV OPEN; feed retained |
| Reactor P-HHH | 4.35 bar | above actions + feed reduced to <=40%; plant-trip latch |
| Reactor L-HH | 72 kmol | fresh feed OFF, heater OFF |
| Reactor L-LL | 20 kmol | heater OFF, reactor outlet stopped |
| Reboiler L-LL | 12.08 kmol | steam OFF, bottoms withdrawal stopped |
| Reboiler L-HH | 51.76 kmol | steam OFF, column feed stopped; plant-trip latch |
| Drum L-LL | 10.59 kmol | reflux/distillate stopped, steam/feed stopped; plant-trip latch |
| Drum L-HH | 45.37 kmol | steam/feed stopped; plant-trip latch |
| Column P-HH | 1.35 bar | steam OFF, condenser CW valve forced to 100%, feed stopped; plant-trip latch |
| Column L-LL (dry tray) | 0.30 kmol lowest tray holdup | steam OFF, column feed / fresh feed / reactor heater stopped; plant-trip latch |

The pressure logic deliberately reflects the endothermic benchmark reaction: the first pressure-high action maximizes gas removal **without cutting feed**. Feed reduction is reserved for the escalated P-HHH stage.

SIF tags are independent of BPCS tags in the software architecture. In v0.5 they read the same underlying true process state; later they can receive independent sensor faults/common-cause faults.

For the current prototype, SIFs designated as `plant_trip_latched` terminate the active physical episode as `SAFE_TRIP`. The output data are then padded to the requested fixed horizon with `episode.active = 0`. This avoids pushing the present nominal-range column model through a shutdown region it cannot yet represent reliably. `EpisodeSpec.terminate_on_plant_trip=False` can be used when later shutdown/recovery physics are added.

### Fault mechanisms implemented

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

The seeded surprise generator uses a bounded whitelist and never generates more than two simultaneous faults in v0.5.

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

The dry-tray / phase-disappearance problem is **not** solved in v0.5. If a future non-trip scenario drives tray holdup outside the defensible region, the environment should terminate as `MODEL_LIMIT` rather than treating the resulting equilibrium-stage solution as physically valid.

## Running one episode

Examples:

```bash
python run_v05_episode.py --mode nominal
python run_v05_episode.py --mode variability --seed 20261004
python run_v05_episode.py --mode curated --scenario h2_pcv_capacity_degradation
python run_v05_episode.py --mode curated --scenario h2_pcv_stuck_closed
python run_v05_episode.py --mode surprise --seed 42
```

The batch runner pads terminated episodes to the selected fixed horizon, but `EpisodeRunner.step()` immediately returns a terminal outcome for interactive use.

## Prototype validation

Run unit/regression tests:

```bash
python -m unittest -v test_model.py test_v05.py test_operator_layer.py
```

The v0.5.2 package passes **31 tests**.  These include checks for the 25 m3
effective H2 gas capacitance, the gradual-capacity fault mode, the
0.1/1/5 min multi-rate timing configuration, and the new column-pressure PIC / condenser cooling-valve architecture.

Run the quick column-pressure-controller check:

```bash
python run_column_pressure_controller_check.py
```

The packaged tuning gives about 1.009 bar maximum pressure for a +2 K cooling-water
temperature step in this check, with the cooling-water valve opening from about 60%
to about 64%.

Run the dynamic prototype check:

```bash
python run_v05_prototype_tests.py
```

Because v0.5.1 changes the pressure capacitance and the internal clock, the old
v0.5 one-minute dynamic CSVs are not shipped as current validation results.
`run_v05_prototype_tests.py` can regenerate the dynamic suite with the new
0.1 min plant/SIF clock.- `random_disturbance_schedule.csv`
- `prototype_test_report.json`
- one full episode CSV for each tested case

## Legacy PCA/FDD campaign

The v0.4 data-generation workflow remains available:

```bash
python run_base_scenario.py
python generate_fault_detection_data.py
```

The production-rate schedule remains useful for PCA training across a wider normal operating envelope, even though autonomous-operation episodes now default to constant nominal production.

## Current model boundary

v0.5 is not intended to be a rigorous startup/shutdown simulator. Deferred items include:

- rigorous liquid/vapour phase disappearance on dry trays
- flooding/weeping/entrainment
- detailed pump curves, NPSH and cavitation physics
- rigorous relief sizing/choked flow
- full cold startup from empty equipment
- detailed electrical/instrument-air systems
- a complete human operator model
- a real LLM/action agent

The physical DAE remains compact and transparent, and the existing backward-Euler nonlinear solve is retained as the reference integrator.

## Live master/operator interface

A Bokeh-based live operator console is included in `operator_console.py`. It displays process faceplates, live trends, operator alarm states, SIF status and a master fault/disturbance injection panel.

Run locally from a terminal:

```bash
bokeh serve --show operator_console.py
```

or run `run_operator_console.py` directly from Spyder. A simpler native Matplotlib monitor for Spyder is provided as `spyder_live_trends.py`.

See `OPERATOR_CONSOLE.md` for the alarm state model, initial setpoints and fault/disturbance injection controls.
