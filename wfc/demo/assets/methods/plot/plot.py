"""Demo method: plot — per-sample density of a measurement, shaded by label.

Contract mapping (see method.yaml):
- ctx.input("data")                <-> the `data` input slot (label's output)
- ctx.params.get("value_column"), ctx.params.get("bins"),
  ctx.params.get("smoothing")      <-> the `params:` block
- ctx.save_artifact("figure", ...) <-> the `figure` output slot (.png)

The figure is a Gaussian kernel density curve over a faint histogram, with
one tick per cell under it. This method deliberately has NO threshold
param: it shades each side by the `label` column the upstream `label`
method wrote, so the threshold split is visible in the figure and can never
drift out of sync with label's setting.
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
MUTED_INK = "#898781"     # axis, tick labels, subtitle and legend
GRIDLINE = "#e1e0d9"      # baseline
INK = "#0b0b0b"           # heading and curve
# The demo's names carry its reserved prefix; the heading shows the sample
# as the user thinks of it.
DEMO_PREFIX = "__demo__"


def density(values, grid, smoothing):
    """Gaussian kernel density of *values* at each point of *grid*.

    The bandwidth is Silverman's rule of thumb times *smoothing*.
    """
    import numpy as np

    spread = values.std() or 1.0
    bandwidth = 1.06 * spread * len(values) ** -0.2 * smoothing
    z = (grid[:, None] - values[None, :]) / bandwidth
    return np.exp(-0.5 * z ** 2).sum(axis=1) / (
        len(values) * bandwidth * np.sqrt(2 * np.pi))


@wfc.method
def plot(ctx):
    import matplotlib
    matplotlib.use("Agg")  # headless rendering inside the container
    import matplotlib.pyplot as plt
    import numpy as np

    with open(ctx.input("data"), newline="") as f:
        rows = list(csv.DictReader(f))

    value_column = ctx.params.get("value_column", "intensity")
    bins = int(ctx.params.get("bins", 20))
    smoothing = float(ctx.params.get("smoothing", 1.0))

    below = np.array([float(r[value_column]) for r in rows if r.get("label") == "below"])
    above = np.array([float(r[value_column]) for r in rows if r.get("label") == "above"])
    values = np.concatenate([below, above])

    sample = os.environ.get("WFC_SAMPLE", "sample")
    if sample.startswith(DEMO_PREFIX):
        sample = sample[len(DEMO_PREFIX):]

    fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    if len(values):
        pad = (values.max() - values.min()) * 0.15 or 1.0
        lo, hi = values.min() - pad, values.max() + pad
        if values.min() >= 0:  # a non-negative measurement's axis starts at 0 or above
            lo = max(0.0, lo)
        grid = np.linspace(lo, hi, 512)
        curve = density(values, grid, smoothing)
        # Where the labels change sides: between the highest "below" and
        # the lowest "above" value.
        split = ((below.max() + above.min()) / 2 if len(below) and len(above)
                 else (hi if len(below) else lo))

        # Faint bars, scaled so their total area is 1, like the curve's.
        edges = np.linspace(lo, hi, bins + 1)
        per_cell = 1 / (len(values) * (edges[1] - edges[0]))
        heights = [0.0]
        for part, color in ((below, COLOR_BELOW), (above, COLOR_ABOVE)):
            if len(part):
                counts, _, _ = ax.hist(
                    part, bins=edges, weights=np.full(len(part), per_cell),
                    histtype="stepfilled", color=color, alpha=0.18, linewidth=0)
                heights.append(counts.max())
        ax.fill_between(grid, curve, where=grid < split, color=COLOR_BELOW,
                        alpha=0.35, linewidth=0,
                        label=f"below threshold ({len(below)})")
        ax.fill_between(grid, curve, where=grid >= split, color=COLOR_ABOVE,
                        alpha=0.35, linewidth=0,
                        label=f"above threshold ({len(above)})")
        ax.plot(grid, curve, color=INK, linewidth=1.2)
        top = max(curve.max(), *heights)
        for part, color in ((below, COLOR_BELOW), (above, COLOR_ABOVE)):
            ax.plot(part, np.full(len(part), -top * 0.04), "|", color=color,
                    alpha=0.5, markersize=6)
        if len(below) and len(above):
            ax.axvline(split, color=MUTED_INK, linestyle="--", linewidth=0.8)
        ax.set_xlim(lo, hi)
        ax.set_ylim(-top * 0.08, top * 1.05)
        ax.legend(frameon=False, labelcolor=MUTED_INK, fontsize=9,
                  loc="lower right", bbox_to_anchor=(1.0, 1.0),
                  handlelength=1.2, borderaxespad=0.2)

    ax.text(0, 1.10, sample, transform=ax.transAxes, ha="left", va="bottom",
            fontsize=13, fontweight="bold", color=INK)
    # The input is label's output, already quality-filtered upstream.
    ax.text(0, 1.03, f"{len(values)} cells after the quality filter",
            transform=ax.transAxes, ha="left", va="bottom", fontsize=9.5,
            color=MUTED_INK)
    ax.set_xlabel(value_column, color=MUTED_INK)
    ax.set_yticks([])
    ax.tick_params(colors=MUTED_INK)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRIDLINE)

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
