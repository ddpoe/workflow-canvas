"""qc (RunContext) — summary PNG + per-sample directory (PNG + stats CSV)."""
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless; must precede pyplot import
import matplotlib.pyplot as plt
import pandas as pd

from wfc.wfc_context import RunContext


def qc(inputs, params):
    df = pd.read_csv(inputs["data"][0])
    min_rows = int(params.get("min_rows", 1))

    values = pd.to_numeric(df.get("value"), errors="coerce").dropna()

    run_dir = Path(os.environ["WFC_RUN_DIR"])

    # Summary histogram — write directly as summary_plot.png under run_dir.
    summary_path = run_dir / "summary_plot.png"
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hist(values, bins=20, color="#4A90D9", edgecolor="#2e5d8f")
    ax.set_title(f"value distribution (n={len(values)})")
    ax.set_xlabel("value")
    ax.set_ylabel("count")
    fig.savefig(summary_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # Per-group directory: write stats.csv + hist.png per label subgroup.
    per_sample_dir = run_dir / "per_sample"
    per_sample_dir.mkdir(parents=True, exist_ok=True)

    if "label" in df.columns:
        groups = list(df.groupby("label", dropna=False))
    else:
        groups = [("all", df)]

    for label, sub in groups:
        label_str = str(label)
        sub_dir = per_sample_dir / label_str
        sub_dir.mkdir(parents=True, exist_ok=True)

        sub_vals = pd.to_numeric(sub.get("value"), errors="coerce").dropna()
        stats = pd.DataFrame({
            "stat": ["n", "min", "max", "mean"],
            "value": [
                int(len(sub_vals)),
                float(sub_vals.min()) if len(sub_vals) else 0.0,
                float(sub_vals.max()) if len(sub_vals) else 0.0,
                float(sub_vals.mean()) if len(sub_vals) else 0.0,
            ],
        })
        stats.to_csv(sub_dir / "stats.csv", index=False)

        sub_fig, sub_ax = plt.subplots(figsize=(4, 3))
        sub_ax.hist(sub_vals, bins=10, color="#50C878", edgecolor="#2b8b4f")
        sub_ax.set_title(f"{label_str} (n={len(sub_vals)})")
        sub_fig.savefig(sub_dir / "hist.png", dpi=100, bbox_inches="tight")
        plt.close(sub_fig)

    return (
        {"summary_plot": str(summary_path), "per_sample": str(per_sample_dir)},
        {
            "rows": int(len(df)),
            "plot_path": "summary_plot.png",
            "pass_count": int(len(values) >= min_rows),
        },
    )


if __name__ == "__main__":
    ctx = RunContext()
    inputs = ctx.load_input()
    _outputs, metrics = qc(inputs, ctx.params)
    ctx.log_metrics(metrics)
    ctx.finalize()
