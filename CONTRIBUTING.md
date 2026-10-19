# Contributing to OperAItor

OperAItor is in **beta**. The most useful contributions right now are reports of anything that looks wrong.
The quickest way is **Feedback** in the console's header (the desktop app, Docker, pip and the website all have
it): it opens a GitHub issue filled in with the version, how OperAItor runs, the pipeline and the plant state
(never an API key). No GitHub account? Copy or download the report there instead.

- **Physics or process behaviour** ([report](https://github.com/AISL-at-Imperial-College-London/operAItor/issues/new?template=physics.yml)):
  a balance that does not close, a response a real process would not show, a trip or a limit in the wrong place.
  First check the [model scope](docs/plant-model.md#model-scope-and-known-simplifications-beta), which lists the
  simplifications we know about.
- **Bugs in the app or the workflow** ([report](https://github.com/AISL-at-Imperial-College-London/operAItor/issues/new?template=bug.yml)):
  installing, the console, the agents, custom pipelines, the benchmark.
- **Your pipeline's results.** If you benchmark a pipeline of your own, open an issue with the exported pipeline
  file (`.operaitor-pipeline.json`) and the benchmark report. Comparisons on the same faults are what make the
  benchmark useful.
- **A plant model of your own** ([propose it](https://github.com/AISL-at-Imperial-College-London/operAItor/issues/new?template=plant.yml)):
  another process for the agents to be benchmarked on. Plants are integrated together with their authors; see
  [Contribute a plant model](README.md#contribute-a-plant-model).

## Fixing an issue

Issues labelled [`help wanted`](https://github.com/AISL-at-Imperial-College-London/operAItor/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22)
are ready for someone to pick up; reporters can tick "I would like to work on a fix". Comment on the issue to
say you're on it, then open a pull request to `dev` that links it (`Fixes #123`).

## Changing the code

```bash
pip install -e ".[dev]" "admit-bench @ git+https://github.com/Refiant-Inc/admit-bench.git"
pytest                                                    # Python tests, including the physics audit
cd frontend && npm ci && npm run build && npx playwright install chromium && npm run test:e2e   # console tests
```

- Pull requests go to `dev`; `main` holds the releases. CI runs the Python tests (3.11–3.13), the console build
  and browser tests, the Docker image, and, when the packaging changes, the desktop apps on Windows, macOS and
  Linux.
- **Changes to the plant model change the benchmark.** Physics fixes are welcome, but they are released in a new
  version together with re-run reference results, so a version's published numbers always come from its own model.
- The five baseline pipelines are locked (`tests/test_pipeline.py`): a change to their settings or prompts is a
  change to the benchmark and needs the same care.
