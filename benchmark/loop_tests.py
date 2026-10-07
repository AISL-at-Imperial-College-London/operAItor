"""Closed-loop step tests for the 8 regulatory PI loops (BPCS feedback loops).

For every loop, starting from the nominal steady state: apply a setpoint step, run
40 sim-min with sensor noise, and check that
  * the manipulated variable first moves in the direction that drives PV toward SP,
  * PV settles near the new SP (|PV - SP| within 20 % of the step, or 2 deadbands),
  * no SIF activates and no priority-1 alarm comes on.
Also checks disturbance rejection: +3 K cooling water (COL_PIC must open its valve and hold
column pressure) and +5 K feed temperature (R_TIC must hold reactor temperature).

    py -3 -m benchmark.loop_tests          # prints the table, writes benchmark_results/loop_tests.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from integrated_plant import EpisodeRunner, IntegratedPlantDAE, RuntimeInputEvent, SupervisoryAction, nominal_episode
from integrated_plant.scenario import disturbance_step
from server.plant_service import PlantService

STEPS = {  # loop -> setpoint step (same sign convention as the loop's PV)
    "R_LIC": +3.0, "R_TIC": +1.5, "R_PIC": +0.10, "RB_LIC": +3.0,
    "DR_LIC": +3.0, "COL_PIC": +0.05, "TOP_TIC": +1.0, "BOT_TIC": +1.5,
}
OUT = Path(__file__).resolve().parent.parent / "benchmark_results"


def _env(minutes: float) -> EpisodeRunner:
    return EpisodeRunner(IntegratedPlantDAE(), nominal_episode(minutes), timing=PlantService.TIMING)


def _run(env: EpisodeRunner, minutes: float, rec) -> None:
    for _ in range(int(round(minutes / env.timing.decision_interval_min))):
        env.step()
        rec(env)


def step_test(loop: str, step: float, minutes: float = 40.0) -> dict:
    env = _env(minutes + 5)
    spec = env.controllers.LOOP_SPECS[loop]
    ctrl = getattr(env.controllers, spec.controller_attr)
    sp0 = float(getattr(env.controllers.sp, spec.setpoint_attr))
    mv0 = float(ctrl.last_output)
    env.apply_action(SupervisoryAction(setpoints={loop: sp0 + step}))
    rows = []

    def rec(e):
        true = e.plant.measurements(e.y)
        rows.append((60 * e.t_h, true[spec.pv_tag], float(getattr(e.controllers, spec.controller_attr).last_output),
                     list(e.last_safety_result.latched_causes),
                     [s.definition.alarm_id for s in e.alarms.active() if s.definition.priority == 1]))
    _run(env, minutes, rec)
    t = np.array([r[0] for r in rows]); pv = np.array([r[1] for r in rows]); mv = np.array([r[2] for r in rows])
    sp = sp0 + step
    first_mv = next((m - mv0 for m in mv if abs(m - mv0) > 1e-9), 0.0)
    # direct-acting (kp < 0): MV up lowers... use the controller gain sign to get the expected direction
    expected = np.sign(ctrl.kp * step)
    band = max(0.2 * abs(step), 2 * ctrl.deadband)
    inside = np.abs(pv - sp) <= band
    settle = next((t[i] for i in range(len(t)) if inside[i:].all()), None)
    overshoot = float(max(0.0, np.max((pv - sp) * np.sign(step))) / abs(step) * 100)
    sifs = sorted({c for r in rows for c in r[3]}); p1 = sorted({a for r in rows for a in r[4]})
    ok = bool(np.sign(first_mv) == expected and inside[-1] and not sifs and not p1)
    return {"loop": loop, "pv": spec.pv_tag, "mv": spec.mv_field, "sp0": sp0, "sp1": sp, "pv_end": float(pv[-1]),
            "error_end": float(pv[-1] - sp), "band": band, "mv_first_move": float(first_mv),
            "mv_direction_ok": bool(np.sign(first_mv) == expected), "settling_min": settle,
            "overshoot_pct": round(overshoot, 1), "sifs": sifs, "p1_alarms": p1, "pass": ok}


def disturbance_test(target: str, loop: str, minutes: float = 40.0) -> dict:
    env = _env(minutes + 5)
    spec = env.controllers.LOOP_SPECS[loop]
    sp = float(getattr(env.controllers.sp, spec.setpoint_attr))
    mode, value, label = disturbance_step(target, +1.0)
    env.runtime_inputs.add(RuntimeInputEvent(f"test_{target}", "disturbance", target, 0.0, minutes / 60, mode, value))
    rows = []
    _run(env, minutes, lambda e: rows.append((e.plant.measurements(e.y)[spec.pv_tag],
                                              float(getattr(e.controllers, spec.controller_attr).last_output),
                                              list(e.last_safety_result.latched_causes))))
    pv = np.array([r[0] for r in rows]); mv = np.array([r[1] for r in rows])
    ctrl = getattr(env.controllers, spec.controller_attr)
    worst = float(np.max(np.abs(pv - sp)))
    sifs = sorted({c for r in rows for c in r[2]})
    ok = bool(abs(pv[-1] - sp) <= max(2 * ctrl.deadband, 0.01 * abs(sp) * 0.2) and not sifs)
    return {"disturbance": f"{target} {label}", "loop": loop, "sp": sp, "pv_end": float(pv[-1]),
            "max_deviation": worst, "mv_start": float(mv[0]), "mv_end": float(mv[-1]), "sifs": sifs, "pass": ok}


def main() -> dict:
    import sys
    for s in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    steps = [step_test(k, v) for k, v in STEPS.items()]
    dists = [disturbance_test("cooling_water_temperature_K", "COL_PIC"),
             disturbance_test("fresh_feed_temperature_K", "R_TIC"),
             disturbance_test("steam_pressure_bar", "BOT_TIC")]
    print(f"{'loop':8s} {'PV':24s} {'SP0→SP1':>18s} {'PV end':>9s} {'MV dir':>6s} {'settle':>7s} {'OS%':>5s}  pass")
    for r in steps:
        print(f"{r['loop']:8s} {r['pv']:24s} {r['sp0']:8.3f}→{r['sp1']:8.3f} {r['pv_end']:9.3f} "
              f"{str(r['mv_direction_ok']):>6s} {str(r['settling_min'] and round(r['settling_min'], 1)):>7s} "
              f"{r['overshoot_pct']:5.1f}  {r['pass']}  {r['sifs'] or ''}{r['p1_alarms'] or ''}")
    for r in dists:
        print(f"{r['disturbance']:40s} {r['loop']:8s} SP {r['sp']:.3f} PV end {r['pv_end']:.3f} "
              f"max dev {r['max_deviation']:.3f}  MV {r['mv_start']:.3f}→{r['mv_end']:.3f}  {r['pass']} {r['sifs'] or ''}")
    OUT.mkdir(exist_ok=True)
    res = {"setpoint_steps": steps, "disturbances": dists}
    (OUT / "loop_tests.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


if __name__ == "__main__":
    main()
