"""Demo method: plot — per-sample intensity histogram, coloured by label.

Contract mapping (see method.yaml):
- ctx.input("data")                <-> the `data` input slot (label's output)
- ctx.params.get("value_column"), ctx.params.get("bins")
                                   <-> the `params:` block
- ctx.save_artifact("figure", ...) <-> the `figure` output slot (.png)

This method deliberately has NO threshold param: it colours each histogram
bar by the `label` column the upstream `label` method wrote, so the
threshold split is visible in the figure and can never drift out of sync
with label's setting.
"""
import csv
import os

import wfc_client as wfc

# Fixed two-colour palette + muted chrome (do not substitute matplotlib
# defaults — the legend is mandatory because the colours alone are not
# distinguishable for every kind of colour vision).
COLOR_BELOW = "#2a78d6"   # blue
COLOR_ABOVE = "#008300"   # green
SURFACE = "#fcfcfb"       # light chart surface
MUTED_INK = "#898781"     # axis + tick labels
GRIDLINE = "#e1e0d9"      # hairline gridlines
INK = "#0b0b0b"           # title


@wfc.method
def plot(ctx):
    import matplotlib
    matplotlib.use("Agg")  # headless rendering inside the container
    import matplotlib.pyplot as plt

    with open(ctx.input("data")[0], newline="") as f:
        rows = list(csv.DictReader(f))

    value_column = ctx.params.get("value_column", "intensity")
    bins = int(ctx.params.get("bins", 20))

    below = [float(r[value_column]) for r in rows if r.get("label") == "below"]
    above = [float(r[value_column]) for r in rows if r.get("label") == "above"]

    sample = os.environ.get("WFC_SAMPLE", "sample")

    fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.hist(
        [below, above],
        bins=bins,
        stacked=True,
        color=[COLOR_BELOW, COLOR_ABOVE],
        label=["below threshold", "above threshold"],
    )
    ax.legend(frameon=False, labelcolor=MUTED_INK)
    ax.set_title(f"{sample}\n{len(rows)} cells after filtering",
                 color=INK, fontsize=11)
    ax.set_xlabel(value_column, color=MUTED_INK)
    ax.set_ylabel("cells", color=MUTED_INK)
    ax.tick_params(colors=MUTED_INK)
    ax.grid(axis="y", color=GRIDLINE, linewidth=0.6)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color(GRIDLINE)

    out_path = ctx.workdir / "figure.png"
    fig.savefig(out_path, facecolor=SURFACE, bbox_inches="tight")
    ctx.save_artifact("figure", out_path)

    n_above = len(above)
    n_total = len(rows)
    ctx.log_metric("rows", n_total)
    ctx.log_metric("rows_above", n_above)
    ctx.log_metric("fraction_above", round(n_above / n_total, 3) if n_total else 0.0)


if __name__ == "__main__":
    wfc.run()
