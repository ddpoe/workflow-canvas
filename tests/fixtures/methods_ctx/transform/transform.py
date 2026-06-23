"""transform (RunContext) — add a computed column; also emit per-row JSON log."""
import json
import os
from pathlib import Path

import pandas as pd

from wfc.wfc_context import RunContext


def transform(inputs, params):
    print(f"transform: reading {inputs['data'][0]}")
    df = pd.read_csv(inputs["data"][0])
    suffix = str(params.get("suffix", "_transformed"))
    new_col = f"computed{suffix}"
    print(f"transform: loaded {len(df)} rows; adding column '{new_col}'")
    df[new_col] = df["id"].astype(str).map(lambda x: f"v_{x}")
    print(f"transform: done ({len(df)} rows)")

    run_dir = Path(os.environ["WFC_RUN_DIR"])
    out_path = run_dir / "output.csv"
    df.to_csv(out_path, index=False)

    log = [{"id": row["id"], new_col: row[new_col]} for _, row in df.iterrows()]
    log_path = run_dir / "transform_log.json"
    log_path.write_text(json.dumps(log, indent=2))

    return (
        {"output": str(out_path), "transform_log": str(log_path)},
        {"rows": int(len(df)), "suffix_used": suffix},
    )


if __name__ == "__main__":
    ctx = RunContext()
    inputs = ctx.load_input()
    _outputs, metrics = transform(inputs, ctx.params)
    ctx.log_metrics(metrics)
    ctx.finalize()
