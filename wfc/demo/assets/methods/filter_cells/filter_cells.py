"""Demo method: filter_cells — keep cells that pass quality gates.

Contract mapping (see method.yaml):
- ctx.input("data")                  <-> the `data` input slot
- ctx.params["min_quality"] (required) and ctx.params.get("max_area")
  (optional)                         <-> the `params:` block
- ctx.save_artifact("filtered", ...) <-> the `filtered` output slot
"""
import csv

import wfc_client as wfc


@wfc.method
def filter_cells(ctx):
    with open(ctx.input("data")[0], newline="") as f:
        rows = list(csv.DictReader(f))
    fieldnames = list(rows[0].keys()) if rows else ["id", "intensity", "area", "quality"]

    # A required param has no default in method.yaml — the pipeline must set
    # it (the demo pipeline ships min_quality = 0.5).
    min_quality = float(ctx.params["min_quality"])
    # Optional param: absent unless the user sets it.
    max_area = ctx.params.get("max_area")

    kept = []
    for row in rows:
        if float(row["quality"]) < min_quality:
            continue
        if max_area is not None and float(row["area"]) > float(max_area):
            continue
        kept.append(row)

    out_path = ctx.workdir / "filtered.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(kept)
    ctx.save_artifact("filtered", out_path)

    ctx.log_metric("rows_in", len(rows))
    ctx.log_metric("rows_kept", len(kept))


if __name__ == "__main__":
    wfc.run()
