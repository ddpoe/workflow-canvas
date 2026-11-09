"""Helpers for the plate assay notebooks."""
import json
import os

import pandas as pd

CONDITION_ORDER = ["DMSO", "drugA_1uM", "drugA_10uM", "drugB_1uM"]


def load_plate(data_dir, plate):
    """Read one plate's cells and its layout, and label each cell with its condition."""
    cells = pd.read_csv(os.path.join(data_dir, plate, "cells.csv"))
    with open(os.path.join(data_dir, plate, "plate.json")) as f:
        layout = json.load(f)
    cells["condition"] = cells["well"].map(layout["wells"])
    return cells, layout


def normalize(cells, background):
    """Background-subtract and express each cell as fold change over the DMSO median."""
    out = cells.copy()
    out["signal"] = (out["intensity"] - background).clip(lower=0)
    dmso = out.loc[out["condition"] == "DMSO", "signal"].median()
    out["fold_change"] = out["signal"] / dmso
    return out
