"""Registration refuses a method.yaml whose declared default breaks its own rules.

A declared default runs whenever a node leaves its parameter out, so one that
fails its own declared type, ``enum`` or ``min``/``max`` is refused when the
method is registered, naming the parameter, and nothing is registered.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from axiom_annotations import workflow
from sqlmodel import select

from wfc.persistence import Method, get_session

from tests.fixtures.routes import register_test_method


def _write_method(project_root: Path, params_block: str) -> Path:
    """Write method ``m``'s script and a method.yaml with the given params block."""
    method_dir = project_root / "methods" / "m"
    method_dir.mkdir(parents=True, exist_ok=True)
    (method_dir / "method.yaml").write_text(
        "inputs:\n"
        "  data:\n"
        "    type: .csv\n"
        "    required: true\n"
        "outputs:\n"
        "  out:\n"
        "    type: .csv\n"
        f"params:\n{params_block}"
        "executor: python\n"
        "env: fixture-env\n",
        encoding="utf-8",
    )
    (method_dir / "m.py").write_text("def main():\n    pass\n", encoding="utf-8")
    return method_dir


@pytest.mark.parametrize("params_block, reason", [
    ("  count:\n    type: int\n    default: three\n",
     "expects int, got \"three\" (str)"),
    ("  mode:\n    type: str\n    default: slow\n"
     "    constraints:\n      enum: [fast, medium]\n",
     "must be one of \"fast\", \"medium\", got \"slow\""),
    ("  ratio:\n    type: float\n    default: 2.5\n"
     "    constraints:\n      min: 0\n      max: 1\n",
     "must be <= 1, got 2.5"),
], ids=["type", "enum", "max"])
@workflow(purpose="Registering a method.yaml whose declared default fails its "
                  "own type, enum or min/max is refused naming the parameter, "
                  "and no method is registered")
def test_a_default_that_breaks_its_own_rules_is_refused_at_registration(
        tmp_project, params_block, reason):
    method_dir = _write_method(tmp_project, params_block)
    with pytest.raises(ValueError) as exc:
        register_test_method(tmp_project, module_name="data_preprocessing",
                             method_dir=method_dir)
    name = params_block.split(":", 1)[0].strip()
    assert f"parameter '{name}' declares a default that {reason}" in str(exc.value)
    with get_session() as session:
        assert session.exec(select(Method).where(Method.name == "m")).first() is None


def test_valid_and_null_defaults_register(tmp_project):
    """A default that passes its rules, a null default and a required ``""`` register."""
    method_dir = _write_method(
        tmp_project,
        "  count:\n    type: int\n    default: 3.0\n"
        "    constraints:\n      min: 1\n"
        "  seed:\n    type: int\n    default:\n"
        "  label:\n    type: str\n    required: true\n    default: ''\n",
    )
    register_test_method(tmp_project, module_name="data_preprocessing",
                         method_dir=method_dir)
    with get_session() as session:
        assert session.exec(select(Method).where(Method.name == "m")).first() is not None
