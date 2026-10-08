# Fault campaign: profit with and without the agents

Every agent action comes from the LLM (no rule-based agent). Configurations: `lucky` = LLM, no validation (feeling lucky); `judge` = LLM + LLM judge; `llmtwin` = LLM + digital twin.
Faults start at 10 min. Each fault is lifted 10 min after the time the plant would have tripped (probe run, faults never lifted, nobody acting), or 30 min after its onset if the probe does not trip. Window = max(120 min, last lift + 60 min).
Trip penalty (shutdown + startup): $20,000; after a trip the plant earns nothing for the rest of the window. Profit includes the change in liquid hold-up valued at the IPA price.

| id | faults | would trip at | no agents | LLM, no validation (feeling lucky) | LLM + LLM judge | LLM + digital twin | nominal |
|---|---|---|---|---|---|---|---:|
| S01 | reflux_pump_trip | 24.4 min | -$19,876 · column dry-out (MODEL_LIMIT, no SIF) at 24.4 min | $1,441 · no stop | $1,441 · no stop | $1,441 · no stop | $1,449 |
| S02 | bottoms_pump_trip | 48.7 min | -$19,279 · SIF trip RB_L_HH at 48.7 min | $1,634 · no stop | $909 · no stop | $1,448 · no stop | $1,449 |
| S03 | distillate_pump_trip | 22.4 min | -$19,849 · SIF trip DR_L_HH at 22.4 min | -$3,977 · no stop | $772 · no stop | $1,449 · no stop | $1,449 |
| S04 | feed_pump_trip | 81.1 min | -$19,731 · SIF trip DR_L_LL at 81.1 min | $1,813 · no stop | $1,813 · no stop | $1,813 · no stop | $1,822 |
| S05 | cstr_outlet_capacity_loss | no trip | $775 · no trip (SIF R_L_HH latched, reset) | -$144 · no stop | $82 · no stop | $733 · no stop | $1,449 |
| S06 | h2_pcv_capacity_degradation | 177.8 min | -$20,042 · SIF trip RB_L_HH at 177.8 min (also R_P_HH) | -$1,375 · no stop | -$18,582 · stop 221.9 | $2,146 · no stop | $2,987 |
| S07 | reactor_heater_stuck_high | no trip | $1,478 · no trip (SIF R_T_HH latched, reset) | $1,193 · no stop | $1,309 · no stop | $1,421 · no stop | $1,449 |
| S08 | condenser_cooling_loss | 23.2 min | -$21,023 · SIF trip DR_L_LL at 23.2 min | -$21,003 · stop 23.2 | -$20,999 · stop 23.2 | -$20,991 · stop 23.2 | $1,449 |
| S09 | reflux_drum_leak | 46.2 min | -$23,491 · SIF trip DR_L_LL at 46.2 min | -$21,544 · stop 28.2 | -$22,822 · stop 39.2 | -$7,515 · no stop | $1,449 |
| S10 | reactor_pressure_sensor_low_bias | 197.5 min | -$19,797 · SIF trip RB_L_HH at 197.5 min (also R_P_HH) | -$20,664 · stop 167.5 | -$39 · no stop | $3,036 · no stop | $3,234 |
| D01 | reactor_pressure_sensor_low_bias + bottoms_pump_trip | 49.9 min | -$19,320 · SIF trip RB_L_HH at 49.9 min (also R_P_HH) | -$132 · no stop | $1,400 · no stop | $1,452 · no stop | $1,449 |
| D02 | reactor_heater_stuck_high + distillate_pump_trip | 21.9 min | -$19,834 · SIF trip DR_L_HH at 21.9 min | $1,337 · no stop | $1,245 · no stop | $1,388 · no stop | $1,449 |
| D03 | h2_pcv_capacity_degradation + reflux_pump_trip | 29.4 min | -$19,873 · column dry-out (MODEL_LIMIT, no SIF) at 29.4 min (SIF R_P_HH) | $1,455 · no stop | -$241 · no stop | $1,473 · no stop | $1,449 |
| D04 | reactor_pressure_sensor_low_bias + reflux_drum_leak | 46.7 min | -$23,344 · SIF trip DR_L_LL at 46.7 min (also R_P_HH) | -$6,739 · no stop | -$21,958 · stop 29.8 | -$5,709 · no stop | $1,449 |
| C01 | condenser_cooling_loss + reflux_pump_trip | 28.3 min | -$19,524 · column dry-out (MODEL_LIMIT, no SIF) at 28.3 min | -$21,018 · stop 23.5 | -$21,035 · stop 23.6 | -$20,727 · stop 30.8 | $1,449 |
| C02 | h2_pcv_capacity_degradation + reactor_pressure_sensor_low_bias | 176.5 min | -$20,052 · SIF trip RB_L_HH at 176.5 min (also R_P_HH) | $1,757 · no stop | $914 · no stop | $2,100 · no stop | $2,975 |
| C03 | reflux_drum_leak + distillate_pump_trip | 46.8 min | -$23,548 · SIF trip DR_L_LL at 46.8 min | -$23,888 · stop 51.6 | -$21,518 · stop 28.1 | -$6,838 · no stop | $1,449 |
| T01 | h2_pcv_capacity_degradation + distillate_pump_trip + bottoms_pump_trip | 24.9 min | -$19,832 · SIF trip DR_L_HH at 24.9 min (also R_P_HH) | -$3,135 · no stop | -$43 · no stop | $1,495 · no stop | $1,449 |
| T02 | condenser_cooling_loss + reactor_pressure_sensor_low_bias + reflux_pump_trip | 28.3 min | -$19,526 · column dry-out (MODEL_LIMIT, no SIF) at 28.3 min | -$21,020 · stop 23.6 | -$21,045 · stop 23.6 | -$20,833 · stop 37.3 | $1,449 |
| T03 | condenser_cooling_loss + reflux_pump_trip + reflux_drum_leak | 27.2 min | -$20,922 · column dry-out (MODEL_LIMIT, no SIF) at 27.2 min | -$21,067 · stop 18.9 | -$21,065 · stop 18.9 | -$20,824 · stop 27.3 | $1,449 |

**Without agents: 18/20 plant stops (13 SIF trips + 5 column dry-outs with no SIF), profit -$366,610; nominal $34,202.**

## Agent configurations

| configuration | runs | plant stops | profit | saving vs no agents | LLM calls | LLM cost | decision time | mean per decision | mean LLM latency | twin time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LLM, no validation (feeling lucky) | 20/20 | 7 | -$155,076 | $211,534 | 734 | $0.53 | 283.5 min | 44.1 s | 9.2 s | 0.0 min |
| LLM + LLM judge | 20/20 | 8 | -$159,462 | $207,148 | 1448 | $1.09 | 293.7 min | 47.5 s | 8.1 s | 0.0 min |
| LLM + digital twin | 20/20 | 4 | -$82,042 | $284,568 | 1290 | $0.99 | 1285.8 min | 274.6 s | 8.6 s | 1047.4 min |
