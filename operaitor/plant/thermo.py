from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from scipy.optimize import brentq

T_REF = 298.15  # K


@dataclass(frozen=True)
class ThermoSurrogate:
    """Low-order acetone/isopropanol surrogate.

    Component order is [acetone (light), isopropanol (heavy)].
    Pressure: bar; temperature: K; enthalpy: kJ/kmol.
    """

    # ln(Psat/bar) = A - B/T
    A_psat: tuple[float, float] = (10.938628, 13.521464)
    B_psat: tuple[float, float] = (3600.0, 4800.0)

    cp_liq: tuple[float, float] = (125.0, 160.0)  # kJ/(kmol K)
    cp_vap: tuple[float, float] = (80.0, 100.0)   # kJ/(kmol K)
    dh_vap: tuple[float, float] = (30000.0, 40000.0)  # kJ/kmol

    # Low-order asymmetric Margules-like surrogate.
    A12: float = 0.20
    A21: float = 0.15

    def psat(self, T: float) -> np.ndarray:
        A = np.asarray(self.A_psat)
        B = np.asarray(self.B_psat)
        return np.exp(A - B / T)

    def gamma(self, x_light: float) -> np.ndarray:
        x1 = float(np.clip(x_light, 1e-10, 1.0 - 1e-10))
        x2 = 1.0 - x1
        lng1 = (self.A12 + 2.0 * (self.A21 - self.A12) * x1) * x2**2
        lng2 = (self.A21 + 2.0 * (self.A12 - self.A21) * x2) * x1**2
        return np.exp(np.array([lng1, lng2]))

    def bubble_pressure(self, T: float, x_light: float) -> float:
        x = np.array([x_light, 1.0 - x_light])
        return float(np.sum(x * self.gamma(x_light) * self.psat(T)))

    def y_light(self, T: float, x_light: float, P_bar: float | None = None) -> float:
        P = self.bubble_pressure(T, x_light) if P_bar is None else P_bar
        return float(x_light * self.gamma(x_light)[0] * self.psat(T)[0] / P)

    def bubble_temperature(self, x_light: float, P_bar: float) -> float:
        return float(brentq(lambda T: self.bubble_pressure(T, x_light) - P_bar, 275.0, 450.0))

    def h_liq(self, T: float, x_light: float) -> float:
        x = np.array([x_light, 1.0 - x_light])
        cp = float(np.dot(x, np.asarray(self.cp_liq)))
        return cp * (T - T_REF)

    def h_vap(self, T: float, y_light: float) -> float:
        y = np.array([y_light, 1.0 - y_light])
        cp = float(np.dot(y, np.asarray(self.cp_vap)))
        latent = float(np.dot(y, np.asarray(self.dh_vap)))
        return cp * (T - T_REF) + latent
    def water_saturation_temperature(self, P_bar: float) -> float:
        """Small Antoine surrogate for saturated steam temperature [K].

        Validity is more than adequate around the nominal ~5.66 bar(abs) steam
        header used by this benchmark.
        """
        P = max(float(P_bar), 0.05)
        # Antoine: log10(P_mmHg) = A - B/(C + T_C)
        A, B, C = 8.14019, 1810.94, 244.485
        P_mmHg = P / 0.00133322
        T_C = B / (A - np.log10(P_mmHg)) - C
        return float(T_C + 273.15)

