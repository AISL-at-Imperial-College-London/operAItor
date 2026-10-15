# Changelog

## 1.1.0 (unreleased)

**Run it on your own computer**
- Windows app: per-user installer (`OperAItor-Setup-<version>.exe`, no administrator rights) and a portable zip.
  It starts the console on this computer and opens it in the browser.
- Docker image on the GitHub Container Registry: `ghcr.io/aisl-at-imperial-college-london/operaitor`.
- pip wheel with the web console bundled (`pip install "operaitor[llm]"`, then `operaitor serve`).
- One release workflow builds, smoke-tests and publishes all three on a version tag; `vX.Y.Z-rc.N` tags make
  pre-releases.
- ADMIT is bundled in the Windows app and the Docker image. It is no longer a pip extra, because PyPI rejects
  git dependencies.

**Console**
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
