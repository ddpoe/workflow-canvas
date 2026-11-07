"""Fixture method: pick_column -- keep one column of the input CSV.

Reads WFC_RUN_DIR, WFC_INPUT_PATHS, and WFC_PARAMS from environment.
Writes output CSV to <run_dir>/output.csv.
"""

import csv
import json
import os
from pathlib import Path


def main():
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    slot_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{}"))
    params = json.loads(os.environ.get("WFC_PARAMS", "{}"))
    column = str(params.get("column", "value"))

    data_paths = slot_paths.get("data", [])
    if not data_paths or not Path(data_paths[0]).exists():
        raise FileNotFoundError(f"Input file not found: {data_paths}")

    with open(data_paths[0], newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        if column not in (reader.fieldnames or []):
            raise KeyError(f"pick_column: no column {column!r} in the input")

    output_path = run_dir / "output.csv"
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[column])
        writer.writeheader()
        writer.writerows({column: row[column]} for row in rows)

    print(f"pick_column: kept {column} for {len(rows)} rows")


if __name__ == "__main__":
    main()
