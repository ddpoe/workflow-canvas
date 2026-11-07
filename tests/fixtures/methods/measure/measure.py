"""Fixture method: measure -- add a `{stat}_value` column and a `note_source` column.

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
    stat = str(params.get("stat", "mean"))

    data_paths = slot_paths.get("data", [])
    if not data_paths or not Path(data_paths[0]).exists():
        raise FileNotFoundError(f"Input file not found: {data_paths}")
    input_path = Path(data_paths[0])

    with open(input_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    measured = f"{stat}_value"
    for row in rows:
        row[measured] = row.get("value", "")
        row["note_source"] = input_path.name

    output_path = run_dir / "output.csv"
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames + [measured, "note_source"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"measure: wrote {len(rows)} rows with {measured} and note_source")


if __name__ == "__main__":
    main()
