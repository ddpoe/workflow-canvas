"""Every dependency pyproject.toml declares is used by the repository.

Covers:
  - Tier 1: each main and dev dependency is imported somewhere in the
    repository's Python, or is allowlisted with the place that runs it.
  - Tier 1: a declared dependency the environment did not install (an
    optional extra) still counts as used when the code imports it.
  - Tier 1: each allowlist entry is a declared dependency that nothing
    imports, and the file it names still holds the use it describes; an
    allowlisted type-stub package stubs a library the code still imports.

"Imported" is read from the source with an AST walk: ``import`` and
``from`` statements, plus string-literal ``importorskip``, ``import_module``
and ``__import__`` calls. Import names map to distributions through
``importlib.metadata.packages_distributions()``. A declared dependency that
is not installed is matched by its import name, the distribution name with
``-`` turned into ``_``, so the check gives the same answer with or without
the extras installed.

The docs group is out of scope: it serves the Sphinx build of ``userdocs/``,
which installs from ``userdocs/requirements.txt``. wfc-client declares its own
dependencies in its own pyproject.
"""

from __future__ import annotations

import ast
import importlib.metadata
import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# The repository's Python: where an import counts as a use.
SCANNED = ("wfc", "tests", "scripts", "tools", "wfc-client", "dev.py")

# Method scripts `wfc demo` copies into a project. They run inside the
# method's container, whose image installs what they import, so their
# imports say nothing about wfc's own environment.
NOT_SCANNED = ("wfc/demo/assets",)

# Dependencies that are used without being imported, each with the file that
# uses it, the text in that file that shows the use, and why it is needed.
ALLOWLIST: dict[str, tuple[str, str, str]] = {
    "snakemake": (
        "wfc/orchestration/engine.py", '"-m", "snakemake"',
        "the engine launches it as `python -m snakemake`"),
    "dvc-ssh": (
        "wfc/init.py", '"dvc_ssh"',
        "DVC loads it for an ssh:// archive, and `wfc init` accepts one only when it is installed"),
    "dvc-gs": (
        "wfc/init.py", '"dvc_gs"',
        "DVC loads it for a gs:// archive, and `wfc init` accepts one only when it is installed"),
    "dvc-azure": (
        "wfc/init.py", '"dvc_azure"',
        "DVC loads it for an azure:// archive, and `wfc init` accepts one only when it is installed"),
    "pytest-xdist": (
        ".pev/sops.toml", "-n auto",
        "pytest plugin: the parallel suite runs with `-n`"),
    "ruff": (".github/workflows/ci.yml", "poetry run ruff check", "the lint job runs it"),
    "mypy": (".github/workflows/ci.yml", "poetry run mypy", "the lint job runs it"),
    "types-pyyaml": (
        ".github/workflows/ci.yml", "poetry run mypy",
        "type stubs mypy reads for `yaml`"),
    "types-psutil": (
        ".github/workflows/ci.yml", "poetry run mypy",
        "type stubs mypy reads for `psutil`"),
}

# The allowlisted type-stub packages and the library each one stubs: a stub
# is needed only while the scanned code still imports that library.
STUBBED = {"types-pyyaml": "yaml", "types-psutil": "psutil"}

_IMPORT_CALLS = {"importorskip", "import_module", "__import__"}


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared() -> set[str]:
    poetry = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["poetry"]
    main = set(poetry["dependencies"]) - {"python"}
    dev = set(poetry["group"]["dev"]["dependencies"])
    return {_canonical(name) for name in main | dev}


def _python_files():
    skipped = [REPO / part for part in NOT_SCANNED]
    for entry in SCANNED:
        root = REPO / entry
        paths = [root] if root.is_file() else root.rglob("*.py")
        for path in paths:
            if "node_modules" in path.parts or any(path.is_relative_to(s) for s in skipped):
                continue
            yield path


def _imported_modules() -> set[str]:
    modules: set[str] = set()
    for path in _python_files():
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call) and node.args:
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                first = node.args[0]
                if name in _IMPORT_CALLS and isinstance(first, ast.Constant) and isinstance(first.value, str):
                    modules.add(first.value.split(".")[0])
    return modules


def _imported_distributions(owners: dict[str, list[str]] | None = None) -> set[str]:
    """Distributions the scanned code imports, given who owns each module.

    ``owners`` maps an import name to its distributions, as
    ``packages_distributions()`` reports them for the running environment.
    """
    if owners is None:
        owners = importlib.metadata.packages_distributions()
    modules = _imported_modules()
    installed = {_canonical(dist) for dists in owners.values() for dist in dists}
    imported = {_canonical(dist) for module in modules for dist in owners.get(module, ())}
    # A declared dependency the environment lacks (an extra left out of the
    # install) has no owner to look up, so it is found by its import name.
    for dist in _declared() - installed:
        if dist.replace("-", "_") in modules:
            imported.add(dist)
    return imported


def test_every_declared_dependency_is_imported_or_allowlisted():
    unused = _declared() - _imported_distributions() - set(ALLOWLIST)

    assert not unused, (
        f"pyproject.toml declares {sorted(unused)}, which nothing in {SCANNED} imports "
        f"and the allowlist does not name: remove it, or allowlist it with the file that uses it"
    )


def test_a_dependency_the_environment_did_not_install_counts_as_used_when_imported():
    # The DVC remote plugins are optional extras: `poetry install --with dev`
    # leaves them out, so that environment reports no owner for their modules.
    extras = {"dvc_s3", "dvc_ssh", "dvc_gs", "dvc_azure"}
    installed = importlib.metadata.packages_distributions()
    without_extras = {module: dists for module, dists in installed.items() if module not in extras}

    imported = _imported_distributions(without_extras)

    assert "dvc-s3" in imported
    assert not _declared() - imported - set(ALLOWLIST)


def test_each_allowlist_entry_is_declared_unimported_and_used_where_it_says():
    declared = _declared()
    imported = _imported_distributions()

    assert set(ALLOWLIST) <= declared, f"allowlisted but not declared: {sorted(set(ALLOWLIST) - declared)}"
    assert not set(ALLOWLIST) & imported, (
        f"allowlisted but imported, so the entry is not needed: {sorted(set(ALLOWLIST) & imported)}")
    for name, (where, text, _reason) in ALLOWLIST.items():
        assert text in (REPO / where).read_text(encoding="utf-8"), f"{name}: {where} no longer holds {text!r}"
    modules = _imported_modules()
    orphaned = {stub: library for stub, library in STUBBED.items() if library not in modules}
    assert not orphaned, f"nothing in {SCANNED} imports the library these allowlisted stubs cover: {orphaned}"
