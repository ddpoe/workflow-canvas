"""qc (decorated) — summary PNG + directory output (per-sample subdirs).

Demonstrates two output types a typical pipeline produces:
  - summary_plot: a matplotlib Figure (framework saves as PNG)
  - per_sample:   a directory containing per-sample stats.csv + hist.png pairs
                  (framework sees a Path pointing at a directory, handles it
                   via shutil.copytree when source != dest — or skips the
                   copy when the method wrote directly under WFC_RUN_DIR).

The directory output also exercises method.yaml's `contents:` column spec
(soft-checks that required globs matched at least min_count files).
"""
import os
from pathlib import Path

from wfc.method import wfc_method, wfc_method_main
import matplotlib
matplotlib.use("Agg")  # headless; must precede pyplot import
import matplotlib.pyplot as plt
import pandas as pd


@wfc_method
def qc(inputs, params):
    df = pd.read_csv(inputs["data"][0])
    min_rows = int(params.get("min_rows", 1))

    values = pd.to_numeric(df.get("value"), errors="coerce").dropna()

    # Summary histogram — returned as a matplotlib Figure; framework writes .png.
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hist(values, bins=20, color="#4A90D9", edgecolor="#2e5d8f")
    ax.set_title(f"value distribution (n={len(values)})")
    ax.set_xlabel("value")
    ax.set_ylabel("count")

    # Per-group directory: write directly under WFC_RUN_DIR/per_sample so the
    # framework's _write dispatch short-circuits the copy (source==dest).
    run_dir = Path(os.environ["WFC_RUN_DIR"])
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
        {"summary_plot": fig, "per_sample": per_sample_dir},
        {
            "rows": int(len(df)),
            "plot_path": "summary_plot.png",
            "pass_count": int(len(values) >= min_rows),
        },
    )


if __name__ == "__main__":
    wfc_method_main()
