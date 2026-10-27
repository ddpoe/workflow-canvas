"""Container-env manifest at ``.wfc/envs.json``.

The manifest is the tool-managed record of every container env known to a
project: :class:`EnvRecord` and the record API (:func:`load_manifest`,
:func:`save_manifest`, :func:`list_envs`, :func:`get`, :func:`delete`).
:func:`wfc.environments.register` writes a record when it builds an env.

Schema (``schema_version == 3``)::

    {
      "schema_version": 3,
      "envs": {
        "<env_name>": {
          "backend": "pixi" | "conda" | "byo",
          "source": "pixi.toml" | "environment.yml" | null,
          "container": "docker://<host>/<path>@sha256:<hex>",
          "env_fingerprint": "<md5 of the image-content blob>",
          "built_from_lock": "pixi.lock" | "conda-lock.yml" | null,
          "built_at": "<ISO-8601 timestamp>",
          "source_fingerprint": "<md5 of the captures folder's packages.lock>" | null,
          "python": "<container-side interpreter>"
        }
      }
    }

Every record's ``env_fingerprint`` has a captures folder
(:mod:`wfc.environments.captures`), and its ``source_fingerprint`` is the
md5 of that folder's ``packages.lock``.

The env name is the KEY of the ``envs`` dict; it is NOT stored inside the
record. Callers that need
to pair a name with its record receive a ``(name, EnvRecord)`` tuple from
:func:`list_envs`.

A missing manifest file is treated as an empty manifest (zero envs) — every
project starts that way until its first ``wfc register-env`` call.

An older manifest is refused by :func:`load_manifest`, naming each env's
re-register command: version 1 took its fingerprints from the image's name,
and version 2 recorded no captures folders. Only the register path opens
it, through :func:`load_manifest_for_register`; the register writes it
back as version 3, with the registered env fingerprinted and captured and
every other env's ``env_fingerprint`` cleared until that env is
re-registered too.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import layout
from ..reserved import DEMO_ENV_NAME

MANIFEST_SCHEMA_VERSION = 3
MANIFEST_FILENAME = layout.ENV_MANIFEST_FILENAME

#: The older manifest versions the register path converts from, each with
#: the reason every other reader refuses it.
_CONVERTIBLE_SCHEMA_VERSIONS: dict[int, str] = {
    1: "which fingerprinted each env by its image's name. Re-register each "
       "env to record a fingerprint of its image's contents",
    2: "which recorded no captures folder of what each env's image "
       "installed. Re-register each env to record one",
}


def recreate_command(name: str) -> str:
    """Return the command that recreates *name*'s env from scratch.

    Args:
        name: The env name.

    Returns:
        ``wfc demo --force`` for the demo env, otherwise
        ``wfc register-env <name> --force``.
    """
    if name == DEMO_ENV_NAME:
        return "wfc demo --force"
    return f"wfc register-env {name} --force"



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
    source: str | None
    container: str
    env_fingerprint: str
    built_at: str
    built_from_lock: str | None = None
    # md5 of the image's captures-folder packages.lock (lock/explicit-list +
    # an explicit delimiter + pip-freeze), recorded for EVERY pixi/conda
    # registration that stages source content — both live-spec capture and
    # ``--from`` file mode. ``None`` for byo. Read back, verified, by
    # :func:`wfc.environments.captures.verified_package_list` and parsed by
    # :func:`wfc.environments.packages.parse_packages`.
    source_fingerprint: str | None = None
    # Container-side path of the env's Python interpreter, recorded at
    # registration: computed for pixi/conda from the generator recipe;
    # ``"python"`` for byo (overridable via ``wfc register-env --python``).
    # Dispatch does not read it: a method version runs
    # the interpreter its own image's ``image.json`` records
    # (:func:`wfc.environments.interpreter.version_interpreter`).
    python: str | None = None

    def to_dict(self) -> dict:
        """Return a plain dict suitable for JSON serialization.

        The returned dict does NOT contain a ``name`` key — the name is the
        outer dict key in ``.wfc/envs.json::envs`` and is supplied by callers.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> EnvRecord:
        """Reconstruct an ``EnvRecord`` from a JSON-loaded dict.

        Unknown keys (including a stray ``name`` key) are ignored so a
        record written by an older or newer wfc can still be read without
        crashing — forward-compat for additive fields only.
        """
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in allowed})


# =============================================================================
# Manifest IO
# =============================================================================

def _manifest_path(project_dir: Path) -> Path:
    """Return the absolute path of ``<project>/.wfc/envs.json``."""
    return layout.env_manifest_path(Path(project_dir).resolve())


def _read_manifest(path: Path, accepted: tuple[int, ...]) -> dict:
    """Parse the manifest at *path*, refusing a version not in *accepted*.

    Args:
        path: Absolute path of ``.wfc/envs.json``.
        accepted: The ``schema_version`` values the caller can read.

    Returns:
        The parsed manifest, its ``schema_version`` as stored. An empty
        current-version manifest when the file does not exist.

    Raises:
        ValueError: If the file is not valid JSON, its ``envs`` object is
            missing or malformed, or its ``schema_version`` is not in
            *accepted*. An older (version 1 or 2) file is refused naming
            each env's re-register command.
    """
    if not path.exists():
        return {"schema_version": MANIFEST_SCHEMA_VERSION, "envs": {}}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from exc

    envs = raw.get("envs")
    if not isinstance(envs, dict):
        raise ValueError(f"{path}: missing or malformed 'envs' object")

    schema = raw.get("schema_version")
    if schema in accepted:
        return raw
    if schema in _CONVERTIBLE_SCHEMA_VERSIONS:
        commands = list(dict.fromkeys(recreate_command(n) for n in sorted(envs)))
        steps = ("\n".join(f"  {c}" for c in commands) if commands
                 else "  wfc register-env <name>")
        raise ValueError(
            f"{path}: this env manifest was written by an older wfc "
            f"(schema_version {schema}), "
            f"{_CONVERTIBLE_SCHEMA_VERSIONS[schema]}:\n{steps}"
        )
    raise ValueError(
        f"{path}: unknown schema_version {schema!r} "
        f"(this wfc version supports {MANIFEST_SCHEMA_VERSION})"
    )


def load_manifest(project_dir: Path) -> dict:
    """Read ``.wfc/envs.json`` and return the parsed manifest dict.

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        A dict shaped ``{"schema_version": 3, "envs": {<name>: <record-dict>}}``.
        When the file does not exist, an empty manifest is returned — this is
        the normal state for a project before any ``wfc register-env`` call.

    Raises:
        ValueError: If ``schema_version`` is not :data:`MANIFEST_SCHEMA_VERSION`
            (a version-1 or version-2 file is refused naming each env's
            re-register command) or the file is not valid JSON.
    """
    return _read_manifest(_manifest_path(project_dir), (MANIFEST_SCHEMA_VERSION,))


def load_manifest_for_register(project_dir: Path) -> dict:
    """Read ``.wfc/envs.json`` for a register, which may convert an older file.

    The register path is the one reader that opens a version-1 or version-2
    manifest: it writes the file back as the current version (see
    :func:`wfc.environments.build._write_record`). Every other reader uses
    :func:`load_manifest`, which refuses it.

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        The parsed manifest with its ``schema_version`` as stored (1, 2 or
        :data:`MANIFEST_SCHEMA_VERSION`); an empty current-version manifest
        when the file does not exist.

    Raises:
        ValueError: If the file is not valid JSON, its ``envs`` object is
            malformed, or its ``schema_version`` is neither an older
            convertible one nor current.
    """
    return _read_manifest(
        _manifest_path(project_dir),
        (MANIFEST_SCHEMA_VERSION, *_CONVERTIBLE_SCHEMA_VERSIONS),
    )


def put_record(manifest: dict, name: str, record: dict) -> None:
    """Put *record* under *name* in *manifest*, as the current version.

    A manifest read by :func:`load_manifest_for_register` at an older
    version is converted here: no other env's ``env_fingerprint`` has a
    captures folder (and a version-1 one was taken from its image's name),
    so it is cleared, and that env is refused at run time until it is
    re-registered.

    Args:
        manifest: The manifest as read; updated in place.
        name: The env name (the key in ``envs``).
        record: The record dict to store.
    """
    envs = manifest.setdefault("envs", {})
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        for other in envs.values():
            other["env_fingerprint"] = ""
    envs[name] = record
    manifest["schema_version"] = MANIFEST_SCHEMA_VERSION


#: Seconds :func:`save_manifest` waits before each retry of a replace another
#: handle refused: five tries in all, the waits doubling from 50 ms.
_REPLACE_RETRY_WAITS = (0.05, 0.1, 0.2, 0.4)


def _replace_with_retry(source: str, target: Path) -> None:
    """``os.replace`` *source* onto *target*, retrying a refused replace.

    On Windows a replace fails with ``PermissionError`` while another
    handle (a reader, an editor, a virus scanner) holds *target* open;
    such a hold is usually brief. The replace is tried five times, waiting
    :data:`_REPLACE_RETRY_WAITS` between tries, on every platform.

    Args:
        source: The file to move.
        target: Where it goes.

    Raises:
        PermissionError: The fifth try was refused too.
    """
    for wait in _REPLACE_RETRY_WAITS:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            time.sleep(wait)
    os.replace(source, target)


def save_manifest(project_dir: Path, manifest: dict) -> None:
    """Atomically write *manifest* to ``.wfc/envs.json``.

    Uses ``tempfile + os.replace`` so a partial write cannot leave the
    manifest in a corrupted state — readers see either the old content
    or the new content, never a half-flushed file. A replace refused
    because another handle holds the file open is retried five times
    over about 750 ms before it fails.

    Args:
        project_dir: Root directory of the wfc project (must contain ``.wfc/``).
        manifest: Manifest dict in the schema documented at module-level.
            The caller is responsible for setting ``schema_version`` correctly;
            this function does not patch it.

    Raises:
        FileNotFoundError: If the ``.wfc/`` directory does not exist.
        PermissionError: The replace was refused on every try; the old
            manifest is kept and the temp file removed.
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
        _replace_with_retry(tmp_path_str, path)
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


def get(name: str, project_dir: Path) -> EnvRecord | None:
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
) -> tuple[EnvRecord | None, str | None]:
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
    scope — and neither is the env's captures folder. ``wfc delete-env``
    calls this after refusing an env any method version records, removes
    the folder, and commits both.

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
