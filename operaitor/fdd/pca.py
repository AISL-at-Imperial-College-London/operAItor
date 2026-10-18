"""PCA process monitoring: Hotelling T², SPE (Q) and contribution analysis.

The model is trained on normal operating data only. At run time each operator
observation is mapped to a feature vector (BPCS measurements plus controller
outputs), scaled with the training statistics and projected on the retained
principal components.

    T²  = t Λ⁻¹ tᵀ            variation *inside* the PCA model (abnormal
                                 magnitudes of normal correlations)
    SPE = ‖x − x P Pᵀ‖²       variation *outside* the model (broken
                                 correlations, e.g. sensor faults)

Limits use the F distribution for T² and Box's χ² approximation for SPE.
Contributions (complete decomposition) rank the variables driving an alarm and
are the input to the diagnosis step.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
from scipy import stats

# Pump failure flags are binary indications and are handled by rule checks.
EXCLUDED_MEASUREMENT_PREFIXES = ("pump_",)


def observation_features(observation: Mapping) -> dict[str, float]:
    """Feature dictionary from an ``EpisodeRunner.observation()``."""
    feats = {
        f"meas.{k}": float(v) for k, v in observation["measurements"].items()
        if not k.startswith(EXCLUDED_MEASUREMENT_PREFIXES)
    }
    for loop_id, info in observation["controllers"].items():
        feats[f"op.{loop_id}"] = float(info["last_output"])
    return feats


def record_features(record: Mapping) -> dict[str, float]:
    """Same features from an ``EpisodeRunner.records`` row (or its CSV)."""
    feats: dict[str, float] = {}
    for key, value in record.items():
        if key.startswith("meas.") and not key[5:].startswith(EXCLUDED_MEASUREMENT_PREFIXES):
            feats[key] = float(value)
        elif key.startswith("loop.") and key.endswith(".op"):
            feats[f"op.{key.split('.')[1]}"] = float(value)
    return feats


@dataclass
class PCAScore:
    t2: float
    spe: float
    t2_limit: float
    spe_limit: float
    t2_contrib: dict[str, float] = field(default_factory=dict)
    spe_contrib: dict[str, float] = field(default_factory=dict)

    @property
    def t2_violation(self) -> bool:
        return self.t2 > self.t2_limit

    @property
    def spe_violation(self) -> bool:
        return self.spe > self.spe_limit

    @property
    def violation(self) -> bool:
        return self.t2_violation or self.spe_violation

    def top_contributors(self, n: int = 5) -> list[dict[str, float]]:
        """Variables ranked by normalised combined contribution."""
        t2n = {k: max(v, 0.0) / self.t2_limit for k, v in self.t2_contrib.items()}
        spen = {k: v / self.spe_limit for k, v in self.spe_contrib.items()}
        combined = {k: t2n.get(k, 0.0) + spen.get(k, 0.0) for k in set(t2n) | set(spen)}
        ranked = sorted(combined.items(), key=lambda kv: -kv[1])[:n]
        return [{"variable": k, "score": float(v),
                 "t2": float(self.t2_contrib.get(k, 0.0)),
                 "spe": float(self.spe_contrib.get(k, 0.0))} for k, v in ranked]

    def to_dict(self, n_contrib: int = 8) -> dict:
        return {"t2": self.t2, "spe": self.spe, "t2_limit": self.t2_limit,
                "spe_limit": self.spe_limit, "t2_violation": self.t2_violation,
                "spe_violation": self.spe_violation,
                "top_contributors": self.top_contributors(n_contrib)}


class PCAMonitor:
    """Static PCA monitor with T²/SPE limits and contribution analysis."""

    def __init__(self, variables: list[str], mean: np.ndarray, std: np.ndarray,
                 loadings: np.ndarray, eigenvalues: np.ndarray,
                 t2_limit: float, spe_limit: float, meta: dict | None = None):
        self.variables = list(variables)
        self.mean = np.asarray(mean, dtype=float)
        self.std = np.asarray(std, dtype=float)
        self.P = np.asarray(loadings, dtype=float)          # (m, k)
        self.lam = np.asarray(eigenvalues, dtype=float)     # (k,)
        self.t2_limit = float(t2_limit)
        self.spe_limit = float(spe_limit)
        self.meta = dict(meta or {})

    # ------------------------------------------------------------------ fit
    @classmethod
    def fit(cls, X: np.ndarray, variables: list[str], *, cpv: float = 0.90,
            n_components: int | None = None, alpha: float = 0.99,
            limits: str = "max", min_std: float = 1e-9) -> "PCAMonitor":
        X = np.asarray(X, dtype=float)
        if X.ndim != 2 or X.shape[1] != len(variables):
            raise ValueError("X must be (n_samples, n_variables)")
        keep = np.all(np.isfinite(X), axis=1)
        X = X[keep]
        n, m = X.shape
        if n <= m:
            raise ValueError(f"Need more samples ({n}) than variables ({m})")

        mean = X.mean(axis=0)
        std = X.std(axis=0, ddof=1)
        # Constant variables carry no information; keep them but never scale up noise.
        std = np.where(std < min_std, 1.0, std)
        Z = (X - mean) / std

        _, s, Vt = np.linalg.svd(Z, full_matrices=False)
        eig = s**2 / (n - 1)
        if n_components is None:
            frac = np.cumsum(eig) / eig.sum()
            n_components = int(np.searchsorted(frac, cpv) + 1)
        k = int(min(max(n_components, 1), m - 1))
        P = Vt[:k].T
        lam = eig[:k]

        T = Z @ P
        t2 = np.sum(T**2 / lam, axis=1)
        t2_theory = k * (n - 1) * (n + 1) / (n * (n - k)) * stats.f.ppf(alpha, k, n - k)

        # Box (1954): SPE ~ g·χ²_h, moments matched on the training SPE.
        R = Z - T @ P.T
        spe = np.sum(R**2, axis=1)
        mu, var = spe.mean(), spe.var(ddof=1)
        g, h = var / (2 * mu), 2 * mu**2 / var
        spe_theory = g * stats.chi2.ppf(alpha, h)

        # Closed-loop plant data are autocorrelated and non-Gaussian (setpoint
        # and disturbance steps), so the theoretical limits are optimistic.
        # "max" (default) never goes below the empirical alpha-quantile of the
        # training data; "theory" is more sensitive but alarms more often.
        t2_emp, spe_emp = float(np.quantile(t2, alpha)), float(np.quantile(spe, alpha))
        if limits == "theory":
            t2_lim, spe_lim = t2_theory, spe_theory
        elif limits == "empirical":
            t2_lim, spe_lim = t2_emp, spe_emp
        elif limits == "max":
            t2_lim, spe_lim = max(t2_theory, t2_emp), max(spe_theory, spe_emp)
        else:
            raise ValueError("limits must be 'theory', 'empirical' or 'max'")

        meta = {"n_samples": int(n), "n_components": k, "alpha": alpha,
                "explained_variance": float(eig[:k].sum() / eig.sum()),
                "limits": limits, "t2_limit_theory": float(t2_theory), "spe_limit_theory": float(spe_theory)}
        return cls(variables, mean, std, P, lam, t2_lim, spe_lim, meta)

    # ---------------------------------------------------------------- score
    def vector(self, features: Mapping[str, float]) -> np.ndarray:
        missing = [v for v in self.variables if v not in features]
        if missing:
            raise KeyError(f"Missing PCA features: {missing[:5]}")
        return np.array([features[v] for v in self.variables], dtype=float)

    def score_vector(self, x: np.ndarray) -> PCAScore:
        z = (np.asarray(x, dtype=float) - self.mean) / self.std
        t = z @ self.P
        t2 = float(np.sum(t**2 / self.lam))
        r = z - t @ self.P.T
        spe = float(r @ r)
        # Complete decomposition: c_i = z_i * (P Λ⁻¹ Pᵀ z)_i, sums to T².
        t2c = z * (self.P @ (t / self.lam))
        return PCAScore(t2, spe, self.t2_limit, self.spe_limit,
                        dict(zip(self.variables, map(float, t2c))),
                        dict(zip(self.variables, map(float, r**2))))

    def score(self, features: Mapping[str, float]) -> PCAScore:
        return self.score_vector(self.vector(features))

    def score_observation(self, observation: Mapping) -> PCAScore:
        return self.score(observation_features(observation))

    # ------------------------------------------------------------ persist
    def to_dict(self) -> dict:
        return {"variables": self.variables, "mean": self.mean.tolist(),
                "std": self.std.tolist(), "loadings": self.P.tolist(),
                "eigenvalues": self.lam.tolist(), "t2_limit": self.t2_limit,
                "spe_limit": self.spe_limit, "meta": self.meta}

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "PCAMonitor":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(d["variables"], d["mean"], d["std"], d["loadings"],
                   d["eigenvalues"], d["t2_limit"], d["spe_limit"], d.get("meta"))


def matrix_from_feature_rows(rows: Iterable[Mapping[str, float]]) -> tuple[np.ndarray, list[str]]:
    rows = list(rows)
    variables = sorted(set.intersection(*(set(r) for r in rows)))
    X = np.array([[r[v] for v in variables] for r in rows], dtype=float)
    return X, variables

