"""Container-env manifest at ``.wfc/envs.json``.

The manifest is the tool-managed record of every container env known to a
project: :class:`EnvRecord` and the record API (:func:`load_manifest`,
:func:`save_manifest`, :func:`list_envs`, :func:`get`, :func:`delete`).
:func:`wfc.environments.register` writes a record when it builds an env.

Schema (``schema_version == 1``)::

    {
      "schema_version": 1,
      "envs": {
        "<env_name>": {
          "backend": "pixi" | "conda" | "byo",
          "source": "pixi.toml" | "environment.yml" | null,
          "container": "docker://<host>/<path>@sha256:<hex>",
          "env_fingerprint": "<sha256 of source-file content / image fingerprint>",
          "built_from_lock": "pixi.lock" | "conda-lock.yml" | null,
          "built_at": "<ISO-8601 timestamp>"
        }
      }
    }

The env name is the KEY of the ``envs`` dict; it is NOT stored inside the
record. Callers that need
to pair a name with its record receive a ``(name, EnvRecord)`` tuple from
:func:`list_envs`.

A missing manifest file is treated as an empty manifest (zero envs) — every
project starts that way until its first ``wfc register-env`` call.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .. import layout


MANIFEST_SCHEMA_VERSION = 1
MANIFEST_FILENAME = layout.ENV_MANIFEST_FILENAME



@dataclass
class EnvRecord:
    """One row in ``.wfc/envs.json::envs``.

    Mirrors the JSON shape in the module docstring.
    The env *name* is the KEY of the ``envs`` dict and is **not** a field on
    this record — pairing a name with a record is the caller's responsibility
    (see :func:`list_envs`, which returns ``(name, EnvRecord)`` tuples).

    Optional fields default to ``None`` so a record that lacks them still
    loads.
    """

    backend: str
    source: Optional[str]
    container: str
    env_fingerprint: str
    built_at: str
    built_from_lock: Optional[str] = None
    # md5 of the captured package-list blob (lock/explicit-list + an explicit
    # delimiter + pip-freeze), recorded for EVERY pixi/conda registration that
    # stages source content — both live-spec capture and ``--from`` file mode.
    # ``None`` for byo and for legacy/no-source registrations. Retrievable via
    # ``GET /api/registry/envs/blob/<md5>`` and parsed by
    # :func:`wfc.environments.packages.parse_packages`.
    source_fingerprint: Optional[str] = None
    # Container-side path of the env's Python interpreter, recorded at
    # registration: computed for pixi/conda from the generator recipe;
    # ``"python"`` for byo (overridable via ``wfc register-env --python``).
    # ``None`` for records written before this field existed — dispatch
    # falls back to the per-backend default via :func:`resolve_env_python`
    # (identical values by construction, so no re-registration is needed).
    python: Optional[str] = None

    def to_dict(self) -> dict:
        """Return a plain dict suitable for JSON serialization.

        The returned dict does NOT contain a ``name`` key — the name is the
        outer dict key in ``.wfc/envs.json::envs`` and is supplied by callers.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EnvRecord":
        """Reconstruct an ``EnvRecord`` from a JSON-loaded dict.

        Unknown keys (including a stray ``name`` key) are ignored so a
        record written by an older or newer wfc can still be read without
        crashing — forward-compat for additive fields only.
        """
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})


# =============================================================================
# Manifest IO
# =============================================================================

def _manifest_path(project_dir: Path) -> Path:
    """Return the absolute path of ``<project>/.wfc/envs.json``."""
    return layout.env_manifest_path(Path(project_dir).resolve())


def load_manifest(project_dir: Path) -> dict:
    """Read ``.wfc/envs.json`` and return the parsed manifest dict.

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        A dict shaped ``{"schema_version": 1, "envs": {<name>: <record-dict>}}``.
        When the file does not exist, an empty manifest is returned — this is
        the normal state for a project before any ``wfc register-env`` call.

    Raises:
        ValueError: If ``schema_version`` is not :data:`MANIFEST_SCHEMA_VERSION`
            or the file is not valid JSON.
    """
    path = _manifest_path(project_dir)
    if not path.exists():
        return {"schema_version": MANIFEST_SCHEMA_VERSION, "envs": {}}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from exc

    schema = raw.get("schema_version")
    if schema != MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"{path}: unknown schema_version {schema!r} "
            f"(this wfc version supports {MANIFEST_SCHEMA_VERSION})"
        )

    if "envs" not in raw or not isinstance(raw["envs"], dict):
        raise ValueError(f"{path}: missing or malformed 'envs' object")

    return raw


def save_manifest(project_dir: Path, manifest: dict) -> None:
    """Atomically write *manifest* to ``.wfc/envs.json``.

    Uses ``tempfile + os.replace`` so a partial write cannot leave the
    manifest in a corrupted state — readers see either the old content
    or the new content, never a half-flushed file.

    Args:
        project_dir: Root directory of the wfc project (must contain ``.wfc/``).
        manifest: Manifest dict in the schema documented at module-level.
            The caller is responsible for setting ``schema_version`` correctly;
            this function does not patch it.

    Raises:
        FileNotFoundError: If the ``.wfc/`` directory does not exist.
    """
    path = _manifest_path(project_dir)
    wfc_state_dir = path.parent
    if not wfc_state_dir.exists():
        raise FileNotFoundError(
            f"No .wfc/ directory at {wfc_state_dir.parent} — run `wfc init` first"
        )

    # Write to a sibling temp file in the same directory so os.replace is
    # atomic on every platform (rename across filesystems is not atomic).
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=".envs.", suffix=".json.tmp", dir=str(wfc_state_dir)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_path_str, path)
    except Exception:
        # Best-effort cleanup of the temp file on failure.
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise


# =============================================================================
# Read-side public API
# =============================================================================

def list_envs(project_dir: Path) -> list[tuple[str, EnvRecord]]:
    """Return every env in the manifest as ``(name, EnvRecord)`` tuples, sorted by name.

    The name is paired with the record at the API boundary because it is the
    KEY of ``.wfc/envs.json::envs`` — not a field stored inside the record.

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        List of ``(env_name, EnvRecord)`` tuples sorted by env_name. Empty
        when no manifest exists or the manifest's ``envs`` block is empty.
    """
    manifest = load_manifest(project_dir)
    envs = manifest.get("envs", {})
    return [(name, EnvRecord.from_dict(envs[name])) for name in sorted(envs)]


def get(name: str, project_dir: Path) -> Optional[EnvRecord]:
    """Return the env record for *name*, or ``None`` if missing.

    Args:
        name: Env name (key in ``.wfc/envs.json::envs``).
        project_dir: Root directory of the wfc project.

    Returns:
        The :class:`EnvRecord` if present, else ``None``.
    """
    manifest = load_manifest(project_dir)
    record = manifest.get("envs", {}).get(name)
    if record is None:
        return None
    return EnvRecord.from_dict(record)


def env_record_for_spec(
    spec: str, project_dir: Path
) -> tuple[Optional[EnvRecord], Optional[str]]:
    """Resolve a ``Method.env`` spec to its registered record and backend.

    Takes the spec through the env-spec grammar's read side (a legacy
    ``container:`` prefix stripped once) to the bare manifest name, then
    looks it up in ``.wfc/envs.json``. A value outside the grammar, an
    unreadable manifest and a name with no manifest entry all resolve to
    ``(None, None)``, so a caller can show an honest "not captured" state
    rather than an error.

    Args:
        spec: The ``Method.env`` value: a bare env name (e.g. ``demo``); a
            stored legacy ``container:demo`` resolves to the same record.
        project_dir: Project root containing ``.wfc/``.

    Returns:
        ``(record, backend)``: the :class:`EnvRecord` and its ``backend``
        string, or ``(None, None)`` when no manifest entry matches.
    """
    from ..contracts import parse_env_spec

    try:
        record = get(parse_env_spec(spec), project_dir)
    except Exception:
        record = None
    if record is None:
        return None, None
    return record, record.backend


def delete(name: str, project_dir: Path) -> None:
    """Remove *name* from the manifest. Errors if the env is unknown.

    The registry image tag is **not** removed — deleting images is out of
    scope. Method rows that reference this env are also left
    untouched; the CLI surface is responsible for warning the user before
    calling this.

    Args:
        name: Env name to remove.
        project_dir: Root directory of the wfc project.

    Raises:
        KeyError: If *name* is not in the manifest.
    """
    manifest = load_manifest(project_dir)
    envs = manifest.get("envs", {})
    if name not in envs:
        raise KeyError(name)
    del envs[name]
    manifest["envs"] = envs
    save_manifest(project_dir, manifest)
