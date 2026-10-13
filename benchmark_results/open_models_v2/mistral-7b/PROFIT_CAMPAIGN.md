# Fault campaign: profit with and without the agents

Every agent action comes from the LLM (no rule-based agent). Configurations: `lucky` = LLM, no validation (feeling lucky); `judge` = LLM + LLM judge; `llmtwin` = LLM + digital twin; `admit` = LLM planning + ADMIT gates (T0-T4, simulated); `admit_nosim` = LLM planning + ADMIT gates (T0-T3, no simulation).
Faults start at 10 min. Each fault is lifted 10 min after the time the plant would have tripped (probe run, faults never lifted, nobody acting), or 30 min after its onset if the probe does not trip. Window = max(120 min, last lift + 60 min).
Trip penalty (shutdown + startup): $20,000; after a trip the plant earns nothing for the rest of the window. Profit includes the change in liquid hold-up valued at the IPA price.

| id | faults | would trip at | no agents | LLM, no validation (feeling lucky) | LLM + LLM judge | LLM + digital twin | LLM planning + ADMIT gates (T0-T4, simulated) | LLM planning + ADMIT gates (T0-T3, no simulation) | nominal |
|---|---|---|---|---|---|---|---|---|---:|
| S01 | reflux_pump_trip | 16.9 min | -$19,198 · SIF trip COL_L_LL at 16.9 min | $1,446 · no stop | $1,446 · no stop | $1,446 · no stop | $1,446 · no stop | $1,446 · no stop | $1,449 |
| S02 | bottoms_pump_trip | 48.7 min | -$19,279 · SIF trip RB_L_HH at 48.7 min | $1,449 · no stop | $1,449 · no stop | $1,449 · no stop | $1,449 · no stop | $1,449 · no stop | $1,449 |
| S03 | distillate_pump_trip | 22.4 min | -$19,849 · SIF trip DR_L_HH at 22.4 min | $1,449 · no stop | $1,449 · no stop | $1,449 · no stop | $1,449 · no stop | $1,449 · no stop | $1,449 |
| S04 | feed_pump_trip | 81.1 min | -$19,731 · SIF trip DR_L_LL at 81.1 min | $1,813 · no stop | $1,813 · no stop | $1,813 · no stop | $1,813 · no stop | $1,813 · no stop | $1,822 |
| S05 | cstr_outlet_capacity_loss | no trip | $775 · no trip (SIF R_L_HH latched, reset) | $755 · no stop | $755 · no stop | $841 · no stop | $754 · no stop | $754 · no stop | $1,449 |
| S06 | h2_pcv_capacity_degradation | 177.8 min | -$20,042 · SIF trip RB_L_HH at 177.8 min (also R_P_HH) | $1,725 · no stop | $1,725 · no stop | $2,559 · no stop | $3,233 · no stop | $3,233 · no stop | $2,987 |
| S07 | reactor_heater_stuck_high | no trip | $1,478 · no trip (SIF R_T_HH latched, reset) | $1,496 · no stop | $1,496 · no stop | $1,496 · no stop | $1,501 · no stop | $1,501 · no stop | $1,449 |
| S08 | condenser_cooling_loss | 23.2 min | -$21,023 · SIF trip DR_L_LL at 23.2 min | -$21,011 · stop 23.2 | -$21,011 · stop 23.2 | -$21,022 · stop 23.3 | -$21,023 · stop 23.2 | -$21,023 · stop 23.2 | $1,449 |
| S09 | reflux_drum_leak | 46.2 min | -$23,491 · SIF trip DR_L_LL at 46.2 min | -$22,895 · stop 40.5 | -$22,895 · stop 40.5 | -$23,483 · stop 46.2 | -$23,491 · stop 46.2 | -$23,491 · stop 46.2 | $1,449 |
| S10 | reactor_pressure_sensor_low_bias | 197.5 min | -$19,797 · SIF trip RB_L_HH at 197.5 min (also R_P_HH) | $3,586 · no stop | $3,586 · no stop | $3,597 · no stop | $3,577 · no stop | $3,577 · no stop | $3,234 |
| D01 | reactor_pressure_sensor_low_bias + bottoms_pump_trip | 49.9 min | -$19,320 · SIF trip RB_L_HH at 49.9 min (also R_P_HH) | $1,454 · no stop | $1,454 · no stop | $1,454 · no stop | $1,454 · no stop | $1,454 · no stop | $1,449 |
| D02 | reactor_heater_stuck_high + distillate_pump_trip | 21.9 min | -$19,834 · SIF trip DR_L_HH at 21.9 min | $1,504 · no stop | $1,504 · no stop | $1,504 · no stop | $1,505 · no stop | $1,505 · no stop | $1,449 |
| D03 | h2_pcv_capacity_degradation + reflux_pump_trip | 21.9 min | -$19,142 · SIF trip COL_L_LL at 21.9 min (also R_P_HH) | $1,449 · no stop | $1,449 · no stop | $1,449 · no stop | $1,460 · no stop | $1,460 · no stop | $1,449 |
| D04 | reactor_pressure_sensor_low_bias + reflux_drum_leak | 46.7 min | -$23,344 · SIF trip DR_L_LL at 46.7 min (also R_P_HH) | -$23,100 · stop 44.6 | -$23,100 · stop 44.6 | -$23,453 · stop 48.1 | -$23,425 · stop 47.9 | -$23,425 · stop 47.9 | $1,449 |
| C01 | condenser_cooling_loss + reflux_pump_trip | 20.4 min | -$19,707 · SIF trip COL_L_LL at 20.4 min | -$21,026 · stop 23.6 | -$21,026 · stop 23.6 | -$21,045 · stop 23.6 | -$19,707 · stop 20.4 | -$21,045 · stop 23.6 | $1,449 |
| C02 | h2_pcv_capacity_degradation + reactor_pressure_sensor_low_bias | 176.5 min | -$20,052 · SIF trip RB_L_HH at 176.5 min (also R_P_HH) | $3,324 · no stop | $3,324 · no stop | $3,283 · no stop | $3,326 · no stop | $3,322 · no stop | $2,975 |
| C03 | reflux_drum_leak + distillate_pump_trip | 46.8 min | -$23,548 · SIF trip DR_L_LL at 46.8 min | -$23,050 · stop 42.0 | -$23,018 · stop 41.7 | -$23,490 · stop 46.4 | -$23,494 · stop 46.3 | -$23,494 · stop 46.3 | $1,449 |
| T01 | h2_pcv_capacity_degradation + distillate_pump_trip + bottoms_pump_trip | 24.9 min | -$19,832 · SIF trip DR_L_HH at 24.9 min (also R_P_HH) | $1,451 · no stop | $1,451 · no stop | $1,451 · no stop | $1,466 · no stop | $1,466 · no stop | $1,449 |
| T02 | condenser_cooling_loss + reactor_pressure_sensor_low_bias + reflux_pump_trip | 20.4 min | -$19,708 · SIF trip COL_L_LL at 20.4 min | -$21,049 · stop 23.6 | -$21,049 · stop 23.6 | -$21,050 · stop 23.6 | -$19,708 · stop 20.4 | -$21,024 · stop 23.2 | $1,449 |
| T03 | condenser_cooling_loss + reflux_pump_trip + reflux_drum_leak | 19.3 min | -$20,234 · SIF trip COL_L_LL at 19.3 min | -$21,055 · stop 18.9 | -$21,055 · stop 18.9 | -$21,067 · stop 18.9 | -$20,234 · stop 19.3 | -$21,063 · stop 18.9 | $1,449 |

**Without agents: 18/20 plant stops (18 SIF trips + 0 model-limit stops with no SIF), profit -$364,878; nominal $34,202.**

## Agent configurations

| configuration | runs | plant stops | profit | saving vs no agents | LLM calls | LLM cost | decision time | mean per decision | mean LLM latency | twin time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LLM, no validation (feeling lucky) | 20/20 | 7 | -$130,285 | $234,593 | 95 | $0.00 | 108.4 min | 75.6 s | 67.7 s | 0.0 min |
| LLM + LLM judge | 20/20 | 7 | -$130,253 | $234,625 | 181 | $0.00 | 94.8 min | 66.2 s | 31.4 s | 0.0 min |
| LLM + digital twin | 20/20 | 7 | -$130,819 | $234,059 | 313 | $0.00 | 554.1 min | 405.4 s | 86.1 s | 104.7 min |
| LLM planning + ADMIT gates (T0-T4, simulated) | 20/20 | 7 | -$126,649 | $238,229 | 411 | $0.00 | 831.1 min | 361.4 s | 111.6 s | 110.7 min |
| LLM planning + ADMIT gates (T0-T3, no simulation) | 20/20 | 7 | -$130,136 | $234,742 | 375 | $0.00 | 451.7 min | 196.4 s | 102.7 s | 0.0 min |

## Rejected proposals by failure type

| configuration | decisions | admit rejected | malformed reply | no commands | not executable | repeat | twin not restored | twin trip |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| LLM, no validation (feeling lucky) | — | 0 | 1 | 0 | 9 | 0 | 0 | 0 |
| LLM + LLM judge | — | 0 | 0 | 0 | 9 | 0 | 0 | 0 |
| LLM + digital twin | — | 0 | 0 | 0 | 15 | 104 | 113 | 76 |
| LLM planning + ADMIT gates (T0-T4, simulated) | — | 48 | 6 | 5 | 60 | 39 | 0 | 0 |
| LLM planning + ADMIT gates (T0-T3, no simulation) | — | 33 | 6 | 0 | 64 | 5 | 0 | 0 |
