"""Fixture method: scale -- multiply 'value' by factor, round to precision.

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
    factor = float(params.get("factor", 1.0))
    precision = int(params.get("precision", 2))

    data_paths = slot_paths.get("data", [])
    if not data_paths or not Path(data_paths[0]).exists():
        raise FileNotFoundError(f"Input file not found: {data_paths}")
    input_path = data_paths[0]

    print(f"scale: reading input from {input_path}")
    with open(input_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    print(f"scale: loaded {len(rows)} rows")
    print(f"scale: applying factor={factor} precision={precision}")

    scaled = 0
    skipped = 0
    for row in rows:
        raw = row.get("value", "")
        try:
            v = float(raw)
        except (TypeError, ValueError):
            skipped += 1
            continue
        row["value"] = f"{round(v * factor, precision):.{precision}f}"
        scaled += 1

    output_path = run_dir / "output.csv"
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"scale: scaled {scaled} rows ({skipped} skipped as non-numeric)")


if __name__ == "__main__":
    main()
