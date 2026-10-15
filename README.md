# OperAItor

[![ci](https://github.com/AISL-at-Imperial-College-London/operAItor/actions/workflows/ci.yml/badge.svg)](https://github.com/AISL-at-Imperial-College-London/operAItor/actions/workflows/ci.yml)
[![website](https://github.com/AISL-at-Imperial-College-London/operAItor/actions/workflows/pages.yml/badge.svg)](https://aisl-at-imperial-college-london.github.io/operAItor/)
[![dashboard](https://img.shields.io/badge/benchmark-dashboard-0a7bbb.svg)](https://aisl-at-imperial-college-london.github.io/operAItor/dashboard/)
[![license](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-%E2%89%A53.11-blue.svg?logo=python&logoColor=white)](pyproject.toml)
[![node](https://img.shields.io/badge/node-20%2B-339933.svg?logo=node.js&logoColor=white)](frontend/package.json)
[![react](https://img.shields.io/badge/console-React%20%2B%20Vite-61dafb.svg?logo=react&logoColor=black)](frontend/)
[![fastapi](https://img.shields.io/badge/server-FastAPI-009688.svg?logo=fastapi&logoColor=white)](operaitor/server/)
[![langgraph](https://img.shields.io/badge/agents-LangGraph-1c3c3c.svg)](operaitor/agents/)
[![ollama](https://img.shields.io/badge/LLM-Ollama%20(free)-000000.svg?logo=ollama&logoColor=white)](https://ollama.com)
[![ADMIT](https://img.shields.io/badge/validation-ADMIT-b31b1b.svg)](https://arxiv.org/abs/2608.03866)

**AI operators for a chemical plant, checked before they act.**

A simulated chemical plant you can run in the browser, break on purpose, and watch AI agents try
to recover. It has fault detection, safety functions, and LLM agents whose actions are checked
before they reach the plant.

- **The plant.** Isopropanol is dehydrogenated to acetone in a CSTR (R-101) that feeds a 10-tray
  distillation column (T-201). It is a dynamic DAE model with 8 PI loops, 5 duty/standby pump
  trains, ISA-18.2 alarms and 11 independent safety instrumented functions (SIFs).
- **Fault detection.** PCA (Hotelling T² and SPE) confirms faults. The diagnosis comes from rules
  or from an LLM diagnosis agent.
- **Recovery agents.** A LangGraph pipeline runs knowledge graph → planning → LLM action agent →
  validation → dispatch, with reprompting when validation rejects. Four validators can be
  compared:

  | Configuration | Validation before an action is applied |
  |---|---|
  | `llm` | **Digital twin**: the action is simulated on a clone of the plant |
  | `llm_admit` | **ADMIT gates**: the agent files an action record, and T0–T4 decide admissibility, with T4 projected on the plant ([docs/admit.md](docs/admit.md)) |
  | `llm_judge` | **LLM judge**: a second LLM reviews the action |
  | `llm_lucky` | none ("feeling lucky") |

- **Web console.** React + uPlot. It has a live P&ID overview, trends, alarms, the fault-detection
  and agent pipeline, a chat assistant, and fault injection. Every visitor gets their own plant.

## Run it

### In the browser (hosted)

Open the live console (linked from the project page) and press **Run**, then inject a fault from the
**Faults** panel. It runs on a dedicated lab machine; [docs/deployment.md](docs/deployment.md) covers
setting one up.

Without an LLM, the plant, the fault detection and the safety functions all work, and the agents
monitor and diagnose but take no actions. To let the agents act, open **Assistant → Connect an LLM**
and pick a provider:
- **Groq, Google Gemini or OpenRouter:** free tiers with open models (Llama, Qwen, Gemma); you only
  need a free account.
- **Your own Ollama server:** free.
- **OpenAI:** optional, billed to your own account.

The key stays in server memory for your session only. The hosted site itself costs nothing to run;
[docs/deployment.md](docs/deployment.md) covers deploying a copy.

### Locally

Requires Python ≥ 3.11 and Node 20+.

```bash
pip install -e ".[llm,admit]"    # add ,dev for tests
cd frontend && npm ci && npm run build && cd ..
ollama pull qwen3:8b             # free local model (https://ollama.com); any Ollama model works
operaitor doctor                 # checks the installation
operaitor serve                  # http://localhost:8001
```

**LLM choice, free first:**
- **Ollama, if it is running, with no OpenAI key set:** the agents use it automatically (`qwen3:8b`,
  or `--llm-model llama3.2`, `mistral`, …). On a laptop GPU a call takes about 10–20 s.
- **OpenAI:** put `OPENAI_API_KEY=...` in `.env` to use OpenAI models instead.
- **In the console:** **Assistant → Connect an LLM** switches to Groq, Gemini, OpenRouter, another
  Ollama server or OpenAI.
- **Any other OpenAI-compatible server:** set `PLANT_LLM_BASE_URL`.

Without any LLM, the agents monitor and diagnose but take no actions, and the chat answers offline.

**Test it as a human:** switch the header from **Agents** to **Human operator**: the agents are off and you
answer the faults. **Operate → Human test** replays one of the 20 benchmark scenarios (or a random one) with
the same faults, timing and score as the agent benchmark. **Blind** hides what was injected until the end.
At the end the console reveals the faults and ranks your score against the benchmark runs on that scenario,
and writes a report to `campaign_results/human_<scenario>_<time>.json`.

Or with Docker:

```bash
docker build -t operaitor .
docker run -p 7860:7860 operaitor                                   # visitors bring their own key
docker run -p 7860:7860 -e PLANT_HOSTED=0 -e OPENAI_API_KEY=sk-... operaitor   # private: server key
```

## Command line

| Command | What it does |
|---|---|
| `operaitor serve [--agent llm_admit] [--llm off] [--hosted]` | Web console and agents |
| `operaitor doctor` | Check dependencies, PCA model, console build and LLM access |
| `operaitor benchmark probe\|compare\|report` | 20-scenario fault campaign: profit and plant trips, with and without agents |
| `operaitor dashboard` | Build the campaign dashboard (HTML) |
| `operaitor loop-tests` | PI loop setpoint-step tests with plots |
| `operaitor train-pca` | Retrain the PCA fault-detection model |
| `operaitor admit build\|validate\|run` | The plant as an ADMIT cartridge ([docs/admit.md](docs/admit.md)) |

## Results

The 20-scenario campaign (run with gpt-5-nano, with the column dry-out SIF) is in
[benchmark_results/LLM_CAMPAIGN_LOG.md](benchmark_results/LLM_CAMPAIGN_LOG.md):

| Configuration | Plant stops / 20 | Saving vs no agents |
|---|---:|---:|
| No agents | 18 | — |
| LLM, no validation | 9 | $179k |
| LLM + LLM judge | 11 | $135k |
| LLM + digital twin | 5 | $260k |

## Repository

```
operaitor/
  plant/       simulator: CSTR, column, control, safety (SIFs), alarms, pumps, faults, episodes
  fdd/         PCA monitor and rule-based diagnosis
  agents/      monitoring, KG, planning, LLM action / diagnosis / judge, recovery graph, chat
  admit/       ADMIT world, gate (validator) and cartridge
  server/      FastAPI + WebSocket app, per-visitor sessions
  benchmark/   fault campaign, economics, loop tests, dashboard
  data/        knowledge graph (JSON-LD), PCA model
frontend/      React console
tests/         pytest suite
docs/          architecture, plant model, ADMIT, deployment
benchmark_results/   campaign results and logs
```

**Documentation:**
- [docs/architecture.md](docs/architecture.md): agents, console and campaigns.
- [docs/plant-model.md](docs/plant-model.md): equations, control, SIFs and faults.
- [docs/admit.md](docs/admit.md): ADMIT integration.
- [docs/deployment.md](docs/deployment.md): hosting.
- [CHANGELOG.md](CHANGELOG.md).

## Development

```bash
pip install -e ".[dev,admit]"
pytest                            # ~3 min
cd frontend && npm run dev        # console with hot reload on http://localhost:5173 (proxy to :8001)
```

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). OperAItor is a project of AISL, Imperial College London.
