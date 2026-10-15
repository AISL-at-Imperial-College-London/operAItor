from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import numpy as np

from .plant import IntegratedPlantDAE, PlantInputs
from .safety import SafetyConfig, SafetyResult


class EpisodeStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    RECOVERED = "RECOVERED"
    SAFE_TRIP = "SAFE_TRIP"
    DAMAGE = "DAMAGE"
    MODEL_LIMIT = "MODEL_LIMIT"
    TIMEOUT = "TIMEOUT"
    NUMERICAL_FAILURE = "NUMERICAL_FAILURE"


@dataclass(frozen=True)
class OutcomeConfig:
    recovery_dwell_min: float = 15.0
    safe_trip_dwell_min: float = 15.0
    reactor_damage_pressure_bar: float = 5.0
    reactor_overpressure_exposure_min: float = 2.0
    column_damage_pressure_bar: float = 1.8
    column_overpressure_exposure_min: float = 2.0
    reboiler_dry_holdup_kmol: float = 5.0
    reboiler_dry_heat_exposure_min: float = 5.0
    cstr_overflow_holdup_kmol: float = 79.0
    min_valid_tray_holdup_kmol: float = 0.10


@dataclass
class OutcomeState:
    status: EpisodeStatus = EpisodeStatus.RUNNING
    reason: str = ""
    terminal_time_h: float | None = None
    damage_exposure: dict[str, float] | None = None

    @property
    def terminated(self) -> bool:
        return self.status != EpisodeStatus.RUNNING


class OutcomeEvaluator:
    """Low-order episode outcome/termination logic for the prototype."""

    def __init__(self, plant: IntegratedPlantDAE, nominal_measurements: dict[str, float],
                 config: OutcomeConfig = OutcomeConfig()):
        self.plant = plant
        self.nominal = dict(nominal_measurements)
        self.cfg = config
        self.reset()

    def reset(self) -> None:
        self.state = OutcomeState(damage_exposure={
            "reactor_overpressure_h": 0.0,
            "column_overpressure_h": 0.0,
            "reboiler_dry_heat_h": 0.0,
        })
        self._recovery_dwell_h = 0.0
        self._safe_trip_dwell_h = 0.0
        self._abnormal_seen = False

    def mark_abnormal(self) -> None:
        self._abnormal_seen = True

    def numerical_failure(self, t_h: float, reason: str) -> OutcomeState:
        self.state.status = EpisodeStatus.NUMERICAL_FAILURE
        self.state.reason = reason
        self.state.terminal_time_h = float(t_h)
        return self.state

    def _model_limit(self, y: np.ndarray) -> str | None:
        _, ycol = self.plant.split_state(y)
        for i in range(self.plant.column.cfg.n_trays):
            M = float(self.plant.column.tray(ycol, i)[self.plant.column.TRAY_M])
            if M < self.cfg.min_valid_tray_holdup_kmol:
                return f"tray_{i+1:02d}_holdup_below_model_limit"
        return None

    def _safe_envelope(self, m: dict[str, float], s: SafetyConfig = SafetyConfig()) -> bool:
        # Inside every SIF trip threshold; this is not a quality spec.
        return (
            s.cstr_level_ll_kmol < m["cstr_holdup_kmol"] < s.cstr_level_hh_kmol
            and m["cstr_pressure_bar"] < s.cstr_pressure_hh_bar
            and m["cstr_temperature_K"] < s.cstr_temperature_hh_K
            and s.reboiler_level_ll_kmol < m["reboiler_holdup_kmol"] < s.reboiler_level_hh_kmol
            and s.drum_level_ll_kmol < m["drum_holdup_kmol"] < s.drum_level_hh_kmol
            and m["column_pressure_bar"] < s.column_pressure_hh_bar
            and m.get("column_min_tray_holdup_kmol", float("inf")) > s.column_tray_holdup_ll_kmol
        )

    def _near_nominal(self, m: dict[str, float]) -> bool:
        n = self.nominal
        return (
            abs(m["cstr_holdup_kmol"] - n["cstr_holdup_kmol"]) <= 0.08 * n["cstr_holdup_kmol"]
            and abs(m["cstr_temperature_K"] - n["cstr_temperature_K"]) <= 3.0
            and abs(m["cstr_pressure_bar"] - n["cstr_pressure_bar"]) <= 0.25
            and abs(m["reboiler_holdup_kmol"] - n["reboiler_holdup_kmol"]) <= 0.12 * n["reboiler_holdup_kmol"]
            and abs(m["drum_holdup_kmol"] - n["drum_holdup_kmol"]) <= 0.12 * n["drum_holdup_kmol"]
            and abs(m["column_pressure_bar"] - n["column_pressure_bar"]) <= 0.08
        )

    def update(self, t_h: float, dt_h: float, y: np.ndarray, m: dict[str, float],
               actual: PlantInputs, safety: SafetyResult,
               external_abnormal_active: bool, horizon_h: float,
               allow_recovery_termination: bool = True,
               terminate_on_plant_trip: bool = False,
               horizon_is_completion: bool = False) -> OutcomeState:
        if self.state.terminated:
            return self.state

        if external_abnormal_active or safety.latched_causes:
            self._abnormal_seen = True

        limit = self._model_limit(y)
        if limit is not None:
            self.state.status = EpisodeStatus.MODEL_LIMIT
            self.state.reason = limit
            self.state.terminal_time_h = float(t_h)
            return self.state

        exp = self.state.damage_exposure
        assert exp is not None
        if m["cstr_pressure_bar"] >= self.cfg.reactor_damage_pressure_bar:
            exp["reactor_overpressure_h"] += dt_h
        else:
            exp["reactor_overpressure_h"] = max(0.0, exp["reactor_overpressure_h"] - dt_h)

        if m["column_pressure_bar"] >= self.cfg.column_damage_pressure_bar:
            exp["column_overpressure_h"] += dt_h
        else:
            exp["column_overpressure_h"] = max(0.0, exp["column_overpressure_h"] - dt_h)

        # steam on: valve above 3 % open (5 % of the former 0-1.8 duty factor, i.e. the same duty)
        if m["reboiler_holdup_kmol"] <= self.cfg.reboiler_dry_holdup_kmol and actual.steam_factor > 0.03:
            exp["reboiler_dry_heat_h"] += dt_h
        else:
            exp["reboiler_dry_heat_h"] = max(0.0, exp["reboiler_dry_heat_h"] - dt_h)

        damage_reason = None
        if exp["reactor_overpressure_h"] >= self.cfg.reactor_overpressure_exposure_min / 60.0:
            damage_reason = "reactor_overpressure_exposure"
        elif exp["column_overpressure_h"] >= self.cfg.column_overpressure_exposure_min / 60.0:
            damage_reason = "column_overpressure_exposure"
        elif exp["reboiler_dry_heat_h"] >= self.cfg.reboiler_dry_heat_exposure_min / 60.0:
            damage_reason = "reboiler_dry_heating_exposure"
        elif m["cstr_holdup_kmol"] >= self.cfg.cstr_overflow_holdup_kmol:
            damage_reason = "cstr_liquid_overflow"

        if damage_reason is not None:
            self.state.status = EpisodeStatus.DAMAGE
            self.state.reason = damage_reason
            self.state.terminal_time_h = float(t_h)
            return self.state

        if terminate_on_plant_trip and safety.plant_trip_latched:
            self.state.status = EpisodeStatus.SAFE_TRIP
            self.state.reason = "plant_trip_sif_latched"
            self.state.terminal_time_h = float(t_h)
            return self.state

        if safety.plant_trip_latched and self._safe_envelope(m):
            self._safe_trip_dwell_h += dt_h
        else:
            self._safe_trip_dwell_h = 0.0
        if self._safe_trip_dwell_h >= self.cfg.safe_trip_dwell_min / 60.0:
            self.state.status = EpisodeStatus.SAFE_TRIP
            self.state.reason = "latched_trip_stable_in_safe_envelope"
            self.state.terminal_time_h = float(t_h)
            return self.state

        if allow_recovery_termination and self._abnormal_seen and not external_abnormal_active and not safety.latched_causes and self._near_nominal(m):
            self._recovery_dwell_h += dt_h
        else:
            self._recovery_dwell_h = 0.0
        if self._recovery_dwell_h >= self.cfg.recovery_dwell_min / 60.0:
            self.state.status = EpisodeStatus.RECOVERED
            self.state.reason = "returned_to_nominal_envelope"
            self.state.terminal_time_h = float(t_h)
            return self.state

        if t_h >= horizon_h - 1e-12:
            self.state.status = EpisodeStatus.COMPLETED if horizon_is_completion else EpisodeStatus.TIMEOUT
            self.state.reason = "episode_horizon_completed" if horizon_is_completion else "episode_horizon_reached"
            self.state.terminal_time_h = float(t_h)
        return self.state
