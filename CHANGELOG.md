# Changelog

## 1.1.0 (unreleased)

**Beta release**
- OperAItor is published as a beta: the project page and README present it as a benchmark to run with your own
  models, prompts and agents. The original study's runs are kept as reference runs (labelled with their model,
  one run each and the earlier plant version), not as headline results; the console's comparison uses the same
  label.
- Physics audit in CI (`tests/test_physics_audit.py`): conservation through transients, time-step convergence,
  thermodynamics against the real compounds, and the direction of fault responses on the closed-loop plant.
- Model scope in `docs/plant-model.md`: what is checked, and the known simplifications (irreversible reaction
  without chemical equilibrium; the condenser vapour has no composition state). The plant model itself is
  unchanged, so the reference runs stay reproducible.
- Issue forms for physics and app bugs, and `CONTRIBUTING.md`.
- P&ID and cause & effect (`docs/pid.md`, `docs/pid.svg`): equipment, control loops, alarms and the SIF cause &
  effect matrix, generated from the simulator (`python -m operaitor.plant.cause_effect --write`) and checked by
  `tests/test_cause_effect.py`.
- Plant tab: **Process graphic | P&ID** switch. The P&ID view shows each controller's live value, lights a latched
  safety function on the drawing, and shows the cause & effect matrix with the latched rows highlighted.
- P&ID revised after review and now editable: `docs/pid/pid.drawio` (draw.io / diagrams.net) and the console's SVG
  are generated from one description (`docs/pid/build_pid.py`). PCV-101 is a control valve driven by PIC-101;
  SIS valves are solenoid valves (black normally closed: BDV-101, XV-102; white normally open: the XV shutdown
  valves); each SIF has its own switch (TSHH, PSHH, LSHH, LSLL …) and interlock diamond I1–I11, numbered as in the
  cause & effect matrix, and each shutdown valve lists the interlocks acting on it.
- **Feedback** in the console's header: report a bug (app or physics) or give feedback. It opens a GitHub issue
  filled in with the version, how OperAItor runs (desktop app, Docker, pip, website), the pipeline, the model
  (never the key) and the plant state; or copy / download the report. Issue forms gain a diagnostics field, a
  "help fix it" box, and a feedback form; `CONTRIBUTING.md` explains picking up `help wanted` issues.
- Clean-up: the console offers only what it can use. New episode lists normal, with disturbances and surprise
  (the single-fault episodes stay loadable by scripts); live fault campaigns are gone (the pipeline benchmark and
  the human test replace them; the fault library is `server/fault_library.py`); the Plant tab's alarm card links
  to the Alarms tab instead of repeating its button; the ADMIT card shows only when ADMIT checks the actions.
- The server no longer accepts API keys at all (`set_api_key` and the server-side key path are gone; the browser
  always makes the model calls), and 13 unused functions are removed.
- Fix: with the agents running on this computer's Ollama (picked by the server when it has no API key), the
  console offered no way to connect Groq, Gemini, OpenRouter or OpenAI. **Use another provider** on the connected
  card opens the provider form.
- Customising is easy to find: **Customise prompts, knowledge graph & agents** in Agent settings opens the
  *Customise the agents* section (baselines, your pipelines, the benchmark), now right below the settings.

**Run it on your own computer**
- Windows app: per-user installer (`OperAItor-Setup-<version>.exe`, no administrator rights) and a portable zip.
  It starts the console on this computer and opens it in the browser.
- Docker image on the GitHub Container Registry: `ghcr.io/aisl-at-imperial-college-london/operaitor`.
- pip wheel with the web console bundled (`pip install "operaitor[llm]"`, then `operaitor serve`).
- One release workflow builds, smoke-tests and publishes all three on a version tag; `vX.Y.Z-rc.N` tags make
  pre-releases, whose files carry the rc number (`OperAItor-Setup-1.1.0-rc.3.exe`).
- Desktop apps for macOS (Apple silicon, `.dmg`) and Linux (x86-64, `.tar.gz`) next to Windows, from one
  launcher and build spec (`packaging/desktop/`). Each is built, started and checked on its own system in CI
  (`.github/workflows/desktop.yml`) on every pull request that changes the packaging, and for every release. A
  second start opens the running app; **Quit OperAItor** in the console stops it. The project page offers the
  download for the visitor's system.
- Security: a server on your own computer only accepts WebSocket connections from its own console page (and the
  Vite dev server), so another website you open cannot drive the plant or run Python agents.
- ADMIT is bundled in the desktop apps and the Docker image. It is no longer a pip extra, because PyPI rejects
  git dependencies.

**Custom agent pipelines** ([docs/pipelines.md](docs/pipelines.md))
- Agents tab → *Agent pipeline*: run a baseline, or customise one: validator (digital twin, ADMIT with or without
  simulation, LLM judge, none), planner, diagnosis, knowledge-graph use, retries, twin acceptance and look-ahead,
  fallback and decision trigger; append to or replace the diagnosis, planning, action and judge prompts; upload
  your own knowledge graph (JSON-LD, checked on upload with a coverage report).
- Pipelines are data only, kept in the visitor's browser, exportable and importable; the server runs the active
  one for that session only. Each has a fingerprint shown with it.
- The five baselines are locked: `tests/test_pipeline.py` fails if their settings or prompts change.
- Python agents (own computer only): a pipeline's Python code can add fault detectors (`DETECTORS`, run next to
  PCA, an alarm triggers the agents) and a validator (`VALIDATOR`, which can simulate each proposed action on a
  copy of the plant). A public server never runs code; a server on the network only with
  `PLANT_ALLOW_PLUGINS=1`.
- The live pipeline diagram shows the validator that actually runs (a custom pipeline may change it).
- Steps diagram in the pipeline editor: click a step to switch it (diagnosis, knowledge graph, planning,
  validator, retries, fallback).
- Your own wiring (own computer only): `BUILD_GRAPH(graph, nodes, routes)` reorders or skips the decision steps
  and adds steps of your own, which show in the live pipeline diagram.
- Benchmark the running pipeline from the console: quick (3), all 20 or one benchmark scenario, replayed with the
  benchmark's schedule and score and compared with the published runs (no agents, the baseline with every
  model). The published results ship with the package (`operaitor/data/benchmark_reference.json`), so the human
  test now compares against them in the Windows app and Docker too.

**Console**
- New logo, process-generic: a control valve as drawn on a process diagram (`branding/`). It is used on the
  website, in the console and as the Windows app and installer icon.
- Human operator mode, with a scored, optionally blind human test on the 20 benchmark scenarios.
- Clean-up: one place per control, Agent settings card, clearer Operate panel, lamps only in alarm.
- Visitors' LLM calls are made by their own browser: a public server never receives an API key.

**Model**
- Reboiler steam is a 0–100 % valve (60 % at design duty).
- Fixes from a logical-consistency review:
  - reboiler high-level trip keeps boil-up;
  - temperature trip reset band;
  - condenser energy constant;
  - safe envelope;
  - setpoint ranges inside the alarm limits.
- Knowledge graph regenerated from its builder (`pip install -e ".[kg]"`).

**Hosting and quality**
- Self-hosting kit for an always-on Windows machine with Tailscale Funnel (`deploy/windows/`).
- Codecov coverage; tests for the web server, CLI, providers, PCA, campaigns and benchmark scoring.

## 1.0.0 (2026-10-09)

First public release of **OperAItor**, a package and hosted web app, under the Apache License 2.0.

**Product**
- `operaitor` package with the `operaitor` CLI: `serve`, `doctor`, `benchmark`,
  `dashboard`, `loop-tests`, `train-pca`, `admit`.
- Installation through `pyproject.toml`, with optional extras `[llm]`, `[ollama]`, `[admit]`,
  `[bench]` and `[dev]`.
- Hosted mode:
  - one plant per visitor (session cap, idle timeout, reattach on reload);
  - visitors supply their own OpenAI key, held in memory for their session only;
  - LLM usage metered per session;
  - visitors choose the agent configuration in the console.
- Docker image, a CI workflow (tests on Python 3.11–3.13, console build, image smoke test), a
  Hugging Face Space deploy workflow and a Render blueprint.

**ADMIT**
- `llm_admit` agent configuration:
  - the action agent files an ADMIT action record with every command;
  - ADMIT's hard gates T0–T4 decide admissibility, with T4 projected on a clone of the plant;
  - violation codes and remedies feed the reprompter.
- The plant as an ADMIT cartridge (`operaitor/admit/cartridge`): action grammar, authority,
  SOPs, one hazard per SIF, cause → effect from the knowledge graph, and one case per benchmark
  scenario with an oracle found by projection.

**Plant**
- Column dry-out protection:
  - measurement `column_min_tray_holdup_kmol`;
  - alarm `COL_TRAY_L` (1.0 kmol);
  - plant-trip SIF `COL_L_LL` (0.30 kmol).
- The five dry-out scenarios of the benchmark now end in a SIF trip instead of the model limit.

**Console**
- Single speed control, PLANT TRIP and latched-SIF indicators, removal of duplicated panels.
- Key and agent-configuration card in the Assistant panel; session notices.

**Removed**
- The v0.5 Bokeh console, the jpc_* prototypes, the old run scripts and requirements files, and
  superseded result folders. They remain in git history.
