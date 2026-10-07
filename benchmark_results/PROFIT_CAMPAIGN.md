# Fault campaign: profit with and without the agents

Every agent action comes from the LLM (no rule-based agent). Configurations: `lucky` = LLM, no validation (feeling lucky); `judge` = LLM + LLM judge; `llmtwin` = LLM + digital twin.
Faults start at 10 min. Each fault is lifted 10 min after the time the plant would have tripped (probe run, faults never lifted, nobody acting), or 30 min after its onset if the probe does not trip. Window = max(120 min, last lift + 60 min).
Trip penalty (shutdown + startup): $20,000; after a trip the plant earns nothing for the rest of the window. Profit includes the change in liquid hold-up valued at the IPA price.

| id | faults | would trip at | no agents | LLM, no validation (feeling lucky) | LLM + LLM judge | LLM + digital twin | nominal |
|---|---|---|---|---|---|---|---:|
| S01 | reflux_pump_trip | 24.4 min | -$19,859 · column dry-out (MODEL_LIMIT, no SIF) at 24.4 min | $1,484 · no stop | $14 · no stop | $1,484 · no stop | $1,448 |
| S02 | bottoms_pump_trip | 48.8 min | -$19,275 · SIF trip RB_L_HH at 48.8 min | $1,346 · no stop | $1,324 · no stop | $1,448 · no stop | $1,448 |
| S03 | distillate_pump_trip | 22.4 min | -$19,848 · SIF trip DR_L_HH at 22.4 min | -$571 · no stop | $1,481 · no stop | $1,439 · no stop | $1,448 |
| S04 | feed_pump_trip | 72.1 min | -$19,835 · SIF trip DR_L_LL at 72.1 min | $1,070 · no stop | $1,699 · no stop | $1,804 · no stop | $1,714 |
| S05 | cstr_outlet_capacity_loss | no trip | $412 · no trip (SIF R_L_HH latched, reset) | -$19,600 · stop 94.1 | -$381 · no stop | -$19,579 · stop 94.9 | $1,448 |
| S06 | h2_pcv_capacity_degradation | 162.8 min | -$20,011 · SIF trip RB_L_HH at 162.8 min (also R_P_HH) | $1,637 · no stop | $1,425 · no stop | $1,746 · no stop | $2,801 |
| S07 | reactor_heater_stuck_high | no trip | $1,473 · no trip (SIF R_T_HH latched, reset) | -$19,790 · stop 79.1 | -$19,430 · stop 97.6 | $1,505 · no stop | $1,448 |
| S08 | condenser_cooling_loss | 21.5 min | -$20,857 · SIF trip DR_L_LL at 21.5 min | -$20,847 · stop 21.6 | -$20,851 · stop 21.6 | -$20,852 · stop 21.6 | $1,448 |
| S09 | reflux_drum_leak | 38.6 min | -$22,684 · SIF trip DR_L_LL at 38.6 min | -$26,193 · stop 96.1 | -$20,987 · stop 22.6 | -$7,119 · no stop | $1,448 |
| S10 | reactor_pressure_sensor_low_bias | 185.3 min | -$19,765 · SIF trip RB_L_HH at 185.3 min (also R_P_HH) | -$19,106 · stop 156.8 | $2,323 · no stop | $2,768 · no stop | $3,077 |
| D01 | reactor_pressure_sensor_low_bias + bottoms_pump_trip | 50.0 min | -$19,322 · SIF trip RB_L_HH at 50.0 min (also R_P_HH) | $1,177 · no stop | $1,019 · no stop | $1,497 · no stop | $1,448 |
| D02 | reactor_heater_stuck_high + distillate_pump_trip | 21.9 min | -$19,835 · SIF trip DR_L_HH at 21.9 min | -$19,666 · stop 106.0 | $1,267 · no stop | $1,433 · no stop | $1,448 |
| D03 | h2_pcv_capacity_degradation + reflux_pump_trip | 29.4 min | -$19,783 · column dry-out (MODEL_LIMIT, no SIF) at 29.4 min (SIF R_P_HH) | $1,095 · no stop | $1,286 · no stop | $1,249 · no stop | $1,448 |
| D04 | reactor_pressure_sensor_low_bias + reflux_drum_leak | 40.6 min | -$22,661 · SIF trip DR_L_LL at 40.6 min | -$22,143 · stop 34.4 | -$21,447 · stop 29.3 | -$22,658 · stop 43.9 | $1,448 |
| C01 | condenser_cooling_loss + reflux_pump_trip | 28.3 min | -$19,499 · column dry-out (MODEL_LIMIT, no SIF) at 28.3 min | -$20,843 · stop 21.8 | -$20,839 · stop 21.9 | -$19,487 · stop 28.4 | $1,448 |
| C02 | h2_pcv_capacity_degradation + reactor_pressure_sensor_low_bias | 161.1 min | -$20,020 · SIF trip RB_L_HH at 161.1 min (also R_P_HH) | -$17,849 · stop 203.9 | -$18,394 · stop 222.7 | $2,202 · no stop | $2,783 |
| C03 | reflux_drum_leak + distillate_pump_trip | 44.6 min | -$23,325 · SIF trip DR_L_LL at 44.6 min | -$23,105 · stop 46.8 | -$21,293 · stop 25.5 | -$23,728 · stop 50.3 | $1,448 |
| T01 | h2_pcv_capacity_degradation + distillate_pump_trip + bottoms_pump_trip | 24.9 min | -$19,831 · SIF trip DR_L_HH at 24.9 min (also R_P_HH) | $970 · no stop | $1,459 · no stop | $1,235 · no stop | $1,448 |
| T02 | condenser_cooling_loss + reactor_pressure_sensor_low_bias + reflux_pump_trip | 28.3 min | -$19,500 · column dry-out (MODEL_LIMIT, no SIF) at 28.3 min | -$20,843 · stop 21.9 | -$20,841 · stop 21.8 | -$19,601 · stop 28.9 | $1,448 |
| T03 | condenser_cooling_loss + reflux_pump_trip + reflux_drum_leak | 27.2 min | -$20,867 · column dry-out (MODEL_LIMIT, no SIF) at 27.2 min | -$20,908 · stop 18.1 | -$20,885 · stop 17.8 | -$20,869 · stop 27.3 | $1,448 |

**Without agents: 18/20 plant stops (13 SIF trips + 5 column dry-outs with no SIF), profit -$364,892; nominal $33,543.**

## Agent configurations

| configuration | runs | plant stops | profit | saving vs no agents | LLM calls | LLM cost | decision time | mean per decision | mean LLM latency | twin time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LLM, no validation (feeling lucky) | 20/20 | 12 | -$242,685 | $122,207 | 362 | $0.32 | 43.5 min | 7.2 s | 7.2 s | 0.0 min |
| LLM + LLM judge | 20/20 | 9 | -$172,051 | $192,841 | 1072 | $0.87 | 122.6 min | 20.8 s | 6.9 s | 0.0 min |
| LLM + digital twin | 20/20 | 7 | -$134,083 | $230,809 | 1023 | $0.85 | 1008.9 min | 228.4 s | 6.7 s | 895.3 min |
