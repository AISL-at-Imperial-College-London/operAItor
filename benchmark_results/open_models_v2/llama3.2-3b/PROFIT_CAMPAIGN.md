# Fault campaign: profit with and without the agents

Every agent action comes from the LLM (no rule-based agent). Configurations: `lucky` = LLM, no validation (feeling lucky); `judge` = LLM + LLM judge; `llmtwin` = LLM + digital twin; `admit` = LLM planning + ADMIT gates (T0-T4, simulated); `admit_nosim` = LLM planning + ADMIT gates (T0-T3, no simulation).
Faults start at 10 min. Each fault is lifted 10 min after the time the plant would have tripped (probe run, faults never lifted, nobody acting), or 30 min after its onset if the probe does not trip. Window = max(120 min, last lift + 60 min).
Trip penalty (shutdown + startup): $20,000; after a trip the plant earns nothing for the rest of the window. Profit includes the change in liquid hold-up valued at the IPA price.

| id | faults | would trip at | no agents | LLM, no validation (feeling lucky) | LLM + LLM judge | LLM + digital twin | LLM planning + ADMIT gates (T0-T4, simulated) | LLM planning + ADMIT gates (T0-T3, no simulation) | nominal |
|---|---|---|---|---|---|---|---|---|---:|
| S01 | reflux_pump_trip | 16.9 min | -$19,198 · SIF trip COL_L_LL at 16.9 min | -$19,198 · stop 16.9 | -$19,198 · stop 16.9 | -$19,198 · stop 16.9 | -$19,198 · stop 16.9 | $1,434 · no stop | $1,449 |
| S02 | bottoms_pump_trip | 48.7 min | -$19,279 · SIF trip RB_L_HH at 48.7 min | -$19,279 · stop 48.7 | -$19,279 · stop 48.7 | -$19,279 · stop 48.7 | $1,449 · no stop | $1,449 · no stop | $1,449 |
| S03 | distillate_pump_trip | 22.4 min | -$19,849 · SIF trip DR_L_HH at 22.4 min | -$19,849 · stop 22.4 | -$19,849 · stop 22.4 | -$19,849 · stop 22.4 | $1,449 · no stop | $1,449 · no stop | $1,449 |
| S04 | feed_pump_trip | 81.1 min | -$19,731 · SIF trip DR_L_LL at 81.1 min | -$19,731 · stop 81.1 | -$19,731 · stop 81.1 | -$19,731 · stop 81.1 | $1,817 · no stop | $1,817 · no stop | $1,822 |
| S05 | cstr_outlet_capacity_loss | no trip | $775 · no trip (SIF R_L_HH latched, reset) | $754 · no stop | $754 · no stop | $754 · no stop | $754 · no stop | $754 · no stop | $1,449 |
| S06 | h2_pcv_capacity_degradation | 177.8 min | -$20,042 · SIF trip RB_L_HH at 177.8 min (also R_P_HH) | $3,233 · no stop | $3,233 · no stop | $3,233 · no stop | $3,233 · no stop | $3,233 · no stop | $2,987 |
| S07 | reactor_heater_stuck_high | no trip | $1,478 · no trip (SIF R_T_HH latched, reset) | $1,501 · no stop | $1,501 · no stop | $1,501 · no stop | $1,501 · no stop | $1,501 · no stop | $1,449 |
| S08 | condenser_cooling_loss | 23.2 min | -$21,023 · SIF trip DR_L_LL at 23.2 min | -$21,029 · stop 23.3 | -$21,023 · stop 23.2 | -$21,029 · stop 23.3 | -$21,023 · stop 23.2 | -$21,023 · stop 23.2 | $1,449 |
| S09 | reflux_drum_leak | 46.2 min | -$23,491 · SIF trip DR_L_LL at 46.2 min | -$23,491 · stop 46.2 | -$23,491 · stop 46.2 | -$23,491 · stop 46.2 | -$23,488 · stop 46.1 | -$23,491 · stop 46.2 | $1,449 |
| S10 | reactor_pressure_sensor_low_bias | 197.5 min | -$19,797 · SIF trip RB_L_HH at 197.5 min (also R_P_HH) | $3,576 · no stop | $3,577 · no stop | $3,577 · no stop | $3,577 · no stop | $3,577 · no stop | $3,234 |
| D01 | reactor_pressure_sensor_low_bias + bottoms_pump_trip | 49.9 min | -$19,320 · SIF trip RB_L_HH at 49.9 min (also R_P_HH) | -$19,251 · stop 51.5 | -$19,251 · stop 51.5 | $1,444 · no stop | -$19,251 · stop 51.5 | $1,454 · no stop | $1,449 |
| D02 | reactor_heater_stuck_high + distillate_pump_trip | 21.9 min | -$19,834 · SIF trip DR_L_HH at 21.9 min | $1,502 · no stop | -$19,834 · stop 21.9 | $1,505 · no stop | $1,505 · no stop | $1,505 · no stop | $1,449 |
| D03 | h2_pcv_capacity_degradation + reflux_pump_trip | 21.9 min | -$19,142 · SIF trip COL_L_LL at 21.9 min (also R_P_HH) | -$19,137 · stop 21.9 | -$19,137 · stop 21.9 | -$19,137 · stop 21.9 | $1,445 · no stop | $1,445 · no stop | $1,449 |
| D04 | reactor_pressure_sensor_low_bias + reflux_drum_leak | 46.7 min | -$23,344 · SIF trip DR_L_LL at 46.7 min (also R_P_HH) | -$23,425 · stop 47.9 | -$23,425 · stop 47.9 | -$23,425 · stop 47.9 | -$23,425 · stop 47.9 | -$23,425 · stop 47.9 | $1,449 |
| C01 | condenser_cooling_loss + reflux_pump_trip | 20.4 min | -$19,707 · SIF trip COL_L_LL at 20.4 min | -$21,029 · stop 23.3 | -$19,707 · stop 20.4 | -$21,029 · stop 23.3 | -$19,707 · stop 20.4 | -$21,023 · stop 23.2 | $1,449 |
| C02 | h2_pcv_capacity_degradation + reactor_pressure_sensor_low_bias | 176.5 min | -$20,052 · SIF trip RB_L_HH at 176.5 min (also R_P_HH) | $3,322 · no stop | $3,322 · no stop | $3,307 · no stop | $3,322 · no stop | $3,322 · no stop | $2,975 |
| C03 | reflux_drum_leak + distillate_pump_trip | 46.8 min | -$23,548 · SIF trip DR_L_LL at 46.8 min | -$23,548 · stop 46.8 | -$23,548 · stop 46.8 | -$23,548 · stop 46.8 | -$23,494 · stop 46.3 | -$23,494 · stop 46.3 | $1,449 |
| T01 | h2_pcv_capacity_degradation + distillate_pump_trip + bottoms_pump_trip | 24.9 min | -$19,832 · SIF trip DR_L_HH at 24.9 min (also R_P_HH) | $1,467 · no stop | -$19,803 · stop 24.4 | $1,467 · no stop | $1,466 · no stop | $1,466 · no stop | $1,449 |
| T02 | condenser_cooling_loss + reactor_pressure_sensor_low_bias + reflux_pump_trip | 20.4 min | -$19,708 · SIF trip COL_L_LL at 20.4 min | -$21,024 · stop 23.2 | -$19,708 · stop 20.4 | -$19,708 · stop 20.4 | -$19,708 · stop 20.4 | -$21,024 · stop 23.2 | $1,449 |
| T03 | condenser_cooling_loss + reflux_pump_trip + reflux_drum_leak | 19.3 min | -$20,234 · SIF trip COL_L_LL at 19.3 min | -$21,089 · stop 18.8 | -$20,234 · stop 19.3 | -$21,089 · stop 18.8 | -$20,234 · stop 19.3 | -$21,074 · stop 18.7 | $1,449 |

**Without agents: 18/20 plant stops (18 SIF trips + 0 model-limit stops with no SIF), profit -$364,878; nominal $34,202.**

## Agent configurations

| configuration | runs | plant stops | profit | saving vs no agents | LLM calls | LLM cost | decision time | mean per decision | mean LLM latency | twin time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LLM, no validation (feeling lucky) | 20/20 | 13 | -$255,725 | $109,153 | 427 | $0.00 | 317.5 min | 138.1 s | 44.6 s | 0.0 min |
| LLM + LLM judge | 20/20 | 15 | -$294,831 | $70,047 | 617 | $0.00 | 370.8 min | 163.6 s | 36.1 s | 0.0 min |
| LLM + digital twin | 20/20 | 12 | -$233,725 | $131,153 | 560 | $0.00 | 255.1 min | 109.3 s | 21.8 s | 51.8 min |
| LLM planning + ADMIT gates (T0-T4, simulated) | 20/20 | 9 | -$168,010 | $196,868 | 498 | $0.00 | 456.3 min | 191.4 s | 41.2 s | 128.2 min |
| LLM planning + ADMIT gates (T0-T3, no simulation) | 20/20 | 7 | -$130,148 | $234,730 | 443 | $0.00 | 205.8 min | 89.5 s | 35.6 s | 0.0 min |

## Rejected proposals by failure type

| configuration | decisions | admit rejected | judge rejected | malformed reply | no commands | not executable | repeat | twin not restored | twin trip |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LLM, no validation (feeling lucky) | — | 0 | 0 | 10 | 84 | 117 | 157 | 0 | 0 |
| LLM + LLM judge | — | 0 | 73 | 14 | 94 | 143 | 220 | 0 | 0 |
| LLM + digital twin | — | 0 | 0 | 13 | 102 | 139 | 214 | 62 | 29 |
| LLM planning + ADMIT gates (T0-T4, simulated) | — | 59 | 0 | 5 | 118 | 25 | 37 | 0 | 0 |
| LLM planning + ADMIT gates (T0-T3, no simulation) | — | 32 | 0 | 3 | 115 | 19 | 13 | 0 | 0 |
