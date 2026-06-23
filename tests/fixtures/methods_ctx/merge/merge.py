"""merge (RunContext) — fan-in concat of multiple CSVs.

Lower-level form: the method owns file I/O and returns paths; the __main__
block drives RunContext.load_input → call → log_metrics → finalize.
"""
from pathlib import Path
import os

import pandas as pd

from wfc.wfc_context import RunContext


def merge(inputs, params):
    paths = inputs["sources"]
    df = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)

    run_dir = Path(os.environ["WFC_RUN_DIR"])
    out_path = run_dir / "merged.csv"
    df.to_csv(out_path, index=False)

    return (
        {"merged": str(out_path)},
        {
            "rows_in": int(len(df)),
            "sources_count": int(len(paths)),
        },
    )


if __name__ == "__main__":
    ctx = RunContext()
    inputs = ctx.load_input()
    _outputs, metrics = merge(inputs, ctx.params)
    ctx.log_metrics(metrics)
    ctx.finalize()
