"""RunContext — the ``ctx`` object handed to ``@wfc.method`` functions.

This is the Tier-1 sugar over the canonical Tier-2 env-var + file contract
(ADR-020). It is a *metadata recorder*: it never copies, moves, reads, or
serializes the user's data bytes. ``save_artifact(name, path)`` records a
path; ``log_metric(name, value)`` records a scalar; at exit ``_finalize()``
writes a single ``_wfc_results.json`` manifest the host reads.

Pure stdlib: only ``json``, ``os``, ``pathlib``. No wfc / pandas /
sqlmodel imports, and no ``method.yaml`` read.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Filename of the single results channel (outputs + metrics) the host reads.
RESULTS_FILENAME = "_wfc_results.json"


class RunContext:
    """Runtime context for a wfc-managed method script.

    Reads the canonical ``WFC_*`` environment variables the host sets
    before launching the user process, and records declared outputs and
    metrics for the host to archive after the process exits.

    Attributes:
        run_dir: ``WFC_RUN_DIR`` — the directory the host can read after
            the container exits. All declared outputs must resolve inside
            this directory.
        workdir: A scratch directory at ``WFC_RUN_DIR/_workdir/``, created
            on access. The host deletes it after archiving.
        params: Parsed ``WFC_PARAMS`` dict.
    """

    def __init__(self):
        run_dir_env = os.environ.get("WFC_RUN_DIR")
        if not run_dir_env:
            raise RuntimeError(
                "WFC_RUN_DIR is not set. wfc-client methods must be launched "
                "by `wfc run-step`, which sets WFC_RUN_DIR / WFC_INPUT_PATHS / "
                "WFC_PARAMS before running your script."
            )
        self.run_dir = Path(run_dir_env).resolve()
        self.params = json.loads(os.environ.get("WFC_PARAMS", "{}"))

        self._input_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{}"))
        self._outputs: "dict[str, str]" = {}
        self._metrics: "dict[str, object]" = {}
        self._workdir: "Path | None" = None

    @property
    def workdir(self) -> Path:
        """Scratch directory at ``WFC_RUN_DIR/_workdir/`` (created on access).

        Located inside ``WFC_RUN_DIR`` so files written here automatically
        satisfy ``save_artifact``'s path-inside-run_dir constraint and are
        reachable by the host after the container exits.

        Returns:
            The path to the scratch directory.
        """
        if self._workdir is None:
            wd = self.run_dir / "_workdir"
            wd.mkdir(parents=True, exist_ok=True)
            self._workdir = wd
        return self._workdir

    def input(self, slot_name: str) -> "list[Path]":
        """Return resolved input paths for an input slot.

        Args:
            slot_name: The input slot name as declared in ``method.yaml``.

        Returns:
            A list of resolved :class:`~pathlib.Path` objects from
            ``WFC_INPUT_PATHS`` for that slot, or an empty list if the
            slot has no inputs.
        """
        paths = self._input_paths.get(slot_name, [])
        return [Path(p) for p in paths]

    def save_artifact(self, name: str, source_path) -> None:
        """Record that the file at ``source_path`` is the declared output ``name``.

        Does **not** copy, move, read, or serialize the file. The only
        guard is that ``source_path`` must resolve to a path inside
        ``WFC_RUN_DIR`` (the bind-mounted directory the host can read
        after the container exits). Output *type/extension* correctness is
        validated host-side after the run, not here.

        Args:
            name: The declared output name (from ``method.yaml``).
            source_path: Path to the already-written output file. Must
                resolve inside ``WFC_RUN_DIR``; use ``ctx.workdir`` or
                ``ctx.run_dir / 'name.ext'``.

        Raises:
            ValueError: If ``source_path`` resolves outside ``WFC_RUN_DIR``.
        """
        resolved = Path(source_path).resolve()
        try:
            rel = resolved.relative_to(self.run_dir)
        except ValueError:
            raise ValueError(
                f"save_artifact source must be inside WFC_RUN_DIR (got {source_path}). "
                f"Use ctx.workdir or write to ctx.run_dir / 'name.ext'."
            )
        self._outputs[name] = rel.as_posix()

    def log_metric(self, name: str, value) -> None:
        """Record a scalar metric.

        Args:
            name: Metric name.
            value: Scalar value (number, string, bool).
        """
        self._metrics[name] = value

    def _finalize(self) -> Path:
        """Write the ``_wfc_results.json`` manifest to ``WFC_RUN_DIR``.

        The manifest is the single results channel for both declared
        outputs and metrics. Output paths are relative to ``WFC_RUN_DIR``
        so the host can join them against its own run-dir without any
        container-vs-host path translation.

        Returns:
            The path to the written manifest.
        """
        manifest = {"outputs": self._outputs, "metrics": self._metrics}
        manifest_path = self.run_dir / RESULTS_FILENAME
        manifest_path.write_text(json.dumps(manifest, default=str))
        return manifest_path
