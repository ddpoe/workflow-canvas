"""Fixture method: qc -- multi-i/o stress test (2 inputs, 3 outputs).

Drops rows whose 'value' column is missing or non-numeric. Optionally
joins per-row metadata by 'id'. Emits report.json, clean.csv, dropped.csv.

Reads WFC_RUN_DIR, WFC_INPUT_PATHS, and WFC_PARAMS from environment.
"""

import csv
import json
import os
from pathlib import Path


def _read_csv(path: str) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main():
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    slot_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{}"))
    params = json.loads(os.environ.get("WFC_PARAMS", "{}"))
    min_rows = int(params.get("min_rows", 1))

    data_paths = slot_paths.get("data", [])
    if not data_paths or not Path(data_paths[0]).exists():
        raise FileNotFoundError(f"Input file not found: {data_paths}")
    rows = _read_csv(data_paths[0])
    fieldnames = list(rows[0].keys()) if rows else ["id", "value"]

    meta_paths = slot_paths.get("metadata", [])
    meta_by_id: dict[str, dict] = {}
    if meta_paths and Path(meta_paths[0]).exists():
        for m in _read_csv(meta_paths[0]):
            if "id" in m:
                meta_by_id[m["id"]] = m

    clean: list[dict] = []
    dropped: list[dict] = []
    for row in rows:
        raw = row.get("value", "")
        try:
            float(raw)
            clean.append(row)
        except (TypeError, ValueError):
            row["__drop_reason"] = "non_numeric_value"
            dropped.append(row)

    clean_path = run_dir / "clean.csv"
    with open(clean_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(clean)

    dropped_path = run_dir / "dropped.csv"
    drop_fields = fieldnames + ["__drop_reason"]
    with open(dropped_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=drop_fields)
        w.writeheader()
        w.writerows(dropped)

    report = {
        "kept": len(clean),
        "dropped": len(dropped),
        "metadata_rows_seen": len(meta_by_id),
        "passes_min_rows": len(clean) >= min_rows,
    }
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2))

    print(f"qc: kept={len(clean)} dropped={len(dropped)}")


if __name__ == "__main__":
    main()
