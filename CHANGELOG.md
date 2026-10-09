# Changelog

## 1.0.0 (2026-10-09)

First release as a shippable package and hosted web app.

**Product**
- `acetone_plant` package with the `acetone-plant` CLI: `serve`, `doctor`, `benchmark`,
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
- The plant as an ADMIT cartridge (`acetone_plant/admit/cartridge`): action grammar, authority,
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
