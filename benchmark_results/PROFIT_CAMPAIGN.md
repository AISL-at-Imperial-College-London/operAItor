# Fault campaign: profit with and without the agents

Agents: preset `rule_twin` (offline rule proposer + digital twin + validate/reprompt; PCA-only trigger).
Faults start at 10 min. Each fault is lifted 10 min after the time the plant would have tripped (probe run, faults never lifted, nobody acting), or 30 min after its onset if the probe does not trip. Window = max(120 min, last lift + 60 min).
Trip penalty (shutdown + startup): $20,000; after a trip the plant earns nothing for the rest of the window.

| id | group | faults | would trip at (probe) | lifted at | window | passive: trip (SIF) | agents: trip (SIF) | nominal | passive | agents | saving |
|---|---|---|---|---|---:|---|---|---:|---:|---:|---:|
| S01 | single | reflux_pump_trip | 24.4 min | 34.4 | 120 | column dry-out (MODEL_LIMIT, no SIF) at 24.4 min | no trip | $1,445 | -$20,186 | $1,489 | $21,674 |
| S02 | single | bottoms_pump_trip | 48.8 min | 58.8 | 120 | SIF trip RB_L_HH at 48.8 min | no trip | $1,445 | -$20,312 | $1,441 | $21,753 |
| S03 | single | distillate_pump_trip | 22.4 min | 32.4 | 120 | SIF trip DR_L_HH at 22.4 min | no trip | $1,445 | -$20,763 | $1,435 | $22,198 |
| S04 | single | feed_pump_trip | 72.1 min | 82.1 | 142 | SIF trip DR_L_LL at 72.1 min | no trip | $1,717 | -$16,920 | $1,877 | $18,797 |
| S05 | single | cstr_outlet_capacity_loss | no trip | 40 | 120 | no trip (SIF R_L_HH latched, reset) | no trip (SIF R_L_HH latched, reset) | $1,445 | $1,233 | $1,630 | $397 |
| S06 | single | h2_pcv_capacity_degradation | 162.8 min | 172.8 | 233 | SIF trip RB_L_HH at 162.8 min (also R_P_HH) | no trip (SIF R_P_HH latched, reset) | $2,799 | -$21,029 | $2,808 | $23,836 |
| S07 | single | reactor_heater_stuck_high | no trip | 40 | 120 | no trip (SIF R_T_HH latched, reset) | no trip (SIF R_T_HH latched, reset) | $1,445 | $1,317 | $1,341 | $24 |
| S08 | single | condenser_cooling_loss | 21.5 min | 31.5 | 120 | SIF trip DR_L_LL at 21.5 min | no trip | $1,445 | -$20,253 | $372 | $20,625 |
| S09 | single | reflux_drum_leak | 38.6 min | 48.6 | 120 | SIF trip DR_L_LL at 38.6 min | no trip | $1,445 | -$21,502 | -$5,336 | $16,166 |
| S10 | single | reactor_pressure_sensor_low_bias | 185.3 min | 195.3 | 255 | SIF trip RB_L_HH at 185.3 min (also R_P_HH) | no trip (SIF R_P_HH latched, reset) | $3,068 | -$20,758 | $3,082 | $23,840 |
| D01 | pair_indep | reactor_pressure_sensor_low_bias + bottoms_pump_trip | 50.0 min | 60, 60 | 120 | SIF trip RB_L_HH at 50.0 min (also R_P_HH) | no trip (SIF R_P_HH latched, reset) | $1,445 | -$20,294 | $1,458 | $21,752 |
| D02 | pair_indep | reactor_heater_stuck_high + distillate_pump_trip | 21.9 min | 31.9, 31.9 | 120 | SIF trip DR_L_HH at 21.9 min | no trip | $1,445 | -$20,721 | $1,390 | $22,112 |
| D03 | pair_indep | h2_pcv_capacity_degradation + reflux_pump_trip | 29.4 min | 39.4, 39.4 | 120 | column dry-out (MODEL_LIMIT, no SIF) at 29.4 min (SIF R_P_HH) | no trip | $1,445 | -$20,068 | $1,498 | $21,566 |
| D04 | pair_indep | reactor_pressure_sensor_low_bias + reflux_drum_leak | 40.6 min | 50.6, 50.6 | 120 | SIF trip DR_L_LL at 40.6 min | no trip (SIF R_P_HH latched, reset) | $1,445 | -$21,479 | -$6,790 | $14,690 |
| C01 | pair_coupled | condenser_cooling_loss + reflux_pump_trip | 28.3 min | 38.3, 38.3 | 120 | column dry-out (MODEL_LIMIT, no SIF) at 28.3 min | no trip | $1,445 | -$19,995 | $482 | $20,478 |
| C02 | pair_coupled | h2_pcv_capacity_degradation + reactor_pressure_sensor_low_bias | 161.1 min | 171.1, 171.1 | 231 | SIF trip RB_L_HH at 161.1 min (also R_P_HH) | no trip (SIF R_P_HH latched, reset) | $2,781 | -$21,040 | $2,885 | $23,925 |
| C03 | pair_coupled | reflux_drum_leak + distillate_pump_trip | 44.6 min | 54.6, 54.6 | 120 | SIF trip DR_L_LL at 44.6 min | no trip | $1,445 | -$22,148 | -$5,718 | $16,431 |
| T01 | triple_none | h2_pcv_capacity_degradation + distillate_pump_trip + bottoms_pump_trip | 24.9 min | 34.9, 34.9, 34.9 | 120 | SIF trip DR_L_HH at 24.9 min (also R_P_HH) | no trip | $1,445 | -$21,043 | $1,445 | $22,488 |
| T02 | triple_2plus1 | condenser_cooling_loss + reactor_pressure_sensor_low_bias + reflux_pump_trip | 28.3 min | 38.3, 38.3, 38.3 | 120 | column dry-out (MODEL_LIMIT, no SIF) at 28.3 min | no trip | $1,445 | -$19,997 | $482 | $20,479 |
| T03 | triple_all | condenser_cooling_loss + reflux_pump_trip + reflux_drum_leak | 27.2 min | 37.2, 37.2, 37.2 | 120 | column dry-out (MODEL_LIMIT, no SIF) at 27.2 min | SIF trip DR_L_LL at 21.9 min | $1,445 | -$20,465 | -$19,684 | $781 |

| group | n | passive trips | agent trips | passive profit | agents profit | saving |
|---|---:|---:|---:|---:|---:|---:|
| single | 10 | 8 | 0 | -$159,173 | $10,139 | $169,312 |
| pair_indep | 4 | 4 | 0 | -$82,562 | -$2,444 | $80,118 |
| pair_coupled | 3 | 3 | 0 | -$63,183 | -$2,351 | $60,832 |
| triple_none | 1 | 1 | 0 | -$21,043 | $1,445 | $22,488 |
| triple_2plus1 | 1 | 1 | 0 | -$19,997 | $482 | $20,479 |
| triple_all | 1 | 1 | 1 | -$20,465 | -$19,684 | $781 |

**Plant stops: without agents 18/20 (13 SIF trips + 5 column dry-outs with no SIF); with agents 1/20 (1 SIF trip, 0 dry-outs).**
**Campaign profit: nominal $33,485, passive -$366,423, agents -$12,413; saving from the agents $354,010.**
## How to read these numbers

- **The saving is dominated by the trip penalty.** 17 stops avoided x USD 20,000 = USD 340,000 of the
  USD 354,010; the remaining USD 14,010 is operating margin. The penalty (shutdown 5k + startup 15k) is an
  assumption in `benchmark/economics.py`; the conclusion "the agents avoid 17 of 18 stops" does not
  depend on it, the dollar figure does.
- **Column dry-outs** (S01, D03, C01, T02, T03 without agents) are not SIF trips: the top tray runs
  dry after loss of reflux and the simulator stops (README: MODEL_LIMIT, outside the model's valid
  region). No SIF exists for it; it is counted as an unplanned shutdown with the same penalty.
- **T03 is physically unrecoverable:** the 100 kmol/h drum leak exceeds what the crippled condenser can
  still condense, so the drum empties whatever the reflux. The DR_L_LL SIF trips the plant safely.
- **Drum-leak scenarios** (S09, D04, C03) stay negative with agents: while the leak is active it
  loses ~100 kmol/h of product. Avoiding the trip still saves USD 15-16k each.
- **Profit integrates flows, not inventory changes.** A scenario can briefly beat the nominal run
  (e.g. S04) when hold-up drawn down during the fault is sold afterwards.
- **Agents = offline rule proposer + oracle digital twin** (preset `rule_twin`, no LLM calls). The twin
  knows the present plant exactly (but not when faults clear); a real twin would be less accurate.
- **Alarm lists at the end:** with agents, 17/20 runs end with an empty list. S10 and C02 keep R_P_H/R_P_HH
  that came on at the moment the sensor bias was lifted, long after the agents last acted, so they
  are left for the operator by design. T03 is a real trip. Without agents, all 20 runs end with
  alarms standing.
- **Determinism:** the final round reproduced the previous round's profits exactly (all 20).
