"""Create episode dashboards from an existing run_v05_episode.py CSV."""
import argparse
import csv
from pathlib import Path

from integrated_plant.episode_plotting import plot_episode_results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--output-dir", type=Path, help="Defaults to the CSV directory")
    parser.add_argument("--plot", action="store_true", help="Display the saved figures")
    args = parser.parse_args()
    with args.csv_path.open(encoding="utf-8-sig", newline="") as stream:
        records = list(csv.DictReader(stream))
    for path in plot_episode_results(records, args.output_dir or args.csv_path.parent, show=args.plot):
        print(f"Saved: {path}")


if __name__ == "__main__":
    main()
