"""Fault campaigns: timed sequences of master injections for the live plant.

A campaign is a list of injections, each relative to the campaign start (sim min).
By default the next injection waits until the monitoring agent reports normal for
3 min (max 45 min), and operator settings + SIF latches are restored after each
fault clears, so faults are judged one at a time from the same operating point.



    {"at_min": 5, "fault": "reflux_pump_trip", "duration_min": 20}

``fault`` names a ``FAULT_LIBRARY`` entry (same names as the KG fault modes and
the diagnosis vocabulary, so live sessions can be scored). Extra keys override
the template's parameters. ``duration_min: 0`` means persistent.
"""
from __future__ import annotations

import numpy as np

# name → PlantService "inject" parameters (+ optional "vary": {param: (lo, hi)} for random campaigns)
FAULT_LIBRARY: dict[str, dict] = {
    "reflux_pump_trip":            {"kind": "pump", "pump_id": "P-201A"},
    "bottoms_pump_trip":           {"kind": "pump", "pump_id": "P-203A"},
    "distillate_pump_trip":        {"kind": "pump", "pump_id": "P-202A"},
    "feed_pump_trip":              {"kind": "pump", "pump_id": "P-101A"},
    "cstr_outlet_capacity_loss":   {"kind": "actuator", "target": "cstr_outflow", "mode": "capacity",
                                    "capacity_fraction": 0.20, "vary": {"capacity_fraction": (0.15, 0.45)}},
    "h2_pcv_stuck_closed":         {"kind": "actuator", "target": "cstr_vent_valve", "mode": "fixed", "fixed_value": 0.0},
    "h2_pcv_capacity_degradation": {"kind": "actuator", "target": "cstr_vent_valve", "mode": "capacity_ramp",
                                    "capacity_fraction": 0.20, "ramp_min": 10.0,
                                    "vary": {"capacity_fraction": (0.15, 0.35), "ramp_min": (6.0, 12.0)}},
    "reactor_heater_stuck_high":   {"kind": "actuator", "target": "cstr_heater_factor", "mode": "fixed", "fixed_value": 1.0},
    "reflux_valve_stiction":       {"kind": "actuator", "target": "reflux_flow", "mode": "stiction",
                                    "stiction_band_fraction": 0.08, "vary": {"stiction_band_fraction": (0.05, 0.12)}},
    "bottoms_flow_freeze":         {"kind": "actuator", "target": "bottoms_flow", "mode": "freeze"},
    "reactor_pressure_sensor_low_bias": {"kind": "sensor", "target": "cstr_pressure_bar", "mode": "bias",
                                         "bias": -1.2, "vary": {"bias": (-1.3, -0.8)}},
    "top_temperature_sensor_freeze": {"kind": "sensor", "target": "top_temperature_K", "mode": "freeze"},
    "condenser_cooling_loss":      {"kind": "process", "fault": "condenser_cooling_loss", "severity": 80,
                                    "vary": {"severity": (60, 90)}},
    "catalyst_activity_loss":      {"kind": "process", "fault": "catalyst_activity_loss", "severity": 40,
                                    "vary": {"severity": (25, 50)}},
    "reflux_drum_leak":            {"kind": "process", "fault": "drum_liquid_leak", "severity": 100,
                                    "vary": {"severity": (75, 110)}},
    "cstr_liquid_leak":            {"kind": "process", "fault": "cstr_liquid_leak", "severity": 30,
                                    "vary": {"severity": (20, 45)}},
    "reboiler_liquid_leak":        {"kind": "process", "fault": "reboiler_liquid_leak", "severity": 30,
                                    "vary": {"severity": (20, 45)}},
    # Normal disturbance (not a fault): the agents should NOT act on this.
    "cooling_water_warm":          {"kind": "disturbance", "target": "cooling_water_temperature_K", "multiplier": 1.1},
}

CAMPAIGNS: dict[str, dict] = {
    "demo": {
        "description": "Four recoverable faults with recovery gaps (settings restored after each)",
        "items": [
            {"at_min": 5, "fault": "reflux_pump_trip", "duration_min": 15},
            {"at_min": 30, "fault": "reactor_pressure_sensor_low_bias", "duration_min": 15},
            {"at_min": 55, "fault": "cstr_outlet_capacity_loss", "duration_min": 15},
            {"at_min": 80, "fault": "condenser_cooling_loss", "severity": 60, "duration_min": 10},
        ],
    },
    "reprompt_demo": {
        "description": "One severe condenser loss: the twin rejects weak responses, the reprompter escalates",
        "items": [{"at_min": 5, "fault": "condenser_cooling_loss", "severity": 70, "duration_min": 20}],
    },
    "pumps": {
        "description": "Duty-pump trips on every pump train",
        "items": [{"at_min": 5 + 15 * i, "fault": f, "duration_min": 0}
                  for i, f in enumerate(["reflux_pump_trip", "bottoms_pump_trip", "distillate_pump_trip", "feed_pump_trip"])],
    },
    "sensors_vs_process": {
        "description": "A disturbance, a sensor fault and a real process fault: can the agents tell them apart?",
        "items": [
            {"at_min": 5, "fault": "cooling_water_warm", "duration_min": 15},
            {"at_min": 30, "fault": "reactor_pressure_sensor_low_bias", "duration_min": 15},
            {"at_min": 55, "fault": "h2_pcv_capacity_degradation", "duration_min": 20},
        ],
    },
}


def random_campaign(seed: int = 0, n: int = 4, gap_min: float = 25.0, duration_min: float = 15.0,
                    start_min: float = 5.0, faults: list[str] | None = None) -> dict:
    """Seeded random sequence: n faults, randomised severities (template 'vary' ranges)."""
    rng = np.random.default_rng(seed)
    pool = faults or [f for f, t in FAULT_LIBRARY.items() if t["kind"] != "disturbance"]
    items = []
    for i, name in enumerate(rng.choice(pool, size=n, replace=n > len(pool))):
        item = {"at_min": round(start_min + i * gap_min + float(rng.uniform(0, 3)), 1), "fault": str(name),
                "duration_min": duration_min}
        for param, (lo, hi) in FAULT_LIBRARY[str(name)].get("vary", {}).items():
            item[param] = round(float(rng.uniform(lo, hi)), 3)
        items.append(item)
    return {"description": f"random seed={seed}, {n} faults every {gap_min:g} min", "items": items}


def build_campaign(spec: str | dict, **random_kw) -> tuple[str, list[dict]]:
    """Resolve a preset name, 'random', or an explicit {'items': [...]} into injection commands."""
    if isinstance(spec, dict):
        name, camp = spec.get("name", "custom"), spec
    elif spec == "random":
        name, camp = f"random_s{random_kw.get('seed', 0)}", random_campaign(**random_kw)
    elif spec in CAMPAIGNS:
        name, camp = spec, CAMPAIGNS[spec]
    else:
        raise ValueError(f"unknown campaign {spec!r}; choose from {sorted(CAMPAIGNS)} or 'random'")
    items = []
    for raw in sorted(camp["items"], key=lambda x: x["at_min"]):
        if raw["fault"] not in FAULT_LIBRARY:
            raise ValueError(f"unknown fault {raw['fault']!r}; choose from {sorted(FAULT_LIBRARY)}")
        template = {k: v for k, v in FAULT_LIBRARY[raw["fault"]].items() if k != "vary"}
        # The item's "fault" is the library name; process templates have their own "fault" parameter.
        overrides = {k: v for k, v in raw.items() if k != "fault"}
        items.append({**template, **overrides, "name": raw["fault"], "label": raw.get("label", raw["fault"])})
    return name, items
