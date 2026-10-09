from __future__ import annotations

from dataclasses import dataclass, field, replace

from .plant import PlantInputs


@dataclass(frozen=True)
class SafetyConfig:
    # Synthetic benchmark thresholds, not engineered SIL set points.
    cstr_temperature_hh_K: float = 348.0
    cstr_pressure_hh_bar: float = 4.00
    cstr_pressure_hhh_bar: float = 4.35
    cstr_level_hh_kmol: float = 72.0   # about 90% of the 6 m3 vessel volume
    cstr_level_ll_kmol: float = 20.0   # about 25% vessel fill

    reboiler_level_ll_kmol: float = 12.08
    reboiler_level_hh_kmol: float = 51.76
    drum_level_ll_kmol: float = 10.59
    drum_level_hh_kmol: float = 45.37
    column_pressure_hh_bar: float = 1.35
    # Column low liquid (dry tray): lowest tray holdup. Nominal ~2.24 kmol; the column model is
    # invalid below 0.10 kmol (MODEL_LIMIT), so the SIF trips well before that.
    column_tray_holdup_ll_kmol: float = 0.30

    max_emergency_cooling_factor: float = 1.0
    max_condenser_cooling_valve: float = 1.0
    pressure_escalation_feed_fraction: float = 0.40
    reset_hysteresis_fraction: float = 0.05


@dataclass
class SafetyResult:
    commanded: PlantInputs
    active_causes: tuple[str, ...]
    latched_causes: tuple[str, ...]
    plant_trip_latched: bool
    cause_flags: dict[str, bool] = field(default_factory=dict)


class SafetySystem:
    """Independent deterministic SIF/interrupt layer.

    It receives independent safety measurements (currently noise-free copies of
    physical states), latches trips, and overrides requested commands.  Final
    element faults are applied *after* this layer by the actuator-fault engine.
    """

    CAUSES = (
        "R_T_HH",
        "R_P_HH",
        "R_P_HHH",
        "R_L_HH",
        "R_L_LL",
        "RB_L_LL",
        "RB_L_HH",
        "DR_L_LL",
        "DR_L_HH",
        "COL_P_HH",
        "COL_L_LL",
    )

    def __init__(self, config: SafetyConfig = SafetyConfig()):
        self.cfg = config
        self.latched: dict[str, bool] = {k: False for k in self.CAUSES}
        self.plant_trip_latched = False

    def reset(self) -> None:
        for k in self.latched:
            self.latched[k] = False
        self.plant_trip_latched = False

    def _instantaneous_flags(self, m: dict[str, float]) -> dict[str, bool]:
        c = self.cfg
        return {
            "R_T_HH": m["cstr_temperature_K"] >= c.cstr_temperature_hh_K,
            "R_P_HH": m["cstr_pressure_bar"] >= c.cstr_pressure_hh_bar,
            "R_P_HHH": m["cstr_pressure_bar"] >= c.cstr_pressure_hhh_bar,
            "R_L_HH": m["cstr_holdup_kmol"] >= c.cstr_level_hh_kmol,
            "R_L_LL": m["cstr_holdup_kmol"] <= c.cstr_level_ll_kmol,
            "RB_L_LL": m["reboiler_holdup_kmol"] <= c.reboiler_level_ll_kmol,
            "RB_L_HH": m["reboiler_holdup_kmol"] >= c.reboiler_level_hh_kmol,
            "DR_L_LL": m["drum_holdup_kmol"] <= c.drum_level_ll_kmol,
            "DR_L_HH": m["drum_holdup_kmol"] >= c.drum_level_hh_kmol,
            "COL_P_HH": m["column_pressure_bar"] >= c.column_pressure_hh_bar,
            "COL_L_LL": m.get("column_min_tray_holdup_kmol", float("inf")) <= c.column_tray_holdup_ll_kmol,
        }

    def _safe_to_reset(self, cause: str, m: dict[str, float]) -> bool:
        h = self.cfg.reset_hysteresis_fraction
        c = self.cfg
        if cause == "R_T_HH":
            return m["cstr_temperature_K"] < c.cstr_temperature_hh_K * (1.0 - h)
        if cause == "R_P_HH":
            return m["cstr_pressure_bar"] < c.cstr_pressure_hh_bar * (1.0 - h)
        if cause == "R_P_HHH":
            return m["cstr_pressure_bar"] < c.cstr_pressure_hhh_bar * (1.0 - h)
        if cause == "R_L_HH":
            return m["cstr_holdup_kmol"] < c.cstr_level_hh_kmol * (1.0 - h)
        if cause == "R_L_LL":
            return m["cstr_holdup_kmol"] > c.cstr_level_ll_kmol * (1.0 + h)
        if cause == "RB_L_HH":
            return m["reboiler_holdup_kmol"] < c.reboiler_level_hh_kmol * (1.0 - h)
        if cause == "RB_L_LL":
            return m["reboiler_holdup_kmol"] > c.reboiler_level_ll_kmol * (1.0 + h)
        if cause == "DR_L_HH":
            return m["drum_holdup_kmol"] < c.drum_level_hh_kmol * (1.0 - h)
        if cause == "DR_L_LL":
            return m["drum_holdup_kmol"] > c.drum_level_ll_kmol * (1.0 + h)
        if cause == "COL_P_HH":
            return m["column_pressure_bar"] < c.column_pressure_hh_bar * (1.0 - h)
        if cause == "COL_L_LL":
            return m.get("column_min_tray_holdup_kmol", float("inf")) > c.column_tray_holdup_ll_kmol * (1.0 + h)
        return False

    def apply(
        self,
        measurements: dict[str, float],
        requested: PlantInputs,
        nominal: PlantInputs,
        reset_requested: bool = False,
    ) -> SafetyResult:
        flags = self._instantaneous_flags(measurements)

        if reset_requested:
            for cause in self.CAUSES:
                if self.latched[cause] and self._safe_to_reset(cause, measurements):
                    self.latched[cause] = False
            if not any(self.latched[c] for c in ("R_P_HHH", "RB_L_HH", "DR_L_LL", "DR_L_HH", "COL_P_HH", "COL_L_LL")):
                self.plant_trip_latched = False

        for cause, active in flags.items():
            if active:
                self.latched[cause] = True

        # Severe escalations define a process trip state, but individual SIF
        # activation is not by itself an episode-termination condition.
        if any(self.latched[c] for c in ("R_P_HHH", "RB_L_HH", "DR_L_LL", "DR_L_HH", "COL_P_HH", "COL_L_LL")):
            self.plant_trip_latched = True

        u = requested

        # Reactor T-HH: remove external heat, retain feed, add emergency cooling.
        if self.latched["R_T_HH"]:
            u = replace(
                u,
                cstr_heater_factor=0.0,
                cstr_emergency_cooling_factor=max(u.cstr_emergency_cooling_factor, self.cfg.max_emergency_cooling_factor),
            )

        # First pressure stage: maximize H2 removal, do not cut feed.
        if self.latched["R_P_HH"]:
            u = replace(
                u,
                cstr_heater_factor=0.0,
                cstr_vent_valve=1.0,
                cstr_bdv_valve=1.0,
            )

        # Escalated pressure: preserve the same protection and reduce, rather
        # than immediately isolate, feed because the benchmark reaction is endothermic.
        if self.latched["R_P_HHH"]:
            u = replace(
                u,
                fresh_ipa_feed=min(u.fresh_ipa_feed, self.cfg.pressure_escalation_feed_fraction * nominal.fresh_ipa_feed),
                cstr_heater_factor=0.0,
                cstr_vent_valve=1.0,
                cstr_bdv_valve=1.0,
            )

        if self.latched["R_L_HH"]:
            u = replace(u, fresh_ipa_feed=0.0, cstr_heater_factor=0.0)

        if self.latched["R_L_LL"]:
            u = replace(u, cstr_heater_factor=0.0, cstr_outflow=0.0)

        if self.latched["RB_L_LL"]:
            u = replace(u, steam_factor=0.0, bottoms_flow=0.0)

        if self.latched["RB_L_HH"]:
            u = replace(
                u, steam_factor=0.0, cstr_outflow=0.0,
                fresh_ipa_feed=0.0, cstr_heater_factor=0.0,
            )

        if self.latched["DR_L_LL"]:
            u = replace(
                u, reflux_flow=0.0, distillate_flow=0.0, steam_factor=0.0,
                cstr_outflow=0.0, fresh_ipa_feed=0.0, cstr_heater_factor=0.0,
            )

        if self.latched["DR_L_HH"]:
            u = replace(
                u, steam_factor=0.0, cstr_outflow=0.0,
                fresh_ipa_feed=0.0, cstr_heater_factor=0.0,
            )

        if self.latched["COL_P_HH"]:
            u = replace(
                u,
                steam_factor=0.0,
                condenser_cooling_valve=max(
                    u.condenser_cooling_valve, self.cfg.max_condenser_cooling_valve
                ),
                cstr_outflow=0.0,
                fresh_ipa_feed=0.0,
                cstr_heater_factor=0.0,
            )

        # Column low liquid (dry trays, e.g. loss of reflux): stop boil-up so dry trays do not
        # overheat, and stop the feeds (column shutdown, plant-trip latch).
        if self.latched["COL_L_LL"]:
            u = replace(
                u,
                steam_factor=0.0,
                cstr_outflow=0.0,
                fresh_ipa_feed=0.0,
                cstr_heater_factor=0.0,
            )

        return SafetyResult(
            commanded=u,
            active_causes=tuple(k for k, v in flags.items() if v),
            latched_causes=tuple(k for k, v in self.latched.items() if v),
            plant_trip_latched=bool(self.plant_trip_latched),
            cause_flags=flags,
        )
