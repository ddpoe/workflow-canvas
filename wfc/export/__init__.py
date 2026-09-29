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
"""

from .output import export_output

__all__ = ["export_output"]
