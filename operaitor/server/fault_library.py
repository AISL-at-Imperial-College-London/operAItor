"""The fault library: every named fault the benchmark, the human test, the agents and ADMIT use.

Each entry gives the PlantService "inject" parameters of one fault (see PlantService._inject). The names are
the knowledge graph's fault modes and the diagnosis vocabulary, so a fault can be injected, diagnosed and scored
under one name. ``fault_params`` returns a fresh copy to inject (extra keys of a scenario override it).
"""
from __future__ import annotations

FAULT_LIBRARY: dict[str, dict] = {
    "reflux_pump_trip":            {"kind": "pump", "pump_id": "P-201A"},
    "bottoms_pump_trip":           {"kind": "pump", "pump_id": "P-203A"},
    "distillate_pump_trip":        {"kind": "pump", "pump_id": "P-202A"},
    "feed_pump_trip":              {"kind": "pump", "pump_id": "P-101A"},
    "cstr_outlet_capacity_loss":   {"kind": "actuator", "target": "cstr_outflow", "mode": "capacity", "capacity_fraction": 0.20},
    "h2_pcv_stuck_closed":         {"kind": "actuator", "target": "cstr_vent_valve", "mode": "fixed", "fixed_value": 0.0},
    "h2_pcv_capacity_degradation": {"kind": "actuator", "target": "cstr_vent_valve", "mode": "capacity_ramp",
                                    "capacity_fraction": 0.20, "ramp_min": 10.0},
    "reactor_heater_stuck_high":   {"kind": "actuator", "target": "cstr_heater_factor", "mode": "fixed", "fixed_value": 1.0},
    "reflux_valve_stiction":       {"kind": "actuator", "target": "reflux_flow", "mode": "stiction", "stiction_band_fraction": 0.08},
    "bottoms_flow_freeze":         {"kind": "actuator", "target": "bottoms_flow", "mode": "freeze"},
    "reactor_pressure_sensor_low_bias": {"kind": "sensor", "target": "cstr_pressure_bar", "mode": "bias", "bias": -1.2},
    "top_temperature_sensor_freeze": {"kind": "sensor", "target": "top_temperature_K", "mode": "freeze"},
    "condenser_cooling_loss":      {"kind": "process", "fault": "condenser_cooling_loss", "severity": 80},
    "catalyst_activity_loss":      {"kind": "process", "fault": "catalyst_activity_loss", "severity": 40},
    "reflux_drum_leak":            {"kind": "process", "fault": "drum_liquid_leak", "severity": 100},
    "cstr_liquid_leak":            {"kind": "process", "fault": "cstr_liquid_leak", "severity": 30},
    "reboiler_liquid_leak":        {"kind": "process", "fault": "reboiler_liquid_leak", "severity": 30},
    # Normal disturbance (not a fault): the agents should NOT act on this.
    "cooling_water_warm":          {"kind": "disturbance", "target": "cooling_water_temperature_K", "direction": 1},  # +3 K
}


def fault_params(name: str) -> dict:
    """The inject parameters of a library fault (a copy: callers add or override keys)."""
    return dict(FAULT_LIBRARY[name])
