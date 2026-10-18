# Custom agent pipelines

The agents decide in a fixed sequence: monitoring → knowledge graph → planning → action → validation →
dispatch ([architecture](architecture.md)). A **pipeline** is how that sequence is set up: which validator
checks each action, the planner, the knowledge graph, each LLM agent's prompt and the decision settings.

OperAItor ships five **baseline** pipelines: the configurations behind the published results. They never
change, so anything you build can be compared with them. Your own pipeline starts from a baseline and
changes parts of it.

## In the console

**Agents tab → Agent settings → Customise prompts, knowledge graph & agents** (it opens the *Customise the
agents* section).

1. **Customise** a baseline.
2. Change what you want. **Steps** shows the decision as a chain: click a step to switch it (LLM diagnosis
   or rules only, knowledge graph on or off, LLM or rule-based planning), pick what checks each action, and
   set the retries and what happens when every retry fails. Below it:
   - **Settings**: what checks each action (digital twin, ADMIT gates with or without simulation, LLM judge, or
     nothing), the planner (knowledge-graph rules or the LLM planning agent), LLM or rule-based diagnosis, use of the
     knowledge graph, retries after a rejected proposal, the digital twin's acceptance criterion and look-ahead,
     the fallback when every retry fails, and when the agents decide. Changed values are highlighted; everything
     else follows the baseline.
   - **Prompts** of the diagnosis, planning, action and judge agents. *Add to the baseline prompt* appends your
     instructions; *Replace it* swaps the whole prompt. With ADMIT validation the action-record instructions are
     always added to the action agent's prompt, because ADMIT checks those records.
   - **Knowledge graph**: upload your own JSON-LD (see below). It is checked straight away and the report says
     what the agents will find in it.
3. **Save and run.** The pipeline runs in your session only, from that moment. Its fingerprint (a short hash of
   everything that changes behaviour) is shown next to it.

Your pipelines are kept in this browser, like your LLM key; the server keeps the running one in memory for
your session only. **Export** saves a pipeline as a `.operaitor-pipeline.json` file, to share or keep;
**Import** loads one. Pick a baseline in **Agent settings → Pipeline** (or **Run** it) to go back.

The pipeline benchmark (below) runs whichever pipeline is running, so a custom pipeline is compared with the
baselines on the same faults.

## Knowledge graph format

Use the format of the baseline graph,
[`Integrated_CSTR_Distillation_Knowledge_Graph.jsonld`](../operaitor/data/knowledge_graph/Integrated_CSTR_Distillation_Knowledge_Graph.jsonld):
a JSON-LD list of named graphs, each with an `@graph` list of nodes. The agents find nodes by their id (the last
part of the IRI), so keep the baseline's id families; they are what links the graph to the plant:

| Id family | Example | Used for |
|---|---|---|
| `fault_<name>` | `fault_reflux_pump_trip` | where retrieval starts for a diagnosed fault; the diagnosis agent's fault catalogue (`FaultMode` type) |
| `alarm_<id>`, `controller_<loop>`, `pump_train_<train>`, `obs_param_<tag>` | `alarm_R_P_H`, `controller_R_PIC` | where retrieval starts from alarms, saturated loops, failed pumps and PCA contributors |
| `action_<COMMAND>` linked by `actsOn` | `action_START_SPARE` | candidate levers for the planner |
| `sym_`, `SIF_`, `act_param_` | `SIF_R_T_HH` | context in the evidence the agents read |

The report after upload counts these families, how many nodes map to plant variables or commands, and how many
of the baseline's fault modes are covered, with a warning for anything the agents will miss. Limits: 5 MB per
graph and 20,000 characters per prompt.

## Python agents (on your own computer)

A pipeline can carry Python code, written in the editor's **Python agents** box (**Start from the example**,
then **Check the code**). The code is a module that defines either or both of:

```python
from operaitor.plugins import Proposal

class MyDetector:
    name = "my_detector"
    async def detect(self, observation: dict) -> dict:     # every observation (every 0.5 min)
        p = observation["measurements"]["cstr_pressure_bar"]
        return {"alarm": p > 3.4, "detail": f"{p:.2f} bar"}  # an alarm triggers the agents, like PCA

class MyValidator:
    name = "my_validator"
    async def validate(self, proposal: Proposal) -> dict:   # each proposed action
        result = await proposal.simulate(minutes=20)        # the action on a copy of the plant
        ok = result["terminal_status"] == "RUNNING" and result["restored"]
        return {"accept": ok, "reason": "...", "checks": proposal.standard_checks(result)}

DETECTORS = [MyDetector()]
VALIDATOR = MyValidator()   # runs when "Check each action with" is "Your Python validator"
```

- **Detectors** get the same observation as the agents: `time_min`, `measurements` (tag → value),
  `controllers`, `pump_trains`, `alarm_states`, `sif_latched`. They return `{"alarm": bool, "detail": str}` or a
  bool. Their results appear under *Fault detection → Your detectors*.
- **The validator** gets a `Proposal`: `commands`, `reasoning`, `attempt`, `observation`, `detection`,
  `diagnosis`, `brief`, `knowledge`, `limits` (alarm and trip limits), `await simulate(minutes)` (the digital
  twin's result: `terminal_status`, `restored`, `time_to_safe_min`, `unsafe_fraction`, `final`, ...) and
  `standard_checks(result)` (the digital-twin validator's checks). A rejection's reason goes back to the action
  agent, which proposes again.
- Errors never reach the plant: a detector that fails or takes more than 5 s reports the error and raises no
  alarm; a validator that fails or returns something else rejects the action, with the reason.

Python code runs inside the OperAItor server with your permissions, so it only runs when OperAItor runs **on
your own computer** (the desktop app, `operaitor serve`, or Docker with `PLANT_HOSTED=0`):

| Server | Python agents |
|---|---|
| Public (`PLANT_HOSTED=1`, e.g. the website's live demo) | never |
| Your computer, listening on `127.0.0.1` (default) | yes |
| Listening on the network (`--host 0.0.0.0`, Docker) | only with `PLANT_ALLOW_PLUGINS=1` |
| `PLANT_ALLOW_PLUGINS=0` | never |

On a public server you can still write a pipeline with Python agents, save and export it, and run it in
OperAItor on your computer.

### Your own wiring: `BUILD_GRAPH`

The step diagram chooses each standard step's variant; to change the **order** of the steps, skip some or add
your own, define `BUILD_GRAPH` (**Add the wiring example** in the editor starts from the reference wiring):

```python
from operaitor.plugins import END, START

async def no_throughput_changes(state: dict) -> dict:   # a step of your own
    if any(c.get("type") == "SET_THROUGHPUT" for c in state["plan"].get("commands", [])):
        return {"errors": ["this pipeline does not change the throughput"]}   # sent back to the action agent
    return {}

def BUILD_GRAPH(graph, nodes, routes):
    for name, step in nodes.items():
        graph.add_node(name, step)
    graph.add_node("no_throughput_changes", no_throughput_changes)
    graph.add_edge(START, "monitoring")
    graph.add_conditional_edges("monitoring", routes["after_monitoring"], {"act": "kg_retrieval", "end": END})
    ...   # the rest of the reference wiring, with action_propose → no_throughput_changes → twin_evaluate
```

- `nodes`: the standard steps `monitoring`, `kg_retrieval`, `planning`, `action_propose`, `twin_evaluate`
  (whichever validator the pipeline uses), `validate`, `reprompt`, `dispatch`, `reject`.
- `routes`: the standard decisions `after_monitoring` (`act` / `end`), `after_validate` (`dispatch` /
  `reprompt` / `reject`) and `after_reject` (`dispatch` / `end`).
- A step of your own is `async def step(state) -> dict` and returns updates of the decision state (for example
  `errors`, which rejects the proposal as not executable, or `plan`). Its progress shows in the live pipeline
  diagram; if it fails, that decision ends and the plant is released.
- **Check the code** builds the wiring once and reports a mistake (an edge to a step that does not exist, ...)
  before it runs.

## Benchmark a pipeline

**Customise the agents → Benchmark the running pipeline** replays benchmark scenarios on your plant: the published
benchmark's faults, onset and lift times, shift operator and score (margin + change in liquid inventory − the
plant-stop penalty over the scenario window). The agents act on their own with whichever pipeline is running,
and the plant runs as fast as it can (it is held while the agents decide).

Choose **Quick** (3 scenarios: a single fault, a coupled pair and a coupled triple), **All 20**, or one
scenario. Each scored scenario is shown next to the published runs of the same scenario: no agents, and the
baseline your pipeline starts from with every model that was benchmarked; the totals row adds them up over the
scenarios you ran. A report is written to the campaign folder (`campaign_results/` by default).

The published results ship with OperAItor (`operaitor/data/benchmark_reference.json`, from
`benchmark_results/` with `python -m operaitor.benchmark.reference`), so the comparison works in the Windows
app and Docker too.

The baselines stay as they are: a test (`tests/test_pipeline.py`) fails if their settings or prompts change.
