from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
import numpy as np


@dataclass(frozen=True)
class SensorSpec:
    sigma: float
    lower: float | None = None
    upper: float | None = None


@dataclass(frozen=True)
class SensorFault:
    """Simple BPCS measurement fault.

    Supported modes in v0.5:
      bias   : add `bias` to the sampled measurement
      freeze : hold the first sampled value after fault activation
    """
    target: str
    start_h: float
    end_h: float | None = None
    mode: str = "bias"
    bias: float = 0.0
    name: str = "sensor_fault"

    def active(self, t_h: float) -> bool:
        return t_h >= self.start_h and (self.end_h is None or t_h < self.end_h)


class SensorFaultEngine:
    def __init__(self, faults: Sequence[SensorFault] | None = None):
        self.faults = list(faults or [])
        self._frozen: dict[int, float] = {}

    def reset(self) -> None:
        self._frozen.clear()

    def active_faults(self, t_h: float) -> list[SensorFault]:
        return [f for f in self.faults if f.active(t_h)]

    def apply(self, t_h: float, measurements: dict[str, float]) -> dict[str, float]:
        out = dict(measurements)
        for idx, fault in enumerate(self.faults):
            if not fault.active(t_h):
                self._frozen.pop(idx, None)
                continue
            if fault.target not in out:
                raise KeyError(f"Unknown measurement tag '{fault.target}'")
            if fault.mode == "bias":
                out[fault.target] = float(out[fault.target] + fault.bias)
            elif fault.mode == "freeze":
                if idx not in self._frozen:
                    self._frozen[idx] = float(out[fault.target])
                out[fault.target] = self._frozen[idx]
            else:
                raise ValueError(f"Unsupported sensor fault mode '{fault.mode}'")
        return out


class MeasurementModel:
    """Fixed-rate reproducible Gaussian sensor-noise layer."""

    def __init__(self, specs: dict[str, SensorSpec], seed: int = 20260928):
        self.specs = dict(specs)
        self.seed = int(seed)
        self.rng = np.random.default_rng(self.seed)

    def reset(self) -> None:
        self.rng = np.random.default_rng(self.seed)

    def observe(self, truth: dict[str, float]) -> dict[str, float]:
        out = {}
        for key, val in truth.items():
            spec = self.specs.get(key)
            if spec is None or spec.sigma <= 0.0:
                noisy = float(val)
            else:
                noisy = float(val + self.rng.normal(0.0, spec.sigma))
                if spec.lower is not None:
                    noisy = max(noisy, spec.lower)
                if spec.upper is not None:
                    noisy = min(noisy, spec.upper)
            out[key] = noisy
        return out


def default_sensor_specs(nominal_truth: dict[str, float]) -> dict[str, SensorSpec]:
    """Small sensor-like noise rather than a fixed percentage of all signals."""
    specs: dict[str, SensorSpec] = {}
    for key, val in nominal_truth.items():
        if "x_acetone" in key:
            specs[key] = SensorSpec(0.0025, 0.0, 1.0)
        elif "temperature_K" in key:
            specs[key] = SensorSpec(0.15, 200.0, 600.0)
        elif "pressure_bar" in key:
            specs[key] = SensorSpec(max(0.0025, 0.0025 * abs(val)), 0.0, None)
        elif "holdup_kmol" in key:
            specs[key] = SensorSpec(max(1e-6, 0.005 * abs(val)), 0.0, None)
        elif "flow_kmol_h" in key or "vent_kmol_h" in key:
            specs[key] = SensorSpec(max(1e-6, 0.005 * abs(val)), 0.0, None)
        else:
            specs[key] = SensorSpec(0.0)
    return specs
