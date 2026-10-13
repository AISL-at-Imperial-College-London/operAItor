# Fault campaign: profit with and without the agents

Every agent action comes from the LLM (no rule-based agent). Configurations: `lucky` = LLM, no validation (feeling lucky); `judge` = LLM + LLM judge; `llmtwin` = LLM + digital twin; `admit` = LLM planning + ADMIT gates (T0-T4, simulated); `admit_nosim` = LLM planning + ADMIT gates (T0-T3, no simulation).
Faults start at 10 min. Each fault is lifted 10 min after the time the plant would have tripped (probe run, faults never lifted, nobody acting), or 30 min after its onset if the probe does not trip. Window = max(120 min, last lift + 60 min).
Trip penalty (shutdown + startup): $20,000; after a trip the plant earns nothing for the rest of the window. Profit includes the change in liquid hold-up valued at the IPA price.

| id | faults | would trip at | no agents | LLM, no validation (feeling lucky) | LLM + LLM judge | LLM + digital twin | LLM planning + ADMIT gates (T0-T4, simulated) | LLM planning + ADMIT gates (T0-T3, no simulation) | nominal |
|---|---|---|---|---|---|---|---|---|---:|
| S01 | reflux_pump_trip | 16.9 min | -$19,198 · SIF trip COL_L_LL at 16.9 min | $1,446 · no stop | $1,446 · no stop | $1,446 · no stop | $1,446 · no stop | $1,446 · no stop | $1,449 |
| S02 | bottoms_pump_trip | 48.7 min | -$19,279 · SIF trip RB_L_HH at 48.7 min | $1,449 · no stop | $1,449 · no stop | $1,449 · no stop | $1,449 · no stop | $1,449 · no stop | $1,449 |
| S03 | distillate_pump_trip | 22.4 min | -$19,849 · SIF trip DR_L_HH at 22.4 min | $1,449 · no stop | $1,449 · no stop | $1,449 · no stop | $1,449 · no stop | $1,450 · no stop | $1,449 |
| S04 | feed_pump_trip | 81.1 min | -$19,731 · SIF trip DR_L_LL at 81.1 min | $1,813 · no stop | $1,813 · no stop | $1,813 · no stop | $971 · no stop | $1,813 · no stop | $1,822 |
| S05 | cstr_outlet_capacity_loss | no trip | $775 · no trip (SIF R_L_HH latched, reset) | $980 · no stop | $980 · no stop | $997 · no stop | $754 · no stop | $743 · no stop | $1,449 |
| S06 | h2_pcv_capacity_degradation | 177.8 min | -$20,042 · SIF trip RB_L_HH at 177.8 min (also R_P_HH) | $2,312 · no stop | $938 · no stop | $2,963 · no stop | $3,203 · no stop | $3,123 · no stop | $2,987 |
| S07 | reactor_heater_stuck_high | no trip | $1,478 · no trip (SIF R_T_HH latched, reset) | $1,599 · no stop | $1,347 · no stop | $1,498 · no stop | $1,501 · no stop | $1,309 · no stop | $1,449 |
| S08 | condenser_cooling_loss | 23.2 min | -$21,023 · SIF trip DR_L_LL at 23.2 min | -$21,016 · stop 23.2 | -$21,015 · stop 23.2 | -$21,008 · stop 23.2 | -$21,023 · stop 23.2 | -$21,010 · stop 22.8 | $1,449 |
| S09 | reflux_drum_leak | 46.2 min | -$23,491 · SIF trip DR_L_LL at 46.2 min | -$23,757 · stop 48.9 | -$23,680 · stop 50.4 | -$23,841 · stop 49.5 | -$23,491 · stop 46.2 | -$8,382 · no stop | $1,449 |
| S10 | reactor_pressure_sensor_low_bias | 197.5 min | -$19,797 · SIF trip RB_L_HH at 197.5 min (also R_P_HH) | $3,331 · no stop | $2,324 · no stop | $3,156 · no stop | $3,575 · no stop | $3,607 · no stop | $3,234 |
| D01 | reactor_pressure_sensor_low_bias + bottoms_pump_trip | 49.9 min | -$19,320 · SIF trip RB_L_HH at 49.9 min (also R_P_HH) | $1,515 · no stop | $1,351 · no stop | $1,509 · no stop | $1,454 · no stop | $1,454 · no stop | $1,449 |
| D02 | reactor_heater_stuck_high + distillate_pump_trip | 21.9 min | -$19,834 · SIF trip DR_L_HH at 21.9 min | $1,361 · no stop | $1,361 · no stop | $1,300 · no stop | $1,505 · no stop | $1,505 · no stop | $1,449 |
| D03 | h2_pcv_capacity_degradation + reflux_pump_trip | 21.9 min | -$19,142 · SIF trip COL_L_LL at 21.9 min (also R_P_HH) | $1,547 · no stop | $1,528 · no stop | $1,544 · no stop | $1,462 · no stop | $1,460 · no stop | $1,449 |
| D04 | reactor_pressure_sensor_low_bias + reflux_drum_leak | 46.7 min | -$23,344 · SIF trip DR_L_LL at 46.7 min (also R_P_HH) | -$23,220 · stop 46.2 | -$23,363 · stop 47.3 | -$22,985 · stop 44.2 | -$23,425 · stop 47.9 | -$23,425 · stop 47.9 | $1,449 |
| C01 | condenser_cooling_loss + reflux_pump_trip | 20.4 min | -$19,707 · SIF trip COL_L_LL at 20.4 min | -$21,028 · stop 23.6 | -$21,028 · stop 23.6 | -$21,025 · stop 23.6 | -$19,707 · stop 20.4 | -$21,022 · stop 23.1 | $1,449 |
| C02 | h2_pcv_capacity_degradation + reactor_pressure_sensor_low_bias | 176.5 min | -$20,052 · SIF trip RB_L_HH at 176.5 min (also R_P_HH) | $3,021 · no stop | $2,659 · no stop | $3,110 · no stop | $3,433 · no stop | $3,286 · no stop | $2,975 |
| C03 | reflux_drum_leak + distillate_pump_trip | 46.8 min | -$23,548 · SIF trip DR_L_LL at 46.8 min | -$23,542 · stop 45.6 | -$8,761 · no stop | -$23,765 · stop 48.9 | -$23,548 · stop 46.8 | -$8,383 · no stop | $1,449 |
| T01 | h2_pcv_capacity_degradation + distillate_pump_trip + bottoms_pump_trip | 24.9 min | -$19,832 · SIF trip DR_L_HH at 24.9 min (also R_P_HH) | $463 · no stop | $1,181 · no stop | $82 · no stop | $1,462 · no stop | $1,463 · no stop | $1,449 |
| T02 | condenser_cooling_loss + reactor_pressure_sensor_low_bias + reflux_pump_trip | 20.4 min | -$19,708 · SIF trip COL_L_LL at 20.4 min | -$21,017 · stop 23.6 | -$20,942 · stop 22.7 | -$21,017 · stop 23.6 | -$19,708 · stop 20.4 | -$21,051 · stop 23.6 | $1,449 |
| T03 | condenser_cooling_loss + reflux_pump_trip + reflux_drum_leak | 19.3 min | -$20,234 · SIF trip COL_L_LL at 19.3 min | -$21,067 · stop 18.9 | -$21,067 · stop 18.9 | -$21,069 · stop 18.9 | -$20,234 · stop 19.3 | -$21,071 · stop 18.7 | $1,449 |

**Without agents: 18/20 plant stops (18 SIF trips + 0 model-limit stops with no SIF), profit -$364,878; nominal $34,202.**

## Agent configurations

| configuration | runs | plant stops | profit | saving vs no agents | LLM calls | LLM cost | decision time | mean per decision | mean LLM latency | twin time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LLM, no validation (feeling lucky) | 20/20 | 7 | -$132,361 | $232,517 | 96 | $0.08 | 12.1 min | 7.7 s | 7.6 s | 0.0 min |
| LLM + LLM judge | 20/20 | 6 | -$120,030 | $244,848 | 285 | $0.22 | 31.7 min | 19.8 s | 6.7 s | 0.0 min |
| LLM + digital twin | 20/20 | 7 | -$132,394 | $232,484 | 279 | $0.23 | 147.3 min | 117.9 s | 6.5 s | 116.9 min |
| LLM planning + ADMIT gates (T0-T4, simulated) | 20/20 | 7 | -$127,472 | $237,406 | 691 | $0.65 | 271.0 min | 98.5 s | 8.1 s | 180.1 min |
| LLM planning + ADMIT gates (T0-T3, no simulation) | 20/20 | 5 | -$100,236 | $264,642 | 422 | $0.40 | 50.2 min | 28.1 s | 7.8 s | 0.0 min |

## Rejected proposals by failure type

| configuration | decisions | admit rejected | judge rejected | not executable | repeat | twin not restored | twin trip |
|---|---:|---:|---:|---:|---:|---:|---:|
| LLM, no validation (feeling lucky) | — | 0 | 0 | 1 | 0 | 0 | 0 |
| LLM + LLM judge | — | 0 | 47 | 2 | 1 | 0 | 0 |
| LLM + digital twin | — | 0 | 0 | 0 | 22 | 175 | 75 |
| LLM planning + ADMIT gates (T0-T4, simulated) | — | 304 | 0 | 111 | 39 | 0 | 0 |
| LLM planning + ADMIT gates (T0-T3, no simulation) | — | 184 | 0 | 63 | 4 | 0 | 0 |
