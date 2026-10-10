from __future__ import annotations

from dataclasses import dataclass, replace

from .plant import PlantInputs


@dataclass(frozen=True)
class RuntimeInputEvent:
    name: str
    kind: str
    target: str
    start_h: float
    end_h: float | None
    mode: str  # multiply, set, add
    value: float

    def active(self, t_h: float) -> bool:
        return t_h >= self.start_h and (self.end_h is None or t_h < self.end_h)


class RuntimeInputEngine:
    """Interactive process/disturbance injections applied during an episode."""

    def __init__(self):
        self.events: list[RuntimeInputEvent] = []

    def reset(self) -> None:
        self.events.clear()

    def add(self, event: RuntimeInputEvent) -> None:
        if not hasattr(PlantInputs(), event.target):
            raise AttributeError(f"Unknown PlantInputs field '{event.target}'")
        self.events.append(event)

    def active_events(self, t_h: float) -> list[RuntimeInputEvent]:
        return [e for e in self.events if e.active(t_h)]

    def clear_active(self, t_h: float) -> None:
        # Keep historical events for traceability but terminate currently active ones.
        new_events: list[RuntimeInputEvent] = []
        for e in self.events:
            if e.active(t_h):
                new_events.append(replace(e, end_h=float(t_h)))
            else:
                new_events.append(e)
        self.events = new_events

    def apply(self, t_h: float, inputs: PlantInputs) -> PlantInputs:
        u = inputs
        for e in self.events:
            if not e.active(t_h):
                continue
            if not hasattr(u, e.target):
                raise AttributeError(f"Unknown PlantInputs field '{e.target}'")
            old = float(getattr(u, e.target))
            if e.mode == "multiply":
                new = old * float(e.value)
            elif e.mode == "set":
                new = float(e.value)
            elif e.mode == "add":
                new = old + float(e.value)
            else:
                raise ValueError(f"Unsupported runtime event mode '{e.mode}'")
            u = replace(u, **{e.target: new})
        return u
