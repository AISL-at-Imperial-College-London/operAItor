"""Bokeh master operator/training console for the CSTR + distillation simulator.

Run from the package directory with:
    python run_operator_console.py
or:
    bokeh serve --show operator_console.py

The left sidebar intentionally separates OPERATOR ACTIONS from MASTER
INJECTIONS.  Operator actions use the restricted persistent control interface;
master injections manipulate scenario/fault ground truth and are not available
to an autonomous agent.
"""
from __future__ import annotations

from dataclasses import replace
from html import escape

from bokeh.io import curdoc
from bokeh.layouts import column, row, gridplot
from bokeh.models import (
    Button, CheckboxGroup, ColumnDataSource, DataTable, Div, NumberFormatter,
    Select, Spinner, TableColumn, TabPanel, Tabs,
)
from bokeh.plotting import figure

from integrated_plant import (
    ActuatorFault, EpisodeRunner, EpisodeTiming, IntegratedPlantDAE,
    PumpFault, PUMP_IDS, PUMP_TRAINS, RuntimeInputEvent, SensorFault,
    SupervisoryAction, CURATED_EPISODES, curated_episode, nominal_episode,
    normal_variability_episode, surprise_episode, RegulatoryControlSystem,
)

DOC = curdoc()
DOC.title = "CSTR + Distillation Master Operator Console"

plant = IntegratedPlantDAE()
env: EpisodeRunner | None = None
running = False
pending_reset_sif = False
periodic_handle = None


# ---------------------------------------------------------------------------
# Episode construction
# ---------------------------------------------------------------------------
scenario_options = [
    ("nominal", "Nominal hold"),
    ("variability", "Random normal disturbances"),
    ("surprise", "Seeded surprise episode"),
] + [(x, f"Curated: {x}") for x in CURATED_EPISODES]
scenario_select = Select(title="Episode", value="nominal", options=scenario_options)
horizon_spinner = Spinner(title="Horizon [min]", low=30, high=720, step=30, value=180, width=150)
seed_spinner = Spinner(title="Random seed", low=0, high=99999999, step=1, value=20261004, width=150)
stop_on_trip_checkbox = CheckboxGroup(labels=["Terminate when plant-trip SIF latches"], active=[])
load_episode_button = Button(label="Load / reset selected episode", button_type="primary", width=240)


def _build_spec():
    mode = scenario_select.value
    horizon = float(horizon_spinner.value)
    seed = int(seed_spinner.value)
    if mode == "nominal":
        spec = nominal_episode(horizon)
    elif mode == "variability":
        spec = normal_variability_episode(horizon, seed)
    elif mode == "surprise":
        spec = surprise_episode(horizon, seed)
    else:
        spec = curated_episode(mode, horizon)
    stop_on_trip = 0 in stop_on_trip_checkbox.active
    return replace(spec, terminate_on_plant_trip=stop_on_trip, allow_recovery_termination=False)


# ---------------------------------------------------------------------------
# Trend data
# ---------------------------------------------------------------------------
fields = [
    "time", "r_temp", "r_temp_sp", "r_press", "r_press_sp", "r_level", "r_level_sp", "r_x",
    "col_press", "col_press_sp", "top_temp", "top_temp_sp", "bottom_temp", "bottom_temp_sp",
    "rb_level", "rb_level_sp", "dr_level", "dr_level_sp", "dist_x", "btm_x",
    "feed", "outflow", "reflux", "distillate", "bottoms",
    "heater", "steam", "cooling", "cooling_cmd", "cool_eff", "pcv", "bdv", "psv",
    "feed_temp", "cw_temp", "steam_press", "heat_util_temp", "ambient_temp",
]
history_source = ColumnDataSource({k: [] for k in fields})
alarm_source = ColumnDataSource(dict(priority=[], alarm_id=[], state=[], value=[], limit=[], unit=[], description=[]))
alarm_history_source = ColumnDataSource(dict(time_min=[], priority=[], alarm_id=[], transition=[], value=[], description=[]))


def _make_plot(title, y_label, series):
    p = figure(height=220, sizing_mode="stretch_width", title=title,
               x_axis_label="Simulated time [min]", y_axis_label=y_label,
               tools="pan,wheel_zoom,box_zoom,reset,save")
    for field, label in series:
        p.line("time", field, source=history_source, legend_label=label, line_width=2)
    if len(series) > 1:
        p.legend.click_policy = "hide"
        p.legend.location = "top_left"
    return p


reactor_tab = TabPanel(title="Reactor", child=gridplot([
    [_make_plot("Reactor temperature", "K", [("r_temp", "PV"), ("r_temp_sp", "SP")]),
     _make_plot("Reactor pressure", "bar", [("r_press", "PV"), ("r_press_sp", "SP")])],
    [_make_plot("Reactor liquid holdup", "kmol", [("r_level", "PV"), ("r_level_sp", "SP")]),
     _make_plot("Reactor acetone mole fraction", "mol fraction", [("r_x", "x acetone")])],
], sizing_mode="stretch_width"))

column_tab = TabPanel(title="Column", child=gridplot([
    [_make_plot("Column pressure", "bar", [("col_press", "PV"), ("col_press_sp", "SP")]),
     _make_plot("Column temperatures", "K", [("top_temp", "Top PV"), ("top_temp_sp", "Top SP"), ("bottom_temp", "Bottom PV"), ("bottom_temp_sp", "Bottom SP")])],
    [_make_plot("Reboiler / reflux drum holdup", "kmol", [("rb_level", "Reboiler PV"), ("rb_level_sp", "Reboiler SP"), ("dr_level", "Drum PV"), ("dr_level_sp", "Drum SP")]),
     _make_plot("Product compositions", "mol fraction", [("dist_x", "Distillate acetone"), ("btm_x", "Bottoms acetone")])],
], sizing_mode="stretch_width"))

controls_tab = TabPanel(title="Controls / MVs", child=gridplot([
    [_make_plot("Reactor flows", "kmol/h", [("feed", "Feed"), ("outflow", "CSTR out")]),
     _make_plot("Column product/reflux flows", "kmol/h", [("reflux", "Reflux"), ("distillate", "Distillate"), ("bottoms", "Bottoms")])],
    [_make_plot("Heat / cooling final elements", "fraction", [("heater", "CSTR heater"), ("steam", "Steam"), ("cooling_cmd", "CW command"), ("cooling", "CW actual")]),
     _make_plot("H2 final elements", "fraction", [("pcv", "PCV"), ("bdv", "BDV"), ("psv", "PSV")])],
], sizing_mode="stretch_width"))

utilities_tab = TabPanel(title="Utilities", child=gridplot([
    [_make_plot("Feed / cooling-water temperature", "K", [("feed_temp", "Feed T"), ("cw_temp", "CW T")]),
     _make_plot("Heating utility / ambient temperature", "K", [("heat_util_temp", "Heating utility"), ("ambient_temp", "Ambient")])],
    [_make_plot("Steam header pressure", "bar", [("steam_press", "Steam P")]),
     _make_plot("Condenser cooling effectiveness", "fraction", [("cool_eff", "Cooling effectiveness")])],
], sizing_mode="stretch_width"))
trend_tabs = Tabs(tabs=[reactor_tab, column_tab, controls_tab, utilities_tab], sizing_mode="stretch_width")


def _clear_sources():
    history_source.data = {k: [] for k in fields}
    alarm_source.data = dict(priority=[], alarm_id=[], state=[], value=[], limit=[], unit=[], description=[])
    alarm_history_source.data = dict(time_min=[], priority=[], alarm_id=[], transition=[], value=[], description=[])


def _current_values():
    assert env is not None
    m, u = env.last_measured, env.last_actual
    sp = env.controllers.sp
    yc, _ = plant.split_state(env.y)
    h2 = plant.cstr.h2_flow_components(yc, plant._cstr_inputs(u))
    return dict(
        time=60.0 * env.t_h,
        r_temp=m["cstr_temperature_K"], r_temp_sp=sp.cstr_temperature_K,
        r_press=m["cstr_pressure_bar"], r_press_sp=sp.cstr_pressure_bar,
        r_level=m["cstr_holdup_kmol"], r_level_sp=sp.cstr_holdup_kmol,
        r_x=m["cstr_x_acetone"],
        col_press=m["column_pressure_bar"], col_press_sp=sp.column_pressure_bar,
        top_temp=m["top_temperature_K"], top_temp_sp=sp.top_temperature_K,
        bottom_temp=m["bottom_temperature_K"], bottom_temp_sp=sp.bottom_temperature_K,
        rb_level=m["reboiler_holdup_kmol"], rb_level_sp=sp.reboiler_holdup_kmol,
        dr_level=m["drum_holdup_kmol"], dr_level_sp=sp.drum_holdup_kmol,
        dist_x=m["distillate_x_acetone"], btm_x=m["bottoms_x_acetone"],
        feed=u.fresh_ipa_feed, outflow=u.cstr_outflow, reflux=u.reflux_flow,
        distillate=u.distillate_flow, bottoms=u.bottoms_flow,
        heater=u.cstr_heater_factor, steam=u.steam_factor,
        cooling=u.condenser_cooling_valve,
        cooling_cmd=env.last_controller_request.condenser_cooling_valve,
        cool_eff=u.condenser_cooling_effectiveness,
        pcv=u.cstr_vent_valve, bdv=u.cstr_bdv_valve, psv=h2["psv_open_fraction"],
        feed_temp=u.fresh_feed_temperature_K, cw_temp=u.cooling_water_temperature_K,
        steam_press=u.steam_pressure_bar,
        heat_util_temp=u.cstr_heating_utility_temperature_K,
        ambient_temp=u.ambient_temperature_K,
    )


def _append_point():
    history_source.stream({k: [v] for k, v in _current_values().items()}, rollover=720)


# ---------------------------------------------------------------------------
# Status / faceplates / alarm displays
# ---------------------------------------------------------------------------
status_banner = Div(sizing_mode="stretch_width", height=52)
reactor_faceplate = Div(width=390, height=260)
column_faceplate = Div(width=430, height=260)
truth_panel = Div(width=430, height=260)
pump_panel = Div(sizing_mode="stretch_width", height=120)
message_div = Div(sizing_mode="stretch_width", height=70)

alarm_columns = [
    TableColumn(field="priority", title="Pri", width=45),
    TableColumn(field="alarm_id", title="Alarm", width=90),
    TableColumn(field="state", title="State", width=120),
    TableColumn(field="value", title="PV", formatter=NumberFormatter(format="0.000"), width=75),
    TableColumn(field="limit", title="Limit", formatter=NumberFormatter(format="0.000"), width=75),
    TableColumn(field="unit", title="Unit", width=65),
    TableColumn(field="description", title="Description", width=240),
]
alarm_table = DataTable(source=alarm_source, columns=alarm_columns, height=215, sizing_mode="stretch_width", index_position=None)
alarm_history_columns = [
    TableColumn(field="time_min", title="Time [min]", formatter=NumberFormatter(format="0.0"), width=85),
    TableColumn(field="priority", title="Pri", width=45),
    TableColumn(field="alarm_id", title="Alarm", width=90),
    TableColumn(field="transition", title="Transition", width=150),
    TableColumn(field="value", title="PV", formatter=NumberFormatter(format="0.000"), width=80),
    TableColumn(field="description", title="Description", width=260),
]
alarm_history_table = DataTable(source=alarm_history_source, columns=alarm_history_columns, height=180, sizing_mode="stretch_width", index_position=None)


def _alarm_color_for_tag(tag):
    assert env is not None
    states = [s for s in env.alarms.status.values() if s.definition.tag == tag]
    if any(s.state.value == "ACTIVE_UNACK" for s in states): return "#c62828"
    if any(s.state.value == "ACTIVE_ACK" for s in states): return "#ef6c00"
    if any(s.state.value == "RTN_UNACK" for s in states): return "#6a1b9a"
    return "#263238"


def _pv(label, tag, value, unit, fmt=".2f"):
    color = _alarm_color_for_tag(tag)
    return f"<tr><td>{escape(label)}</td><td style='font-weight:700;color:{color};text-align:right'>{value:{fmt}} {escape(unit)}</td></tr>"


def _loop_line(loop_id):
    assert env is not None
    x = env.controllers.access_snapshot(env.last_measured)[loop_id]
    return f"<b>{loop_id}</b> {x['mode']} &nbsp; SP {x['setpoint']:.3g} &nbsp; OP {x['last_output']:.3g}"


def _refresh_faceplates():
    assert env is not None
    m, u = env.last_measured, env.last_actual
    reactor_rows = "".join([
        _pv("Temperature", "cstr_temperature_K", m["cstr_temperature_K"], "K"),
        _pv("Pressure", "cstr_pressure_bar", m["cstr_pressure_bar"], "bar", ".3f"),
        _pv("Liquid holdup", "cstr_holdup_kmol", m["cstr_holdup_kmol"], "kmol"),
        _pv("Acetone x", "cstr_x_acetone", m["cstr_x_acetone"], "", ".3f"),
        _pv("H2 vent", "cstr_h2_vent_kmol_h", m["cstr_h2_vent_kmol_h"], "kmol/h"),
    ])
    reactor_faceplate.text = f"""
    <div style='border:1px solid #999;padding:10px;border-radius:4px'>
      <h3 style='margin:0 0 6px 0'>R-101 CSTR</h3><table style='width:100%'>{reactor_rows}</table>
      <hr>{_loop_line('R_LIC')}<br>{_loop_line('R_TIC')}<br>{_loop_line('R_PIC')}<br>
      <b>Actual:</b> Heater {100*u.cstr_heater_factor:.1f}% &nbsp; PCV {100*u.cstr_vent_valve:.1f}% &nbsp; BDV {100*u.cstr_bdv_valve:.1f}%<br>
      <b>Feed / outlet:</b> {u.fresh_ipa_feed:.1f} / {u.cstr_outflow:.1f} kmol/h
    </div>"""

    top_color = _alarm_color_for_tag("top_temperature_K")
    bottom_color = _alarm_color_for_tag("bottom_temperature_K")
    temp_row = ("<tr><td>Top / bottom T</td><td style='font-weight:700;text-align:right'>"
                f"<span style='color:{top_color}'>{m['top_temperature_K']:.2f}</span> / "
                f"<span style='color:{bottom_color}'>{m['bottom_temperature_K']:.2f}</span> K</td></tr>")
    column_rows = "".join([
        _pv("Column pressure", "column_pressure_bar", m["column_pressure_bar"], "bar", ".3f"), temp_row,
        _pv("Reboiler holdup", "reboiler_holdup_kmol", m["reboiler_holdup_kmol"], "kmol"),
        _pv("Drum holdup", "drum_holdup_kmol", m["drum_holdup_kmol"], "kmol"),
        _pv("Distillate acetone", "distillate_x_acetone", m["distillate_x_acetone"], "", ".3f"),
        _pv("Bottoms acetone", "bottoms_x_acetone", m["bottoms_x_acetone"], "", ".3f"),
    ])
    column_faceplate.text = f"""
    <div style='border:1px solid #999;padding:10px;border-radius:4px'>
      <h3 style='margin:0 0 6px 0'>C-201 Distillation</h3><table style='width:100%'>{column_rows}</table>
      <hr>{_loop_line('COL_PIC')}<br>{_loop_line('TOP_TIC')}<br>{_loop_line('BOT_TIC')}<br>
      {_loop_line('RB_LIC')}<br>{_loop_line('DR_LIC')}<br>
      <b>Cooling effectiveness:</b> {100*u.condenser_cooling_effectiveness:.1f}%
    </div>"""


def _refresh_alarm_table():
    assert env is not None
    items = [s for s in env.alarms.status.values() if s.state.value != "NORMAL"]
    items.sort(key=lambda s: (s.definition.priority, s.definition.alarm_id))
    alarm_source.data = dict(
        priority=[s.definition.priority for s in items], alarm_id=[s.definition.alarm_id for s in items],
        state=[s.state.value for s in items], value=[s.value for s in items],
        limit=[s.definition.limit for s in items], unit=[s.definition.unit for s in items],
        description=[s.definition.description for s in items],
    )
    hist = env.alarms.history[-50:][::-1]
    alarm_history_source.data = dict(
        time_min=[x.time_min for x in hist], priority=[x.priority for x in hist],
        alarm_id=[x.alarm_id for x in hist], transition=[x.transition for x in hist],
        value=[x.value for x in hist], description=[x.description for x in hist],
    )


def _refresh_truth_panel():
    assert env is not None
    rt = env.runtime_inputs.active_events(env.t_h)
    af = env.spec.actuator_faults.active_faults(env.t_h)
    sf = env.spec.sensor_faults.active_faults(env.t_h)
    pf = env.spec.pump_faults.active_faults(env.t_h)
    scheduled = env.spec.scenario.active_events(env.t_h)
    truth_panel.text = """
    <div style='border:1px solid #999;padding:10px;border-radius:4px'>
      <h3 style='margin:0 0 6px 0'>Master / ground truth</h3>
      <b>Scenario events:</b> %s<br><b>Runtime injections:</b> %s<br>
      <b>Actuator faults:</b> %s<br><b>Sensor faults:</b> %s<br><b>Pump faults:</b> %s<br>
      <b>SIF latched:</b> %s<br><b>Plant-trip latch:</b> %s<br>
      <b>Last operator action:</b> %s
    </div>""" % (
        escape(", ".join(x.name for x in scheduled) or "none"),
        escape(", ".join(x.name for x in rt) or "none"),
        escape(", ".join(x.name for x in af) or "none"),
        escape(", ".join(x.name for x in sf) or "none"),
        escape(", ".join(x.name for x in pf) or "none"),
        escape(", ".join(env.last_safety_result.latched_causes) or "none"),
        "YES" if env.last_safety_result.plant_trip_latched else "no",
        escape(env.last_action_result.summary),
    )

    ps = env.pumps.snapshot(env.t_h, env.spec.pump_faults)
    bits = []
    for tid, x in ps.items():
        stat = "FAILED" if x["selected_failed"] else "running"
        bits.append(f"<b>{tid}</b>: {x['active_pump']} ({stat}); standby {x['spare']}")
    pump_panel.text = "<div style='border:1px solid #bbb;padding:8px'><b>Pump trains</b><br>" + " &nbsp; | &nbsp; ".join(bits) + "</div>"


def _refresh_status():
    assert env is not None
    n_active, n_unack = len(env.alarms.active()), len(env.alarms.unacknowledged())
    mode = "RUNNING" if running else "PAUSED"
    if env.done: mode = "TERMINATED"
    status_banner.text = f"""
    <div style='padding:9px;border:1px solid #777;border-radius:4px;font-size:16px'>
    <b>{mode}</b> &nbsp; | &nbsp; Sim time: <b>{60*env.t_h:.1f} min</b>
    &nbsp; | &nbsp; Episode: <b>{escape(env.spec.name)}</b>
    &nbsp; | &nbsp; Status: <b>{escape(env.outcomes.state.status.value)}</b>
    &nbsp; | &nbsp; Active alarms: <b>{n_active}</b> &nbsp; Unack: <b>{n_unack}</b>
    </div>"""


def _refresh_all():
    _refresh_status(); _refresh_faceplates(); _refresh_alarm_table(); _refresh_truth_panel(); _sync_operator_widgets()


# ---------------------------------------------------------------------------
# Run controls
# ---------------------------------------------------------------------------
speed_select = Select(title="Display speed", value="250", options=[
    ("125", "0.5 sim min / 0.125 s"), ("250", "0.5 sim min / 0.25 s"),
    ("500", "0.5 sim min / 0.5 s"), ("1000", "0.5 sim min / 1 s")], width=190)
run_button = Button(label="Run", button_type="success", width=90)
step1_button = Button(label="Step 1 min", width=100)
step5_button = Button(label="Step 5 min", width=100)
ack_button = Button(label="Acknowledge all alarms", button_type="warning", width=180)
reset_sif_button = Button(label="Request SIF reset", width=150)


def _new_environment():
    global env, pending_reset_sif, running
    running = False; pending_reset_sif = False
    env = EpisodeRunner(plant, _build_spec(), timing=EpisodeTiming(plant_step_min=0.1, decision_interval_min=0.5, control_interval_min=1.0))
    _clear_sources(); _append_point(); _refresh_all(); run_button.label = "Run"
    message_div.text = f"Loaded episode <b>{escape(env.spec.name)}</b>."


def _one_tick():
    global running, pending_reset_sif
    assert env is not None
    if env.done:
        running = False; run_button.label = "Run"; _refresh_all(); return
    action = SupervisoryAction(reset_sifs=pending_reset_sif, note="master_console_sif_reset" if pending_reset_sif else "")
    pending_reset_sif = False
    env.step(action)
    _append_point()
    if env.done: running = False; run_button.label = "Run"
    _refresh_all()


def _periodic_tick():
    if running: _one_tick()


def _restart_periodic():
    global periodic_handle
    if periodic_handle is not None:
        try: DOC.remove_periodic_callback(periodic_handle)
        except Exception: pass
    periodic_handle = DOC.add_periodic_callback(_periodic_tick, int(speed_select.value))


def _toggle_run():
    global running
    running = not running; run_button.label = "Pause" if running else "Run"; _refresh_status()


def _step(n):
    global running
    running = False; run_button.label = "Run"
    for _ in range(n):
        if env is None or env.done: break
        _one_tick()


def _ack_all():
    assert env is not None
    env.apply_action(SupervisoryAction(acknowledge_all_alarms=True)); _refresh_all()


def _request_sif_reset():
    global pending_reset_sif
    pending_reset_sif = True
    message_div.text = "SIF reset requested; it will be evaluated on the next process tick."

run_button.on_click(_toggle_run)
step1_button.on_click(lambda: _step(2)); step5_button.on_click(lambda: _step(10))
load_episode_button.on_click(_new_environment); ack_button.on_click(_ack_all); reset_sif_button.on_click(_request_sif_reset)
speed_select.on_change("value", lambda attr, old, new: _restart_periodic())


# ---------------------------------------------------------------------------
# Restricted operator / agent actions
# ---------------------------------------------------------------------------
throughput_spinner = Spinner(title="Throughput target [kmol/h]", low=0, high=150, step=5, value=100)
set_throughput_button = Button(label="Set throughput", button_type="primary")
loop_select = Select(title="Controller loop", value="R_TIC", options=list(RegulatoryControlSystem.LOOP_SPECS.keys()))
loop_sp_spinner = Spinner(title="Setpoint", low=0, high=500, step=0.1, value=0)
set_sp_button = Button(label="Set SP", button_type="primary")
loop_mode_select = Select(title="Mode", value="AUTO", options=["AUTO", "MANUAL"])
set_mode_button = Button(label="Apply mode")
manual_output_spinner = Spinner(title="Manual output", low=0, high=200, step=0.1, value=0)
set_manual_button = Button(label="Set manual output")
aux_select = Select(title="Auxiliary", value="H2_BDV", options=[("H2_BDV", "H2 emergency BDV"), ("EMERGENCY_COOLING", "Emergency reactor cooling")])
aux_value_spinner = Spinner(title="Auxiliary command [%]", low=0, high=100, step=5, value=0)
set_aux_button = Button(label="Set auxiliary")
pump_train_select = Select(title="Pump train", value="REFLUX", options=[(k, f"{k}: {v.description}") for k, v in PUMP_TRAINS.items()])
start_spare_button = Button(label="Start/select standby pump", button_type="warning")
select_primary_button = Button(label="Return to primary pump")


def _sync_operator_widgets(*_):
    if env is None: return
    snap = env.controllers.access_snapshot(env.last_measured)[loop_select.value]
    loop_sp_spinner.low = snap["sp_min"]; loop_sp_spinner.high = snap["sp_max"]
    loop_sp_spinner.step = 0.01 if snap["sp_unit"] == "bar" else (0.1 if snap["sp_unit"] == "K" else 0.5)
    loop_sp_spinner.value = snap["setpoint"]
    loop_mode_select.value = snap["mode"]
    manual_output_spinner.low = snap["mv_min"]; manual_output_spinner.high = snap["mv_max"]
    manual_output_spinner.step = 0.01 if snap["mv_unit"] == "fraction" else 1.0
    manual_output_spinner.value = snap["manual_output"] if snap["manual_output"] is not None else snap["last_output"]
    throughput_spinner.value = env.operator_throughput_target if env.operator_throughput_target is not None else env.last_controller_request.fresh_ipa_feed
    aux_value_spinner.value = 100.0 * env.operator_auxiliary[aux_select.value]


def _show_action_result(result):
    message_div.text = escape(result.summary)
    _refresh_all()


def _set_throughput():
    _show_action_result(env.apply_action(SupervisoryAction(throughput_target=float(throughput_spinner.value))))


def _set_sp():
    _show_action_result(env.apply_action(SupervisoryAction(setpoints={loop_select.value: float(loop_sp_spinner.value)})))


def _set_mode():
    _show_action_result(env.apply_action(SupervisoryAction(modes={loop_select.value: loop_mode_select.value})))


def _set_manual():
    _show_action_result(env.apply_action(SupervisoryAction(manual_outputs={loop_select.value: float(manual_output_spinner.value)})))


def _set_aux():
    _show_action_result(env.apply_action(SupervisoryAction(auxiliary_commands={aux_select.value: float(aux_value_spinner.value)/100.0})))


def _start_spare():
    _show_action_result(env.apply_action(SupervisoryAction(start_spares=(pump_train_select.value,))))


def _select_primary():
    _show_action_result(env.apply_action(SupervisoryAction(select_primaries=(pump_train_select.value,))))

loop_select.on_change("value", lambda attr, old, new: _sync_operator_widgets())
aux_select.on_change("value", lambda attr, old, new: _sync_operator_widgets())
set_throughput_button.on_click(_set_throughput); set_sp_button.on_click(_set_sp); set_mode_button.on_click(_set_mode)
set_manual_button.on_click(_set_manual); set_aux_button.on_click(_set_aux)
start_spare_button.on_click(_start_spare); select_primary_button.on_click(_select_primary)


# ---------------------------------------------------------------------------
# Master injection controls -- hidden ground-truth manipulation
# ---------------------------------------------------------------------------
disturbance_map = {
    "Feed temperature": "fresh_feed_temperature_K",
    "Cooling-water temperature": "cooling_water_temperature_K",
    "Steam-header pressure": "steam_pressure_bar",
    "CSTR heating-utility temperature": "cstr_heating_utility_temperature_K",
    "Ambient temperature": "ambient_temperature_K",
}
disturbance_select = Select(title="15-min disturbance", value="Cooling-water temperature", options=list(disturbance_map))
disturbance_direction = Select(title="Step", value="1.10", options=[("1.10", "+10%"), ("0.90", "-10%")])
inject_disturbance_button = Button(label="Inject disturbance now", button_type="primary")

actuator_targets = [
    "fresh_ipa_feed", "cstr_outflow", "cstr_heater_factor", "cstr_vent_valve", "cstr_bdv_valve",
    "reflux_flow", "steam_factor", "bottoms_flow", "distillate_flow", "condenser_cooling_valve",
]
actuator_target = Select(title="Actuator target", value="cstr_vent_valve", options=actuator_targets)
actuator_mode = Select(title="Fault mode", value="freeze", options=["freeze", "capacity", "fixed", "stiction"])
actuator_severity = Spinner(title="Severity / setting [% nominal]", low=0, high=200, step=5, value=50)
actuator_duration = Spinner(title="Duration [min], 0=persistent", low=0, high=300, step=5, value=0)
inject_actuator_button = Button(label="Inject actuator fault", button_type="danger")

pump_fault_target = Select(title="Pump to trip", value="P-201A", options=list(PUMP_IDS))
pump_fault_duration = Spinner(title="Pump trip duration [min], 0=persistent", low=0, high=300, step=5, value=0)
inject_pump_fault_button = Button(label="Trip pump", button_type="danger")

sensor_targets = ["cstr_pressure_bar", "cstr_temperature_K", "cstr_holdup_kmol", "column_pressure_bar", "top_temperature_K", "bottom_temperature_K", "reboiler_holdup_kmol", "drum_holdup_kmol"]
sensor_target = Select(title="Sensor target", value="cstr_pressure_bar", options=sensor_targets)
sensor_mode = Select(title="Sensor fault", value="bias", options=["bias", "freeze"])
sensor_bias_pct = Spinner(title="Bias [% current true value]", low=-100, high=100, step=5, value=-20)
sensor_duration = Spinner(title="Duration [min], 0=persistent", low=0, high=300, step=5, value=0)
inject_sensor_button = Button(label="Inject sensor fault", button_type="danger")

process_fault_type = Select(title="Process / utility fault", value="catalyst_activity_loss", options=[
    "catalyst_activity_loss", "cstr_liquid_leak", "reboiler_liquid_leak", "drum_liquid_leak", "condenser_cooling_loss",
])
process_severity = Spinner(title="Severity (loss %; leaks use kmol/h)", low=0, high=150, step=5, value=50)
process_duration = Spinner(title="Duration [min], 0=persistent", low=0, high=300, step=5, value=0)
inject_process_button = Button(label="Inject process fault", button_type="danger")
clear_injections_button = Button(label="End all manual injections now", button_type="warning")


def _end_h(duration_min):
    return None if float(duration_min) <= 0 else env.t_h + float(duration_min)/60.0


def _inject_disturbance():
    target = disturbance_map[disturbance_select.value]; mult = float(disturbance_direction.value)
    event = RuntimeInputEvent(
        name=f"manual_{target}_{'up' if mult > 1 else 'down'}_{60*env.t_h:.1f}min",
        kind="disturbance", target=target, start_h=env.t_h, end_h=env.t_h + 15.0/60.0,
        mode="multiply", value=mult,
    )
    env.runtime_inputs.add(event)
    base = float(getattr(env.spec.scenario.evaluate(env.t_h, env.nominal), target))
    message_div.text = (f"Armed {escape(disturbance_select.value)} {'+10%' if mult > 1 else '-10%'} "
                        f"for 15 simulated min: {base:.3g} → {base*mult:.3g}. "
                        "The change appears when process time next advances (Run or Step).")
    _refresh_truth_panel()


def _inject_actuator():
    target, mode = actuator_target.value, actuator_mode.value; frac = float(actuator_severity.value)/100.0
    kwargs = dict(name=f"manual_{target}_{mode}_{60*env.t_h:.1f}min", target=target, start_h=env.t_h, end_h=_end_h(actuator_duration.value), mode=mode)
    if mode == "capacity": kwargs["capacity_fraction"] = frac
    elif mode == "fixed": kwargs["fixed_value"] = float(getattr(env.nominal, target))*frac
    elif mode == "stiction": kwargs["stiction_band_fraction"] = frac
    env.spec.actuator_faults.faults.append(ActuatorFault(**kwargs))
    message_div.text = f"Injected actuator fault: {escape(target)} / {escape(mode)}."
    _refresh_truth_panel()


def _inject_pump_fault():
    pid = pump_fault_target.value
    env.spec.pump_faults.faults.append(PumpFault(name=f"manual_{pid}_trip_{60*env.t_h:.1f}min", pump_id=pid, start_h=env.t_h, end_h=_end_h(pump_fault_duration.value)))
    message_div.text = f"Tripped pump {escape(pid)}. A healthy standby must be selected from Operator actions."
    _refresh_truth_panel()


def _inject_sensor():
    target, mode = sensor_target.value, sensor_mode.value
    truth0 = plant.measurements(env.y)[target]; bias = float(truth0)*float(sensor_bias_pct.value)/100.0
    env.spec.sensor_faults.faults.append(SensorFault(name=f"manual_{target}_{mode}_{60*env.t_h:.1f}min", target=target, start_h=env.t_h, end_h=_end_h(sensor_duration.value), mode=mode, bias=bias if mode == "bias" else 0.0))
    message_div.text = f"Injected sensor fault: {escape(target)} / {escape(mode)}."
    _refresh_truth_panel()


def _inject_process():
    ftype = process_fault_type.value; sev = float(process_severity.value); end = _end_h(process_duration.value)
    if ftype == "catalyst_activity_loss": target, mode, value = "catalyst_activity", "set", max(0.0, 1.0-sev/100.0)
    elif ftype == "condenser_cooling_loss": target, mode, value = "condenser_cooling_effectiveness", "set", max(0.0, 1.0-sev/100.0)
    else:
        target = {"cstr_liquid_leak":"cstr_liquid_leak_kmol_h", "reboiler_liquid_leak":"reboiler_liquid_leak_kmol_h", "drum_liquid_leak":"drum_liquid_leak_kmol_h"}[ftype]
        mode, value = "set", max(0.0, sev)
    env.runtime_inputs.add(RuntimeInputEvent(name=f"manual_{ftype}_{60*env.t_h:.1f}min", kind="process_fault", target=target, start_h=env.t_h, end_h=end, mode=mode, value=value))
    message_div.text = f"Injected process/utility fault: {escape(ftype)}."
    _refresh_truth_panel()


def _clear_injections():
    t = env.t_h; env.clear_runtime_injections()
    env.spec.actuator_faults.faults = [replace(f, end_h=t) if f.active(t) and f.name.startswith("manual_") else f for f in env.spec.actuator_faults.faults]
    env.spec.sensor_faults.faults = [replace(f, end_h=t) if f.active(t) and f.name.startswith("manual_") else f for f in env.spec.sensor_faults.faults]
    env.spec.pump_faults.faults = [replace(f, end_h=t) if f.active(t) and f.name.startswith("manual_") else f for f in env.spec.pump_faults.faults]
    message_div.text = "All currently active manual injections ended. Scheduled scenario events are unchanged."
    _refresh_truth_panel()

inject_disturbance_button.on_click(_inject_disturbance); inject_actuator_button.on_click(_inject_actuator)
inject_pump_fault_button.on_click(_inject_pump_fault); inject_sensor_button.on_click(_inject_sensor)
inject_process_button.on_click(_inject_process); clear_injections_button.on_click(_clear_injections)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
run_controls = column(
    Div(text="<h2>Simulation control</h2>"), scenario_select, row(horizon_spinner, seed_spinner),
    stop_on_trip_checkbox, load_episode_button, speed_select,
    row(run_button, step1_button, step5_button), row(ack_button, reset_sif_button),
    sizing_mode="stretch_width",
)

operator_panel = column(
    Div(text="<h2>Operator / agent actions</h2><p>Persistent actions available to an operator or autonomous agent.</p>"),
    throughput_spinner, set_throughput_button,
    Div(text="<hr><b>Controller access</b>"), loop_select, loop_sp_spinner, set_sp_button,
    row(loop_mode_select, set_mode_button), manual_output_spinner, set_manual_button,
    Div(text="<hr><b>Emergency auxiliaries</b>"), aux_select, aux_value_spinner, set_aux_button,
    Div(text="<hr><b>Duty / standby pumps</b>"), pump_train_select, start_spare_button, select_primary_button,
    sizing_mode="stretch_width",
)

injection_panel = column(
    Div(text="<h2>Master injections</h2><p><b>Ground-truth test controls only.</b> These are not exposed to the agent.</p>"),
    Div(text="<b>Normal disturbance</b>"), disturbance_select, disturbance_direction, inject_disturbance_button,
    Div(text="<hr><b>Actuator fault</b>"), actuator_target, actuator_mode, actuator_severity, actuator_duration, inject_actuator_button,
    Div(text="<hr><b>Pump trip</b>"), pump_fault_target, pump_fault_duration, inject_pump_fault_button,
    Div(text="<hr><b>Sensor fault</b>"), sensor_target, sensor_mode, sensor_bias_pct, sensor_duration, inject_sensor_button,
    Div(text="<hr><b>Process / utility fault</b>"), process_fault_type, process_severity, process_duration, inject_process_button,
    Div(text="<hr>"), clear_injections_button, sizing_mode="stretch_width",
)

main = column(
    status_banner,
    row(reactor_faceplate, column_faceplate, truth_panel, sizing_mode="stretch_width"),
    pump_panel, trend_tabs,
    Div(text="<h2>Operator alarms</h2>"), alarm_table,
    Div(text="<h3>Alarm event history</h3>"), alarm_history_table,
    message_div, sizing_mode="stretch_width",
)

sidebar = column(run_controls, operator_panel, injection_panel, width=390)
DOC.add_root(row(sidebar, main, sizing_mode="stretch_width"))

_new_environment()
_restart_periodic()
