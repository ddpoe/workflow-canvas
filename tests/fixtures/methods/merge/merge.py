"""Fixture method: merge -- fan-in concatenation of multiple CSVs.

Reads WFC_RUN_DIR and WFC_INPUT_PATHS from environment.
WFC_INPUT_PATHS is a JSON dict: {"sources": ["path1.csv", "path2.csv", ...]}.
Writes merged CSV to <run_dir>/merged.csv.
"""

import csv
import json
import os
from pathlib import Path


def main():
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    input_paths_raw = os.environ.get("WFC_INPUT_PATHS", "{}")
    slot_paths = json.loads(input_paths_raw)

    # Collect all source paths from the "sources" slot
    source_paths = slot_paths.get("sources", [])

    if not source_paths:
        raise ValueError("No input paths provided for merge")

    # Read and concatenate all CSVs, unioning fieldnames across sources
    all_rows = []
    fieldnames = []
    seen = set()
    for sp in source_paths:
        with open(sp, newline="") as f:
            reader = csv.DictReader(f)
            for name in (reader.fieldnames or []):
                if name not in seen:
                    fieldnames.append(name)
                    seen.add(name)
            for row in reader:
                all_rows.append(row)

    # Write merged output
    output_path = run_dir / "merged.csv"
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames or [])
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"merge: wrote {len(all_rows)} rows from {len(source_paths)} sources to {output_path}")


if __name__ == "__main__":
    main()
