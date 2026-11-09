"""Bootstrap a 95% interval of the mean fold change per condition (the slow step).

From 01_process_plates.ipynb, cell 9.
"""

import numpy as np
import pandas as pd
import wfc_client as wfc

from plate_utils import CONDITION_ORDER


@wfc.method
def bootstrap_ci(ctx):
    norm = pd.read_csv(ctx.input("normalized"))
    n_boot = int(ctx.params["n_boot"])
    np.random.seed(int(ctx.params["seed"]))

    rows = []
    for cond in CONDITION_ORDER:
        fc = norm.loc[norm["condition"] == cond, "fold_change"].to_numpy()
        means = []
        for i in range(n_boot):
            means.append(np.random.choice(fc, size=len(fc), replace=True).mean())
        lo, hi = np.percentile(means, [2.5, 97.5])
        rows.append({"condition": cond, "mean_fc": fc.mean(), "ci_low": lo, "ci_high": hi,
                     "n_cells": len(fc)})
    boot = pd.DataFrame(rows)
    out_path = ctx.workdir / "bootstrap.csv"
    boot.to_csv(out_path, index=False)
    ctx.save_artifact("bootstrap", out_path)


if __name__ == "__main__":
    wfc.run()
