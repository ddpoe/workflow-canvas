"""Behavior tests for the witness walk (tools/witness_walk.py).

The walk derives a catalog row's ``- Stub disclosure:`` bullet from the
witness's source: the fixtures pytest would inject, the ``conftest.py`` chain,
and the helpers the bodies call, to a depth counted in files. These tests
drive it over synthetic package trees in ``tmp_path`` (the
``tests/test_check_catalog_coverage.py`` precedent), so what they pin is the
walk's contract -- which injection paths reach it, where it stops, and the
tokens -- never a site in the real suite.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from axiom_annotations import workflow

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "witness_walk", REPO_ROOT / "tools" / "witness_walk.py"
)
witness_walk = importlib.util.module_from_spec(_spec)
sys.modules["witness_walk"] = witness_walk
_spec.loader.exec_module(witness_walk)


def _write(root: Path, rel: str, source: str) -> None:
    """Write one source file into a synthetic tree."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


_READINESS = '''
from ._entry import fake


@fake(boundary="wfc.execution.readiness.check_git", preserves="the shape",
      not_proven="the real gate", backed_by="owed: nothing drives it")
def stub_probe():
    pass


@fake(boundary="b", preserves="p", not_proven="n", backed_by="undrivable: r")
def stub_second():
    pass
'''

_BAD = '''
from ._entry import fake

WHO = "x"


@fake(boundary=f"a {WHO}", preserves="p", not_proven="n", backed_by="owed: r")
def stub_computed():
    pass
'''

_TEST_MODULE = '''
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.usefixtures("marked_fixture")


@pytest.fixture(autouse=True)
def auto_fixture():
    with patch("wfc.a.b"):
        yield


@pytest.fixture
def marked_fixture():
    from tests.harness import run_it
    run_it()


def test_one(deep_fixture):
    assert True
'''


def _tree(root: Path, *, entry_depth: int = 3) -> None:
    """A test two directories below ``tests/`` whose fixtures fan out.

    The parameter fixture lives in ``tests/conftest.py`` (two levels up) and
    calls a routes builder, reached through the package's re-export, that
    references a registry entry. The module's autouse fixture carries a raw
    ``patch``. The ``usefixtures`` mark names a fixture that calls into the
    harness; the harness chain reaches a third file that carries a raw
    ``setattr`` on a production module and, at ``entry_depth`` 3, references
    a second entry -- or, at 4, calls a fourth file that does.

    Args:
        root: The synthetic repository root.
        entry_depth: The file depth of the second entry's reference.
    """
    _write(root, "tests/__init__.py", "")
    _write(root, "tests/conftest.py",
           "import pytest\nfrom tests.fixtures.routes import build\n\n\n"
           "@pytest.fixture\ndef deep_fixture():\n    build()\n")
    _write(root, "tests/fixtures/__init__.py", "")
    _write(root, "tests/fixtures/routes/__init__.py", "from .roots import build\n")
    _write(root, "tests/fixtures/routes/roots.py",
           'def build():\n    """Build.\n\n    Pins: the archive location.\n'
           '    Does not prove: init\'s summary.\n    """\n'
           "    from tests.fixtures.fakes import stub_probe\n    stub_probe()\n")
    _write(root, "tests/fixtures/fakes/__init__.py",
           "from .readiness import stub_probe, stub_second\n")
    _write(root, "tests/fixtures/fakes/_entry.py", "def fake(**kw):\n    return lambda f: f\n")
    _write(root, "tests/fixtures/fakes/readiness.py", _READINESS)
    _write(root, "tests/fixtures/fakes/bad.py", _BAD)
    _write(root, "tests/harness/__init__.py", "from .drivers import run_it\n")
    _write(root, "tests/harness/drivers.py",
           "from tests.harness.deep import helper_three\n\n\n"
           "def run_it():\n    _inner()\n\n\ndef _inner():\n    helper_three()\n")
    _write(root, "tests/harness/deep.py",
           "from tests.harness.deeper import deepest\n\n\ndef helper_three():\n    deepest()\n")
    if entry_depth == 3:
        _write(root, "tests/harness/deeper.py",
               "from wfc.execution import readiness\n"
               "from tests.fixtures.fakes import stub_second\n\n\n"
               "def deepest(mp):\n    mp.setattr(readiness, \"check_dvc\", None)\n"
               "    stub_second()\n")
    else:
        _write(root, "tests/harness/deeper.py",
               "from wfc.execution import readiness\n"
               "from tests.harness.fourth import fourth\n\n\n"
               "def deepest(mp):\n    mp.setattr(readiness, \"check_dvc\", None)\n"
               "    fourth()\n")
        _write(root, "tests/harness/fourth.py",
               "from tests.fixtures.fakes import stub_second\n\n\n"
               "def fourth():\n    stub_second()\n")
    _write(root, "tests/unit/sub/test_walk.py", _TEST_MODULE)
    _write(root, "tests/test_plain.py", "def test_nothing():\n    assert True\n")
    _write(root, "canvas/static/src/lib/__tests__/x.test.ts", "// vitest\n")


@workflow(purpose="A witness's parameter fixture, module autouse fixture and "
                  "usefixtures mark all reach the walk; the entry reached through "
                  "a conftest fixture two levels up and a re-exported routes "
                  "builder, the raw patch in the autouse fixture, and the raw "
                  "setattr in a depth-three helper are all named in the bullet")
def test_every_injection_path_reaches_the_walk(tmp_path):
    _tree(tmp_path)

    text = witness_walk.derive_disclosure(
        tmp_path, [("tests/unit/sub/test_walk.py", "test_one")])

    assert text == ("fakes.stub_probe; fakes.stub_second; raw patch wfc.a.b; "
                    "raw patch wfc.execution.readiness.check_dvc")


@workflow(purpose="Depth is counted in files: a registry reference three files "
                  "from the witness is named, the same reference moved to a "
                  "fourth file is not, and the walk reports the function it "
                  "stopped at rather than guessing what it holds")
def test_the_depth_rule_names_at_three_and_stops_at_four(tmp_path):
    near, far = tmp_path / "near", tmp_path / "far"
    _tree(near, entry_depth=3)
    _tree(far, entry_depth=4)

    at_three = witness_walk.walk_witness(near, "tests/unit/sub/test_walk.py", "test_one")
    at_four = witness_walk.walk_witness(far, "tests/unit/sub/test_walk.py", "test_one")

    assert "stub_second" in at_three.entries
    assert at_three.visited["tests/harness/deeper.py::deepest"] == 3
    assert at_three.stopped == set()
    assert "stub_second" not in at_four.entries
    assert at_four.stopped == {"tests/harness/fourth.py::fourth"}
    # The raw site at depth three is still reached either way.
    assert "raw patch wfc.execution.readiness.check_dvc" in at_four.raw


@workflow(purpose="The tokens: Python witnesses that reach nothing say none, a "
                  "row witnessed only by Vitest is not walked, a mixed row lists "
                  "its Python findings and appends the Vitest note, and a row "
                  "with no resolvable witness says so")
def test_the_tokens(tmp_path):
    _tree(tmp_path)
    vitest = "canvas/static/src/lib/__tests__/x.test.ts"
    plain = ("tests/test_plain.py", "test_nothing")

    assert witness_walk.derive_disclosure(tmp_path, [plain]) == "none"
    assert witness_walk.derive_disclosure(tmp_path, [(vitest, None)]) == "not walked (vitest)"
    assert witness_walk.derive_disclosure(tmp_path, [plain, (vitest, None)]) == (
        "none; vitest witness not walked")
    assert witness_walk.derive_disclosure(
        tmp_path, [("tests/unit/sub/test_walk.py", "test_one"), (vitest, None)]
    ).endswith("; vitest witness not walked")
    assert witness_walk.derive_disclosure(tmp_path, []) == "no witness"


def test_a_module_level_witness_walks_every_test_in_the_module(tmp_path):
    _tree(tmp_path)
    whole = witness_walk.walk_witness(tmp_path, "tests/unit/sub/test_walk.py", None)
    one = witness_walk.walk_witness(tmp_path, "tests/unit/sub/test_walk.py", "test_one")
    assert whole.items() == one.items()
    missing = witness_walk.walk_witness(tmp_path, "tests/unit/sub/test_walk.py", "test_gone")
    assert missing.problems == ["tests/unit/sub/test_walk.py: no function 'test_gone'"]


def test_the_harvest_reads_decorator_literals_and_refuses_a_computed_field(tmp_path):
    _tree(tmp_path)
    registry = witness_walk.harvest_registry(tmp_path)

    assert sorted(registry.entries) == ["stub_probe", "stub_second"]
    probe = registry.entries["stub_probe"]
    assert (probe.kind, probe.rel) == ("fake", "tests/fixtures/fakes/readiness.py")
    assert probe.boundary == "wfc.execution.readiness.check_git"
    assert probe.backed_by == "owed: nothing drives it"
    assert registry.problems == [
        "tests/fixtures/fakes/bad.py:8 stub_computed: 'boundary' is not a string literal"
    ]
    [builder] = registry.builders
    assert (builder.name, builder.pins, builder.not_proven) == (
        "build", "the archive location.", "init's summary.")


@pytest.mark.parametrize("content, expected", [
    # Replace an existing bullet in place, its continuation lines included.
    ("- Given: g.\n- Test: t.\n- Stub disclosure: old prose\n  spanning a line.\n- Falsifier: f.",
     "- Given: g.\n- Test: t.\n- Stub disclosure: none\n- Falsifier: f."),
    # Insert after the Test bullet's block.
    ("- Given: g.\n- Test: t,\n  continued.\n- Falsifier: f.",
     "- Given: g.\n- Test: t,\n  continued.\n- Stub disclosure: none\n- Falsifier: f."),
    # Append when the row has no Test bullet.
    ("- Given: g.\n- Expect: e.", "- Given: g.\n- Expect: e.\n- Stub disclosure: none"),
])
def test_apply_disclosure_touches_only_the_bullet(content, expected):
    assert witness_walk.apply_disclosure(content, "none") == expected
    # Idempotent: applying the same text again changes nothing.
    assert witness_walk.apply_disclosure(expected, "none") == expected
