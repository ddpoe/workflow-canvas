"""Fixture: quantify — 2-slot fan-in (stitched skip-level + masks immediate)."""

import csv
import json
import os
from pathlib import Path

STAGE = "quantify"


def main():
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    slot_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{}"))
    primary = Path(slot_paths["masks"][0])
    with primary.open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [row + [STAGE] for row in reader]
    out_path = run_dir / "measurements.csv"
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header + ["stage"])
        w.writerows(rows)
    print(f"{STAGE}: fan-in slots={list(slot_paths)}; wrote {len(rows)} rows")


if __name__ == "__main__":
    main()
