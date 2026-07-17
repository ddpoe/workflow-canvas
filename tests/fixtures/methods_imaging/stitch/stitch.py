"""Fixture: stitch — 2-slot fan-in (config skip-level + corrected immediate).

Reads EVERY declared input slot (config: 3-hop skip from build_config;
corrected: immediate from illum_correct), tags each row with the slot it
arrived through (``source_slot``) and the upstream lineage chain it carried,
keeps the producing STAGE, and concatenates all rows. Tagging by source_slot
is what makes skip-link lineage assertable: a row's ``source_slot`` plus its
``lineage`` value uniquely identify which wired input it flowed through, so a
mis-wired or dropped skip-link is detectable in the output content itself (not
just in print() noise).
"""

import csv
import json
import os
from pathlib import Path

STAGE = "stitch"

# Declared input slots (must match method.yaml); order is the output row order.
SLOTS = ["config", "corrected"]


def write_results(run_dir, outputs, metrics):
    """Hand-written Tier-2 results manifest — same shape wfc-client finalizes."""
    payload = {"outputs": outputs, "metrics": metrics}
    (run_dir / "_wfc_results.json").write_text(json.dumps(payload, indent=2))


def read_slot_rows(slot_paths, slot_name):
    """Yield (row_id, lineage, source_slot) for every row of every path in a slot.

    Schema-robust: the first column is the row identity (``id`` flowing from
    the seed CSV); the trailing ``stage`` columns accumulated by upstream
    stages form the lineage chain. We join all non-id cells into ``lineage``
    so the full upstream provenance survives and re-tag the row with the slot
    it arrived through.
    """
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

    out_path = run_dir / "stitched.csv"
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "lineage", "source_slot", "stage"])
        for row_id, lineage, source_slot in rows:
            w.writerow([row_id, lineage, source_slot, STAGE])

    write_results(
        run_dir,
        {"stitched": "stitched.csv"},
        {"stitched_rows": len(rows),
         "slots_seen": len({slot for _, _, slot in rows})},
    )
    print(f"{STAGE}: fan-in slots={list(slot_paths)}; wrote {len(rows)} rows")


if __name__ == "__main__":
    main()
