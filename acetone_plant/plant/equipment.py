from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from .plant import PlantInputs


@dataclass(frozen=True)
class PumpTrainDefinition:
    train_id: str
    flow_field: str
    primary_id: str
    spare_id: str
    description: str


PUMP_TRAINS: dict[str, PumpTrainDefinition] = {
    "FEED": PumpTrainDefinition("FEED", "fresh_ipa_feed", "P-101A", "P-101B", "Fresh IPA feed pump"),
    "CSTR_OUTLET": PumpTrainDefinition("CSTR_OUTLET", "cstr_outflow", "P-102A", "P-102B", "CSTR outlet / column feed pump"),
    "REFLUX": PumpTrainDefinition("REFLUX", "reflux_flow", "P-201A", "P-201B", "Reflux pump"),
    "DISTILLATE": PumpTrainDefinition("DISTILLATE", "distillate_flow", "P-202A", "P-202B", "Distillate product pump"),
    "BOTTOMS": PumpTrainDefinition("BOTTOMS", "bottoms_flow", "P-203A", "P-203B", "Bottoms product pump"),
}

PUMP_IDS = tuple(
    pump_id
    for train in PUMP_TRAINS.values()
    for pump_id in (train.primary_id, train.spare_id)
)


@dataclass(frozen=True)
class PumpFault:
    pump_id: str
    start_h: float
    end_h: float | None = None
    name: str = "pump_trip"

    def active(self, t_h: float) -> bool:
        return t_h >= self.start_h and (self.end_h is None or t_h < self.end_h)


class PumpFaultEngine:
    """Simple equipment-failure layer for duty/standby centrifugal pumps.

    A failed selected pump transmits zero flow.  The BPCS/controller request is
    otherwise left untouched, so starting the healthy spare immediately restores
    the requested flow without changing the controller state.
    """

    def __init__(self, faults: Sequence[PumpFault] | None = None):
        self.faults = list(faults or [])

    def reset(self) -> None:
        # Fault schedules are immutable; no internal dynamic state yet.
        return None

    def active_faults(self, t_h: float) -> list[PumpFault]:
        return [f for f in self.faults if f.active(t_h)]

    def failed_pumps(self, t_h: float) -> set[str]:
        return {f.pump_id for f in self.active_faults(t_h)}

    def is_failed(self, pump_id: str, t_h: float) -> bool:
        return pump_id in self.failed_pumps(t_h)


class PumpSystem:
    """Duty/standby pump selection for the principal liquid-flow services."""

    def __init__(self):
        self.active_pump: dict[str, str] = {}
        self.reset()

    def reset(self) -> None:
        self.active_pump = {tid: d.primary_id for tid, d in PUMP_TRAINS.items()}

    def start_spare(self, train_id: str, faults: PumpFaultEngine, t_h: float) -> tuple[bool, str]:
        if train_id not in PUMP_TRAINS:
            return False, f"Unknown pump train '{train_id}'"
        d = PUMP_TRAINS[train_id]
        if faults.is_failed(d.spare_id, t_h):
            return False, f"Standby pump {d.spare_id} is failed/unavailable"
        self.active_pump[train_id] = d.spare_id
        return True, f"{d.spare_id} started and selected for {train_id}"

    def select_primary(self, train_id: str, faults: PumpFaultEngine, t_h: float) -> tuple[bool, str]:
        if train_id not in PUMP_TRAINS:
            return False, f"Unknown pump train '{train_id}'"
        d = PUMP_TRAINS[train_id]
        if faults.is_failed(d.primary_id, t_h):
            return False, f"Primary pump {d.primary_id} is failed/unavailable"
        self.active_pump[train_id] = d.primary_id
        return True, f"{d.primary_id} selected for {train_id}"

    def apply(self, t_h: float, requested: PlantInputs, faults: PumpFaultEngine) -> PlantInputs:
        actual = requested
        failed = faults.failed_pumps(t_h)
        for train_id, d in PUMP_TRAINS.items():
            selected = self.active_pump[train_id]
            if selected in failed:
                actual = replace(actual, **{d.flow_field: 0.0})
        return actual

    def snapshot(self, t_h: float, faults: PumpFaultEngine) -> dict[str, dict[str, object]]:
        failed = faults.failed_pumps(t_h)
        out: dict[str, dict[str, object]] = {}
        for tid, d in PUMP_TRAINS.items():
            selected = self.active_pump[tid]
            out[tid] = {
                "description": d.description,
                "primary": d.primary_id,
                "spare": d.spare_id,
                "active_pump": selected,
                "primary_failed": d.primary_id in failed,
                "spare_failed": d.spare_id in failed,
                "selected_failed": selected in failed,
                "flow_field": d.flow_field,
            }
        return out
