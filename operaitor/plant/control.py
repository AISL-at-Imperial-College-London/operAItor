from __future__ import annotations

from dataclasses import dataclass, replace
import os

from .plant import IntegratedPlantDAE, PlantInputs


@dataclass
class PIController:
    """Sampled PI controller with AUTO/MANUAL, clamping and tracking.

    ``filter_h`` > 0 smooths the measured PV with a first-order filter (time constant in hours) before the error is
    formed, so a small deadband does not make the output chase sensor noise; 0 uses the raw measurement.
    """

    kp: float
    ti_h: float
    bias: float
    u_min: float
    u_max: float
    integral: float = 0.0
    deadband: float = 0.0
    mode: str = "AUTO"
    manual_output: float | None = None
    last_output: float | None = None
    filter_h: float = 0.0
    pv_filtered: float | None = None

    def reset(self) -> None:
        self.integral = 0.0
        self.pv_filtered = None
        self.mode = "AUTO"
        self.manual_output = None
        self.last_output = float(self.bias)

    def _error(self, sp: float, pv: float) -> float:
        e = sp - pv
        return 0.0 if abs(e) <= self.deadband else e

    def track(self, output: float, sp: float, pv: float) -> float:
        """Back-calculate the integral so AUTO/MANUAL transfer is bumpless."""
        out = min(max(float(output), self.u_min), self.u_max)
        e = self._error(sp, pv)
        self.integral = out - self.bias - self.kp * e
        self.last_output = out
        return out

    def set_mode(self, mode: str) -> None:
        mode = str(mode).upper()
        if mode not in ("AUTO", "MANUAL"):
            raise ValueError("mode must be AUTO or MANUAL")
        if mode == "MANUAL" and self.mode != "MANUAL":
            self.manual_output = self.last_output if self.last_output is not None else self.bias
        self.mode = mode

    def set_manual_output(self, value: float) -> None:
        value = float(value)
        if not self.u_min <= value <= self.u_max:
            raise ValueError(f"manual output {value:g} outside [{self.u_min:g}, {self.u_max:g}]")
        self.manual_output = value

    def _filter(self, pv: float, dt_h: float) -> float:
        if self.filter_h <= 0.0:
            return pv
        if self.pv_filtered is None:
            self.pv_filtered = float(pv)
        else:
            self.pv_filtered += dt_h / (self.filter_h + dt_h) * (float(pv) - self.pv_filtered)
        return self.pv_filtered

    def output(self, sp: float, pv: float, dt_h: float) -> float:
        pv = self._filter(pv, dt_h)
        if self.mode == "MANUAL":
            out = self.bias if self.manual_output is None else float(self.manual_output)
            return self.track(out, sp, pv)

        e = self._error(sp, pv)
        p = self.kp * e
        candidate_i = self.integral
        if self.ti_h > 0.0:
            candidate_i = self.integral + self.kp / self.ti_h * e * dt_h
        raw = self.bias + p + candidate_i
        clipped = min(max(raw, self.u_min), self.u_max)

        if raw == clipped:
            self.integral = candidate_i
        elif raw > self.u_max and self.kp * e < 0.0:
            self.integral = candidate_i
        elif raw < self.u_min and self.kp * e > 0.0:
            self.integral = candidate_i
        self.last_output = clipped
        return clipped


@dataclass(frozen=True)
class ControlSetpoints:
    cstr_holdup_kmol: float
    cstr_temperature_K: float
    cstr_pressure_bar: float
    reboiler_holdup_kmol: float
    drum_holdup_kmol: float
    column_pressure_bar: float
    top_temperature_K: float
    bottom_temperature_K: float


@dataclass(frozen=True)
class LoopAccessSpec:
    loop_id: str
    controller_attr: str
    setpoint_attr: str
    pv_tag: str
    mv_field: str
    sp_min: float
    sp_max: float
    sp_unit: str
    mv_unit: str


class RegulatoryControlSystem:
    """Regulatory control with explicit operator access points."""

    LOOP_SPECS: dict[str, LoopAccessSpec] = {
        "R_LIC": LoopAccessSpec("R_LIC", "cstr_level", "cstr_holdup_kmol", "cstr_holdup_kmol", "cstr_outflow", 40.0, 60.0, "kmol", "kmol/h"),
        "R_TIC": LoopAccessSpec("R_TIC", "cstr_temp", "cstr_temperature_K", "cstr_temperature_K", "cstr_heater_factor", 325.0, 340.0, "K", "fraction"),
        "R_PIC": LoopAccessSpec("R_PIC", "cstr_pressure", "cstr_pressure_bar", "cstr_pressure_bar", "cstr_vent_valve", 2.5, 3.4, "bar", "fraction"),
        "RB_LIC": LoopAccessSpec("RB_LIC", "reboiler_level", "reboiler_holdup_kmol", "reboiler_holdup_kmol", "bottoms_flow", 18.0, 46.0, "kmol", "kmol/h"),
        "DR_LIC": LoopAccessSpec("DR_LIC", "drum_level", "drum_holdup_kmol", "drum_holdup_kmol", "distillate_flow", 16.0, 40.0, "kmol", "kmol/h"),
        "COL_PIC": LoopAccessSpec("COL_PIC", "column_pressure", "column_pressure_bar", "column_pressure_bar", "condenser_cooling_valve", 0.90, 1.12, "bar", "fraction"),
        "TOP_TIC": LoopAccessSpec("TOP_TIC", "top_temp", "top_temperature_K", "top_temperature_K", "reflux_flow", 325.0, 340.0, "K", "kmol/h"),
        "BOT_TIC": LoopAccessSpec("BOT_TIC", "bottom_temp", "bottom_temperature_K", "bottom_temperature_K", "steam_factor", 345.0, 365.0, "K", "fraction"),
    }

    # PI tuning per loop: (gain kp, integral time ti_h [h], deadband [PV units], PV filter time constant [h]).
    # "v1.1" is the tuning of every run up to release 1.1 (PLANT_TUNING=v1.1 reproduces them); "v1.2" is the default.
    TUNINGS: dict[str, dict[str, tuple[float, float, float, float]]] = {
        "v1.1": {
            "cstr_level": (-6.0, 2.0, 0.50, 0.0),
            "cstr_temp": (+0.030, 0.12, 0.30, 0.0),
            "cstr_pressure": (-0.10, 0.30, 0.015, 0.0),
            "reboiler_level": (-6.0, 2.0, 0.35, 0.0),
            "drum_level": (-12.0, 2.0, 0.30, 0.0),
            "column_pressure": (-4.5, 0.20, 0.002, 0.0),
            "top_temp": (-7.0, 0.15, 0.30, 0.0),
            "bottom_temp": (+0.0325, 0.15, 0.30, 0.0),
        },
    }
    # v1.2: operaitor.benchmark.tune_loops (benchmark_results/loop_tuning*.json): better tracking on the 2-h setpoint
    # test, accepted only where no benchmark fault scenario trips earlier than with v1.1. The PV filter lets the
    # deadband shrink to about two standard deviations of the filtered sensor noise.
    TUNINGS["v1.2"] = {
        "cstr_level": (-6, 8, 0.2236, 2 / 60),
        "cstr_temp": (+0.06, 0.24, 0.1, 4 / 60),
        "cstr_pressure": (-0.1, 0.3, 0.005, 4 / 60),
        "reboiler_level": (-12, 4, 0.1543, 2 / 60),
        "drum_level": (-12, 4, 0.3, 0.0),
        "column_pressure": (-4.5, 0.4, 0.002236, 2 / 60),
        "top_temp": (-10.5, 0.3, 0.1, 4 / 60),
        "bottom_temp": (+0.0975, 0.15, 0.1342, 2 / 60),
    }
    TUNING: dict[str, tuple[float, float, float, float]] = TUNINGS[os.environ.get("PLANT_TUNING", "v1.2")]

    def __init__(self, setpoints: ControlSetpoints, nominal: PlantInputs):
        self.sp = setpoints
        self._initial_sp = setpoints
        self.nominal = nominal

        t = self.TUNING
        def pi(name: str, bias: float, u_max: float) -> PIController:
            kp, ti_h, deadband, filter_h = t[name]
            return PIController(kp, ti_h, bias, 0.0, u_max, deadband=deadband, filter_h=filter_h)
        self.cstr_level = pi("cstr_level", nominal.cstr_outflow, 160.0)
        self.cstr_temp = pi("cstr_temp", nominal.cstr_heater_factor, 1.0)
        self.cstr_pressure = pi("cstr_pressure", nominal.cstr_vent_valve, 1.0)
        self.reboiler_level = pi("reboiler_level", nominal.bottoms_flow, 90.0)
        self.drum_level = pi("drum_level", nominal.distillate_flow, 130.0)
        self.column_pressure = pi("column_pressure", nominal.condenser_cooling_valve, 1.0)
        self.top_temp = pi("top_temp", nominal.reflux_flow, 180.0)
        self.bottom_temp = pi("bottom_temp", nominal.steam_factor, 1.0)
        self.reset()

    @classmethod
    def from_steady_state(cls, plant: IntegratedPlantDAE, y_ss, nominal: PlantInputs):
        m = plant.measurements(y_ss)
        sp = ControlSetpoints(
            cstr_holdup_kmol=m["cstr_holdup_kmol"],
            cstr_temperature_K=m["cstr_temperature_K"],
            cstr_pressure_bar=m["cstr_pressure_bar"],
            reboiler_holdup_kmol=m["reboiler_holdup_kmol"],
            drum_holdup_kmol=m["drum_holdup_kmol"],
            column_pressure_bar=m["column_pressure_bar"],
            top_temperature_K=m["top_temperature_K"],
            bottom_temperature_K=m["bottom_temperature_K"],
        )
        return cls(sp, nominal)

    def reset(self) -> None:
        self.sp = self._initial_sp
        for obj in vars(self).values():
            if isinstance(obj, PIController):
                obj.reset()

    def _spec(self, loop_id: str) -> LoopAccessSpec:
        key = str(loop_id).upper()
        if key not in self.LOOP_SPECS:
            raise ValueError(f"unknown controller loop '{loop_id}'")
        return self.LOOP_SPECS[key]

    def set_setpoint(self, loop_id: str, value: float) -> None:
        spec = self._spec(loop_id)
        value = float(value)
        if not spec.sp_min <= value <= spec.sp_max:
            raise ValueError(f"{spec.loop_id} setpoint {value:g} outside [{spec.sp_min:g}, {spec.sp_max:g}] {spec.sp_unit}")
        self.sp = replace(self.sp, **{spec.setpoint_attr: value})

    def set_mode(self, loop_id: str, mode: str) -> None:
        spec = self._spec(loop_id)
        getattr(self, spec.controller_attr).set_mode(mode)

    def set_manual_output(self, loop_id: str, value: float) -> None:
        spec = self._spec(loop_id)
        ctrl: PIController = getattr(self, spec.controller_attr)
        if ctrl.mode != "MANUAL":
            raise ValueError(f"{spec.loop_id} must be in MANUAL before setting manual output")
        ctrl.set_manual_output(value)

    def access_snapshot(self, measurements: dict[str, float] | None = None) -> dict[str, dict[str, object]]:
        out: dict[str, dict[str, object]] = {}
        for loop_id, spec in self.LOOP_SPECS.items():
            ctrl: PIController = getattr(self, spec.controller_attr)
            row = {
                "mode": ctrl.mode,
                "setpoint": float(getattr(self.sp, spec.setpoint_attr)),
                "manual_output": None if ctrl.manual_output is None else float(ctrl.manual_output),
                "last_output": float(ctrl.last_output if ctrl.last_output is not None else ctrl.bias),
                "sp_min": spec.sp_min,
                "sp_max": spec.sp_max,
                "sp_unit": spec.sp_unit,
                "mv_min": ctrl.u_min,
                "mv_max": ctrl.u_max,
                "mv_unit": spec.mv_unit,
                "pv_tag": spec.pv_tag,
                "mv_field": spec.mv_field,
            }
            if measurements is not None and spec.pv_tag in measurements:
                row["pv"] = float(measurements[spec.pv_tag])
            out[loop_id] = row
        return out

    def update(self, measurements: dict[str, float], dt_h: float,
               base_inputs: PlantInputs | None = None,
               throughput_setpoint: float | None = None) -> PlantInputs:
        u = self.nominal if base_inputs is None else base_inputs
        feed = u.fresh_ipa_feed if throughput_setpoint is None else throughput_setpoint

        return replace(
            u,
            fresh_ipa_feed=feed,
            cstr_outflow=self.cstr_level.output(self.sp.cstr_holdup_kmol, measurements["cstr_holdup_kmol"], dt_h),
            cstr_heater_factor=self.cstr_temp.output(self.sp.cstr_temperature_K, measurements["cstr_temperature_K"], dt_h),
            cstr_vent_valve=self.cstr_pressure.output(self.sp.cstr_pressure_bar, measurements["cstr_pressure_bar"], dt_h),
            bottoms_flow=self.reboiler_level.output(self.sp.reboiler_holdup_kmol, measurements["reboiler_holdup_kmol"], dt_h),
            distillate_flow=self.drum_level.output(self.sp.drum_holdup_kmol, measurements["drum_holdup_kmol"], dt_h),
            condenser_cooling_valve=self.column_pressure.output(self.sp.column_pressure_bar, measurements["column_pressure_bar"], dt_h),
            reflux_flow=self.top_temp.output(self.sp.top_temperature_K, measurements["top_temperature_K"], dt_h),
            steam_factor=self.bottom_temp.output(self.sp.bottom_temperature_K, measurements["bottom_temperature_K"], dt_h),
        )
