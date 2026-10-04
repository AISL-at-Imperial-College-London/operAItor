from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Protocol

from .plant import PlantInputs


@dataclass(frozen=True)
class SupervisoryAction:
    """Restricted operator/agent action request.

    These fields represent actions an operator could plausibly request from the
    control system.  Fault/environment variables are intentionally not exposed.

    `input_overrides` is retained only for backwards-compatible scripted tests;
    it should not be exposed to an autonomous agent.
    """

    throughput_target: float | None = None
    setpoints: dict[str, float] = field(default_factory=dict)
    modes: dict[str, str] = field(default_factory=dict)
    manual_outputs: dict[str, float] = field(default_factory=dict)
    auxiliary_commands: dict[str, float] = field(default_factory=dict)
    start_spares: tuple[str, ...] = ()
    select_primaries: tuple[str, ...] = ()
    acknowledge_alarm_ids: tuple[str, ...] = ()
    acknowledge_all_alarms: bool = False
    reset_sifs: bool = False
    note: str = ""

    # Legacy/master-only compatibility hook.  Do not include this in an agent
    # tool schema because it can bypass the intended operator authority model.
    input_overrides: dict[str, float] = field(default_factory=dict)

    @classmethod
    def no_action(cls) -> "SupervisoryAction":
        return cls()

    def apply_legacy_overrides(self, requested: PlantInputs) -> PlantInputs:
        u = requested
        for key, value in self.input_overrides.items():
            if not hasattr(u, key):
                raise AttributeError(f"Unknown PlantInputs field '{key}'")
            u = replace(u, **{key: float(value)})
        return u


@dataclass(frozen=True)
class ActionResult:
    accepted: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.rejected

    @property
    def summary(self) -> str:
        parts: list[str] = []
        if self.accepted:
            parts.append("accepted: " + "; ".join(self.accepted))
        if self.rejected:
            parts.append("rejected: " + "; ".join(self.rejected))
        return " | ".join(parts) if parts else "no action"


class Actor(Protocol):
    def act(self, observation: dict[str, object]) -> SupervisoryAction: ...


class PassiveActor:
    """Placeholder actor: deliberately takes no supervisory action."""

    def act(self, observation: dict[str, object]) -> SupervisoryAction:
        del observation
        return SupervisoryAction.no_action()
