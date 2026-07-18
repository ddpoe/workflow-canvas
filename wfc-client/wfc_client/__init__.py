"""wfc-client — pure-stdlib Tier-1 sugar for writing wfc methods.

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
file contract (ADR-020). It has zero third-party dependencies and never
imports the full ``wfc`` package, pandas, or sqlmodel. It is a metadata
recorder: it never copies, reads, or serializes your data bytes.
"""

from __future__ import annotations

from .context import RunContext
from .decorator import method
from .errors import ContractViolation
from .main import run

__all__ = ["method", "run", "RunContext", "ContractViolation"]
