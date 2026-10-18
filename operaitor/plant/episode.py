from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import numpy as np

from .actions import ActionResult, PassiveActor, SupervisoryAction
from .alarms import AlarmManager, default_alarm_definitions
from .runtime import RuntimeInputEngine
from .control import RegulatoryControlSystem
from .equipment import PumpFaultEngine, PumpSystem
from .measurement import MeasurementModel, SensorFaultEngine, default_sensor_specs
from .outcome import EpisodeStatus, OutcomeConfig, OutcomeEvaluator, OutcomeState
from .plant import IntegratedPlantDAE, PlantInputs
from .safety import SafetyConfig, SafetyResult, SafetySystem
from .scenario import ActuatorFaultEngine, Scenario


@dataclass(frozen=True)
class EpisodeSpec:
    scenario: Scenario
    actuator_faults: ActuatorFaultEngine = field(default_factory=ActuatorFaultEngine)
    sensor_faults: SensorFaultEngine = field(default_factory=SensorFaultEngine)
    pump_faults: PumpFaultEngine = field(default_factory=PumpFaultEngine)
    name: str = "episode"
    allow_recovery_termination: bool = True
    terminate_on_plant_trip: bool = True
    horizon_is_completion: bool = False


@dataclass(frozen=True)
class EpisodeTiming:
    plant_step_min: float = 0.1
    decision_interval_min: float = 5.0
    control_interval_min: float = 1.0

    @property
    def plant_step_h(self) -> float:
        return self.plant_step_min / 60.0

    @property
    def control_interval_h(self) -> float:
        return self.control_interval_min / 60.0

    def strides(self) -> tuple[int, int]:
        if self.plant_step_min <= 0.0:
            raise ValueError("plant_step_min must be positive")
        n_dec = int(round(self.decision_interval_min / self.plant_step_min))
        n_ctl = int(round(self.control_interval_min / self.plant_step_min))
        if n_dec < 1 or not np.isclose(n_dec * self.plant_step_min, self.decision_interval_min):
            raise ValueError("decision_interval_min must be an integer multiple of plant_step_min")
        if n_ctl < 1 or not np.isclose(n_ctl * self.plant_step_min, self.control_interval_min):
            raise ValueError("control_interval_min must be an integer multiple of plant_step_min")
        return n_dec, n_ctl


class EpisodeRunner:
    """Stateful operator/agent environment around the DAE plant."""

    AUXILIARY_BOUNDS = {
        "H2_BDV": (0.0, 1.0),
        "EMERGENCY_COOLING": (0.0, 1.0),
    }
    THROUGHPUT_BOUNDS = (0.0, 150.0)

    def __init__(
        self,
        plant: IntegratedPlantDAE,
        spec: EpisodeSpec,
        nominal_inputs: PlantInputs | None = None,
        y0: np.ndarray | None = None,
        controllers: RegulatoryControlSystem | None = None,
        measurement_model: MeasurementModel | None = None,
        safety_system: SafetySystem | None = None,
        timing: EpisodeTiming = EpisodeTiming(),
        safety_config: SafetyConfig = SafetyConfig(),
        outcome_config: OutcomeConfig = OutcomeConfig(),
        alarm_manager: AlarmManager | None = None,
        runtime_inputs: RuntimeInputEngine | None = None,
    ):
        self.plant = plant
        self.spec = spec
        self.nominal = plant.default_inputs() if nominal_inputs is None else nominal_inputs
        self.y0 = plant.nominal_state(self.nominal) if y0 is None else np.asarray(y0, dtype=float).copy()
        self.controllers = controllers or RegulatoryControlSystem.from_steady_state(plant, self.y0, self.nominal)
        if measurement_model is None:
            measurement_model = MeasurementModel(default_sensor_specs(plant.measurements(self.y0)))
        self.measurement_model = measurement_model
        self.safety = safety_system or SafetySystem(safety_config)
        self.timing = timing
        self.outcomes = OutcomeEvaluator(plant, plant.measurements(self.y0), outcome_config)
        self.alarms = alarm_manager or AlarmManager(default_alarm_definitions(safety_config))
        self.runtime_inputs = runtime_inputs or RuntimeInputEngine()
        self.pumps = PumpSystem()
        self.reset()

    def reset(self) -> dict[str, object]:
        self.t_h = 0.0
        self.y = self.y0.copy()
        self.controllers.reset()
        self.measurement_model.reset()
        self.spec.actuator_faults.reset()
        self.spec.sensor_faults.reset()
        self.spec.pump_faults.reset()
        self.pumps.reset()
        self.safety.reset()
        self.outcomes.reset()
        self.alarms.reset()
        self.runtime_inputs.reset()
        self.records: list[dict[str, object]] = []
        self._plant_tick_index = 0
        self.substepped_ticks = 0  # plant ticks that needed adaptive sub-stepping
        self.timing.strides()
        self.operator_throughput_target: float | None = None
        self.operator_auxiliary = {
            "H2_BDV": float(self.nominal.cstr_bdv_valve),
            "EMERGENCY_COOLING": float(self.nominal.cstr_emergency_cooling_factor),
        }
        self.last_action_result = ActionResult()
        self._pending_force_control_update = False
        self.last_controller_request = self.nominal
        self.last_supervisory_request = self.nominal
        self.last_safety_command = self.nominal
        self.last_actual = self.nominal
        self.last_safety_result = SafetyResult(self.nominal, (), (), False, {})
        self.last_measured = self.measurement_model.observe(self.plant.measurements(self.y))
        return self.observation()

    @property
    def done(self) -> bool:
        return self.outcomes.state.terminated

    def acknowledge_all_alarms(self) -> None:
        self.alarms.acknowledge_all(60.0 * self.t_h)

    def clear_runtime_injections(self) -> None:
        self.runtime_inputs.clear_active(self.t_h)

    def _external_abnormal_active(self, t_h: float) -> bool:
        return bool(
            self.spec.scenario.active_events(t_h)
            or self.spec.actuator_faults.active_faults(t_h)
            or self.spec.sensor_faults.active_faults(t_h)
            or self.spec.pump_faults.active_faults(t_h)
            or abs(self.spec.scenario.catalyst_activity.value(t_h) - 1.0) > 1e-12
            or abs(self.spec.scenario.condenser_cooling_effectiveness.value(t_h) - 1.0) > 1e-12
            or self.spec.scenario.cstr_liquid_leak_kmol_h.value(t_h) > 0.0
            or self.spec.scenario.reboiler_liquid_leak_kmol_h.value(t_h) > 0.0
            or self.spec.scenario.drum_liquid_leak_kmol_h.value(t_h) > 0.0
            or bool(self.runtime_inputs.active_events(t_h))
        )

    def action_catalog(self) -> dict[str, object]:
        """Machine-readable operator authority for agent tool/system-prompt setup."""
        return {
            "actions": (
                "NO_ACTION", "SET_THROUGHPUT", "SET_SETPOINT", "SET_MODE",
                "SET_MANUAL_OUTPUT", "OPERATE_AUXILIARY", "START_SPARE",
                "SELECT_PRIMARY", "ACK_ALARM", "RESET_SIF",
            ),
            "throughput_bounds_kmol_h": self.THROUGHPUT_BOUNDS,
            "controllers": self.controllers.access_snapshot(self.last_measured),
            "auxiliaries": {k: {"min": v[0], "max": v[1]} for k, v in self.AUXILIARY_BOUNDS.items()},
            "pump_trains": self.pumps.snapshot(self.t_h, self.spec.pump_faults),
            "prohibited_master_variables": (
                "fault activation", "sensor bias/freeze", "catalyst activity",
                "leak rates", "utility temperatures/pressures",
                "condenser cooling effectiveness", "SIF thresholds/bypass",
            ),
        }

    def observation(self) -> dict[str, object]:
        return {
            "time_h": float(self.t_h),
            "time_min": float(60.0 * self.t_h),
            "measurements": dict(self.last_measured),
            "controllers": self.controllers.access_snapshot(self.last_measured),
            "throughput_target_kmol_h": (
                float(self.operator_throughput_target)
                if self.operator_throughput_target is not None
                else float(self.last_controller_request.fresh_ipa_feed)
            ),
            "auxiliaries": dict(self.operator_auxiliary),
            "pump_trains": self.pumps.snapshot(self.t_h, self.spec.pump_faults),
            "sif_latched": tuple(self.last_safety_result.latched_causes),
            "plant_trip_latched": bool(self.last_safety_result.plant_trip_latched),
            "episode_status": self.outcomes.state.status.value,
            "active_alarm_count": len(self.alarms.active()),
            "unack_alarm_count": len(self.alarms.unacknowledged()),
            "alarm_states": self.alarms.snapshot(),
            "last_action_result": {
                "accepted": self.last_action_result.accepted,
                "rejected": self.last_action_result.rejected,
                "summary": self.last_action_result.summary,
            },
        }

    def _process_action(self, action: SupervisoryAction) -> tuple[ActionResult, bool]:
        accepted: list[str] = []
        rejected: list[str] = []
        force_control_update = False

        if action.throughput_target is not None:
            lo, hi = self.THROUGHPUT_BOUNDS
            value = float(action.throughput_target)
            if lo <= value <= hi:
                self.operator_throughput_target = value
                accepted.append(f"throughput={value:g} kmol/h")
            else:
                rejected.append(f"throughput {value:g} outside [{lo:g}, {hi:g}] kmol/h")

        for loop_id, value in action.setpoints.items():
            try:
                self.controllers.set_setpoint(loop_id, value)
                accepted.append(f"{loop_id} setpoint={float(value):g}")
                force_control_update = True
            except Exception as exc:
                rejected.append(str(exc))

        for loop_id, mode in action.modes.items():
            try:
                self.controllers.set_mode(loop_id, mode)
                accepted.append(f"{loop_id} mode={str(mode).upper()}")
                force_control_update = True
            except Exception as exc:
                rejected.append(str(exc))

        # Modes are processed first so one action can transfer a loop and set its output.
        for loop_id, value in action.manual_outputs.items():
            try:
                self.controllers.set_manual_output(loop_id, value)
                accepted.append(f"{loop_id} manual_output={float(value):g}")
                force_control_update = True
            except Exception as exc:
                rejected.append(str(exc))

        for device, value in action.auxiliary_commands.items():
            key = str(device).upper()
            if key not in self.AUXILIARY_BOUNDS:
                rejected.append(f"unknown auxiliary '{device}'")
                continue
            lo, hi = self.AUXILIARY_BOUNDS[key]
            value = float(value)
            if not lo <= value <= hi:
                rejected.append(f"{key} command {value:g} outside [{lo:g}, {hi:g}]")
                continue
            self.operator_auxiliary[key] = value
            accepted.append(f"{key}={value:g}")

        for train_id in action.start_spares:
            ok, msg = self.pumps.start_spare(str(train_id).upper(), self.spec.pump_faults, self.t_h)
            (accepted if ok else rejected).append(msg)

        for train_id in action.select_primaries:
            ok, msg = self.pumps.select_primary(str(train_id).upper(), self.spec.pump_faults, self.t_h)
            (accepted if ok else rejected).append(msg)

        ack_by = "action-agent" if action.note.startswith("agent") else "operator"
        if action.acknowledge_all_alarms:
            self.alarms.acknowledge_all(60.0 * self.t_h, ack_by)
            accepted.append("all alarms acknowledged")
        for alarm_id in action.acknowledge_alarm_ids:
            try:
                self.alarms.acknowledge(alarm_id, 60.0 * self.t_h, ack_by)
                accepted.append(f"alarm {alarm_id} acknowledged")
            except Exception as exc:
                rejected.append(f"alarm {alarm_id}: {exc}")

        if action.reset_sifs:
            accepted.append("SIF reset requested")
        if action.note:
            accepted.append(f"note={action.note}")

        result = ActionResult(tuple(accepted), tuple(rejected))
        if accepted or rejected:
            self.last_action_result = result
        return result, force_control_update

    def _apply_operator_state(self, request: PlantInputs) -> PlantInputs:
        u = request
        if self.operator_throughput_target is not None:
            u = replace(u, fresh_ipa_feed=float(self.operator_throughput_target))
        u = replace(
            u,
            cstr_bdv_valve=float(self.operator_auxiliary["H2_BDV"]),
            cstr_emergency_cooling_factor=float(self.operator_auxiliary["EMERGENCY_COOLING"]),
        )
        return u

    def _derived(self, y: np.ndarray, u: PlantInputs) -> dict[str, float]:
        yc, ycol = self.plant.split_state(y)
        rb = ycol[self.plant.column.reboiler_offset : self.plant.column.reboiler_offset + 8]
        Tsteam = self.plant.th.water_saturation_temperature(u.steam_pressure_bar)
        Qreb = self.plant.column.cfg.UA_reboiler * u.steam_factor * self.plant.column._positive(Tsteam - rb[3])
        Qj = self.plant.cstr.cfg.UA_jacket_kJ_hK * u.cstr_heater_factor * self.plant.cstr._positive(
            u.cstr_heating_utility_temperature_K - yc[self.plant.cstr.T]
        )
        h2 = self.plant.cstr.h2_flow_components(yc, self.plant._cstr_inputs(u))
        cap = self.plant.column.condenser_capacity(ycol, self.plant._column_inputs(u, yc))
        return {
            "reflux_ratio": float(u.reflux_flow / max(u.distillate_flow, 1e-8)),
            "reboiler_duty_MW": float(Qreb / 3.6e6),
            "cstr_heater_duty_MW": float(Qj / 3.6e6),
            "h2_pcv_kmol_h": h2["pcv_kmol_h"],
            "h2_bdv_kmol_h": h2["bdv_kmol_h"],
            "h2_psv_kmol_h": h2["psv_kmol_h"],
            "psv_open_fraction": h2["psv_open_fraction"],
            "condenser_capacity_kmol_h": cap["capacity_kmol_h"],
            "condensed_flow_kmol_h": cap["condensed_flow_kmol_h"],
            "condenser_cooling_valve": float(u.condenser_cooling_valve),
            "condenser_cooling_effectiveness": float(u.condenser_cooling_effectiveness),
            "condenser_duty_MW": float(cap["available_duty_kJ_h"] / 3.6e6),
        }

    def _record(self, t_h: float, truth: dict[str, float], measured: dict[str, float],
                controller_request: PlantInputs, supervisory_request: PlantInputs,
                safety_command: PlantInputs, actual: PlantInputs,
                safety_result: SafetyResult, action: SupervisoryAction) -> dict[str, object]:
        row: dict[str, object] = {
            "time_h": float(t_h),
            "time_min": float(60.0 * t_h),
            "episode.name": self.spec.name,
            "episode.status": self.outcomes.state.status.value,
            "episode.active": int(not self.outcomes.state.terminated),
            "episode.terminal_reason": self.outcomes.state.reason,
            "action.note": action.note,
            "action.reset_sifs": int(action.reset_sifs),
            "action.result": self.last_action_result.summary,
            "operator.throughput_target": float(self.operator_throughput_target) if self.operator_throughput_target is not None else float("nan"),
            "operator.H2_BDV": float(self.operator_auxiliary["H2_BDV"]),
            "operator.EMERGENCY_COOLING": float(self.operator_auxiliary["EMERGENCY_COOLING"]),
            "action.override_targets": ";".join(sorted(action.input_overrides)) if action.input_overrides else "none",
        }
        row.update({f"true.{k}": float(v) for k, v in truth.items()})
        row.update({f"meas.{k}": float(v) for k, v in measured.items()})
        row.update({f"ctrl.{k}": float(v) for k, v in asdict(controller_request).items()})
        row.update({f"sup.{k}": float(v) for k, v in asdict(supervisory_request).items()})
        row.update({f"safe.{k}": float(v) for k, v in asdict(safety_command).items()})
        row.update({f"act.{k}": float(v) for k, v in asdict(actual).items()})
        row.update({f"derived.{k}": float(v) for k, v in self._derived(self.y, actual).items()})

        for loop_id, info in self.controllers.access_snapshot(measured).items():
            row[f"loop.{loop_id}.mode"] = info["mode"]
            row[f"loop.{loop_id}.sp"] = info["setpoint"]
            row[f"loop.{loop_id}.op"] = info["last_output"]

        for tag in (
            "cstr_temperature_K", "cstr_pressure_bar", "cstr_holdup_kmol",
            "reboiler_holdup_kmol", "drum_holdup_kmol", "column_pressure_bar",
        ):
            row[f"safety_meas.{tag}"] = float(truth[tag])
        for cause in self.safety.CAUSES:
            row[f"sif.active.{cause}"] = int(safety_result.cause_flags.get(cause, False))
            row[f"sif.latched.{cause}"] = int(cause in safety_result.latched_causes)
        row["sif.plant_trip_latched"] = int(safety_result.plant_trip_latched)

        active_events = self.spec.scenario.active_events(t_h)
        active_af = self.spec.actuator_faults.active_faults(t_h)
        active_sf = self.spec.sensor_faults.active_faults(t_h)
        active_pf = self.spec.pump_faults.active_faults(t_h)
        row["truth.active_events"] = ";".join(e.name for e in active_events) if active_events else "none"
        row["truth.active_actuator_faults"] = ";".join(f.name for f in active_af) if active_af else "none"
        row["truth.active_sensor_faults"] = ";".join(f.name for f in active_sf) if active_sf else "none"
        row["truth.active_pump_faults"] = ";".join(f.name for f in active_pf) if active_pf else "none"
        row["truth.catalyst_activity"] = float(self.spec.scenario.catalyst_activity.value(t_h))
        row["truth.condenser_cooling_effectiveness"] = float(self.spec.scenario.condenser_cooling_effectiveness.value(t_h))
        row["truth.cstr_leak_kmol_h"] = float(self.spec.scenario.cstr_liquid_leak_kmol_h.value(t_h))
        row["truth.reboiler_leak_kmol_h"] = float(self.spec.scenario.reboiler_liquid_leak_kmol_h.value(t_h))
        row["truth.drum_leak_kmol_h"] = float(self.spec.scenario.drum_liquid_leak_kmol_h.value(t_h))
        runtime_events = self.runtime_inputs.active_events(t_h)
        row["truth.active_runtime_events"] = ";".join(e.name for e in runtime_events) if runtime_events else "none"
        for train_id, info in self.pumps.snapshot(t_h, self.spec.pump_faults).items():
            row[f"pump.{train_id}.active"] = info["active_pump"]
            row[f"pump.{train_id}.selected_failed"] = int(bool(info["selected_failed"]))
        row["alarm.active_count"] = len(self.alarms.active())
        row["alarm.unack_count"] = len(self.alarms.unacknowledged())
        for alarm_id, state in self.alarms.snapshot().items():
            row[f"alarm.state.{alarm_id}"] = state
        return row

    @staticmethod
    def _merge_current_exogenous(controller_request: PlantInputs, exogenous: PlantInputs) -> PlantInputs:
        return replace(
            exogenous,
            cstr_outflow=controller_request.cstr_outflow,
            cstr_heater_factor=controller_request.cstr_heater_factor,
            cstr_vent_valve=controller_request.cstr_vent_valve,
            reflux_flow=controller_request.reflux_flow,
            steam_factor=controller_request.steam_factor,
            bottoms_flow=controller_request.bottoms_flow,
            distillate_flow=controller_request.distillate_flow,
            condenser_cooling_valve=controller_request.condenser_cooling_valve,
        )

    def _advance_one_tick(self, action: SupervisoryAction, force_control_update: bool = False,
                          reset_sif_this_tick: bool = False) -> OutcomeState:
        if self.done:
            return self.outcomes.state
        dt = self.timing.plant_step_h
        tk = self.t_h

        truth = self.plant.measurements(self.y)
        noisy = self.measurement_model.observe(truth)
        measured = self.spec.sensor_faults.apply(tk, noisy)
        pump_snapshot = self.pumps.snapshot(tk, self.spec.pump_faults)
        for train_id, info in pump_snapshot.items():
            measured[f"pump_{train_id}_failed"] = 1.0 if info["selected_failed"] else 0.0
        self.alarms.update(60.0 * tk, measured)

        exogenous = self.spec.scenario.evaluate(tk, self.nominal)
        exogenous = self.runtime_inputs.apply(tk, exogenous)
        if self.operator_throughput_target is not None:
            exogenous = replace(exogenous, fresh_ipa_feed=float(self.operator_throughput_target))

        _, control_stride = self.timing.strides()
        if force_control_update or self._plant_tick_index % control_stride == 0:
            dt_control = 0.0 if force_control_update or self._plant_tick_index == 0 else self.timing.control_interval_h
            controller_request = self.controllers.update(
                measured, dt_control, base_inputs=exogenous,
                throughput_setpoint=exogenous.fresh_ipa_feed,
            )
        else:
            controller_request = self._merge_current_exogenous(self.last_controller_request, exogenous)

        supervisory_request = self._apply_operator_state(controller_request)
        supervisory_request = action.apply_legacy_overrides(supervisory_request)
        safety_result = self.safety.apply(
            truth, supervisory_request, self.nominal,
            reset_requested=bool(reset_sif_this_tick),
        )
        after_actuator_faults = self.spec.actuator_faults.apply(tk, safety_result.commanded, self.nominal)
        actual = self.pumps.apply(tk, after_actuator_faults, self.spec.pump_faults)

        if self.spec.terminate_on_plant_trip and safety_result.plant_trip_latched:
            self.outcomes.state.status = EpisodeStatus.SAFE_TRIP
            self.outcomes.state.reason = "plant_trip_sif_latched"
            self.outcomes.state.terminal_time_h = float(tk)

        self.last_controller_request = controller_request
        self.last_supervisory_request = supervisory_request
        self.last_safety_command = safety_result.commanded
        self.last_actual = actual
        self.last_safety_result = safety_result
        self.last_measured = measured

        self.records.append(self._record(
            tk, truth, measured, controller_request, supervisory_request,
            safety_result.commanded, actual, safety_result, action,
        ))

        if self.done:
            return self.outcomes.state

        # Adaptive sub-stepping: split the 0.1-min step when the implicit solve stalls
        # (sharp transitions such as SIF overrides), instead of ending the episode.
        sol = self.plant.adaptive_step(self.y, dt, actual)
        if getattr(sol, "nsub", 1) > 1:
            self.substepped_ticks += 1
        if (not sol.success) or (not np.all(np.isfinite(sol.x))) or np.linalg.norm(sol.fun) > 1e-5:
            reason = f"DAE step failed/residual={np.linalg.norm(sol.fun):.3e}: {getattr(sol, 'message', '')}"
            self.outcomes.numerical_failure(tk, reason)
            return self.outcomes.state

        self.y = sol.x
        self.t_h = min(tk + dt, self.spec.scenario.duration_h)
        self._plant_tick_index += 1
        truth_next = self.plant.measurements(self.y)
        external_active = self._external_abnormal_active(self.t_h)
        self.outcomes.update(
            self.t_h, dt, self.y, truth_next, actual, safety_result,
            external_abnormal_active=external_active,
            horizon_h=self.spec.scenario.duration_h,
            allow_recovery_termination=self.spec.allow_recovery_termination,
            terminate_on_plant_trip=self.spec.terminate_on_plant_trip,
            horizon_is_completion=self.spec.horizon_is_completion,
        )
        return self.outcomes.state

    def apply_action(self, action: SupervisoryAction) -> ActionResult:
        """Apply a persistent operator/agent action without advancing process time."""
        result, force = self._process_action(action)
        self._pending_force_control_update = self._pending_force_control_update or force
        return result

    def step(self, action: SupervisoryAction | None = None) -> tuple[dict[str, object], OutcomeState]:
        if action is None:
            action = SupervisoryAction.no_action()
        if self.done:
            return self.observation(), self.outcomes.state

        _, force_from_action = self._process_action(action)
        force_control_update = self._pending_force_control_update or force_from_action
        self._pending_force_control_update = False
        n, _ = self.timing.strides()
        for i in range(n):
            self._advance_one_tick(
                action,
                force_control_update=force_control_update and i == 0,
                reset_sif_this_tick=action.reset_sifs and i == 0,
            )
            if self.done:
                break
        return self.observation(), self.outcomes.state

    def run(self, actor=None, pad_to_horizon: bool = True,
            progress: bool = False) -> tuple[np.ndarray, list[dict[str, object]], OutcomeState]:
        actor = PassiveActor() if actor is None else actor
        self.reset()
        while not self.done:
            action = actor.act(self.observation())
            _, state = self.step(action)
            if progress:
                print(f"  {60*self.t_h:6.1f} min  {state.status.value}")

        terminal_t = self.t_h
        if self.records and abs(float(self.records[-1]["time_h"]) - terminal_t) > 1e-12:
            truth = self.plant.measurements(self.y)
            noisy = self.measurement_model.observe(truth)
            measured = self.spec.sensor_faults.apply(terminal_t, noisy)
            self.last_measured = measured
            self.records.append(self._record(
                terminal_t, truth, measured,
                self.last_controller_request, self.last_supervisory_request,
                self.last_safety_command, self.last_actual, self.last_safety_result,
                SupervisoryAction.no_action(),
            ))
        terminal_row = dict(self.records[-1]) if self.records else None

        if pad_to_horizon and terminal_row is not None and terminal_t < self.spec.scenario.duration_h - 1e-12:
            t = terminal_t + self.timing.plant_step_h
            while t <= self.spec.scenario.duration_h + 1e-12:
                row = dict(terminal_row)
                row["time_h"] = float(min(t, self.spec.scenario.duration_h))
                row["time_min"] = float(60.0 * min(t, self.spec.scenario.duration_h))
                row["episode.status"] = self.outcomes.state.status.value
                row["episode.active"] = 0
                row["episode.terminal_reason"] = self.outcomes.state.reason
                self.records.append(row)
                t += self.timing.plant_step_h

        times = np.asarray([float(r["time_h"]) for r in self.records], dtype=float)
        return times, self.records, self.outcomes.state
