# Custom agent pipelines

The agents decide in a fixed sequence: monitoring → knowledge graph → planning → action → validation →
dispatch ([architecture](architecture.md)). A **pipeline** is how that sequence is set up: which validator
checks each action, the planner, the knowledge graph, each LLM agent's prompt and the decision settings.

OperAItor ships five **baseline** pipelines: the configurations behind the published results. They never
change, so anything you build can be compared with them. Your own pipeline starts from a baseline and
changes parts of it.

## In the console

**Agents tab → Agent pipeline.**

1. **Customise** a baseline.
2. Change what you want:
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

Scenario campaigns started from the console run with whichever pipeline is running, so a custom pipeline can be
compared with a baseline on the same faults.

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

## Not in this version

- Your own Python agents (monitoring detectors, validators) on your own computer.
- Changing the sequence of steps itself, and running the 20-scenario benchmark from the console with a
  side-by-side comparison against the baselines.

These follow in later versions. The baselines stay as they are: a test (`tests/test_pipeline.py`) fails if
their settings or prompts change.
