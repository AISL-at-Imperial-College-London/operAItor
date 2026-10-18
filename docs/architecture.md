# Agents, scored fault scenarios and web console

```
 React console (frontend/) ──WebSocket /ws──► FastAPI (operaitor/server/app.py)
                                                   │
                                  PlantService (operaitor/server/plant_service.py)
                       owns EpisodeRunner · steps it on a worker thread · human test / benchmark
                         │ latest observation                       ▲ apply_action (locked)
                         ▼                                          │
            AsyncSupervisor (operaitor/agents/supervisor.py) runs the recovery LangGraph:

   START → monitoring ──no persistent PCA fault / cooldown──────────────► END
              │  trigger: PCA only (T² or SPE over its limit for 3 samples)
              │  hypotheses (PCA contributions + alarm / pump / loop evidence)
              ▼
         kg_retrieval → planning → action_propose → digital twin → validate → dispatch → END
                                        ▲                                    not restored │
                                        └──── reprompt ◄──────────────────────────────────┘
                     retries exhausted → best candidate that was at least safe → dispatch (mitigation)

            ChatAgent (operaitor/agents/chat.py): LangGraph tool loop over live data + KG
```

The plant loop and the agents are separate asyncio tasks. A slow decision (an
LLM call or twin rollout) never pauses the simulation, and the agents always
take the newest observation. Plant steps, operator actions, agent actions and
injections all go through one `asyncio.Lock`.

## Quick start (repo root)

```powershell
pip install -e ".[llm]"                               # or ".[llm,admit,dev]"
copy .env.example .env                                 # then put your OPENAI_API_KEY in .env (git-ignored)
operaitor train-pca --evaluate                    # only after simulator changes (~3 min)
cd frontend; npm install; npm run build; cd ..         # once

operaitor serve                                    # http://localhost:8001
```

In the console:

- **Plant** tab: process graphic with all safety functions, key variables, control loops, active alarms.
- **Trends**, **Agents** (pipeline, current decision, fault detection, ADMIT trace, timeline), **Alarms** (active alarms + event log).
- Header buttons open a side panel: **Operate** (operator actions), **Assistant** (chat, OpenAI key,
  agent configuration), **Faults** (inject a fault). **New episode** starts a fresh plant (normal, with disturbances,
  or with hidden faults).

### Watching the agents (Agents tab)

- **Agent pipeline.** A live strip of the decision currently in progress:
  Monitoring → Knowledge graph → Planning → Action agent → Digital twin →
  Validate → Dispatch, plus the ↺ Reprompter loop.
  - A plan that cannot be executed (unknown loop, out-of-range value) is
    rejected by Validate ("plan executable") and reprompted.
  - The *digital twin* simulates the candidate for 30 min on a copy of the
    plant.
  - *Validate* (default `acceptance="alarms"`) accepts when the metrics that
    are faulty at decision time return within their limits and stay there for
    2 min, with the plant safe and no new safety alarm:
    - active alarms → their alarm limit;
    - failed duty pumps → running again;
    - a fault confirmed by PCA → Hotelling T² and SPE back under their limits
      (the PCA statistic is the faulty metric);
    - new safety alarms (priority 1–2) block; new quality alarms (priority 3,
      purity / bottoms acetone) are only noted.

    Other options: `acceptance="restore"` (fully normal, PCA included) or
    `"safe"` (SIF envelope only). A rejection's failed checks go to the
    reprompter. When retries run out, the best safe candidate is applied as a
    **mitigation** (`fallback_to_safe`).
  - *Verification window*: after an accepted action, no new decision is taken
    until the twin's predicted recovery time (+2 min) has passed. After a
    mitigation, no new decision is taken for the twin's whole 30-min look-ahead.
    In both cases a newly failed pump still triggers immediately.
  - If an LLM call fails, that attempt is an empty proposal (logged as "LLM call failed");
    nothing is applied and the next attempt asks the LLM again. There is no rule-based fallback.
- **Every action comes from the LLM.** There is no rule-based action agent. With OpenAI the
  preset is `llm`; without an LLM the agents only monitor (preset `passive`).

### Pacing and holding the plant (top bar)

- **Pacing.** The plant sleeps 0.05 s after every 10 iterations (one iteration
  = 0.5 sim-min) by default. The pacing selector offers 0.2 s / 10, 0.5 s / 10,
  0.25 s per iteration, or none. The speed selector adds a fixed delay per
  iteration on top.
- **Hold for agents** (on by default). Once a fault is confirmed, the plant
  pauses until the agents finish the decision (KG → plan → act → twin →
  reprompt → dispatch). The action then lands on the state they analysed;
  without the hold, a 15-min twin rollout can arrive too late. The top bar shows
  "HELD · agents deciding" while this happens, and a 3-min timeout means a
  stuck decision can never freeze the plant.

  Untick the box to run the plant and agents fully concurrently (the async
  setting studied in the paper).
- **Episode length.** Episodes default to 360 min (top-bar horizon); a benchmark scenario runs for its scoring
  window.

Without `langgraph` the server still starts. The plant, console, human test and
offline chat work; the agents show as offline.

### OpenAI

By default (`--llm auto`) the server uses OpenAI **`gpt-5-nano`**, the cheapest
OpenAI text model ($0.05 / $0.40 per 1M input / output tokens on
[OpenAI's pricing page](https://developers.openai.com/api/docs/pricing), checked
2026-10-05). It uses it for:

- the action agent (preset `llm`: KG planning brief → LLM action → twin check);
- the chat agent (OpenAI tool calling over the plant tools).

It does this whenever `langchain-openai` is installed and `OPENAI_API_KEY` is
set, either in the environment or in `.env` in the repo root. This repo already
has a `.env` containing a key, so `operaitor serve` makes real
(cheap) API calls; use `--llm off` for none. If the package or key is missing,
the agents only monitor (no actions), the chat is offline, and the server prints why.

Startup prints, e.g.: `Agents: preset llm, planning/action model gpt-5-nano, chat llm gpt-5-nano`.
The Agents tab shows the model and the running token count and cost.

Settings for GPT-5 / o-series reasoning models (`operaitor/agents/llm.py`):

- no `temperature` (the API rejects it);
- `reasoning_effort=low` (override with `PLANT_REASONING_EFFORT`);
- JSON mode for the planning and action agents.

Cost estimates use the `PRICES_PER_1M` table there. Update it if OpenAI changes
prices.

Server options:

```powershell
operaitor serve --llm off                         # no API calls at all
operaitor serve --llm on                          # fail at startup if OpenAI is not usable
operaitor serve --agent llm_plan                  # LLM planning agent as well (2 calls per decision)
operaitor serve --llm-model gpt-5-mini            # stronger, ~5x the price
operaitor serve --agent llm_judge                 # LLM proposes, a second LLM validates (no twin)
operaitor serve --diagnoser heuristic             # rule-based diagnosis instead of the LLM diagnosis agent
operaitor serve --port 9000
```

- **Agent presets:** `passive` (monitor only), `llm` (LLM + digital twin), `llm_admit` (LLM + ADMIT gates,
  see [admit.md](admit.md)), `llm_judge` (LLM + LLM judge), `llm_lucky` (LLM, no validation), `llm_plan`
  (LLM planning agent too), `llm_nokg`, `llm_notwin`.
- **Models:** `gpt*` / `o*` names use langchain-openai; any other name uses a local Ollama.

## Scored fault scenarios

Every named fault is in the fault library, `operaitor/server/fault_library.py` (`FAULT_LIBRARY`). Its names are the
knowledge graph's fault modes and the diagnosis vocabulary, so a fault can be injected, diagnosed and scored under
one name. The 20 benchmark scenarios (`operaitor/benchmark/profit_campaign.py`) combine these faults with fixed
onset and lift times, and are replayed in the console in two ways, with the benchmark's schedule, shift operator and
score (`operaitor/server/human_test.py`):

- **Human test** (Operate panel): a person answers one scenario, optionally blind, and is ranked against the
  reference runs.
- **Pipeline benchmark** (Agents → Customise the agents → Benchmark): the running pipeline answers 3, 20 or one
  scenario, each scored next to the reference runs ([pipelines.md](pipelines.md)).

Reports go to the campaign folder (`PLANT_CAMPAIGN_DIR`, `campaign_results/` by default; none on a public server).
The **Faults** panel injects single faults by hand.

## The agents

| Module | Role |
|---|---|
| `operaitor/fdd/pca.py`, `operaitor/fdd/diagnosis.py` | PCA T²/SPE limits and contributions; rule checks; hypotheses from contributions, alarms, pumps and saturated loops |
| `operaitor/agents/monitoring.py` | Monitoring agent: PCA is the only trigger (3-sample persistence, 5-min cooldown); alarm / pump / loop rules only feed the diagnosis. `AgentConfig(rules_trigger=True)` restores rule triggering; rules also trigger if no PCA model is loaded |
| `operaitor/agents/kg.py` | KG retrieval seeded by the hypotheses (fault → symptoms → loops/actuators → allowed operator actions) and free-text search |
| `operaitor/agents/planning.py` | Planning agent: target fault, objective, KG-grounded candidate levers, constraints (`KGPlanner`, or `LLMPlanner`) |
| `operaitor/agents/action.py` | Action agent: `LLMActionAgent` (context levels `PLANT_ACTION_CONTEXT`: base / loops / manual / dynamics) |
| `operaitor/agents/diagnosis_llm.py` | LLM diagnosis agent: reads T² / SPE, contributions, rule evidence, trends and the fault catalogue; ranked hypotheses with evidence for / against |
| `operaitor/agents/judge.py` | LLM judge (validator without a simulator) |
| `operaitor/agents/recovery_graph.py` | The LangGraph, presets, oracle-twin rollout. Shared by the server, the benchmark and the tests |
| `operaitor/agents/supervisor.py` | Runs the graph next to the live plant. Modes: off / advisory (approve in UI) / autonomous |
| `operaitor/agents/chat.py` | Chat agent: tools `plant_status`, `current_values`, `trend`, `fault_diagnosis`, `agent_activity`, `knowledge_graph`, `list_variables`; per-session memory |

### LLM diagnosis agent

When a fault is confirmed (and on demand: **Ask the LLM now** in the Fault-detection tab, or the
`diagnose_now` command), `operaitor/agents/diagnosis_llm.py` gets:
- Hotelling T² and SPE (Q) against their limits, and the top contributors, split into T² and SPE parts;
- the rule checks (alarms, failed pumps, saturated loops, SIFs) and the rule-based hypotheses;
- the trends (rate, minutes to the next alarm / SIF);
- the fault catalogue (knowledge-graph fault modes + fault library, with symptoms).

It returns ranked hypotheses (catalogue names only) with evidence for and against, the fault class
(sensor / actuator / equipment / process), what the T² vs SPE pattern says, and checks for the
operator. With `diagnoser="llm"` (the server's default when the LLM is on) its hypotheses replace
the heuristic ones in the pipeline (the heuristic result is kept). Evaluation on the 20 scenarios,
at PCA confirmation (`operaitor python -m operaitor.benchmark.diagnosis_eval`, `benchmark_results/DIAGNOSIS_EVAL.md`):

| | heuristic | gpt-5-nano | gpt-5-mini |
|---|---:|---:|---:|
| most likely fault correct | 14/20 | 15/20 | 18/20 |
| a true fault in the top 3 | 20/20 | 20/20 | 20/20 |

### Validation, fallback, SIF reset and alarms (2026-10-06)

- **Every proposal is rolled out by the twin, NO_ACTION included.** Doing nothing is accepted only if
  the twin shows the plant recovering by itself.
- **Counterfactual fallback.** When no candidate restores the plant (e.g. several faults at once, one
  of them a sensor fault), the "do nothing" rollout is the baseline. The candidate that beats it is
  applied as a mitigation; the ranking is in `consequence_key`:
  1. no trip (or a later one);
  2. more faulty targets back within limits;
  3. less time unsafe;
  4. lower PCA T² + SPE.

  After a mitigation the agents re-decide after `mitigation_wait_min` (10 min).
- **The twin knows only the present.** `clone_env(present_only=True)` keeps the faults active now
  persistent and removes future injections. The twin cannot "know" when a fault will clear.
- **SIF reset** (`sif_reset=True`): the agents request a reset while non-trip SIFs are latched. The
  The plant model (docs/plant-model.md) lists SIF reset as an operator/agent action. The SIF logic resets only latches whose variable
  is back in the reset band. Plant-trip latches are left for the operator.
- **Alarm acknowledgement:**
  - When the agents apply an action, they acknowledge the standing alarms. Each alarm then leaves the
    Alarms list by itself once its variable is back within limits.
  - Alarms that returned to normal during an incident the agents handled are acknowledged, which
    removes them from the list.
  - Alarms with no agent involvement stay for the operator.
  - The alarm history shows who acknowledged each alarm (`· action-agent`).

The chat agent is read-only and sees only what an operator sees, never the
injected ground truth. Without `--chat-model`, a deterministic intent router
answers with the same tools. Examples: "what is R_PIC?", "how is the reactor
level moving?", "why is there a fault?", "what did the agents do?".

## KG ↔ simulator translation

[kg_map.py](../operaitor/agents/kg_map.py) maps every KG node to its simulator counterpart. It
is built from the KG and the simulator's own catalogs, so it stays in sync:

| KG node | Simulator counterpart |
|---|---|
| `controller_*` / `PO_*` | Loop, with its PV, MV and setpoint range |
| `act_param_*` | Input, and the loop or auxiliary that drives it |
| `obs_param_*` | Measurement, with its loop and alarms |
| `pump_train_*`, `P-…` | Pump train / pump |
| `alarm_*`, `SIF_*`, `fault_*` | Alarm, safety trip, fault library |
| Equipment tags (`PT-101`, `PCV-101`, `CV-201`, `BDV-101`, `E-102` …) | Their loop, measurement or auxiliary |
| `exo_param_*` | Exogenous variable, flagged not commandable |

It is used in three places:

- **KG evidence.** Every node sent to the agents carries a `sim` field with the
  simulator name and the exact command to use.
- **Command targets.** Whatever name an agent writes is resolved to the catalog
  target (e.g. `PCV-101`, `controller_R_PIC`, `obs_param_cstr_pressure_bar` →
  `R_PIC`; `P-201A` → `REFLUX`).
- **Chat.** KG answers include "In the simulation: …".

Regenerate the table and coverage report with `python -m operaitor.agents.kg_map`. It writes
`operaitor/data/knowledge_graph/kg_sim_mapping.json`.

## Normal disturbances

Disturbances are physical steps (`DISTURBANCE_STEPS` in `operaitor/plant/scenario.py`).
The console, the benchmark scenarios and the random scenario generator all use them:

| Variable | Step |
|---|---|
| Cooling water temperature | ±3 K |
| Feed temperature | ±5 K |
| Reactor heating utility temperature | ±5 K |
| Ambient temperature | ±3 K |
| Steam header pressure | ±10% |

Before 2026-10-05 every variable was scaled by ±10%. On absolute temperatures that
meant ±30–39 K (e.g. cooling water at 55 °C), which is a severe upset rather than
normal variability. `randomized_step_disturbance_scenario(..., magnitude_fraction=0.10)`
still reproduces the old behaviour.

## PCA model

The model has 42 features: 34 BPCS measurements plus 8 controller outputs. It is
trained on six normal-operation runs: nominal, three seeded runs with physical
disturbance steps, and steady operation at 70% and 85% throughput.

It keeps 20 PCs (95.4% of variance), with limits max(theoretical, empirical 99th
percentile). That gives a 1.97% per-sample false-alarm rate on training data and 0%
before onset in the fault tests. Detection delays at a 15-min onset (3-sample persistence):

| Fault | Delay (min) |
|---|---|
| Sensor bias | 0 |
| Pump trip | 1.0 |
| Condenser loss | 0.5 |
| Stuck heater | 0.5 |
| Drum leak | 1.5 |
| PCV degradation | 7.5 |

Retrain after simulator changes with `operaitor train-pca --evaluate` (about 4 min).

## Benchmark

**20-scenario profit campaign** (`operaitor/benchmark/profit_campaign.py`, economics in `operaitor/benchmark/economics.py`).
There are 10 single-fault, 7 two-fault and 3 three-fault scenarios. Each runs without agents and with
agents, against a nominal reference, and faults are lifted 10 min after the time the plant would have
tripped. Results are in `benchmark_results/PROFIT_CAMPAIGN.md`.

```powershell
operaitor benchmark probe --jobs 21     # when would each scenario trip?
operaitor benchmark compare --jobs 20   # agents + passive with the lift schedule
operaitor benchmark report
operaitor loop-tests                        # PI loop setpoint tests + plots (benchmark_results/loop_plots/)
operaitor dashboard                     # benchmark_results/fault_campaign_dashboard.html
```

**Agent configurations in the campaign** (`compare <modes>`; all use the same faults and lift times):

| mode | preset | proposer | validation |
|---|---|---|---|
| `lucky` | `llm_lucky` | LLM | none: the first executable proposal is applied ("feeling lucky") |
| `judge` | `llm_judge` | LLM | a second LLM call (`operaitor/agents/judge.py`) accepts or rejects; rejections reprompt (3 retries); nothing is applied if every proposal is rejected |
| `llmtwin` | `llm` | LLM | digital twin, counterfactual fallback |

```powershell
operaitor benchmark compare lucky judge llmtwin --jobs 12                      # gpt-5-nano (OpenAI key in .env)
operaitor benchmark compare lucky --model llama3.1:8b                           # native Ollama
operaitor benchmark compare judge --model qwen2.5-7b --base-url http://localhost:1234/v1   # LM Studio / vLLM / llama.cpp
```

Local-model environment variables (also used by `operaitor serve`): `PLANT_LLM_BASE_URL` (any
OpenAI-compatible server, any model name), `PLANT_LLM_API_KEY`, `PLANT_LLM_JSON_MODE=0` (if the
server rejects JSON mode), `PLANT_OLLAMA_URL`, `PLANT_LLM_MAX_RETRIES`.

## Known limitations

- **Diagnosis can confuse faults.** Contribution-based diagnosis can mix up
  faults with similar signatures; for example, condenser loss can show up as
  pump or level hypotheses. Alarm evidence helps before PCA confirms.
- **LLM reasoning errors on the column.** gpt-5-nano and gpt-5-mini raise reflux when the reflux
  drum drains (wrong direction), even with the loop directions and the drum balance in the
  prompt and with the knowledge graph switched off (see `benchmark_results/LLM_CAMPAIGN_LOG.md`).
- **The twin is an oracle.** It is a clone of the true simulator state.
- **The simulator is CPU bound.** It needs about 1 s of CPU per simulated minute.
