"""A registration plan returns what changed as data, beside the message it prints.

Per changed slot and param, its whole declaration before and after (without
its description, which is not part of a version); the code files changed,
added and removed; the top-level ``script``, ``executor`` and ``gpus``; the
env; and what the version holds. The CLI's sentences are rendered from the
same data. Every plan is made by ``plan_method_registration`` against a real
git project after a real registration.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.routes import init_test_project
from tests.fixtures.routes.registration import write_env_record

REPO = Path(__file__).resolve().parent.parent
FIXTURE_METHOD = REPO / "tests" / "fixtures" / "methods" / "transform"
MODULE, METHOD = "mod", "transform"

V1_YAML = """\
inputs:
  data:
    type: .csv
    required: true
    description: "Input CSV"
outputs:
  output:
    type: .csv
    required: true
params:
  suffix:
    type: str
    required: false
    default: "_transformed"
  drop_me:
    type: int
    default: 3
executor: python
env: fixture-env
"""

V2_YAML = """\
inputs:
  data:
    type: .csv
    required: true
    description: "Input CSV, reworded"
  extra:
    type: .csv
    required: false
outputs:
  output:
    type: .csv
    required: false
params:
  suffix:
    type: str
    required: false
    default: "_t2"
  n:
    type: int
    default: 1
    constraints:
      min: 0
executor: python
env: fixture-env
"""


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def project(git_project, monkeypatch):
    """A ``wfc init`` project with its env committed and the module registered."""
    from wfc.persistence import reset_engine
    from wfc.registration import register_module

    monkeypatch.chdir(git_project)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(git_project))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{git_project / '.wfc' / 'wfc.db'}")
    init_test_project(git_project)
    write_env_record(git_project, "fixture-env")
    _git(git_project, "add", ".wfc/envs.json", ".wfc/captures")
    _git(git_project, "commit", "-q", "-m", "user commits the env")
    reset_engine()
    register_module(name=MODULE, contracts=[])
    yield git_project
    reset_engine()


@pytest.fixture
def source(tmp_path_factory) -> Path:
    dest = tmp_path_factory.mktemp("elsewhere") / METHOD
    shutil.copytree(FIXTURE_METHOD, dest)
    (dest / "method.yaml").write_text(V1_YAML, encoding="utf-8")
    return dest


def test_a_new_method_plan_registers_every_entry_as_added(project, source):
    from wfc.registration import plan_method_registration

    plan = plan_method_registration(source, MODULE)

    assert plan.outcome == "new"
    assert [(s["name"], s["kind"], s["change"]) for s in plan.changes["slots"]] == [
        ("data", "input", "added"), ("output", "output", "added")]
    assert all(s["before"] is None for s in plan.changes["slots"])
    assert {p["name"]: p["after"] for p in plan.changes["params"]} == {
        "drop_me": {"type": "int", "default": 3},
        "suffix": {"type": "str", "required": False, "default": "_transformed"},
    }
    assert plan.changes["env"] == {"before": None, "after": "fixture-env",
                                   "rebuilt": False}
    assert plan.changes["code"]["added"] == ["transform.py"]
    assert plan.changes["run"]["executor"] == [None, "python"]
    assert plan.holds == {"script": "transform.py", "helpers": [], "ignored": []}


def test_a_changed_plan_names_each_change_with_its_declarations(project, source):
    from wfc.registration import plan_method_registration, register_method

    register_method(source, MODULE, assume_yes=True)
    (source / "method.yaml").write_text(V2_YAML, encoding="utf-8")

    plan = plan_method_registration(source, MODULE)

    assert plan.outcome == "changed"
    assert plan.changes["params"] == [
        {"name": "drop_me", "change": "removed",
         "before": {"type": "int", "default": 3}, "after": None},
        {"name": "n", "change": "added", "before": None,
         "after": {"type": "int", "default": 1, "constraints": {"min": 0}}},
        {"name": "suffix", "change": "changed",
         "before": {"type": "str", "required": False, "default": "_transformed"},
         "after": {"type": "str", "required": False, "default": "_t2"}},
    ]
    assert plan.changes["slots"] == [
        {"name": "extra", "kind": "input", "change": "added", "before": None,
         "after": {"type": ".csv", "required": False}},
        {"name": "output", "kind": "output", "change": "changed",
         "before": {"type": ".csv", "required": True},
         "after": {"type": ".csv", "required": False}},
    ]
    # The reworded description of `data` is not a change.
    assert "data" not in {s["name"] for s in plan.changes["slots"]}
    assert plan.changes["code"] == {"changed": [], "added": [], "removed": []}
    assert plan.changes["run"] == {}
    assert plan.changes["env"] == {"before": "fixture-env", "after": "fixture-env",
                                   "rebuilt": False}
    # The CLI's sentence is rendered from the same data.
    assert ("Params/contract (method.yaml) changed since v1: inputs: extra added; "
            "outputs: output changed; params: drop_me removed, n added, suffix changed"
            in plan.message)


def test_a_description_only_edit_plans_no_change(project, source):
    from wfc.registration import plan_method_registration, register_method

    register_method(source, MODULE, assume_yes=True)
    yaml_path = source / "method.yaml"
    yaml_path.write_text(yaml_path.read_text(encoding="utf-8")
                         .replace('"Input CSV"', '"Reworded"'), encoding="utf-8")

    plan = plan_method_registration(source, MODULE)

    assert plan.outcome == "same"
    assert plan.changes["slots"] == [] and plan.changes["params"] == []
