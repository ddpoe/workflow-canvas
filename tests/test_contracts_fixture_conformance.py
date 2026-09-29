"""Contracts unit: every checked-in method.yaml is inside the grammar.

The fixtures under ``tests/fixtures/`` and the demo assets under
``wfc/demo/assets/`` are the declarations the suite registers and runs. This
walk hands each one to the owner — the Contracts parser and the env-name
rule — so a fixture that drifts off the grammar fails here, by name, rather
than as a registration error deep inside whichever test copies it first.
It is the proof that the sweep to bare env names is complete and the
standing guard that keeps it so; it adds no rule of its own.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from axiom_annotations import workflow

from wfc.contracts import is_env_name, parse_method_yaml

REPO = Path(__file__).resolve().parents[1]
FIXTURE_ROOTS = (REPO / "tests" / "fixtures", REPO / "wfc" / "demo" / "assets")
METHOD_YAMLS = sorted(
    path for root in FIXTURE_ROOTS for path in root.rglob("method.yaml")
)
assert METHOD_YAMLS, "the fixture walk found no method.yaml — the roots moved"


def _fixture_id(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


@pytest.mark.parametrize("method_yaml", METHOD_YAMLS, ids=_fixture_id)
@workflow(purpose="Every checked-in method.yaml under tests/fixtures/ and "
                  "wfc/demo/assets/ parses through the Contracts owner and "
                  "declares an env that passes the env-name rule",
          inputs="One checked-in method.yaml",
          outputs="A parsed contract whose env is a bare env name")
def test_checked_in_method_yaml_conforms_to_the_grammar(method_yaml):
    contract = parse_method_yaml(method_yaml.parent)
    assert contract is not None
    assert is_env_name(contract["env"]), (
        f"{_fixture_id(method_yaml)} declares env {contract['env']!r}, "
        f"which is not a bare env name"
    )
