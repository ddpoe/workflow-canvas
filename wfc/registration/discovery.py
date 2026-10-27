"""Script discovery for method registration.

Refuses a folder that does not clearly hold one method, locates a method's
script, resolves its declared helpers, and refuses recognized scripts that
are neither.
"""

from __future__ import annotations

from pathlib import Path

from ..identity import RECOGNIZED_SCRIPT_EXTENSIONS, collect_method_scripts
from .snapshot import HELPER_SNAPSHOT_DIR


def _folder_scripts(method_dir: Path) -> list[str]:
    """The recognized scripts in a method folder, folder-relative, sorted."""
    return [
        p.relative_to(method_dir).as_posix()
        for p in collect_method_scripts(method_dir)
        if HELPER_SNAPSHOT_DIR not in p.relative_to(method_dir).parts
    ]


def check_method_folder(method_dir: Path, method_name: str, script_named: bool) -> None:
    """Refuse a folder that does not clearly hold one method.

    A method folder holds one ``method.yaml``: a second one anywhere in its
    tree is another method's, and is refused naming both. A folder holding
    more than one recognized script, at any depth, must name the main one
    (``script:`` in ``method.yaml``, or the CLI's ``--script``); every other
    script is then a helper.

    Args:
        method_dir: The source directory, resolved.
        method_name: The method's name, the stem the line to add suggests.
        script_named: True when the main script is named explicitly.

    Raises:
        ValueError: Naming the ``method.yaml`` files, or listing the scripts
            and the ``script:`` line to add.
    """
    yamls = sorted(p.relative_to(method_dir).as_posix()
                   for p in method_dir.rglob("method.yaml") if p.is_file())
    if len(yamls) > 1:
        raise ValueError(
            f"Cannot register from {method_dir}: it holds more than one "
            f"method.yaml ({', '.join(yamls)}). A method folder holds one "
            f"method: move the other method into its own folder, or remove "
            f"its method.yaml."
        )
    scripts = _folder_scripts(method_dir)
    if len(scripts) > 1 and not script_named:
        main = next((s for s in scripts if Path(s).stem == method_name
                     and "/" not in s), scripts[0])
        raise ValueError(
            f"Cannot register from {method_dir}: it holds more than one script "
            f"({', '.join(scripts)}) and method.yaml does not say which is the "
            f"main one. Add `script: {main}` to method.yaml (naming the main "
            f"script); every other script is then registered as a helper. "
            f"The `script:` line is part of the method's contract, so for a "
            f"method already registered the next registration adds a version."
        )


def _locate_method_script(
    method_dir: Path,
    method_name: str,
    script_name: str | None,
) -> Path:
    """Locate the method's script file inside *method_dir*.

    When *script_name* is given (CLI ``--script`` or method.yaml ``script:``),
    it is validated (recognized extension, file exists) and used verbatim.
    Otherwise the directory is probed for ``{method_name}.<ext>`` across
    :data:`wfc.identity.RECOGNIZED_SCRIPT_EXTENSIONS`; exactly one match is
    required. The probe compares actual directory entry names (case-sensitive
    string match), so a case-insensitive filesystem cannot produce a false
    ``.R``/``.r`` ambiguity.

    Args:
        method_dir: Resolved method directory.
        method_name: Method name (probe stem).
        script_name: Optional explicit script filename.

    Returns:
        Path to the located script.

    Raises:
        ValueError: If an explicit *script_name* has an unrecognized
            extension, resolves outside the method dir (symlinks are
            resolved before the containment check), or the probe finds
            multiple candidate scripts.
        FileNotFoundError: If the explicit script is missing, or the probe
            finds no candidate script.
    """
    recognized = "/".join(RECOGNIZED_SCRIPT_EXTENSIONS)

    if script_name is not None:
        ext = Path(script_name).suffix
        if ext not in RECOGNIZED_SCRIPT_EXTENSIONS:
            raise ValueError(
                f"Method script {script_name!r} has an unrecognized extension "
                f"{ext or '(none)'}. Recognized extensions: {recognized}."
            )
        script_path = method_dir / script_name
        # Containment: the registered script MUST live inside the method dir
        # so the step-8 snapshot and the code fingerprint (which walks the
        # snapshot) cover it. Resolve symlinks first — same rule as the
        # `helpers:` path validation — so a symlink escape is caught too.
        resolved = script_path.resolve()
        try:
            resolved.relative_to(method_dir.resolve())
        except ValueError:
            raise ValueError(
                f"Method script {script_name!r} resolves to {resolved}, "
                f"outside the method directory {method_dir}. The method "
                f"script must live inside the method directory so the "
                f"registration snapshot and code fingerprint cover it. For "
                f"shared out-of-dir code, list it under `helpers:` in "
                f"method.yaml instead."
            ) from None
        if not script_path.exists():
            raise FileNotFoundError(
                f"Script not found: {script_path}\n"
                f"Expected '{script_name}' in {method_dir}"
            )
        return script_path

    entry_names = {p.name for p in method_dir.iterdir() if p.is_file()}
    candidates = [
        f"{method_name}{ext}"
        for ext in RECOGNIZED_SCRIPT_EXTENSIONS
        if f"{method_name}{ext}" in entry_names
    ]

    if not candidates:
        probed = ", ".join(
            f"{method_name}{ext}" for ext in RECOGNIZED_SCRIPT_EXTENSIONS
        )
        raise FileNotFoundError(
            f"No method script found in {method_dir}.\n"
            f"Looked for: {probed}.\n"
            f"Name the script '{method_name}.<ext>' ({recognized}) or set the "
            f"optional `script:` key in method.yaml to name it explicitly."
        )
    if len(candidates) > 1:
        listing = ", ".join(candidates)
        raise ValueError(
            f"Ambiguous method script in {method_dir}: found {listing}. "
            f"A method has exactly one script — set the optional `script:` "
            f"key in method.yaml to select one (e.g. `script: {candidates[0]}`)."
        )
    return method_dir / candidates[0]


def snapshot_selected_script(snapshot_dir: Path, method_name: str) -> Path | None:
    """Return the script a method directory selects on its own, resolved.

    The same selection registration makes, with no explicit ``--script``:
    the ``script:`` key of the directory's ``method.yaml``, else the probe
    for ``{method_name}.<ext>``. Over a registered copy this answers which
    script the copy itself names, so registration can record the chosen
    script in the copy and the verify rule can check the record against it.

    Args:
        snapshot_dir: The directory to select in (a registered copy).
        method_name: The method's name (the probe stem).

    Returns:
        The selected script, resolved; ``None`` when the selection fails (an
        unparsable ``method.yaml``, a missing or ambiguous script, a script
        outside the directory).
    """
    from ..contracts import parse_method_yaml

    try:
        contract = parse_method_yaml(snapshot_dir)
        script_name = contract.get("script") if contract is not None else None
        return _locate_method_script(
            snapshot_dir, method_name, script_name).resolve()
    except (ValueError, TypeError, OSError):
        return None


def _resolve_declared_helpers(
    method_dir: Path,
    project_dir: Path,
    helpers: list[str],
    script_path: Path,
) -> list[Path]:
    """Validate and resolve the method.yaml ``helpers:`` declarations.

    Rules: paths are method-dir-relative; absolute paths are rejected. A
    helper whose resolved (symlink-free) path lies inside the method's own
    directory is accepted wherever that directory is, inside the project or
    not. A helper outside the method's directory (``../shared/x.py``) must
    resolve inside *project_dir*: its snapshot location mirrors its
    project-relative path, and that path is part of the code fingerprint.
    Each helper must exist, carry a recognized script extension, and not
    duplicate the main script.

    Args:
        method_dir: Resolved method directory.
        project_dir: Project root an out-of-directory helper must stay
            inside.
        helpers: Declared helper path strings.
        script_path: The method's main script (duplicate guard).

    Returns:
        Resolved absolute helper paths, in declaration order.

    Raises:
        ValueError: Absolute path, an out-of-directory helper outside the
            project (incl. symlink escape), not a file, or duplicate of
            the main script. A helper may be of any file type.
        FileNotFoundError: Declared helper does not exist.
    """
    method_root = method_dir.resolve()
    project_dir = project_dir.resolve()
    script_resolved = script_path.resolve()
    resolved: list[Path] = []
    for decl in helpers:
        if Path(decl).is_absolute():
            raise ValueError(
                f"method.yaml `helpers:` entry {decl!r} is an absolute path — "
                f"helpers must be paths relative to the method's directory."
            )
        helper = (method_root / decl).resolve()
        if not (helper.is_relative_to(method_root)
                or helper.is_relative_to(project_dir)):
            raise ValueError(
                f"method.yaml `helpers:` entry {decl!r} resolves to "
                f"{helper}, outside both the method's directory "
                f"{method_root} and the project root {project_dir}. Move the "
                f"helper into the method's directory, or keep the method's "
                f"source inside the project."
            )
        if not helper.exists():
            raise FileNotFoundError(
                f"method.yaml `helpers:` entry {decl!r} not found "
                f"(resolved to {helper})."
            )
        # A helper may be any file (a lookup table, a config): the code
        # fingerprint covers every file of the registered copy.
        if not helper.is_file():
            raise ValueError(
                f"method.yaml `helpers:` entry {decl!r} is not a file "
                f"(resolved to {helper}). List each helper file by its path."
            )
        if helper == script_resolved:
            raise ValueError(
                f"method.yaml `helpers:` entry {decl!r} duplicates the "
                f"method's main script {script_path.name!r} — list helpers "
                f"only."
            )
        resolved.append(helper)
    return resolved


def _check_no_undeclared_scripts(
    method_dir: Path,
    script_path: Path,
    helper_paths: list[Path],
) -> None:
    """Strict helpers mode: reject undeclared recognized scripts in the dir.

    Any recognized-extension file in *method_dir* that is neither the main
    script nor a declared helper (nor a previous snapshot's reserved
    ``_wfc_helpers/`` artifact) fails registration loudly — before any DB
    write — naming ``helpers:`` as the fix.

    Raises:
        ValueError: If undeclared recognized script files are present.
    """
    declared = {script_path.resolve()} | {h for h in helper_paths}
    undeclared = []
    for p in collect_method_scripts(method_dir):
        rel_parts = p.relative_to(method_dir).parts
        if HELPER_SNAPSHOT_DIR in rel_parts:
            continue
        if p.resolve() not in declared:
            undeclared.append(p.relative_to(method_dir).as_posix())
    if undeclared:
        listing = ", ".join(sorted(undeclared))
        raise ValueError(
            f"Strict helpers mode: undeclared script file(s) in "
            f"{method_dir}: {listing}. With `helpers:` present in "
            f"method.yaml, every recognized script file must be either the "
            f"method script or listed under `helpers:` — declare them or "
            f"remove them."
        )
