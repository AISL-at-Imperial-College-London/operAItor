"""Cause & effect matrix of the safety instrumented functions, derived from the safety system itself.

Each SIF cause is latched on its own at the design point and ``SafetySystem.apply`` is run; the matrix lists what
it does to every final element. docs/pid.md shows the result with the P&ID (frontend/public/static/pid.svg), and the
console's P&ID view shows it live (checked by tests/test_cause_effect.py):

    python -m operaitor.plant.cause_effect           # prints the cause & effect table
    python -m operaitor.plant.cause_effect --write   # rewrites docs/pid.md
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from .plant import IntegratedPlantDAE, PlantInputs
from .safety import SafetyConfig, SafetySystem


@dataclass(frozen=True)
class Cause:
    sif: str            # SafetySystem cause id
    description: str
    measurement: str    # measured variable (plant.measurements key)
    direction: str      # "HIGH": trips at or above the limit; "LOW": at or below
    limit_attr: str     # SafetyConfig field holding the limit
    unit: str


CAUSES = (
    Cause("R_T_HH", "Reactor temperature high-high", "cstr_temperature_K", "HIGH", "cstr_temperature_hh_K", "K"),
    Cause("R_P_HH", "Reactor pressure high-high", "cstr_pressure_bar", "HIGH", "cstr_pressure_hh_bar", "bar"),
    Cause("R_P_HHH", "Reactor pressure high-high-high", "cstr_pressure_bar", "HIGH", "cstr_pressure_hhh_bar", "bar"),
    Cause("R_L_HH", "Reactor level high-high", "cstr_holdup_kmol", "HIGH", "cstr_level_hh_kmol", "kmol"),
    Cause("R_L_LL", "Reactor level low-low", "cstr_holdup_kmol", "LOW", "cstr_level_ll_kmol", "kmol"),
    Cause("RB_L_HH", "Reboiler level high-high", "reboiler_holdup_kmol", "HIGH", "reboiler_level_hh_kmol", "kmol"),
    Cause("RB_L_LL", "Reboiler level low-low", "reboiler_holdup_kmol", "LOW", "reboiler_level_ll_kmol", "kmol"),
    Cause("DR_L_HH", "Reflux drum level high-high", "drum_holdup_kmol", "HIGH", "drum_level_hh_kmol", "kmol"),
    Cause("DR_L_LL", "Reflux drum level low-low", "drum_holdup_kmol", "LOW", "drum_level_ll_kmol", "kmol"),
    Cause("COL_P_HH", "Column pressure high-high", "column_pressure_bar", "HIGH", "column_pressure_hh_bar", "bar"),
    Cause("COL_L_LL", "Column tray liquid low-low (dry tray)", "column_min_tray_holdup_kmol", "LOW",
          "column_tray_holdup_ll_kmol", "kmol"),
)

# Final elements: (column heading, PlantInputs field, what it is on the P&ID)
EFFECTS = (
    ("Fresh feed", "fresh_ipa_feed", "P-101A/B, FV-101"),
    ("Reactor heating", "cstr_heater_factor", "TV-101 (jacket steam)"),
    ("Emergency cooling", "cstr_emergency_cooling_factor", "HV-102 (E-102 cooling water)"),
    ("H₂ vent", "cstr_vent_valve", "PCV-101"),
    ("Blowdown", "cstr_bdv_valve", "BDV-101"),
    ("Reactor outlet / column feed", "cstr_outflow", "P-102A/B, FV-102"),
    ("Reflux", "reflux_flow", "P-201A/B, FV-201"),
    ("Distillate", "distillate_flow", "P-202A/B, FV-202"),
    ("Bottoms", "bottoms_flow", "P-203A/B, FV-203"),
    ("Reboiler steam", "steam_factor", "TV-202"),
    ("Condenser cooling water", "condenser_cooling_valve", "PV-201"),
)


def _effect(before: float, after: float, nominal: float) -> str:
    if abs(after - before) < 1e-12:
        return ""
    if after <= 1e-12:
        return "STOP" if before > 1.0 + 1e-9 or nominal > 1.0 + 1e-9 else "CLOSE"
    if after >= 1.0 - 1e-12 and before < 1.0 and nominal <= 1.0 + 1e-9:
        return "OPEN"
    return f"≤{100 * after / nominal:.0f} %"


@lru_cache(maxsize=1)
def matrix() -> tuple[dict, ...]:
    """One row per SIF cause: limit, reset, plant-trip latch, and the effect on each final element."""
    cfg = SafetyConfig()
    plant = IntegratedPlantDAE()
    nominal: PlantInputs = plant.default_inputs()
    meas = plant.measurements(plant.nominal_state(nominal))
    rows = []
    for c in CAUSES:
        sis = SafetySystem(cfg)
        sis.latched[c.sif] = True
        result = sis.apply(meas, nominal, nominal)
        assert result.active_causes == (), "the design point must not trip any SIF"
        effects = {name: _effect(getattr(nominal, f), getattr(result.commanded, f), getattr(nominal, f))
                   for name, f, _ in EFFECTS}
        if c.unit == "K":
            reset = f"< {getattr(cfg, c.limit_attr) - cfg.reset_band_temperature_K:g} K"
        else:
            k = 1 - cfg.reset_hysteresis_fraction if c.direction == "HIGH" else 1 + cfg.reset_hysteresis_fraction
            reset = f"{'<' if c.direction == 'HIGH' else '>'} {getattr(cfg, c.limit_attr) * k:.3g} {c.unit}"
        rows.append({"sif": c.sif, "description": c.description, "measurement": c.measurement,
                     "trip": f"{'≥' if c.direction == 'HIGH' else '≤'} {getattr(cfg, c.limit_attr):g} {c.unit}",
                     "reset": reset, "plant_trip": result.plant_trip_latched, "effects": effects})
    return tuple(rows)


# Control loops on the P&ID: simulator loop id -> (ISA controller tag, final element, what it controls)
LOOP_TAGS = {
    "R_TIC": ("TIC-101", "TV-101", "Reactor temperature, by the jacket heating"),
    "R_PIC": ("PIC-101", "PCV-101", "Reactor pressure, by the H₂ vent"),
    "R_LIC": ("LIC-101", "FV-102", "Reactor level, by the reactor outlet (column feed)"),
    "TOP_TIC": ("TIC-201", "FV-201", "Top-tray temperature, by the reflux"),
    "COL_PIC": ("PIC-201", "PV-201", "Column pressure, by the condenser cooling water"),
    "DR_LIC": ("LIC-202", "FV-202", "Reflux drum level, by the distillate"),
    "BOT_TIC": ("TIC-202", "TV-202", "Bottom temperature, by the reboiler steam"),
    "RB_LIC": ("LIC-203", "FV-203", "Reboiler level, by the bottoms"),
}


def loops_markdown() -> str:
    from .control import RegulatoryControlSystem
    specs = RegulatoryControlSystem.LOOP_SPECS
    assert set(specs) == set(LOOP_TAGS), "every control loop needs a P&ID tag"
    lines = ["| Controller | Loop id | Controls | Measured | Final element | Setpoint range |",
             "|---|---|---|---|---|---:|"]
    for loop, (tag, element, what) in LOOP_TAGS.items():
        s = specs[loop]
        lines.append(f"| {tag} | `{loop}` | {what} | `{s.pv_tag}` | {element} (`{s.mv_field}`) | "
                     f"{s.sp_min:g}–{s.sp_max:g} {s.sp_unit} |")
    return "\n".join(lines)


def alarms_markdown() -> str:
    from .alarms import default_alarm_definitions
    lines = ["| Alarm | Description | Variable | Limit | Priority |", "|---|---|---|---:|:-:|"]
    for d in default_alarm_definitions():
        limit = ("trip" if d.unit == "bool" else f"{'≥' if d.direction.upper() == 'HIGH' else '≤'} {d.limit:g} {d.unit}")
        lines.append(f"| `{d.alarm_id}` | {d.description} | `{d.tag}` | {limit} | {d.priority} |")
    return "\n".join(lines)


def markdown() -> str:
    names = [n for n, _, _ in EFFECTS]
    head = "| SIF | Cause | Trips at | Resets when | " + " | ".join(names) + " | Plant trip |"
    sep = "|---|---|---:|---:|" + "|".join([":-:"] * len(names)) + "|:-:|"
    lines = [head, sep]
    for r in matrix():
        cells = [r["effects"][n] or "·" for n in names]
        lines.append(f"| `{r['sif']}` | {r['description']} | {r['trip']} | {r['reset']} | " + " | ".join(cells)
                     + f" | {'**LATCH**' if r['plant_trip'] else '·'} |")
    return "\n".join(lines)


def document() -> str:
    """docs/pid.md: the P&ID and every table on it, from the simulator's code."""
    elements = "\n".join(f"| {name} | {tag} | `{field}` |" for name, field, tag in EFFECTS)
    return f"""# P&ID and cause & effect

The plant as an engineer would draw it: equipment, control loops, alarms and the safety instrumented functions
(SIFs) with their cause & effect matrix. Everything here comes from the simulator (`operaitor/plant`); the tables
are generated by `python -m operaitor.plant.cause_effect` and `tests/test_cause_effect.py` fails if they no longer
match the code. The process is modelled on isopropanol → acetone + H₂ (see the
[model scope](plant-model.md#model-scope-and-known-simplifications-beta)).

![P&ID of the OperAItor plant](../frontend/public/static/pid.svg)

The console shows it live: **Plant → P&ID** (controller values, latched SIFs, this matrix).

## Equipment

| Tag | Equipment |
|---|---|
| R-101 | Reactor: CSTR, 6 m³, jacket heated (TV-101), agitator M-101, emergency cooling coil E-102 (HV-102) |
| T-201 | Distillation column: 10 equilibrium trays, feed on tray 6 |
| E-201 | Condenser (cooling water, PV-201) |
| V-201 | Reflux drum |
| E-202 | Kettle reboiler (steam, TV-202) |
| P-101A/B | Fresh IPA feed pumps (duty / standby) |
| P-102A/B | Reactor outlet / column feed pumps |
| P-201A/B | Reflux pumps |
| P-202A/B | Distillate pumps |
| P-203A/B | Bottoms pumps |
| PCV-101 | Reactor H₂ vent to the flare / fuel-gas header (pressure control) |
| BDV-101 | Reactor blowdown valve (SIF and operator) |
| PSV-101 | Reactor relief valve: starts to open at 4.50 bar, fully open at 4.70 bar (mechanical, no SIF) |

Every pump train alarms when its running pump fails; the operator or the agents start the standby pump. The fresh
feed (FV-101) is the throughput target set by the operator or the agents (FIC-101), not a PI loop.

## Control loops (BPCS)

{loops_markdown()}

## Alarms (ISA-18.2)

Priority 1 is the most urgent. Alarms warn the operator before a SIF acts: each high-high / low-low alarm sits inside
the matching SIF limit.

{alarms_markdown()}

## Safety instrumented functions: cause & effect

Each SIF reads the process independently of the BPCS, latches when its limit is crossed, and overrides the commands
to the final elements below (a final element that has failed can still defeat the override). A latched SIF resets
only on request (operator, agents, or the shift operator in the benchmark) once its variable is back past the reset
value. **LATCH**: the SIF also latches the plant trip, which stops the episode as a safe trip.

Final elements:

| Column | On the P&ID | Simulator input |
|---|---|---|
{elements}

STOP / CLOSE: driven to zero. OPEN: driven fully open. ≤40 %: limited to 40 % of the design value. ·: not changed.

{markdown()}
"""


if __name__ == "__main__":
    import sys
    from pathlib import Path
    if sys.argv[1:] == ["--write"]:
        out = Path(__file__).resolve().parents[2] / "docs" / "pid.md"
        out.write_text(document(), encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(markdown())
