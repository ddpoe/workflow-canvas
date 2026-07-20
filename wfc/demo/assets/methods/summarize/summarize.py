"""Demo method: summarize — per-group counts and mean intensity.

Contract mapping (see method.yaml):
- ctx.input("data")                 <-> the `data` input slot
- ctx.params.get("group_by")        <-> the `group_by` param
- ctx.save_artifact("summary", ...) <-> the `summary` output slot
"""
import csv
from collections import defaultdict

import wfc_client as wfc


@wfc.method
def summarize(ctx):
    with open(ctx.input("data")[0], newline="") as f:
        rows = list(csv.DictReader(f))

    group_by = ctx.params.get("group_by", "label")

    groups: dict = defaultdict(list)
    for row in rows:
        groups[row.get(group_by, "")].append(float(row["intensity"]))

    out_path = ctx.workdir / "summary.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([group_by, "n_cells", "mean_intensity"])
        for key in sorted(groups):
            vals = groups[key]
            w.writerow([key, len(vals), round(sum(vals) / len(vals), 2)])
    ctx.save_artifact("summary", out_path)

    ctx.log_metric("n_groups", len(groups))


if __name__ == "__main__":
    wfc.run()
