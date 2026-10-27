"""A method is named by its module and its name together, never its name alone.

Covers:
  - Tier 1: a static scan of every module under ``wfc/`` for a query that
    filters ``Method`` rows on their name without filtering on their module
    in the same statement: a ``Method.name`` comparison or predicate
    (``==``, ``in_``, ``like`` ...), a ``filter_by(name=...)`` on a
    ``select(Method)``, or a raw SQL literal selecting from ``method`` by
    ``name``. A module filter is a comparison of ``Method.module_id``,
    ``Module.name`` or ``Module.id`` against a value; a join's ON clause
    compares two columns and qualifies nothing. Zero hits, no allowlist.
  - Tier 1: the scan's self-test: planted offenders of each shape are
    reported, and module-qualified queries, an ``order_by`` and docstring
    prose are not.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

WFC = Path(__file__).resolve().parents[1] / "wfc"

#: Column-operator methods that turn ``Method.name`` into a filter.
_PREDICATES = frozenset({
    "in_", "not_in", "notin_", "like", "ilike", "startswith", "endswith",
    "contains", "is_", "op", "regexp_match", "__eq__",
})

#: A raw SQL literal selecting ``method`` rows by name.
_RAW_SQL = re.compile(r"\bfrom\s+method\b.*\bwhere\b.*\bname\s*(=|in\b|like\b)",
                      re.IGNORECASE | re.DOTALL)


def _column(node: ast.AST) -> tuple[str, str] | None:
    """Return ``(class, attr)`` for ``Cls.attr`` or ``col(Cls.attr)``."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "col" and len(node.args) == 1):
        node = node.args[0]
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id, node.attr
    return None


def _is_method_name(node: ast.AST) -> bool:
    return _column(node) == ("Method", "name")


def _is_module_column(node: ast.AST) -> bool:
    return _column(node) in {("Method", "module_id"), ("Module", "name"),
                             ("Module", "id")}


def _own_nodes(stmt: ast.stmt):
    """Yield the nodes of ``stmt`` that belong to it and no nested statement."""
    stack = list(ast.iter_child_nodes(stmt))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.stmt):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


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


def _name_filters(nodes: list[ast.AST]) -> list[int]:
    """Return the lines of the name-only filter shapes among ``nodes``."""
    selects_method = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "select" and n.args
        and isinstance(n.args[0], ast.Name) and n.args[0].id == "Method"
        for n in nodes)
    lines = []
    for n in nodes:
        if isinstance(n, ast.Compare) and any(
                _is_method_name(side) for side in [n.left, *n.comparators]):
            lines.append(n.lineno)
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in _PREDICATES and _is_method_name(n.func.value)):
            lines.append(n.lineno)
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "filter_by" and selects_method
                and any(k.arg == "name" for k in n.keywords)):
            lines.append(n.lineno)
    return lines


def _module_filtered(nodes: list[ast.AST]) -> bool:
    """Whether ``nodes`` compare a module column against a value."""
    for n in nodes:
        if isinstance(n, ast.Compare):
            sides = [n.left, *n.comparators]
            module_sides = [s for s in sides if _is_module_column(s)]
            if module_sides and len(module_sides) < len(sides):
                return True
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "filter_by"
                and any(k.arg == "module_id" for k in n.keywords)):
            return True
    return False


def name_only_method_queries(source: str) -> list[int]:
    """Return the lines of queries selecting a ``Method`` by name alone.

    Args:
        source: Python source text.

    Returns:
        The sorted line numbers of each offending filter or SQL literal.
    """
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    hits: set[int] = set()
    for stmt in ast.walk(tree):
        if not isinstance(stmt, ast.stmt):
            continue
        nodes = list(_own_nodes(stmt))
        filters = _name_filters(nodes)
        if filters and not _module_filtered(nodes):
            hits.update(filters)
        for n in nodes:
            if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and id(n) not in docstrings and _RAW_SQL.search(n.value)
                    and "module_id" not in n.value):
                hits.add(n.lineno)
    return sorted(hits)


def test_no_query_in_wfc_selects_a_method_by_name_alone():
    offenders = []
    for path in sorted(WFC.rglob("*.py")):
        for line in name_only_method_queries(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(WFC.parent).as_posix()}:{line}")
    assert not offenders, (
        "a Method is named by (module, name); filter on its module too:\n"
        + "\n".join(offenders)
    )


def test_the_scan_reports_planted_offenders_and_skips_qualified_queries():
    source = '''
"""Prose may say select(Method).where(Method.name == n) freely."""
from sqlmodel import col, select


def lookups(session, n, names, mod):
    a = select(Method).where(Method.name == n)
    b = select(Method).where(col(Method.name).in_(names))
    c = select(Method).filter_by(name=n)
    d = (select(Method)
         .join(Module, col(Method.module_id) == col(Module.id))
         .where(Method.name == n))
    e = "SELECT id FROM method WHERE name = ?"
    for m in session.exec(select(Method).where(Method.name == n)):
        keep = Module.name == "x"
    ok1 = select(Method).where(Method.name == n, Method.module_id == mod.id)
    ok2 = select(Method).join(Module).where(Module.name == mod, Method.name == n)
    ok3 = select(Method).order_by(Method.name)
    ok4 = "SELECT id FROM method WHERE module_id = ? AND name = ?"
    return a, b, c, d, e, keep, ok1, ok2, ok3, ok4
'''
    assert name_only_method_queries(source) == [7, 8, 9, 12, 13, 14]
