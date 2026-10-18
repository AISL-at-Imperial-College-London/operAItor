"""Translation between knowledge-graph nodes and simulator variables / command targets.

The KG names things by node id (``controller_R_PIC``, ``act_param_cstr_vent_valve``,
``obs_param_cstr_pressure_bar``, ``pump_train_REFLUX``, ``PCV-101``, ``alarm_R_P_H`` ...);
the simulator and the operator action catalog use their own names (loop ``R_PIC``,
measurement ``cstr_pressure_bar``, input ``cstr_vent_valve``, pump train ``REFLUX``,
auxiliary ``H2_BDV``). This module builds the mapping from both sides, so it stays in
sync when either changes, and is used to

  * give the KG evidence sent to the agents the simulator name and the exact
    command target (``node["sim"]``, via ``lookup``),
  * resolve whatever name an agent writes in a command to the catalog target
    (``VariableMap.command_target``),
  * show simulator names in chat answers.

Export the full table and a coverage report (what has no counterpart):

    py -3 -m operaitor.agents.kg_map            # writes operaitor/data/knowledge_graph/kg_sim_mapping.json
"""
from __future__ import annotations

from dataclasses import fields
import json
from pathlib import Path
from typing import Any

from operaitor.plant.alarms import default_alarm_definitions
from operaitor.plant.control import RegulatoryControlSystem
from operaitor.plant.episode import EpisodeRunner
from operaitor.plant.equipment import PUMP_TRAINS
from operaitor.plant.plant import PlantInputs
from operaitor.plant.safety import SafetySystem

# Operator auxiliaries → the PlantInputs field they drive (see EpisodeRunner._apply_operator_state).
AUX_FIELDS = {"H2_BDV": "cstr_bdv_valve", "EMERGENCY_COOLING": "cstr_emergency_cooling_factor"}
# Equipment tags of those auxiliaries (the KG has no controller link for them).
AUX_EQUIPMENT = {"BDV-101": "H2_BDV", "E-102": "EMERGENCY_COOLING"}
# Major equipment → the loops that act on it (and notes for passive devices).
EQUIPMENT = {"R-101": {"loops": ["R_LIC", "R_TIC", "R_PIC"]}, "V-201": {"loops": ["DR_LIC"]},
             "E-201": {"loops": ["COL_PIC"]}, "E-202": {"loops": ["BOT_TIC", "RB_LIC"]},
             "C-201": {"loops": ["COL_PIC", "TOP_TIC", "BOT_TIC"]},
             "PSV-101": {"measurements": ["cstr_pressure_bar", "cstr_h2_vent_kmol_h"],
                         "note": "passive relief valve; not commandable"}}
THROUGHPUT_FIELD = "fresh_ipa_feed"

COMMAND_KINDS = {"SET_SETPOINT": "loop", "START_SPARE": "pump_train", "SELECT_PRIMARY": "pump_train",
                 "OPERATE_AUXILIARY": "aux", "SET_MODE": "loop", "SET_MANUAL_OUTPUT": "loop"}


class VariableMap:
    def __init__(self, kg, measurement_tags: list[str] | None = None):
        self.kg = kg
        self.loops = {k: {"pv": v.pv_tag, "mv": v.mv_field, "sp_range": [v.sp_min, v.sp_max], "unit": v.sp_unit}
                      for k, v in RegulatoryControlSystem.LOOP_SPECS.items()}
        self.trains = {k: {"primary": v.primary_id, "standby": v.spare_id, "flow": v.flow_field}
                       for k, v in PUMP_TRAINS.items()}
        self.aux = {k: {"input": AUX_FIELDS.get(k)} for k in EpisodeRunner.AUXILIARY_BOUNDS}
        self.alarms = {d.alarm_id: d for d in default_alarm_definitions()}
        self.sifs = set(SafetySystem.CAUSES)
        self.inputs = {f.name for f in fields(PlantInputs)}
        obs_tags = [n.removeprefix("obs_param_") for n in kg.nodes if n.startswith("obs_param_")]
        self.measurements = set(measurement_tags) if measurement_tags else set(obs_tags)
        self.table: dict[str, dict] = {}
        self._build()

    # ------------------------------------------------------------ construction
    def _loop_entry(self, loop: str) -> dict:
        info = self.loops[loop]
        return {"kind": "loop", "sim": loop, "pv": info["pv"], "mv": info["mv"],
                "setpoint_range": info["sp_range"], "unit": info["unit"],
                "command": {"type": "SET_SETPOINT", "target": loop}}

    def _input_entry(self, field: str) -> dict:
        e: dict[str, Any] = {"kind": "input", "sim": field}
        loop = next((k for k, v in self.loops.items() if v["mv"] == field), None)
        aux = next((k for k, v in AUX_FIELDS.items() if v == field), None)
        train = next((k for k, v in self.trains.items() if v["flow"] == field), None)
        if loop:
            e.update(manipulated_by=loop, command={"type": "SET_SETPOINT", "target": loop})
        if aux:
            e.update(auxiliary=aux, command={"type": "OPERATE_AUXILIARY", "target": aux})
        if train:
            e["pump_train"] = train
        if field == THROUGHPUT_FIELD:
            e["command"] = {"type": "SET_THROUGHPUT"}
        return e

    def _measurement_entry(self, tag: str) -> dict:
        loop = next((k for k, v in self.loops.items() if v["pv"] == tag), None)
        e: dict[str, Any] = {"kind": "measurement", "sim": tag,
                             "alarms": {a: f"{'>' if d.direction == 'HIGH' else '<'} {d.limit:g} {d.unit}".strip()
                                        for a, d in self.alarms.items() if d.tag == tag}}
        if loop:
            e.update(controlled_by=loop, command={"type": "SET_SETPOINT", "target": loop})
        return e

    def _build(self) -> None:
        t = self.table
        for nid in self.kg.nodes:
            if nid.startswith(("controller_", "PO_")):
                loop = nid.split("_", 1)[1]
                if loop in self.loops:
                    t[nid] = self._loop_entry(loop)
            elif nid.startswith("pump_train_"):
                train = nid.removeprefix("pump_train_")
                if train in self.trains:
                    t[nid] = {"kind": "pump_train", "sim": train, **self.trains[train],
                              "command": {"type": "START_SPARE", "target": train}}
            elif nid.startswith("act_param_") and nid.removeprefix("act_param_") in self.inputs:
                t[nid] = self._input_entry(nid.removeprefix("act_param_"))
            elif nid.startswith("obs_param_pump_") and nid.endswith("_failed"):
                train = nid.removeprefix("obs_param_pump_").removesuffix("_failed")
                if train in self.trains:
                    t[nid] = {"kind": "pump_status", "sim": f"pump_{train}_failed", "pump_train": train,
                              "command": {"type": "START_SPARE", "target": train}}
            elif nid.startswith("obs_param_") and nid.removeprefix("obs_param_") in self.measurements:
                t[nid] = self._measurement_entry(nid.removeprefix("obs_param_"))
            elif nid.startswith("alarm_") and nid.removeprefix("alarm_") in self.alarms:
                d = self.alarms[nid.removeprefix("alarm_")]
                t[nid] = {"kind": "alarm", "sim": d.alarm_id, "tag": d.tag, "limit": d.limit,
                          "direction": d.direction, "priority": d.priority}
            elif nid.startswith("SIF_") and nid.removeprefix("SIF_") in self.sifs:
                t[nid] = {"kind": "sif", "sim": nid.removeprefix("SIF_")}
            elif nid.startswith("fault_"):
                t[nid] = {"kind": "fault", "sim": nid.removeprefix("fault_")}
            elif nid.startswith("action_"):
                t[nid] = {"kind": "operator_action", "sim": nid.removeprefix("action_")}
            elif nid.startswith("exo_param_") and nid.removeprefix("exo_param_") in self.inputs:
                t[nid] = {"kind": "exogenous", "sim": nid.removeprefix("exo_param_"), "commandable": False,
                          "note": "disturbance / fault variable, not an operator lever"}
        # Equipment tags (P-201A, PT-101, PCV-101, CV-201, BDV-101 ...) via their KG links.
        pump_ids = {v["primary"]: (k, "primary") for k, v in self.trains.items()}
        pump_ids.update({v["standby"]: (k, "standby") for k, v in self.trains.items()})
        for nid in self.kg.nodes:
            if nid in pump_ids:
                train, role = pump_ids[nid]
                t[nid] = {"kind": "pump", "sim": nid, "pump_train": train, "role": role,
                          "command": {"type": "START_SPARE", "target": train}}
            elif nid in EQUIPMENT:
                eq = EQUIPMENT[nid]
                t[nid] = {"kind": "equipment", "sim": nid, **eq}
            elif nid.startswith("C-201-TRAY-"):
                n = int(nid.rsplit("-", 1)[1])
                tags = [f"tray_{n:02d}_temperature_K", f"tray_{n:02d}_pressure_bar"]
                t[nid] = {"kind": "tray", "sim": nid, "measurements": [x for x in tags if x in self.measurements]}
            elif nid in AUX_EQUIPMENT:
                aux = AUX_EQUIPMENT[nid]
                t[nid] = {"kind": "aux_equipment", "sim": aux, "input": AUX_FIELDS[aux],
                          "command": {"type": "OPERATE_AUXILIARY", "target": aux}}
            elif "-" in nid and nid not in t:
                types = self.kg.types(nid)
                ctrl = [o for p, o in self.kg.out.get(nid, []) + self.kg.inc.get(nid, [])
                        if p == "isConnectedWith" and o.startswith("controller_")]
                if "Sensor" in types:
                    tags = [s.removeprefix("obs_param_") for p, s in self.kg.inc.get(nid, [])
                            if p == "isObservedBy" and s.startswith("obs_param_")]
                    if tags:
                        t[nid] = {"kind": "sensor", "sim": tags[0], "measures": tags,
                                  **({"command": {"type": "SET_SETPOINT", "target": ctrl[0][11:]}} if ctrl else {})}
                elif "Actuator" in types and ctrl:
                    loop = ctrl[0].removeprefix("controller_")
                    if loop in self.loops:
                        t[nid] = {"kind": "actuator", "sim": self.loops[loop]["mv"], "manipulated_by": loop,
                                  "command": {"type": "SET_SETPOINT", "target": loop}}

    # ------------------------------------------------------------ lookups
    def lookup(self, name: str) -> dict | None:
        return self.table.get(name)

    def command_target(self, command_type: str, name: Any) -> Any:
        """Resolve any KG id / equipment tag / simulator name to the catalog target for a command."""
        if not isinstance(name, str):
            return name
        raw = name.strip()
        want = COMMAND_KINDS.get(command_type)
        if want == "loop" and raw.upper() in self.loops:
            return raw.upper()
        if want == "pump_train" and raw.upper() in self.trains:
            return raw.upper()
        if want == "aux" and raw.upper() in self.aux:
            return raw.upper()
        candidates = [raw, f"controller_{raw}", f"pump_train_{raw}", f"act_param_{raw}", f"obs_param_{raw}"]
        for c in candidates:
            e = self.table.get(c) or self.table.get(c.upper()) if c else None
            cmd = (e or {}).get("command") or {}
            if cmd.get("type") == command_type and "target" in cmd:
                return cmd["target"]
            if e and want == "loop" and e.get("controlled_by"):
                return e["controlled_by"]
            if e and want == "loop" and e.get("manipulated_by"):
                return e["manipulated_by"]
            if e and want == "aux" and e.get("auxiliary"):
                return e["auxiliary"]
            if e and want == "pump_train" and e.get("pump_train"):
                return e["pump_train"]
        return raw

    def command_targets(self) -> dict:
        """Compact cheat-sheet for prompts: the exact names commands accept."""
        return {"SET_SETPOINT (loop: pv / mv)": {k: f"{v['pv']} / {v['mv']}" for k, v in self.loops.items()},
                "START_SPARE (pump train: duty / standby)": {k: f"{v['primary']} / {v['standby']}"
                                                            for k, v in self.trains.items()},
                "OPERATE_AUXILIARY (device: input)": {k: v["input"] for k, v in self.aux.items()},
                "SET_THROUGHPUT": THROUGHPUT_FIELD}

    def coverage(self) -> dict:
        kg_tags = {e["sim"] for e in self.table.values() if e["kind"] == "measurement"}
        kg_inputs = {e["sim"] for e in self.table.values() if e["kind"] in ("input", "exogenous")}
        kg_loops = {e["sim"] for e in self.table.values() if e["kind"] == "loop"}
        unmapped = sorted(n for n in self.kg.nodes if self.kg.useful(n) and n not in self.table
                          and not n.startswith(("sym_", "override_", "module_", "Equation_", "feat_", "ref_")))
        return {"mapped_kg_nodes": len(self.table),
                "simulator_without_kg_node": {
                    "measurements": sorted(self.measurements - kg_tags),
                    "inputs": sorted(self.inputs - kg_inputs),
                    "loops": sorted(set(self.loops) - kg_loops),
                    "alarms": sorted(set(self.alarms) - {e["sim"] for e in self.table.values() if e["kind"] == "alarm"}),
                    "sifs": sorted(self.sifs - {e["sim"] for e in self.table.values() if e["kind"] == "sif"})},
                "kg_nodes_without_simulator_counterpart": unmapped}


_DEFAULT: VariableMap | None = None


def default_map() -> VariableMap:
    """Shared map built from the repo's KG (cached; ~0.2 s on first use)."""
    global _DEFAULT
    if _DEFAULT is None:
        from .kg import KnowledgeGraph
        _DEFAULT = VariableMap(KnowledgeGraph())
    return _DEFAULT


def main() -> None:
    from operaitor.plant import IntegratedPlantDAE
    from .kg import KnowledgeGraph
    plant = IntegratedPlantDAE()
    tags = [k for k in plant.measurements(plant.nominal_state(plant.default_inputs())) if not k.startswith("pump_")]
    vm = VariableMap(KnowledgeGraph(), measurement_tags=tags)
    out = Path(__file__).resolve().parent.parent / "data" / "knowledge_graph" / "kg_sim_mapping.json"
    out.write_text(json.dumps({"command_targets": vm.command_targets(), "coverage": vm.coverage(),
                               "nodes": vm.table}, indent=1, default=str), encoding="utf-8")
    cov = vm.coverage()
    print(f"wrote {out} ({cov['mapped_kg_nodes']} KG nodes mapped)")
    for k, v in cov["simulator_without_kg_node"].items():
        print(f"  simulator {k} with no KG node: {v or 'none'}")
    print(f"  KG nodes with no simulator counterpart: {cov['kg_nodes_without_simulator_counterpart'] or 'none'}")


if __name__ == "__main__":
    main()
