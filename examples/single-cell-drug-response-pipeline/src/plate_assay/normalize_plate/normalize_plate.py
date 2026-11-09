"""Load one plate, drop debris below a minimum area, normalize to fold change over DMSO.

From 01_process_plates.ipynb, cells 3-7 (load, QC histogram, area filter, normalize).
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import wfc_client as wfc

from plate_utils import load_plate, normalize


@wfc.method
def normalize_plate(ctx):
    plate_dir = Path(ctx.input("plate"))
    min_area = float(ctx.params["min_area"])

    cells, layout = load_plate(str(plate_dir.parent), plate_dir.name)
    print(layout["plate"], len(cells), "cells")

    # QC: the area histogram, to look at for each new plate (the notebook showed it inline)
    plt.hist(cells["area"], bins=80)
    plt.axvline(min_area, color="r")
    plt.xlabel("area (px)")
    qc_path = ctx.workdir / "qc_area_histogram.png"
    plt.savefig(qc_path, dpi=100)
    plt.close("all")

    cells = cells[cells["area"] >= min_area]
    print(len(cells), "cells after area filter")

    norm = normalize(cells, layout["background"])
    out_path = ctx.workdir / "normalized.csv"
    norm.to_csv(out_path, index=False)

    ctx.save_artifact("normalized", out_path)
    ctx.save_artifact("qc_histogram", qc_path)


if __name__ == "__main__":
    wfc.run()
