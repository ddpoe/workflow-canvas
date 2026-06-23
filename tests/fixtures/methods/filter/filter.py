"""Fixture method: filter -- keeps rows where value >= threshold.

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
    threshold = float(params["threshold"])
    drop_na = bool(params.get("drop_na", True))

    data_paths = slot_paths.get("data", [])
    if not data_paths or not Path(data_paths[0]).exists():
        raise FileNotFoundError(f"Input file not found: {data_paths}")
    input_path = data_paths[0]

    print(f"filter: reading input from {input_path}")
    with open(input_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    print(f"filter: loaded {len(rows)} rows")
    print(f"filter: evaluating threshold={threshold} drop_na={drop_na}")

    kept = []
    dropped_na = 0
    dropped_below = 0
    for row in rows:
        raw = row.get("value", "")
        try:
            v = float(raw)
        except (TypeError, ValueError):
            if drop_na:
                dropped_na += 1
                continue
            kept.append(row)
            continue
        if v >= threshold:
            kept.append(row)
        else:
            dropped_below += 1

    output_path = run_dir / "output.csv"
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)

    print(
        f"filter: kept {len(kept)}/{len(rows)} rows "
        f"(dropped {dropped_below} below threshold, {dropped_na} NA)"
    )


if __name__ == "__main__":
    main()
