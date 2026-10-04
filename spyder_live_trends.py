"""Simple live Matplotlib monitor intended for Spyder.

Edit the CONFIG block below and Run File in Spyder.  For interactive fault and
disturbance injection use the richer Bokeh master console instead.
"""
from __future__ import annotations

from dataclasses import replace
import time
import matplotlib.pyplot as plt

from integrated_plant import (
    CURATED_EPISODES, EpisodeRunner, EpisodeTiming, IntegratedPlantDAE,
    PassiveActor, curated_episode, nominal_episode, normal_variability_episode,
    surprise_episode,
)

# ---------------------------- CONFIG ---------------------------------------
MODE = "nominal"  # nominal | variability | surprise | any name in CURATED_EPISODES
SEED = 20261004
HORIZON_MIN = 180.0
WALL_PAUSE_S_PER_DISPLAY_STEP = 0.05
STOP_ON_PLANT_TRIP = False
ROLLING_WINDOW_MIN = 120.0
# ---------------------------------------------------------------------------


def build_spec():
    if MODE == "nominal":
        spec = nominal_episode(HORIZON_MIN)
    elif MODE == "variability":
        spec = normal_variability_episode(HORIZON_MIN, SEED)
    elif MODE == "surprise":
        spec = surprise_episode(HORIZON_MIN, SEED)
    elif MODE in CURATED_EPISODES:
        spec = curated_episode(MODE, HORIZON_MIN)
    else:
        raise ValueError(f"Unknown MODE: {MODE}")
    return replace(spec, terminate_on_plant_trip=STOP_ON_PLANT_TRIP, allow_recovery_termination=False)


plant = IntegratedPlantDAE()
env = EpisodeRunner(plant, build_spec(), timing=EpisodeTiming(0.1, 0.5, 1.0))
actor = PassiveActor()

plt.ion()
fig, axes = plt.subplots(3, 2, figsize=(12, 9), constrained_layout=True)
fig.canvas.manager.set_window_title("CSTR + Distillation Live Monitor")
series = {
    "t": [], "rt": [], "rp": [], "rl": [], "cp": [], "tt": [], "bt": [],
    "rb": [], "dr": [], "dx": [], "bx": [],
}


def append_values():
    m = env.last_measured
    series["t"].append(60*env.t_h)
    series["rt"].append(m["cstr_temperature_K"])
    series["rp"].append(m["cstr_pressure_bar"])
    series["rl"].append(m["cstr_holdup_kmol"])
    series["cp"].append(m["column_pressure_bar"])
    series["tt"].append(m["top_temperature_K"])
    series["bt"].append(m["bottom_temperature_K"])
    series["rb"].append(m["reboiler_holdup_kmol"])
    series["dr"].append(m["drum_holdup_kmol"])
    series["dx"].append(m["distillate_x_acetone"])
    series["bx"].append(m["bottoms_x_acetone"])


def draw():
    t = series["t"]
    for ax in axes.flat:
        ax.clear()
        ax.grid(True, alpha=0.25)
        ax.set_xlabel("simulated time [min]")
        if t:
            ax.set_xlim(max(0, t[-1]-ROLLING_WINDOW_MIN), max(5, t[-1]+1))
    axes[0,0].plot(t, series["rt"]); axes[0,0].set_title("Reactor T [K]")
    axes[0,1].plot(t, series["rp"]); axes[0,1].set_title("Reactor P [bar]")
    axes[1,0].plot(t, series["rl"]); axes[1,0].set_title("Reactor holdup [kmol]")
    axes[1,1].plot(t, series["cp"]); axes[1,1].set_title("Column P [bar]")
    axes[2,0].plot(t, series["tt"], label="Top T"); axes[2,0].plot(t, series["bt"], label="Bottom T"); axes[2,0].legend(); axes[2,0].set_title("Column T [K]")
    axes[2,1].plot(t, series["dx"], label="Distillate xA"); axes[2,1].plot(t, series["bx"], label="Bottoms xA"); axes[2,1].legend(); axes[2,1].set_title("Product compositions")
    fig.suptitle(
        f"{env.spec.name} | t={60*env.t_h:.1f} min | status={env.outcomes.state.status.value} | "
        f"alarms={len(env.alarms.active())}, unack={len(env.alarms.unacknowledged())} | "
        f"SIF={','.join(env.last_safety_result.latched_causes) or 'none'}"
    )
    fig.canvas.draw_idle()
    plt.pause(0.001)


append_values(); draw()
while not env.done and plt.fignum_exists(fig.number):
    env.step(actor.act(env.observation()))
    append_values(); draw()
    if WALL_PAUSE_S_PER_DISPLAY_STEP > 0:
        time.sleep(WALL_PAUSE_S_PER_DISPLAY_STEP)

print("Episode finished:", env.outcomes.state.status.value, env.outcomes.state.reason)
plt.ioff()
plt.show()
