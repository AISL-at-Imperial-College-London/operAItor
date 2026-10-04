from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from integrated_plant import (
    CURATED_EPISODES,
    EpisodeRunner, EpisodeTiming, IntegratedPlantDAE, PassiveActor,
    SafetySystem, SafetyConfig,
    curated_episode, nominal_episode, normal_variability_episode,
    write_records_csv,
)


def _sif_interrupt_table(plant: IntegratedPlantDAE):
    nominal = plant.default_inputs()
    y0 = plant.nominal_state(nominal)
    base = plant.measurements(y0)
    cases = [
        ("R_T_HH", {"cstr_temperature_K": 349.0}),
        ("R_P_HH", {"cstr_pressure_bar": 4.10}),
        ("R_P_HHH", {"cstr_pressure_bar": 4.50}),
        ("R_L_HH", {"cstr_holdup_kmol": 73.0}),
        ("R_L_LL", {"cstr_holdup_kmol": 19.0}),
        ("RB_L_LL", {"reboiler_holdup_kmol": 10.0}),
        ("RB_L_HH", {"reboiler_holdup_kmol": 55.0}),
        ("DR_L_LL", {"drum_holdup_kmol": 9.0}),
        ("DR_L_HH", {"drum_holdup_kmol": 50.0}),
        ("COL_P_HH", {"column_pressure_bar": 1.40}),
    ]
    rows = []
    for name, patch in cases:
        m = dict(base); m.update(patch)
        sif = SafetySystem(SafetyConfig())
        res = sif.apply(m, nominal, nominal)
        u = res.commanded
        rows.append({
            "cause": name,
            "active_causes": ";".join(res.active_causes),
            "latched_causes": ";".join(res.latched_causes),
            "plant_trip_latched": int(res.plant_trip_latched),
            "fresh_ipa_feed": u.fresh_ipa_feed,
            "cstr_outflow": u.cstr_outflow,
            "cstr_heater_factor": u.cstr_heater_factor,
            "cstr_vent_valve": u.cstr_vent_valve,
            "cstr_bdv_valve": u.cstr_bdv_valve,
            "cstr_emergency_cooling_factor": u.cstr_emergency_cooling_factor,
            "reflux_flow": u.reflux_flow,
            "steam_factor": u.steam_factor,
            "bottoms_flow": u.bottoms_flow,
            "distillate_flow": u.distillate_flow,
            "condenser_cooling_valve": u.condenser_cooling_valve,
            "condenser_cooling_effectiveness": u.condenser_cooling_effectiveness,
        })
    return rows


def _run_case(spec, outdir: Path, timing: EpisodeTiming):
    plant = IntegratedPlantDAE()
    env = EpisodeRunner(plant, spec, timing=timing)
    _, records, state = env.run(PassiveActor(), pad_to_horizon=True)
    write_records_csv(records, outdir / f"{spec.name}.csv")

    def col(name):
        return [float(r[name]) for r in records if int(r.get("episode.active", 1)) == 1]

    sif_causes = []
    for cause in env.safety.CAUSES:
        if any(int(r[f"sif.latched.{cause}"]) for r in records):
            sif_causes.append(cause)

    return {
        "case": spec.name,
        "horizon_min": spec.scenario.duration_h * 60.0,
        "terminal_status": state.status.value,
        "terminal_reason": state.reason,
        "terminal_time_min": None if state.terminal_time_h is None else state.terminal_time_h * 60.0,
        "sif_latches_seen": ";".join(sif_causes) if sif_causes else "none",
        "max_cstr_pressure_bar": max(col("true.cstr_pressure_bar")),
        "max_cstr_temperature_K": max(col("true.cstr_temperature_K")),
        "max_cstr_holdup_kmol": max(col("true.cstr_holdup_kmol")),
        "min_cstr_holdup_kmol": min(col("true.cstr_holdup_kmol")),
        "max_column_pressure_bar": max(col("true.column_pressure_bar")),
        "min_reboiler_holdup_kmol": min(col("true.reboiler_holdup_kmol")),
        "min_drum_holdup_kmol": min(col("true.drum_holdup_kmol")),
    }


def main():
    out = Path("prototype_test_results")
    out.mkdir(exist_ok=True)
    timing = EpisodeTiming(plant_step_min=0.1, decision_interval_min=5.0, control_interval_min=1.0)

    specs = [
        nominal_episode(90.0),
        normal_variability_episode(90.0, seed=20261004),
    ]
    specs += [curated_episode(name, duration_min=90.0, onset_min=30.0) for name in CURATED_EPISODES]

    summaries = []
    for spec in specs:
        print(f"running {spec.name} ...")
        summaries.append(_run_case(spec, out, timing))

    with (out / "episode_summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summaries[0].keys()))
        w.writeheader(); w.writerows(summaries)

    plant = IntegratedPlantDAE()
    sif_rows = _sif_interrupt_table(plant)
    with (out / "sif_interrupt_check.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(sif_rows[0].keys()))
        w.writeheader(); w.writerows(sif_rows)

    variability = specs[1].scenario
    disturbance_rows = [
        {
            "name": e.name,
            "kind": e.kind,
            "target": e.target,
            "start_min": e.start_h * 60.0,
            "end_min": None if e.end_h is None else e.end_h * 60.0,
            "fractional_step": e.magnitude,
        }
        for e in variability.events
    ]
    with (out / "random_disturbance_schedule.csv").open("w", newline="", encoding="utf-8") as f:
        if disturbance_rows:
            w = csv.DictWriter(f, fieldnames=list(disturbance_rows[0].keys()))
            w.writeheader(); w.writerows(disturbance_rows)

    report = {
        "timing": asdict(timing),
        "disturbance_seed": 20261004,
        "disturbance_rule": "random subset; independent 15 min rectangular pulses; +/-10% of nominal; overlap allowed",
        "episode_summaries": summaries,
    }
    (out / "prototype_test_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\nSummary")
    for row in summaries:
        print(f"{row['case']:34s} {row['terminal_status']:12s} t={row['terminal_time_min']:.1f} min  SIF={row['sif_latches_seen']}")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
