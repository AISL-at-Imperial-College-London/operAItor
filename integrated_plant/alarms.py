from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .safety import SafetyConfig


class AlarmState(str, Enum):
    NORMAL = "NORMAL"
    ACTIVE_UNACK = "ACTIVE_UNACK"
    ACTIVE_ACK = "ACTIVE_ACK"
    RTN_UNACK = "RTN_UNACK"


@dataclass(frozen=True)
class AlarmDefinition:
    alarm_id: str
    tag: str
    description: str
    direction: str  # HIGH or LOW
    limit: float
    priority: int = 2  # 1 highest
    hysteresis: float = 0.0
    unit: str = ""


@dataclass
class AlarmStatus:
    definition: AlarmDefinition
    state: AlarmState = AlarmState.NORMAL
    value: float = float("nan")
    first_active_min: float | None = None
    last_change_min: float = 0.0

    @property
    def active(self) -> bool:
        return self.state in (AlarmState.ACTIVE_UNACK, AlarmState.ACTIVE_ACK)

    @property
    def unacknowledged(self) -> bool:
        return self.state in (AlarmState.ACTIVE_UNACK, AlarmState.RTN_UNACK)


@dataclass(frozen=True)
class AlarmEvent:
    time_min: float
    alarm_id: str
    transition: str
    state: AlarmState
    priority: int
    value: float
    limit: float
    description: str
    by: str = ""  # who acknowledged (operator / action-agent); empty for process transitions


class AlarmManager:
    """Simple ISA-18.2-like alarm state machine for the operator layer.

    Alarm logic acts on BPCS measurements, not on independent SIF measurements.
    SIF latches are therefore intentionally separate from alarm acknowledgement.
    """

    def __init__(self, definitions: Iterable[AlarmDefinition]):
        self.definitions = tuple(definitions)
        self.status: dict[str, AlarmStatus] = {
            d.alarm_id: AlarmStatus(d) for d in self.definitions
        }
        self.history: list[AlarmEvent] = []

    def reset(self) -> None:
        self.status = {d.alarm_id: AlarmStatus(d) for d in self.definitions}
        self.history = []

    @staticmethod
    def _is_active(d: AlarmDefinition, value: float, prior_active: bool) -> bool:
        direction = d.direction.upper()
        if direction == "HIGH":
            clear_limit = d.limit - abs(d.hysteresis)
            return value >= (clear_limit if prior_active else d.limit)
        if direction == "LOW":
            clear_limit = d.limit + abs(d.hysteresis)
            return value <= (clear_limit if prior_active else d.limit)
        raise ValueError(f"Unsupported alarm direction '{d.direction}'")

    def _event(self, time_min: float, s: AlarmStatus, transition: str, by: str = "") -> None:
        self.history.append(AlarmEvent(
            time_min=float(time_min),
            alarm_id=s.definition.alarm_id,
            transition=transition,
            state=s.state,
            priority=s.definition.priority,
            value=float(s.value),
            limit=float(s.definition.limit),
            description=s.definition.description,
            by=by,
        ))

    def update(self, time_min: float, measurements: dict[str, float]) -> None:
        for alarm_id, s in self.status.items():
            d = s.definition
            if d.tag not in measurements:
                continue
            s.value = float(measurements[d.tag])
            condition = self._is_active(d, s.value, s.active)

            if condition:
                if s.state == AlarmState.NORMAL:
                    s.state = AlarmState.ACTIVE_UNACK
                    s.first_active_min = float(time_min)
                    s.last_change_min = float(time_min)
                    self._event(time_min, s, "ACTIVATED")
                elif s.state == AlarmState.RTN_UNACK:
                    # Alarm re-activates before acknowledgement.
                    s.state = AlarmState.ACTIVE_UNACK
                    s.last_change_min = float(time_min)
                    self._event(time_min, s, "REACTIVATED")
            else:
                if s.state == AlarmState.ACTIVE_UNACK:
                    s.state = AlarmState.RTN_UNACK
                    s.last_change_min = float(time_min)
                    self._event(time_min, s, "RETURNED")
                elif s.state == AlarmState.ACTIVE_ACK:
                    s.state = AlarmState.NORMAL
                    s.last_change_min = float(time_min)
                    self._event(time_min, s, "CLEARED")
                    s.first_active_min = None

    def acknowledge(self, alarm_id: str, time_min: float, by: str = "operator") -> None:
        s = self.status.get(alarm_id)
        if s is None:
            raise KeyError(alarm_id)
        if s.state == AlarmState.ACTIVE_UNACK:
            s.state = AlarmState.ACTIVE_ACK
            s.last_change_min = float(time_min)
            self._event(time_min, s, "ACKNOWLEDGED", by)
        elif s.state == AlarmState.RTN_UNACK:
            s.state = AlarmState.NORMAL
            s.last_change_min = float(time_min)
            self._event(time_min, s, "ACKNOWLEDGED_AND_CLEARED", by)
            s.first_active_min = None

    def acknowledge_all(self, time_min: float, by: str = "operator") -> None:
        for alarm_id in list(self.status):
            if self.status[alarm_id].unacknowledged:
                self.acknowledge(alarm_id, time_min, by)

    def active(self) -> list[AlarmStatus]:
        return sorted(
            [s for s in self.status.values() if s.active],
            key=lambda s: (s.definition.priority, s.first_active_min or 0.0),
        )

    def unacknowledged(self) -> list[AlarmStatus]:
        return sorted(
            [s for s in self.status.values() if s.unacknowledged],
            key=lambda s: (s.definition.priority, s.last_change_min),
        )

    def snapshot(self) -> dict[str, str]:
        return {alarm_id: s.state.value for alarm_id, s in self.status.items()}


def default_alarm_definitions(safety: SafetyConfig = SafetyConfig()) -> tuple[AlarmDefinition, ...]:
    """Initial benchmark alarms, intentionally inside the SIF limits.

    These are simulator/operator-training thresholds, not engineered plant alarm
    setpoints. They are chosen to provide an operator response window before an
    independent SIF acts.
    """
    return (
        AlarmDefinition("R_P_H", "cstr_pressure_bar", "Reactor pressure high", "HIGH", 3.50, 2, 0.05, "bar"),
        AlarmDefinition("R_P_HH", "cstr_pressure_bar", "Reactor pressure high-high", "HIGH", 3.80, 1, 0.05, "bar"),
        AlarmDefinition("R_T_H", "cstr_temperature_K", "Reactor temperature high", "HIGH", 342.0, 2, 0.5, "K"),
        AlarmDefinition("R_T_HH", "cstr_temperature_K", "Reactor temperature high-high", "HIGH", 346.0, 1, 0.5, "K"),
        AlarmDefinition("R_L_H", "cstr_holdup_kmol", "Reactor level high", "HIGH", 62.0, 2, 1.0, "kmol"),
        AlarmDefinition("R_L_HH", "cstr_holdup_kmol", "Reactor level high-high", "HIGH", 68.0, 1, 1.0, "kmol"),
        AlarmDefinition("R_L_L", "cstr_holdup_kmol", "Reactor level low", "LOW", 38.0, 2, 1.0, "kmol"),
        AlarmDefinition("R_L_LL", "cstr_holdup_kmol", "Reactor level low-low", "LOW", 25.0, 1, 1.0, "kmol"),
        AlarmDefinition("RB_L_HH", "reboiler_holdup_kmol", "Reboiler level high-high", "HIGH", 48.0, 1, 1.0, "kmol"),
        AlarmDefinition("RB_L_LL", "reboiler_holdup_kmol", "Reboiler level low-low", "LOW", 16.0, 1, 1.0, "kmol"),
        AlarmDefinition("DR_L_HH", "drum_holdup_kmol", "Reflux drum level high-high", "HIGH", 42.0, 1, 1.0, "kmol"),
        AlarmDefinition("DR_L_LL", "drum_holdup_kmol", "Reflux drum level low-low", "LOW", 14.0, 1, 1.0, "kmol"),
        AlarmDefinition("COL_P_H", "column_pressure_bar", "Column pressure high", "HIGH", 1.15, 2, 0.02, "bar"),
        AlarmDefinition("COL_P_HH", "column_pressure_bar", "Column pressure high-high", "HIGH", 1.28, 1, 0.02, "bar"),
        AlarmDefinition("DIST_X_L", "distillate_x_acetone", "Distillate acetone purity low", "LOW", 0.90, 3, 0.01, "mol frac"),
        AlarmDefinition("BTM_X_H", "bottoms_x_acetone", "Bottoms acetone high", "HIGH", 0.08, 3, 0.01, "mol frac"),
        AlarmDefinition("P101_TRIP", "pump_FEED_failed", "Fresh-feed duty pump trip / no selected pump flow", "HIGH", 0.5, 1, 0.0, "bool"),
        AlarmDefinition("P102_TRIP", "pump_CSTR_OUTLET_failed", "CSTR outlet / column-feed duty pump trip", "HIGH", 0.5, 1, 0.0, "bool"),
        AlarmDefinition("P201_TRIP", "pump_REFLUX_failed", "Reflux duty pump trip", "HIGH", 0.5, 1, 0.0, "bool"),
        AlarmDefinition("P202_TRIP", "pump_DISTILLATE_failed", "Distillate duty pump trip", "HIGH", 0.5, 1, 0.0, "bool"),
        AlarmDefinition("P203_TRIP", "pump_BOTTOMS_failed", "Bottoms duty pump trip", "HIGH", 0.5, 1, 0.0, "bool"),
    )
