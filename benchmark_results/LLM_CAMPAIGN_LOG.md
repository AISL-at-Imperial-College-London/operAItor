# Campaign log: controller tests + three LLM configurations (7 Oct 2026)

## Requests
1. Test the controllers with setpoint changes and keep a plot of each.
2. Run the 20-scenario campaign in three configurations and report the saving of each:
   - **feeling lucky**: no simulator, no validator;
   - **LLM as judge**: no simulator, an LLM validates the proposed actions;
   - **the earlier run**: rule agent + digital twin, unchanged.
3. Use gpt-5-nano, with the option to switch to a local model.
4. Keep the dashboard updated, and log timings / LLM overheads (added mid-run).

## What "the earlier run" is
The earlier campaign used the preset `rule_twin`:
- a **rule-based action agent** (no LLM calls) proposes the actions;
- the digital twin rolls each proposal out for 30 min;
- the validator accepts or rejects it, with up to 4 retries;
- a counterfactual fallback applies the best candidate when nothing fully recovers the plant.

It is kept unchanged as **"Rule agent + digital twin (earlier run)"** (mode `agents`). It is re-run
once only to collect timing data. It is deterministic, so its profits must reproduce exactly.

**"LLM + digital twin"** (mode `llmtwin`, preset `llm`) is the same framework, with the same twin,
retries and fallback. The only difference is that gpt-5-nano proposes the actions instead of the
rule agent. It was added because the request was to run everything with gpt-5-nano.

## Configurations

| mode | preset | proposer | validation |
|---|---|---|---|
| `passive` | — | nobody | — (BPCS + SIFs only) |
| `agents` | `rule_twin` | rule agent | digital twin (earlier run) |
| `lucky` | `llm_lucky` | gpt-5-nano | none: the first executable proposal is applied |
| `judge` | `llm_judge` | gpt-5-nano | gpt-5-nano judge (`agents/judge.py`); a rejection reprompts the action agent (3 retries); nothing is applied if every proposal is rejected |
| `llmtwin` | `llm` | gpt-5-nano | digital twin, same as `agents` |

All modes share:
- **The same pipeline:** PCA-only trigger, knowledge-graph planner and alarm acknowledgement.
- **The same fault schedule:** onset 10 min, faults lifted 10 min after the would-be trip, plus the
  same scenario windows.
- **A held plant:** the plant is held while the agents decide, so LLM latency changes run time,
  not the simulated outcome.
- **The same model settings:** gpt-5-nano with reasoning effort low, JSON output, up to 6 retries
  on rate limits.

## Changes made for this campaign
- **`benchmark/loop_tests.py`:** each PI loop is stepped up at 5 min and back at 45 min, then run
  to 85 min. Pass criteria are checked on both steps. The PNG plots are in `loop_plots/`, and the
  series are in `loop_series.json` for the dashboard.
- **`agents/judge.py` (new):** the LLM judge. Its prompt covers:
  - the plant description and acceptance rules (addresses the fault, no move toward a SIF limit,
    nothing unrelated, proportionate, correct loop directions);
  - the inputs: proposal, snapshot, diagnosis, brief, knowledge-graph evidence, SIF and alarm limits.

  It returns `{accept, confidence, reason, risks, suggestion}`. A failed judge call counts as a
  rejection.
- **`agents/recovery_graph.py`:**
  - `AgentConfig.judge`;
  - the presets `llm_lucky` and `llm_judge`;
  - the judge branch in the twin/validate nodes;
  - wall time of every twin rollout.
- **`agents/supervisor.py`:** a wall-clock stamp on every agent event.
- **`agents/llm.py`:** local models.
  - `PLANT_LLM_BASE_URL` points at any OpenAI-compatible server (LM Studio, vLLM, llama.cpp,
    Ollama's `/v1`).
  - Also: `PLANT_LLM_API_KEY`, `PLANT_LLM_JSON_MODE`, `PLANT_OLLAMA_URL`, `PLANT_LLM_MAX_RETRIES`.
  - The runner's `--model` / `--base-url` flags set these.
- **`benchmark/profit_campaign.py`:**
  - the modes above;
  - per-run LLM usage (calls, tokens, cost);
  - judge verdicts;
  - timing: each LLM call's latency, each twin rollout's time, each decision's time, and totals.
- **`benchmark/economics.py`: accounting fix.** The change in liquid hold-up (reactor + reboiler +
  drum) over the window is now valued at the IPA price.
  - **Found when:** a "feeling lucky" run lowered several level setpoints and cut the feed to
    20 kmol/h. It appeared to earn twice the nominal profit, because the old accounting counted
    flows only, so selling material the plant already held looked like profit.
  - **Applied to:** every run, including the earlier ones.
  - **Effect on earlier results:** the rule-agent saving moved from $354,010 to $350,993, and the
    nominal reference from $33,485 to $33,543.
- **Dashboard:** every configuration is shown side by side:
  - a configuration table with savings, LLM cost and overheads;
  - profit per scenario, and plant stops per configuration;
  - mean decision time split into LLM, twin and other;
  - per scenario: state and cumulative-profit plots for all configurations, plus the decisions
    of each configuration including judge verdicts;
  - controller step charts.
- **Restart at 17:47:** the first LLM runs (17:24–17:46, kept in `runs_llm_untimed/`) had no
  per-call timing. They were stopped and all three LLM configurations restarted with full timing.

## Timeline
- **17:15:** controller tests done. TOP_TIC, R_TIC, R_PIC and BOT_TIC pass both steps. R_LIC,
  RB_LIC, DR_LIC and COL_PIC are stable but slow: about 30–40 % overshoot, and not settled within
  40 min. All three disturbance tests pass.
- **17:24:** pilot (S01, S08 × 3 configurations): the pipeline works, at about 8k tokens and
  $0.0008 per call.
- **17:30:** full lucky and judge runs started; dashboard v2.
- **17:41:** inventory accounting fix; dashboard v3.
- **17:47:** restart with timing.
- **18:00:** lucky 18/20, judge 3/20, twin 4/20; dashboard v4.
- **18:03:** lucky 20/20; rule-agent timing rerun started.
- **18:30:** judge 20/20.
- **20:24:** rule-agent rerun 20/20, all identical to the earlier campaign (profit, outcome, trip time).
- **~20:45:** LLM + twin 20/20.

## Results (20 scenarios each; profit includes the hold-up valuation)

| Configuration | Plant stops | Profit | Saving vs no agents | LLM calls | LLM cost | Decisions | Mean per decision | Mean LLM latency | Twin rollouts |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| No agents | 18 | −$364,892 | — | — | — | — | — | — | — |
| Rule agent + twin (earlier run) | 1 | −$13,899 | **$350,993** | 0 | $0 | 334 | 112 s | — | 658 |
| LLM + twin (gpt-5-nano) | 7 | −$134,083 | **$230,809** | 1,023 | $0.85 | 265 | 228 s | 6.7 s | 1,082 |
| LLM + LLM judge | 9 | −$172,051 | **$192,841** | 1,072 | $0.87 | 353 | 21 s | 6.9 s | 0 |
| LLM, feeling lucky | 12 | −$242,685 | **$122,207** | 362 | $0.32 | 362 | 7 s | 7.2 s | 0 |

Nominal reference (no fault) over the same windows: $33,543. The timed LLM runs cost $2.04 in total;
the untimed first attempt and the pilot added about $0.30.

### Findings
- **Order of results.** The rule agent + twin is still best (1 stop). With gpt-5-nano proposing,
  the twin is the best validator (7 stops), the LLM judge is next (9), and no validation is worst
  (12). Every validator added saves money.
- **Ungrounded LLM actions cause trips.** In S05 and S07 the plant does not stop at all without
  agents, but:
  - "feeling lucky" stops it in both;
  - the judge stops it in S07;
  - the LLM + twin stops it in S05.
- **Where gpt-5-nano fails.** The condenser-cooling-loss and drum-leak cases (S08, C01, C03, D04,
  T02) are saved only by the rule agent. It applies a specific combined boilup + reflux + feed cut,
  which gpt-5-nano never proposes. The twin can only choose among the LLM's candidates, so it
  cannot save these.
- **Where everyone succeeds.** Every configuration handles pump trips, with START_SPARE.
- **The judge is cheap but not grounded.** It rejected about a third of the proposals, but its
  process reasoning was sometimes wrong. For example, it accepted raising the column-pressure
  setpoint as "boosting condenser cooling", which is backwards.
- **Non-determinism.** The same scenario can end differently between runs with the same model.
  D02 "feeling lucky" was saved in the untimed run but tripped in the timed run. Repeating each
  LLM configuration several times would give confidence intervals.
- **Overheads per decision** (wall-clock time; the plant is held while the agents decide):
  - about 7 s for "feeling lucky" (one LLM call);
  - about 21 s with the judge (about 3 calls per decision, counting retries);
  - about 2 min for the rule agent + twin (twin rollouts);
  - about 4 min for LLM + twin (LLM calls plus 4 rollouts per decision).

  All timings were measured with 20–27 runs sharing 22 CPU cores, so the twin times are higher
  than on an idle machine.

### Files
- `benchmark_results/runs/<scenario>_<mode>.json`: every run. Each file holds the 0.5-min data
  points, profit breakdown, alarm history, decisions, judge verdicts, LLM usage and timing (every
  call, rollout and decision).
- `benchmark_results/profit_campaign.json` and `PROFIT_CAMPAIGN.md`: the comparison tables.
- `benchmark_results/fault_campaign_dashboard.html`: the dashboard.
- `benchmark_results/loop_plots/*.png`, `loop_tests.json`, `loop_series.json`: the controller tests.
- `benchmark_results/runs_llm_untimed/`: the first, untimed LLM attempt.
- `benchmark_results/runs_rule_reference/`: the original rule-agent runs, identical to the rerun.

## 8 Oct: rule agent removed, why the LLMs fail, controllers retuned

### Rule agent removed
At the user's request the rule-based action agent is gone everywhere:
- `RuleActionAgent` and the presets `rule`, `rule_twin`, `pca_only` and `pca_only_twin` are deleted.
- **No hidden fallback:** a failed LLM call is now an empty proposal and nothing is applied. No LLM
  call failed in the 60 campaign runs, so the LLM results were already 100 % LLM decisions.
- **Without an LLM:** the server and campaign tools only monitor (PCA detection, no actions).
- **Tests:** they use a scripted fake LLM instead. All 86 pass.
- **Archive:** the old rule-agent runs are kept in `runs_rule_reference/` for traceability only.

### Why the LLMs fail: model size vs context vs genuine (`benchmark/llm_ablation.py`, `ablation/ABLATION.md`)
**Setup.** 6 hard scenarios (S08, C01, D04, S09, S05, S07), feeling-lucky mode (no validator),
2 repeats each, 2 models (gpt-5-nano, gpt-5-mini) and 4 context levels. Each level adds to the one
before:
- `base`: the campaign prompt.
- `loops`: what each loop's setpoint physically does; the brief's levers are suggestions, not
  mandatory; the "change only one thing" rule is removed.
- `manual`: a generic operating manual (column vapour/condensation balance, reflux-drum balance,
  biased transmitters).
- `dynamics`: rate of change and minutes to the next alarm / SIF for each key variable, plus a rule
  to size the action to the time available instead of "prefer small changes".

That makes 96 runs, about $2.2.

| | base | loops | manual | dynamics |
|---|---|---|---|---|
| gpt-5-nano saved / 12 | 4 | 1 | 2 | 1 |
| gpt-5-mini saved / 12 | 2 | 1 | 0 | 0 |

**Findings:**
1. **Not model size.** gpt-5-mini is no better than gpt-5-nano.
2. **Context changes the behaviour, not the outcome.**
   - With the loop guide and manual, gpt-5-mini starts cutting steam in every condenser case
     (0/4 → 4/4 runs); gpt-5-nano never does.
   - With `dynamics`, gpt-5-mini acts decisively, with several levers at once in the first minute.
3. **A genuine reasoning error decides the drum cases.** Both models raise reflux (lower the TOP_TIC
   setpoint) when the reflux drum is draining. Reflux is drawn from that drum, so this drains it
   faster. They do it even when the prompt states explicitly that raising TOP_TIC cuts reflux and
   that only less reflux stops a draining drum: gpt-5-mini went the wrong way in 9 of 11 attempts.
   - **Counterfactual** (S08, old tuning): gpt-5-mini's own first plan (throughput 40, BOT_TIC 348,
     TOP_TIC 328) trips at 20.1 min, earlier than doing nothing (21.5 min).
   - The same plan with only the TOP_TIC direction flipped (335.5) avoids the trip: the drum bottoms
     out at 17.5 kmol.
4. **Upstream diagnosis errors.** The PCA diagnosis mislabels the reactor-pressure sensor bias as an
   H2 PCV fault and emits non-fault labels such as `DR_LIC_out_of_range`, which the LLM then acts on.
5. **Little time to act.** The column faults leave about 10 min between detection and the trip. That
   is enough only for a correct, decisive plan in the first decision.

### Controllers retuned (`benchmark/tune_loops.py`, `loop_tuning.json`)
The slow loops were retuned with a grid search. The new gain kp and integral time Ti (old values in
brackets) and the step-test results:

| Loop | kp | Ti | Step up | Step back |
|---|---|---|---|---|
| R_LIC | −6 (−2) | 2 h (0.2 h) | overshoot 4 %, settles in 9.5 min | 12 min |
| RB_LIC | −6 (−2) | 2 h (0.25 h) | 7 %, 14 min | 16 min |
| DR_LIC | −12 (−4) | 2 h (0.2 h) | 8 %, 8 min | 7.5 min |
| COL_PIC | −4.5 (−1.5) | 0.2 h (0.1 h) | 20 %, 8.5 min | 26 min |

- **Before:** these loops overshot 30–40 % and had not settled after 40 min.
- **Now:** all 8 loops pass both steps, and all 3 disturbance tests pass.
- **Plots:** `loop_plots/`; the earlier plots are in `loop_plots_before_retune/`.
- **PCA retrained** on the retuned plant: 23 PCs, T² limit 67.9, SPE limit 5.07, 0 % false alarms
  before onset. The old model is kept as `fdd_results/pca_model_before_retune.json`.
- **Not yet re-run:** the campaign results above use the OLD tuning and the OLD PCA model, so the
  probe, no-agent and LLM runs need re-running on the retuned plant.

### Knowledge graph or the model? (KG ablation, same harness, `ablation/ABLATION.md` rows `*-nokg`)
**What the knowledge graph contains.** On the reflux drum it is correct but incomplete:
- **Correct:** the drum balance `dM_drum/dt = F_cond − (reflux + distillate + leak)`, and the
  low-drum SIF cuts reflux to 0.
- **Missing:**
  - which way each loop acts (TOP_TIC says only PV = top temperature, MV = reflux);
  - remedies for a drum leak or a condenser loss.

**Experiment.** The drum / condenser scenarios (S09, D04, S08, C01) with the knowledge graph
switched off: no KG evidence, and no KG levers in the brief. Both models, `base` and `dynamics`
contexts, 2 repeats each.
- **The reflux error is not caused by the knowledge graph.** With KG off and `dynamics` context,
  gpt-5-mini still moved TOP_TIC the wrong way in 8 of 10 attempts (with KG: 9 of 11). With KG off
  and the `base` context, neither model touched reflux at all. Without the knowledge graph the
  models simply don't consider that lever.
- **It's a genuine model reasoning error.** Both models treat "more reflux" as helping a draining
  drum.
- **Caveat:** the no-KG runs ran after the controller retune, so their saved / stopped counts are
  not comparable with the earlier rows. The direction-of-reflux comparison does not depend on it.

### LLM diagnosis agent (`agents/diagnosis_llm.py`, `benchmark/diagnosis_eval.py`, `DIAGNOSIS_EVAL.md`)
**Inputs:**
- T² and SPE against their limits, and the top contributors, each split into its T² and SPE parts;
- the rule checks (alarms, failed pumps, saturated loops, SIFs) and the rule-based hypotheses;
- the trends;
- the fault catalogue (knowledge-graph fault modes + fault library).

**Output:** ranked hypotheses with evidence for and against, the fault class, a reading of the
T² / SPE pattern, and operator checks.

**Evaluation** at PCA confirmation, on the retuned plant with no agents acting:

| | heuristic | gpt-5-nano | gpt-5-mini |
|---|---:|---:|---:|
| most likely fault correct | 14/20 | 15/20 | 18/20 |
| a true fault in the top 3 | 20/20 | 20/20 | 20/20 |

Five minutes after confirmation: 15 / 16 / 17 correct (top-1).

**Where it is used:**
- **Pipeline:** `AgentConfig.diagnoser="llm"`, the server's default when the LLM is on.
- **Console:** the Fault-detection tab shows the latest LLM diagnosis, and **Ask the LLM now**
  (`diagnose_now`) runs one on demand.

## Fresh campaign on the retuned plant (8 Oct)
**Setup:**
- retuned controllers, PCA retrained on the retuned plant;
- **LLM diagnosis agent on** (`PLANT_DIAGNOSER=llm`);
- gpt-5-nano, original action prompt (`base` context);
- new probe runs, so the fault-lift times are recomputed for the retuned plant;
- previous results archived in `runs_old_tuning/`.

**Probe runs.** 18/20 scenarios still trip if nobody acts. Several trip later than before, because
the retuned loops buy time:

| Scenario | Trip time now | Before retune |
|---|---|---|
| S09 drum leak | 46.2 min | 38.6 min |
| D04 | 46.7 min | 40.6 min |
| S04 feed-pump trip | 81 min | 72 min |
| S06 | 178 min | 163 min |

| Configuration | Plant stops | Profit | Saving vs no agents | LLM calls | LLM cost | Mean time per decision | Old tuning (stops / saving) |
|---|---:|---:|---:|---:|---:|---:|---|
| No agents | 18 | −$366,610 | — | — | — | — | 18 |
| LLM, feeling lucky | 7 | −$155,076 | $211,534 | 734 | $0.53 | 44 s | 12 / $122,207 |
| LLM + LLM judge | 8 | −$159,462 | $207,148 | 1,448 | $1.09 | 48 s | 9 / $192,841 |
| LLM + digital twin | **4** | −$82,042 | **$284,568** | 1,290 | $0.99 | 275 s | 7 / $230,809 |

Nominal reference over the same windows: $34,202. Total LLM cost: $2.62.

**Findings:**
- **LLM + twin is now clearly best.** Its only stops are the three condenser-cooling-loss cases
  (S08, C01, T02) and the unrecoverable T03. It saved both drum-leak cases (S09, C03) that every LLM
  configuration lost on the old tuning.
- **Every LLM configuration improved** on the old tuning (lucky 12 → 7 stops). The cause is the
  retune, which gives more time before a trip, plus the LLM diagnosis agent. This run does not
  separate the two effects.
- **The condenser-loss cases still fail in every configuration.** These are the reflux / boil-up
  reasoning cases from the ablation.
- **LLM diagnosis.** The first diagnosis in each run names a true fault in 46 of 60 runs. Later in a
  run the label can drift after the agents act: in S01 the post-action transient was relabelled as
  condenser loss.
- **Decisions are slower** than in the old campaign, because of the extra diagnosis call per
  decision and many runs sharing the CPU. Mean LLM latency was 8–9 s per call.
