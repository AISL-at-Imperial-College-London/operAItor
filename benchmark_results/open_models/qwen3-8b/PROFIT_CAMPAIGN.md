# Fault campaign: profit with and without the agents

Every agent action comes from the LLM (no rule-based agent). Configurations: `lucky` = LLM, no validation (feeling lucky); `judge` = LLM + LLM judge; `llmtwin` = LLM + digital twin; `admit` = LLM planning + ADMIT gates (T0-T4, simulated); `admit_nosim` = LLM planning + ADMIT gates (T0-T3, no simulation).
Faults start at 10 min. Each fault is lifted 10 min after the time the plant would have tripped (probe run, faults never lifted, nobody acting), or 30 min after its onset if the probe does not trip. Window = max(120 min, last lift + 60 min).
Trip penalty (shutdown + startup): $20,000; after a trip the plant earns nothing for the rest of the window. Profit includes the change in liquid hold-up valued at the IPA price.

| id | faults | would trip at | no agents | LLM, no validation (feeling lucky) | LLM + LLM judge | LLM + digital twin | LLM planning + ADMIT gates (T0-T4, simulated) | LLM planning + ADMIT gates (T0-T3, no simulation) | nominal |
|---|---|---|---|---|---|---|---|---|---:|
| S01 | reflux_pump_trip | 16.9 min | -$19,198 · SIF trip COL_L_LL at 16.9 min | $1,444 · no stop | $1,444 · no stop | $1,441 · no stop | $1,441 · no stop | $1,444 · no stop | $1,449 |
| S02 | bottoms_pump_trip | 48.7 min | -$19,279 · SIF trip RB_L_HH at 48.7 min | $1,448 · no stop | $1,448 · no stop | $1,448 · no stop | $1,448 · no stop | $1,448 · no stop | $1,449 |
| S03 | distillate_pump_trip | 22.4 min | -$19,849 · SIF trip DR_L_HH at 22.4 min | $1,449 · no stop | $1,394 · no stop | $1,449 · no stop | $1,449 · no stop | $1,449 · no stop | $1,449 |
| S04 | feed_pump_trip | 81.1 min | -$19,731 · SIF trip DR_L_LL at 81.1 min | $1,813 · no stop | $1,813 · no stop | $1,813 · no stop | $1,813 · no stop | $1,813 · no stop | $1,822 |
| S05 | cstr_outlet_capacity_loss | no trip | $775 · no trip (SIF R_L_HH latched, reset) | $757 · no stop | $1,096 · no stop | $855 · no stop | $758 · no stop | $709 · no stop | $1,449 |
| S06 | h2_pcv_capacity_degradation | 177.8 min | -$20,042 · SIF trip RB_L_HH at 177.8 min (also R_P_HH) | $3,201 · no stop | $3,219 · no stop | $3,191 · no stop | $3,214 · no stop | $3,101 · no stop | $2,987 |
| S07 | reactor_heater_stuck_high | no trip | $1,478 · no trip (SIF R_T_HH latched, reset) | $1,527 · no stop | $1,527 · no stop | $1,554 · no stop | $1,730 · no stop | $1,496 · no stop | $1,449 |
| S08 | condenser_cooling_loss | 23.2 min | -$21,023 · SIF trip DR_L_LL at 23.2 min | -$21,028 · stop 23.3 | -$21,024 · stop 23.2 | -$21,029 · stop 23.3 | -$21,023 · stop 23.2 | -$21,027 · stop 23.2 | $1,449 |
| S09 | reflux_drum_leak | 46.2 min | -$23,491 · SIF trip DR_L_LL at 46.2 min | -$23,004 · stop 41.6 | -$23,410 · stop 45.8 | -$23,764 · stop 48.9 | -$23,491 · stop 46.2 | -$23,441 · stop 45.8 | $1,449 |
| S10 | reactor_pressure_sensor_low_bias | 197.5 min | -$19,797 · SIF trip RB_L_HH at 197.5 min (also R_P_HH) | $3,237 · no stop | $3,239 · no stop | $3,257 · no stop | $3,242 · no stop | $3,238 · no stop | $3,234 |
| D01 | reactor_pressure_sensor_low_bias + bottoms_pump_trip | 49.9 min | -$19,320 · SIF trip RB_L_HH at 49.9 min (also R_P_HH) | $1,444 · no stop | $1,445 · no stop | $1,427 · no stop | $1,480 · no stop | $1,454 · no stop | $1,449 |
| D02 | reactor_heater_stuck_high + distillate_pump_trip | 21.9 min | -$19,834 · SIF trip DR_L_HH at 21.9 min | $1,523 · no stop | $1,563 · no stop | $1,446 · no stop | $1,505 · no stop | $1,502 · no stop | $1,449 |
| D03 | h2_pcv_capacity_degradation + reflux_pump_trip | 21.9 min | -$19,142 · SIF trip COL_L_LL at 21.9 min (also R_P_HH) | $1,455 · no stop | $1,453 · no stop | $1,453 · no stop | $1,454 · no stop | $1,430 · no stop | $1,449 |
| D04 | reactor_pressure_sensor_low_bias + reflux_drum_leak | 46.7 min | -$23,344 · SIF trip DR_L_LL at 46.7 min (also R_P_HH) | -$23,463 · stop 48.0 | -$23,463 · stop 48.0 | -$23,467 · stop 48.3 | -$23,425 · stop 47.9 | -$23,425 · stop 47.9 | $1,449 |
| C01 | condenser_cooling_loss + reflux_pump_trip | 20.4 min | -$19,707 · SIF trip COL_L_LL at 20.4 min | -$21,044 · stop 23.6 | -$21,044 · stop 23.6 | -$21,046 · stop 23.6 | -$19,707 · stop 20.4 | -$21,049 · stop 23.6 | $1,449 |
| C02 | h2_pcv_capacity_degradation + reactor_pressure_sensor_low_bias | 176.5 min | -$20,052 · SIF trip RB_L_HH at 176.5 min (also R_P_HH) | $3,302 · no stop | $3,333 · no stop | $3,263 · no stop | $3,203 · no stop | $3,017 · no stop | $2,975 |
| C03 | reflux_drum_leak + distillate_pump_trip | 46.8 min | -$23,548 · SIF trip DR_L_LL at 46.8 min | -$23,120 · stop 42.8 | -$23,442 · stop 45.5 | -$23,764 · stop 48.9 | -$23,548 · stop 46.8 | -$23,548 · stop 46.8 | $1,449 |
| T01 | h2_pcv_capacity_degradation + distillate_pump_trip + bottoms_pump_trip | 24.9 min | -$19,832 · SIF trip DR_L_HH at 24.9 min (also R_P_HH) | $1,437 · no stop | $1,437 · no stop | $1,440 · no stop | $1,467 · no stop | $1,471 · no stop | $1,449 |
| T02 | condenser_cooling_loss + reactor_pressure_sensor_low_bias + reflux_pump_trip | 20.4 min | -$19,708 · SIF trip COL_L_LL at 20.4 min | -$21,027 · stop 22.9 | -$21,025 · stop 22.9 | -$21,047 · stop 23.7 | -$19,708 · stop 20.4 | -$21,046 · stop 23.6 | $1,449 |
| T03 | condenser_cooling_loss + reflux_pump_trip + reflux_drum_leak | 19.3 min | -$20,234 · SIF trip COL_L_LL at 19.3 min | -$21,062 · stop 18.9 | -$21,086 · stop 18.8 | -$21,065 · stop 18.9 | -$20,234 · stop 19.3 | -$21,055 · stop 19.7 | $1,449 |

**Without agents: 18/20 plant stops (18 SIF trips + 0 model-limit stops with no SIF), profit -$364,878; nominal $34,202.**

## Agent configurations

| configuration | runs | plant stops | profit | saving vs no agents | LLM calls | LLM cost | decision time | mean per decision | mean LLM latency | twin time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LLM, no validation (feeling lucky) | 20/20 | 7 | -$129,711 | $235,167 | 345 | $0.00 | 720.9 min | 127.6 s | 125.4 s | 0.0 min |
| LLM + LLM judge | 20/20 | 7 | -$130,083 | $234,795 | 782 | $0.00 | 1547.3 min | 259.3 s | 118.7 s | 0.0 min |
| LLM + digital twin | 20/20 | 7 | -$131,145 | $233,733 | 967 | $0.00 | 2403.4 min | 576.8 s | 115.2 s | 547.1 min |
| LLM planning + ADMIT gates (T0-T4, simulated) | 20/20 | 7 | -$126,932 | $237,946 | 888 | $0.00 | 2667.0 min | 521.2 s | 159.1 s | 373.3 min |
| LLM planning + ADMIT gates (T0-T3, no simulation) | 20/20 | 7 | -$131,019 | $233,859 | 810 | $0.00 | 2011.2 min | 377.1 s | 155.0 s | 0.0 min |
