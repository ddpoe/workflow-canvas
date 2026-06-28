"""Host-side reader for the ``_wfc_results.json`` results manifest (ADR-020).

This is Phase 2 (archive) input parsing. After a container exits, the host
reads ``${run_dir}/_wfc_results.json`` — the single results channel for
both declared outputs and metrics — resolving each manifest-relative
output path against ``run_dir`` and validating it sits inside ``run_dir``.

It does **not** hash, cache, or write DB rows. The existing row-based
``wfc/provenance.py::archive_outputs`` sweep handles ADR-018 hashing and
DVC caching after ``run_step`` populates ``RunOutput`` rows from this data.

Tier 2 (no manifest): ``read_results_manifest`` returns ``None`` and the
caller falls back to scanning ``run_dir`` for declared output filenames.

Pure host-side function — no container, no Docker, runnable in isolation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Filename of the single results channel written by wfc-client (or by hand
# in a pure Tier-2 method that wants to record metrics).
RESULTS_FILENAME = "_wfc_results.json"


@dataclass
class ManifestResults:
    """Parsed ``_wfc_results.json`` for one run.

    Attributes:
        outputs: Mapping of declared output name -> resolved absolute path
            inside ``run_dir``.
        metrics: Mapping of metric name -> scalar value.
    """

    outputs: "dict[str, Path]" = field(default_factory=dict)
    metrics: "dict[str, object]" = field(default_factory=dict)


def read_results_manifest(run_dir: "Path | str") -> "ManifestResults | None":
    """Read and validate ``${run_dir}/_wfc_results.json``.

    Args:
        run_dir: The populated run directory (``WFC_RUN_DIR`` equivalent on
            the host).

    Returns:
        A :class:`ManifestResults` with resolved absolute output paths and
        metrics, or ``None`` when no manifest is present (Tier-2 mode — the
        caller scans ``run_dir`` for declared outputs instead).

    Raises:
        ValueError: If the manifest is malformed, an output path resolves
            outside ``run_dir`` (escape attempt), or a declared output file
            does not exist at its recorded path.
    """
    run_dir = Path(run_dir).resolve()
    manifest_path = run_dir / RESULTS_FILENAME
    if not manifest_path.exists():
        return None

    try:
        raw = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(
            f"Malformed results manifest at {manifest_path}: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise ValueError(
            f"Results manifest at {manifest_path} must be a JSON object, "
            f"got {type(raw).__name__}."
        )

    outputs_raw = raw.get("outputs", {}) or {}
    metrics = raw.get("metrics", {}) or {}
    if not isinstance(outputs_raw, dict) or not isinstance(metrics, dict):
        raise ValueError(
            f"Results manifest at {manifest_path} must have dict 'outputs' "
            f"and 'metrics' fields."
        )

    resolved: "dict[str, Path]" = {}
    for name, rel_path in outputs_raw.items():
        candidate = (run_dir / rel_path).resolve()
        try:
            candidate.relative_to(run_dir)
        except ValueError:
            raise ValueError(
                f"Results manifest output '{name}' resolves to {candidate}, "
                f"which is outside the run_dir {run_dir}. Manifest output "
                f"paths must be relative to WFC_RUN_DIR and stay inside it."
            )
        if not candidate.exists():
            raise ValueError(
                f"Results manifest output '{name}' points at {candidate}, "
                f"which does not exist. The method recorded an output it did "
                f"not write."
            )
        resolved[name] = candidate

    return ManifestResults(outputs=resolved, metrics=metrics)
