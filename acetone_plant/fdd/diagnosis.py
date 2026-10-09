"""Rule checks and contribution-based fault diagnosis.

Everything here uses operator observations only (no simulator ground truth),
so it is safe to run inside an autonomous monitoring agent.
"""
from __future__ import annotations

from typing import Mapping

from acetone_plant.plant.alarms import default_alarm_definitions

from .pca import PCAScore

ALARM_TAGS = {d.alarm_id: d.tag for d in default_alarm_definitions()}

ACTIVE_ALARM_STATES = ("ACTIVE_UNACK", "ACTIVE_ACK")

# Which plant section / fault classes a feature points to when it dominates the
# PCA contributions. Fault class names match the simulator's fault library so
# diagnosis accuracy can be scored against episode ground truth offline.
VARIABLE_KNOWLEDGE: dict[str, tuple[str, tuple[str, ...]]] = {
    "meas.cstr_pressure_bar": ("R-101 pressure / H2 vent", ("h2_pcv_capacity_degradation", "h2_pcv_stuck_closed", "reactor_pressure_sensor_low_bias")),
    "op.R_PIC": ("R-101 pressure / H2 vent", ("h2_pcv_capacity_degradation", "h2_pcv_stuck_closed")),
    "meas.cstr_h2_vent_kmol_h": ("R-101 pressure / H2 vent", ("h2_pcv_stuck_closed", "catalyst_activity_loss")),
    "meas.cstr_temperature_K": ("R-101 heating", ("reactor_heater_stuck_high", "catalyst_activity_loss")),
    "op.R_TIC": ("R-101 heating", ("reactor_heater_stuck_high",)),
    "meas.cstr_x_acetone": ("R-101 conversion", ("catalyst_activity_loss",)),
    "meas.cstr_holdup_kmol": ("R-101 inventory", ("cstr_outlet_capacity_loss", "cstr_liquid_leak")),
    "op.R_LIC": ("R-101 inventory", ("cstr_outlet_capacity_loss", "cstr_liquid_leak")),
    "meas.column_pressure_bar": ("C-201 pressure / condenser", ("condenser_cooling_loss",)),
    "op.COL_PIC": ("C-201 pressure / condenser", ("condenser_cooling_loss",)),
    "meas.top_pressure_bar": ("C-201 pressure / condenser", ("condenser_cooling_loss",)),
    "meas.drum_holdup_kmol": ("Reflux drum inventory", ("reflux_drum_leak", "reflux_pump_trip")),
    "op.DR_LIC": ("Reflux drum inventory", ("reflux_drum_leak",)),
    "meas.top_temperature_K": ("C-201 top / reflux", ("reflux_pump_trip", "reflux_valve_stiction", "top_temperature_sensor_freeze")),
    "op.TOP_TIC": ("C-201 top / reflux", ("reflux_pump_trip", "reflux_valve_stiction")),
    "meas.distillate_x_acetone": ("C-201 separation", ("reflux_pump_trip", "condenser_cooling_loss")),
    "meas.reboiler_holdup_kmol": ("Reboiler inventory", ("reboiler_liquid_leak",)),
    "op.RB_LIC": ("Reboiler inventory", ("reboiler_liquid_leak",)),
    "meas.bottom_temperature_K": ("Reboiler heating", ()),
    "op.BOT_TIC": ("Reboiler heating", ()),
    "meas.bottoms_x_acetone": ("C-201 separation", ()),
    "meas.column_min_tray_holdup_kmol": ("C-201 tray liquid (dry trays)", ("reflux_pump_trip", "reflux_valve_stiction", "reflux_drum_leak")),
}


def _section_for(variable: str) -> tuple[str, tuple[str, ...]]:
    if variable in VARIABLE_KNOWLEDGE:
        return VARIABLE_KNOWLEDGE[variable]
    if variable.startswith("meas.tray_"):
        return ("C-201 tray profile", ("reflux_pump_trip", "condenser_cooling_loss"))
    return ("unmapped", ())


def rule_checks(observation: Mapping) -> dict:
    """Deterministic indications from alarms, pumps, SIFs and controller margins."""
    failed = [t for t, info in observation["pump_trains"].items() if info["selected_failed"]]
    alarms = [a for a, s in observation["alarm_states"].items() if s in ACTIVE_ALARM_STATES]
    saturated = []
    for loop, info in observation["controllers"].items():
        lo, hi, out = info["mv_min"], info["mv_max"], info["last_output"]
        if hi > lo and info["mode"] == "AUTO":
            frac = (out - lo) / (hi - lo)
            span = info["sp_max"] - info["sp_min"]
            if (frac >= 0.97 or frac <= 0.03) and abs(info["pv"] - info["setpoint"]) > 0.03 * span:
                saturated.append(loop)
    sif = list(observation.get("sif_latched", ()))
    return {"failed_trains": failed, "active_alarms": alarms,
            "saturated_loops": saturated, "sif_latched": sif,
            "plant_trip": bool(observation.get("plant_trip_latched")),
            "abnormal": bool(failed or alarms or saturated or sif)}


def diagnose(score: PCAScore | None, rules: Mapping, n_contrib: int = 6) -> dict:
    """Rank fault hypotheses from rule evidence and PCA contributions."""
    hypotheses: dict[str, dict] = {}

    failed = {t.lower() for t in rules["failed_trains"]}

    def add(name: str, weight: float, evidence: str):
        # Pump status is directly observable: never hypothesise a trip of a pump that is running.
        if name.endswith("_pump_trip") and name.removesuffix("_pump_trip") not in failed:
            return
        h = hypotheses.setdefault(name, {"fault": name, "score": 0.0, "evidence": []})
        h["score"] += weight
        h["evidence"].append(evidence)

    for train in rules["failed_trains"]:
        add(f"{train.lower()}_pump_trip", 10.0, f"selected {train} pump reports failed")
    for loop in rules["saturated_loops"]:
        add(f"{loop}_out_of_range", 2.0, f"{loop} output saturated with PV off setpoint")
    # Alarms point at fault modes through the variable they monitor (useful before PCA confirms).
    for alarm in rules["active_alarms"]:
        tag = ALARM_TAGS.get(alarm, "")
        if tag.startswith("pump_"):
            continue  # pump trips are already covered by failed_trains
        for f in _section_for(f"meas.{tag}")[1]:
            add(f, 1.0, f"alarm {alarm} on {tag}")

    sections: dict[str, float] = {}
    contributors = score.top_contributors(n_contrib) if score is not None else []
    if score is not None and score.violation:
        total = sum(c["score"] for c in contributors) or 1.0
        for c in contributors:
            section, faults = _section_for(c["variable"])
            share = c["score"] / total
            sections[section] = sections.get(section, 0.0) + share
            for f in faults:
                add(f, 3.0 * share, f"{c['variable']} contributes {100*share:.0f}%")
        # One variable explaining most of a residual violation, with its own
        # loop not compensating, is the classic signature of a sensor fault.
        if score.spe_violation and contributors:
            spe_total = sum(score.spe_contrib.values()) or 1.0
            top = max(score.spe_contrib.items(), key=lambda kv: kv[1])
            if top[1] / spe_total > 0.6:
                add(f"sensor_fault:{top[0]}", 2.5, f"{top[0]} explains {100*top[1]/spe_total:.0f}% of SPE")

    ranked = sorted(hypotheses.values(), key=lambda h: -h["score"])
    return {"hypotheses": ranked[:5],
            "sections": sorted(({"section": k, "share": v} for k, v in sections.items()),
                               key=lambda s: -s["share"]),
            "contributors": contributors,
            "summary": ranked[0]["fault"] if ranked else "no fault isolated"}
