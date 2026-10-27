"""The method snapshot: its write and its layout.

Method registration empties the snapshot,
``.wfc/registry/methods/<module>/<name>/``, and copies the registered file
set into it, so the code fingerprint reads, and the version commits,
exactly that set. Declared helpers from outside the method directory are
copied under ``HELPER_SNAPSHOT_DIR``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from axiom_annotations import task

from ..identity import collect_method_scripts
from ..persistence import project_root as get_project_root

# Reserved snapshot subdirectory for out-of-dir declared helpers (strict
# helpers mode). Out-of-dir helpers are copied under
# ``<snapshot>/_wfc_helpers/<project-relative-path>`` so the snapshot
# layout never contains ``..`` and the fingerprint (which walks the
# snapshot) covers them by construction.
HELPER_SNAPSHOT_DIR = "_wfc_helpers"


#: Directory names the not-copied listing never descends into: tool and
#: version-control state, not files an author placed beside the method.
_UNLISTED_DIR_NAMES = frozenset({
    "__pycache__", ".git", ".hg", ".svn", ".ipynb_checkpoints",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "node_modules",
})
#: File names the not-copied listing leaves out for the same reason.
_UNLISTED_FILE_NAMES = frozenset({".DS_Store", "Thumbs.db"})


def _not_copied(method_dir: Path, copied: set[Path]) -> list[str]:
    """List the source directory's files the snapshot does not hold.

    Args:
        method_dir: The source directory, resolved.
        copied: Resolved source paths of the files the snapshot copied.

    Returns:
        Sorted source-relative POSIX paths, leaving out tool and
        version-control state (``__pycache__``, ``.git`` and similar).
    """
    left_out: list[str] = []
    for current, dirnames, filenames in os.walk(method_dir):
        dirnames[:] = [d for d in dirnames if d not in _UNLISTED_DIR_NAMES]
        for name in filenames:
            if name in _UNLISTED_FILE_NAMES or name.endswith(".pyc"):
                continue
            path = Path(current) / name
            if path.resolve() in copied:
                continue
            left_out.append(path.relative_to(method_dir).as_posix())
    return sorted(left_out)


@task(
    purpose="Empty the method's snapshot and copy its scripts, method.yaml "
            "and declared helpers from its source directory into it, the code "
            "the version records and the code fingerprint reads, and name "
            "every other source file it left out",
    inputs="source directory, snapshot directory, main script, helpers mode, "
           "declared helpers, method name",
    outputs="the snapshot holding exactly the registered script set and "
            "method.yaml; the source-relative paths of the files not copied",
    critical="Always copies: a source directory is never its own snapshot. "
             "The snapshot is emptied first, so nothing an earlier "
             "registration or a hand left in it survives. "
             "Strict mode copies exactly the main script, method.yaml and the "
             "declared helpers (out-of-dir ones under _wfc_helpers/); "
             "permissive mode copies collect_method_scripts' set. "
             "method.yaml is copied verbatim unless the copy alone would not "
             "select the chosen main script (a --script choice): the copy "
             "then names it in `script:`, so the fingerprint covers the "
             "entry script. The source method.yaml is never written.",
)
def _write_snapshot(
    method_dir: Path,
    registered_dir: Path,
    script_path: Path,
    strict_helpers: bool,
    helper_paths: list[Path],
    method_name: str,
) -> list[str]:
    """Write the method's snapshot from its source directory.

    In strict helpers mode the snapshot holds exactly the main script, the
    declared helpers and ``method.yaml``. In permissive mode it holds the
    scripts ``collect_method_scripts`` finds (the set the code fingerprint
    hashes) and ``method.yaml``. Either way the snapshot is emptied first,
    so it holds nothing an earlier registration left. The copy of
    ``method.yaml`` is verbatim unless it would not select the main script
    on its own; it then names the main script in its ``script:`` key.

    Args:
        method_dir: The source directory being registered, resolved.
        registered_dir: The snapshot directory,
            ``.wfc/registry/methods/<module>/<name>/``.
        script_path: The main script.
        strict_helpers: True when ``method.yaml`` has a ``helpers:`` key.
        helper_paths: The declared helpers, resolved.
        method_name: The method's name, the stem the script probe uses.

    Returns:
        The source-relative POSIX paths of the source directory's files the
        snapshot does not hold, sorted; tool and version-control state
        (``__pycache__``, ``.git`` and similar) is not listed.
    """
    project_dir = get_project_root()
    method_dir = method_dir.resolve()
    # Rebuilt from scratch: nothing an earlier registration (or a hand) left
    # in the snapshot survives into the copy the fingerprint reads.
    if registered_dir.exists():
        shutil.rmtree(registered_dir)
    registered_dir.mkdir(parents=True, exist_ok=True)
    copied: set[Path] = set()

    def _copy(src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied.add(src.resolve())

    yaml_file = method_dir / "method.yaml"
    if strict_helpers:
        # Strict helpers mode: snapshot exactly main script + declared helpers
        # + method.yaml. Out-of-dir helpers land under the reserved
        # HELPER_SNAPSHOT_DIR mirroring their project-relative path (no
        # `..` in the snapshot layout). The fingerprint walks the snapshot,
        # so snapshot and fingerprint stay in lockstep by construction.
        helpers_dest_root = registered_dir / HELPER_SNAPSHOT_DIR
        _copy(script_path, registered_dir / script_path.relative_to(method_dir))

        for helper in helper_paths:
            try:
                dest = registered_dir / helper.relative_to(method_dir)
            except ValueError:
                # Out-of-dir helper: mirror its project-relative path under
                # the reserved subdir (collision-safe — project-relative
                # paths are unique).
                dest = helpers_dest_root / helper.relative_to(project_dir.resolve())
            _copy(helper, dest)
    else:
        # Permissive mode copies the method's recognised scripts; the
        # fingerprint then covers every file of the copy.
        for src_file in collect_method_scripts(method_dir):
            _copy(src_file, registered_dir / src_file.relative_to(method_dir))
    if yaml_file.exists():
        _copy(yaml_file, registered_dir / "method.yaml")
    # The copy names the entry script registration chose whenever it would
    # not select that script on its own (a --script choice), so the
    # fingerprint, the version and the key cover which script runs.
    script_rel = script_path.relative_to(method_dir).as_posix()
    if yaml_file.exists():
        _record_entry_script(registered_dir, method_name, script_rel)
    return _not_copied(method_dir, copied)


def _record_entry_script(registered_dir: Path, method_name: str,
                         script_rel: str) -> None:
    """Make the copy's ``method.yaml`` select the registered entry script.

    Nothing is written when the copy already selects it (its ``script:``
    key, else the ``{method}.<ext>`` probe), so a method registered without
    ``--script`` keeps its verbatim copy and its fingerprint. Otherwise the
    copy's top-level ``script:`` is set to *script_rel*: appended when the
    copy has none, or its line replaced; when that text edit would not parse
    to the same contract with only ``script:`` changed, the copy is dumped
    again from its parsed mapping (comments are then lost).

    Args:
        registered_dir: The snapshot directory, holding the copied
            ``method.yaml``.
        method_name: The method's name (the probe stem).
        script_rel: The entry script's snapshot-relative POSIX path.
    """
    import re

    import yaml

    from .discovery import snapshot_selected_script

    script = (registered_dir / script_rel).resolve()
    if snapshot_selected_script(registered_dir, method_name) == script:
        return
    copy = registered_dir / "method.yaml"
    text = copy.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    wanted = {**raw, "script": script_rel}
    line = yaml.safe_dump({"script": script_rel}, default_flow_style=False)
    pattern = re.compile(r"^script[ \t]*:.*$\n?", re.MULTILINE)
    if "script" in raw:
        edited = pattern.sub(line, text, count=1)
    else:
        edited = text + ("" if not text or text.endswith("\n") else "\n") + line
    try:
        parsed = yaml.safe_load(edited)
    except yaml.YAMLError:
        parsed = None
    if parsed != wanted:
        edited = yaml.safe_dump(wanted, sort_keys=False, allow_unicode=True)
    copy.write_text(edited, encoding="utf-8", newline="\n")

