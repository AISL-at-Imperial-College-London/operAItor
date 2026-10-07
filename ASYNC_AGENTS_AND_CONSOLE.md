# Agents, fault campaigns and web console

```
 React console (frontend/) ──WebSocket /ws──► FastAPI (server/app.py)
                                                   │
                                  PlantService (server/plant_service.py)
                       owns EpisodeRunner · steps it on a worker thread · campaign scheduler
                         │ latest observation                       ▲ apply_action (locked)
                         ▼                                          │
            AsyncSupervisor (agents/supervisor.py) runs the recovery LangGraph:

   START → monitoring ──no persistent PCA fault / cooldown──────────────► END
              │  trigger: PCA only (T² or SPE over its limit for 3 samples)
              │  hypotheses (PCA contributions + alarm / pump / loop evidence)
              ▼
         kg_retrieval → planning → action_propose → digital twin → validate → dispatch → END
                                        ▲                                    not restored │
                                        └──── reprompt ◄──────────────────────────────────┘
                     retries exhausted → best candidate that was at least safe → dispatch (mitigation)

            ChatAgent (agents/chat.py): LangGraph tool loop over live data + KG
```

The plant loop and the agents are separate asyncio tasks. A slow decision (an
LLM call or twin rollout) never pauses the simulation, and the agents always
take the newest observation. Plant steps, operator actions, agent actions and
injections all go through one `asyncio.Lock`.

## Quick start (PowerShell, repo root, system Python)

```powershell
py -3 -m pip install -r requirements-server.txt       # fastapi, uvicorn, langgraph, langchain-openai
copy .env.example .env                                 # then put your OPENAI_API_KEY in .env (git-ignored)
py -3 train_pca.py --evaluate                          # once: fdd_results/pca_model.json (~3 min)
cd frontend; npm install; npm run build; cd ..         # once

py -3 run_server.py                                    # http://localhost:8001
```

In the console:

- **Master / faults** tab: start a fault campaign.
- **Assistant** tab: chat with the plant.
- **FDD & Agents** tab: watch the agent pipeline.

### Watching the agents (FDD & Agents tab)

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
  - If an LLM call fails, that attempt falls back to the rule agent (shown as
    "LLM call failed, rule fallback used").
- **Twin and reprompter are on in both defaults.** With OpenAI the preset is
  `llm`. Without it, the preset is `rule_twin`: the rule agent proposes
  candidates in order and escalates on each retry, e.g. a throughput cut of
  20% → 35% → 50% as the twin rejects them. The plain `rule` preset has the
  twin off and no retries.

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
- **Episode length.** Episodes default to 360 min (top-bar horizon), and
  campaigns keep running 30 min after the last fault.

Without `langgraph` the server still starts. The plant, console, campaigns and
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
has a `.env` containing a key, so `py -3 run_server.py` makes real
(cheap) API calls; use `--llm off` for none. If the package or key is missing,
the server falls back to `rule_twin` and offline chat and prints why.

Startup prints, e.g.: `Agents: preset llm, planning/action model gpt-5-nano, chat llm gpt-5-nano`.
The FDD & Agents tab shows the model and the running token count and cost.

Settings for GPT-5 / o-series reasoning models (`agents/llm.py`):

- no `temperature` (the API rejects it);
- `reasoning_effort=low` (override with `PLANT_REASONING_EFFORT`);
- JSON mode for the planning and action agents.

Cost estimates use the `PRICES_PER_1M` table there. Update it if OpenAI changes
prices.

Server options:

```powershell
py -3 run_server.py --llm off                         # no API calls at all
py -3 run_server.py --llm on                          # fail at startup if OpenAI is not usable
py -3 run_server.py --agent llm_plan                  # LLM planning agent as well (2 calls per decision)
py -3 run_server.py --llm-model gpt-5-mini            # stronger, ~5x the price
py -3 run_server.py --agent rule_twin --llm off       # twin-validated rule agent
py -3 run_server.py --port 9000
```

- **Agent presets:** `passive`, `rule`, `rule_twin`, `pca_only`, `llm`, `llm_plan` (LLM planning agent too), `llm_nokg`, `llm_notwin`.
- **Models:** `gpt*` / `o*` names use langchain-openai; any other name uses a local Ollama.

## Fault campaigns (automated injection)

```powershell
py -3 fault_campaign.py list                                  # presets + fault library
py -3 fault_campaign.py run --campaign demo                    # against the running server (watch the console)
py -3 fault_campaign.py run --campaign random --seed 3 --n 5   # seeded random sequence
py -3 fault_campaign.py run --campaign demo --headless         # no server, plant + agents in-process
py -3 fault_campaign.py run --campaign reprompt_demo           # one severe condenser loss: watch twin rejections + retries
py -3 fault_campaign.py run --campaign my_campaign.json        # custom: {"items": [{"at_min": 5, "fault": "...", "duration_min": 10}]}
```

A campaign is a timed list of injections from `server/campaigns.py`
(`FAULT_LIBRARY`). Its fault names are the KG fault-mode names, so each run can
be scored. By default:

- **After each fault clears:** the campaign restores the operator settings it
  started with and keeps requesting a SIF reset. A latch only clears once its
  variable is back inside the reset band.
- **Before the next injection:** the campaign waits until the monitoring agent
  has reported normal for 3 min (at most 45 min). Later injections shift by the
  same amount.

At the end, `campaign_results/<name>_<timestamp>.json` holds the schedule, a
per-fault report and the full agent log. The report gives, for each fault:
detection delay, top-1/top-3 diagnosis, the planning agent's target, and the
first action. A fault injected while the plant was still abnormal is marked
**carry-over** and left out of the scores.

Example with the rule agent on `demo`:

| Fault | Detected | Delay (min) | Diagnosis | Action |
|---|---|---|---|---|
| Pump trip | yes | 0.5 | correct (top 1) | start spare |
| Pressure-sensor bias | yes | 1.5 | correct in top 3 | open blowdown, later closed again |
| Outlet capacity loss | yes | 2.0 | correct (top 1) | — |
| Condenser loss | carry-over | — | — | — |

The plant needs more than 45 min to settle after the outlet fault, so the
condenser fault started while it was still abnormal.

## The agents

| Module | Role |
|---|---|
| `fdd/pca.py`, `fdd/diagnosis.py` | PCA T²/SPE limits and contributions; rule checks; hypotheses from contributions, alarms, pumps and saturated loops |
| `agents/monitoring.py` | Monitoring agent: PCA is the only trigger (3-sample persistence, 5-min cooldown); alarm / pump / loop rules only feed the diagnosis. `AgentConfig(rules_trigger=True)` restores rule triggering; rules also trigger if no PCA model is loaded |
| `agents/kg.py` | KG retrieval seeded by the hypotheses (fault → symptoms → loops/actuators → allowed operator actions) and free-text search |
| `agents/planning.py` | Planning agent: target fault, objective, KG-grounded candidate levers, constraints (`KGPlanner`, or `LLMPlanner`) |
| `agents/action.py` | Action agent: `RuleActionAgent` (incl. unwinding of earlier emergency moves) or `LLMActionAgent` |
| `agents/recovery_graph.py` | The LangGraph, presets, oracle-twin rollout. Shared by the server and `recovery_agents_langgraph.py` |
| `agents/supervisor.py` | Runs the graph next to the live plant. Modes: off / advisory (approve in UI) / autonomous |
| `agents/chat.py` | Chat agent: tools `plant_status`, `current_values`, `trend`, `fault_diagnosis`, `agent_activity`, `knowledge_graph`, `list_variables`; per-session memory |

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
  README lists SIF reset as an operator/agent action. The SIF logic resets only latches whose variable
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

[agents/kg_map.py](agents/kg_map.py) maps every KG node to its simulator counterpart. It
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

Regenerate the table and coverage report with `py -3 -m agents.kg_map`. It writes
`knowledge_graph/kg_sim_mapping.json`.

## Normal disturbances

Disturbances are physical steps (`DISTURBANCE_STEPS` in `integrated_plant/scenario.py`).
The console, the campaigns and the random scenario generator all use them:

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

Retrain after simulator changes with `py -3 train_pca.py --evaluate` (about 4 min).

## Benchmark

`recovery_agents_langgraph.py` runs the same graph on reproducible episodes,
with frozen, async or delay clocks, and sweeps faults × seeds × presets. See
[RECOVERY_BENCHMARK.md](RECOVERY_BENCHMARK.md).

**20-scenario profit campaign** (`benchmark/profit_campaign.py`, economics in `benchmark/economics.py`).
There are 10 single-fault, 7 two-fault and 3 three-fault scenarios. Each runs without agents and with
agents, against a nominal reference, and faults are lifted 10 min after the time the plant would have
tripped. Results are in `benchmark_results/PROFIT_CAMPAIGN.md`.

```powershell
py -3 -m benchmark.profit_campaign probe --jobs 21     # when would each scenario trip?
py -3 -m benchmark.profit_campaign compare --jobs 20   # agents + passive with the lift schedule
py -3 -m benchmark.profit_campaign report
py -3 -m benchmark.loop_tests                          # BPCS loop step / disturbance tests
```

## Known limitations

- **Diagnosis can confuse faults.** Contribution-based diagnosis can mix up
  faults with similar signatures; for example, condenser loss can show up as
  pump or level hypotheses. Alarm evidence helps before PCA confirms.
- **The rule action agent is limited** (it is what runs with `--llm off`). Its responses are:
  - start spare pumps (all at once if several failed);
  - open the blowdown;
  - for a condenser-limited column, cut boilup, reflux and throughput together;
  - cut throughput;
  - raise the TOP_TIC setpoint when the reflux drum drains;
  - unwind once the plant is quiet: close the blowdown, and return setpoints and throughput to nominal.

  It returns NO_ACTION otherwise. That gap is what the LLM presets are meant to fill.
- **The twin is an oracle.** It is a clone of the true simulator state.
- **The simulator is CPU bound.** It needs about 1 s of CPU per simulated minute.
