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
| S05 | cstr_outlet_capacity_loss | no trip | $775 · no trip (SIF R_L_HH latched, reset) | $844 · no stop | $847 · no stop | $843 · no stop | $754 · no stop | $754 · no stop | $1,449 |
| S06 | h2_pcv_capacity_degradation | 177.8 min | -$20,042 · SIF trip RB_L_HH at 177.8 min (also R_P_HH) | $3,224 · no stop | $3,224 · no stop | $3,128 · no stop | $3,022 · no stop | $3,206 · no stop | $2,987 |
| S07 | reactor_heater_stuck_high | no trip | $1,478 · no trip (SIF R_T_HH latched, reset) | $1,655 · no stop | $1,655 · no stop | $1,657 · no stop | $1,501 · no stop | $1,501 · no stop | $1,449 |
| S08 | condenser_cooling_loss | 23.2 min | -$21,023 · SIF trip DR_L_LL at 23.2 min | -$21,027 · stop 23.3 | -$21,026 · stop 23.3 | -$21,029 · stop 23.3 | -$21,023 · stop 23.2 | -$21,024 · stop 23.2 | $1,449 |
| S09 | reflux_drum_leak | 46.2 min | -$23,491 · SIF trip DR_L_LL at 46.2 min | -$23,451 · stop 45.8 | -$23,451 · stop 45.8 | -$23,764 · stop 48.9 | -$23,491 · stop 46.2 | -$23,491 · stop 46.2 | $1,449 |
| S10 | reactor_pressure_sensor_low_bias | 197.5 min | -$19,797 · SIF trip RB_L_HH at 197.5 min (also R_P_HH) | $3,234 · no stop | $3,234 · no stop | $3,235 · no stop | $3,536 · no stop | $3,592 · no stop | $3,234 |
| D01 | reactor_pressure_sensor_low_bias + bottoms_pump_trip | 49.9 min | -$19,320 · SIF trip RB_L_HH at 49.9 min (also R_P_HH) | $1,448 · no stop | $1,448 · no stop | $1,421 · no stop | $1,454 · no stop | $1,454 · no stop | $1,449 |
| D02 | reactor_heater_stuck_high + distillate_pump_trip | 21.9 min | -$19,834 · SIF trip DR_L_HH at 21.9 min | $1,659 · no stop | $1,659 · no stop | $1,740 · no stop | $1,659 · no stop | $1,505 · no stop | $1,449 |
| D03 | h2_pcv_capacity_degradation + reflux_pump_trip | 21.9 min | -$19,142 · SIF trip COL_L_LL at 21.9 min (also R_P_HH) | $1,459 · no stop | $1,459 · no stop | $1,462 · no stop | $1,461 · no stop | -$24,342 · stop 83.0 | $1,449 |
| D04 | reactor_pressure_sensor_low_bias + reflux_drum_leak | 46.7 min | -$23,344 · SIF trip DR_L_LL at 46.7 min (also R_P_HH) | -$23,504 · stop 48.6 | -$23,466 · stop 48.1 | -$23,504 · stop 48.6 | -$23,425 · stop 47.9 | -$23,425 · stop 47.9 | $1,449 |
| C01 | condenser_cooling_loss + reflux_pump_trip | 20.4 min | -$19,707 · SIF trip COL_L_LL at 20.4 min | -$21,046 · stop 23.6 | -$21,046 · stop 23.6 | -$21,046 · stop 23.6 | -$19,707 · stop 20.4 | -$21,045 · stop 23.6 | $1,449 |
| C02 | h2_pcv_capacity_degradation + reactor_pressure_sensor_low_bias | 176.5 min | -$20,052 · SIF trip RB_L_HH at 176.5 min (also R_P_HH) | $3,325 · no stop | $3,204 · no stop | $3,204 · no stop | $3,282 · no stop | $3,288 · no stop | $2,975 |
| C03 | reflux_drum_leak + distillate_pump_trip | 46.8 min | -$23,548 · SIF trip DR_L_LL at 46.8 min | -$23,454 · stop 45.9 | -$23,454 · stop 45.9 | -$23,764 · stop 48.9 | -$23,494 · stop 46.3 | -$23,494 · stop 46.3 | $1,449 |
| T01 | h2_pcv_capacity_degradation + distillate_pump_trip + bottoms_pump_trip | 24.9 min | -$19,832 · SIF trip DR_L_HH at 24.9 min (also R_P_HH) | $1,465 · no stop | $1,465 · no stop | $1,461 · no stop | $1,466 · no stop | $1,466 · no stop | $1,449 |
| T02 | condenser_cooling_loss + reactor_pressure_sensor_low_bias + reflux_pump_trip | 20.4 min | -$19,708 · SIF trip COL_L_LL at 20.4 min | -$21,027 · stop 22.9 | -$21,025 · stop 22.9 | -$21,048 · stop 23.6 | -$19,708 · stop 20.4 | -$21,046 · stop 23.6 | $1,449 |
| T03 | condenser_cooling_loss + reflux_pump_trip + reflux_drum_leak | 19.3 min | -$20,234 · SIF trip COL_L_LL at 19.3 min | -$21,062 · stop 18.9 | -$21,065 · stop 18.9 | -$21,065 · stop 18.9 | -$20,234 · stop 19.3 | -$21,061 · stop 18.9 | $1,449 |

**Without agents: 18/20 plant stops (18 SIF trips + 0 model-limit stops with no SIF), profit -$364,878; nominal $34,202.**

## Agent configurations

| configuration | runs | plant stops | profit | saving vs no agents | LLM calls | LLM cost | decision time | mean per decision | mean LLM latency | twin time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LLM, no validation (feeling lucky) | 20/20 | 7 | -$130,101 | $234,777 | 114 | $0.00 | 124.8 min | 66.3 s | 65.7 s | 0.0 min |
| LLM + LLM judge | 20/20 | 7 | -$130,181 | $234,697 | 241 | $0.00 | 172.2 min | 95.7 s | 42.9 s | 0.0 min |
| LLM + digital twin | 20/20 | 7 | -$130,912 | $233,966 | 354 | $0.00 | 520.1 min | 335.5 s | 63.2 s | 147.0 min |
| LLM planning + ADMIT gates (T0-T4, simulated) | 20/20 | 7 | -$126,790 | $238,088 | 257 | $0.00 | 557.5 min | 398.2 s | 114.8 s | 72.7 min |
| LLM planning + ADMIT gates (T0-T3, no simulation) | 20/20 | 8 | -$156,005 | $208,873 | 330 | $0.00 | 438.2 min | 219.1 s | 96.2 s | 0.0 min |

## Rejected proposals by failure type

| configuration | decisions | admit rejected | judge rejected | not executable | repeat | twin not restored | twin trip |
|---|---:|---:|---:|---:|---:|---:|---:|
| LLM, no validation (feeling lucky) | — | 0 | 0 | 1 | 0 | 0 | 0 |
| LLM + LLM judge | — | 0 | 11 | 1 | 2 | 0 | 0 |
| LLM + digital twin | — | 0 | 0 | 3 | 87 | 208 | 49 |
| LLM planning + ADMIT gates (T0-T4, simulated) | — | 45 | 0 | 14 | 50 | 0 | 0 |
| LLM planning + ADMIT gates (T0-T3, no simulation) | — | 44 | 0 | 35 | 20 | 0 | 0 |
