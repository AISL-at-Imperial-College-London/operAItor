"""Action agents: turn the planning brief into concrete, validatable commands.

Both return the plan format used by ``plan_compiler.compile_plan``:

    {"commands": [{"type": "START_SPARE", "target": "REFLUX"}, ...], "reasoning": "..."}

``LLMActionAgent`` calls a chat model with ``ainvoke`` (so it never blocks the
plant loop) and records latency and token usage in ``last_usage``. There is no
rule-based action agent: every action comes from the LLM.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Protocol

from .llm import DEFAULT_MODEL, USAGE, make_llm, message_text, parse_json_object


class ActionAgent(Protocol):
    async def propose(self, context: dict) -> dict: ...


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
    from acetone_plant.plant.alarms import default_alarm_definitions
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


# --------------------------------------------------------------------------- context levels
# PLANT_ACTION_CONTEXT selects what the action LLM is told (ablation of "is it the model or the
# context?"). All levels are generic plant / control-room knowledge, not fault-specific recipes.
#   base    the original prompt: brief levers are mandatory, "change only what the fault requires"
#   loops   + what each loop's setpoint physically does; brief levers are suggestions; no
#           "single change" rule
#   manual  + a short generic operating manual (column / drum balances, biased transmitters)
LOOP_GUIDE = {
    "R_LIC": "reactor level -> CSTR outflow pump. Raising the SP holds more liquid (outflow drops for a while).",
    "R_TIC": "reactor temperature -> jacket heater. Raising the SP adds heat: faster reaction, more H2, higher pressure.",
    "R_PIC": "reactor pressure -> H2 vent valve (PCV). Raising the SP closes the PCV (less H2 vented, pressure rises). "
             "If the pressure transmitter is wrong this loop controls a wrong value.",
    "RB_LIC": "reboiler level -> bottoms draw. Raising the SP holds more liquid in the reboiler (less bottoms draw).",
    "DR_LIC": "reflux-drum level -> distillate draw. Raising the SP holds more liquid (less distillate). "
              "At 0 output the draw is already closed.",
    "COL_PIC": "column pressure -> condenser cooling-water valve. Raising the SP lets pressure float higher (valve "
               "closes). It cannot add cooling beyond a fully open valve.",
    "TOP_TIC": "top temperature -> reflux flow, REVERSE acting: raising the SP REDUCES reflux (top runs hotter, "
               "distillate purity falls); lowering the SP increases reflux.",
    "BOT_TIC": "bottom temperature -> reboiler steam: raising the SP adds steam (more boil-up, more vapour to "
               "condense); lowering the SP reduces boil-up.",
}
OPERATING_MANUAL = [
    "All vapour boiled up in the reboiler must be condensed. If condensing capacity falls, column pressure rises "
    "and less liquid reaches the reflux drum, unless boil-up (steam) is reduced to match.",
    "Reflux-drum balance: condensate in = reflux + distillate + any leak out. With the distillate draw already "
    "shut, only less reflux (or more condensate) stops the drum from draining.",
    "Throughput changes act slowly (through the reactor holdup) and change the column load only gradually.",
    "A biased or frozen transmitter makes its loop control to a wrong value. Act on the true variable with an "
    "independent lever (e.g. the H2 blowdown for reactor pressure) rather than moving that loop's setpoint.",
    "Several commands may be needed at once; the plan is judged as a whole.",
    "The reactor is endothermic: lowering feed does not cool it.",
]


def _context_level() -> str:
    level = os.environ.get("PLANT_ACTION_CONTEXT", "base")
    return level if level in ("base", "loops", "manual", "dynamics") else "base"


def action_system_prompt(level: str) -> str:
    if level == "base":
        return ACTION_SYSTEM_PROMPT
    p = ACTION_SYSTEM_PROMPT.replace(
        """- Work towards the brief's objective using its candidate levers; deviate only if the
  snapshot contradicts the brief, and say why.""",
        """- The brief's candidate levers come from a knowledge-graph lookup and can be incomplete:
  use any catalog command the plant needs, and say why. `operating_guide` says what each
  loop's setpoint physically does.""")
    i = p.index("10. Change ONLY what the diagnosed fault requires.")
    p = p[:i].rstrip() + "\n"
    if level == "dynamics":
        p = p.replace("""5. Prefer small, reversible changes. After a rejected proposal, use the feedback and
   propose something DIFFERENT.""", """5. Size the action to the time available. `dynamics` gives each key variable's rate of change
   and the minutes left before its next alarm / SIF limit at that rate. When a plant-trip SIF is
   only minutes away, act decisively NOW (several levers at once, large enough to reverse the
   trend) instead of small steps over several decisions; you may not get another chance. After a
   rejected proposal, use the feedback and propose something DIFFERENT.""")
    return p


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
        level = _context_level()
        if level != "base":
            user["operating_guide"] = {"loops": LOOP_GUIDE,
                                       **({"principles": OPERATING_MANUAL} if level in ("manual", "dynamics") else {})}
        if level == "dynamics":
            user["dynamics"] = context["detection"].get("trends") or {}
        t0 = time.perf_counter()
        msg = await self.llm.ainvoke([("system", action_system_prompt(level)), ("human", json.dumps(user, default=str))])
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
