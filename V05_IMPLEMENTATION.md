# v0.5.2 implementation summary

This prototype implements the minimum complete autonomous-operation environment discussed during design.

## Implemented architecture

- Stateful `EpisodeRunner` with `reset()` / `step(action)` turn-based execution.
- Multi-rate clock: 0.1 min physical/SIF/alarm update, 1 min regulatory BPCS update, 5 min agent decision interval.
- Process time freezes while an external actor is deciding.
- `PassiveActor` placeholder returns `NO_ACTION`.
- Fixed-horizon batch output; terminated runs are padded with `episode.active = 0`.
- Controller request -> supervisory request -> SIF arbitration -> actual actuator after faults.
- Explicit outcome states: COMPLETED, RECOVERED, SAFE_TRIP, DAMAGE, MODEL_LIMIT, TIMEOUT, NUMERICAL_FAILURE.

## Implemented physical changes

- CSTR environmental heat loss.
- CSTR normal H2 PCV, independent emergency BDV and passive PSV surrogate.
- One lumped dynamic condenser/top-pressure state.
- Normal column-pressure PI controller manipulating a condenser cooling-water valve.
- Condenser capacity limited by available cooling duty, with cooling effectiveness kept separate from valve position.
- Physical liquid-leak terms on CSTR, reboiler and reflux drum.
- Emergency CSTR cooling path available to the T-HH SIF.

The nominal steady state remains numerically closed; the regression test gives a scaled residual norm below 1e-9.

## Implemented disturbances

The random normal-variability generator independently selects one or more of:

- feed temperature
- cooling-water temperature
- steam-header pressure
- CSTR heating-utility temperature
- ambient temperature

Each selected disturbance is a 15 min rectangular pulse at exactly +/-10% of nominal. Start times are independently randomized and overlaps are allowed. A seed makes the full schedule reproducible.

## Implemented fault mechanisms

### Actuators

- capacity loss
- freeze
- fixed/stuck value
- simplified stiction

### BPCS sensors

- bias
- freeze

### Process/utility

- catalyst/reaction-activity loss
- CSTR liquid leak
- reboiler liquid leak
- reflux-drum liquid leak
- condenser cooling degradation/loss

Independent SIF measurements are architecturally separate from BPCS measurements, although v0.5 currently maps them to the same true plant state.

## SIFs implemented

- reactor T-HH
- reactor P-HH
- reactor P-HHH escalation
- reactor L-HH
- reactor L-LL
- reboiler L-HH / L-LL
- reflux-drum L-HH / L-LL
- column P-HH

The P-HH action preserves feed because the benchmark reaction is endothermic; feed reduction is only used at P-HHH escalation.

## Curated prototype scenarios

1. H2 PCV stuck closed
2. reactor heater stuck high
3. CSTR outlet capacity loss
4. reflux-drum leak
5. reactor pressure transmitter low bias
6. condenser cooling loss

All start from the same nominal state. Default episode horizon is 180 min; fault onset defaults to 30 min.

## Validation run included with the package

A common 90 min regression horizon was used for the saved prototype check.

| Scenario | Outcome | Terminal time | SIFs observed |
|---|---|---:|---|
| Nominal hold | COMPLETED | 90 min | none |
| Random +/-10% disturbances | COMPLETED | 90 min | none |
| H2 PCV stuck closed | SAFE_TRIP | 32 min | R_P_HH, R_P_HHH |
| Reactor heater stuck high | TIMEOUT | 90 min | R_T_HH |
| CSTR outlet capacity loss | TIMEOUT | 90 min | R_L_HH |
| Reflux-drum leak | SAFE_TRIP | 59 min | DR_L_LL |
| Reactor pressure sensor low bias | TIMEOUT | 90 min | R_P_HH |
| Condenser cooling loss | SAFE_TRIP | 39 min | COL_P_HH |

`TIMEOUT` in a fault episode means the passive actor did not produce recovery and the event did not meet a configured terminal damage/trip criterion before the horizon. It is not a numerical failure.

The selected random-disturbance validation run used seed `20261004` and generated three overlapping-capable pulses: steam pressure +10%, feed temperature -10%, and cooling-water temperature -10%. No SIF fired in that run.

## Automated tests

`python -m unittest -v test_model.py test_v05.py`

At packaging time: **22 tests passed**.

The tests include the original v0.4 regression tests plus direct checks of every v0.5 SIF action, disturbance timing/magnitude, sensor bias/freeze, actuator stiction, column-pressure response to cooling loss, and turn-based episode stepping.

## Deliberately deferred

- rigorous dry-tray / phase disappearance treatment
- flooding, weeping and entrainment
- detailed pumps/NPSH/cavitation
- rigorous PSV sizing/choked gas flow
- full shutdown/restart physics
- manual/automatic controller mode transfer
- real human UI
- real autonomous action agent

The action API is present so those additions do not require changing the episode execution contract.


## v0.5.1 H2 pressure update

- Added 22.75 m3 connected H2-system gas volume to the 2.25 m3 nominal reactor headspace, giving about 25 m3 effective pressure capacitance.
- Added `capacity_ramp` actuator faults and a curated `h2_pcv_capacity_degradation` scenario (100% -> 20% maximum opening over 10 min).
- Retained instantaneous `h2_pcv_stuck_closed` as a severe SIF/BDV/PSV protection test.
- Operator console now plots at 0.5 min display intervals while the pressure/SIF dynamics are resolved every 0.1 min.


## v0.5.2 column pressure-control update

- Added a BPCS column-pressure PI controller (`column_pressure_bar` -> `condenser_cooling_valve`).
- Nominal cooling-water-valve position is 0.60; valve limits are 0..1.
- Rescaled condenser UA to 225000 kJ/h/K so the 60% nominal valve position preserves the prior nominal condenser duty.
- Split cooling into `condenser_cooling_valve` (manipulated/faultable final element) and `condenser_cooling_effectiveness` (utility/equipment degradation).
- Updated the column P-HH SIF to force the valve to 100% without overriding a degraded cooling utility.
- Updated the operator console to show pressure PIC SP/OP/actual valve and separate cooling effectiveness.
- Added regression tests for nominal cooling headroom, PIC direction, cooling-effectiveness loss, and SIF action.
- Automated test suite at packaging time: **31 passed**.


## v0.5.4 operator/action and spare-pump pass

- Added persistent restricted operator/agent actions for throughput, SP changes, AUTO/MANUAL, manual MV output, auxiliary BDV/emergency cooling, alarm acknowledgement, SIF reset, and pump switchover.
- Added PI tracking for bumpless AUTO/MANUAL transfer.
- Added A/B duty/standby pumps for feed, CSTR outlet, reflux, distillate and bottoms services.
- Added pump-fault engine and priority-1 equipment-trip alarms.
- Added curated `reflux_pump_trip` episode and pump faults to the surprise-fault whitelist.
- Reworked the web console sidebar into separate operator-action and master-injection sections.
- Verified the master runtime disturbance path: a +10% cooling-water temperature injection changes 298.15 K to 327.965 K on the next process advance and remains active for 15 simulated minutes.
- Added machine-readable `EpisodeRunner.action_catalog()` for later agent tool/system-prompt generation.
- Regression suite: 37 tests passing.


## v0.5.4 operator overview

Added a live Bokeh Overview tab using `static/process_overview.png` as the process faceplate. Live scalar overlays, alarm/SIF lamps and pump A/B state are updated from `EpisodeRunner` state without changing the plant, control, fault, SIF or scenario equations.

## v0.5.6 console episode-selection fix

- Episode dropdown changes now immediately instantiate the selected nominal,
  variability, surprise, or curated `EpisodeSpec`; the console can no longer
  continue `nominal_hold` merely because the user forgot a second load click.
- Horizon, seed and trip-termination configuration changes rebuild the episode
  while paused.
- Pressing Run after a completed/terminated episode reloads the selected episode
  before starting.
- Manual master injections into a completed episode automatically reset the
  selected episode before the event is added.
- Master ground truth now shows the full pending/active/done event schedule with
  event times.
- Overview/trend tabs moved above the scalar faceplate/status region.
- Added console workflow regression tests; full suite: 40 passing tests.

