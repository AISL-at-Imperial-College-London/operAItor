from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol, Sequence
import math
import numpy as np

from .plant import PlantInputs


class Trajectory(Protocol):
    def value(self, t_h: float) -> float: ...
    def breakpoints(self) -> list[float]: ...


@dataclass(frozen=True)
class Constant:
    c: float
    def value(self, t_h: float) -> float:
        return float(self.c)
    def breakpoints(self) -> list[float]:
        return []


@dataclass(frozen=True)
class PiecewiseConstant:
    """Right-continuous piecewise-constant trajectory."""
    points: Sequence[tuple[float, float]]

    def __post_init__(self):
        if not self.points:
            raise ValueError("PiecewiseConstant requires at least one point")
        times = [p[0] for p in self.points]
        if times != sorted(times):
            raise ValueError("PiecewiseConstant points must be sorted by time")

    def value(self, t_h: float) -> float:
        val = self.points[0][1]
        for t0, v in self.points:
            if t_h + 1e-12 >= t0:
                val = v
            else:
                break
        return float(val)

    def breakpoints(self) -> list[float]:
        return [float(t) for t, _ in self.points[1:]]


@dataclass(frozen=True)
class PiecewiseLinear:
    points: Sequence[tuple[float, float]]

    def __post_init__(self):
        if len(self.points) < 2:
            raise ValueError("PiecewiseLinear requires at least two points")
        times = [p[0] for p in self.points]
        if times != sorted(times):
            raise ValueError("PiecewiseLinear points must be sorted by time")

    def value(self, t_h: float) -> float:
        if t_h <= self.points[0][0]:
            return float(self.points[0][1])
        if t_h >= self.points[-1][0]:
            return float(self.points[-1][1])
        for (t0, v0), (t1, v1) in zip(self.points[:-1], self.points[1:]):
            if t0 <= t_h <= t1:
                f = (t_h - t0) / (t1 - t0)
                return float(v0 + f * (v1 - v0))
        return float(self.points[-1][1])

    def breakpoints(self) -> list[float]:
        return [float(t) for t, _ in self.points[1:-1]]


@dataclass(frozen=True)
class Sine:
    mean: float
    amplitude: float
    period_h: float
    phase_rad: float = 0.0
    def value(self, t_h: float) -> float:
        return float(self.mean + self.amplitude * math.sin(2.0 * math.pi * t_h / self.period_h + self.phase_rad))
    def breakpoints(self) -> list[float]:
        return []


@dataclass(frozen=True)
class ReactionRateFault:
    """Temporary catalyst/reaction-rate loss used by the legacy FDD campaign."""
    name: str
    start_h: float
    final_loss_fraction: float
    ramp_duration_h: float = 10.0 / 60.0
    full_duration_h: float = 10.0 / 60.0

    def __post_init__(self):
        if not (0.0 <= self.final_loss_fraction < 1.0):
            raise ValueError("final_loss_fraction must be in [0,1)")

    @property
    def end_h(self) -> float:
        return self.start_h + self.ramp_duration_h + self.full_duration_h

    def loss_fraction(self, t_h: float) -> float:
        if t_h < self.start_h or t_h >= self.end_h:
            return 0.0
        dt = t_h - self.start_h
        if dt < self.ramp_duration_h - 1e-12:
            return float((self.final_loss_fraction / 3.0) * dt / self.ramp_duration_h)
        return float(self.final_loss_fraction)

    def active(self, t_h: float) -> bool:
        return self.start_h <= t_h < self.end_h

    def breakpoints(self) -> list[float]:
        return [self.start_h, self.start_h + self.ramp_duration_h, self.end_h]


@dataclass(frozen=True)
class CatalystActivityTrajectory:
    faults: Sequence[ReactionRateFault]
    base_activity: float = 1.0

    def value(self, t_h: float) -> float:
        loss = max((f.loss_fraction(t_h) for f in self.faults), default=0.0)
        return float(max(0.0, self.base_activity - loss))

    def breakpoints(self) -> list[float]:
        return sorted({x for f in self.faults for x in f.breakpoints()})

    def active_faults(self, t_h: float) -> list[ReactionRateFault]:
        return [f for f in self.faults if f.active(t_h)]


@dataclass(frozen=True)
class ScenarioEvent:
    name: str
    kind: str
    target: str
    start_h: float
    end_h: float | None
    magnitude: float | None = None
    metadata: str = ""

    def active(self, t_h: float) -> bool:
        return t_h >= self.start_h and (self.end_h is None or t_h < self.end_h)


@dataclass(frozen=True)
class Scenario:
    """External scenario definition; all trajectories use simulated time."""
    duration_h: float
    throughput_fraction: Trajectory = Constant(1.0)
    feed_temperature_K: Trajectory = Constant(298.15)
    cooling_water_temperature_K: Trajectory = Constant(298.15)
    steam_pressure_bar: Trajectory = Constant(5.659806566043441)
    cstr_heating_utility_temperature_K: Trajectory = Constant(390.0)
    ambient_temperature_K: Trajectory = Constant(298.15)
    catalyst_activity: Trajectory = Constant(1.0)
    # Independent condenser utility/equipment effectiveness.  The cooling-water
    # valve itself is now a manipulated variable generated by the column PIC.
    condenser_cooling_effectiveness: Trajectory = Constant(1.0)
    condenser_pressure_bar: Trajectory = Constant(1.0)  # legacy/reference only
    cstr_liquid_leak_kmol_h: Trajectory = Constant(0.0)
    reboiler_liquid_leak_kmol_h: Trajectory = Constant(0.0)
    drum_liquid_leak_kmol_h: Trajectory = Constant(0.0)
    name: str = "scenario"
    events: tuple[ScenarioEvent, ...] = ()

    def evaluate(self, t_h: float, nominal: PlantInputs) -> PlantInputs:
        return replace(
            nominal,
            fresh_ipa_feed=nominal.fresh_ipa_feed * self.throughput_fraction.value(t_h),
            fresh_feed_temperature_K=self.feed_temperature_K.value(t_h),
            cooling_water_temperature_K=self.cooling_water_temperature_K.value(t_h),
            steam_pressure_bar=self.steam_pressure_bar.value(t_h),
            cstr_heating_utility_temperature_K=self.cstr_heating_utility_temperature_K.value(t_h),
            ambient_temperature_K=self.ambient_temperature_K.value(t_h),
            catalyst_activity=self.catalyst_activity.value(t_h),
            condenser_cooling_effectiveness=self.condenser_cooling_effectiveness.value(t_h),
            condenser_pressure_bar=self.condenser_pressure_bar.value(t_h),
            cstr_liquid_leak_kmol_h=self.cstr_liquid_leak_kmol_h.value(t_h),
            reboiler_liquid_leak_kmol_h=self.reboiler_liquid_leak_kmol_h.value(t_h),
            drum_liquid_leak_kmol_h=self.drum_liquid_leak_kmol_h.value(t_h),
        )

    def breakpoints(self) -> list[float]:
        fields = [
            self.throughput_fraction,
            self.feed_temperature_K,
            self.cooling_water_temperature_K,
            self.steam_pressure_bar,
            self.cstr_heating_utility_temperature_K,
            self.ambient_temperature_K,
            self.catalyst_activity,
            self.condenser_cooling_effectiveness,
            self.condenser_pressure_bar,
            self.cstr_liquid_leak_kmol_h,
            self.reboiler_liquid_leak_kmol_h,
            self.drum_liquid_leak_kmol_h,
        ]
        return sorted({x for tr in fields for x in tr.breakpoints() if 0.0 < x < self.duration_h})

    def active_events(self, t_h: float) -> list[ScenarioEvent]:
        return [e for e in self.events if e.active(t_h)]


def nominal_hold_scenario(duration_min: float = 180.0) -> Scenario:
    """Constant 100% production.  Only the measurement layer adds sensor noise."""
    return Scenario(duration_h=float(duration_min) / 60.0, name="nominal_hold")


def _pulse(nominal: float, start_h: float, duration_h: float, multiplier: float) -> PiecewiseConstant:
    return PiecewiseConstant([
        (0.0, nominal),
        (float(start_h), nominal * multiplier),
        (float(start_h + duration_h), nominal),
    ])


# Size of one "normal" disturbance step. Temperatures are stepped in kelvin (additive)
# and pressures as a fraction of nominal. Before 2026-10-05 every variable was scaled by
# +/-10 %, which on absolute temperatures meant +/-30-39 K (cooling water at 55 degC):
# a severe upset rather than normal variability.
DISTURBANCE_STEPS: dict[str, tuple[str, float]] = {
    "feed_temperature_K": ("add", 5.0),
    "fresh_feed_temperature_K": ("add", 5.0),  # same variable, PlantInputs name (console injections)
    "cooling_water_temperature_K": ("add", 3.0),
    "steam_pressure_bar": ("mul", 0.10),
    "cstr_heating_utility_temperature_K": ("add", 5.0),
    "ambient_temperature_K": ("add", 3.0),
}


def disturbance_step(target: str, sign: float) -> tuple[str, float, str]:
    """(runtime mode, value, label) for one normal disturbance step on ``target``."""
    mode, size = DISTURBANCE_STEPS[target]
    if mode == "add":
        return "add", sign * size, f"{'+' if sign > 0 else '-'}{size:g} K"
    return "multiply", 1.0 + sign * size, f"{'+' if sign > 0 else '-'}{100 * size:g}%"


def randomized_step_disturbance_scenario(
    duration_min: float = 180.0,
    seed: int = 20261004,
    step_duration_min: float = 15.0,
    magnitude_fraction: float | None = None,
    earliest_start_min: float = 30.0,
    max_disturbances: int = 5,
) -> Scenario:
    """Random subset of 15-min normal disturbances (sizes in ``DISTURBANCE_STEPS``).

    Each selected variable receives one rectangular pulse.  Start times are
    independent, so overlap is allowed.  The seed fully determines the run.
    ``magnitude_fraction`` restores the old behaviour (scale every variable by
    1 +/- fraction) for reproducing earlier datasets.
    """
    duration_h = float(duration_min) / 60.0
    pulse_h = float(step_duration_min) / 60.0
    rng = np.random.default_rng(int(seed))
    candidates = {
        "feed_temperature_K": 298.15,
        "cooling_water_temperature_K": 298.15,
        "steam_pressure_bar": 5.659806566043441,
        "cstr_heating_utility_temperature_K": 390.0,
        "ambient_temperature_K": 298.15,
    }
    nmax = min(max(1, int(max_disturbances)), len(candidates))
    nsel = int(rng.integers(1, nmax + 1))
    names = [str(x) for x in rng.choice(list(candidates), size=nsel, replace=False)]
    kwargs: dict[str, object] = {}
    events: list[ScenarioEvent] = []
    latest_start_h = max(float(earliest_start_min) / 60.0, duration_h - pulse_h)
    lo = min(float(earliest_start_min) / 60.0, max(0.0, duration_h - pulse_h))
    hi = max(lo, duration_h - pulse_h)
    for target in names:
        nominal = candidates[target]
        start = float(rng.uniform(lo, hi)) if hi > lo else lo
        sign = float(rng.choice([-1.0, 1.0]))
        if magnitude_fraction is not None:  # legacy: relative step on every variable
            mult, magnitude, label = 1.0 + sign * float(magnitude_fraction), sign * float(magnitude_fraction), \
                f"{100 * sign * float(magnitude_fraction):+g}%"
        else:
            mode, value, label = disturbance_step(target, sign)
            mult = (nominal + value) / nominal if mode == "add" else value
            magnitude = value if mode == "add" else value - 1.0
        kwargs[target] = _pulse(nominal, start, pulse_h, mult)
        events.append(ScenarioEvent(
            name=f"disturbance_{target}_{'up' if sign > 0 else 'down'}",
            kind="disturbance",
            target=target,
            start_h=start,
            end_h=start + pulse_h,
            magnitude=magnitude,
            metadata=f"{step_duration_min:g} min step {label}",
        ))
    return Scenario(
        duration_h=duration_h,
        name=f"random_step_disturbances_seed_{seed}",
        events=tuple(sorted(events, key=lambda e: e.start_h)),
        **kwargs,
    )


@dataclass(frozen=True)
class ActuatorFault:
    """Fault between safety-arbitrated request and the physical actuator.

    modes:
      capacity      : upper capacity becomes reference * capacity_fraction
      capacity_ramp : upper capacity degrades linearly from 100% to the
                      specified fraction over ramp_duration_h
      freeze        : freeze at requested value when fault becomes active
      fixed         : force a specified fixed value
      stiction      : hold last actual value until request moves outside a deadband
    """
    target: str
    start_h: float
    end_h: float | None = None
    mode: str = "capacity"
    capacity_fraction: float = 1.0
    capacity_reference_value: float | None = None
    ramp_duration_h: float = 0.0
    fixed_value: float | None = None
    stiction_band_fraction: float = 0.05
    name: str = "actuator_fault"

    def active(self, t_h: float) -> bool:
        return t_h >= self.start_h and (self.end_h is None or t_h < self.end_h)


class ActuatorFaultEngine:
    def __init__(self, faults: Sequence[ActuatorFault] | None = None):
        self.faults = list(faults or [])
        self._frozen_values: dict[int, float] = {}
        self._stiction_values: dict[int, float] = {}

    def reset(self) -> None:
        self._frozen_values.clear()
        self._stiction_values.clear()

    def active_faults(self, t_h: float) -> list[ActuatorFault]:
        return [f for f in self.faults if f.active(t_h)]

    def apply(self, t_h: float, requested: PlantInputs, nominal: PlantInputs) -> PlantInputs:
        actual = requested
        for idx, fault in enumerate(self.faults):
            if not fault.active(t_h):
                self._frozen_values.pop(idx, None)
                self._stiction_values.pop(idx, None)
                continue
            if not hasattr(actual, fault.target):
                raise AttributeError(f"Unknown PlantInputs field '{fault.target}'")
            req = float(getattr(actual, fault.target))
            if fault.mode == "capacity":
                ref = (float(getattr(nominal, fault.target)) if fault.capacity_reference_value is None
                       else float(fault.capacity_reference_value))
                cap = ref * fault.capacity_fraction
                value = min(req, cap)
            elif fault.mode == "capacity_ramp":
                ref = (float(getattr(nominal, fault.target)) if fault.capacity_reference_value is None
                       else float(fault.capacity_reference_value))
                if fault.ramp_duration_h <= 0.0:
                    progress = 1.0
                else:
                    progress = min(max((t_h - fault.start_h) / fault.ramp_duration_h, 0.0), 1.0)
                frac = 1.0 + progress * (fault.capacity_fraction - 1.0)
                value = min(req, ref * frac)
            elif fault.mode == "freeze":
                if idx not in self._frozen_values:
                    self._frozen_values[idx] = req
                value = self._frozen_values[idx]
            elif fault.mode == "fixed":
                if fault.fixed_value is None:
                    raise ValueError("fixed actuator fault requires fixed_value")
                value = float(fault.fixed_value)
            elif fault.mode == "stiction":
                if idx not in self._stiction_values:
                    self._stiction_values[idx] = req
                held = self._stiction_values[idx]
                scale = max(abs(float(getattr(nominal, fault.target))), 1.0)
                band = max(0.0, fault.stiction_band_fraction) * scale
                if abs(req - held) > band:
                    held = req
                    self._stiction_values[idx] = held
                value = held
            else:
                raise ValueError(f"Unsupported actuator fault mode '{fault.mode}'")
            actual = replace(actual, **{fault.target: value})
        return actual


@dataclass(frozen=True)
class CampaignBlock:
    block_id: int
    name: str
    scenario: Scenario
    actuator_faults: ActuatorFaultEngine
    reaction_faults: Sequence[ReactionRateFault]
    production_levels: Sequence[float]
    segment_duration_h: float
    global_offset_h: float

    def labels(self, t_h: float) -> dict[str, object]:
        tt = min(max(t_h, 0.0), self.scenario.duration_h - 1e-12)
        segment = min(int(tt // self.segment_duration_h), len(self.production_levels) - 1)
        active_r = [f for f in self.reaction_faults if f.active(t_h)]
        active_a = self.actuator_faults.active_faults(t_h)
        names = [f.name for f in active_r] + [f.name for f in active_a]
        rloss = max((f.loss_fraction(t_h) for f in active_r), default=0.0)
        return {
            "label.block_id": self.block_id,
            "label.block_name": self.name,
            "label.production_segment": segment + 1,
            "label.production_fraction": float(self.production_levels[segment]),
            "label.fault_active": int(bool(names)),
            "label.fault_names": ";".join(names) if names else "none",
            "label.reaction_rate_loss_fraction": float(rloss),
            "label.cstr_heater_frozen": int(any(f.target == "cstr_heater_factor" for f in active_a)),
            "label.bottoms_flow_frozen": int(any(f.target == "bottoms_flow" for f in active_a)),
            "label.reflux_frozen": int(any(f.target == "reflux_flow" and f.mode == "freeze" for f in active_a)),
            "label.reflux_shutdown": int(any(f.target == "reflux_flow" and f.mode == "fixed" and f.fixed_value == 0.0 for f in active_a)),
        }


@dataclass(frozen=True)
class FaultDetectionCampaign:
    """Three independent 600-min experiments that are stacked after simulation."""
    blocks: Sequence[CampaignBlock]
    production_levels: Sequence[float]
    segment_duration_h: float

    @property
    def block_duration_h(self) -> float:
        return len(self.production_levels) * self.segment_duration_h

    @property
    def duration_h(self) -> float:
        return len(self.blocks) * self.block_duration_h

def production_step_trajectory(
    segment_duration_min: float = 120.0,
    levels: Sequence[float] = (1.00, 0.85, 0.70, 0.85, 1.00),
    repeats: int = 1,
) -> tuple[PiecewiseConstant, float]:
    seg_h = float(segment_duration_min) / 60.0
    pts: list[tuple[float, float]] = []
    for b in range(repeats):
        offset = b * len(levels) * seg_h
        for j, level in enumerate(levels):
            pts.append((offset + j * seg_h, float(level)))
    duration_h = repeats * len(levels) * seg_h
    return PiecewiseConstant(pts), duration_h


def base_production_scenario(
    segment_duration_min: float = 120.0,
    levels: Sequence[float] = (1.00, 0.85, 0.70, 0.85, 1.00),
) -> Scenario:
    tr, duration_h = production_step_trajectory(segment_duration_min, levels, repeats=1)
    return Scenario(name="base_production_steps", duration_h=duration_h, throughput_fraction=tr)


def fault_detection_campaign(
    segment_duration_min: float = 120.0,
    production_levels: Sequence[float] = (1.00, 0.85, 0.70, 0.85, 1.00),
) -> FaultDetectionCampaign:
    """Build the requested three-block offline-FDD campaign.

    Each block is simulated independently from the same nominal steady state and
    later stacked with a global time offset. This avoids controller-history leakage
    between experiments and makes the three blocks directly comparable.
    """
    levels = tuple(float(x) for x in production_levels)
    seg_h = float(segment_duration_min) / 60.0
    block_h = len(levels) * seg_h
    min_h = 1.0 / 60.0
    reaction_offset = 20.0 * min_h
    actuator_offset = 5.0 * min_h
    actuator_duration = 20.0 * min_h

    def throughput_scenario(name: str, catalyst: Trajectory = Constant(1.0)) -> Scenario:
        tr, duration_h = production_step_trajectory(segment_duration_min, levels, repeats=1)
        return Scenario(name=name, duration_h=duration_h, throughput_fraction=tr, catalyst_activity=catalyst)

    # Block 1: clean normal operation.
    b1 = CampaignBlock(
        block_id=1,
        name="normal",
        scenario=throughput_scenario("normal"),
        actuator_faults=ActuatorFaultEngine(),
        reaction_faults=(),
        production_levels=levels,
        segment_duration_h=seg_h,
        global_offset_h=0.0,
    )

    # Block 2: one large reaction-rate fault + two frozen control outputs.
    r2 = ReactionRateFault(
        name="reaction_loss_50_block2",
        start_h=1.0 * seg_h + reaction_offset,
        final_loss_fraction=0.50,
    )
    a2 = ActuatorFaultEngine([
        ActuatorFault(
            name="cstr_temperature_valve_freeze",
            target="cstr_heater_factor",
            start_h=2.0 * seg_h + actuator_offset,
            end_h=2.0 * seg_h + actuator_offset + actuator_duration,
            mode="freeze",
        ),
        ActuatorFault(
            name="bottoms_level_valve_freeze",
            target="bottoms_flow",
            start_h=3.0 * seg_h + actuator_offset,
            end_h=3.0 * seg_h + actuator_offset + actuator_duration,
            mode="freeze",
        ),
    ])
    b2 = CampaignBlock(
        block_id=2,
        name="fault_set_1",
        scenario=throughput_scenario("fault_set_1", CatalystActivityTrajectory([r2])),
        actuator_faults=a2,
        reaction_faults=(r2,),
        production_levels=levels,
        segment_duration_h=seg_h,
        global_offset_h=block_h,
    )

    # Block 3: two reaction-rate faults + frozen reflux temperature-control output.
    r31 = ReactionRateFault(
        name="reaction_loss_50_block3",
        start_h=1.0 * seg_h + reaction_offset,
        final_loss_fraction=0.50,
    )
    r32 = ReactionRateFault(
        name="reaction_loss_30_block3",
        start_h=2.0 * seg_h + reaction_offset,
        final_loss_fraction=0.30,
    )
    a3 = ActuatorFaultEngine([
        ActuatorFault(
            name="reflux_temperature_control_freeze",
            target="reflux_flow",
            start_h=3.0 * seg_h + actuator_offset,
            end_h=3.0 * seg_h + actuator_offset + actuator_duration,
            mode="freeze",
        ),
    ])
    b3 = CampaignBlock(
        block_id=3,
        name="fault_set_2",
        scenario=throughput_scenario("fault_set_2", CatalystActivityTrajectory([r31, r32])),
        actuator_faults=a3,
        reaction_faults=(r31, r32),
        production_levels=levels,
        segment_duration_h=seg_h,
        global_offset_h=2.0 * block_h,
    )

    return FaultDetectionCampaign(
        blocks=(b1, b2, b3),
        production_levels=levels,
        segment_duration_h=seg_h,
    )
