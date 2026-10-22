"""The plant's equations in CasADi, for a faster implicit step with exact Jacobians.

This mirrors the residuals of cstr.py, distillation.py and thermo.py (the SciPy model, kept as the reference
solver) term by term: max/min/clip become fmax/fmin, which take the same values, so nothing is smoothed and the
physics are identical. tests/test_casadi_model.py checks that both give the same residuals; change the physics in
both places.

Each 0.1-min backward-Euler step solves G(y_new) = scaled_residual(y_new, (y_new - y_old) / dt) = 0 with CasADi's
Newton rootfinder; the step halving and the SciPy least-squares last resort of IntegratedPlantDAE.adaptive_step are
kept. Quantities that depend only on the inputs (clipped valve positions, the steam saturation temperature, the
fresh-feed enthalpy) are computed by the SciPy model's own code and passed in as parameters.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import OptimizeResult

import casadi as ca

from .cstr import R_BAR, R_KJ
from .thermo import T_REF

PARAMS = ("feed", "Fliq_total", "heater", "T_hot", "T_cw", "T_amb", "em_factor", "vent", "bdv", "h_feed", "catalyst",
          "col_feed", "reflux", "B", "D", "rb_leak", "dr_leak", "steam", "T_steam", "cond_valve", "cond_eff", "P_ref")


def _pos(a, eps: float = 1e-10):
    return 0.5 * (a + ca.sqrt(a * a + eps * eps))


class CasadiPlant:
    """Symbolic residual and Newton stepper for an IntegratedPlantDAE (same configuration and thermodynamics)."""

    def __init__(self, plant, abstol: float = 1e-10, max_iter: int = 30):
        self.plant = plant
        self.n = plant.n_variables
        self._build(abstol, max_iter)

    # -- parameters: the inputs, preprocessed exactly as the SciPy residuals do -----------------------------------
    def params(self, u) -> np.ndarray:
        p, th = self.plant, self.plant.th
        cc = p.column.cfg
        clip01 = lambda v: min(max(v, 0.0), 1.0)  # noqa: E731
        vals = {
            "feed": u.fresh_ipa_feed, "Fliq_total": u.cstr_outflow + max(u.cstr_liquid_leak_kmol_h, 0.0),
            "heater": u.cstr_heater_factor, "T_hot": u.cstr_heating_utility_temperature_K,
            "T_cw": u.cooling_water_temperature_K, "T_amb": u.ambient_temperature_K,
            "em_factor": u.cstr_emergency_cooling_factor, "vent": clip01(u.cstr_vent_valve), "bdv": clip01(u.cstr_bdv_valve),
            "h_feed": th.h_liq(u.fresh_feed_temperature_K, 0.0), "catalyst": u.catalyst_activity,
            "col_feed": u.cstr_outflow, "reflux": u.reflux_flow, "B": u.bottoms_flow, "D": u.distillate_flow,
            "rb_leak": max(u.reboiler_liquid_leak_kmol_h, 0.0), "dr_leak": max(u.drum_liquid_leak_kmol_h, 0.0),
            "steam": float(np.clip(u.steam_factor, 0.0, 1.0)), "T_steam": th.water_saturation_temperature(u.steam_pressure_bar),
            "cond_valve": float(np.clip(u.condenser_cooling_valve, 0.0, 1.0)),
            "cond_eff": max(float(u.condenser_cooling_effectiveness), 0.0), "P_ref": cc.condenser_pressure_bar,
        }
        return np.array([float(vals[k]) for k in PARAMS])

    # -- thermodynamics (thermo.py) -------------------------------------------------------------------------------
    def _psat(self, T):
        th = self.plant.th
        return ca.exp(th.A_psat[0] - th.B_psat[0] / T), ca.exp(th.A_psat[1] - th.B_psat[1] / T)

    def _gamma(self, x):
        th = self.plant.th
        x1 = ca.fmin(ca.fmax(x, 1e-10), 1.0 - 1e-10)
        x2 = 1.0 - x1
        lng1 = (th.A12 + 2.0 * (th.A21 - th.A12) * x1) * x2**2
        lng2 = (th.A21 + 2.0 * (th.A12 - th.A21) * x2) * x1**2
        return ca.exp(lng1), ca.exp(lng2)

    def _bubble_p(self, T, x):
        g1, g2 = self._gamma(x)
        p1, p2 = self._psat(T)
        return x * g1 * p1 + (1.0 - x) * g2 * p2

    def _y_light(self, T, x, P):
        g1, _ = self._gamma(x)
        p1, _ = self._psat(T)
        return x * g1 * p1 / P

    def _h_liq(self, T, x):
        cp = self.plant.th.cp_liq
        return (x * cp[0] + (1.0 - x) * cp[1]) * (T - T_REF)

    def _h_vap(self, T, y):
        th = self.plant.th
        return (y * th.cp_vap[0] + (1.0 - y) * th.cp_vap[1]) * (T - T_REF) + (y * th.dh_vap[0] + (1.0 - y) * th.dh_vap[1])

    # -- CSTR (cstr.py) -------------------------------------------------------------------------------------------
    def _cstr(self, y, yd, q):
        cs = self.plant.cstr
        c = cs.cfg
        M, nB, U, nH2, T, xB, P, rate, Fvent = (y[i] for i in range(9))
        Qj = c.UA_jacket_kJ_hK * q["heater"] * _pos(q["T_hot"] - T)
        Qloss = c.UA_heat_loss_kJ_hK * _pos(T - q["T_amb"])
        Qem = c.UA_emergency_cooling_kJ_hK * q["em_factor"] * _pos(T - q["T_cw"])
        h_out = self._h_liq(T, xB)
        h2_out = c.cp_h2_kJ_kmolK * (T - T_REF)
        u_h2 = (c.cp_h2_kJ_kmolK - R_KJ) * (T - T_REF)
        F = q["Fliq_total"]
        Vg = ca.fmax(c.total_vessel_volume_m3 - c.liquid_molar_volume_m3_kmol * M, 1e-5) + max(c.connected_h2_gas_volume_m3, 0.0)
        k = c.k_ref_h * ca.exp(-c.activation_energy_kJ_kmol / R_KJ * (1.0 / T - 1.0 / c.T_kin_ref))
        dp = _pos(P - c.vent_backpressure_bar)
        root_dp = ca.sqrt(dp + 1e-14)
        span = max(c.psv_full_open_pressure_bar - c.psv_set_pressure_bar, 1e-6)
        psv_open = ca.fmin(ca.fmax(_pos(P - c.psv_set_pressure_bar) / span, 0.0), 1.0)
        pcv = q["vent"] * c.vent_Cv_kmol_h_sqrtbar * root_dp
        bdv = q["bdv"] * c.bdv_Cv_kmol_h_sqrtbar * root_dp
        vent = pcv + bdv + psv_open * c.psv_Cv_kmol_h_sqrtbar * root_dp
        r = [
            yd[0] - (q["feed"] - F),
            yd[1] - (rate - F * xB),
            yd[2] - (q["feed"] * q["h_feed"] - F * h_out - Fvent * h2_out + Qj - Qloss - Qem - rate * c.reaction_enthalpy_kJ_kmol),
            yd[3] - (rate - Fvent),
            xB - nB / ca.fmax(M, 1e-12),
            U - (M * self._h_liq(T, xB) + nH2 * u_h2),
            P - nH2 * R_BAR * T / Vg,
            rate - q["catalyst"] * k * _pos(M - nB),
            Fvent - vent,
        ]
        scale = [100.0, 100.0, 6.0e6, 100.0, 1.0, 1.0e5, 1.0, 100.0, 100.0]
        return [ri / s for ri, s in zip(r, scale)]

    # -- column (distillation.py) ---------------------------------------------------------------------------------
    def _column(self, y, yd, q, z_feed, T_feed):
        col = self.plant.column
        c, N = col.cfg, col.cfg.n_trays
        tr = [[y[9 * i + j] for j in range(9)] for i in range(N)]
        M, n, U, T, x, yv, P, L, V = (list(v) for v in zip(*tr))
        ro, do, po = col.reboiler_offset, col.drum_offset, col.pressure_offset
        Mr, nr, Ur, Tr, xr, yr, Pr, Vr = (y[ro + j] for j in range(8))
        Md, nd, Ud, Td, xd = (y[do + j] for j in range(5))
        Pcond = y[po]
        vflow = lambda dP: col.Cv_vap * ca.sqrt(_pos(dP) + 1e-14)  # noqa: E731
        Q_reb = c.UA_reboiler * q["steam"] * _pos(q["T_steam"] - Tr)
        Q_rb_loss = c.UA_reboiler_loss_kJ_hK * _pos(Tr - q["T_amb"])
        Q_dr_loss = c.UA_drum_loss_kJ_hK * _pos(Td - q["T_amb"])
        h_feed = self._h_liq(T_feed, z_feed)
        out, scale = [], []
        for i in range(N):
            b = 9 * i
            if i == 0:
                Lin, xLin, hLin = q["reflux"], xd, self._h_liq(Td, xd)
            else:
                Lin, xLin, hLin = L[i - 1], x[i - 1], self._h_liq(T[i - 1], x[i - 1])
            if i == N - 1:
                Vin, yVin, hVin = Vr, yr, self._h_vap(Tr, yr)
            else:
                Vin, yVin, hVin = V[i + 1], yv[i + 1], self._h_vap(T[i + 1], yv[i + 1])
            Fi = q["col_feed"] if i == c.feed_stage else 0.0
            L_model = col.L_nominal[i] * (_pos(M[i]) / c.tray_holdup_nominal) ** c.liquid_hydraulic_exponent
            P_above = Pcond if i == 0 else P[i - 1]
            out += [
                yd[b + 0] - (Lin + Vin + Fi - L[i] - V[i]),
                yd[b + 1] - (Lin * xLin + Vin * yVin + Fi * z_feed - L[i] * x[i] - V[i] * yv[i]),
                yd[b + 2] - (Lin * hLin + Vin * hVin + Fi * h_feed - L[i] * self._h_liq(T[i], x[i]) - V[i] * self._h_vap(T[i], yv[i])),
                x[i] - n[i] / ca.fmax(M[i], 1e-12),
                U[i] - M[i] * self._h_liq(T[i], x[i]),
                P[i] - self._bubble_p(T[i], x[i]),
                yv[i] - self._y_light(T[i], x[i], P[i]),
                L[i] - L_model,
                V[i] - vflow(P[i] - P_above),
            ]
            scale += [100.0, 100.0, 5.0e6, 1.0, 1.0e4, 0.1, 1.0, 100.0, 100.0]
        Btot = q["B"] + q["rb_leak"]
        out += [
            yd[ro + 0] - (L[-1] - Btot - Vr),
            yd[ro + 1] - (L[-1] * x[-1] - Btot * xr - Vr * yr),
            yd[ro + 2] - (L[-1] * self._h_liq(T[-1], x[-1]) - Btot * self._h_liq(Tr, xr) - Vr * self._h_vap(Tr, yr) + Q_reb - Q_rb_loss),
            xr - nr / ca.fmax(Mr, 1e-12),
            Ur - Mr * self._h_liq(Tr, xr),
            Pr - self._bubble_p(Tr, xr),
            yr - self._y_light(Tr, xr, Pr),
            Vr - vflow(Pr - P[-1]),
        ]
        scale += [100.0, 100.0, 5.0e6, 1.0, 1.0e5, 0.1, 1.0, 100.0]
        Qavail = c.UA_condenser * q["cond_valve"] * q["cond_eff"] * _pos(T[0] - q["T_cw"])
        capacity = ca.fmax(Qavail / c.condenser_energy_per_kmol_kJ, 0.0)
        demand = _pos(V[0] + c.pressure_recovery_gain_kmol_h_per_bar * (Pcond - q["P_ref"]))
        Fcond = ca.fmax(ca.fmin(capacity, demand), 0.0)
        Dout = q["reflux"] + q["D"] + q["dr_leak"]
        out += [
            yd[do + 0] - (Fcond - Dout),
            yd[do + 1] - (Fcond * yv[0] - Dout * xd),
            yd[do + 2] - (Fcond * self._h_vap(T[0], yv[0]) - Dout * self._h_liq(Td, xd) - Fcond * c.condenser_energy_per_kmol_kJ - Q_dr_loss),
            xd - nd / ca.fmax(Md, 1e-12),
            Ud - Md * self._h_liq(Td, xd),
            yd[po] - (V[0] - Fcond) / c.vapour_capacitance_kmol_per_bar,
        ]
        scale += [100.0, 100.0, 5.0e6, 1.0, 1.0e5, 1.0]
        return [ri / s for ri, s in zip(out, scale)]

    def _residual(self, y, yd, p):
        q = {k: p[i] for i, k in enumerate(PARAMS)}
        off = self.plant.column_offset
        cstr = self.plant.cstr
        r = self._cstr(y[:off], yd[:off], q)
        r += self._column(y[off:], yd[off:], q, z_feed=y[cstr.XB], T_feed=y[cstr.T])
        return ca.vertcat(*r)

    def _build(self, abstol: float, max_iter: int) -> None:
        n, npar = self.n, len(PARAMS)
        y, yd, p = ca.SX.sym("y", n), ca.SX.sym("yd", n), ca.SX.sym("p", npar)
        self.scaled_residual_fn = ca.Function("scaled_residual", [y, yd, p], [self._residual(y, yd, p)])
        y_old, dt = ca.SX.sym("y_old", n), ca.SX.sym("dt")
        mask = self.plant.differential_mask
        ydot = ca.vertcat(*[(y[i] - y_old[i]) / dt if mask[i] else 0 for i in range(n)])
        G = ca.Function("G", [y, ca.vertcat(y_old, dt, p)], [self._residual(y, ydot, p)])
        self.step_residual = G
        self.rootfinder = ca.rootfinder("be_step", "newton", G, {"abstol": abstol, "max_iter": max_iter,
                                                                  "error_on_fail": False})

    # -- stepping --------------------------------------------------------------------------------------------------
    def scaled_residual(self, y: np.ndarray, ydot: np.ndarray, u) -> np.ndarray:
        return np.asarray(self.scaled_residual_fn(y, ydot, self.params(u))).ravel()

    def newton_step(self, y_old: np.ndarray, dt_h: float, u, guess: np.ndarray | None = None) -> OptimizeResult:
        pv = np.concatenate([y_old, [dt_h], self.params(u)])
        x0 = y_old if guess is None else guess
        try:
            x = np.asarray(self.rootfinder(x0, pv)).ravel()
        except RuntimeError as e:   # a NaN/inf in the evaluation: let the caller halve the step
            return OptimizeResult(x=np.full(self.n, np.nan), fun=np.full(self.n, np.inf), success=False, message=str(e))
        f = np.asarray(self.step_residual(x, pv)).ravel()
        ok = bool(self.rootfinder.stats().get("success", False)) and bool(np.all(np.isfinite(x)))
        return OptimizeResult(x=x, fun=f, success=ok, message="newton" if ok else "newton did not converge")
