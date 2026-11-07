"""RunContext — the ``ctx`` object handed to ``@wfc.method`` functions.

This is the Tier-1 sugar over the canonical Tier-2 env-var + file
contract. It is a *metadata recorder*: it never copies, moves, reads, or
serializes the user's data bytes. ``save_artifact(name, path)`` records a
path; ``log_metric(name, value)`` records a scalar; at exit ``_finalize()``
writes a single ``_wfc_results.json`` manifest the host reads.

Pure stdlib: only ``json``, ``os``, ``pathlib``, ``sys``. No wfc / pandas /
sqlmodel imports, and no ``method.yaml`` read: the declared names arrive in
``WFC_DECLARED_NAMES``.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# Filename of the single results channel (outputs + metrics) the host reads.
RESULTS_FILENAME = "_wfc_results.json"


class InputPath(type(Path())):  # type: ignore[misc]
    """The path of an input that takes one file, as ``ctx.input`` returns it.

    It is the platform's concrete path class, so ``isinstance(p, Path)``
    holds and every path operation works. It also answers ``[0]`` (returning
    itself) and iterates once, so a script written for the list form keeps
    running; the first such use per input writes one note to the step log.
    The concrete class is subclassed rather than ``pathlib.Path`` because
    ``Path`` cannot be subclassed directly before Python 3.12.
    """

    _wfc_slot: str | None = None
    _wfc_noted: set[str] | None = None

    def with_segments(self, *pathsegments: Any) -> Path:  # Python 3.12+: derived paths are plain
        """Return a derived path as the plain concrete class."""
        return type(Path())(*pathsegments)

    def _wfc_note(self) -> None:
        slot, noted = self._wfc_slot, self._wfc_noted
        if slot is None or noted is None or slot in noted:
            return
        noted.add(slot)
        sys.stderr.write(
            f"wfc: input '{slot}' takes one file, so ctx.input('{slot}') is "
            f"its path; the [0] (or the loop) is not needed.\n"
        )

    def __getitem__(self, index: int | slice) -> Path | list[Path]:
        if isinstance(index, slice):
            self._wfc_note()
            items: list[Path] = [self]
            return items[index]
        if index in (0, -1):
            self._wfc_note()
            return self
        raise IndexError(
            f"input '{self._wfc_slot}' takes one file: ctx.input("
            f"'{self._wfc_slot}') is that file's path, so only [0] names it"
        )

    def __iter__(self) -> Iterator[Path]:
        self._wfc_note()
        yield self


# The name the class had before it was public; code that imported it keeps working.
_SinglePath = InputPath


class UnknownInputError(KeyError):
    """``ctx.input`` was asked for a name the method does not declare."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""


class RunContext:
    """The run context passed to your ``@wfc.method`` function as ``ctx``.

    :func:`wfc_client.run` creates it from the environment variables wfc
    sets when it runs the step (``WFC_RUN_DIR``, ``WFC_INPUT_PATHS``,
    ``WFC_PARAMS`` and ``WFC_DECLARED_NAMES``), so you do not create one
    yourself. Use it to find the step's input files and parameters, and to
    record the output files and metrics the step produces.

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
        # The method's declared names; absent when an older wfc started the
        # step, in which case every input reads as a list, as it always did.
        declared_env = os.environ.get("WFC_DECLARED_NAMES")
        self._declared: dict[str, Any] | None = (
            json.loads(declared_env) if declared_env else None
        )
        self._noted_inputs: set[str] = set()
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

    def input(self, slot_name: str) -> Path | list[Path] | None:
        """Return the file, or files, connected to an input.

        Args:
            slot_name: The input name, as declared under ``inputs:`` in
                ``method.yaml``.

        Returns:
            For an input that takes one file, its path as an
            :class:`InputPath` (``None`` when an optional input has nothing
            connected). For an input declared
            ``multiple: true``, a list with one path per file (empty when
            nothing is connected). The single path also answers ``[0]``
            and iterates once, so scripts written for the list keep working.

        Raises:
            UnknownInputError: If the method declares no input of that name;
                the message lists the declared inputs.
            RuntimeError: If more than one file reached an input that takes
                one (wfc refuses such a pipeline before it runs).
        """
        paths = [Path(p) for p in self._input_paths.get(slot_name, [])]
        if self._declared is None:
            return paths
        inputs = self._declared.get("inputs") or {}
        if slot_name not in inputs:
            declared = ", ".join(f"'{n}'" for n in inputs) or "none"
            raise UnknownInputError(
                f"ctx.input('{slot_name}'): the method declares no input "
                f"'{slot_name}'. Declared inputs: {declared}."
            )
        if (inputs[slot_name] or {}).get("multiple"):
            return paths
        if not paths:
            return None
        if len(paths) > 1:
            raise RuntimeError(
                f"input '{slot_name}' takes one file but {len(paths)} reached "
                f"it; declare `multiple: true` on it in method.yaml to "
                f"collect several."
            )
        single = InputPath(paths[0])
        single._wfc_slot = slot_name
        single._wfc_noted = self._noted_inputs
        return single

    def save_artifact(self, name: str, source_path: str | os.PathLike[str]) -> None:
        """Record a file you have written as the output ``name``.

        Write the file first, then call this. The file is not copied or
        read here; wfc collects it after the script exits and checks it
        against the output's declared type in ``method.yaml``. A name the
        method does not declare is recorded with a warning in the step log
        naming the declared outputs.

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
        if self._declared is not None:
            outputs = list(self._declared.get("outputs") or [])
            if name not in outputs:
                declared = ", ".join(f"'{n}'" for n in outputs) or "none"
                sys.stderr.write(
                    f"wfc: WARNING: ctx.save_artifact('{name}', ...): the "
                    f"method declares no output '{name}'. Declared outputs: "
                    f"{declared}.\n"
                )
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
