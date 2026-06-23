"""filter (RunContext) — keep rows where value >= threshold."""
import os
from pathlib import Path

import pandas as pd

from wfc.wfc_context import RunContext


def filter(inputs, params):
    print(f"filter: reading {inputs['data'][0]}")
    df = pd.read_csv(inputs["data"][0])
    threshold = float(params.get("threshold", 10.0))
    drop_na = bool(params.get("drop_na", True))
    print(f"filter: loaded {len(df)} rows; threshold={threshold} drop_na={drop_na}")

    values = pd.to_numeric(df.get("value"), errors="coerce")
    if drop_na:
        mask = values.notna() & (values >= threshold)
    else:
        mask = values.isna() | (values >= threshold)
    kept = df[mask].reset_index(drop=True)
    print(f"filter: kept {len(kept)}/{len(df)} rows")

    run_dir = Path(os.environ["WFC_RUN_DIR"])
    out_path = run_dir / "output.csv"
    kept.to_csv(out_path, index=False)

    return (
        {"output": str(out_path)},
        {
            "rows_in": int(len(df)),
            "rows_out": int(len(kept)),
            "threshold": float(threshold),
        },
    )


if __name__ == "__main__":
    ctx = RunContext()
    inputs = ctx.load_input()
    _outputs, metrics = filter(inputs, ctx.params)
    ctx.log_metrics(metrics)
    ctx.finalize()
