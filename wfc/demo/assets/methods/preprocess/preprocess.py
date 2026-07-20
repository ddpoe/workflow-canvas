"""Demo method: preprocess — drop rows without a usable measurement.

This script is a worked example of the wfc-client authoring contract:

- ``@wfc.method`` marks the entry point; wfc calls it with ``ctx``.
- Input files arrive via ``ctx.input("<slot>")`` — the slot name must match
  an input declared in this method's method.yaml (here: ``data``).
- Every ``ctx.params`` read maps to a param declared in method.yaml
  (here: ``drop_na``, ``value_column``).
- Every ``ctx.save_artifact("<name>", path)`` maps to an output slot
  declared in method.yaml (here: ``clean``).
"""
import csv

import wfc_client as wfc


@wfc.method
def preprocess(ctx):
    # ctx.input("data") -> list of files wired into the "data" input slot.
    with open(ctx.input("data")[0], newline="") as f:
        rows = list(csv.DictReader(f))
    fieldnames = list(rows[0].keys()) if rows else ["id", "intensity", "area", "quality"]

    # Params: each key mirrors the `params:` block in method.yaml.
    drop_na = ctx.params.get("drop_na", True)
    value_column = ctx.params.get("value_column", "intensity")

    clean = []
    for row in rows:
        if drop_na:
            try:
                float(row.get(value_column, ""))
            except (TypeError, ValueError):
                continue  # missing / non-numeric measurement -> drop the row
        clean.append(row)

    # Outputs: write into ctx.workdir, then declare the file under the
    # output-slot name from method.yaml. Downstream nodes connect to "clean".
    out_path = ctx.workdir / "clean.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(clean)
    ctx.save_artifact("clean", out_path)

    # Metrics appear in the run's Metrics tab.
    ctx.log_metric("rows_in", len(rows))
    ctx.log_metric("rows_out", len(clean))


if __name__ == "__main__":
    wfc.run()
