from __future__ import annotations

from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt


def _arr(records, key):
    return np.asarray([row[key] for row in records], dtype=float)


def _plot_truth_meas(ax, records, truth_key, meas_key, ylabel, title):
    t = 60.0 * _arr(records, "time_h")
    ax.plot(t, _arr(records, truth_key), linewidth=1.8, label="true")
    ax.plot(t, _arr(records, meas_key), linewidth=0.8, alpha=0.55, label="measured")
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.25)


def plot_key_measurements(records, path: str | Path):
    """Three-row process dashboard: CSTR, overhead, bottoms."""
    t = 60.0 * _arr(records, "time_h")
    fig = plt.figure(figsize=(16, 10), constrained_layout=True)
    axes = [fig.add_subplot(3, 4, i + 1) for i in range(12)]

    _plot_truth_meas(axes[0], records, "true.cstr_x_acetone", "meas.cstr_x_acetone", "mole fraction", "CSTR acetone fraction")
    _plot_truth_meas(axes[1], records, "true.cstr_temperature_K", "meas.cstr_temperature_K", "K", "CSTR temperature")
    _plot_truth_meas(axes[2], records, "true.cstr_pressure_bar", "meas.cstr_pressure_bar", "bar", "CSTR pressure")
    axes[3].plot(t, _arr(records, "true.cstr_level_percent"), linewidth=1.8, label="true")
    axes[3].plot(t, _arr(records, "meas.cstr_level_percent"), linewidth=0.8, alpha=0.55, label="measured")
    axes[3].set_title("CSTR level"); axes[3].set_ylabel("% nominal level"); axes[3].grid(True, alpha=0.25)

    _plot_truth_meas(axes[4], records, "true.distillate_x_acetone", "meas.distillate_x_acetone", "mole fraction", "Distillate acetone fraction")
    _plot_truth_meas(axes[5], records, "true.top_temperature_K", "meas.top_temperature_K", "K", "Top temperature")
    axes[6].plot(t, _arr(records, "true.drum_level_percent"), linewidth=1.8, label="true")
    axes[6].plot(t, _arr(records, "meas.drum_level_percent"), linewidth=0.8, alpha=0.55, label="measured")
    axes[6].set_title("Reflux drum level"); axes[6].set_ylabel("% nominal level"); axes[6].grid(True, alpha=0.25)
    axes[7].plot(t, _arr(records, "derived.reflux_ratio"), linewidth=1.5)
    axes[7].set_title("Reflux ratio R/D"); axes[7].set_ylabel("-"); axes[7].grid(True, alpha=0.25)

    _plot_truth_meas(axes[8], records, "true.bottoms_x_acetone", "meas.bottoms_x_acetone", "mole fraction", "Bottoms acetone fraction")
    _plot_truth_meas(axes[9], records, "true.bottom_temperature_K", "meas.bottom_temperature_K", "K", "Reboiler temperature")
    axes[10].plot(t, _arr(records, "true.reboiler_level_percent"), linewidth=1.8, label="true")
    axes[10].plot(t, _arr(records, "meas.reboiler_level_percent"), linewidth=0.8, alpha=0.55, label="measured")
    axes[10].set_title("Reboiler level"); axes[10].set_ylabel("% nominal level"); axes[10].grid(True, alpha=0.25)
    axes[11].plot(t, _arr(records, "derived.reboiler_duty_MW"), linewidth=1.5)
    axes[11].set_title("Reboiler heat rate"); axes[11].set_ylabel("MW"); axes[11].grid(True, alpha=0.25)

    for ax in axes:
        ax.set_xlabel("time [min]")
    axes[0].legend(loc="best", fontsize=8)
    fig.suptitle("Key process measurements", fontsize=15)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _plot_req_actual(ax, records, field, title, ylabel):
    t = 60.0 * _arr(records, "time_h")
    ax.plot(t, _arr(records, f"req.{field}"), linewidth=1.2, linestyle="--", label="requested")
    ax.plot(t, _arr(records, f"act.{field}"), linewidth=1.5, label="actual")
    ax.set_title(title); ax.set_ylabel(ylabel); ax.grid(True, alpha=0.25)


def plot_manipulated_and_disturbances(records, path: str | Path):
    """Controller outputs / actual actuators plus normal exogenous conditions."""
    t = 60.0 * _arr(records, "time_h")
    fig = plt.figure(figsize=(16, 10), constrained_layout=True)
    axes = [fig.add_subplot(3, 4, i + 1) for i in range(12)]

    axes[0].plot(t, 100.0 * _arr(records, "scenario.throughput_fraction"), linewidth=1.7)
    axes[0].set_title("Throughput command"); axes[0].set_ylabel("% nominal"); axes[0].grid(True, alpha=0.25)
    _plot_req_actual(axes[1], records, "cstr_outflow", "CSTR outlet flow", "kmol/h")
    _plot_req_actual(axes[2], records, "cstr_heater_factor", "CSTR heater valve", "fraction")
    _plot_req_actual(axes[3], records, "cstr_vent_valve", "CSTR vent valve", "fraction")

    _plot_req_actual(axes[4], records, "reflux_flow", "Reflux flow", "kmol/h")
    _plot_req_actual(axes[5], records, "distillate_flow", "Distillate flow", "kmol/h")
    _plot_req_actual(axes[6], records, "bottoms_flow", "Bottoms flow", "kmol/h")
    _plot_req_actual(axes[7], records, "steam_factor", "Steam valve / heat factor", "fraction")

    axes[8].plot(t, _arr(records, "scenario.feed_temperature_K"), linewidth=1.5)
    axes[8].set_title("Reactor feed temperature"); axes[8].set_ylabel("K"); axes[8].grid(True, alpha=0.25)
    axes[9].plot(t, _arr(records, "scenario.cooling_water_temperature_K"), linewidth=1.5)
    axes[9].set_title("Cooling-water inlet temperature"); axes[9].set_ylabel("K"); axes[9].grid(True, alpha=0.25)
    axes[10].plot(t, _arr(records, "scenario.steam_pressure_bar"), linewidth=1.5)
    axes[10].set_title("Steam-header pressure"); axes[10].set_ylabel("bar abs"); axes[10].grid(True, alpha=0.25)
    axes[11].plot(t, _arr(records, "scenario.catalyst_activity"), linewidth=1.5)
    axes[11].set_title("Catalyst activity"); axes[11].set_ylabel("fraction"); axes[11].grid(True, alpha=0.25)

    for ax in axes:
        ax.set_xlabel("time [min]")
    axes[1].legend(loc="best", fontsize=8)
    fig.suptitle("Manipulated variables and normal-operation disturbances", fontsize=15)
    fig.savefig(path, dpi=160)
    plt.close(fig)
