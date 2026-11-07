"""Export: a run's archived outputs out of managed storage, for a user.

The consumption half of Client & export, outside the browser.  One
engine, :func:`export_output`, serves the ``wfc export`` verb in both of
its modes: a mutable copy the user owns, or the read-only cache path for
a script to read in place.

The package reads through Storage's published surface only — the output
resolver and its typed errors, the strict enumeration of a run's named
outputs, the export naming rule and the egress copy.  It opens no
session and names no table of its own, so nothing about the database's
shape reaches the export.

:func:`export_draft_output` serves ``wfc export --run draft:…``: a draft
step's outputs, read from its draft's latest draft run through Execution's
resolver, while that draft run exists.

:func:`export_paths` gives the read-only local path of each requested
output, of a run or a draft step, as values (``wfc export --path --json``);
the ``--path`` lines read the same resolution.
"""

from .output import (
    ExportPath,
    ExportPathError,
    export_draft_output,
    export_output,
    export_paths,
)

__all__ = ["ExportPath", "ExportPathError", "export_draft_output", "export_output",
           "export_paths"]
