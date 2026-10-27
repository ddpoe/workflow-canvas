"""The project's configuration file: its reader and its one-key writer.

:func:`read_config` parses ``wf-canvas.toml`` — located through Layout's
marker path — and returns the settings ``wfc`` reads back from it: the pixi
and conda environment roots, the ``[dvc]`` archive entry, the
``[integrity] inputs`` setting and the ``[registry]`` base.

:func:`set_registry_url` writes the one key ``wfc`` sets after ``wfc init``,
``[registry] url``, by a text edit that leaves every other byte of the file
as it was (no TOML writer: a rewrite would drop the user's comments and
layout).

Inside ``wfc`` the module imports Layout and the standard library only: it
opens the config file, it never touches the database.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from axiom_annotations import Step, task

from .. import layout

#: ``[integrity] inputs``: matching stat fields vouch for a read; a mismatch
#: re-hashes. The default when the section or key is absent.
INTEGRITY_INPUTS_STAT_THEN_HASH = "stat-then-hash"
#: ``[integrity] inputs``: every read with a recorded hash re-hashes.
INTEGRITY_INPUTS_HASH = "hash"
#: The values ``[integrity] inputs`` accepts.
INTEGRITY_INPUTS_VALUES = (INTEGRITY_INPUTS_STAT_THEN_HASH, INTEGRITY_INPUTS_HASH)


@task(purpose="Read the project wf-canvas.toml and return the environment roots, "
              "the [dvc] archive entry, the [integrity] inputs setting and the "
              "[registry] base")
def read_config(project_dir: Path) -> dict:
    """Read the project wf-canvas.toml and return parsed settings.

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        Dict with ``pixi_root``, ``conda_root``, ``dvc``, ``integrity`` and
        ``registry``. The two roots are resolved absolute path strings to
        the pixi and conda environment root directories (``conda_root`` is
        ``""`` when no ``[conda]`` root is declared); ``dvc`` is the parsed
        ``[dvc]`` archive entry, or ``None``; ``integrity`` is
        ``{"inputs": ...}``, the ``[integrity] inputs`` setting
        (``stat-then-hash`` when absent); ``registry`` is ``{"url": ...}``,
        the base env images are pushed under, or ``None`` when
        ``[registry] url`` is absent or empty.

    Raises:
        FileNotFoundError: If wf-canvas.toml does not exist.
        ValueError: ``[integrity] inputs`` names a value other than
            ``stat-then-hash`` or ``hash``, or ``[registry] url`` is not a
            string.
    """
    import tomllib

    口 = Step(step_num=1, name="Locate config file",
             purpose="Find .wfc/wf-canvas.toml in the project directory")

    project_dir = Path(project_dir).resolve()
    config_path = layout.marker_path(project_dir)
    if not config_path.exists():
        raise FileNotFoundError(
            f"No wfc project found at {project_dir} — run `wfc init` first"
        )

    口 = Step(step_num=2, name="Parse config",
             purpose="Read wf-canvas.toml with tomllib and extract settings")

    with open(config_path, "rb") as f:
        parsed = tomllib.load(f)

    result: dict = {}

    口 = Step(step_num=3, name="Resolve pixi root",
             purpose="Parse [pixi] section and resolve the root path")

    pixi_section = parsed.get("pixi", {})
    pixi_root_raw = pixi_section.get("root", ".pixi")
    pixi_root_path = Path(pixi_root_raw)
    if not pixi_root_path.is_absolute():
        pixi_root_path = project_dir / pixi_root_path
    result["pixi_root"] = str(pixi_root_path.resolve())

    口 = Step(step_num=4, name="Parse conda config",
             purpose="Extract optional [conda] root for conda env resolution")

    conda_section = parsed.get("conda", {})
    conda_root_raw = conda_section.get("root", "")
    if conda_root_raw:
        conda_root_path = Path(conda_root_raw)
        if not conda_root_path.is_absolute():
            conda_root_path = project_dir / conda_root_path
        result["conda_root"] = str(conda_root_path.resolve())
    else:
        result["conda_root"] = ""

    口 = Step(step_num=5, name="Parse DVC config",
             purpose="Extract [dvc] section for provenance storage")

    dvc_section = parsed.get("dvc", None)
    if dvc_section is not None:
        # Prefer `url` (any scheme); fall back to legacy
        # `remote_path` (local-only) for backwards compatibility.
        result["dvc"] = {
            "url": dvc_section.get("url") or dvc_section.get("remote_path"),
            "remote_type": dvc_section.get("remote_type", "local"),
            "remote_path": dvc_section.get("remote_path"),
            "auto_init": dvc_section.get("auto_init", True),
        }
    else:
        result["dvc"] = None

    口 = Step(step_num=6, name="Parse the integrity setting",
             purpose="Read [integrity] inputs, stat-then-hash when absent, and "
                     "refuse any other value naming the two it accepts")

    integrity_section = parsed.get("integrity", {})
    inputs = integrity_section.get("inputs", INTEGRITY_INPUTS_STAT_THEN_HASH)
    if inputs not in INTEGRITY_INPUTS_VALUES:
        raise ValueError(
            f"{config_path}: [integrity] inputs = {inputs!r} is not a setting "
            f"wfc knows; use \"{INTEGRITY_INPUTS_STAT_THEN_HASH}\" (the default: "
            f"matching stat fields vouch for a read) or \"{INTEGRITY_INPUTS_HASH}\" "
            f"(every read re-hashes)"
        )
    result["integrity"] = {"inputs": inputs}

    口 = Step(step_num=7, name="Parse the registry base",
             purpose="Read [registry] url, the base env images are pushed "
                     "under; None when the section or key is absent or empty")

    result["registry"] = _registry_entry(parsed, config_path)

    return result


def _registry_entry(parsed: dict, config_path: Path) -> dict | None:
    """Return ``{"url": ...}`` from a parsed config, or ``None`` when unset.

    Args:
        parsed: The config as ``tomllib`` parsed it.
        config_path: The file, named in a refusal.

    Returns:
        ``{"url": <the base>}``, or ``None`` when ``[registry] url`` is
        absent or empty.

    Raises:
        ValueError: ``[registry]`` is not a table, or its ``url`` is not a
            string.
    """
    section = parsed.get("registry")
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ValueError(f"{config_path}: [registry] is not a table")
    url = section.get("url")
    if url is None:
        return None
    if not isinstance(url, str):
        raise ValueError(
            f"{config_path}: [registry] url = {url!r} is not a string; it names "
            f"the registry base env images are pushed under, e.g. "
            f"\"ghcr.io/<owner>/<repo>\""
        )
    url = url.strip()
    return {"url": url} if url else None


#: A table header line (``[name]`` or ``[[name]]``), with an optional comment.
_HEADER_RE = re.compile(r"^[ \t]*\[")
#: The ``[registry]`` header line.
_REGISTRY_HEADER_RE = re.compile(r"^[ \t]*\[[ \t]*registry[ \t]*\][ \t]*(?:#.*)?$")
#: A ``url = ...`` line inside a table (bare or quoted key).
_URL_KEY_RE = re.compile(r"""^([ \t]*)(?:url|"url"|'url')[ \t]*=""")


def set_registry_url(project_dir: Path, url: str) -> Path:
    """Set ``[registry] url`` in the project's ``wf-canvas.toml`` by a text edit.

    The ``url`` line of an existing ``[registry]`` table is replaced; a
    ``[registry]`` table without one gets the line under its header; a file
    without the table gets it appended. Every other byte of the file is
    kept: comments, key order, blank lines and the file's line endings. The
    edited text is parsed back before it is written, so a file that already
    spells the key another way (a dotted ``registry.url`` key, an inline
    table) is refused rather than left holding two values.

    Args:
        project_dir: Root directory of the wfc project.
        url: The registry base (``ghcr.io/<owner>/<repo>``).

    Returns:
        The config file's path.

    Raises:
        FileNotFoundError: If wf-canvas.toml does not exist.
        ValueError: *url* is empty, or the edit would not leave
            ``[registry] url`` reading *url* (the file defines the key in a
            form this edit does not touch, or is not valid TOML).
    """
    import tomllib

    url = (url or "").strip()
    if not url:
        raise ValueError("a registry base cannot be empty")
    project_dir = Path(project_dir).resolve()
    config_path = layout.marker_path(project_dir)
    if not config_path.exists():
        raise FileNotFoundError(
            f"No wfc project found at {project_dir} — run `wfc init` first"
        )
    raw = config_path.read_bytes()
    text = raw.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    value_line = f"url = {json.dumps(url, ensure_ascii=False)}"
    lines = text.splitlines(keepends=True)

    header = next((i for i, line in enumerate(lines)
                   if _REGISTRY_HEADER_RE.match(line.rstrip("\r\n"))), None)
    if header is None:
        lead = "" if not text or text.endswith(("\n", "\r")) else newline
        sep = newline if text.strip() else ""
        new_text = f"{text}{lead}{sep}[registry]{newline}{value_line}{newline}"
    else:
        end = next((i for i in range(header + 1, len(lines))
                    if _HEADER_RE.match(lines[i])), len(lines))
        found = next(((i, m) for i in range(header + 1, end)
                      if (m := _URL_KEY_RE.match(lines[i]))), None)
        if found is not None:
            key, key_match = found
            indent = key_match.group(1)
            ending = lines[key][len(lines[key].rstrip("\r\n")):] or newline
            lines[key] = f"{indent}{value_line}{ending}"
        else:
            if not lines[header].endswith(("\n", "\r")):
                lines[header] += newline
            lines.insert(header + 1, f"{value_line}{newline}")
        new_text = "".join(lines)

    try:
        parsed = tomllib.loads(new_text)
        written = _registry_entry(parsed, config_path)
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        raise ValueError(
            f"{config_path}: [registry] url could not be set by an edit that "
            f"keeps the rest of the file ({exc}); set it by hand"
        ) from exc
    if written != {"url": url}:
        raise ValueError(
            f"{config_path}: [registry] url could not be set by an edit that "
            f"keeps the rest of the file (the file defines the registry in "
            f"another form); set it by hand"
        )

    fd, tmp = tempfile.mkstemp(prefix=".wf-canvas.", suffix=".tmp",
                               dir=config_path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(new_text.encode("utf-8"))
        os.replace(tmp, config_path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return config_path
