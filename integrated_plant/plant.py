from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import numpy as np
from scipy.optimize import least_squares, root, OptimizeResult

from .thermo import ThermoSurrogate
from .cstr import CSTRDAE, CSTRConfig, CSTRInputs
from .distillation import DistillationDAE, ColumnConfig, ColumnInputs


@dataclass(frozen=True)
class PlantInputs:
    """Manipulated variables and exogenous inputs seen by the physical plant.

    v0.5 keeps the physical model independent of the control/safety/actor
    layers.  Requested, safety-modified and actual versions of this structure
    are handled by the episode runner.
    """

    fresh_ipa_feed: float = 100.0
    fresh_feed_temperature_K: float = 298.15
    catalyst_activity: float = 1.0

    cstr_outflow: float = 100.0
    cstr_heater_factor: float = 0.60
    cstr_vent_valve: float = 0.5       # normal PCV
    cstr_bdv_valve: float = 0.0        # emergency blowdown
    cstr_emergency_cooling_factor: float = 0.0

    reflux_flow: float = 80.0
    steam_factor: float = 1.0
    bottoms_flow: float = 26.797062506934168
    distillate_flow: float = 73.20293749306585

    # Condenser pressure-control final element and independent utility/equipment
    # effectiveness.  The valve is a true manipulated variable; effectiveness is
    # an exogenous/fault multiplier (e.g. low cooling-water pressure/fouling).
    condenser_cooling_valve: float = 0.60
    condenser_cooling_effectiveness: float = 1.0
    cooling_water_temperature_K: float = 298.15
    steam_pressure_bar: float = 5.659806566043441
    cstr_heating_utility_temperature_K: float = 390.0
    ambient_temperature_K: float = 298.15
    condenser_pressure_bar: float = 1.0  # nominal/reference retained for compatibility

    # Physical process-fault terms.  Zero in normal operation.
    cstr_liquid_leak_kmol_h: float = 0.0
    reboiler_liquid_leak_kmol_h: float = 0.0
    drum_liquid_leak_kmol_h: float = 0.0


class IntegratedPlantDAE:
    """Synthetic liquid CSTR + binary distillation column direct-residual DAE."""

    def __init__(
        self,
        thermo: ThermoSurrogate = ThermoSurrogate(),
        cstr_config: CSTRConfig = CSTRConfig(),
        column_config: ColumnConfig = ColumnConfig(),
    ):
        self.th = thermo
        self.cstr = CSTRDAE(cstr_config, thermo)
        self.column = DistillationDAE(column_config, thermo)
        self.column_offset = self.cstr.n_variables
        self.n_variables = self.cstr.n_variables + self.column.n_variables
        self.differential_mask = np.concatenate([
            self.cstr.differential_mask,
            self.column.differential_mask,
        ])

    def default_inputs(self) -> PlantInputs:
        cu = self.cstr.default_inputs()
        co = self.column.default_inputs()
        return PlantInputs(
            fresh_ipa_feed=cu.feed_flow,
            fresh_feed_temperature_K=cu.feed_temperature,
            catalyst_activity=cu.catalyst_activity,
            cstr_outflow=cu.liquid_outflow,
            cstr_heater_factor=cu.heater_factor,
            cstr_vent_valve=cu.vent_valve,
            cstr_bdv_valve=cu.bdv_valve,
            cstr_emergency_cooling_factor=cu.emergency_cooling_factor,
            reflux_flow=co.reflux_flow,
            steam_factor=co.steam_factor,
            bottoms_flow=co.bottoms_flow,
            distillate_flow=co.distillate_flow,
            condenser_cooling_valve=co.condenser_cooling_valve,
            condenser_cooling_effectiveness=co.condenser_cooling_effectiveness,
            cooling_water_temperature_K=co.cooling_water_temperature_K,
            steam_pressure_bar=co.steam_pressure_bar,
            cstr_heating_utility_temperature_K=cu.heating_utility_temperature_K,
            ambient_temperature_K=cu.ambient_temperature_K,
            condenser_pressure_bar=co.condenser_pressure_bar,
            cstr_liquid_leak_kmol_h=0.0,
            reboiler_liquid_leak_kmol_h=0.0,
            drum_liquid_leak_kmol_h=0.0,
        )

    def split_state(self, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return y[:self.column_offset], y[self.column_offset:]

    def _cstr_inputs(self, u: PlantInputs) -> CSTRInputs:
        return CSTRInputs(
            feed_flow=u.fresh_ipa_feed,
            liquid_outflow=u.cstr_outflow,
            heater_factor=u.cstr_heater_factor,
            vent_valve=u.cstr_vent_valve,
            bdv_valve=u.cstr_bdv_valve,
            emergency_cooling_factor=u.cstr_emergency_cooling_factor,
            feed_temperature=u.fresh_feed_temperature_K,
            heating_utility_temperature_K=u.cstr_heating_utility_temperature_K,
            cooling_water_temperature_K=u.cooling_water_temperature_K,
            ambient_temperature_K=u.ambient_temperature_K,
            catalyst_activity=u.catalyst_activity,
            liquid_leak_flow=u.cstr_liquid_leak_kmol_h,
        )

    def _column_inputs(self, u: PlantInputs, cstr_state: np.ndarray) -> ColumnInputs:
        return ColumnInputs(
            feed_flow=u.cstr_outflow,
            feed_z_light=float(cstr_state[self.cstr.XB]),
            feed_temperature=float(cstr_state[self.cstr.T]),
            reflux_flow=u.reflux_flow,
            steam_factor=u.steam_factor,
            condenser_cooling_valve=u.condenser_cooling_valve,
            condenser_cooling_effectiveness=u.condenser_cooling_effectiveness,
            cooling_water_temperature_K=u.cooling_water_temperature_K,
            steam_pressure_bar=u.steam_pressure_bar,
            ambient_temperature_K=u.ambient_temperature_K,
            condenser_pressure_bar=u.condenser_pressure_bar,
            bottoms_flow=u.bottoms_flow,
            distillate_flow=u.distillate_flow,
            reboiler_leak_flow=u.reboiler_liquid_leak_kmol_h,
            drum_leak_flow=u.drum_liquid_leak_kmol_h,
        )

    def ida_residual(self, t, y, ydot, result, inputs: Optional[PlantInputs] = None):
        result[:] = self.residual(t, y, ydot, inputs)
        return 0

    def residual(self, t: float, state: np.ndarray, state_dot: np.ndarray,
                 inputs: Optional[PlantInputs] = None) -> np.ndarray:
        u = self.default_inputs() if inputs is None else inputs
        yc, ycol = self.split_state(state)
        dc, dcol = self.split_state(state_dot)
        return np.concatenate([
            self.cstr.residual(t, yc, dc, self._cstr_inputs(u)),
            self.column.residual(t, ycol, dcol, self._column_inputs(u, yc)),
        ])

    def scaled_residual(self, t: float, state: np.ndarray, state_dot: np.ndarray,
                        inputs: Optional[PlantInputs] = None) -> np.ndarray:
        u = self.default_inputs() if inputs is None else inputs
        yc, ycol = self.split_state(state)
        dc, dcol = self.split_state(state_dot)
        return np.concatenate([
            self.cstr.scaled_residual(t, yc, dc, self._cstr_inputs(u)),
            self.column.scaled_residual(t, ycol, dcol, self._column_inputs(u, yc)),
        ])

    def nominal_state(self, inputs: Optional[PlantInputs] = None) -> np.ndarray:
        """Construct the controlled nominal steady state used to reset episodes."""
        u = self.default_inputs() if inputs is None else inputs
        cu = self._cstr_inputs(u)
        yc = self.cstr.initial_guess(cu)
        colu = self._column_inputs(u, yc)
        col_ss = self.column.solve_steady_state(colu, pin_nominal_inventories=True)
        if not col_ss.success:
            raise RuntimeError(f"Column nominal steady state failed: {col_ss.message}")
        return np.concatenate([yc, col_ss.x])

    def initial_guess(self, inputs: Optional[PlantInputs] = None) -> np.ndarray:
        return self.nominal_state(inputs)

    def solve_open_loop_steady_state(self, inputs: Optional[PlantInputs] = None,
                                     guess: Optional[np.ndarray] = None,
                                     max_nfev: int = 6000):
        u = self.default_inputs() if inputs is None else inputs
        y0 = self.initial_guess(u) if guess is None else np.asarray(guess, dtype=float)
        z = np.zeros_like(y0)
        return least_squares(
            lambda yy: self.scaled_residual(0.0, yy, z, u),
            y0, x_scale="jac", max_nfev=max_nfev,
            ftol=1e-12, xtol=1e-12, gtol=1e-12,
        )

    def backward_euler_step(self, y_old: np.ndarray, dt_h: float,
                            inputs: Optional[PlantInputs] = None,
                            guess: Optional[np.ndarray] = None,
                            max_nfev: int = 1200):
        """Transparent implicit stepper used by the prototype environment."""
        u = self.default_inputs() if inputs is None else inputs
        y_old = np.asarray(y_old, dtype=float)
        y0 = y_old.copy() if guess is None else np.asarray(guess, dtype=float)
        mask = self.differential_mask

        def fun(y_new):
            ydot = np.zeros_like(y_new)
            ydot[mask] = (y_new[mask] - y_old[mask]) / dt_h
            return self.scaled_residual(0.0, y_new, ydot, u)

        f0 = fun(y0)
        if np.linalg.norm(f0) < 1e-10:
            return OptimizeResult(x=y0.copy(), fun=f0, success=True, message="implicit step already satisfied")

        sol = root(fun, y0, method="hybr", options={"xtol": 1e-9, "maxfev": max_nfev * 4})
        if sol.success and np.linalg.norm(sol.fun) < 1e-6:
            return sol

        return least_squares(
            fun, sol.x if np.all(np.isfinite(sol.x)) else y0,
            x_scale="jac", max_nfev=max_nfev,
            ftol=1e-10, xtol=1e-10, gtol=1e-10,
        )

    def measurements(self, y: np.ndarray) -> dict[str, float]:
        yc, ycol = self.split_state(y)
        rb = ycol[self.column.reboiler_offset : self.column.reboiler_offset + 8]
        dr = ycol[self.column.drum_offset : self.column.drum_offset + 5]
        top = self.column.tray(ycol, 0)
        m = {
            "cstr_holdup_kmol": float(yc[self.cstr.M]),
            "cstr_temperature_K": float(yc[self.cstr.T]),
            "cstr_pressure_bar": float(yc[self.cstr.P]),
            "cstr_x_acetone": float(yc[self.cstr.XB]),
            "cstr_h2_vent_kmol_h": float(yc[self.cstr.FVENT]),
            "reboiler_holdup_kmol": float(rb[0]),
            "drum_holdup_kmol": float(dr[0]),
            "top_temperature_K": float(top[3]),
            "bottom_temperature_K": float(rb[3]),
            "column_pressure_bar": float(ycol[self.column.pressure_offset]),
            "top_pressure_bar": float(top[6]),
            "reboiler_pressure_bar": float(rb[6]),
            "distillate_x_acetone": float(dr[4]),
            "bottoms_x_acetone": float(rb[4]),
        }
        for i in range(self.column.cfg.n_trays):
            tr = self.column.tray(ycol, i)
            m[f"tray_{i+1:02d}_temperature_K"] = float(tr[self.column.TRAY_T])
            m[f"tray_{i+1:02d}_pressure_bar"] = float(tr[self.column.TRAY_P])
        return m

    def operating_summary(self, y: np.ndarray, inputs: Optional[PlantInputs] = None) -> dict:
        u = self.default_inputs() if inputs is None else inputs
        yc, ycol = self.split_state(y)
        cu = self._cstr_inputs(u)
        colu = self._column_inputs(u, yc)
        ans = {f"cstr.{k}": v for k, v in self.cstr.operating_summary(yc, cu).items()}
        ans.update({f"column.{k}": v for k, v in self.column.operating_summary(ycol, colu).items()})
        return ans
