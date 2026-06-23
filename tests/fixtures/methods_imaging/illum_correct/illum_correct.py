"""Fixture: illum_correct — reads tiles, tags rows, writes corrected.csv."""

import csv
import json
import os
from pathlib import Path

STAGE = "illum_correct"


def main():
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    slot_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{}"))
    in_path = Path(slot_paths["tiles"][0])
    with in_path.open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [row + [STAGE] for row in reader]
    out_path = run_dir / "corrected.csv"
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header + ["stage"])
        w.writerows(rows)
    print(f"{STAGE}: wrote {len(rows)} rows to {out_path.name}")


if __name__ == "__main__":
    main()
