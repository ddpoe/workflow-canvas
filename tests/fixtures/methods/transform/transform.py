"""Fixture method: transform -- reads CSV, adds a computed column, writes result.

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
    suffix = params.get("suffix", "_transformed")

    data_paths = slot_paths.get("data", [])
    if not data_paths or not Path(data_paths[0]).exists():
        raise FileNotFoundError(f"Input file not found: {data_paths}")
    input_path = data_paths[0]

    print(f"transform: reading input from {input_path}")
    with open(input_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    print(f"transform: loaded {len(rows)} rows, {len(fieldnames)} columns")

    new_col = f"computed{suffix}"
    fieldnames.append(new_col)
    print(f"transform: adding column '{new_col}' (suffix={suffix!r})")
    for row in rows:
        row[new_col] = f"v_{row.get('id', '')}"

    output_path = run_dir / "output.csv"
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"transform: wrote {len(rows)} rows to {output_path}")


if __name__ == "__main__":
    main()
