"""
AST scanner for method scripts.

Parses method scripts (``{method_name}.py``) to extract:
  - Public function signatures (name, parameters, type annotations, defaults)
  - The "main" function (heuristic: first function whose first param is 'ctx',
    or named 'run', or the only public function)
  - Whether the script uses RunContext mode (main function takes 'ctx' first arg)

No code is executed — this is pure static analysis via Python's ``ast`` module.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParamInfo:
    """Extracted parameter from a function signature."""
    name: str
    type_annotation: str | None = None
    default_value: str | None = None


@dataclass
class FunctionInfo:
    """Extracted function from a Python script."""
    name: str
    params: list[ParamInfo] = field(default_factory=list)
    docstring: str | None = None
    is_main: bool = False
    uses_run_context: bool = False
    uses_wfc_method: bool = False


@dataclass
class ScriptInfo:
    """Extracted info from a method script."""
    functions: list[FunctionInfo] = field(default_factory=list)
    uses_run_context: bool = False
    uses_wfc_method: bool = False

    @property
    def main_function(self) -> FunctionInfo | None:
        """Return the identified main function, if any."""
        for f in self.functions:
            if f.is_main:
                return f
        return self.functions[0] if self.functions else None


def scan_script(path: Path) -> ScriptInfo:
    """Parse a Python script and extract function signatures.

    Identifies all public functions (not starting with ``_``),
    extracts their parameters (skipping ``self``, ``cls``, and ``ctx``),
    and determines whether the script uses RunContext mode.

    Args:
        path: Path to the Python script to scan.

    Returns:
        A ``ScriptInfo`` with extracted functions and metadata.

    Raises:
        FileNotFoundError: If the script does not exist.
        SyntaxError: If the script has syntax errors.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Script not found: {path}")

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    functions: list[FunctionInfo] = []
    script_uses_ctx = False

    script_uses_wfc_method = False

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name.startswith("_"):
            continue

        func_info = _extract_function(node)
        functions.append(func_info)

        if func_info.uses_run_context:
            script_uses_ctx = True
        if func_info.uses_wfc_method:
            script_uses_wfc_method = True

    # Identify main function
    _identify_main(functions)

    return ScriptInfo(
        functions=functions,
        uses_run_context=script_uses_ctx,
        uses_wfc_method=script_uses_wfc_method,
    )


def _has_decorator(node: ast.FunctionDef, name: str) -> bool:
    """Check if a FunctionDef has a decorator with the given name."""
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == name:
            return True
        if isinstance(dec, ast.Attribute) and dec.attr == name:
            return True
    return False


def _extract_function(node: ast.FunctionDef) -> FunctionInfo:
    """Extract function info from an AST FunctionDef node."""
    params: list[ParamInfo] = []
    uses_ctx = False
    # ADR-020: the Tier-1 decorator is `@wfc.method` / `@method`. The legacy
    # `@wfc_method` name is also recognized for backward AST-detection.
    uses_wfc = (
        _has_decorator(node, "method")
        or _has_decorator(node, "wfc_method")
    )

    args = node.args

    # Build defaults mapping (defaults align to the end of the arg list)
    num_args = len(args.args)
    num_defaults = len(args.defaults)
    defaults_offset = num_args - num_defaults

    for i, arg in enumerate(args.args):
        name = arg.arg

        # Skip self, cls
        if name in ("self", "cls"):
            continue

        # Detect RunContext pattern (first non-self param is 'ctx')
        if i == 0 and name == "ctx":
            uses_ctx = True
            continue  # Don't include ctx in user-facing params

        # @wfc_method pattern: skip 'inputs' (first param) and 'params' (second)
        if uses_wfc and name in ("inputs", "params"):
            continue

        # Extract type annotation
        type_ann = None
        if arg.annotation:
            type_ann = _annotation_to_str(arg.annotation)

        # Extract default value
        default = None
        default_idx = i - defaults_offset
        if default_idx >= 0 and default_idx < num_defaults:
            default = _default_to_str(args.defaults[default_idx])

        params.append(ParamInfo(
            name=name,
            type_annotation=type_ann,
            default_value=default,
        ))

    # Extract docstring
    docstring = ast.get_docstring(node)

    return FunctionInfo(
        name=node.name,
        params=params,
        docstring=docstring,
        uses_run_context=uses_ctx,
        uses_wfc_method=uses_wfc,
    )


def _identify_main(functions: list[FunctionInfo]) -> None:
    """Mark the main function in a list of extracted functions.

    Heuristic priority:
    1. First function with @wfc_method decorator
    2. First function whose first param is 'ctx' (RunContext mode)
    3. Function named 'run'
    4. Function named 'main'
    5. The only public function (if there's exactly one)
    """
    # 1. @wfc_method decorated
    for f in functions:
        if f.uses_wfc_method:
            f.is_main = True
            return

    # 2. ctx-based
    for f in functions:
        if f.uses_run_context:
            f.is_main = True
            return

    # 2. Named 'run'
    for f in functions:
        if f.name == "run":
            f.is_main = True
            return

    # 3. Named 'main'
    for f in functions:
        if f.name == "main":
            f.is_main = True
            return

    # 4. Only public function
    if len(functions) == 1:
        functions[0].is_main = True


def _annotation_to_str(node: ast.expr) -> str:
    """Convert an AST annotation node to a string representation."""
    if isinstance(node, ast.Constant):
        return repr(node.value)
    elif isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        value = _annotation_to_str(node.value)
        return f"{value}.{node.attr}"
    elif isinstance(node, ast.Subscript):
        value = _annotation_to_str(node.value)
        slice_str = _annotation_to_str(node.slice)
        return f"{value}[{slice_str}]"
    elif isinstance(node, ast.Tuple):
        elts = ", ".join(_annotation_to_str(e) for e in node.elts)
        return elts
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _annotation_to_str(node.left)
        right = _annotation_to_str(node.right)
        return f"{left} | {right}"
    else:
        return ast.dump(node)


def _default_to_str(node: ast.expr) -> str:
    """Convert an AST default value node to a string representation."""
    if isinstance(node, ast.Constant):
        return repr(node.value)
    elif isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.List):
        elts = ", ".join(_default_to_str(e) for e in node.elts)
        return f"[{elts}]"
    elif isinstance(node, ast.Dict):
        items = []
        for k, v in zip(node.keys, node.values):
            if k is not None:
                items.append(f"{_default_to_str(k)}: {_default_to_str(v)}")
            else:
                items.append(f"**{_default_to_str(v)}")
        return "{" + ", ".join(items) + "}"
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return f"-{_default_to_str(node.operand)}"
    elif isinstance(node, ast.Attribute):
        value = _default_to_str(node.value)
        return f"{value}.{node.attr}"
    else:
        return ast.dump(node)
