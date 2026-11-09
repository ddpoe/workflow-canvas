"""wfc-client — Tier-1 sugar for writing wfc methods.

Usage::

    import wfc_client as wfc

    @wfc.method
    def qc(ctx):
        clean_path = ctx.workdir / "clean.csv"
        ...  # write the file
        ctx.save_artifact("clean", clean_path)
        ctx.log_metric("kept_rows", 100)

    if __name__ == "__main__":
        wfc.run()

This package is a strict subset focused on the canonical Tier-2 env-var +
file contract. It carries one small dependency,
``axiom-annotations`` (the workflow markers), and never imports the full
``wfc`` package, pandas, or sqlmodel. It is a metadata
recorder: it never copies, reads, or serializes your data bytes.
"""

from __future__ import annotations

from .context import InputPath, RunContext
from .decorator import method
from .main import run

# The package version; kept equal to ``version`` in wfc-client/pyproject.toml.
__version__ = "0.3.1"

__all__ = ["method", "run", "RunContext", "InputPath"]
