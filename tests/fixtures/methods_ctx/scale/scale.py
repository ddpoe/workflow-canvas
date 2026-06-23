"""scale (RunContext) — multiply 'value' column by factor, round to precision."""
import os
from pathlib import Path

import pandas as pd

from wfc.wfc_context import RunContext


def scale(inputs, params):
    print(f"scale: reading {inputs['data'][0]}")
    df = pd.read_csv(inputs["data"][0])
    factor = float(params.get("factor", 1.0))
    precision = int(params.get("precision", 2))
    print(f"scale: loaded {len(df)} rows; applying factor={factor} precision={precision}")

    values = pd.to_numeric(df.get("value"), errors="coerce")
    scaled = (values * factor).round(precision)
    df["value"] = scaled
    print(f"scale: scaled {len(scaled.dropna())} values")

    run_dir = Path(os.environ["WFC_RUN_DIR"])
    out_path = run_dir / "output.csv"
    df.to_csv(out_path, index=False)

    non_na = scaled.dropna()
    return (
        {"output": str(out_path)},
        {
            "factor": float(factor),
            "min_scaled": float(non_na.min()) if len(non_na) else 0.0,
            "max_scaled": float(non_na.max()) if len(non_na) else 0.0,
        },
    )


if __name__ == "__main__":
    ctx = RunContext()
    inputs = ctx.load_input()
    _outputs, metrics = scale(inputs, ctx.params)
    ctx.log_metrics(metrics)
    ctx.finalize()
