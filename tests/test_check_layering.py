"""Behavior tests for the R4 layering gate (tools/check_layering.py)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "check_layering", REPO_ROOT / "tools" / "check_layering.py"
)
check_layering = importlib.util.module_from_spec(_spec)
sys.modules["check_layering"] = check_layering
_spec.loader.exec_module(check_layering)


def _write(root: Path, rel: str, source: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_current_tree_passes():
    report = check_layering.check_tree(REPO_ROOT)
    assert report.ratchet_errors == []
    assert report.violations == []
    # No path-literal site is grandfathered: the grandfather list is empty
    # and the ratchet holds it there.
    assert report.grandfathered_sites == []


def test_new_path_literal_fails_unless_grandfathered_or_in_layout(tmp_path):
    _write(tmp_path, "wfc/newmod.py", 'RUNS_DIR = ".runs"\n')
    _write(tmp_path, "wfc/layout/paths.py", 'RUNS_DIR = ".runs"\n')

    report = check_layering.check_tree(
        tmp_path, grandfathered=frozenset(), max_grandfathered=0
    )
    assert [(v.rel_path, v.fragment) for v in report.violations] == [
        ("wfc/newmod.py", ".runs")
    ]

    grandfathered = check_layering.check_tree(
        tmp_path, grandfathered=frozenset({"wfc/newmod.py"}), max_grandfathered=1
    )
    assert grandfathered.violations == []
    assert [s.rel_path for s in grandfathered.grandfathered_sites] == ["wfc/newmod.py"]


def test_prose_and_non_path_literals_do_not_match(tmp_path):
    _write(
        tmp_path,
        "wfc/prose.py",
        '"""Docstring mentioning .runs and data/samples."""\n'
        "import argparse\n"
        "\n"
        "\n"
        "def build(parser, p):\n"
        '    parser.add_argument("--x", help="stored under .wfc/archives")\n'
        "    if not p.exists():\n"
        '        raise ValueError(f"no .wfc directory at {p}")\n'
        '    print("cleaning .runs now")\n'
        '    payload = {"methods": [], "samples": 3}\n'
        '    table = "samples"\n'
        "    return payload, table\n",
    )
    report = check_layering.check_tree(
        tmp_path, grandfathered=frozenset(), max_grandfathered=0
    )
    assert report.violations == []


def test_ratchet_teeth(tmp_path):
    _write(tmp_path, "wfc/mod.py", 'X = ".runs"\n')

    missing = check_layering.check_tree(
        tmp_path,
        grandfathered=frozenset({"wfc/mod.py", "wfc/gone.py"}),
        max_grandfathered=2,
    )
    assert any("wfc/gone.py" in err for err in missing.ratchet_errors)

    over_max = check_layering.check_tree(
        tmp_path, grandfathered=frozenset({"wfc/mod.py"}), max_grandfathered=0
    )
    assert any("maximum" in err for err in over_max.ratchet_errors)


def test_marker_documentation_kwargs_are_prose_only_on_marker_calls(tmp_path):
    # inputs/outputs/critical document an axiom-annotations marker (Step,
    # AutoStep, @workflow, @task); the same kwarg on any other callee is code.
    _write(
        tmp_path,
        "wfc/markers.py",
        "from axiom_annotations import Step, task\n"
        "\n"
        "\n"
        '@task(purpose="Freeze the document", outputs=".runs/pipelines/<pid>/pipeline.json")\n'
        "def freeze(project):\n"
        '    marker = Step(step_num=1, name="Write", outputs=".runs/pipelines/<pid>/pipeline.json")\n'
        '    return render(project, outputs=".runs/pipelines/<pid>/pipeline.json")\n',
    )
    report = check_layering.check_tree(
        tmp_path, grandfathered=frozenset(), max_grandfathered=0
    )
    assert [(v.rel_path, v.line, v.fragment) for v in report.violations] == [
        ("wfc/markers.py", 7, ".runs")
    ]


# -----------------------------------------------------------------------------
# R1 -- Tier 0 purity
# -----------------------------------------------------------------------------

def test_r1_current_tree_is_pure():
    report = check_layering.check_tree(REPO_ROOT)
    assert report.purity_violations == []


def test_r1_tier0_package_may_not_reach_outside_even_deferred_or_relative(tmp_path, monkeypatch):
    # A module-level database import, the same import deferred inside a
    # function body, a two-dot relative import that resolves to wfc.persistence,
    # and a third-party module off the allow-list all fail; the gate exits 1.
    _write(tmp_path, "wfc/identity/__init__.py", "")
    _write(tmp_path, "wfc/identity/a.py", "from wfc.persistence import get_session\n")
    _write(
        tmp_path,
        "wfc/identity/b.py",
        "def fetch(run_id):\n"
        "    from wfc.persistence import get_session\n"
        "    return get_session\n",
    )
    _write(tmp_path, "wfc/graph/__init__.py", "")
    _write(tmp_path, "wfc/graph/load.py", "from ..persistence import get_session\nimport docker\n")

    report = check_layering.check_tree(
        tmp_path, grandfathered=frozenset(), max_grandfathered=0
    )
    assert [(v.rel_path, v.line, v.snippet) for v in report.purity_violations] == [
        ("wfc/graph/load.py", 1, "wfc.persistence.get_session"),
        ("wfc/graph/load.py", 2, "docker"),
        ("wfc/identity/a.py", 1, "wfc.persistence.get_session"),
        ("wfc/identity/b.py", 2, "wfc.persistence.get_session"),
    ]
    monkeypatch.setattr(check_layering, "REPO_ROOT", tmp_path)
    assert check_layering.main([]) == 1


def test_r1_permits_stdlib_tier0_siblings_and_the_allow_list(tmp_path, monkeypatch):
    # The standard library (module level and deferred), another Tier 0
    # package by absolute or one-dot relative import, and the two allow-listed
    # third parties are all clean; the gate exits 0.  (R3's permitted-surface
    # ratchet wants wfc/cli.py to exist in any tree the gate runs over.)
    _write(tmp_path, "wfc/cli.py", "")
    _write(tmp_path, "wfc/graph/__init__.py", "from .model import PipelineDef\n")
    _write(
        tmp_path,
        "wfc/graph/model.py",
        "from __future__ import annotations\n"
        "import hashlib\n"
        "from pathlib import Path\n"
        "import yaml\n"
        "from axiom_annotations import task\n"
        "from wfc.contracts import parse_env_spec\n"
        "from wfc import layout\n"
        "from . import order\n"
        "from .expansion import variant_axis\n"
        "\n"
        "\n"
        "def load(doc):\n"
        "    import json\n"
        "    return json.loads(doc)\n",
    )
    report = check_layering.check_tree(
        tmp_path, grandfathered=frozenset(), max_grandfathered=0
    )
    assert report.purity_violations == []
    monkeypatch.setattr(check_layering, "REPO_ROOT", tmp_path)
    assert check_layering.main([]) == 0


# -----------------------------------------------------------------------------
# R3 -- the engine's words
# -----------------------------------------------------------------------------

def _r3(report):
    return [(v.rel_path, v.line, v.fragment) for v in report.engine_violations]


def test_r3_current_tree_is_clean():
    report = check_layering.check_tree(REPO_ROOT)
    assert report.engine_violations == []
    assert report.ratchet_errors == []


def test_r3_engine_words_are_code_outside_the_package_and_prose_anywhere(tmp_path):
    # Code constants carrying engine words fail outside the package,
    # whatever the case; the same words in a docstring, a help=, a raised
    # message and a marker's purpose= pass; a marker's name= is code; the
    # package's own file is exempt wholesale.
    _write(
        tmp_path,
        "wfc/execution/engine.py",
        "from axiom_annotations import Step\n"
        'ENGINE = "snakemake"\n'
        'ARGV = ["--cores", "4"]\n'
        'HANDLER = "onError"\n'
        'marker = Step(step_num=1, name="Invoke Snakemake", purpose="Run rule all")\n',
    )
    _write(
        tmp_path,
        "wfc/execution/prose.py",
        '"""Snakemake is the engine; its onsuccess handler runs the summary."""\n'
        "from axiom_annotations import Step\n"
        "\n"
        "\n"
        "def build(parser, log_dir):\n"
        '    parser.add_argument("--jobs", help="forwarded as snakemake --cores")\n'
        '    marker = Step(step_num=1, name="Launch", purpose="spawn with --keep-going")\n'
        '    raise RuntimeError(f"Snakemake pipeline failed. See logs in {log_dir}")\n',
    )
    _write(tmp_path, "wfc/orchestration/snakemake.py", 'RULE = "rule all:"\nENGINE = "snakemake"\n')
    report = check_layering.check_tree(
        tmp_path, grandfathered=frozenset(), max_grandfathered=0, engine_permitted={}
    )
    assert _r3(report) == [
        ("wfc/execution/engine.py", 2, "snakemake"),
        ("wfc/execution/engine.py", 3, "--cores"),
        ("wfc/execution/engine.py", 4, "onerror"),
        ("wfc/execution/engine.py", 5, "snakemake"),
    ]


def test_r3_permitted_surface_is_the_three_flags_in_cli_only(tmp_path):
    # The three flag literals pass in wfc/cli.py and fail in any other
    # file; any other engine word in wfc/cli.py fails; a permitted-surface
    # file that does not exist is a ratchet error.
    flags = (
        "import argparse\n"
        "p = argparse.ArgumentParser()\n"
        'p.add_argument("--cores", type=int, help="Snakemake cores")\n'
        'p.add_argument("--snakefile", default=None)\n'
        'p.add_argument("--keep-going", dest="keep_going")\n'
    )
    _write(tmp_path, "wfc/cli.py", flags + 'ENGINE = "snakemake"\n')
    _write(tmp_path, "wfc/canvas/server.py", flags)
    report = check_layering.check_tree(
        tmp_path, grandfathered=frozenset(), max_grandfathered=0
    )
    assert report.ratchet_errors == []
    assert _r3(report) == [
        ("wfc/canvas/server.py", 3, "--cores"),
        ("wfc/canvas/server.py", 4, "--snakefile"),
        ("wfc/canvas/server.py", 5, "--keep-going"),
        ("wfc/cli.py", 6, "snakemake"),
    ]

    _write(tmp_path / "moved", "wfc/verbs.py", flags)
    missing = check_layering.check_tree(
        tmp_path / "moved", grandfathered=frozenset(), max_grandfathered=0
    )
    assert any("wfc/cli.py" in err for err in missing.ratchet_errors)


def test_r3_orchestration_is_importable_only_from_execution(tmp_path, monkeypatch):
    # Module-level and relative imports of the package from
    # wfc/execution/ pass; a deferred one from the canvas and a relative one
    # from wfc/cli.py fail; the gate exits 1.
    _write(
        tmp_path,
        "wfc/execution/pipeline.py",
        "from ..orchestration.engine import invoke_engine\n"
        "from wfc.orchestration import generate_snakefile\n",
    )
    _write(
        tmp_path,
        "wfc/canvas/server.py",
        "def run():\n"
        "    from wfc.orchestration import generate_snakefile\n"
        "    return generate_snakefile\n",
    )
    _write(tmp_path, "wfc/cli.py", "from .orchestration import invoke_engine\n")
    report = check_layering.check_tree(
        tmp_path, grandfathered=frozenset(), max_grandfathered=0
    )
    assert [(v.rel_path, v.line, v.snippet) for v in report.engine_violations] == [
        ("wfc/canvas/server.py", 2, "wfc.orchestration.generate_snakefile"),
        ("wfc/cli.py", 1, "wfc.orchestration.invoke_engine"),
    ]
    monkeypatch.setattr(check_layering, "REPO_ROOT", tmp_path)
    assert check_layering.main([]) == 1
