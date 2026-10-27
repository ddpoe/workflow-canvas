"""Layout is the one place a registry path is spelled.

Covers:
  - Tier 1: a static scan of every module under ``wfc/`` outside
    ``wfc/layout/`` for a string literal naming the registry's path
    segments (``registry`` as a path segment, alone or beside a separator),
    the literal a ``Path`` join or an f-string would need to build a
    registry path itself. Docstrings are prose and are not scanned.
  - Tier 1: the scan's self-test: a synthetic offender is reported (bare
    segment, joined path, f-string piece) and a docstring naming the same
    path is not, nor a mapping key (``wf-canvas.toml``'s ``[registry]``
    table read by name), nor a ``name=`` keyword's value (a readiness row's
    label, ``CheckResult(name="registry")``), while the same literal passed
    by another keyword is still reported.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

WFC = Path(__file__).resolve().parents[1] / "wfc"
LAYOUT = WFC / "layout"

#: ``registry`` as a path segment: the whole literal, or bounded by a path
#: separator on either side (``.wfc/registry``, ``registry/methods``). The
#: canvas API's URL namespace (``/api/registry/...``) is a route, not a path
#: on disk, and is not a registry path.
_SEGMENT = re.compile(
    r"(^registry$)|((?<!/api)[/\\]registry([/\\]|$))|(^registry[/\\])")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Return the ids of the Constant nodes that are docstrings."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                ids.add(id(body[0].value))
    return ids


def _mapping_key_nodes(tree: ast.AST) -> set[int]:
    """Return the ids of the Constant nodes used as a mapping key.

    A subscript key (``cfg["registry"]``), the key a ``.get`` asks for
    (``parsed.get("registry")``) and a dict display's key name a TOML table
    or a result field (``wf-canvas.toml``'s ``[registry]``), not a path.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            ids.add(id(node.slice))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant)):
            ids.add(id(node.args[0]))
        elif isinstance(node, ast.Dict):
            ids.update(id(key) for key in node.keys if isinstance(key, ast.Constant))
        elif (isinstance(node, ast.keyword) and node.arg == "name"
                and isinstance(node.value, ast.Constant)):
            # A readiness row's label (``CheckResult(name="registry")``).
            ids.add(id(node.value))
    return ids


def registry_path_literals(source: str) -> list[tuple[int, str]]:
    """Return the non-docstring string literals naming a registry segment.

    Args:
        source: Python source text.

    Returns:
        ``(line, literal)`` for each offending literal, in source order.
    """
    tree = ast.parse(source)
    skipped = _docstring_nodes(tree) | _mapping_key_nodes(tree)
    hits = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in skipped
                and _SEGMENT.search(node.value.strip())):
            hits.append((node.lineno, node.value))
    return sorted(hits)


def test_no_module_outside_layout_spells_a_registry_path():
    offenders = []
    for path in sorted(WFC.rglob("*.py")):
        if LAYOUT in path.parents:
            continue
        for line, literal in registry_path_literals(
                path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(WFC.parent).as_posix()}:"
                             f"{line}: {literal!r}")
    assert not offenders, (
        "registry paths are Layout's to build; ask wfc.layout instead:\n"
        + "\n".join(offenders)
    )


def test_the_scan_reports_a_synthetic_offender_and_skips_prose():
    source = '''
"""Module prose may name .wfc/registry/methods freely."""
from pathlib import Path


def snapshot(root, module, method):
    """So may a function's docstring: .wfc/registry/methods/<m>."""
    a = Path(root) / ".wfc" / "registry" / "methods" / module
    b = Path(root) / ".wfc/registry/methods"
    c = f"{root}/.wfc/registry/methods/{module}/{method}"
    d = "a docker registry image"
    e = "/api/registry/methods"
    f = cfg["registry"], parsed.get("registry"), {"registry": None}
    g = CheckResult(name="registry", status="ok")
    h = Path(root).joinpath(part="registry")
    return a, b, c, d, e, f, g, h
'''
    hits = registry_path_literals(source)
    assert [(line, literal) for line, literal in hits] == [
        (8, "registry"), (9, ".wfc/registry/methods"),
        (10, "/.wfc/registry/methods/"), (15, "registry"),
    ]
