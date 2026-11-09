"""Compare mean fold change per condition across plates.

From 02_compare_plates.ipynb, cells 3-5.
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import wfc_client as wfc

from plate_utils import CONDITION_ORDER


@wfc.method
def compare_plates(ctx):
    paths = list(ctx.input("bootstraps"))
    plates = [p.strip() for p in str(ctx.params["plate_names"]).split(",")]
    min_cells = int(ctx.params["min_cells"])
    if len(plates) != len(paths):
        raise SystemExit(f"plate_names names {len(plates)} plates but {len(paths)} tables arrived")

    boots = []
    for p, path in zip(plates, paths):
        b = pd.read_csv(path)
        b["plate"] = p
        boots.append(b)
    combined = pd.concat(boots)
    combined = combined[combined["n_cells"] >= min_cells]

    summary = combined.pivot(index="condition", columns="plate", values="mean_fc").reindex(CONDITION_ORDER)
    summary["mean_across_plates"] = summary[plates].mean(axis=1)
    summary["diff"] = summary["plate2"] - summary["plate1"]
    out_csv = ctx.workdir / "plate_comparison.csv"
    summary.to_csv(out_csv)

    fig, ax = plt.subplots(figsize=(4, 4))
    ax.scatter(summary["plate1"], summary["plate2"])
    for cond, row in summary.iterrows():
        ax.annotate(cond, (row["plate1"], row["plate2"]), fontsize=8)
    lim = [0, max(summary["plate1"].max(), summary["plate2"].max()) * 1.1]
    ax.plot(lim, lim, color="grey", lw=0.8, ls="--")
    ax.set_xlabel("plate1 fold change")
    ax.set_ylabel("plate2 fold change")
    plt.tight_layout()
    out_png = ctx.workdir / "comparison.png"
    plt.savefig(out_png, dpi=120)

    ctx.save_artifact("comparison", out_csv)
    ctx.save_artifact("figure", out_png)


if __name__ == "__main__":
    wfc.run()
