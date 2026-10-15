# Integrated CSTR + Distillation Knowledge Graph (GraphDB)

Generated from the uploaded v0.5.3 simulator and patterned after `CSTR_Knowledge_Graph.jsonld`.

## Files
- `Integrated_CSTR_Distillation_Knowledge_Graph.jsonld` — JSON-LD with a named graph.
- `Integrated_CSTR_Distillation_Knowledge_Graph.trig` — equivalent TriG serialization; often the clearest GraphDB import format for a named graph.
- `Integrated_CSTR_Distillation_GraphDB_Queries.sparql` — process, control, safety, fault, operator-authority, alarm and state-machine queries.

## Named graph
`<http://www.hsu-ifa.de/graphs/Integrated_CSTR_Distillation_Simulation>`

## Base namespace
`<http://example.org/integrated_cstr_distillation#>`

## GraphDB import
Create/select a repository, open **Import → RDF**, upload either the `.jsonld` or `.trig` file, and import. The graph context is already encoded in the file; you do not need to assign another context.

## Scope encoded
The graph includes the CSTR and H2 protection, 10 column trays, condenser/reflux drum/reboiler, five A/B duty-standby pump trains, 34+ observable process tags, manipulated/exogenous variables, eight PI loops, 22 alarms, 11 SIF causes and their override rules, operator/agent actions and prohibited master variables, curated/surprise fault modes, disturbances, mathematical-model nodes, and the episode outcome state machine.

## Important modelling choice
The simulator explicitly separates controller request → supervisory request → safety-commanded request → actual plant input after actuator/pump faults. The KG preserves this as four `ip:CommandStage` nodes. This is important because an SIF can command a safe position while a failed final element still does not move.
