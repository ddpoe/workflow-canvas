"""Fixture: illum_correct — reads tiles, tags rows, writes corrected.csv.

Params (``WFC_PARAMS``) feed the demo metrics only — output rows are always
the full pass-through, so param values never change downstream content.
"""

import csv
import json
import os
from pathlib import Path

STAGE = "illum_correct"


def write_results(run_dir, outputs, metrics):
    """Hand-written Tier-2 results manifest — same shape wfc-client finalizes."""
    payload = {"outputs": outputs, "metrics": metrics}
    (run_dir / "_wfc_results.json").write_text(json.dumps(payload, indent=2))


def main():
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    slot_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{}"))
    params = json.loads(os.environ.get("WFC_PARAMS", "{}"))
    sigma = float(params.get("sigma", 2.0))

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

    mean_correction = round(sigma / (sigma + len(rows)), 4) if rows else 0.0
    write_results(
        run_dir,
        {"corrected": "corrected.csv"},
        {"rows_corrected": len(rows), "mean_correction": mean_correction},
    )
    print(f"{STAGE}: wrote {len(rows)} rows to {out_path.name}")


if __name__ == "__main__":
    main()
