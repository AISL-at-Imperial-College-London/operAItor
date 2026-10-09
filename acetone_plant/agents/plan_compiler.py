"""Turn an agent plan (JSON commands) into one validated SupervisoryAction, and check the
operating envelope of a (twin) plant.

    compile_plan(plan, observation, catalog) -> (SupervisoryAction, errors)
    inspect_operation(env, observation, truth=False) -> envelope / alarm / saturation summary

Validation bounds express operator authority (catalog limits), not predicted process safety;
the simulator's independent SIF layer stays authoritative.
"""
from __future__ import annotations

from dataclasses import replace
import math

from acetone_plant.plant.actions import SupervisoryAction


def validate_proposal(proposal: dict, observation: dict, catalog: dict):
    """Validate a deliberately small action schema without mutating the plant.

    Bounds validate operator authority, not predicted process safety. The
    simulator's independent SIF arbitration remains authoritative.
    """
    try:
        if not isinstance(proposal, dict):
            raise ValueError("Proposal must be a dictionary")
        kind = proposal.get("type")
        supported = {"NO_ACTION", "START_SPARE", "OPERATE_AUXILIARY",
                     "SET_THROUGHPUT", "SET_SETPOINT"}
        if kind not in supported or kind not in catalog["actions"]:
            raise ValueError(f"Unsupported action: {kind}")
        fields = {"type"} if kind == "NO_ACTION" else {"type", "value"} if kind == "SET_THROUGHPUT" else {"type", "target"} if kind == "START_SPARE" else {"type", "target", "value"}
        if set(proposal) != fields:
            raise ValueError(f"Expected exactly these fields: {sorted(fields)}")
        if kind == "NO_ACTION":
            return SupervisoryAction.no_action(), []
        target = proposal.get("target")
        if kind == "START_SPARE":
            if target not in observation["pump_trains"]:
                raise ValueError("Unknown pump train")
            info = observation["pump_trains"][target]
            if info["spare_failed"]:
                raise ValueError("Standby pump unavailable")
            return SupervisoryAction(start_spares=(target,)), []
        value = proposal["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Value must be a finite number")
        if kind == "SET_THROUGHPUT":
            lo, hi = catalog["throughput_bounds_kmol_h"]
            action = SupervisoryAction(throughput_target=value)
        elif kind == "SET_SETPOINT":
            if target not in catalog["controllers"]:
                raise ValueError("Unknown controller")
            loop = catalog["controllers"][target]
            lo, hi = loop["sp_min"], loop["sp_max"]
            action = SupervisoryAction(setpoints={target: value})
        else:
            if target not in catalog["auxiliaries"]:
                raise ValueError("Unknown auxiliary")
            bounds = catalog["auxiliaries"][target]
            lo, hi = bounds["min"], bounds["max"]
            action = SupervisoryAction(auxiliary_commands={target: value})
        if not lo <= value <= hi:
            raise ValueError(f"Value outside [{lo}, {hi}]")
        return action, []
    except (ValueError, TypeError, KeyError) as exc:
        return SupervisoryAction.no_action(), [str(exc)]


def inspect_operation(env, observation: dict, *, truth: bool = False) -> dict:
    """Integrated-model safety envelope + alarm/control-margin monitoring.

    Numeric limits come from this plant's SIF configuration, not the old CSTR.
    Saturation is judged against each loop's actual MV bounds.
    """
    m = env.plant.measurements(env.y) if truth else observation["measurements"]
    c = env.safety.cfg
    intervals = {
        "cstr_temperature_K": (-math.inf, c.cstr_temperature_hh_K),
        "cstr_pressure_bar": (-math.inf, c.cstr_pressure_hh_bar),
        "cstr_holdup_kmol": (c.cstr_level_ll_kmol, c.cstr_level_hh_kmol),
        "reboiler_holdup_kmol": (c.reboiler_level_ll_kmol, c.reboiler_level_hh_kmol),
        "drum_holdup_kmol": (c.drum_level_ll_kmol, c.drum_level_hh_kmol),
        "column_pressure_bar": (-math.inf, c.column_pressure_hh_bar),
    }
    unsafe = [tag for tag, (lo, hi) in intervals.items()
              if not math.isfinite(m[tag]) or not lo < m[tag] < hi]
    saturated = []
    invalid = []
    for loop, info in observation["controllers"].items():
        output, lo, hi = info["last_output"], info["mv_min"], info["mv_max"]
        if not math.isfinite(output) or not lo <= output <= hi:
            invalid.append(loop)
        elif hi > lo and (output - lo) / (hi - lo) >= 0.95:
            # High demand with significant PV error indicates depleted margin.
            span = info["sp_max"] - info["sp_min"]
            if abs(info.get("pv", info["setpoint"]) - info["setpoint"]) > 0.05 * span:
                saturated.append(loop)
    failed = [train for train, info in observation["pump_trains"].items()
              if info["selected_failed"]]
    alarms = [tag for tag, status in observation["alarm_states"].items()
              if status in ("ACTIVE_UNACK", "ACTIVE_ACK")]
    safe = not (unsafe or invalid or failed or observation["sif_latched"])
    zone = "UNSAFE" if not safe else "WARNING" if saturated or alarms else "SAFE"
    return {"safe_now": safe, "control_zone": zone, "unsafe_tags": unsafe,
            "invalid_controller_outputs": invalid, "saturated_loops": saturated,
            "failed_trains": failed, "active_alarms": alarms,
            "abnormal": bool(unsafe or invalid or failed or saturated or alarms or
                             observation["sif_latched"])}


def compile_plan(plan, observation, catalog):
    """Validate all commands before combining them into one operator action."""
    if (not isinstance(plan, dict) or set(plan) != {"commands", "reasoning"}
            or not isinstance(plan["reasoning"], str)
            or not isinstance(plan["commands"], list) or not plan["commands"]):
        return SupervisoryAction.no_action(), ["Expected nonempty commands list and reasoning string"]
    combined = SupervisoryAction()
    seen = set()
    for proposal in plan["commands"]:
        action, errors = validate_proposal(proposal, observation, catalog)
        if errors:
            return SupervisoryAction.no_action(), errors
        identity = (proposal["type"], proposal.get("target"))
        if identity in seen or (proposal["type"] == "NO_ACTION" and len(plan["commands"]) != 1):
            return SupervisoryAction.no_action(), ["Duplicate command or mixed NO_ACTION"]
        seen.add(identity)
        combined = replace(combined,
            throughput_target=action.throughput_target if action.throughput_target is not None else combined.throughput_target,
            setpoints={**combined.setpoints, **action.setpoints},
            auxiliary_commands={**combined.auxiliary_commands, **action.auxiliary_commands},
            start_spares=combined.start_spares + action.start_spares)
    return combined, []
