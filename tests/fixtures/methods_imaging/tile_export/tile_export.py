"""Fixture: tile_export — reads config, tags rows, writes tiles.csv.

Params (``WFC_PARAMS``) feed the demo metrics only — output rows are always
the full pass-through, so param values never change downstream content.
"""

import csv
import json
import math
import os
from pathlib import Path

STAGE = "tile_export"


def write_results(run_dir, outputs, metrics):
    """Hand-written Tier-2 results manifest — same shape wfc-client finalizes."""
    payload = {"outputs": outputs, "metrics": metrics}
    (run_dir / "_wfc_results.json").write_text(json.dumps(payload, indent=2))


def main():
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    slot_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{}"))
    params = json.loads(os.environ.get("WFC_PARAMS", "{}"))
    tile_size = int(params.get("tile_size", 256))
    overlap = float(params.get("overlap", 0.1))

    in_path = Path(slot_paths["config"][0])
    with in_path.open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [row + [STAGE] for row in reader]
    out_path = run_dir / "tiles.csv"
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header + ["stage"])
        w.writerows(rows)

    # Tiles needed to cover a synthetic 2048x2048 image at this tile geometry.
    stride = max(1.0, tile_size * (1.0 - overlap))
    per_axis = math.ceil(2048 / stride)
    write_results(
        run_dir,
        {"tiles": "tiles.csv"},
        {"n_tiles": per_axis * per_axis, "tile_rows": len(rows)},
    )
    print(f"{STAGE}: wrote {len(rows)} rows to {out_path.name}")


if __name__ == "__main__":
    main()
