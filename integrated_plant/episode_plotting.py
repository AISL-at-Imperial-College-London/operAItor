"""Saved result dashboards for the v0.5 episode CSV schema."""
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm, ListedColormap
import numpy as np

from .equipment import PUMP_TRAINS
from .safety import SafetyConfig, SafetySystem


def plot_episode_results(records, output_dir, *, name=None, safety_config=None,
                         terminal_time_min=None, show=False):
    """Save three PNG dashboards; optionally display them after saving.

    Only the terminal sample is retained after termination; padded rows are
    shaded and hidden so they cannot be mistaken for simulated recovery.
    Accepts both in-memory rows and csv.DictReader rows.
    """
    if not records:
        raise ValueError("Cannot plot an empty episode")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    config = safety_config or SafetyConfig()
    name = name or str(records[0].get("episode.name", "episode"))
    status = str(records[-1].get("episode.status", "UNKNOWN"))
    reason = str(records[-1].get("episode.terminal_reason", ""))
    time = np.asarray([float(row["time_min"]) for row in records])
    active = np.asarray([float(row.get("episode.active", 1)) != 0 for row in records])
    inactive = np.flatnonzero(~active)
    keep = active.copy()
    if inactive.size:
        keep[inactive[0]] = True
        if terminal_time_min is None:
            terminal_time_min = time[inactive[0]]
    events = []
    previous = set()
    for row in records:
        current = set()
        for key in ("truth.active_events", "truth.active_actuator_faults",
                    "truth.active_sensor_faults", "truth.active_pump_faults",
                    "truth.active_runtime_events"):
            current.update(x for x in str(row.get(key, "none")).split(";") if x and x != "none")
        new = current - previous
        if new:
            events.append((float(row["time_min"]), ", ".join(sorted(new))))
        previous = current

    def values(key):
        data = np.asarray([float(row.get(key, "nan")) for row in records])
        data[~keep] = np.nan
        return data

    def line(ax, key, label, **kwargs):
        if key in records[0]:
            ax.plot(time, values(key), label=label, **kwargs)

    def decorate(fig, axes, title):
        fig.suptitle(f"{name} | {status}\n{title}", fontsize=15)
        for ax in np.asarray(axes).flat:
            ax.set_xlabel("Time [min]")
            ax.grid(alpha=0.2)
            for onset, _ in events:
                ax.axvline(onset, color="#b45309", linestyle=":", linewidth=1, alpha=0.65)
            if terminal_time_min is not None:
                ax.axvline(terminal_time_min, color="#64748b", linestyle="--", linewidth=1)
                if terminal_time_min < time[-1]:
                    ax.axvspan(terminal_time_min, time[-1], color="#e2e8f0", alpha=0.7, zorder=0)
            if time[-1] > time[0]:
                ax.set_xlim(time[0], time[-1])
        caption = "Dotted orange: event onset. Dashed grey: episode termination. Shading: padded data (not simulated)."
        if events:
            caption += "\n" + " | ".join(f"{t:g} min: {label}" for t, label in events[:3])
            if len(events) > 3:
                caption += f" | +{len(events) - 3} further event onsets"
        if reason:
            caption += f"\nOutcome reason: {reason}"
        fig.text(0.02, 0.015, caption, fontsize=8, va="bottom", wrap=True)
        fig.tight_layout(rect=(0, 0.08, 1, 0.94))

    figures = []
    paths = []

    def save(fig, suffix):
        path = output / f"{name}_{suffix}.png"
        fig.savefig(path, dpi=150, facecolor="white")
        paths.append(path)
        if show:
            figures.append(fig)
        else:
            plt.close(fig)

    fig, axes = plt.subplots(3, 3, figsize=(16, 12))
    panels = [
        ("cstr_temperature_K", "CSTR temperature", "K", "R_TIC", [config.cstr_temperature_hh_K]),
        ("cstr_pressure_bar", "CSTR pressure", "bar", "R_PIC", [config.cstr_pressure_hh_bar, config.cstr_pressure_hhh_bar]),
        ("column_pressure_bar", "Column pressure", "bar", "COL_PIC", [config.column_pressure_hh_bar]),
        ("cstr_holdup_kmol", "CSTR inventory", "kmol", "R_LIC", [config.cstr_level_ll_kmol, config.cstr_level_hh_kmol]),
        ("reboiler_holdup_kmol", "Reboiler inventory", "kmol", "RB_LIC", [config.reboiler_level_ll_kmol, config.reboiler_level_hh_kmol]),
        ("drum_holdup_kmol", "Reflux drum inventory", "kmol", "DR_LIC", [config.drum_level_ll_kmol, config.drum_level_hh_kmol]),
        ("cstr_x_acetone", "CSTR acetone fraction", "Mole fraction", None, []),
        ("distillate_x_acetone", "Distillate acetone fraction", "Mole fraction", None, []),
        ("bottoms_x_acetone", "Bottoms acetone fraction", "Mole fraction", None, []),
    ]
    for ax, (tag, title, unit, loop, limits) in zip(axes.flat, panels):
        line(ax, f"true.{tag}", "True", color="#2563eb", linewidth=1.6)
        line(ax, f"meas.{tag}", "Measured", color="#38bdf8", linewidth=0.7, alpha=0.65)
        if loop:
            line(ax, f"loop.{loop}.sp", "Setpoint", color="#15803d", linestyle="--")
        for i, limit in enumerate(limits):
            ax.axhline(limit, color="#dc2626", linestyle=":", linewidth=1,
                       label="SIF limit" if i == 0 else None)
        ax.set(title=title, ylabel=unit)
        ax.legend(fontsize=7, loc="best")
    decorate(fig, axes, "Process measurements, setpoints and SIF limits")
    save(fig, "process")

    fig, axes = plt.subplots(3, 3, figsize=(16, 12))
    actuators = [
        ("fresh_ipa_feed", "Fresh-feed throughput", "kmol/h"),
        ("cstr_outflow", "CSTR outlet flow", "kmol/h"),
        ("cstr_heater_factor", "CSTR heater command", "Fraction"),
        ("cstr_vent_valve", "H2 pressure-control valve", "Fraction"),
        ("cstr_bdv_valve", "H2 emergency blowdown valve", "Fraction"),
        ("condenser_cooling_valve", "Condenser cooling-water valve", "Fraction"),
        ("reflux_flow", "Reflux flow", "kmol/h"),
        ("distillate_flow", "Distillate product flow", "kmol/h"),
        ("steam_factor", "Reboiler steam command", "Factor"),
    ]
    for ax, (field, title, unit) in zip(axes.flat, actuators):
        for stage, label, color, style in (
            ("ctrl", "BPCS request", "#94a3b8", ":"),
            ("sup", "Operator request", "#9333ea", "--"),
            ("safe", "Safety command", "#f59e0b", "-."),
            ("act", "Actual", "#2563eb", "-"),
        ):
            line(ax, f"{stage}.{field}", label, color=color, linestyle=style, linewidth=1.3)
        ax.set(title=title, ylabel=unit)
        ax.legend(fontsize=7, loc="best")
    decorate(fig, axes, "Controller request → operator request → safety command → actual input")
    save(fig, "actuators")

    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    ax = axes[0, 0]
    line(ax, "alarm.active_count", "Active alarms", color="#dc2626", drawstyle="steps-post")
    line(ax, "alarm.unack_count", "Unacknowledged alarms", color="#f59e0b", drawstyle="steps-post")
    ax.set(title="Operator alarms", ylabel="Count")
    ax.legend(fontsize=8)

    def heatmap(ax, keys, labels, title, colors, ticks):
        data = np.ma.masked_invalid(np.vstack([values(key) for key in keys]))
        cmap = ListedColormap(colors)
        cmap.set_bad("#e2e8f0")
        # Keep recorded (possibly irregular) physical times on the heatmap.
        mesh = ax.pcolormesh(time, np.arange(len(labels)), data,
                             shading="nearest", cmap=cmap,
                             norm=BoundaryNorm(np.arange(len(colors) + 1) - 0.5, len(colors)))
        ax.set_yticks(np.arange(len(labels)), labels, fontsize=8)
        ax.set_title(title)
        ax.invert_yaxis()
        bar = fig.colorbar(mesh, ax=ax, ticks=range(len(colors)), pad=0.02)
        bar.ax.set_yticklabels(ticks, fontsize=8)

    causes = SafetySystem.CAUSES
    heatmap(axes[0, 1], [f"sif.latched.{cause}" for cause in causes], causes,
            "Latched safety functions", ["#f1f5f9", "#dc2626"], ["Clear", "Latched"])
    heatmap(axes[1, 0], [f"pump.{train}.selected_failed" for train in PUMP_TRAINS], list(PUMP_TRAINS),
            "Selected pump availability", ["#dcfce7", "#dc2626"], ["Available", "Failed"])
    ax = axes[1, 1]
    for field, label, color in (("cstr_vent_valve", "H2 PCV", "#2563eb"),
                                 ("cstr_bdv_valve", "H2 BDV", "#9333ea"),
                                 ("cstr_emergency_cooling_factor", "Emergency cooling", "#0891b2")):
        line(ax, f"act.{field}", label, color=color)
    line(ax, "sif.plant_trip_latched", "Plant-trip latch", color="#dc2626", linestyle="--")
    ax.set(title="Actual emergency/protection commands", ylabel="Fraction / latch")
    ax.legend(fontsize=8)
    decorate(fig, axes, "Alarm, safety-function and equipment timelines")
    save(fig, "safety")

    if show:
        plt.show()
        for fig in figures:
            plt.close(fig)
    return paths
