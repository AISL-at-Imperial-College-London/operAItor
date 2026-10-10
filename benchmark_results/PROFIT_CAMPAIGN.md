# Fault campaign: profit with and without the agents

Every agent action comes from the LLM (no rule-based agent). Configurations: `lucky` = LLM, no validation (feeling lucky); `judge` = LLM + LLM judge; `llmtwin` = LLM + digital twin; `admit` = LLM planning + ADMIT gates (T0-T4, simulated); `admit_nosim` = LLM planning + ADMIT gates (T0-T3, no simulation).
Faults start at 10 min. Each fault is lifted 10 min after the time the plant would have tripped (probe run, faults never lifted, nobody acting), or 30 min after its onset if the probe does not trip. Window = max(120 min, last lift + 60 min).
Trip penalty (shutdown + startup): $20,000; after a trip the plant earns nothing for the rest of the window. Profit includes the change in liquid hold-up valued at the IPA price.

| id | faults | would trip at | no agents | LLM, no validation (feeling lucky) | LLM + LLM judge | LLM + digital twin | LLM planning + ADMIT gates (T0-T4, simulated) | LLM planning + ADMIT gates (T0-T3, no simulation) | nominal |
|---|---|---|---|---|---|---|---|---|---:|
| S01 | reflux_pump_trip | 16.9 min | -$19,198 · SIF trip COL_L_LL at 16.9 min | $1,441 · no stop | $1,456 · no stop | $1,441 · no stop | $1,044 · no stop | $1,441 · no stop | $1,449 |
| S02 | bottoms_pump_trip | 48.7 min | -$19,279 · SIF trip RB_L_HH at 48.7 min | $1,553 · no stop | $1,529 · no stop | $1,448 · no stop | $1,467 · no stop | $1,450 · no stop | $1,449 |
| S03 | distillate_pump_trip | 22.4 min | -$19,849 · SIF trip DR_L_HH at 22.4 min | $1,450 · no stop | $473 · no stop | $1,449 · no stop | $1,450 · no stop | $1,449 · no stop | $1,449 |
| S04 | feed_pump_trip | 81.1 min | -$19,731 · SIF trip DR_L_LL at 81.1 min | $1,813 · no stop | $1,813 · no stop | $1,813 · no stop | $1,970 · no stop | $1,627 · no stop | $1,822 |
| S05 | cstr_outlet_capacity_loss | no trip | $775 · no trip (SIF R_L_HH latched, reset) | $568 · no stop | -$11 · no stop | $651 · no stop | $753 · no stop | $899 · no stop | $1,449 |
| S06 | h2_pcv_capacity_degradation | 177.8 min | -$20,042 · SIF trip RB_L_HH at 177.8 min (also R_P_HH) | -$22,000 · stop 237.1 | -$18,916 · stop 246.1 | $848 · no stop | -$12,347 · no stop | -$5,545 · no stop | $2,987 |
| S07 | reactor_heater_stuck_high | no trip | $1,478 · no trip (SIF R_T_HH latched, reset) | $1,276 · no stop | $1,305 · no stop | $1,632 · no stop | $1,358 · no stop | $1,410 · no stop | $1,449 |
| S08 | condenser_cooling_loss | 23.2 min | -$21,023 · SIF trip DR_L_LL at 23.2 min | -$21,030 · stop 23.3 | -$20,999 · stop 23.2 | -$21,008 · stop 23.2 | -$21,023 · stop 23.2 | -$21,027 · stop 23.3 | $1,449 |
| S09 | reflux_drum_leak | 46.2 min | -$23,491 · SIF trip DR_L_LL at 46.2 min | -$21,041 · stop 23.3 | -$22,394 · stop 34.6 | -$7,311 · no stop | -$23,491 · stop 46.2 | -$5,433 · no stop | $1,449 |
| S10 | reactor_pressure_sensor_low_bias | 197.5 min | -$19,797 · SIF trip RB_L_HH at 197.5 min (also R_P_HH) | $544 · no stop | -$31,537 · stop 239.6 | $3,138 · no stop | $3,365 · no stop | $3,459 · no stop | $3,234 |
| D01 | reactor_pressure_sensor_low_bias + bottoms_pump_trip | 49.9 min | -$19,320 · SIF trip RB_L_HH at 49.9 min (also R_P_HH) | $127 · no stop | $1,438 · no stop | $1,450 · no stop | $1,475 · no stop | $1,442 · no stop | $1,449 |
| D02 | reactor_heater_stuck_high + distillate_pump_trip | 21.9 min | -$19,834 · SIF trip DR_L_HH at 21.9 min | $1,448 · no stop | -$19,819 · stop 98.7 | $1,590 · no stop | $1,538 · no stop | $1,443 · no stop | $1,449 |
| D03 | h2_pcv_capacity_degradation + reflux_pump_trip | 21.9 min | -$19,142 · SIF trip COL_L_LL at 21.9 min (also R_P_HH) | -$2,415 · no stop | $1,182 · no stop | $505 · no stop | $1,453 · no stop | $1,454 · no stop | $1,449 |
| D04 | reactor_pressure_sensor_low_bias + reflux_drum_leak | 46.7 min | -$23,344 · SIF trip DR_L_LL at 46.7 min (also R_P_HH) | -$22,966 · stop 41.6 | -$22,707 · stop 43.5 | -$6,349 · no stop | -$8,676 · no stop | -$8,352 · no stop | $1,449 |
| C01 | condenser_cooling_loss + reflux_pump_trip | 20.4 min | -$19,707 · SIF trip COL_L_LL at 20.4 min | -$20,809 · stop 21.7 | -$21,035 · stop 23.6 | -$21,007 · stop 23.6 | -$19,707 · stop 20.4 | -$21,045 · stop 23.6 | $1,449 |
| C02 | h2_pcv_capacity_degradation + reactor_pressure_sensor_low_bias | 176.5 min | -$20,052 · SIF trip RB_L_HH at 176.5 min (also R_P_HH) | -$17,989 · stop 222.8 | -$18,048 · stop 231.1 | $2,224 · no stop | $3,314 · no stop | $3,276 · no stop | $2,975 |
| C03 | reflux_drum_leak + distillate_pump_trip | 46.8 min | -$23,548 · SIF trip DR_L_LL at 46.8 min | -$22,722 · stop 37.7 | -$22,747 · stop 37.6 | -$23,531 · stop 46.5 | -$23,494 · stop 46.3 | -$23,494 · stop 46.3 | $1,449 |
| T01 | h2_pcv_capacity_degradation + distillate_pump_trip + bottoms_pump_trip | 24.9 min | -$19,832 · SIF trip DR_L_HH at 24.9 min (also R_P_HH) | -$3,051 · no stop | $1,385 · no stop | -$1,365 · no stop | $1,404 · no stop | $1,464 · no stop | $1,449 |
| T02 | condenser_cooling_loss + reactor_pressure_sensor_low_bias + reflux_pump_trip | 20.4 min | -$19,708 · SIF trip COL_L_LL at 20.4 min | -$20,809 · stop 21.7 | -$21,041 · stop 23.6 | -$20,977 · stop 23.6 | -$19,708 · stop 20.4 | -$21,047 · stop 23.6 | $1,449 |
| T03 | condenser_cooling_loss + reflux_pump_trip + reflux_drum_leak | 19.3 min | -$20,234 · SIF trip COL_L_LL at 19.3 min | -$21,067 · stop 18.9 | -$21,069 · stop 18.9 | -$21,064 · stop 18.9 | -$20,234 · stop 19.3 | -$21,071 · stop 18.7 | $1,449 |

**Without agents: 18/20 plant stops (18 SIF trips + 0 model-limit stops with no SIF), profit -$364,878; nominal $34,202.**

## Agent configurations

| configuration | runs | plant stops | profit | saving vs no agents | LLM calls | LLM cost | decision time | mean per decision | mean LLM latency | twin time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LLM, no validation (feeling lucky) | 20/20 | 9 | -$185,679 | $179,199 | 678 | $0.49 | 123.3 min | 20.6 s | 8.8 s | 0.0 min |
| LLM + LLM judge | 20/20 | 11 | -$229,742 | $135,136 | 1477 | $1.10 | 183.4 min | 29.0 s | 6.4 s | 0.0 min |
| LLM + digital twin | 20/20 | 5 | -$104,423 | $260,455 | 1165 | $0.91 | 718.0 min | 167.0 s | 7.8 s | 537.4 min |
| LLM planning + ADMIT gates (T0-T4, simulated) | 20/20 | 6 | -$128,089 | $236,789 | 2332 | $2.09 | 1086.6 min | 143.3 s | 8.4 s | 731.5 min |
| LLM planning + ADMIT gates (T0-T3, no simulation) | 20/20 | 5 | -$106,200 | $258,678 | 2096 | $1.88 | 296.8 min | 42.2 s | 7.9 s | 0.0 min |
