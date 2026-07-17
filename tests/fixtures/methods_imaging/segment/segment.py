"""Fixture: segment — reads stitched, tags rows, writes masks.csv.

``threshold`` (``WFC_PARAMS``) drops rows whose numeric id is below it. The
default 0.0 keeps every row (seed ids are non-negative), so output is
byte-identical to the pre-params fixture unless a threshold is explicitly set.
"""

import csv
import json
import os
from pathlib import Path

STAGE = "segment"


def write_results(run_dir, outputs, metrics):
    """Hand-written Tier-2 results manifest — same shape wfc-client finalizes."""
    payload = {"outputs": outputs, "metrics": metrics}
    (run_dir / "_wfc_results.json").write_text(json.dumps(payload, indent=2))


def main():
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    slot_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{}"))
    params = json.loads(os.environ.get("WFC_PARAMS", "{}"))
    threshold = float(params.get("threshold", 0.0))

    in_path = Path(slot_paths["stitched"][0])
    kept, dropped = [], 0
    with in_path.open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            try:
                below = float(row[0]) < threshold
            except (ValueError, IndexError):
                below = False  # non-numeric/empty id rows always pass
            if below:
                dropped += 1
            else:
                kept.append(row + [STAGE])

    out_path = run_dir / "masks.csv"
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header + ["stage"])
        w.writerows(kept)

    write_results(
        run_dir,
        {"masks": "masks.csv"},
        {"n_objects": len(kept), "n_dropped": dropped},
    )
    print(f"{STAGE}: wrote {len(kept)} rows to {out_path.name} "
          f"(dropped {dropped} below threshold={threshold})")


if __name__ == "__main__":
    main()
