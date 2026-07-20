"""Demo method: label — split cells at an intensity threshold.

Contract mapping (see method.yaml):
- ctx.input("data")                 <-> the `data` input slot
- ctx.params["threshold"] (required), ctx.params.get("label_column")
                                    <-> the `params:` block
- ctx.save_artifact("labeled", ...) <-> the `labeled` output slot

The threshold lives HERE and only here: the downstream `plot` method colours
by the label column this method writes, so retuning `threshold` moves the
colour split in the figure without a second copy of the value anywhere.
"""
import csv

import wfc_client as wfc


@wfc.method
def label(ctx):
    with open(ctx.input("data")[0], newline="") as f:
        rows = list(csv.DictReader(f))

    threshold = float(ctx.params["threshold"])
    label_column = ctx.params.get("label_column", "label")

    n_above = 0
    for row in rows:
        above = float(row["intensity"]) >= threshold
        n_above += int(above)
        # Adds one new column holding the side of the threshold.
        row[label_column] = "above" if above else "below"

    fieldnames = (list(rows[0].keys()) if rows
                  else ["id", "intensity", "area", "quality", label_column])
    out_path = ctx.workdir / "labeled.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    ctx.save_artifact("labeled", out_path)

    ctx.log_metric("rows", len(rows))
    ctx.log_metric("rows_above", n_above)


if __name__ == "__main__":
    wfc.run()
