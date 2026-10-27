"""Run bundle: export runs and everything that made them as a bundle anyone can rerun.

A Tier 1 unit. It turns a selection of runs into a zip that holds the runs,
every run above them, and what a rerun needs without wfc. The CLI and the
Canvas API import this facade and nothing beneath it.

- :func:`plan_export` is the plan, which writes no bundle: it selects the
  runs and every ancestor, each once, with each cache-hit row replaced by the
  run it reused (:func:`select_runs`); runs Storage's archive pass over their
  unarchived outputs; collects every refusal; and lists each step whose
  image only this machine holds. It returns an :class:`ExportPlan` the
  surfaces print or serialise.
- :func:`write_bundle` is the apply: it writes the zip a plan with no
  refusal describes, all or nothing, reading every byte through Storage,
  git and Environments. :func:`render_argv` renders a step's ``docker run``
  argv from its invocation record and each mount's host side.
- :func:`render_readme` and :func:`render_crate` render the bundle's
  ``README.md`` and its Process Run Crate (``ro-crate-metadata.json``) from
  the ``wfc-run.json`` value.
- ``rerun.py`` beside this file is the bundle's rerun script, copied into
  every bundle byte for byte. It uses the standard library only and is never
  imported by wfc: it replays each step in Docker and compares the outputs.
"""

from .assemble import (
    BundleResult,
    ExportRefusedError,
    out_refusal,
    safe_sample,
    step_folder,
    write_bundle,
)
from .command import command_script, render_argv
from .describe import render_crate, render_readme
from .plan import (
    COMPLETED,
    REFUSED_TEXT,
    EntryRead,
    ExportPlan,
    LocalOnlyImage,
    PlanImage,
    PlanInput,
    PlanOutput,
    PlanStep,
    Refusal,
    plan_export,
)
from .selection import Selection, select_runs

__all__ = [
    "BundleResult",
    "COMPLETED",
    "ExportRefusedError",
    "command_script",
    "out_refusal",
    "render_argv",
    "render_crate",
    "render_readme",
    "safe_sample",
    "step_folder",
    "write_bundle",
    "EntryRead",
    "ExportPlan",
    "LocalOnlyImage",
    "PlanImage",
    "PlanInput",
    "PlanOutput",
    "PlanStep",
    "REFUSED_TEXT",
    "Refusal",
    "Selection",
    "plan_export",
    "select_runs",
]
