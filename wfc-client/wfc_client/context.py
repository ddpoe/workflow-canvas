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
    """The run context passed to your ``@wfc.method`` function as ``ctx``.

    :func:`wfc_client.run` creates it from the environment variables wfc
    sets when it runs the step (``WFC_RUN_DIR``, ``WFC_INPUT_PATHS`` and
    ``WFC_PARAMS``), so you do not create one yourself. Use it to find the
    step's input files and parameters, and to record the output files and
    metrics the step produces.

    Attributes:
        run_dir (pathlib.Path): The step's run directory. Every output file
            you record with :meth:`save_artifact` must be inside it.
        params (dict): The parameter values for this run, keyed by
            parameter name.

    Raises:
        RuntimeError: If ``WFC_RUN_DIR`` is not set, which means the script
            was not started by wfc.
    """

    def __init__(self) -> None:
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
        self._outputs: dict[str, str] = {}
        self._metrics: dict[str, object] = {}
        self._workdir: Path | None = None

    @property
    def workdir(self) -> Path:
        """A working directory for output files, created on first use.

        It is ``_workdir/`` inside :attr:`run_dir`, so any file you write
        here can be recorded with :meth:`save_artifact`.

        Returns:
            The path to the directory.
        """
        if self._workdir is None:
            wd = self.run_dir / "_workdir"
            wd.mkdir(parents=True, exist_ok=True)
            self._workdir = wd
        return self._workdir

    def input(self, slot_name: str) -> list[Path]:
        """Return the files connected to an input slot.

        Args:
            slot_name: The input name, as declared under ``inputs:`` in
                ``method.yaml``.

        Returns:
            The paths of the slot's input files. A slot that collects
            several upstream outputs returns one path per file. The list
            is empty when nothing is connected to the slot.
        """
        paths = self._input_paths.get(slot_name, [])
        return [Path(p) for p in paths]

    def save_artifact(self, name: str, source_path: str | os.PathLike[str]) -> None:
        """Record a file you have written as the output ``name``.

        Write the file first, then call this. The file is not copied or
        read here; wfc collects it after the script exits and checks it
        against the output's declared type in ``method.yaml``.

        Args:
            name: The output name, as declared under ``outputs:`` in
                ``method.yaml``.
            source_path: Path to the written file. It must be inside
                :attr:`run_dir`; write it under :attr:`workdir` or
                directly in ``ctx.run_dir``.

        Raises:
            ValueError: If ``source_path`` is outside :attr:`run_dir`.
        """
        resolved = Path(source_path).resolve()
        try:
            rel = resolved.relative_to(self.run_dir)
        except ValueError as exc:
            raise ValueError(
                f"save_artifact source must be inside WFC_RUN_DIR (got {source_path}). "
                f"Use ctx.workdir or write to ctx.run_dir / 'name.ext'."
            ) from exc
        self._outputs[name] = rel.as_posix()

    def log_metric(self, name: str, value: object) -> None:
        """Record a single value, such as a row count or a score, for this run.

        Recording the same name again replaces the earlier value.

        Args:
            name: The metric name.
            value: A number, string or boolean.
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
