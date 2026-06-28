"""Fixture: quantify — 2-slot fan-in (stitched skip-level + masks immediate).

Reads EVERY declared input slot (stitched: 2-hop skip from stitch; masks:
immediate from segment), tags each row by the slot it arrived through
(``source_slot``) plus its upstream lineage chain, keeps the producing STAGE,
and concatenates. The source_slot tag makes skip-link lineage assertable.
"""

import csv
import json
import os
from pathlib import Path

STAGE = "quantify"

# Declared input slots (must match method.yaml); order is the output row order.
SLOTS = ["stitched", "masks"]


def read_slot_rows(slot_paths, slot_name):
    """Yield (row_id, lineage, source_slot) for every row of every path in a slot."""
    out = []
    for path in slot_paths.get(slot_name, []):
        with Path(path).open(newline="") as f:
            reader = csv.reader(f)
            next(reader, None)  # drop header
            for row in reader:
                if not row:
                    continue
                row_id = row[0]
                lineage = ">".join(c for c in row[1:] if c)
                out.append((row_id, lineage, slot_name))
    return out


def main():
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    slot_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{}"))

    rows = []
    for slot in SLOTS:
        rows.extend(read_slot_rows(slot_paths, slot))

    out_path = run_dir / "measurements.csv"
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "lineage", "source_slot", "stage"])
        for row_id, lineage, source_slot in rows:
            w.writerow([row_id, lineage, source_slot, STAGE])

    print(f"{STAGE}: fan-in slots={list(slot_paths)}; wrote {len(rows)} rows")


if __name__ == "__main__":
    main()
