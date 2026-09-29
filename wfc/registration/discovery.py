"""Script discovery: locate a method's script, resolve its declared helpers, and
refuse recognized scripts that are neither."""

from __future__ import annotations

from pathlib import Path

from ..identity import collect_method_scripts, RECOGNIZED_SCRIPT_EXTENSIONS
from .snapshot import HELPER_SNAPSHOT_DIR


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


def _resolve_declared_helpers(
    method_dir: Path,
    project_dir: Path,
    helpers: list[str],
    script_path: Path,
) -> list[Path]:
    """Validate and resolve the method.yaml ``helpers:`` declarations.

    Rules: paths are method-dir-relative; ``../`` is allowed but the
    resolved (symlink-free) path must stay inside *project_dir*; absolute
    paths are rejected; each helper must exist, carry a recognized script
    extension, and not duplicate the main script.

    Args:
        method_dir: Resolved method directory.
        project_dir: Project root the helpers must stay inside.
        helpers: Declared helper path strings.
        script_path: The method's main script (duplicate guard).

    Returns:
        Resolved absolute helper paths, in declaration order.

    Raises:
        ValueError: Absolute path, project escape (incl. symlink escape),
            unrecognized extension, or duplicate of the main script.
        FileNotFoundError: Declared helper does not exist.
    """
    recognized = "/".join(RECOGNIZED_SCRIPT_EXTENSIONS)
    project_dir = project_dir.resolve()
    script_resolved = script_path.resolve()
    resolved: list[Path] = []
    for decl in helpers:
        if Path(decl).is_absolute():
            raise ValueError(
                f"method.yaml `helpers:` entry {decl!r} is an absolute path — "
                f"helpers must be method-dir-relative paths inside the project."
            )
        helper = (method_dir / decl).resolve()
        try:
            helper.relative_to(project_dir)
        except ValueError:
            raise ValueError(
                f"method.yaml `helpers:` entry {decl!r} resolves to "
                f"{helper}, outside the project root {project_dir} — "
                f"helpers must stay inside the project."
            ) from None
        if not helper.exists():
            raise FileNotFoundError(
                f"method.yaml `helpers:` entry {decl!r} not found "
                f"(resolved to {helper})."
            )
        if helper.suffix not in RECOGNIZED_SCRIPT_EXTENSIONS:
            raise ValueError(
                f"method.yaml `helpers:` entry {decl!r} has an unrecognized "
                f"extension. Recognized extensions: {recognized}."
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
