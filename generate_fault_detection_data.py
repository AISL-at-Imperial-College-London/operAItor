"""Generate the complete offline FDD data campaign in one command.

The heavy numerical workers are launched before NumPy/SciPy are imported in this
parent process. This keeps the three independent simulations isolated and avoids
thread-pool/fork interactions on some platforms.
"""
from pathlib import Path
import os
import pickle
import shutil
import subprocess
import sys
import tempfile

SEGMENT_DURATION_MIN = 120.0
PRODUCTION_LEVELS = (1.00, 0.85, 0.70, 0.85, 1.00)
CONTROL_AND_MEASUREMENT_INTERVAL_MIN = 1.0
RANDOM_SEED = 20261001
OUTPUT_DIRECTORY = Path("fdd_results")


def main():
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="distillation_fdd_"))
    worker_files = []
    levels_arg = ",".join(str(x) for x in PRODUCTION_LEVELS)

    try:
        # Run the three independent numerical experiments first, before importing
        # NumPy/SciPy/Matplotlib into the parent process.
        env = os.environ.copy()
        env["OPENBLAS_NUM_THREADS"] = "1"
        env["OMP_NUM_THREADS"] = "1"
        env["MKL_NUM_THREADS"] = "1"
        for block_id in (1, 2, 3):
            print(f"running block {block_id}", flush=True)
            pkl = tmp / f"block_{block_id}.pkl"
            cmd = [
                sys.executable, str(Path(__file__).with_name("generate_block_worker.py")),
                "--block", str(block_id),
                "--segment-min", str(SEGMENT_DURATION_MIN),
                "--dt-min", str(CONTROL_AND_MEASUREMENT_INTERVAL_MIN),
                "--levels", levels_arg,
                "--seed", str(RANDOM_SEED),
                "--output", str(pkl),
            ]
            subprocess.run(cmd, check=True, env=env)
            worker_files.append(pkl)
            print(f"finished block {block_id}", flush=True)

        # Imports below happen only after all numerical workers have exited.
        import csv
        import json
        import numpy as np
        from integrated_plant import IntegratedPlantDAE
        from integrated_plant.scenario import fault_detection_campaign
        from integrated_plant.simulate import write_records_csv
        from integrated_plant.plotting import plot_key_measurements, plot_manipulated_and_disturbances

        campaign = fault_detection_campaign(SEGMENT_DURATION_MIN, PRODUCTION_LEVELS)
        all_records = []
        all_t = []
        all_Y = []

        for ib, (block, pkl) in enumerate(zip(campaign.blocks, worker_files)):
            with pkl.open("rb") as f:
                data = pickle.load(f)
            t, Y, records = data["t"], data["Y"], data["records"]
            for row in records:
                row["block_time_min"] = float(row["time_min"])
                row["time_h"] = float(row["time_h"]) + block.global_offset_h
                row["time_min"] = float(row["time_min"]) + 60.0 * block.global_offset_h
            tg = t + block.global_offset_h
            if ib < len(campaign.blocks) - 1:
                records = records[:-1]
                tg = tg[:-1]
                Y = Y[:-1]
            all_records.extend(records)
            all_t.append(tg)
            all_Y.append(Y)

        data_path = OUTPUT_DIRECTORY / "fault_detection_dataset.csv"
        events_path = OUTPUT_DIRECTORY / "fault_schedule.csv"
        state_path = OUTPUT_DIRECTORY / "dae_state_history.npz"
        meta_path = OUTPUT_DIRECTORY / "campaign_metadata.json"
        plot1 = OUTPUT_DIRECTORY / "key_measurements.png"
        plot2 = OUTPUT_DIRECTORY / "manipulated_and_disturbances.png"

        write_records_csv(all_records, data_path)
        keys = ["block_id", "fault_name", "fault_type", "target", "start_min", "end_min", "magnitude", "mode"]
        with events_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(campaign.event_table())
        np.savez_compressed(state_path, time_h=np.concatenate(all_t), state=np.vstack(all_Y))
        plot_key_measurements(all_records, plot1)
        plot_manipulated_and_disturbances(all_records, plot2)

        reference_plant = IntegratedPlantDAE()
        metadata = {
            "segment_duration_min": SEGMENT_DURATION_MIN,
            "production_levels": list(PRODUCTION_LEVELS),
            "blocks": [b.name for b in campaign.blocks],
            "block_duration_min": 60.0 * campaign.block_duration_h,
            "stacked_duration_min": 60.0 * campaign.duration_h,
            "blocks_are_independent_runs": True,
            "control_measurement_interval_min": CONTROL_AND_MEASUREMENT_INTERVAL_MIN,
            "random_seed_first_block": RANDOM_SEED,
            "n_dataset_rows": len(all_records),
            "n_dataset_columns": len(all_records[0]),
            "n_dae_variables": reference_plant.n_variables,
            "cstr_pressure_control": {
                "controlled_variable": "cstr_pressure_bar",
                "manipulated_variable": "cstr_vent_valve",
                "downstream_pressure_bar": reference_plant.cstr.cfg.vent_backpressure_bar,
                "valve_law": "F = opening * Cv * sqrt(max(P_cstr-P_atm,0))",
            },
            "measurement_noise_1sigma": {
                "composition_mole_fraction": 0.0025,
                "temperature_K": 0.15,
                "pressure_fraction_approx": 0.0025,
                "holdup_and_flow_fraction_approx": 0.005,
            },
            "reflux_fault": "20-minute freeze of reflux temperature-control output",
            "full_reflux_shutdown_support": (
                "ActuatorFault supports fixed reflux_flow=0, but long total shutdown may dry "
                "upper trays; dry-tray phase-disappearance logic is not yet included."
            ),
        }
        meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        print(f"completed stacked dataset: {len(all_records)} rows", flush=True)
        print(f"dataset: {data_path}", flush=True)
        print(f"fault schedule: {events_path}", flush=True)
        print(f"plots: {plot1}, {plot2}", flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
