"""Fixture: export_final — 3-slot skip-level fan-in (measurements + stitched + masks).

Reads EVERY declared input slot (measurements: immediate from quantify;
stitched: 3-hop skip from stitch; masks: 2-hop skip from segment), tags each
row by the slot it arrived through (``source_slot``) plus its upstream lineage
chain, keeps the producing STAGE, and concatenates. This is the marquee
3-input fan-in: all three slots must be present and correctly attributed in the
output, so a mis-wired or dropped skip-link surfaces in the content.
"""

import csv
import json
import os
from pathlib import Path

STAGE = "export_final"

# Declared input slots (must match method.yaml); order is the output row order.
SLOTS = ["measurements", "stitched", "masks"]


def write_results(run_dir, outputs, metrics):
    """Hand-written Tier-2 results manifest — same shape wfc-client finalizes."""
    payload = {"outputs": outputs, "metrics": metrics}
    (run_dir / "_wfc_results.json").write_text(json.dumps(payload, indent=2))


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

    out_path = run_dir / "final.csv"
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "lineage", "source_slot", "stage"])
        for row_id, lineage, source_slot in rows:
            w.writerow([row_id, lineage, source_slot, STAGE])

    write_results(
        run_dir,
        {"final": "final.csv"},
        {"total_rows": len(rows),
         "n_sources": len({slot for _, _, slot in rows})},
    )
    print(f"{STAGE}: fan-in slots={list(slot_paths)}; wrote {len(rows)} rows")


if __name__ == "__main__":
    main()
