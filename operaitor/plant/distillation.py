from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import numpy as np
from scipy.optimize import least_squares

from .thermo import ThermoSurrogate


@dataclass(frozen=True)
class ColumnConfig:
    n_trays: int = 10
    feed_stage: int = 5  # zero-based, tray 6 from top

    feed_nominal: float = 100.0
    z_feed_nominal: float = 0.70
    reflux_nominal: float = 80.0
    distillate_nominal: float = 73.20293749306585
    bottoms_nominal: float = 26.797062506934168

    tray_holdup_nominal: float = 2.5
    reboiler_holdup_sp: float = 34.509631253467084
    drum_holdup_sp: float = 30.24518437326646

    liquid_hydraulic_exponent: float = 1.5
    nominal_stage_dp_bar: float = 0.005
    nominal_vapour_flow: float = 158.8889

    # steam_factor is the reboiler steam-valve opening (0-1), the BOT_TIC manipulated variable.
    # UA is sized so the valve is 60 % open at the nominal duty (the same duty as the former
    # UA=60000 x factor 1.0149 formulation); fully open gives 1.69x the nominal duty.
    UA_reboiler: float = 60000.0 * 1.0148912356226403 / 0.60
    steam_temperature: float = 430.0
    steam_pressure_nominal_bar: float = 5.659806566043441
    steam_factor_nominal: float = 0.60

    # v0.5.2: condenser cooling-water valve is the regulatory pressure MV.
    # UA is rescaled so 60% nominal valve opening gives the same nominal duty
    # as the previous UA=135000, cooling_factor=1.0 formulation.
    UA_condenser: float = 225000.0
    condenser_cooling_valve_nominal: float = 0.60
    condenser_cooling_effectiveness_nominal: float = 1.0
    cooling_water_temperature: float = 298.15
    condenser_pressure_bar: float = 1.0

    # v0.5: simple environmental losses on large vessels.
    ambient_temperature_K: float = 298.15
    UA_reboiler_loss_kJ_hK: float = 1200.0
    UA_drum_loss_kJ_hK: float = 0.0

    # v0.5: one lumped condenser/column pressure state.  Pressure is driven by
    # the mismatch between top vapour generation and available condensation.
    # These are phenomenological tuning parameters for abnormal-operation tests.
    vapour_capacitance_kmol_per_bar: float = 20.0
    pressure_recovery_gain_kmol_h_per_bar: float = 80.0
    condenser_energy_per_kmol_kJ: float = 29582.771975577365
    condenser_capacity_per_kmol_kJ: float = 28991.11653606582


@dataclass(frozen=True)
class ColumnInputs:
    """Manipulated/exogenous variables seen by the physical column model."""

    feed_flow: float = 100.0
    feed_z_light: float = 0.70
    feed_temperature: float | None = None

    reflux_flow: float = 80.0
    steam_factor: float = 0.60  # steam-valve opening, 0-1
    condenser_cooling_valve: float = 0.60
    condenser_cooling_effectiveness: float = 1.0
    condenser_pressure_bar: float = 1.0  # retained as nominal/reference input for compatibility
    cooling_water_temperature_K: float = 298.15
    steam_pressure_bar: float = 5.659806566043441
    ambient_temperature_K: float = 298.15

    bottoms_flow: float = 27.7778
    distillate_flow: float = 72.2222
    reboiler_leak_flow: float = 0.0
    drum_leak_flow: float = 0.0


class DistillationDAE:
    """Direct residual DAE for the simplified binary column.

    v0.5 preserves the equilibrium-stage model but adds one dynamic condenser /
    top-pressure state.  This is intentionally a low-order pressure surrogate:
    it is sufficient for cooling-loss/over-boil scenarios without adding vapour
    inventories to every stage.
    """

    TRAY_M, TRAY_NL, TRAY_U, TRAY_T, TRAY_X, TRAY_Y, TRAY_P, TRAY_L, TRAY_V = range(9)

    def __init__(self, config: ColumnConfig = ColumnConfig(), thermo: ThermoSurrogate = ThermoSurrogate()):
        self.cfg = config
        self.th = thermo
        self.nper_tray = 9
        self.reboiler_offset = config.n_trays * self.nper_tray
        # reboiler [M,n_light,U,T,x,y,P,V]
        self.drum_offset = self.reboiler_offset + 8
        # drum [M,n_light,U,T,x]
        self.pressure_offset = self.drum_offset + 5
        # one differential condenser/top pressure state [Pcond]
        self.n_variables = self.pressure_offset + 1

        self.differential_mask = np.zeros(self.n_variables, dtype=bool)
        for i in range(config.n_trays):
            self.differential_mask[i * 9 : i * 9 + 3] = True
        self.differential_mask[self.reboiler_offset : self.reboiler_offset + 3] = True
        self.differential_mask[self.drum_offset : self.drum_offset + 3] = True
        self.differential_mask[self.pressure_offset] = True

        self.Cv_vap = config.nominal_vapour_flow / np.sqrt(config.nominal_stage_dp_bar)
        self.L_nominal = np.array([
            config.reflux_nominal if i < config.feed_stage
            else config.reflux_nominal + config.feed_nominal
            for i in range(config.n_trays)
        ], dtype=float)

    @staticmethod
    def _positive(a: float, eps: float = 1e-10) -> float:
        return float(0.5 * (a + np.sqrt(a * a + eps * eps)))

    @staticmethod
    def _smooth_min(a: float, b: float, eps: float = 1e-8) -> float:
        return float(0.5 * (a + b - np.sqrt((a - b) ** 2 + eps ** 2)))

    def _vapour_flow(self, delta_p_bar: float) -> float:
        return self.Cv_vap * np.sqrt(self._positive(delta_p_bar) + 1e-14)

    def tray_slice(self, i: int) -> slice:
        return slice(i * 9, (i + 1) * 9)

    def tray(self, y: np.ndarray, i: int) -> np.ndarray:
        return y[self.tray_slice(i)]

    def default_inputs(self) -> ColumnInputs:
        c = self.cfg
        return ColumnInputs(
            feed_flow=c.feed_nominal,
            feed_z_light=c.z_feed_nominal,
            reflux_flow=c.reflux_nominal,
            steam_factor=c.steam_factor_nominal,
            condenser_pressure_bar=c.condenser_pressure_bar,
            condenser_cooling_valve=c.condenser_cooling_valve_nominal,
            condenser_cooling_effectiveness=c.condenser_cooling_effectiveness_nominal,
            cooling_water_temperature_K=c.cooling_water_temperature,
            steam_pressure_bar=c.steam_pressure_nominal_bar,
            ambient_temperature_K=c.ambient_temperature_K,
            bottoms_flow=c.bottoms_nominal,
            distillate_flow=c.distillate_nominal,
        )

    def condenser_capacity(self, state: np.ndarray, inputs: ColumnInputs) -> dict[str, float]:
        top = self.tray(state, 0)
        Pcond = float(state[self.pressure_offset])
        Vtop = float(top[self.TRAY_V])
        valve = float(np.clip(inputs.condenser_cooling_valve, 0.0, 1.0))
        effectiveness = max(float(inputs.condenser_cooling_effectiveness), 0.0)
        Qavail = self.cfg.UA_condenser * valve * effectiveness * self._positive(
            top[self.TRAY_T] - inputs.cooling_water_temperature_K
        )
        latent = self.cfg.condenser_energy_per_kmol_kJ
        capacity = max(Qavail / self.cfg.condenser_capacity_per_kmol_kJ, 0.0)
        demand = self._positive(
            Vtop + self.cfg.pressure_recovery_gain_kmol_h_per_bar
            * (Pcond - self.cfg.condenser_pressure_bar)
        )
        Fcond = max(min(capacity, demand), 0.0)
        return {
            "available_duty_kJ_h": float(Qavail),
            "latent_kJ_kmol": float(latent),
            "capacity_kmol_h": float(capacity),
            "condensed_flow_kmol_h": float(Fcond),
            "top_vapour_flow_kmol_h": float(Vtop),
            "condenser_pressure_bar": float(Pcond),
            "cooling_valve_position": float(valve),
            "cooling_effectiveness": float(effectiveness),
        }

    def residual(self, t: float, state: np.ndarray, state_dot: np.ndarray,
                 inputs: Optional[ColumnInputs] = None) -> np.ndarray:
        del t
        u = self.default_inputs() if inputs is None else inputs
        c, th, N = self.cfg, self.th, self.cfg.n_trays
        r = np.zeros_like(state)

        M = np.empty(N); n = np.empty(N); U = np.empty(N)
        T = np.empty(N); x = np.empty(N); yv = np.empty(N)
        P = np.empty(N); L = np.empty(N); V = np.empty(N)
        for i in range(N):
            z = self.tray(state, i)
            M[i], n[i], U[i], T[i], x[i], yv[i], P[i], L[i], V[i] = z

        rb = state[self.reboiler_offset : self.reboiler_offset + 8]
        Mr, nr, Ur, Tr, xr, yr, Pr, Vr = rb

        dr = state[self.drum_offset : self.drum_offset + 5]
        Md, nd, Ud, Td, xd = dr
        Pcond = state[self.pressure_offset]

        B = u.bottoms_flow
        D = u.distillate_flow
        Rflx = u.reflux_flow
        rb_leak = max(u.reboiler_leak_flow, 0.0)
        dr_leak = max(u.drum_leak_flow, 0.0)

        T_steam = th.water_saturation_temperature(u.steam_pressure_bar)
        Q_reb = c.UA_reboiler * float(np.clip(u.steam_factor, 0.0, 1.0)) * self._positive(T_steam - Tr)
        Q_rb_loss = c.UA_reboiler_loss_kJ_hK * self._positive(Tr - u.ambient_temperature_K)
        Q_dr_loss = c.UA_drum_loss_kJ_hK * self._positive(Td - u.ambient_temperature_K)

        if u.feed_temperature is None:
            P_feed_guess = Pcond + (c.feed_stage + 1) * c.nominal_stage_dp_bar
            T_feed = th.bubble_temperature(u.feed_z_light, P_feed_guess)
        else:
            T_feed = u.feed_temperature
        h_feed = th.h_liq(T_feed, u.feed_z_light)

        for i in range(N):
            b = i * 9
            if i == 0:
                Lin = Rflx; xLin = xd; hLin = th.h_liq(Td, xd)
            else:
                Lin = L[i - 1]; xLin = x[i - 1]; hLin = th.h_liq(T[i - 1], x[i - 1])

            if i == N - 1:
                Vin = Vr; yVin = yr; hVin = th.h_vap(Tr, yr)
            else:
                Vin = V[i + 1]; yVin = yv[i + 1]; hVin = th.h_vap(T[i + 1], yv[i + 1])

            Fi = u.feed_flow if i == c.feed_stage else 0.0
            zi = u.feed_z_light

            r[b + self.TRAY_M] = state_dot[b + self.TRAY_M] - (Lin + Vin + Fi - L[i] - V[i])
            r[b + self.TRAY_NL] = state_dot[b + self.TRAY_NL] - (
                Lin * xLin + Vin * yVin + Fi * zi - L[i] * x[i] - V[i] * yv[i]
            )
            r[b + self.TRAY_U] = state_dot[b + self.TRAY_U] - (
                Lin * hLin + Vin * hVin + Fi * h_feed
                - L[i] * th.h_liq(T[i], x[i]) - V[i] * th.h_vap(T[i], yv[i])
            )

            r[b + self.TRAY_T] = x[i] - n[i] / max(M[i], 1e-12)
            r[b + self.TRAY_X] = U[i] - M[i] * th.h_liq(T[i], x[i])
            r[b + self.TRAY_Y] = P[i] - th.bubble_pressure(T[i], x[i])
            r[b + self.TRAY_P] = yv[i] - th.y_light(T[i], x[i], P[i])

            Mpos = self._positive(M[i])
            L_model = self.L_nominal[i] * (Mpos / c.tray_holdup_nominal) ** c.liquid_hydraulic_exponent
            r[b + self.TRAY_L] = L[i] - L_model

            P_above = Pcond if i == 0 else P[i - 1]
            r[b + self.TRAY_V] = V[i] - self._vapour_flow(P[i] - P_above)

        # Reboiler with physical liquid leak.
        b = self.reboiler_offset
        Btot = B + rb_leak
        r[b + 0] = state_dot[b + 0] - (L[-1] - Btot - Vr)
        r[b + 1] = state_dot[b + 1] - (L[-1] * x[-1] - Btot * xr - Vr * yr)
        r[b + 2] = state_dot[b + 2] - (
            L[-1] * th.h_liq(T[-1], x[-1])
            - Btot * th.h_liq(Tr, xr) - Vr * th.h_vap(Tr, yr) + Q_reb - Q_rb_loss
        )
        r[b + 3] = xr - nr / max(Mr, 1e-12)
        r[b + 4] = Ur - Mr * th.h_liq(Tr, xr)
        r[b + 5] = Pr - th.bubble_pressure(Tr, xr)
        r[b + 6] = yr - th.y_light(Tr, xr, Pr)
        r[b + 7] = Vr - self._vapour_flow(Pr - P[-1])

        # Condenser capacity and drum.  The condensed stream is treated as
        # saturated/subcooled liquid at approximately the top-tray temperature.
        cap = self.condenser_capacity(state, u)
        Fcond = cap["condensed_flow_kmol_h"]
        Q_cond_used = Fcond * c.condenser_energy_per_kmol_kJ

        b = self.drum_offset
        Dout = Rflx + D + dr_leak
        r[b + 0] = state_dot[b + 0] - (Fcond - Dout)
        r[b + 1] = state_dot[b + 1] - (Fcond * yv[0] - Dout * xd)
        r[b + 2] = state_dot[b + 2] - (
            Fcond * th.h_vap(T[0], yv[0])
            - Dout * th.h_liq(Td, xd) - Q_cond_used - Q_dr_loss
        )
        r[b + 3] = xd - nd / max(Md, 1e-12)
        r[b + 4] = Ud - Md * th.h_liq(Td, xd)

        # One low-order pressure accumulator for the condenser/top section.
        r[self.pressure_offset] = state_dot[self.pressure_offset] - (
            (V[0] - Fcond) / c.vapour_capacitance_kmol_per_bar
        )
        return r

    def scaled_residual(self, t: float, y: np.ndarray, ydot: np.ndarray,
                        inputs: Optional[ColumnInputs] = None) -> np.ndarray:
        r = self.residual(t, y, ydot, inputs).copy()
        for i in range(self.cfg.n_trays):
            b = i * 9
            r[b + 0] /= 100.0
            r[b + 1] /= 100.0
            r[b + 2] /= 5.0e6
            r[b + 4] /= 1.0e4
            r[b + 5] /= 0.1
            r[b + 7] /= 100.0
            r[b + 8] /= 100.0
        b = self.reboiler_offset
        r[b + 0] /= 100.0; r[b + 1] /= 100.0; r[b + 2] /= 5.0e6
        r[b + 4] /= 1.0e5; r[b + 5] /= 0.1; r[b + 7] /= 100.0
        b = self.drum_offset
        r[b + 0] /= 100.0; r[b + 1] /= 100.0; r[b + 2] /= 5.0e6; r[b + 4] /= 1.0e5
        r[self.pressure_offset] /= 1.0
        return r

    def initial_guess(self, inputs: Optional[ColumnInputs] = None) -> np.ndarray:
        u = self.default_inputs() if inputs is None else inputs
        c, th, N = self.cfg, self.th, self.cfg.n_trays
        Y = np.zeros(self.n_variables)
        Pcond = c.condenser_pressure_bar
        x_guess = np.linspace(0.90, 0.08, N)
        for i, xi in enumerate(x_guess):
            Pi = Pcond + (i + 1) * c.nominal_stage_dp_bar
            Ti = th.bubble_temperature(xi, Pi)
            yi = th.y_light(Ti, xi, Pi)
            Mi = c.tray_holdup_nominal
            Y[i * 9 : (i + 1) * 9] = [
                Mi, Mi * xi, Mi * th.h_liq(Ti, xi), Ti, xi, yi, Pi,
                self.L_nominal[i], c.nominal_vapour_flow,
            ]

        xr = 0.04
        Pr = Pcond + (N + 1) * c.nominal_stage_dp_bar
        Tr = th.bubble_temperature(xr, Pr); yr = th.y_light(Tr, xr, Pr)
        Mr = c.reboiler_holdup_sp
        Y[self.reboiler_offset : self.reboiler_offset + 8] = [
            Mr, Mr * xr, Mr * th.h_liq(Tr, xr), Tr, xr, yr, Pr, c.nominal_vapour_flow
        ]

        xd = 0.94
        Td = th.bubble_temperature(xd, Pcond)
        Md = c.drum_holdup_sp
        Y[self.drum_offset : self.drum_offset + 5] = [Md, Md * xd, Md * th.h_liq(Td, xd), Td, xd]
        Y[self.pressure_offset] = Pcond
        return Y

    def solve_steady_state(self, inputs: Optional[ColumnInputs] = None,
                           guess: Optional[np.ndarray] = None, max_nfev: int = 5000,
                           pin_nominal_inventories: bool = True):
        """Steady-state/design initializer with inventory pins for the nominal design."""
        u = self.default_inputs() if inputs is None else inputs
        y0 = self.initial_guess(u) if guess is None else np.asarray(guess, dtype=float)
        z = np.zeros_like(y0)

        def fun(yy):
            rr = self.scaled_residual(0.0, yy, z, u)
            if not pin_nominal_inventories:
                return rr
            rb_M = yy[self.reboiler_offset + 0]
            dr_M = yy[self.drum_offset + 0]
            return np.concatenate([
                rr,
                [(rb_M - self.cfg.reboiler_holdup_sp) / 10.0],
                [(dr_M - self.cfg.drum_holdup_sp) / 10.0],
                [(yy[self.pressure_offset] - self.cfg.condenser_pressure_bar) / 0.1],
            ])

        return least_squares(
            fun, y0, x_scale="jac", max_nfev=max_nfev,
            ftol=1e-12, xtol=1e-12, gtol=1e-12,
        )

    def operating_summary(self, y: np.ndarray, inputs: Optional[ColumnInputs] = None) -> dict:
        u = self.default_inputs() if inputs is None else inputs
        rb = y[self.reboiler_offset : self.reboiler_offset + 8]
        dr = y[self.drum_offset : self.drum_offset + 5]
        top = self.tray(y, 0)
        T_steam = self.th.water_saturation_temperature(u.steam_pressure_bar)
        Q_reb = self.cfg.UA_reboiler * float(np.clip(u.steam_factor, 0.0, 1.0)) * self._positive(T_steam - rb[3])
        cap = self.condenser_capacity(y, u)
        return {
            "distillate_x_acetone": float(dr[4]),
            "bottoms_x_acetone": float(rb[4]),
            "top_tray_temperature_K": float(top[3]),
            "bottom_temperature_K": float(rb[3]),
            "condenser_pressure_bar": float(y[self.pressure_offset]),
            "top_pressure_bar": float(top[6]),
            "reboiler_pressure_bar": float(rb[6]),
            "reboiler_holdup_kmol": float(rb[0]),
            "drum_holdup_kmol": float(dr[0]),
            "distillate_flow_kmol_h": float(u.distillate_flow),
            "bottoms_flow_kmol_h": float(u.bottoms_flow),
            "reflux_flow_kmol_h": float(u.reflux_flow),
            "reboiler_leak_kmol_h": float(u.reboiler_leak_flow),
            "drum_leak_kmol_h": float(u.drum_leak_flow),
            "reboiler_duty_MW": float(Q_reb / 3.6e6),
            "condenser_available_duty_MW": float(cap["available_duty_kJ_h"] / 3.6e6),
            "condenser_capacity_kmol_h": float(cap["capacity_kmol_h"]),
            "condensed_flow_kmol_h": float(cap["condensed_flow_kmol_h"]),
        }
