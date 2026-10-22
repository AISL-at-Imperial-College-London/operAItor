"""Closed-loop setpoint tests for the 8 regulatory PI loops (BPCS feedback loops), with plots.

For every loop, from the nominal steady state (sensor noise on):
  0-5 min    hold at the design setpoint
  5 min      setpoint step up (STEPS below)
  125 min    setpoint back to the design value (2-h holds, so even the slow loops settle)
  245 min    end
Checks per step: the controller output first moves in the direction that drives PV toward SP;
PV ends within 20 % of the step (or two deadbands) of the setpoint; no SIF, no priority-1 alarm.
Overshoot and settling time (PV stays inside that band) are reported for both steps.
Disturbance rejection: +3 K cooling water (COL_PIC), +5 K feed temperature (R_TIC), +10 % steam
pressure (BOT_TIC), each a 40-min step.

    py -3 -m operaitor.benchmark.loop_tests     # table + benchmark_results/loop_tests.json + loop_plots/*.png
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import sys

import numpy as np

from operaitor.plant import EpisodeRunner, IntegratedPlantDAE, RuntimeInputEvent, SupervisoryAction, nominal_episode
from operaitor.plant.scenario import disturbance_step
from operaitor.server.plant_service import PlantService

STEPS = {  # loop -> setpoint step (same sign convention as the loop's PV)
    "R_LIC": +3.0, "R_TIC": +1.5, "R_PIC": +0.10, "RB_LIC": +3.0,
    "DR_LIC": +3.0, "COL_PIC": +0.05, "TOP_TIC": +1.0, "BOT_TIC": +1.5,
}
T_UP, HOLD = 5.0, 120.0
T_BACK, T_END = T_UP + HOLD, T_UP + 2 * HOLD
DISTURBANCES = [("cooling_water_temperature_K", "COL_PIC"), ("fresh_feed_temperature_K", "R_TIC"),
                ("steam_pressure_bar", "BOT_TIC")]
from .profit_campaign import OUT
PLOTS = OUT / "loop_plots"


def _env(minutes: float) -> EpisodeRunner:
    return EpisodeRunner(IntegratedPlantDAE(), nominal_episode(minutes), timing=PlantService.TIMING)


def _series(env: EpisodeRunner, spec, ctrl_attr: str, rows: list) -> None:
    ctrl = getattr(env.controllers, ctrl_attr)
    rows.append({"t": 60 * env.t_h, "pv": float(env.plant.measurements(env.y)[spec.pv_tag]),
                 "pv_meas": float(env.last_measured[spec.pv_tag]),
                 "sp": float(getattr(env.controllers.sp, spec.setpoint_attr)), "mv": float(ctrl.last_output),
                 "sif": list(env.last_safety_result.latched_causes),
                 "p1": [s.definition.alarm_id for s in env.alarms.active() if s.definition.priority == 1]})


def _step_metrics(rows: list, t0: float, t1: float, sp_before: float, sp_after: float, ctrl) -> dict:
    seg = [r for r in rows if t0 < r["t"] <= t1 + 1e-9]
    step = sp_after - sp_before
    band = max(0.2 * abs(step), 2 * ctrl.deadband)
    pv = np.array([r["pv"] for r in seg]); t = np.array([r["t"] for r in seg]); mv = np.array([r["mv"] for r in seg])
    mv0 = next(r["mv"] for r in reversed(rows) if r["t"] <= t0 + 1e-9)
    first = next((m - mv0 for m in mv if abs(m - mv0) > 1e-9), 0.0)
    expected = np.sign(ctrl.kp * step)
    inside = np.abs(pv - sp_after) <= band
    settle = next((t[i] - t0 for i in range(len(t)) if inside[i:].all()), None)
    overshoot = float(max(0.0, np.max((pv - sp_after) * np.sign(step))) / abs(step) * 100)
    return {"from": sp_before, "to": sp_after, "pv_end": float(pv[-1]), "error_end": float(pv[-1] - sp_after),
            "band": band, "mv_direction_ok": bool(np.sign(first) == expected), "settling_min": settle,
            "overshoot_pct": round(overshoot, 1), "ok": bool(np.sign(first) == expected and inside[-1])}


def step_test(loop: str, hold: float = HOLD, plot: bool = True) -> dict:
    t_back, t_end = T_UP + hold, T_UP + 2 * hold
    env = _env(t_end + 1)
    spec = env.controllers.LOOP_SPECS[loop]
    ctrl = getattr(env.controllers, spec.controller_attr)
    sp0 = float(getattr(env.controllers.sp, spec.setpoint_attr))
    sp1 = sp0 + STEPS[loop]
    rows: list = []
    _series(env, spec, spec.controller_attr, rows)
    while 60 * env.t_h < t_end - 1e-9:
        t = 60 * env.t_h
        if abs(t - T_UP) < 1e-6:
            env.apply_action(SupervisoryAction(setpoints={loop: sp1}))
        if abs(t - t_back) < 1e-6:
            env.apply_action(SupervisoryAction(setpoints={loop: sp0}))
        env.step()
        _series(env, spec, spec.controller_attr, rows)
    up = _step_metrics(rows, T_UP, t_back, sp0, sp1, ctrl)
    back = _step_metrics(rows, t_back, t_end, sp1, sp0, ctrl)
    sifs = sorted({c for r in rows for c in r["sif"]})
    p1 = sorted({a for r in rows for a in r["p1"]})
    res = {"loop": loop, "pv": spec.pv_tag, "mv": spec.mv_field, "pv_unit": spec.sp_unit, "mv_unit": spec.mv_unit,
           "sp0": sp0, "sp1": sp1, "t_back": t_back, "t_end": t_end, "step_up": up, "step_back": back,
           "sifs": sifs, "p1_alarms": p1,
           "pass": bool(up["ok"] and back["ok"] and not sifs and not p1),
           # legacy single-step fields (dashboard)
           "pv_end": up["pv_end"], "overshoot_pct": up["overshoot_pct"], "settling_min": up["settling_min"],
           "mv_direction_ok": up["mv_direction_ok"],
           "series": {k: [round(r[k], 5) for r in rows] for k in ("t", "pv", "pv_meas", "sp", "mv")}}
    res["plot"] = _plot_step(res) if plot else ""
    return res


def disturbance_test(target: str, loop: str, minutes: float = 40.0) -> dict:
    env = _env(minutes + 6)
    spec = env.controllers.LOOP_SPECS[loop]
    ctrl = getattr(env.controllers, spec.controller_attr)
    sp = float(getattr(env.controllers.sp, spec.setpoint_attr))
    mode, value, label = disturbance_step(target, +1.0)
    env.runtime_inputs.add(RuntimeInputEvent(f"test_{target}", "disturbance", target, 5 / 60, (5 + minutes) / 60, mode, value))
    rows: list = []
    _series(env, spec, spec.controller_attr, rows)
    while 60 * env.t_h < minutes + 5 - 1e-9:
        env.step()
        _series(env, spec, spec.controller_attr, rows)
    pv = np.array([r["pv"] for r in rows]); mv = np.array([r["mv"] for r in rows])
    sifs = sorted({c for r in rows for c in r["sif"]})
    ok = bool(abs(pv[-1] - sp) <= max(2 * ctrl.deadband, 0.002 * abs(sp)) and not sifs)
    res = {"disturbance": f"{target} {label}", "target": target, "loop": loop, "sp": sp, "pv_end": float(pv[-1]),
           "max_deviation": float(np.max(np.abs(pv - sp))), "mv_start": float(mv[0]), "mv_end": float(mv[-1]),
           "sifs": sifs, "pass": ok, "pv_unit": spec.sp_unit, "mv_unit": spec.mv_unit, "pv": spec.pv_tag, "mv": spec.mv_field,
           "series": {k: [round(r[k], 5) for r in rows] for k in ("t", "pv", "pv_meas", "sp", "mv")}}
    res["plot"] = _plot_step(res, title=f"{loop}: disturbance {target.replace('_', ' ')} {label} at 5 min")
    return res


def _plot_step(res: dict, title: str | None = None) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    s = res["series"]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(8, 5.2), sharex=True, gridspec_kw={"height_ratios": [3, 2]})
    a1.plot(s["t"], s["pv_meas"], color="#9db7c6", lw=0.8, label="PV measured (noisy)")
    a1.plot(s["t"], s["pv"], color="#1f5f80", lw=1.8, label="PV true")
    a1.step(s["t"], s["sp"], where="post", color="#c0392b", lw=1.4, ls="--", label="setpoint")
    a1.set_ylabel(f"{res['pv'].replace('_', ' ')} [{res['pv_unit']}]")
    a1.legend(loc="best", fontsize=8, frameon=False)
    a1.grid(alpha=0.3)
    a2.plot(s["t"], s["mv"], color="#7a5c2e", lw=1.5)
    a2.set_ylabel(f"output: {res['mv'].replace('_', ' ')}\n[{res['mv_unit']}]", fontsize=9)
    a2.set_xlabel("time [min]")
    a2.grid(alpha=0.3)
    if title is None:
        u, b = res["step_up"], res["step_back"]
        fmt = lambda x: "not settled" if x is None else f"settled {x:.1f} min"  # noqa: E731
        title = (f"{res['loop']}: SP {res['sp0']:.4g} → {res['sp1']:.4g} at {T_UP:g} min, back at {res['t_back']:g} min\n"
                 f"up: overshoot {u['overshoot_pct']:.0f} %, {fmt(u['settling_min'])} · "
                 f"back: overshoot {b['overshoot_pct']:.0f} %, {fmt(b['settling_min'])} · "
                 f"{'PASS' if res['pass'] else 'stable, slow'}")
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    PLOTS.mkdir(parents=True, exist_ok=True)
    name = f"{res['loop']}_{'disturbance_' + res['target'] if 'target' in res else 'setpoint'}.png"
    fig.savefig(PLOTS / name, dpi=110)
    plt.close(fig)
    return f"loop_plots/{name}"


def _job(args):
    kind, a, b = args
    return step_test(a) if kind == "step" else disturbance_test(a, b)


def main() -> dict:
    for s in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    jobs = [("step", k, None) for k in STEPS] + [("dist", t, l) for t, l in DISTURBANCES]
    with ProcessPoolExecutor(len(jobs)) as ex:
        results = list(ex.map(_job, jobs))
    steps, dists = results[:len(STEPS)], results[len(STEPS):]
    print(f"{'loop':8s} {'SP step':>20s} {'up: OS% settle':>18s} {'back: OS% settle':>18s}  pass")
    def fmt(m: dict) -> str:
        settle = "—" if m["settling_min"] is None else f"{m['settling_min']:.1f}"
        return f"{m['overshoot_pct']:5.0f} {settle:>8s}"
    for r in steps:
        print(f"{r['loop']:8s} {r['sp0']:9.3f}→{r['sp1']:9.3f} {fmt(r['step_up']):>18s} {fmt(r['step_back']):>18s}  "
              f"{r['pass']}  {r['plot']}")
    for r in dists:
        print(f"{r['disturbance']:40s} {r['loop']:8s} max dev {r['max_deviation']:.3f}  {r['pass']}  {r['plot']}")
    OUT.mkdir(exist_ok=True)
    slim = lambda r: {k: v for k, v in r.items() if k != "series"}  # noqa: E731
    (OUT / "loop_tests.json").write_text(json.dumps({"setpoint_steps": [slim(r) for r in steps],
                                                     "disturbances": [slim(r) for r in dists]}, indent=1, default=str),
                                         encoding="utf-8")
    (OUT / "loop_series.json").write_text(json.dumps({r.get("loop") + ("_dist_" + r["target"] if "target" in r else ""):
                                                      r["series"] for r in steps + dists}), encoding="utf-8")
    return {"setpoint_steps": steps, "disturbances": dists}


if __name__ == "__main__":
    main()
