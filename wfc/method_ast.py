"""Registration-time AST validation of ``ctx.save_artifact`` calls (ADR-020).

Tier-1 only: extends ``register-method``'s static scan to cross-check the
literal output names a ``@wfc.method``-decorated function saves against the
``method.yaml`` ``outputs:`` declarations. Pure static analysis — no code is
executed.

Scope (v1): only the **body** of decorated functions is walked, not helper
functions they call. Saves inside helpers work at runtime but are not
statically validated; users wanting full static validation inline saves into
the main function (also a readability win, recommended in the style guide).

Hard errors (raise ``ValueError`` — fail registration):
  - A required declared output has no matching ``ctx.save_artifact("<name>")``
    literal call in any decorated function body.
  - A literal save name not declared as an output in ``method.yaml``.

Soft warnings (returned, not raised):
  - Dynamic save name (f-string / variable / call) — static check skipped.
  - Save inside obviously-unreachable code (``if False:`` / ``if 0:``).
"""

from __future__ import annotations

import ast
from pathlib import Path


def _is_wfc_method_decorator(node: ast.FunctionDef) -> bool:
    """Return True if the function is decorated with @wfc.method / @method.

    Recognizes ``@method`` (Name), ``@wfc.method`` (Attribute), and any
    attribute decorator whose final attr is ``method``.
    """
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "method":
            return True
        if isinstance(dec, ast.Attribute) and dec.attr == "method":
            return True
    return False


def _is_save_artifact_call(node: ast.Call) -> bool:
    """Return True if the call is ``<something>.save_artifact(...)``."""
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr == "save_artifact"


def _literal_name_arg(node: ast.Call) -> "str | None":
    """Return the first positional arg if it is a string literal, else None."""
    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _is_obviously_unreachable(stack: "list[ast.AST]") -> bool:
    """Return True if any enclosing ``if`` has an obviously-false test.

    Only ``if False:`` and ``if 0:`` (in the body, not the else) count as
    obviously unreachable. ``if some_runtime_condition:`` is reachable.
    """
    for parent, child in zip(stack, stack[1:]):
        if isinstance(parent, ast.If):
            test = parent.test
            is_false = (
                isinstance(test, ast.Constant)
                and test.value in (False, 0)
            )
            if is_false and child in parent.body:
                return True
    return False


def validate_save_artifacts(
    script_path: "Path | str",
    declared_outputs: "list[str]",
    required_outputs: "list[str] | None" = None,
) -> "list[str]":
    """Validate ``ctx.save_artifact`` literal names against declared outputs.

    Args:
        script_path: Path to the method script to scan.
        declared_outputs: All output names declared in ``method.yaml``.
        required_outputs: Output names that are required. Defaults to all
            declared outputs when omitted.

    Returns:
        A list of human-readable warning strings (dynamic names,
        unreachable saves). Empty when there is nothing to warn about.

    Raises:
        ValueError: On a hard error — a literal save of an undeclared name,
            or a required output with no matching literal save.
    """
    script_path = Path(script_path)
    declared = set(declared_outputs)
    required = set(required_outputs if required_outputs is not None else declared_outputs)

    source = script_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(script_path))

    saved_literals: "set[str]" = set()
    warnings: "list[str]" = []
    unknown_literals: "list[str]" = []

    # Only inspect bodies of @wfc.method-decorated functions (body-only v1).
    decorated = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and _is_wfc_method_decorator(n)
    ]

    for func in decorated:
        # Walk with a parent stack so we can detect unreachable `if False:`.
        for call, stack in _iter_calls_with_stack(func):
            if not _is_save_artifact_call(call):
                continue
            name = _literal_name_arg(call)
            if name is None:
                warnings.append(
                    f"{script_path.name}: dynamic ctx.save_artifact(...) name "
                    f"(not a string literal) — static validation skipped for "
                    f"that call; runtime validation still applies."
                )
                continue
            if _is_obviously_unreachable(stack):
                warnings.append(
                    f"{script_path.name}: ctx.save_artifact('{name}', ...) is "
                    f"inside an obviously-unreachable block (if False:/if 0:) "
                    f"— it will never run."
                )
                continue
            if name not in declared:
                unknown_literals.append(name)
            else:
                saved_literals.add(name)

    if unknown_literals:
        raise ValueError(
            f"Method script {script_path.name} calls ctx.save_artifact() with "
            f"output name(s) not declared in method.yaml: {sorted(set(unknown_literals))}. "
            f"Declared outputs: {sorted(declared)}. Add the output to "
            f"method.yaml or fix the save_artifact name."
        )

    missing_required = sorted(required - saved_literals)
    if missing_required:
        raise ValueError(
            f"Method script {script_path.name} never calls "
            f"ctx.save_artifact() for required output(s): {missing_required}. "
            f"Each required output in method.yaml must have a matching "
            f"ctx.save_artifact(\"<name>\", ...) call in the decorated function "
            f"body. (Saves inside helper functions are not statically validated "
            f"in v1 — inline them into the @wfc.method function.)"
        )

    return warnings


def _iter_calls_with_stack(root: ast.AST):
    """Yield ``(Call, ancestor_stack)`` for every Call under ``root``.

    The ancestor stack is the chain of AST nodes from ``root`` down to (and
    including) the Call, enabling reachability checks against enclosing
    ``if`` statements.
    """
    def walk(node, stack):
        stack = stack + [node]
        if isinstance(node, ast.Call):
            yield node, stack
        for child in ast.iter_child_nodes(node):
            yield from walk(child, stack)

    yield from walk(root, [])
