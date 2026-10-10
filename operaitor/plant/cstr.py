from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import numpy as np
from scipy.optimize import least_squares

from .thermo import ThermoSurrogate, T_REF

R_BAR = 0.08314  # bar m^3/(kmol K)
R_KJ = 8.314     # kJ/(kmol K)


@dataclass(frozen=True)
class CSTRConfig:
    """Synthetic catalytic IPA -> acetone + H2 liquid-CSTR benchmark.

    This is intentionally a compact abnormal-operation benchmark rather than a
    detailed industrial acetone reactor model.  v0.5 adds simple environmental
    heat loss and independent PCV/BDV/PSV pressure-protection paths without
    changing the underlying reaction model.
    """

    feed_nominal: float = 100.0            # kmol/h pure IPA
    liquid_holdup_sp: float = 50.0         # kmol
    conversion_nominal: float = 0.70
    temperature_nominal: float = 335.7416402201051  # K
    pressure_nominal: float = 3.0          # bar

    total_vessel_volume_m3: float = 6.0
    liquid_molar_volume_m3_kmol: float = 0.075

    # Effective H2-system gas capacitance.  The physical reactor headspace is
    # augmented by a lumped connected gas volume (vent piping / disengagement
    # space / flare-side capacitance).  At the nominal 50 kmol liquid holdup
    # the reactor headspace is 2.25 m3, so this default gives about 25 m3 total.
    connected_h2_gas_volume_m3: float = 22.75

    # First-order pseudo-homogeneous catalytic rate: r = k(T) * n_IPA
    k_ref_h: float = 4.666666666666667
    T_kin_ref: float = 335.7416402201051
    activation_energy_kJ_kmol: float = 60000.0

    reaction_enthalpy_kJ_kmol: float = 68000.0  # endothermic
    cp_h2_kJ_kmolK: float = 29.0

    # Normal jacket heater + simple environmental loss.
    UA_jacket_kJ_hK: float = 165000.0
    jacket_hot_temperature_K: float = 390.0
    ambient_temperature_K: float = 298.15
    UA_heat_loss_kJ_hK: float = 2500.0

    # Independent emergency cooling path, normally off.
    UA_emergency_cooling_kJ_hK: float = 50000.0

    # Normal H2 pressure-control valve (PCV) to flare/header.
    vent_backpressure_bar: float = 1.01325
    vent_Cv_kmol_h_sqrtbar: float = 99.32450854007949
    vent_valve_nominal: float = 0.5

    # Independent active blowdown valve (BDV), normally closed.
    bdv_Cv_kmol_h_sqrtbar: float = 135.0

    # Passive mechanical relief (PSV).  This is deliberately a low-order
    # surrogate, not a relief-sizing calculation.
    psv_set_pressure_bar: float = 4.50
    psv_full_open_pressure_bar: float = 4.70
    psv_Cv_kmol_h_sqrtbar: float = 180.0

    feed_temperature_K: float = 298.15


@dataclass(frozen=True)
class CSTRInputs:
    feed_flow: float = 100.0
    liquid_outflow: float = 100.0
    heater_factor: float = 0.60             # 0..1 nominally
    vent_valve: float = 0.5                 # normal PCV, 0..1
    bdv_valve: float = 0.0                  # emergency BDV, 0..1
    emergency_cooling_factor: float = 0.0   # 0..1
    feed_temperature: float = 298.15
    heating_utility_temperature_K: float = 390.0
    cooling_water_temperature_K: float = 298.15
    ambient_temperature_K: float = 298.15
    catalyst_activity: float = 1.0
    liquid_leak_flow: float = 0.0           # kmol/h


class CSTRDAE:
    """Direct residual DAE for the synthetic non-isothermal CSTR.

    State order:
      0 M_liq      differential, kmol
      1 n_acetone  differential, kmol
      2 U_total    differential, kJ (sensible-energy surrogate)
      3 n_H2       differential, kmol
      4 T          algebraic, K
      5 x_acetone  algebraic
      6 P          algebraic, bar
      7 r          algebraic, kmol/h
      8 Fvent      algebraic, total H2 removal via PCV+BDV+PSV, kmol/h
    """

    M, NB, U, NH2, T, XB, P, RATE, FVENT = range(9)

    def __init__(self, config: CSTRConfig = CSTRConfig(), thermo: ThermoSurrogate = ThermoSurrogate()):
        self.cfg = config
        self.th = thermo
        self.n_variables = 9
        self.differential_mask = np.zeros(9, dtype=bool)
        self.differential_mask[:4] = True

    @staticmethod
    def _positive(a: float, eps: float = 1e-10) -> float:
        return float(0.5 * (a + np.sqrt(a * a + eps * eps)))

    @staticmethod
    def _clip01(x: float) -> float:
        return float(min(max(x, 0.0), 1.0))

    def default_inputs(self) -> CSTRInputs:
        c = self.cfg
        return CSTRInputs(
            feed_flow=c.feed_nominal,
            liquid_outflow=c.feed_nominal,
            heater_factor=self.nominal_heater_factor(),
            vent_valve=c.vent_valve_nominal,
            bdv_valve=0.0,
            emergency_cooling_factor=0.0,
            feed_temperature=c.feed_temperature_K,
            heating_utility_temperature_K=c.jacket_hot_temperature_K,
            cooling_water_temperature_K=c.feed_temperature_K,
            ambient_temperature_K=c.ambient_temperature_K,
            catalyst_activity=1.0,
            liquid_leak_flow=0.0,
        )

    def rate_constant(self, T: float) -> float:
        c = self.cfg
        return c.k_ref_h * np.exp(-c.activation_energy_kJ_kmol / R_KJ * (1.0 / T - 1.0 / c.T_kin_ref))

    def reactor_headspace_volume(self, M_liq: float) -> float:
        c = self.cfg
        return max(c.total_vessel_volume_m3 - c.liquid_molar_volume_m3_kmol * M_liq, 1e-5)

    def gas_volume(self, M_liq: float) -> float:
        """Effective gas volume used for H2 pressure capacitance.

        The liquid level still uses the real 6 m3 vessel geometry; only the gas
        pressure inventory includes the connected-system capacitance.
        """
        c = self.cfg
        return self.reactor_headspace_volume(M_liq) + max(c.connected_h2_gas_volume_m3, 0.0)

    def h_h2(self, T: float) -> float:
        return self.cfg.cp_h2_kJ_kmolK * (T - T_REF)

    def u_h2(self, T: float) -> float:
        cv = self.cfg.cp_h2_kJ_kmolK - R_KJ
        return cv * (T - T_REF)

    def nominal_heater_factor(self) -> float:
        c, th = self.cfg, self.th
        F = c.feed_nominal
        r = F * c.conversion_nominal
        T = c.temperature_nominal
        x = c.conversion_nominal
        h_in = th.h_liq(c.feed_temperature_K, 0.0)
        h_out = th.h_liq(T, x)
        h2 = self.h_h2(T)
        q_loss = c.UA_heat_loss_kJ_hK * max(T - c.ambient_temperature_K, 0.0)
        Q_needed = (
            F * h_out + r * h2 + r * c.reaction_enthalpy_kJ_kmol
            - F * h_in + q_loss
        )
        return Q_needed / (c.UA_jacket_kJ_hK * (c.jacket_hot_temperature_K - T))

    def initial_guess(self, inputs: Optional[CSTRInputs] = None) -> np.ndarray:
        del inputs
        c = self.cfg
        M = c.liquid_holdup_sp
        x = c.conversion_nominal
        T = c.temperature_nominal
        nB = M * x
        Vg = self.gas_volume(M)
        nH2 = c.pressure_nominal * Vg / (R_BAR * T)
        r = c.feed_nominal * c.conversion_nominal
        U = M * self.th.h_liq(T, x) + nH2 * self.u_h2(T)
        Fvent = r
        return np.array([M, nB, U, nH2, T, x, c.pressure_nominal, r, Fvent], dtype=float)

    def psv_open_fraction(self, P_bar: float) -> float:
        c = self.cfg
        span = max(c.psv_full_open_pressure_bar - c.psv_set_pressure_bar, 1e-6)
        return self._clip01(self._positive(P_bar - c.psv_set_pressure_bar) / span)

    def h2_flow_components(self, state: np.ndarray, inputs: CSTRInputs) -> dict[str, float]:
        c = self.cfg
        P = float(state[self.P])
        dp = self._positive(P - c.vent_backpressure_bar)
        root_dp = np.sqrt(dp + 1e-14)
        pcv = self._clip01(inputs.vent_valve) * c.vent_Cv_kmol_h_sqrtbar * root_dp
        bdv = self._clip01(inputs.bdv_valve) * c.bdv_Cv_kmol_h_sqrtbar * root_dp
        psv_open = self.psv_open_fraction(P)
        psv = psv_open * c.psv_Cv_kmol_h_sqrtbar * root_dp
        return {
            "pcv_kmol_h": float(pcv),
            "bdv_kmol_h": float(bdv),
            "psv_kmol_h": float(psv),
            "psv_open_fraction": float(psv_open),
            "total_kmol_h": float(pcv + bdv + psv),
        }

    def residual(self, t: float, state: np.ndarray, state_dot: np.ndarray,
                 inputs: Optional[CSTRInputs] = None) -> np.ndarray:
        del t
        u = self.default_inputs() if inputs is None else inputs
        c, th = self.cfg, self.th
        M, nB, U, nH2, T, xB, P, rate, Fvent = state
        r = np.zeros_like(state)

        Qj = c.UA_jacket_kJ_hK * u.heater_factor * self._positive(u.heating_utility_temperature_K - T)
        Qloss = c.UA_heat_loss_kJ_hK * self._positive(T - u.ambient_temperature_K)
        Qem = c.UA_emergency_cooling_kJ_hK * u.emergency_cooling_factor * self._positive(
            T - u.cooling_water_temperature_K
        )
        h_feed = th.h_liq(u.feed_temperature, 0.0)  # pure IPA
        h_out = th.h_liq(T, xB)
        h2_out = self.h_h2(T)
        Fliq_total = u.liquid_outflow + max(u.liquid_leak_flow, 0.0)

        # A -> B preserves total liquid moles; H2 leaves to the gas phase.
        r[self.M] = state_dot[self.M] - (u.feed_flow - Fliq_total)
        r[self.NB] = state_dot[self.NB] - (rate - Fliq_total * xB)
        r[self.U] = state_dot[self.U] - (
            u.feed_flow * h_feed
            - Fliq_total * h_out
            - Fvent * h2_out
            + Qj - Qloss - Qem
            - rate * c.reaction_enthalpy_kJ_kmol
        )
        r[self.NH2] = state_dot[self.NH2] - (rate - Fvent)

        r[self.T] = xB - nB / max(M, 1e-12)
        r[self.XB] = U - (M * th.h_liq(T, xB) + nH2 * self.u_h2(T))

        Vg = self.gas_volume(M)
        r[self.P] = P - nH2 * R_BAR * T / Vg
        r[self.RATE] = rate - u.catalyst_activity * self.rate_constant(T) * self._positive(M - nB)

        flows = self.h2_flow_components(state, u)
        r[self.FVENT] = Fvent - flows["total_kmol_h"]
        return r

    def scaled_residual(self, t: float, y: np.ndarray, ydot: np.ndarray,
                        inputs: Optional[CSTRInputs] = None) -> np.ndarray:
        r = self.residual(t, y, ydot, inputs).copy()
        r[self.M] /= 100.0
        r[self.NB] /= 100.0
        r[self.U] /= 6.0e6
        r[self.NH2] /= 100.0
        r[self.XB] /= 1.0e5
        r[self.P] /= 1.0
        r[self.RATE] /= 100.0
        r[self.FVENT] /= 100.0
        return r

    def solve_steady_state(self, inputs: Optional[CSTRInputs] = None,
                           guess: Optional[np.ndarray] = None):
        u = self.default_inputs() if inputs is None else inputs
        y0 = self.initial_guess(u) if guess is None else np.asarray(guess, dtype=float)
        z = np.zeros_like(y0)
        return least_squares(
            lambda yy: self.scaled_residual(0.0, yy, z, u), y0,
            x_scale="jac", max_nfev=2000,
            ftol=1e-12, xtol=1e-12, gtol=1e-12,
        )

    def outlet_stream(self, state: np.ndarray, inputs: CSTRInputs) -> dict:
        return {
            "flow_kmol_h": float(inputs.liquid_outflow),
            "x_acetone": float(state[self.XB]),
            "temperature_K": float(state[self.T]),
        }

    def operating_summary(self, y: np.ndarray, inputs: Optional[CSTRInputs] = None) -> dict:
        u = self.default_inputs() if inputs is None else inputs
        c = self.cfg
        Qj = c.UA_jacket_kJ_hK * u.heater_factor * self._positive(u.heating_utility_temperature_K - y[self.T])
        Qloss = c.UA_heat_loss_kJ_hK * self._positive(y[self.T] - u.ambient_temperature_K)
        flows = self.h2_flow_components(y, u)
        return {
            "liquid_holdup_kmol": float(y[self.M]),
            "acetone_x": float(y[self.XB]),
            "conversion": float(y[self.XB]),
            "temperature_K": float(y[self.T]),
            "pressure_bar": float(y[self.P]),
            "reaction_rate_kmol_h": float(y[self.RATE]),
            "hydrogen_vent_kmol_h": float(y[self.FVENT]),
            "hydrogen_pcv_kmol_h": flows["pcv_kmol_h"],
            "hydrogen_bdv_kmol_h": flows["bdv_kmol_h"],
            "hydrogen_psv_kmol_h": flows["psv_kmol_h"],
            "psv_open_fraction": flows["psv_open_fraction"],
            "liquid_outflow_kmol_h": float(u.liquid_outflow),
            "liquid_leak_kmol_h": float(u.liquid_leak_flow),
            "heater_factor": float(u.heater_factor),
            "heater_duty_MW": float(Qj / 3.6e6),
            "heat_loss_MW": float(Qloss / 3.6e6),
        }
