"""Internal worker used by generate_fault_detection_data.py.

Not normally called directly.
"""
from __future__ import annotations
import argparse
import pickle
from pathlib import Path
import numpy as np

from integrated_plant import IntegratedPlantDAE, RegulatoryControlSystem
from integrated_plant.measurement import MeasurementModel, default_sensor_specs
from integrated_plant.scenario import fault_detection_campaign
from integrated_plant.simulate import simulate_scenario


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--block", type=int, required=True, choices=(1, 2, 3))
    ap.add_argument("--segment-min", type=float, default=120.0)
    ap.add_argument("--dt-min", type=float, default=1.0)
    ap.add_argument("--levels", type=str, default="1.0,0.85,0.70,0.85,1.0")
    ap.add_argument("--seed", type=int, default=20261001)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    levels = tuple(float(x) for x in args.levels.split(","))
    campaign = fault_detection_campaign(args.segment_min, levels)
    block = campaign.blocks[args.block - 1]

    plant = IntegratedPlantDAE()
    nominal = plant.default_inputs()
    y0 = plant.nominal_state(nominal)
    controllers = RegulatoryControlSystem.from_steady_state(plant, y0, nominal)
    sensors = MeasurementModel(
        default_sensor_specs(plant.measurements(y0)),
        seed=args.seed + block.block_id - 1,
    )

    t, Y, records = simulate_scenario(
        plant,
        y0,
        controllers,
        block.scenario,
        control_interval_h=args.dt_min / 60.0,
        nominal_inputs=nominal,
        measurement_model=sensors,
        actuator_faults=block.actuator_faults,
        label_provider=block.labels,
        progress_every_steps=int(round(120.0 / args.dt_min)),
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("wb") as f:
        pickle.dump({"t": t, "Y": Y, "records": records}, f, protocol=pickle.HIGHEST_PROTOCOL)


if __name__ == "__main__":
    main()
