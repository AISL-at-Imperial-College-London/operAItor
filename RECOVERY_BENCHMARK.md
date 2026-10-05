# FDD → KG → action recovery framework and benchmark

`recovery_agents_langgraph.py` is the benchmark harness for the recovery graph in
`agents/recovery_graph.py`, the same graph the live server runs. It adapts the
monitoring / propose / twin-evaluate /
reprompt / apply loop of
`ctrl-alt-recover/case_studies/cstr_case/cstr_case.py` to the integrated acetone
plant. It adds PCA fault detection and diagnosis, structured retrieval from
`knowledge_graph/Integrated_CSTR_Distillation_Knowledge_Graph.jsonld`, and a
planning agent between diagnosis and action, and a choice of three clocks
(`frozen`, `async`, `delay`). The docstring of `agents/recovery_graph.py` shows the graph.

## Setup (system Python, no virtualenv)

```powershell
py -3 -m pip install "langgraph>=1.0,<2"
py -3 -m pip install langchain-openai     # for the LLM presets (OPENAI_API_KEY in env or .env)
py -3 -m pip install langchain-ollama     # only for local Ollama LLM presets
py -3 train_pca.py                        # if fdd_results/pca_model.json is missing
```

## Sanity checks, in this order

```powershell
# 1. Unit tests (~30 s): KG retrieval, planning brief, LLM reprompt path (fake LLM), passive,
#    delay clock, supervisor advisory mode, chat agent (offline + tool loop), campaigns
py -3 -m pytest test_recovery_agents.py test_agents_async.py -q

# 2. Smoke test (~25 s): pump trip at 5 min → detected at 5.5 min → START_SPARE
py -3 recovery_agents_langgraph.py smoke

# 3. Passive vs rule on the same fault: passive should end MODEL_LIMIT, rule should not
py -3 recovery_agents_langgraph.py run --scenario reflux_pump_trip --agent passive --horizon 25 --onset 5
py -3 recovery_agents_langgraph.py run --scenario reflux_pump_trip --agent rule    --horizon 25 --onset 5

# 4. Twin validation on a harder fault (~1 min); the twin rejects throughput cuts here
py -3 recovery_agents_langgraph.py run --scenario condenser_cooling_loss --agent rule_twin --horizon 45 --onset 10

# 5. Async clock: plant at 10× real time; look for "sim_delay_min" on the applied event
py -3 recovery_agents_langgraph.py run --scenario reflux_pump_trip --agent rule_twin --clock async --speedup 10 --horizon 25 --onset 5

# 6. LLM agent with the KG (one episode; default model gpt-5-nano, the cheapest OpenAI model)
py -3 recovery_agents_langgraph.py run --scenario condenser_cooling_loss --agent llm --horizon 45 --onset 10
```

## Benchmark sweeps

```powershell
# Baselines, all 8 curated faults × 3 seeds (~15–30 min with 4 workers)
py -3 recovery_agents_langgraph.py bench --agents passive rule rule_twin pca_only --seeds 3

# LLM ablations (KG / twin)
py -3 recovery_agents_langgraph.py bench --agents llm llm_plan llm_nokg llm_notwin --seeds 3    # gpt-5-nano

# Latency ablation: same agent, increasing decision delay
foreach ($d in 0, 1, 2, 5) { py -3 recovery_agents_langgraph.py bench --agents rule --clock delay --delay-min $d --seeds 3 }

# Rebuild the tables from any run
py -3 recovery_agents_langgraph.py summarize benchmark_results\<timestamp>\runs.csv
```

Each sweep writes the following to `benchmark_results/<timestamp>/`:

- `runs.csv`: one row per episode;
- `summary.csv` and `summary.md`: mean ± 95% CI per agent/clock, plus a per-fault table;
- `traces/*.jsonl`: every monitoring cycle, KG node list, proposal (with KG citations, latency and tokens), twin verdict and applied action;
- `config.json`.

## Metrics (per episode)

| Column | Meaning |
|---|---|
| `detected`, `detection_delay_min`, `false_alarm_pre_onset` | Monitoring agent versus true fault onset |
| `plan_target`, `plan_target_correct` | Planning agent's target fault versus the truth |
| `diag_top1`, `diag_top3` | First diagnosis versus the true fault-library name |
| `n_proposals`, `n_actions`, `twin_rejections`, `kg_cited` | Action-agent behaviour |
| `mean_sim_delay_min` | Simulated time from observation to applied action (async/delay clocks) |
| `failed` | Final status SAFE_TRIP, DAMAGE, MODEL_LIMIT or NUMERICAL_FAILURE |
| `unsafe_min` | Minutes with true T/P/levels outside the SIF envelope |
| `onspec_acetone_kmol` | Acetone in distillate while x_D ≥ 0.90 |
| `llm_latency_mean_s`, `llm_tokens`, `llm_cost_usd` | Cost of LLM presets (cost from `PRICES_PER_1M` in agents/llm.py) |

## Assumptions to state in a paper

- The twin is an oracle clone of the simulator (true state and fault schedule),
  as in `cstr_case.py`. Proposers see only operator observations and KG evidence.
- KG retrieval is a typed one-hop walk over the local JSON-LD, seeded from the
  diagnosis. It is not SPARQL against GraphDB, as `cstr_case.py` does.
- Curated faults are deterministic; seeds vary the measurement noise only. For
  held-out tests use `--faults surprise`: each seed draws 0–2 random faults
  (including ones outside the curated set) at onsets from 30 min on, so use
  `--horizon 120`.
- Async results depend on `--speedup` and CPU speed. `delay` gives the
  reproducible latency ablation.
