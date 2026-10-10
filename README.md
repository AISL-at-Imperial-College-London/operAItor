# OperAItor

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

Open the deployed Space and press **Run**, then inject a fault from the **Faults** panel. To turn
on the agents, paste your own OpenAI API key in the **Assistant** panel. It is kept only in server
memory for your session, and a run costs a few cents with gpt-5-nano. Deploying your own copy for
free is covered in [docs/deployment.md](docs/deployment.md).

### Locally

Requires Python ≥ 3.11 and Node 20+.

```bash
pip install -e ".[llm]"          # add ,admit for the ADMIT gates and cartridge; ,dev for tests
cd frontend && npm ci && npm run build && cd ..
cp .env.example .env              # put OPENAI_API_KEY=... in .env (optional; git-ignored)
operaitor doctor              # checks the installation
operaitor serve               # http://localhost:8001
```

Without a key, the agents monitor and diagnose but take no actions, and the chat answers offline.
Other model names (`--llm-model llama3.1:8b`) use a local Ollama. `PLANT_LLM_BASE_URL` points the
agents at any OpenAI-compatible server.

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

The 20-scenario campaign (gpt-5-nano, with the column dry-out SIF) is in
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
