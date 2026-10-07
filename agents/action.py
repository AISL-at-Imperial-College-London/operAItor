"""Action agents: turn the planning brief into concrete, validatable commands.

Both return the plan format used by ``jpc_recovery_langgraph.compile_plan``:

    {"commands": [{"type": "START_SPARE", "target": "REFLUX"}, ...], "reasoning": "..."}

``RuleActionAgent`` is the offline baseline. ``LLMActionAgent`` calls a chat
model with ``ainvoke`` (so it never blocks the plant loop) and records latency
and token usage in ``last_usage``.
"""
from __future__ import annotations

import json
import time
from typing import Any, Protocol

from .llm import DEFAULT_MODEL, USAGE, make_llm, message_text, parse_json_object


class ActionAgent(Protocol):
    async def propose(self, context: dict) -> dict: ...


class RuleActionAgent:
    """Conservative heuristics; NO_ACTION (operator advisory) when unsure.

    The rules give an ordered list of candidate plans. The first is proposed; when
    validation or the digital twin rejects it and the reprompter asks again, the
    next untried candidate is proposed (e.g. a deeper throughput cut), so the
    reprompt loop also works offline.
    """

    def __init__(self, min_throughput: float = 60.0, nominal_throughput: float = 100.0):
        self.min_throughput = min_throughput
        self.nominal_throughput = nominal_throughput
        self.last_usage: dict = {}
        self._tried: list = []
        self.nominal_sp: dict[str, float] | None = None  # setpoints at the first decision (the design point)

    async def propose(self, context: dict) -> dict:
        if context.get("previous_plan") is None:  # first attempt of a new decision cycle
            self._tried = []
        for plan in self.candidates(context):
            if plan["commands"] not in self._tried:
                if self._tried:
                    reason = json.loads(context.get("feedback") or "{}").get("fail_reason")
                    plan = {**plan, "reasoning": f"{plan['reasoning']} (previous proposal rejected: {reason})"}
                self._tried.append(plan["commands"])
                return plan
        return {"commands": [{"type": "NO_ACTION"}],
                "reasoning": "Every rule candidate was rejected by the digital twin; operator advised."}

    def candidates(self, context: dict) -> list[dict]:
        obs, diag = context["observation"], context["diagnosis"]
        rules = context["detection"]["rules"]
        hyps = [h["fault"] for h in diag["hypotheses"]]
        alarms = set(rules["active_alarms"])
        saturated = set(rules["saturated_loops"])
        feed = obs["throughput_target_kmol_h"]
        out: list[dict] = []
        if self.nominal_sp is None:  # only the agents change setpoints, so these are the design values
            self.nominal_sp = {k: v["setpoint"] for k, v in obs["controllers"].items()}

        def cuts(why: str, factors: tuple[float, ...]) -> None:
            for f in factors:
                target = round(max(0.5 * self.min_throughput, f * feed), 1)
                out.append({"commands": [{"type": "SET_THROUGHPUT", "value": target}],
                            "reasoning": f"{why}: cut throughput to {target:g} kmol/h ({100 * (1 - f):.0f}% lower)."})

        spares = []
        for train in rules["failed_trains"]:
            info = obs["pump_trains"][train]
            if info["active_pump"] != info["spare"] and not info["spare_failed"]:
                spares.append({"type": "START_SPARE", "target": train})
        if len(spares) > 1:  # several pumps down: start every standby at once
            out.append({"commands": spares, "reasoning": f"Duty pumps failed on {[c['target'] for c in spares]}; "
                                                         "start every healthy standby."})
        for c in spares:
            out.append({"commands": [c], "reasoning": f"Duty pump on {c['target']} failed; standby is healthy."})

        # Reflux drum draining (condensation shortfall or a drum leak) and the distillate draw is
        # already shut by DR_LIC: only less reflux keeps liquid in the drum. Raise the top-temperature
        # setpoint (TOP_TIC is reverse acting on reflux). Purity suffers, but a DR_L_LL trip is avoided.
        drum, top = obs["controllers"].get("DR_LIC"), obs["controllers"].get("TOP_TIC")
        draining = drum and ((drum.get("pv") or 99.0) < drum["setpoint"] - 6.0
                             and (drum.get("last_output") if drum.get("last_output") is not None else 99.0) <= 1.0)
        if drum and top and ({"DR_L_LL"} & alarms or draining) and top["setpoint"] < 339.5:
            for dT in (3.0, 6.0):
                sp = round(min(340.0, top["setpoint"] + dT), 1)
                plan = [{"type": "SET_SETPOINT", "target": "TOP_TIC", "value": sp}]
                out.append({"commands": spares + plan if spares else plan,
                            "reasoning": f"Reflux drum draining: raise TOP_TIC to {sp:g} K to cut reflux"
                                         + (" and start the standby pumps" if spares else "") + "."})

        if ({"R_P_H", "R_P_HH"} & alarms or "R_PIC" in saturated) and obs["auxiliaries"]["H2_BDV"] < 0.3:
            for v in (0.3, 0.6):
                out.append({"commands": [{"type": "OPERATE_AUXILIARY", "target": "H2_BDV", "value": v}],
                            "reasoning": f"Reactor pressure high with vent PCV out of range: open H2 blowdown to {v:g}."})

        # Condenser cuts need physical evidence (a column-pressure alarm, or the condenser hypothesis
        # with column pressure clearly above setpoint): after the fault is gone the PCA hypothesis can
        # still say "condenser" and would otherwise keep the plant cut back.
        col_now = obs["controllers"].get("COL_PIC", {})
        col_high = (col_now.get("pv") or 0.0) > col_now.get("setpoint", 1.0) + 0.03
        if (({"COL_P_H", "COL_P_HH"} & alarms or ("condenser_cooling_loss" in hyps[:2] and (col_high or "COL_PIC" in saturated)))
                and feed > self.min_throughput):
            # Condenser limited: boilup must come down to what the condenser can still condense,
            # otherwise column pressure rises (COL_P_HH) or the drum drains (DR_L_LL). Less steam
            # (lower BOT_TIC setpoint), less reflux (higher TOP_TIC setpoint) and less feed together.
            top_sp = obs["controllers"].get("TOP_TIC", {}).get("setpoint")
            for f in (0.5, 0.65):
                cmds = [{"type": "SET_THROUGHPUT", "value": round(f * feed, 1)},
                        {"type": "SET_SETPOINT", "target": "BOT_TIC", "value": 345.0}]
                if top_sp is not None and top_sp < 339.5:
                    cmds.append({"type": "SET_SETPOINT", "target": "TOP_TIC", "value": round(min(340.0, top_sp + 4.0), 1)})
                out.append({"commands": spares + cmds, "reasoning": f"Condenser limited: cut boilup (BOT_TIC 345 K), reflux "
                                                           f"and throughput to {f * feed:g} kmol/h to match the remaining "
                                                           "condenser duty."})
            cuts("Column pressure rising / condenser limited", (0.8, 0.65, 0.5))

        # Load cuts need hard evidence (alarms / saturated loops): a PCA-only hypothesis can be
        # caused by the reduced-load operating point itself and would keep the plant cut back.
        if ({"R_L_H", "R_L_HH"} & alarms or ("cstr_outlet_capacity_loss" in hyps[:1] and "R_LIC" in saturated)) \
                and feed > self.min_throughput:
            cuts("Reactor inventory rising (outlet limited)", (0.7, 0.5, 0.35))

        # Unwinding earlier emergency moves is offered whenever the plant is quiet, first, so the twin
        # can choose it; otherwise the plant stays at an abnormal operating point after the fault clears.
        unwind = self._unwind(obs, alarms, saturated, rules, feed)
        if unwind:
            return unwind + out
        if out:
            return out
        return [{"commands": [{"type": "NO_ACTION"}],
                 "reasoning": f"Fault '{diag['summary']}' detected but no safe automatic response; operator advised."}]

    def _unwind(self, obs: dict, alarms: set, saturated: set, rules: dict, feed: float) -> list[dict]:
        plans: list[dict] = []
        r_pic = obs["controllers"]["R_PIC"]
        if (obs["auxiliaries"]["H2_BDV"] > 0 and not {"R_P_H", "R_P_HH"} & alarms
                and (r_pic["pv"] or 0.0) < r_pic["setpoint"]):
            plans.append({"commands": [{"type": "OPERATE_AUXILIARY", "target": "H2_BDV", "value": 0.0}],
                          "reasoning": "Reactor pressure back below setpoint: close the H2 blowdown again."})
        col = obs["controllers"]["COL_PIC"]
        # Hysteresis: restore load only when the alarms and loops that justified the cut are
        # quiet and column pressure is near setpoint (otherwise cut/restore oscillates).
        quiet = (not {"COL_P_H", "COL_P_HH", "R_L_H", "R_L_HH", "DR_L_LL"} & alarms
                 and not rules["failed_trains"]
                 and "R_LIC" not in saturated  # COL_PIC sits at 100 % while its integral unwinds: judge its PV
                 and (col["pv"] or 0.0) <= col["setpoint"] + 0.03)  # COL_PIC integrates back slowly
        moved = {k: v for k, v in (self.nominal_sp or {}).items()
                 if k in obs["controllers"] and abs(obs["controllers"][k]["setpoint"] - v) > 1e-6}
        if not (quiet and (moved or feed < self.nominal_throughput - 1e-6)):
            return plans
        # Gradual: temperature setpoints move back at most 3 K per decision (a +9 K BOT_TIC jump is a
        # steam surge the column model does not ride through), throughput up in 20 % steps.
        back = []
        for k, v in moved.items():
            cur = obs["controllers"][k]["setpoint"]
            if k.endswith("TIC") and abs(v - cur) > 3.0:
                v = cur + (3.0 if v > cur else -3.0)
            back.append({"type": "SET_SETPOINT", "target": k, "value": round(v, 4)})
        target = round(min(self.nominal_throughput, 1.2 * feed), 1)
        step = [{"type": "SET_THROUGHPUT", "value": target}] if feed < self.nominal_throughput - 1e-6 else []
        if back:
            if step:
                plans.append({"commands": back + step, "reasoning": "Plant back under control: move setpoints "
                              f"back towards nominal and step throughput to {target:g} kmol/h."})
            plans.append({"commands": back, "reasoning": "Plant back under control: move setpoints back towards nominal."})
        if step:
            plans.append({"commands": step, "reasoning": "Plant back under control: step throughput back towards nominal."})
        return plans


ACTION_SYSTEM_PROMPT = """
You are the **Corrective Action Agent** for an integrated isopropanol→acetone plant:
an endothermic liquid-phase CSTR (R-101) with H2 vent/blowdown, feeding a 10-tray
distillation column (T-201) with condenser, reflux drum and reboiler, and five
duty/standby pump trains.

The monitoring agent detected a fault (PCA Hotelling T²/SPE + alarm/pump/controller
rules) and ranked hypotheses; the planning agent turned them into a brief with a
target fault, an objective, KG-grounded candidate levers and constraints. You also get
the live operator snapshot, the action catalog with bounds and the KG evidence.

# How to use the plan and KG (MANDATORY)
- Work towards the brief's objective using its candidate levers; deviate only if the
  snapshot contradicts the brief, and say why.
- FaultMode nodes (fault_*) list symptoms and affected parameters; controller_* nodes
  say what a loop controls; alarm_*/SIF_* give limits and safety overrides; action_*
  nodes say what operators may act on. Cite the node ids you rely on in `kg_evidence`.

# Output (STRICT JSON, no markdown)
{"commands": [command, ...], "reasoning": "<brief KG-grounded justification>",
 "kg_evidence": ["node_id", ...]}
Each command is one of:
  {"type": "NO_ACTION"}
  {"type": "START_SPARE", "target": <pump train>}
  {"type": "SET_THROUGHPUT", "value": <kmol/h>}
  {"type": "SET_SETPOINT", "target": <loop id>, "value": <number>}
  {"type": "OPERATE_AUXILIARY", "target": "H2_BDV" | "EMERGENCY_COOLING", "value": <0..1>}

# Hard rules
1. Stay inside the catalog bounds and units; never touch faults, utilities or SIFs.
2. A failed duty pump with a healthy standby → START_SPARE that train.
3. Do not push a loop whose output is already saturated; relieve the load instead.
4. The reactor is endothermic: lowering feed does NOT cool it.
5. Prefer small, reversible changes. After a rejected proposal, use the feedback and
   propose something DIFFERENT.
6. If no lever addresses the fault (e.g. a sensor fault), return NO_ACTION and say what
   the operator should check.
7. Targets must be EXACTLY the names in action_catalog.command_targets: loops like "R_PIC"
   (not "controller_R_PIC"), pump trains like "REFLUX", auxiliaries "H2_BDV" /
   "EMERGENCY_COOLING". Every KG node carries `sim` with its simulator name and, if it can
   be acted on, the exact `sim.command` to use; exogenous (`commandable: false`) nodes
   are disturbances or faults and cannot be changed.
8. Keep every setpoint clearly inside the alarm limits of its variable
   (action_catalog.setpoint_alarm_limits); a setpoint near an alarm creates a new alarm.
9. START_SPARE only for a train whose selected pump has failed (pump_trains[...].selected_failed).
10. Change ONLY what the diagnosed fault requires. Every extra setpoint change moves the plant
    away from normal operation and fails validation (PCA must return to normal). For a pump
    trip, START_SPARE alone is the complete answer.
""".strip()


def _command_targets() -> dict:
    from .kg_map import default_map
    return default_map().command_targets()


def _loop_alarm_limits(catalog: dict) -> dict:
    from integrated_plant.alarms import default_alarm_definitions
    defs = default_alarm_definitions()
    out = {}
    for loop, info in catalog["controllers"].items():
        lims = {d.alarm_id: f"{'>' if d.direction == 'HIGH' else '<'} {d.limit:g} {d.unit}".strip()
                for d in defs if d.tag == info.get("pv_tag")}
        if lims:
            out[loop] = {"pv": info.get("pv_tag"), "alarms": lims}
    return out


def slim_observation(obs: dict) -> dict:
    ctrl = {k: {f: v[f] for f in ("mode", "setpoint", "pv", "last_output", "sp_min", "sp_max",
                                  "sp_unit", "mv_min", "mv_max", "mv_unit")}
            for k, v in obs["controllers"].items()}
    return {"time_min": round(obs["time_min"], 2),
            "measurements": {k: round(v, 4) for k, v in obs["measurements"].items() if not k.startswith("pump_")},
            "controllers": ctrl, "throughput_target_kmol_h": obs["throughput_target_kmol_h"],
            "auxiliaries": obs["auxiliaries"], "pump_trains": obs["pump_trains"],
            "active_alarms": {a: s for a, s in obs["alarm_states"].items() if s != "NORMAL"},
            "sif_latched": list(obs["sif_latched"])}


class LLMActionAgent:
    def __init__(self, model: str = DEFAULT_MODEL, timeout: float = 120.0, llm: Any = None):
        self.model = model
        self.llm = llm if llm is not None else make_llm(model, timeout, json_mode=True)
        self.last_usage: dict = {}

    async def propose(self, context: dict) -> dict:
        catalog = context["catalog"]
        user = {
            "plan_brief": context.get("brief"),
            "snapshot": slim_observation(context["observation"]),
            "monitoring": {k: context["detection"][k] for k in ("pca", "rules", "detectors")},
            "diagnosis": {k: context["diagnosis"].get(k) for k in ("hypotheses", "contributors", "summary")},
            "kg_evidence": context.get("knowledge") or [],
            "action_catalog": {"throughput_bounds_kmol_h": catalog["throughput_bounds_kmol_h"],
                               "auxiliaries": catalog["auxiliaries"],
                               "setpoint_bounds": {k: [v["sp_min"], v["sp_max"], v["sp_unit"]]
                                                   for k, v in catalog["controllers"].items()},
                               "setpoint_alarm_limits": _loop_alarm_limits(catalog),
                               "command_targets": _command_targets(),
                               "pump_trains": list(catalog["pump_trains"])},
            "previous_proposal": context.get("previous_plan"),
            "evaluation_feedback": context.get("feedback") or None,
        }
        t0 = time.perf_counter()
        msg = await self.llm.ainvoke([("system", ACTION_SYSTEM_PROMPT), ("human", json.dumps(user, default=str))])
        text = message_text(msg)
        self.last_usage = {**USAGE.record("action", self.model, msg),
                           "latency_s": time.perf_counter() - t0, "raw": text[:4000]}
        return parse_json_object(text)


_AUX_ALIASES = {"ACT_PARAM_CSTR_BDV_VALVE": "H2_BDV", "BDV-101": "H2_BDV", "BDV": "H2_BDV", "CSTR_BDV_VALVE": "H2_BDV",
                "ACT_PARAM_CSTR_EMERGENCY_COOLING_FACTOR": "EMERGENCY_COOLING",
                "CSTR_EMERGENCY_COOLING_FACTOR": "EMERGENCY_COOLING"}


def normalise_target(target: Any) -> Any:
    """Map KG node ids / variants the LLM copies from the evidence onto catalog names.

    controller_R_PIC / PO_R_PIC / r_pic → R_PIC; pump_train_REFLUX → REFLUX; BDV-101 → H2_BDV.
    """
    if not isinstance(target, str):
        return target
    t = target.strip()
    for prefix in ("controller_", "PO_", "pump_train_", "loop_", "alarm_"):
        if t.lower().startswith(prefix.lower()):
            t = t[len(prefix):]
    return _AUX_ALIASES.get(t.upper(), t.upper())


def normalise_plan(plan: Any, vmap: Any = None) -> tuple[dict, list[str]]:
    """Strip LLM extras (kg_evidence, null fields) into the compile_plan schema.

    Targets are resolved through the KG ↔ simulator map, so KG ids, equipment tags and
    variable names all become catalog targets (PCV-101 / controller_R_PIC → R_PIC).
    """
    if vmap is None:
        from .kg_map import default_map
        vmap = default_map()
    if not isinstance(plan, dict):
        return {"commands": [], "reasoning": ""}, []
    cites = [str(x) for x in plan.get("kg_evidence", []) or []]
    cmds = []
    for c in plan.get("commands", []) or []:
        if not isinstance(c, dict):
            continue
        c = {k: v for k, v in c.items() if v is not None}
        if "type" in c:
            c["type"] = str(c["type"]).upper()
        if "target" in c:
            resolved = vmap.command_target(c.get("type", ""), c["target"])
            c["target"] = normalise_target(resolved)
        cmds.append(c)
    if len(cmds) > 1:  # NO_ACTION mixed with real commands: keep the real ones
        cmds = [c for c in cmds if c.get("type") != "NO_ACTION"] or cmds[:1]
    return {"commands": cmds, "reasoning": str(plan.get("reasoning", ""))}, cites


def explain_plan_errors(plan: dict, catalog: dict) -> list[str]:
    """Actionable errors (with the valid names) for the reprompter, before compile_plan."""
    errors = []
    loops, trains, aux = list(catalog["controllers"]), list(catalog["pump_trains"]), list(catalog["auxiliaries"])
    for c in plan.get("commands", []):
        kind, target = c.get("type"), c.get("target")
        if kind == "SET_SETPOINT" and target not in loops:
            errors.append(f"unknown loop {target!r} in SET_SETPOINT; use one of {loops}")
        elif kind == "START_SPARE" and target not in trains:
            errors.append(f"unknown pump train {target!r} in START_SPARE; use one of {trains}")
        elif kind == "OPERATE_AUXILIARY" and target not in aux:
            errors.append(f"unknown auxiliary {target!r}; use one of {aux}")
        elif kind == "SET_SETPOINT" and isinstance(c.get("value"), (int, float)):
            lo, hi = catalog["controllers"][target]["sp_min"], catalog["controllers"][target]["sp_max"]
            if not lo <= c["value"] <= hi:
                errors.append(f"{target} setpoint {c['value']} outside its range [{lo}, {hi}]")
    return errors
