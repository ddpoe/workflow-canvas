"""
Single-selector fan-in tests.

Validates that a pipeline with a fan_mode="in" Input Selector:
  - Sets StepDef.sample_collapsed=True and collapsed_samples on the
    direct consumer and every downstream step (contagious collapse).
  - _output_path emits the literal "__all__" as the sample segment.
  - _input_path on the collapsed consumer emits per-sample restore sentinels.
  - expand_step_combos emits exactly one row per variant with sample="__all__".
  - /api/workflow/validate rejects unsupported shapes (multi-upstream fan-in,
    empty-sample-list fan-in).
  - End-to-end: a Snakefile generated from a fan-in pipeline names "__all__"
    in every downstream rule and in the rule-all expand target.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from axiom_annotations import workflow, Step

from wfc.canvas.models import PipelineInput, PipelineNode, PipelineLink
from wfc.canvas.submission import _enrich_pipeline
from wfc.graph import (
    StepDef, PipelineDef, load_pipeline, expand_step_combos, validate_structure,
)
from wfc.execution import load_pipeline_from_path
from wfc.orchestration.snakemake import generate_snakefile, _output_path, _input_path

from tests.conftest import requires_docker
from tests.fixtures.conftest import FIXTURE_ENV_NAME
from tests.fixtures.fakes import spy_directory_listings
from tests.harness import (
    Behavior,
    Scenario,
    build_project,
    node,
    run_target,
    selector,
    wire,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: The literal contract map the structural core reads for this module's
#: shapes: csv_merge registered, no slot declarations.
CSV_TOOLS_CONTRACTS = {"csv_tools.csv_merge": {"input_slots": {}, "output_slots": {}}}


def _load(pipeline_json: dict):
    """The Graph load on literal values: no contract map, no reference outputs."""
    return load_pipeline(pipeline_json, contract_map={}, reference_outputs={})

#: The bundled target every collapsed fan-in in this module expands to.
BUNDLE = ("merge", "__all__", "default")

#: Three data rows per sample, so a merge of three samples is nine rows and
#: a fan-in that delivered only its first member is distinguishable.
THREE_ROW_SAMPLE_CSV = "id,value\n0,0\n1,10\n2,20\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_pipeline(tmp_path: Path, pipeline_json: dict) -> Path:
    p = tmp_path / "pipeline.json"
    p.write_text(json.dumps(pipeline_json))
    return p


def _fan_in_scenario(**kwargs) -> Scenario:
    """A fan-in selector over three samples feeding one method's `sources` slot.

    The `.sample_ready` sentinel the DVC restore rule leaves in each sample
    directory is declared too: the runtime resolver walks the directory and
    skips dotfiles, and with no dotfile present there is nothing to skip.

    Args:
        **kwargs: Any :class:`~tests.harness.Scenario` field to override.

    Returns:
        The scenario.
    """
    return Scenario(
        nodes=[
            selector(fan_mode="in"),
            node("merge", inputs=[wire("sel", target_slot="sources")]),
        ],
        samples=["s1", "s2", "s3"],
        sample_ready_sentinel=True,
        **kwargs,
    )


def _minimal_fan_in_pipeline(samples: list[str]) -> dict:
    """Pipeline: selector(fan_mode=in, samples) -> merge."""
    return {
        "nodes": [
            {"id": "sel-1", "type": "input_selector",
             "params": {}, "samples": samples,
             "fan_mode": "in"},
            {"id": "merge-1", "type": "method",
             "method": "csv_merge", "module": "csv_tools",
             "script": "modules/_builtin/csv_merge/csv_merge.py",
             "params": {}, "env": "container:demo"},
        ],
        "links": [
            {"source": "sel-1", "target": "merge-1", "target_slot": "sources"},
        ],
        "samples": [],
    }


# ---------------------------------------------------------------------------
# plumbing: fan_mode survives Pydantic round-trip + enrichment
# ---------------------------------------------------------------------------


@workflow(purpose="fan_mode survives Pydantic round-trip through PipelineInput and _enrich_pipeline emits it in the input_selector node_dict")
def test_fan_mode_roundtrip_through_pydantic_and_enrich(tmp_path, monkeypatch):
    from sqlmodel import SQLModel, create_engine
    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    from wfc.persistence import reset_engine
    reset_engine()
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)

    pipeline = PipelineInput(
        name="p",
        nodes=[
            PipelineNode(id="sel-1", type="input_selector",
                         samples=["a", "b", "c"], fan_mode="in"),
            PipelineNode(id="m-1", type="method",
                         method="csv_merge", module="csv_tools"),
        ],
        links=[PipelineLink(source="sel-1", target="m-1", targetHandle="sources")],
    )
    # Pydantic preserves fan_mode on the node.
    selector_node = pipeline.nodes[0]
    assert selector_node.fan_mode == "in"

    # _enrich_pipeline copies fan_mode into the input_selector node_dict.
    # Runs in-process so no DB is strictly required; _enrich_pipeline opens
    # its own session and iterates over empty modules — that is fine.
    enriched = _enrich_pipeline(pipeline)
    sel_dict = next(n for n in enriched["nodes"] if n["id"] == "sel-1")
    assert sel_dict["type"] == "input_selector"
    assert sel_dict["fan_mode"] == "in"
    assert sel_dict["samples"] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# engine: load_pipeline marks the consumer sample_collapsed
# ---------------------------------------------------------------------------


@workflow(purpose="load_pipeline sets sample_collapsed=True and collapsed_samples on the direct consumer of a fan-in selector")
def test_load_pipeline_marks_consumer_collapsed(tmp_path):
    pipeline_json = _minimal_fan_in_pipeline(["s1", "s2", "s3"])
    pipeline = _load(pipeline_json)
    assert len(pipeline.steps) == 1
    merge = pipeline.steps[0]
    assert merge.node_id == "merge-1"
    assert merge.sample_collapsed is True
    assert merge.collapsed_samples == ["s1", "s2", "s3"]
    # The fan-in slot survives the selector link's removal, with no upstreams.
    assert merge.inputs == {"sources": []}


# ---------------------------------------------------------------------------
# collapse propagates downstream (selector -> merge -> filter -> qc)
# ---------------------------------------------------------------------------


@workflow(purpose="Collapse is contagious: every step downstream of a fan-in selector has sample_collapsed=True")
def test_collapse_propagates_through_chain(tmp_path):
    pipeline_json = {
        "nodes": [
            {"id": "sel", "type": "input_selector",
             "samples": ["a", "b", "c"], "fan_mode": "in"},
            {"id": "merge", "method": "csv_merge", "module": "csv_tools",
             "script": "modules/_builtin/csv_merge/csv_merge.py", "params": {}, "env": "container:demo"},
            {"id": "filter", "method": "csv_filter", "module": "csv_tools",
             "script": "modules/_builtin/csv_filter/csv_filter.py", "params": {}, "env": "container:demo"},
            {"id": "qc", "method": "feature_qc", "module": "demo",
             "script": "methods/feature_qc/feature_qc.py", "params": {}, "env": "container:demo"},
        ],
        "links": [
            {"source": "sel", "target": "merge", "target_slot": "sources"},
            {"source": "merge", "target": "filter"},
            {"source": "filter", "target": "qc"},
        ],
        "samples": [],
    }
    pipeline = _load(pipeline_json)

    by_id = {s.node_id: s for s in pipeline.steps}
    for nid in ("merge", "filter", "qc"):
        assert by_id[nid].sample_collapsed is True, (
            f"{nid} must be collapsed (downstream of fan-in selector)"
        )
        assert by_id[nid].collapsed_samples == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# _output_path and _input_path for collapsed steps
# ---------------------------------------------------------------------------


@workflow(purpose="_output_path substitutes '__all__' for {sample} when step.sample_collapsed is True; {variant} stays wildcarded")
def test_output_path_collapsed_uses_all_sentinel():
    step = StepDef(
        method_name="csv_merge", module_name="csv_tools",
        script_path="modules/_builtin/csv_merge/csv_merge.py",
        params={}, depends_on=[], node_id="merge",
        output_ext=".csv",
        sample_collapsed=True, collapsed_samples=["a", "b"],
    )
    step_map = {"merge": step}
    out = _output_path("merge", step_map, pipeline_id="pid")
    # outputs are sentinels, not real workspace files.
    assert out == ".runs/sentinels/pid/merge/__all__/{variant}/.complete"
    # Sanity: a non-collapsed step still uses the {sample} wildcard.
    step2 = StepDef(
        method_name="csv_merge", module_name="csv_tools",
        script_path="modules/_builtin/csv_merge/csv_merge.py",
        params={}, depends_on=[], node_id="merge2",
        output_ext=".csv",
    )
    step_map2 = {"merge2": step2}
    assert "{sample}" in _output_path("merge2", step_map2, pipeline_id="pid")


@workflow(purpose="_input_path on a collapsed root consumer returns a slot-keyed list of per-sample restore sentinels")
def test_input_path_collapsed_root_emits_sentinels(tmp_path):
    # Route through load_pipeline (not a hand-built StepDef) so the slot
    # name must really propagate: the selector->method link is filtered
    # out of slot_map because the source is a system node, and a fixture
    # pre-populating inputs={"sources": []} would hide a missing slot name.
    pipeline = _load(_minimal_fan_in_pipeline(["s1", "s2", "s3"]))
    step_map = {s.node_id: s for s in pipeline.steps}

    merge_step = step_map["merge-1"]
    assert merge_step.sample_collapsed is True
    assert merge_step.collapsed_samples == ["s1", "s2", "s3"]
    # The fan-in target_slot from the selector->merge link must be
    # preserved on step.inputs so _input_path keys sentinels by "sources"
    # and downstream shell-gen routes --ref-input under the right name.
    assert "sources" in merge_step.inputs

    inp = _input_path("merge-1", step_map, pipeline_id="pid")
    assert isinstance(inp, dict)
    assert "sources" in inp
    assert inp["sources"] == [
        "data/samples/s1/.sample_ready",
        "data/samples/s2/.sample_ready",
        "data/samples/s3/.sample_ready",
    ]


# ---------------------------------------------------------------------------
# expand_step_combos for collapsed pipelines
# ---------------------------------------------------------------------------


@workflow(purpose="expand_step_combos over a collapsed pipeline yields exactly one row per variant, each with sample='__all__'")
def test_expand_step_combos_collapsed_one_per_variant():
    collapsed = StepDef(
        method_name="csv_merge", module_name="csv_tools",
        script_path="modules/_builtin/csv_merge/csv_merge.py",
        params={}, depends_on=[], node_id="merge",
        sample_collapsed=True, collapsed_samples=["a", "b", "c"],
    )
    resolved = {"merge": {"v1": {}, "v2": {}}}
    combos = [combo for _, combo in expand_step_combos(
        [collapsed], samples=["a", "b", "c"],
        resolved_params=resolved, explicit_combos=None,
    )]
    assert len(combos) == 2
    assert all(c["sample"] == "__all__" for c in combos)
    assert {c["variant"] for c in combos} == {"v1", "v2"}


# ---------------------------------------------------------------------------
# validation rejects unsupported fan-in shapes
# ---------------------------------------------------------------------------


@workflow(purpose="validate_workflow rejects a method node with multiple upstreams when any upstream is a fan-in selector")
def test_validate_rejects_multi_upstream_with_fan_in():
    pipeline = PipelineInput(
        nodes=[
            PipelineNode(id="sel-a", type="input_selector",
                         samples=["s1"], fan_mode="in"),
            PipelineNode(id="sel-b", type="input_selector",
                         samples=["s2"], fan_mode="out"),
            PipelineNode(id="m", type="method",
                         method="csv_merge", module="csv_tools"),
        ],
        links=[
            PipelineLink(source="sel-a", target="m"),
            PipelineLink(source="sel-b", target="m"),
        ],
        samples=["s1", "s2"],
    )
    result = validate_structure(pipeline.model_dump(), CSV_TOOLS_CONTRACTS)
    assert result["valid"] is False
    # The error should name both the selector and the consumer.
    joined = " | ".join(result["errors"])
    assert "sel-a" in joined
    assert "m" in joined


@workflow(purpose="validate_workflow rejects an input_selector with fan_mode='in' and an empty sample list")
def test_validate_rejects_fan_in_empty_samples():
    pipeline = PipelineInput(
        nodes=[
            PipelineNode(id="sel", type="input_selector",
                         samples=[], fan_mode="in"),
            PipelineNode(id="m", type="method",
                         method="csv_merge", module="csv_tools"),
        ],
        links=[PipelineLink(source="sel", target="m")],
        samples=[],
    )
    result = validate_structure(pipeline.model_dump(), CSV_TOOLS_CONTRACTS)
    assert result["valid"] is False
    assert any("sel" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# Tier 3: End-to-end Snakefile for a fan-in pipeline
# ---------------------------------------------------------------------------


@workflow(purpose="End-to-end: selector(fan-in, 3 samples) -> merge -> filter compiles to a Snakefile that names __all__ in every downstream path and rule-all target")
def test_end_to_end_fan_in_snakefile(tmp_path, wfc_root):
    _ = Step(step_num=1, name="Author pipeline with fan-in selector",
             purpose="Simulate canvas-compiled JSON with fan_mode=in")
    pipeline_json = {
        "nodes": [
            {"id": "sel", "type": "input_selector",
             "samples": ["s1", "s2", "s3"], "fan_mode": "in"},
            {"id": "merge", "method": "csv_merge", "module": "csv_tools",
             "script": "modules/_builtin/csv_merge/csv_merge.py",
             "params": {}, "output_ext": ".csv", "env": "container:demo"},
            {"id": "filter", "method": "csv_filter", "module": "csv_tools",
             "script": "modules/_builtin/csv_filter/csv_filter.py",
             "params": {}, "output_ext": ".csv", "env": "container:demo"},
        ],
        "links": [
            {"source": "sel", "target": "merge", "target_slot": "sources"},
            {"source": "merge", "target": "filter"},
        ],
        "samples": [],
    }
    path = _write_pipeline(tmp_path, pipeline_json)

    # NB: NO pre-staging of data/samples/<s>/ -- the generator must not
    # inspect the filesystem. Per-sample data files are resolved at
    # execution time by wfc run-step's --collapsed-sample handler (after
    # restore_sample populates the directories).

    _ = Step(step_num=2, name="load_pipeline + generate_snakefile",
             purpose="Round-trip through the engine")
    pipeline = load_pipeline_from_path(path)
    snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="fan-pid")

    _ = Step(step_num=3, name="Assert __all__ baked into downstream paths",
             purpose="Both merge and filter should carry the collapsed sentinel in their output paths")
    # Snakemake-visible outputs are sentinels.
    assert ".runs/sentinels/fan-pid/merge/__all__/{variant}/.complete" in snakefile
    assert ".runs/sentinels/fan-pid/filter/__all__/{variant}/.complete" in snakefile

    # rule all for a collapsed leaf must not emit sample=SAMPLES (no sample wildcard).
    rule_all = snakefile.split("rule all:")[1].split("\nrule ")[0]
    assert "__all__" in rule_all or "variant=VARIANT_NAMES" in rule_all
    assert "sample=SAMPLES" not in rule_all

    # Merge rule has the fan-in sentinel input list from _input_path.
    merge_rule = snakefile.split("rule merge:")[1].split("\nrule ")[0]
    assert "data/samples/s1/.sample_ready" in merge_rule
    assert "data/samples/s3/.sample_ready" in merge_rule

    # Shell line on collapsed rules passes literal __all__, not {wildcards.sample}.
    assert "--sample __all__" in merge_rule
    filter_rule = snakefile.split("rule filter:")[1].split("\nrule ")[0]
    assert "--sample __all__" in filter_rule

    # Shell line on the collapsed ROOT must declare each bundled sample
    # via --collapsed-sample <s>. The runtime resolver then walks
    # data/samples/<s>/ per sample and merges the per-sample data files
    # into the fan-in slot. Without this, wfc run-step sees an empty
    # slot_paths dict and the root-node guard fires with "root node
    # has no input data". Each sample must be named.
    for sample in ("s1", "s2", "s3"):
        assert f"--collapsed-sample {sample}" in merge_rule, (
            f"Expected --collapsed-sample {sample} in the merge shell "
            f"command; got:\n{merge_rule}"
        )
    # The generator emits no per-sample --ref-input flags for the
    # collapsed root -- runtime resolves the data file paths.
    assert "--ref-input sources=" not in merge_rule

    # Python preamble still compiles.
    python_section = snakefile.split("rule all:")[0]
    compile(python_section, "<snakefile>", "exec")


# ---------------------------------------------------------------------------
# Pipelines without an input_selector collapse nothing
# ---------------------------------------------------------------------------


@workflow(purpose="Pipelines with no input_selector collapse nothing — sample_collapsed stays False everywhere and every rule keeps its sample wildcard")
def test_legacy_no_input_selector_unchanged(tmp_path, wfc_root):
    pipeline = PipelineDef(
        steps=[
            StepDef("preprocess", "demo", "methods/preprocess/preprocess.py",
                    {}, depends_on=[], node_id="preprocess"),
            StepDef("filter", "demo", "methods/filter/filter.py",
                    {}, depends_on=["preprocess"], node_id="filter"),
        ],
        samples=["Pa16c"],
    )
    # No step is collapsed.
    assert all(not s.sample_collapsed for s in pipeline.steps)

    snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="legacy")
    # Snakemake-visible outputs are sentinels.
    assert ".runs/sentinels/legacy/preprocess/{sample}/{variant}/.complete" in snakefile
    assert ".runs/sentinels/legacy/filter/{sample}/{variant}/.complete" in snakefile
    # rule all uses sample=SAMPLES for non-collapsed leaf.
    rule_all = snakefile.split("rule all:")[1].split("\nrule ")[0]
    assert "sample=SAMPLES" in rule_all
    assert "__all__" not in snakefile


# ---------------------------------------------------------------------------
# NID versioning works for ("__all__", method) groups
# ---------------------------------------------------------------------------


@workflow(purpose="NID allocator versions three runs with sample='__all__' as v1, v2, v3 in chronological order — no schema changes required")
def test_nid_all_sample_group_sequential(tmp_path, monkeypatch):
    """Three runs for same method with sample='__all__' get v1, v2, v3."""
    import sqlite3
    from wfc import layout
    from wfc.canvas.wfc_provider import WfcProvider
    from wfc.persistence import reset_engine

    # The provider reads through the process engine: bind this project's
    # database with the override and a reset, as the harness does.
    monkeypatch.setenv("DATABASE_URL", layout.database_url(tmp_path))
    reset_engine()

    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Build the schema from the ORM models (single source of truth) so it
    # tracks wfc/persistence/schema.py -- the provider reads columns such as
    # run_inputs.input_name that a hand-rolled DDL would have to keep in step.
    import wfc.persistence  # noqa: F401  -- register tables on SQLModel.metadata
    from sqlmodel import SQLModel, create_engine
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO modules (id, name) VALUES (1, 'csv_tools')")
    conn.execute(
        "INSERT INTO methods (id, name, module_id, script_path, env) "
        "VALUES (1, 'csv_merge', 1, 'methods/csv_merge/csv_merge.py', 'container:demo')"
    )
    for rid, ts in [(1, "2026-01-01T10:00"), (2, "2026-01-01T11:00"), (3, "2026-01-01T12:00")]:
        conn.execute(
            "INSERT INTO runs (id, method_id, sample, status, started_at, nid) "
            "VALUES (?, 1, '__all__', 'completed', ?, NULL)",
            (rid, ts),
        )
    conn.commit()
    conn.close()

    provider = WfcProvider(str(tmp_path))
    provider.load()
    runs = {r.id: r for r in provider._runs.values()}
    assert runs["1"].nid == "v1"
    assert runs["2"].nid == "v2"
    assert runs["3"].nid == "v3"


# ---------------------------------------------------------------------------
# Bundle membership reaches the cache key
# ---------------------------------------------------------------------------


@workflow(purpose="A collapsed root's bundled sample SET reaches its cache "
                  "key: two same-size bundles differing in one member "
                  "compute different keys")
def test_bundle_membership_swap_moves_the_cache_key(git_project, monkeypatch):
    """`{s1, s2}` and `{s1, s3}` are the same size and must not share a key.

    Cardinality alone does not separate them — the parts list keeps
    duplicates, so `{s1, s2}` vs `{s1, s2, s3}` would differ on count
    whatever the content is. A same-size swap only moves the key if the
    members hash distinctly, which is what per-sample content buys.
    """
    from wfc.execution.claim import input_fingerprint_from_rows

    build_project(
        Scenario(samples=["s1", "s2", "s3"]),
        root=git_project, monkeypatch=monkeypatch,
    )

    assert input_fingerprint_from_rows(
        [], [("data", "s1"), ("data", "s2")], step="bundle_root") != \
        input_fingerprint_from_rows(
            [], [("data", "s1"), ("data", "s3")], step="bundle_root"), (
        "a same-size membership swap must move the bundle's cache key; "
        "identical sample content would collapse both bundles onto one key"
    )


# ---------------------------------------------------------------------------
# Cache-key stability under permutation; sensitivity to content
# ---------------------------------------------------------------------------


@workflow(purpose="Cache key for a fan-in bundle is stable under sample-list "
                  "permutation and changes when any member sample's content "
                  "hash changes")
def test_bundle_cache_key_permutation_and_sensitivity(git_project, monkeypatch):
    """Two assertions:
      (a) move one member's content hash -> bundle cache key changes
      (b) permute the sample-id input order -> bundle cache key unchanged

    (b) is the load-bearing one: the digest sorts its parts, and DB row
    order is not guaranteed, so without the sort the same bundle would key
    differently run to run. (a) pins that the sort has not flattened the
    bundle into something insensitive to its members.
    """
    from wfc.persistence import get_session, Sample
    from wfc.execution.claim import input_fingerprint_from_rows

    project = build_project(
        Scenario(samples=["a", "b", "c"]),
        root=git_project, monkeypatch=monkeypatch,
    )
    a_id = project.sample_ids["a"]
    bundle = [("data", "a"), ("data", "b"), ("data", "c")]

    # Baseline key over [a, b, c].
    baseline = input_fingerprint_from_rows([], bundle, step="bundle_root")

    # (b) Permute order -> same key.
    permuted = input_fingerprint_from_rows(
        [], [("data", "c"), ("data", "a"), ("data", "b")], step="bundle_root")
    assert baseline == permuted, (
        "cache key must be stable under sample-list permutation"
    )

    # (a) Move one member's content hash -> key changes. Re-registering
    # edited content is what a user does; the row is moved directly here
    # because the claim reads the row, and the row is the whole input.
    with get_session() as session:
        row = session.get(Sample, a_id)
        assert row is not None
        row.content_hash = "9" * 32
        session.add(row)
        session.commit()

    swapped = input_fingerprint_from_rows([], bundle, step="bundle_root")
    assert swapped != baseline, (
        "cache key must change when a member sample's content hash changes"
    )


# ---------------------------------------------------------------------------
# Guard: explicit_combos is incompatible with collapsed pipelines
# ---------------------------------------------------------------------------


def test_expand_step_combos_rejects_explicit_combos_on_collapsed():
    """If a caller supplies explicit_combos AND the pipeline contains any
    sample_collapsed step, expand_step_combos must raise ValueError.

    Silently returning the caller's combos would bake real sample names
    into combos while the collapsed step's output path has the literal
    '__all__' segment -- combo and path would disagree.
    """
    collapsed = StepDef(
        method_name="csv_merge", module_name="csv_tools",
        script_path="modules/_builtin/csv_merge/csv_merge.py",
        params={}, depends_on=[], node_id="merge",
        sample_collapsed=True, collapsed_samples=["s1", "s2"],
    )
    with pytest.raises(ValueError, match="fan-in"):
        expand_step_combos(
            [collapsed],
            samples=["s1", "s2"],
            resolved_params={"merge": {"v1": {}}},
            explicit_combos=[{"sample": "s1", "variant": "v1"}],
        )


# ---------------------------------------------------------------------------
# Generator must not inspect the filesystem during generate_snakefile
# ---------------------------------------------------------------------------


@workflow(purpose="generate_snakefile for a fan-in pipeline emits --collapsed-sample flags without inspecting data/samples/ on disk")
def test_generate_snakefile_collapsed_fanin_no_filesystem_inspection(
    tmp_path, wfc_root, monkeypatch
):
    """The collapsed-fan-in root branch in _generate_rule must derive
    per-sample identities from step.collapsed_samples (the pipeline
    contract) rather than from a Path.iterdir() walk over data/samples/.

    Why this test exists: restore_sample is itself a Snakemake rule whose
    outputs don't exist yet at Snakefile-generation time, so a walk over
    data/samples/ at that moment finds nothing, the Snakefile carries no
    per-sample inputs, and wfc run-step errors at runtime with "root node
    has no input data".

    The guard installed here records every iterdir() call the generator
    makes, and any walk over data/samples/ fails the test.
    """
    pipeline_json = _minimal_fan_in_pipeline(["s1", "s2", "s3"])
    path = _write_pipeline(tmp_path, pipeline_json)

    # Crucially: NO data/samples/<s>/ directories pre-staged. Generation
    # must work for the realistic case where samples live only in the
    # DVC cache until restore_sample materializes them at runtime.

    # Guard: record every iterdir() call made during generate_snakefile.
    # Every listing proceeds, so a failure can show *what* was walked; the
    # assertion below runs after generation completes.
    iterdir_calls = spy_directory_listings(monkeypatch)

    pipeline = load_pipeline_from_path(path)
    snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="no-stage")

    # Filter calls to those that touched data/samples/<s>/ (the forbidden walk).
    # Other iterdir() calls in the generator (e.g. walking modules/) are
    # legitimate and out of scope.
    sample_walks = [c for c in iterdir_calls if "data" in c and "samples" in c]
    assert sample_walks == [], (
        "_generate_rule walked data/samples/ at generation time; the "
        f"collapsed-fan-in root must derive per-sample identities from "
        f"step.collapsed_samples, not from disk. Walks: {sample_walks}"
    )

    # Sanity: the generated Snakefile carries one --collapsed-sample
    # flag per bundled sample on the merge rule's shell line.
    merge_rule = snakefile.split("rule merge-1:")[1].split("\nrule ")[0]
    for s in ("s1", "s2", "s3"):
        assert f"--collapsed-sample {s}" in merge_rule, (
            f"Expected --collapsed-sample {s} in merge rule shell line; "
            f"got:\n{merge_rule}"
        )
    # No per-sample --ref-input flags for the data files (those resolve at runtime).
    assert "--ref-input sources=" not in merge_rule


@workflow(purpose="wfc run-step's runtime fallback resolves per-sample data files for a collapsed-fan-in root and raises a meaningful error when a sample dir is missing")
def test_run_step_collapsed_sample_fallback_resolves_per_sample_dirs(
    git_project, tmp_path, monkeypatch, capsys
):
    """When wfc run-step is invoked with --sample __all__ and one
    --collapsed-sample <s> per bundled sample, it walks each sample's
    data/samples/<s>/ directory at execution time and merges the
    per-sample data files into the fan-in slot.

    Driven on the harness's stub rung: every phase of run_step runs for
    real against a real database and only the method process is faked at
    the dispatch boundary, so the resolved slot_paths are the ones the
    dispatch phase was actually handed.
    """
    obs = run_target(_fan_in_scenario(), "merge",
                     root=git_project, monkeypatch=monkeypatch)
    assert obs.exit_code(BUNDLE) == 0, (
        "run_step should succeed with all sample dirs populated"
    )

    slot_paths = obs.phase_args("dispatch", BUNDLE)["slot_paths"]
    # The link target_slot is "sources" (from _fan_in_scenario).
    assert "sources" in slot_paths, (
        f"Expected 'sources' key (the fan-in target_slot); got {slot_paths}"
    )
    paths = slot_paths["sources"]
    assert len(paths) == 3
    # Order must match collapsed_samples order (cache-key stability).
    for i, s in enumerate(("s1", "s2", "s3")):
        assert Path(paths[i]).resolve() == obs.project.sample_file(s).resolve(), (
            f"Path {i} should reference sample {s}; got {paths[i]}"
        )

    # Now: drop one sample directory -- the resolver must
    # raise a meaningful error rather than silently dropping the sample.
    capsys.readouterr()
    failed = run_target(_fan_in_scenario(missing_samples=("s2",)), "merge",
                        root=tmp_path / "missing_sample_project",
                        monkeypatch=monkeypatch)

    assert failed.exit_code(BUNDLE) == 1, (
        "run_step should fail when a bundled sample dir is missing"
    )
    err = capsys.readouterr().err
    assert "s2" in err, f"Error must name the missing sample; got: {err}"
    assert "collapsed-fan-in root" in err or "restore_sample" in err


# ---------------------------------------------------------------------------
# Tier 3: subprocess-level integration test.
#
# Closes the gap that direct in-process run_step() calls miss:
# the Tier 2 test above invokes wfc.execution.run_step() Python-to-Python with
# collapsed_samples already in the call signature, which short-circuits
# the argparse parser, the subprocess invocation chain, and the
# generator-output -> CLI-input handoff. This test:
#   1. Generates a Snakefile from a real fan-in pipeline (NO disk staging).
#   2. Parses out the literal `wfc run-step` argv from the Snakefile's
#      `shell:` line for the collapsed root rule.
#   3. Stages per-sample data files (simulating post-restore_sample state).
#   4. subprocess.run([sys.executable, "-m", "wfc", "run-step", *args])
#   5. Asserts exit 0 + the merged output file recorded as the run's
#      RunOutput artifact exists AND contains rows from all 3 samples.
#
# Catches: argparse flag-name typos, missing action="append", generator
# slot-name -> CLI parser mismatch, subprocess cwd/env breakage.
# ---------------------------------------------------------------------------


def _extract_wfc_run_step_argv(snakefile: str, rule_name: str) -> list[str]:
    """Pull the `wfc run-step ...` argv out of a rule's `shell:` line.

    The generator emits the shell line as a Snakemake Python f-string
    template like::

        shell:
            "{sys.executable} -m wfc run-step --node-id {params.node_id} ..."

    For a subprocess test we resolve `{sys.executable}`, `{params.node_id}`,
    and `{params.variant}` ourselves using the rule's params block, then
    return the argv list to pass to subprocess.run().
    """
    rule_body = snakefile.split(f"rule {rule_name}:")[1].split("\nrule ")[0]
    # The shell command is the quoted string immediately after `shell:`.
    m = re.search(r'shell:\s*"([^"]+)"', rule_body)
    assert m is not None, f"No shell: line found in rule {rule_name}:\n{rule_body}"
    cmd_template = m.group(1)
    # Pull params.node_id and params.variant from the params block.
    node_id_m = re.search(r'node_id\s*=\s*"([^"]+)"', rule_body)
    variant_m = re.search(r'variant\s*=\s*"([^"]+)"', rule_body)
    assert node_id_m and variant_m, (
        f"Missing node_id/variant in params block:\n{rule_body}"
    )
    resolved = (
        cmd_template
        .replace("{sys.executable}", sys.executable)
        .replace("{params.node_id}", node_id_m.group(1))
        .replace("{params.variant}", variant_m.group(1))
    )
    # Strip the leading `<python> -m wfc ` and shlex-split the rest into argv.
    # The generator template starts with `{sys.executable} -m wfc run-step ...`.
    parts = shlex.split(resolved, posix=(os.name != "nt"))
    # Find the index of "run-step" -- everything from that index onward is
    # the subcommand + flags we want to pass to `python -m wfc`.
    rs_idx = parts.index("run-step")
    return parts[rs_idx:]


@workflow(
    purpose=(
        "Subprocess-level integration: the Snakefile's literal `wfc run-step` "
        "argv for a collapsed-fan-in root parses through argparse, walks "
        "data/samples/<s>/ for each --collapsed-sample, and produces a "
        "merged output containing all 3 bundled samples' rows. Catches "
        "generator-CLI handoff bugs (flag typos, missing action=append, "
        "slot-name mismatches) the in-process Tier 2 tests skip."
    )
)
@pytest.mark.integration
@requires_docker
def test_run_step_subprocess_collapsed_fanin_end_to_end(
    git_project, monkeypatch, fixture_container_image
):
    _ = Step(
        step_num=1, name="Declare the fan-in scenario and build the project",
        purpose="A single fan-in selector (3 samples) feeding the `merge` "
                "method via target_slot='sources'. Each sample's data file "
                "carries 3 rows and the `.sample_ready` sentinel the real "
                "restore rule leaves behind, so the runtime resolver's "
                "dotfile skip is exercised and a merge of the whole bundle "
                "is 9 rows. The env record is pinned to the session-built "
                "image, so the methods run in real containers.",
    )
    scn = Scenario(
        nodes=[
            selector("selector_1", fan_mode="in"),
            node("merge_1", method="merge",
                 inputs=[wire("selector_1", target_slot="sources")],
                 outputs={"merged": ".csv"},
                 behavior=Behavior(concat_input="sources")),
        ],
        samples=["s1", "s2", "s3"],
        sample_ready_sentinel=True,
        sample_content=THREE_ROW_SAMPLE_CSV,
        env_name=FIXTURE_ENV_NAME,
        image_digest=fixture_container_image,
    )
    project = build_project(scn, root=git_project, monkeypatch=monkeypatch)
    project_dir = project.root

    _ = Step(
        step_num=2, name="Generate Snakefile and extract wfc run-step argv",
        purpose="The generator's literal CLI emission is the integration "
                "boundary the in-process Tier 2 tests skip. Parsing the "
                "shell line gives us the exact argv a real Snakemake "
                "invocation would hand to `python -m wfc run-step`.",
    )
    pipeline = load_pipeline_from_path(project.pipeline_json)
    pipeline_id = project.pipeline_id
    snakefile = generate_snakefile(
        pipeline, str(project_dir), pipeline_id=pipeline_id,
    )
    argv = _extract_wfc_run_step_argv(snakefile, "merge_1")
    # Sanity: the generator must have emitted one --collapsed-sample per bundle
    # member. If a change renames the flag (--collapsed_sample) or drops
    # action="append", argparse below will reject the invocation.
    assert argv.count("--collapsed-sample") == 3, (
        f"Expected 3 --collapsed-sample flags in generator argv; got: {argv}"
    )
    for s in ("s1", "s2", "s3"):
        assert s in argv, f"Sample {s} missing from argv: {argv}"
    # Must also carry --pipeline-json + --pipeline-id so the runtime can
    # introspect the topology (fan-in target_slot resolution).
    extra_args = [
        "--pipeline-json", str(project.pipeline_json),
        "--pipeline-id", pipeline_id,
    ]

    _ = Step(
        step_num=3, name="Invoke `python -m wfc run-step` as a real subprocess",
        purpose="Drives the full argparse + run_step path with cwd at the "
                "project root and WFC_PROJECT_ROOT/DATABASE_URL inherited.",
    )
    env = os.environ.copy()
    env["WFC_PROJECT_ROOT"] = str(project_dir)
    env["DATABASE_URL"] = f"sqlite:///{project_dir / '.wfc' / 'wfc.db'}"
    # Snakemake would pass PIPELINE_LOG_DIR; supply one so run_dir is
    # predictable and inside the project.
    pipeline_log_dir = project_dir / ".runs" / "logs" / pipeline_id
    pipeline_log_dir.mkdir(parents=True, exist_ok=True)
    env["PIPELINE_LOG_DIR"] = str(pipeline_log_dir)
    # Make sure the checkout's wfc package wins on PYTHONPATH so `-m wfc`
    # resolves to this source tree (not an installed wheel).
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    cmd = [sys.executable, "-m", "wfc", *argv, *extra_args]
    result = subprocess.run(
        cmd, cwd=str(project_dir), env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, (
        f"wfc run-step exited {result.returncode}.\n"
        f"CMD: {cmd}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    _ = Step(
        step_num=4, name="Verify merged output exists with all 3 samples' rows",
        purpose="Confirms the runtime fan-in resolver routed each sample's "
                "data file into the 'sources' slot and the merge method "
                "concatenated them. Without this, exit 0 alone could still "
                "mask a silently-empty fan-in slot.",
    )
    # Locate the merge run's output via RunOutput.artifact_path (the
    # run-archive path is the source of truth).
    from wfc.persistence import get_session, Method, Run, RunOutput
    from sqlmodel import select
    with get_session() as session:
        stmt = (
            select(RunOutput, Run)
            .join(Run, RunOutput.run_id == Run.id)
            .where(Run.method_id.in_(
                select(Method.id).where(Method.name == "merge")
            ))
            .where(Run.sample == "__all__")
            .where(RunOutput.output_name == "merged.csv")
        )
        ro_rows = session.exec(stmt).all()
    assert len(ro_rows) == 1, (
        f"Expected exactly 1 RunOutput row for merge_1/__all__/merged.csv; "
        f"found {len(ro_rows)}."
    )
    merged_path = Path(ro_rows[0][0].artifact_path)
    assert merged_path.exists(), f"merge artifact missing: {merged_path}"
    with open(merged_path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 9, (
        f"Expected 9 merged rows (3 samples x 3 rows); got {len(rows)}. "
        f"Either the runtime resolver dropped samples or the merge method "
        f"only saw one slot entry."
    )


# ---------------------------------------------------------------------------
# One rule decides who carries the bundle
# ---------------------------------------------------------------------------


@workflow(purpose="One predicate decides which steps carry the fan-in bundle: "
                  "the collapsed root's generated rule waits on the bundle's "
                  "sentinels and its shell names every bundled sample, the "
                  "chain-collapsed consumer's rule does neither, and the test "
                  "harness's run_step driver splits the same way -- so a green "
                  "harness scenario is evidence about production's cache key")
def test_one_predicate_decides_who_carries_the_bundle():
    from wfc import layout
    from wfc.orchestration.snakemake import _generate_rule
    from tests.harness.drivers import _step_kwargs

    pipeline_json = {
        "nodes": [
            {"id": "sel", "type": "input_selector",
             "samples": ["a", "b", "c"], "fan_mode": "in"},
            {"id": "merge", "method": "csv_merge", "module": "csv_tools",
             "script": "modules/_builtin/csv_merge/csv_merge.py",
             "params": {}, "env": "container:demo"},
            {"id": "filter", "method": "csv_filter", "module": "csv_tools",
             "script": "modules/_builtin/csv_filter/csv_filter.py",
             "params": {}, "env": "container:demo"},
        ],
        "links": [
            {"source": "sel", "target": "merge", "target_slot": "sources"},
            {"source": "merge", "target": "filter"},
        ],
        "samples": [],
    }
    pipeline = _load(pipeline_json)
    step_map = {s.node_id: s for s in pipeline.steps}

    # Both steps are collapsed and both carry the sample list -- collapse is
    # contagious. That is precisely why the raw field is not the rule.
    assert step_map["filter"].sample_collapsed is True
    assert step_map["filter"].collapsed_samples == ["a", "b", "c"]

    # The generator: the root names every bundled sample on its shell line
    # and waits on the bundle's sentinels; the chain consumer does neither.
    root_rule = "\n".join(_generate_rule(step_map["merge"], step_map, "pipe-bundle"))
    chain_rule = "\n".join(_generate_rule(step_map["filter"], step_map, "pipe-bundle"))
    for s in ("a", "b", "c"):
        assert f"--collapsed-sample {s}" in root_rule, root_rule
    assert "--collapsed-sample" not in chain_rule, chain_rule

    assert _input_path("merge", step_map, "pipe-bundle") == {
        "sources": [layout.sample_ready_sentinel_relpath(s) for s in ("a", "b", "c")]
    }
    chain_inputs = _input_path("filter", step_map, "pipe-bundle")
    assert "sample_ready" not in str(chain_inputs), chain_inputs

    # The harness driver reads the same predicate, so the two rungs hand
    # run_step the same bundle for the same step.
    project = SimpleNamespace(pipeline_json="pipeline.json",
                              pipeline_id="pipe-bundle")
    root_kwargs = _step_kwargs(project, step_map["merge"],
                               ("merge", "__all__", "default"))
    chain_kwargs = _step_kwargs(project, step_map["filter"],
                                ("filter", "__all__", "default"))
    assert root_kwargs["collapsed_samples"] == ["a", "b", "c"]
    assert chain_kwargs["collapsed_samples"] is None
