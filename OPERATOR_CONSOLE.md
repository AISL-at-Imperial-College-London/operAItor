# Master operator console

The v0.5.6 operator layer is intended as a **master/operator-training view** of the simulator. It uses the same `EpisodeRunner`, regulatory controllers, SIFs, fault engines and physical model as the batch runs.

## Overview faceplate

The first web-console tab is a live process overview. The background artwork is `static/process_overview.png`; Bokeh overlays the current simulator values rather than relying on the illustrative values baked into the image. Small local alarm lamps are white when normal and flash yellow while an operator alarm is active. The four executive alarm lamps shown on the graphic flash red when their corresponding SIF latch is active. Pump A/B badges follow the currently selected duty/standby equipment and indicate a selected failed pump with a red flash.

The faceplate is intentionally an operator display only. Scenario injection, sensor/actuator faults and utility disturbances remain confined to the Master injections panel and are not exposed as operator/agent actions.

## Recommended way to use it

### From Spyder

Set the working directory to this package and run:

```python
runfile('run_operator_console.py')
```

or simply open `run_operator_console.py` in Spyder and press **Run**. A local Bokeh server starts and the console opens in the default web browser at `http://localhost:5006/operator_console`.

For a simple Spyder-native Matplotlib monitor without the fault-injection panel, run `spyder_live_trends.py`. The configuration block at the top selects nominal, variability, surprise or curated runs.

### From a terminal / web browser

Install requirements once:

```bash
pip install -r requirements.txt
```

Then run:

```bash
bokeh serve --show operator_console.py
```

The simulation runs locally; no external web service is required.

## Console layout

The left-hand side has three deliberately separated sections: **Simulation control**, **Operator / agent actions**, and **Master injections**.

Simulation control provides:

- episode selection and seed
- run / pause
- step 1 simulated minute
- step 5 simulated minutes
- reset
- alarm acknowledge
- SIF reset request
- explicit loading/reset of the selected episode

The main area contains:

- CSTR faceplate
- distillation-column faceplate
- master/ground-truth panel
- an **Overview** tab with the process faceplate graphic, live scalar readings, flashing operator alarms, executive SIF lamps and dynamic duty/standby pump status
- live trends grouped into Reactor, Column, Controls/MVs and Utilities tabs
- active alarm table
- alarm event history

The process clock advances only when a simulation tick is executed. Pausing the console therefore freezes simulated process time.



## Operator / agent action panel

The operator panel uses the same restricted persistent action interface intended
for an autonomous agent.  It can change throughput, controller setpoints, AUTO /
MANUAL mode and manual outputs, command the H2 BDV or emergency cooling, and
start/select standby pumps.  These are distinct from the master fault-injection
controls.

Named loops currently exposed are `R_LIC`, `R_TIC`, `R_PIC`, `RB_LIC`, `DR_LIC`,
`COL_PIC`, `TOP_TIC`, and `BOT_TIC`.  The widget limits are populated from the
controller's advertised operator range.  Manual-output commands are rejected unless
the selected loop is in MANUAL.

Five duty/standby pump trains are visible.  Primary pumps are selected on reset; a
pump failure drops the corresponding actual flow to zero while the controller demand
remains visible.  The operator can start the healthy spare from the same panel.

## Column pressure-control faceplate

The distillation faceplate now exposes the normal column-pressure regulatory loop:

- PV: measured column/condenser pressure
- SP: nominal pressure setpoint (about 1.0 bar)
- OP: pressure-controller cooling-water-valve command
- actual valve position: after safety arbitration and actuator faults
- cooling effectiveness: independent utility/equipment effectiveness multiplier

The Controls/MVs trend plots both the PIC command and actual cooling-water-valve
position.  The Utilities tab plots cooling effectiveness separately.  This makes a
stuck cooling valve distinguishable from loss of cooling-water/condenser capacity.

## Disturbance injection

The master panel can inject a 15-minute step of **+10% or -10%** in:

- feed temperature
- cooling-water temperature
- steam-header pressure
- CSTR heating-utility temperature
- ambient temperature

These are process disturbances, not sensor noise and not faults.  After pressing **Inject disturbance now**, the master/ground-truth panel immediately shows the active runtime event.  If the simulation is paused, press **Run** or **Step** to advance process time and see the changed utility value in the trend.

## Fault injection

### Actuator faults

The console can inject:

- freeze
- capacity loss
- fixed output
- stiction

into selected plant actuators.

### Pump trips

Any primary or standby pump can be tripped from the master panel for a temporary or persistent duration.  Pump-trip alarms are generated when the currently selected pump has failed.

### Sensor faults

The console can inject:

- bias
- freeze

into selected BPCS measurements. SIF measurements remain independent in the current software architecture.

### Process / utility faults

The console can inject:

- catalyst activity loss
- CSTR liquid leak
- reboiler liquid leak
- reflux-drum liquid leak
- condenser cooling utility/effectiveness loss (the pressure PIC continues to command its valve)

Injected events can be temporary or persistent.

## Alarm model

Operator alarms are deliberately separate from SIFs. Alarms act on **BPCS measurements** and support acknowledgement; SIFs act on independent safety measurements and remain latched until their reset conditions are satisfied.

Alarm states are:

- `NORMAL`
- `ACTIVE_UNACK`
- `ACTIVE_ACK`
- `RTN_UNACK` — process returned to normal before the occurrence was acknowledged

Acknowledging an active alarm does **not** clear the alarm condition.

Initial benchmark alarm limits are:

| Alarm | Limit | Priority | Related SIF |
|---|---:|---:|---|
| Reactor P-H | 3.50 bar | 2 | P-HH 4.00 bar |
| Reactor P-HH | 3.80 bar | 1 | P-HH 4.00 bar |
| Reactor T-H | 342 K | 2 | T-HH 348 K |
| Reactor T-HH | 346 K | 1 | T-HH 348 K |
| Reactor level H | 62 kmol | 2 | L-HH 72 kmol |
| Reactor level HH | 68 kmol | 1 | L-HH 72 kmol |
| Reactor level L | 38 kmol | 2 | L-LL 20 kmol |
| Reactor level LL | 25 kmol | 1 | L-LL 20 kmol |
| Reboiler level HH | 48 kmol | 1 | L-HH 51.76 kmol |
| Reboiler level LL | 16 kmol | 1 | L-LL 12.08 kmol |
| Reflux drum level HH | 42 kmol | 1 | L-HH 45.37 kmol |
| Reflux drum level LL | 14 kmol | 1 | L-LL 10.59 kmol |
| Column P-H | 1.15 bar | 2 | P-HH 1.35 bar |
| Column P-HH | 1.28 bar | 1 | P-HH 1.35 bar |
| Distillate acetone purity L | 0.90 | 3 | none |
| Bottoms acetone H | 0.08 | 3 | none |

These are **prototype/operator-training setpoints**, not engineered plant alarm settings. The console is intended to help tune them.

## Initial validation

The operator-layer regression suite is `test_operator_layer.py`. Together with the existing tests, **40 tests currently pass** in v0.5.6, including direct tests of runtime disturbance injection, persistent setpoint/MANUAL actions, auxiliary commands, pump trips/spare switchover and pump-trip alarms.

A 180-minute nominal run and the seeded +/-10% normal-disturbance run (`seed=20261004`) were checked against the initial alarm settings and produced no nuisance alarm activations.

v0.5.3 retains the v0.5.1 pressure-sampling fix by using a **6 s physical/SIF/alarm clock**, a **1 min BPCS controller clock**, and an effective H2-system gas volume of about **25 m3**.  With the severe `h2_pcv_stuck_closed` case, the pressure excursion is now resolved through multiple samples before the 4.0 bar SIF threshold; the BDV then acts on the fast SIF clock.  This severe case remains primarily a protection-system test.  For human/agent intervention studies, use the new `h2_pcv_capacity_degradation` case, which develops over 10 min and creates a much larger diagnosis/action window.


## v0.5.6 alarm-lamp annunciation fix

The Overview faceplate now keeps ordinary operator alarms visibly annunciated through the full alarm state machine. Unacknowledged ACTIVE and returned-unacknowledged (RTN_UNACK) alarms flash yellow/dark-amber until acknowledgement; acknowledged active alarms remain steady amber; normal lamps are white. This fixes the case where a fast SIF response returned a PV below its operator alarm limit before the alarm was acknowledged, leaving the alarm visible in the table but no longer flashing on the faceplate.
