"""Bar chart of mean fold change per condition with its bootstrap interval.

From 01_process_plates.ipynb, cell 10.
"""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import wfc_client as wfc


@wfc.method
def plot_fold_change(ctx):
    boot = pd.read_csv(ctx.input("bootstrap"))
    with open(Path(ctx.input("plate")) / "plate.json") as f:
        layout = json.load(f)

    fig, ax = plt.subplots(figsize=(5, 3.5))
    err = [boot["mean_fc"] - boot["ci_low"], boot["ci_high"] - boot["mean_fc"]]
    ax.bar(boot["condition"], boot["mean_fc"], yerr=err, capsize=4, color="#4c72b0")
    ax.axhline(1, color="grey", lw=0.8, ls="--")
    ax.set_ylabel("fold change vs DMSO")
    ax.set_title(layout["plate"])
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()
    out_path = ctx.workdir / "fold_change.png"
    plt.savefig(out_path, dpi=120)
    ctx.save_artifact("figure", out_path)


if __name__ == "__main__":
    wfc.run()
