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
from pathlib import Path
import base64

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
_loading_episode = False


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



# ---------------------------------------------------------------------------
# Live overview / faceplate graphic
# ---------------------------------------------------------------------------
_OVERVIEW_W = 1670
_OVERVIEW_H = 942
_OVERVIEW_IMAGE_PATH = Path(__file__).with_name("static") / "process_overview.png"
_OVERVIEW_IMAGE_URI = "data:image/png;base64," + base64.b64encode(_OVERVIEW_IMAGE_PATH.read_bytes()).decode("ascii")
_overview_flash_phase = False


def _oy(screen_y: float) -> float:
    """Convert image pixel Y (origin at top) to Bokeh data Y (origin at bottom)."""
    return _OVERVIEW_H - float(screen_y)


# These rectangles intentionally cover the illustrative placeholder values
# baked into the artwork.  The live operator values are then rendered on top.
_OVERVIEW_VALUE_SPECS = (
    ("r_temp", 197, 360, 158, 31),
    ("r_press", 197, 397, 158, 31),
    ("r_level", 197, 435, 158, 31),
    ("r_x", 197, 472, 158, 31),
    ("heater", 237, 642, 116, 31),
    ("h2", 628, 146, 138, 30),
    ("col_press", 796, 301, 151, 31),
    ("top_temp", 795, 365, 151, 31),
    ("feed_temp", 779, 446, 158, 31),
    ("bottom_temp", 795, 554, 151, 31),
    ("reflux", 1193, 330, 128, 31),
    ("cooling", 1447, 132, 147, 31),
    ("dr_level", 1563, 243, 127, 31),
    ("dist_x", 1563, 279, 127, 31),
    ("distillate", 1563, 315, 127, 31),
    ("steam", 1434, 626, 119, 31),
    ("rb_level", 1212, 711, 151, 31),
    ("bottoms", 1443, 719, 141, 31),
    ("btm_x", 1443, 757, 141, 31),
)

overview_value_source = ColumnDataSource(dict(
    x=[x for _, x, _, _, _ in _OVERVIEW_VALUE_SPECS],
    y=[_oy(y) for _, _, y, _, _ in _OVERVIEW_VALUE_SPECS],
    w=[w for _, _, _, w, _ in _OVERVIEW_VALUE_SPECS],
    h=[h for _, _, _, _, h in _OVERVIEW_VALUE_SPECS],
    text=["" for _ in _OVERVIEW_VALUE_SPECS],
))

# Small alarm lamps beside the scalar readouts.  Lamps are white in the normal
# state and flash yellow whenever an associated operator alarm is active.
_OVERVIEW_INDICATOR_SPECS = (
    ("r_temp", 295, 360, "cstr_temperature_K"),
    ("r_press", 295, 397, "cstr_pressure_bar"),
    ("r_level", 295, 435, "cstr_holdup_kmol"),
    ("r_x", 295, 472, None),
    ("h2", 540, 146, None),
    ("col_press", 884, 301, "column_pressure_bar"),
    ("top_temp", 884, 365, "top_temperature_K"),
    ("feed_temp", 884, 446, None),
    ("bottom_temp", 884, 554, "bottom_temperature_K"),
    ("reflux", 1110, 330, None),
    ("cooling", 1388, 132, None),
    ("dr_level", 1489, 243, "drum_holdup_kmol"),
    ("dist_x", 1489, 279, "distillate_x_acetone"),
    ("distillate", 1489, 315, None),
    ("heater", 171, 642, None),
    ("steam", 1368, 626, None),
    ("rb_level", 1127, 711, "reboiler_holdup_kmol"),
    ("bottoms", 1368, 719, None),
    ("btm_x", 1368, 757, "bottoms_x_acetone"),
)
overview_indicator_source = ColumnDataSource(dict(
    x=[x for _, x, _, _ in _OVERVIEW_INDICATOR_SPECS],
    y=[_oy(y) for _, _, y, _ in _OVERVIEW_INDICATOR_SPECS],
    fill=["#f8fbff" for _ in _OVERVIEW_INDICATOR_SPECS],
    line=["#9fc4dc" for _ in _OVERVIEW_INDICATOR_SPECS],
))

# Executive alarm lamps shown in the lower-left dedicated panel of the artwork.
_OVERVIEW_EXEC_SPECS = (
    ("R_P_HH", 89, 856),
    ("R_L_LL", 199, 856),
    ("DR_L_LL", 311, 856),
    ("COL_P_HH", 432, 856),
)
overview_exec_source = ColumnDataSource(dict(
    x=[x for _, x, _ in _OVERVIEW_EXEC_SPECS],
    y=[_oy(y) for _, _, y in _OVERVIEW_EXEC_SPECS],
    color=["#f8fbff" for _ in _OVERVIEW_EXEC_SPECS],
))

_OVERVIEW_PUMP_BADGES = (
    ("FEED", "A", 151, 303), ("FEED", "B", 227, 303),
    ("CSTR_OUTLET", "A", 492, 718), ("CSTR_OUTLET", "B", 569, 718),
    ("REFLUX", "A", 1138, 469), ("REFLUX", "B", 1215, 469),
    ("DISTILLATE", "A", 1324, 531), ("DISTILLATE", "B", 1401, 531),
    ("BOTTOMS", "A", 1139, 862), ("BOTTOMS", "B", 1216, 862),
)
overview_pump_source = ColumnDataSource(dict(
    x=[x for _, _, x, _ in _OVERVIEW_PUMP_BADGES],
    y=[_oy(y) for _, _, _, y in _OVERVIEW_PUMP_BADGES],
    text=[f"{side} STBY" for _, side, _, _ in _OVERVIEW_PUMP_BADGES],
    fill=["#0d2638" for _ in _OVERVIEW_PUMP_BADGES],
    line=["#5b7890" for _ in _OVERVIEW_PUMP_BADGES],
    text_color=["#d6e6f2" for _ in _OVERVIEW_PUMP_BADGES],
))

overview_plot = figure(
    width=_OVERVIEW_W, height=_OVERVIEW_H, sizing_mode="scale_width",
    x_range=(0, _OVERVIEW_W), y_range=(0, _OVERVIEW_H),
    toolbar_location=None, min_border=0, outline_line_color=None,
    background_fill_color="#061b28",
)
overview_plot.axis.visible = False
overview_plot.grid.visible = False
overview_plot.image_url(
    url=[_OVERVIEW_IMAGE_URI], x=0, y=0, w=_OVERVIEW_W, h=_OVERVIEW_H,
    anchor="bottom_left",
)
# Value-card masks and live scalar values.
overview_plot.rect("x", "y", "w", "h", source=overview_value_source,
                   fill_color="#082438", fill_alpha=0.98,
                   line_color="#2d6d94", line_width=1.2)
overview_plot.text("x", "y", text="text", source=overview_value_source,
                   text_align="center", text_baseline="middle",
                   text_color="#f1f7fb", text_font_size="12px",
                   text_font="Arial")
# Ordinary alarm lamps.  Use a slightly larger live glyph than the baked-in
# white placeholder so an active lamp remains clearly visible on the faceplate.
overview_plot.rect("x", "y", 24, 24, source=overview_indicator_source,
                   fill_color="fill", line_color="line", line_width=3)
# Executive alarm lamps.
overview_plot.rect("x", "y", 22, 22, source=overview_exec_source,
                   fill_color="color", line_color="color", line_width=2)
# Pump A/B selection / availability badges.
overview_plot.rect("x", "y", 70, 28, source=overview_pump_source,
                   fill_color="fill", fill_alpha=0.96, line_color="line", line_width=1.4)
overview_plot.text("x", "y", text="text", source=overview_pump_source,
                   text_align="center", text_baseline="middle",
                   text_color="text_color", text_font_size="10px",
                   text_font_style="bold")

overview_tab = TabPanel(title="Overview", child=overview_plot)

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
trend_tabs = Tabs(tabs=[overview_tab, reactor_tab, column_tab, controls_tab, utilities_tab], sizing_mode="stretch_width")


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



def _overview_text_values() -> dict[str, str]:
    assert env is not None
    m, u = env.last_measured, env.last_actual
    yc, _ = plant.split_state(env.y)
    h2 = plant.cstr.h2_flow_components(yc, plant._cstr_inputs(u))
    return {
        "r_temp": f"T_R = {m['cstr_temperature_K']:.1f} K",
        "r_press": f"P_R = {m['cstr_pressure_bar']:.3f} bar",
        "r_level": f"M_R = {m['cstr_holdup_kmol']:.1f} kmol",
        "r_x": f"x_B = {m['cstr_x_acetone']:.3f}",
        "heater": f"HV = {100*u.cstr_heater_factor:.0f} %",
        "h2": f"F_H2 = {h2['total_kmol_h']:.1f} kmol/h",
        "col_press": f"P_COL = {m['column_pressure_bar']:.3f} bar",
        "top_temp": f"T_TOP = {m['top_temperature_K']:.1f} K",
        "feed_temp": f"T_FEED = {u.fresh_feed_temperature_K:.1f} K",
        "bottom_temp": f"T_BOT = {m['bottom_temperature_K']:.1f} K",
        "reflux": f"R = {u.reflux_flow:.1f} kmol/h",
        "cooling": f"CWV = {100*u.condenser_cooling_valve:.0f} %",
        "dr_level": f"M_D = {m['drum_holdup_kmol']:.1f} kmol",
        "dist_x": f"x_D = {m['distillate_x_acetone']:.3f}",
        "distillate": f"D = {u.distillate_flow:.1f} kmol/h",
        "steam": f"SV = {100*u.steam_factor:.0f} %",
        "rb_level": f"M_B = {m['reboiler_holdup_kmol']:.1f} kmol",
        "bottoms": f"B = {u.bottoms_flow:.1f} kmol/h",
        "btm_x": f"x_BOT = {m['bottoms_x_acetone']:.3f}",
    }


def _tag_alarm_visual_state(tag: str | None) -> str:
    """Return the most important operator-alarm state for a faceplate tag.

    A returned-but-unacknowledged alarm must remain visually annunciated until
    the operator acknowledges it.  The earlier console used ``AlarmStatus.active``
    here, which intentionally excludes RTN_UNACK; that made a short-lived alarm
    disappear from the faceplate even though it was still listed in the alarm
    table below.
    """
    if env is None or tag is None:
        return "NORMAL"
    states = [s.state.value for s in env.alarms.status.values() if s.definition.tag == tag]
    if "ACTIVE_UNACK" in states:
        return "ACTIVE_UNACK"
    if "RTN_UNACK" in states:
        return "RTN_UNACK"
    if "ACTIVE_ACK" in states:
        return "ACTIVE_ACK"
    return "NORMAL"


def _refresh_overview_values():
    vals = _overview_text_values()
    overview_value_source.data = dict(
        x=list(overview_value_source.data["x"]),
        y=list(overview_value_source.data["y"]),
        w=list(overview_value_source.data["w"]),
        h=list(overview_value_source.data["h"]),
        text=[vals[key] for key, *_ in _OVERVIEW_VALUE_SPECS],
    )

    # Update pump duty/standby state.  A failed selected pump flashes red in the
    # dedicated 500 ms flash callback; healthy selected pumps are green.
    ps = env.pumps.snapshot(env.t_h, env.spec.pump_faults)
    texts, fills, lines, tcols = [], [], [], []
    for train_id, side, _, _ in _OVERVIEW_PUMP_BADGES:
        x = ps[train_id]
        pump_id = x["primary"] if side == "A" else x["spare"]
        selected = x["active_pump"] == pump_id
        failed = x["primary_failed"] if side == "A" else x["spare_failed"]
        if selected and not failed:
            texts.append(f"{side} RUN"); fills.append("#003c23"); lines.append("#00d96b"); tcols.append("#33ff99")
        elif selected and failed:
            texts.append(f"{side} FAIL"); fills.append("#401216"); lines.append("#ff3b45"); tcols.append("#ffb5b8")
        elif failed:
            texts.append(f"{side} FAIL"); fills.append("#2a1012"); lines.append("#b6242c"); tcols.append("#ff9197")
        else:
            texts.append(f"{side} STBY"); fills.append("#0d2638"); lines.append("#5b7890"); tcols.append("#d6e6f2")
    overview_pump_source.data = dict(
        x=list(overview_pump_source.data["x"]), y=list(overview_pump_source.data["y"]),
        text=texts, fill=fills, line=lines, text_color=tcols,
    )


def _refresh_overview_indicators():
    if env is None:
        return
    # A lamp flashes between its active colour and white.  This is deliberately
    # independent of simulation time, so alarms continue to flash while paused.
    ordinary_fill, ordinary_line = [], []
    for _, _, _, tag in _OVERVIEW_INDICATOR_SPECS:
        state = _tag_alarm_visual_state(tag)
        if state in ("ACTIVE_UNACK", "RTN_UNACK"):
            # Flash between bright yellow and dark amber, rather than yellow and
            # white.  The artwork contains a baked-in white placeholder lamp, so
            # a white flash phase was visually indistinguishable from NORMAL.
            ordinary_fill.append("#ffd400" if _overview_flash_phase else "#6f5900")
            ordinary_line.append("#fff176")
        elif state == "ACTIVE_ACK":
            ordinary_fill.append("#ffb300")
            ordinary_line.append("#ffd54f")
        else:
            ordinary_fill.append("#f8fbff")
            ordinary_line.append("#9fc4dc")
    overview_indicator_source.data = dict(
        x=list(overview_indicator_source.data["x"]),
        y=list(overview_indicator_source.data["y"]),
        fill=ordinary_fill,
        line=ordinary_line,
    )

    latched = set(env.last_safety_result.latched_causes)
    exec_colors = [
        ("#ff3038" if _overview_flash_phase else "#f8fbff") if cause in latched else "#f8fbff"
        for cause, _, _ in _OVERVIEW_EXEC_SPECS
    ]
    overview_exec_source.data = dict(
        x=list(overview_exec_source.data["x"]),
        y=list(overview_exec_source.data["y"]),
        color=exec_colors,
    )

    # Flash the badge of a selected failed duty/standby pump red.
    ps = env.pumps.snapshot(env.t_h, env.spec.pump_faults)
    data = {k: list(v) for k, v in overview_pump_source.data.items()}
    for i, (train_id, side, _, _) in enumerate(_OVERVIEW_PUMP_BADGES):
        x = ps[train_id]
        pump_id = x["primary"] if side == "A" else x["spare"]
        selected = x["active_pump"] == pump_id
        failed = x["primary_failed"] if side == "A" else x["spare_failed"]
        if selected and failed:
            data["fill"][i] = "#ff3038" if _overview_flash_phase else "#401216"
            data["line"][i] = "#ff3038"
            data["text_color"][i] = "#ffffff"
    overview_pump_source.data = data


def _refresh_overview():
    _refresh_overview_values()
    _refresh_overview_indicators()


def _overview_flash_tick():
    global _overview_flash_phase
    _overview_flash_phase = not _overview_flash_phase
    _refresh_overview_indicators()

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
    active_scheduled = env.spec.scenario.active_events(env.t_h)
    schedule_bits = []
    for ev in env.spec.scenario.events:
        start_min = 60.0 * ev.start_h
        end_min = None if ev.end_h is None else 60.0 * ev.end_h
        if ev.active(env.t_h):
            phase = "ACTIVE"
        elif env.t_h < ev.start_h:
            phase = "pending"
        else:
            phase = "done"
        interval = f"@ {start_min:.1f} min" if end_min is None else f"@ {start_min:.1f}–{end_min:.1f} min"
        schedule_bits.append(f"{ev.name} ({phase}, {interval})")
    schedule_text = "; ".join(schedule_bits) or "none"
    truth_panel.text = """
    <div style='border:1px solid #999;padding:10px;border-radius:4px'>
      <h3 style='margin:0 0 6px 0'>Master / ground truth</h3>
      <b>Loaded episode:</b> %s<br>
      <b>Scenario schedule:</b> %s<br>
      <b>Active scheduled events:</b> %s<br><b>Runtime injections:</b> %s<br>
      <b>Actuator faults:</b> %s<br><b>Sensor faults:</b> %s<br><b>Pump faults:</b> %s<br>
      <b>SIF latched:</b> %s<br><b>Plant-trip latch:</b> %s<br>
      <b>Last operator action:</b> %s
    </div>""" % (
        escape(env.spec.name),
        escape(schedule_text),
        escape(", ".join(x.name for x in active_scheduled) or "none"),
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
    _refresh_status(); _refresh_faceplates(); _refresh_alarm_table(); _refresh_truth_panel(); _refresh_overview(); _sync_operator_widgets()


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


def _new_environment(reason: str = "manual"):
    global env, pending_reset_sif, running, _loading_episode
    if _loading_episode:
        return
    _loading_episode = True
    try:
        running = False
        pending_reset_sif = False
        spec = _build_spec()
        env = EpisodeRunner(
            plant, spec,
            timing=EpisodeTiming(plant_step_min=0.1, decision_interval_min=0.5, control_interval_min=1.0),
        )
        _clear_sources()
        _append_point()
        _refresh_all()
        run_button.label = "Run"
        message_div.text = (
            f"Loaded episode <b>{escape(env.spec.name)}</b> at t=0.0 min "
            f"({escape(reason)}). Press <b>Run</b> or use Step controls to advance."
        )
    finally:
        _loading_episode = False


def _ensure_live_environment_for_injection():
    """Ensure a master injection is applied to a live episode.

    The old console accepted injections even after the episode had terminated,
    so the ground-truth event existed but process time could no longer advance.
    For master-console use this is surprising.  A terminated episode is now
    transparently reset using the currently selected episode before an
    injection is armed.
    """
    global env
    if env is None or env.done:
        _new_environment(reason="automatic reset before master injection")
    return env


def _on_episode_selection_change(attr, old, new):
    # Selecting an episode should actually load it.  This removes the previous
    # two-step dropdown + load-button ambiguity that made the console appear to
    # keep running nominal_hold.
    _new_environment(reason=f"episode selection changed to {new}")


def _on_episode_config_change(attr, old, new):
    # Horizon/seed changes alter the generated schedule, so rebuild immediately
    # when the episode is not actively running.  While running we leave the
    # current run untouched; the explicit load/reset button can still be used.
    if not running:
        _new_environment(reason="episode configuration changed")


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
    if env is None or env.done:
        _new_environment(reason="Run pressed after completed/terminated episode")
    running = not running
    run_button.label = "Pause" if running else "Run"
    _refresh_status()


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
load_episode_button.on_click(lambda: _new_environment(reason="Load/reset button"))
ack_button.on_click(_ack_all); reset_sif_button.on_click(_request_sif_reset)
speed_select.on_change("value", lambda attr, old, new: _restart_periodic())
scenario_select.on_change("value", _on_episode_selection_change)
horizon_spinner.on_change("value", _on_episode_config_change)
seed_spinner.on_change("value", _on_episode_config_change)
stop_on_trip_checkbox.on_change("active", _on_episode_config_change)


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
    _ensure_live_environment_for_injection()
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
    _ensure_live_environment_for_injection()
    target, mode = actuator_target.value, actuator_mode.value; frac = float(actuator_severity.value)/100.0
    kwargs = dict(name=f"manual_{target}_{mode}_{60*env.t_h:.1f}min", target=target, start_h=env.t_h, end_h=_end_h(actuator_duration.value), mode=mode)
    if mode == "capacity": kwargs["capacity_fraction"] = frac
    elif mode == "fixed": kwargs["fixed_value"] = float(getattr(env.nominal, target))*frac
    elif mode == "stiction": kwargs["stiction_band_fraction"] = frac
    env.spec.actuator_faults.faults.append(ActuatorFault(**kwargs))
    message_div.text = f"Injected actuator fault: {escape(target)} / {escape(mode)}."
    _refresh_truth_panel()


def _inject_pump_fault():
    _ensure_live_environment_for_injection()
    pid = pump_fault_target.value
    env.spec.pump_faults.faults.append(PumpFault(name=f"manual_{pid}_trip_{60*env.t_h:.1f}min", pump_id=pid, start_h=env.t_h, end_h=_end_h(pump_fault_duration.value)))
    message_div.text = f"Tripped pump {escape(pid)}. A healthy standby must be selected from Operator actions."
    _refresh_truth_panel()


def _inject_sensor():
    _ensure_live_environment_for_injection()
    target, mode = sensor_target.value, sensor_mode.value
    truth0 = plant.measurements(env.y)[target]; bias = float(truth0)*float(sensor_bias_pct.value)/100.0
    env.spec.sensor_faults.faults.append(SensorFault(name=f"manual_{target}_{mode}_{60*env.t_h:.1f}min", target=target, start_h=env.t_h, end_h=_end_h(sensor_duration.value), mode=mode, bias=bias if mode == "bias" else 0.0))
    message_div.text = f"Injected sensor fault: {escape(target)} / {escape(mode)}."
    _refresh_truth_panel()


def _inject_process():
    _ensure_live_environment_for_injection()
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
    trend_tabs,
    status_banner,
    row(reactor_faceplate, column_faceplate, truth_panel, sizing_mode="stretch_width"),
    pump_panel,
    Div(text="<h2>Operator alarms</h2>"), alarm_table,
    Div(text="<h3>Alarm event history</h3>"), alarm_history_table,
    message_div, sizing_mode="stretch_width",
)

sidebar = column(run_controls, operator_panel, injection_panel, width=390)
DOC.add_root(row(sidebar, main, sizing_mode="stretch_width"))

_new_environment(reason="initial load")
_restart_periodic()
DOC.add_periodic_callback(_overview_flash_tick, 500)
