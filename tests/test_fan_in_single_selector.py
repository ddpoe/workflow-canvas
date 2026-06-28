"""
Single-selector fan-in tests (PEV cycle 2026-04-18-fan-in-single-selector).

Validates that a pipeline with a fan_mode="in" Input Selector:
  - Sets StepDef.sample_collapsed=True and collapsed_samples on the
    direct consumer and every downstream step (contagious collapse).
  - _output_path emits the literal "__all__" as the sample segment.
  - _input_path on the collapsed consumer emits per-sample restore sentinels.
  - expand_variant_combos emits exactly one combo per variant with sample="__all__".
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

import pytest
from axiom_annotations import workflow, Step

from wfc.canvas.server import validate_workflow, PipelineInput, PipelineNode, PipelineLink, _enrich_pipeline
from wfc.snakemake_gen import (
    StepDef, PipelineDef, load_pipeline, generate_snakefile,
    expand_variant_combos, _output_path, _input_path,
)

# Re-export fixture infra so pytest discovers `pipeline_factory` and
# `register_fixture_methods` in this module's scope (used by the
# Tier 3 subprocess test below).
from tests.fixtures.conftest import (  # noqa: F401
    register_fixture_methods,
    pipeline_factory,
)
from tests.conftest import requires_docker


PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_pipeline(tmp_path: Path, pipeline_json: dict) -> Path:
    p = tmp_path / "pipeline.json"
    p.write_text(json.dumps(pipeline_json))
    return p


def _minimal_fan_in_pipeline(samples: list[str]) -> dict:
    """Pipeline: selector(fan_mode=in, samples) -> merge."""
    return {
        "nodes": [
            {"id": "sel-1", "type": "input_selector",
             "method": "", "module": "",
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
# US-1 plumbing: fan_mode survives Pydantic round-trip + enrichment
# ---------------------------------------------------------------------------


@workflow(purpose="fan_mode survives Pydantic round-trip through PipelineInput and _enrich_pipeline emits it in the input_selector node_dict")
def test_fan_mode_roundtrip_through_pydantic_and_enrich(tmp_path, monkeypatch):
    from sqlmodel import SQLModel, create_engine
    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    from wfc.database import reset_engine
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
# US-1 engine: load_pipeline marks the consumer sample_collapsed
# ---------------------------------------------------------------------------


@workflow(purpose="load_pipeline sets sample_collapsed=True and collapsed_samples on the direct consumer of a fan-in selector")
def test_load_pipeline_marks_consumer_collapsed(tmp_path):
    pipeline_json = _minimal_fan_in_pipeline(["s1", "s2", "s3"])
    path = _write_pipeline(tmp_path, pipeline_json)

    pipeline = load_pipeline(path)
    assert len(pipeline.steps) == 1
    merge = pipeline.steps[0]
    assert merge.node_id == "merge-1"
    assert merge.sample_collapsed is True
    assert merge.collapsed_samples == ["s1", "s2", "s3"]


# ---------------------------------------------------------------------------
# US-2: collapse propagates downstream (selector -> merge -> filter -> qc)
# ---------------------------------------------------------------------------


@workflow(purpose="Collapse is contagious: every step downstream of a fan-in selector has sample_collapsed=True")
def test_collapse_propagates_through_chain(tmp_path):
    pipeline_json = {
        "nodes": [
            {"id": "sel", "type": "input_selector",
             "method": "", "module": "",
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
    path = _write_pipeline(tmp_path, pipeline_json)
    pipeline = load_pipeline(path)

    by_id = {s.node_id: s for s in pipeline.steps}
    for nid in ("merge", "filter", "qc"):
        assert by_id[nid].sample_collapsed is True, (
            f"{nid} must be collapsed (downstream of fan-in selector)"
        )
        assert by_id[nid].collapsed_samples == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# US-1: _output_path and _input_path for collapsed steps
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
    # ADR-018: outputs are sentinels, not real workspace files.
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
    # Route through load_pipeline (not a hand-built StepDef) so the test
    # would actually catch the slot-name propagation bug: the selector->
    # method link is filtered out of slot_map because the source is a
    # system node, and earlier versions of this test hid that by
    # pre-populating inputs={"sources": []} on the fixture.
    path = _write_pipeline(tmp_path, _minimal_fan_in_pipeline(["s1", "s2", "s3"]))
    pipeline = load_pipeline(path)
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
# US-2: expand_variant_combos for collapsed pipelines
# ---------------------------------------------------------------------------


@workflow(purpose="expand_variant_combos over a collapsed pipeline yields exactly one combo per variant, each with sample='__all__'")
def test_expand_variant_combos_collapsed_one_per_variant():
    collapsed = StepDef(
        method_name="csv_merge", module_name="csv_tools",
        script_path="modules/_builtin/csv_merge/csv_merge.py",
        params={}, depends_on=[], node_id="merge",
        sample_collapsed=True, collapsed_samples=["a", "b", "c"],
    )
    resolved = {"merge": {"v1": {}, "v2": {}}}
    combos = expand_variant_combos(
        [collapsed], samples=["a", "b", "c"],
        resolved_params=resolved, explicit_combos=None,
    )
    assert len(combos) == 2
    assert all(c["sample"] == "__all__" for c in combos)
    assert {c["variant"] for c in combos} == {"v1", "v2"}


# ---------------------------------------------------------------------------
# US-3: validation rejects unsupported fan-in shapes
# ---------------------------------------------------------------------------


@pytest.fixture
def validate_db(tmp_path, monkeypatch):
    """Empty SQLite DB so ``validate_workflow``'s ``get_session()`` resolves
    deterministically in isolation.

    These tests assert validation *errors* (fan-in shape rejection), not DB
    contents, so an empty schema is sufficient. Without this the tests only
    pass via engine state leaked from earlier tests in the module.
    """
    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    from wfc.database import reset_engine
    reset_engine()
    yield
    reset_engine()


@workflow(purpose="validate_workflow rejects a method node with multiple upstreams when any upstream is a fan-in selector")
def test_validate_rejects_multi_upstream_with_fan_in(validate_db):
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
    result = validate_workflow(pipeline)
    assert result["valid"] is False
    # The error should name both the selector and the consumer.
    joined = " | ".join(result["errors"])
    assert "sel-a" in joined
    assert "m" in joined


@workflow(purpose="validate_workflow rejects an input_selector with fan_mode='in' and an empty sample list")
def test_validate_rejects_fan_in_empty_samples(validate_db):
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
    result = validate_workflow(pipeline)
    assert result["valid"] is False
    assert any("sel" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# US-1 + US-2 (Tier 3): End-to-end Snakefile for a fan-in pipeline
# ---------------------------------------------------------------------------


@workflow(purpose="End-to-end: selector(fan-in, 3 samples) -> merge -> filter compiles to a Snakefile that names __all__ in every downstream path and rule-all target")
def test_end_to_end_fan_in_snakefile(tmp_path, wfc_root):
    _ = Step(step_num=1, name="Author pipeline with fan-in selector",
             purpose="Simulate canvas-compiled JSON with fan_mode=in")
    pipeline_json = {
        "nodes": [
            {"id": "sel", "type": "input_selector",
             "method": "", "module": "",
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
    # restore_sample populates the directories). PEV cycle
    # 2026-05-02-snakemake-gen-collapsed-fanin-fix removed the previous
    # iterdir() call from _generate_rule.

    _ = Step(step_num=2, name="load_pipeline + generate_snakefile",
             purpose="Round-trip through the engine")
    pipeline = load_pipeline(path)
    snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="fan-pid")

    _ = Step(step_num=3, name="Assert __all__ baked into downstream paths",
             purpose="Both merge and filter should carry the collapsed sentinel in their output paths")
    # ADR-018: Snakemake-visible outputs are sentinels.
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
    # slot_paths dict and the D-2 root-node guard fires with "root node
    # has no input data". Each sample must be named.
    for sample in ("s1", "s2", "s3"):
        assert f"--collapsed-sample {sample}" in merge_rule, (
            f"Expected --collapsed-sample {sample} in the merge shell "
            f"command; got:\n{merge_rule}"
        )
    # Generator no longer emits per-sample --ref-input flags for the
    # collapsed root -- runtime resolves the data file paths.
    assert "--ref-input sources=" not in merge_rule

    # Python preamble still compiles.
    python_section = snakefile.split("rule all:")[0]
    compile(python_section, "<snakefile>", "exec")


# ---------------------------------------------------------------------------
# Legacy regression: pipelines without input_selector unchanged
# ---------------------------------------------------------------------------


@workflow(purpose="Pipelines with no input_selector produce the same Snakefile structure as before — sample_collapsed stays False everywhere")
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
    # ADR-018: Snakemake-visible outputs are sentinels.
    assert ".runs/sentinels/legacy/preprocess/{sample}/{variant}/.complete" in snakefile
    assert ".runs/sentinels/legacy/filter/{sample}/{variant}/.complete" in snakefile
    # rule all uses sample=SAMPLES for non-collapsed leaf.
    rule_all = snakefile.split("rule all:")[1].split("\nrule ")[0]
    assert "sample=SAMPLES" in rule_all
    assert "__all__" not in snakefile


# ---------------------------------------------------------------------------
# US-4: NID versioning works for ("__all__", method) groups
# ---------------------------------------------------------------------------


@workflow(purpose="NID allocator versions three runs with sample='__all__' as v1, v2, v3 in chronological order — no schema changes required")
def test_nid_all_sample_group_sequential(tmp_path):
    """Three runs for same method with sample='__all__' get v1, v2, v3."""
    import sqlite3
    from wfc.canvas.wfc_provider import WfcProvider

    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Build the schema from the ORM models (single source of truth) so it
    # tracks wfc/models.py -- the provider reads run_inputs.input_name, which
    # the old hand-rolled DDL omitted.
    import wfc.models  # noqa: F401  -- register tables on SQLModel.metadata
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
# D-6 Caveat #3: cache-key stability under permutation; sensitivity to content
# ---------------------------------------------------------------------------


@workflow(purpose="Cache key for a fan-in bundle is stable under sample-list permutation and changes when any sample's (size, mtime) changes")
def test_bundle_cache_key_permutation_and_sensitivity(tmp_path, monkeypatch):
    """Two assertions:
      (a) swap one sample's (size, mtime) -> bundle cache key changes
      (b) permute the sample-id input order -> bundle cache key unchanged
    """
    from sqlmodel import SQLModel, Session, create_engine
    from wfc.models import Sample
    from wfc.version import build_input_fingerprint

    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    from wfc.database import reset_engine
    reset_engine()

    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        s_a = Sample(
            name="a", source_path="/a.csv",
            registered_path="data/samples/a/a.csv",
            file_type="csv", file_size=100, file_mtime=1.0,
        )
        s_b = Sample(
            name="b", source_path="/b.csv",
            registered_path="data/samples/b/b.csv",
            file_type="csv", file_size=200, file_mtime=2.0,
        )
        s_c = Sample(
            name="c", source_path="/c.csv",
            registered_path="data/samples/c/c.csv",
            file_type="csv", file_size=300, file_mtime=3.0,
        )
        session.add_all([s_a, s_b, s_c])
        session.commit()
        a_id, b_id, c_id = s_a.id, s_b.id, s_c.id

    # Baseline key over [a, b, c].
    baseline = build_input_fingerprint([], sample_ids=[a_id, b_id, c_id])

    # (b) Permute order -> same key.
    permuted = build_input_fingerprint([], sample_ids=[c_id, a_id, b_id])
    assert baseline == permuted, (
        "cache key must be stable under sample-list permutation"
    )

    # (a) Swap one sample's (size, mtime) -> key changes.
    with Session(engine) as session:
        row = session.get(Sample, a_id)
        assert row is not None
        row.file_size = 999
        row.file_mtime = 42.0
        session.add(row)
        session.commit()

    swapped = build_input_fingerprint([], sample_ids=[a_id, b_id, c_id])
    assert swapped != baseline, (
        "cache key must change when a sample's (size, mtime) changes"
    )


# ---------------------------------------------------------------------------
# Guard: explicit_combos is incompatible with collapsed pipelines
# ---------------------------------------------------------------------------


def test_expand_variant_combos_rejects_explicit_combos_on_collapsed():
    """If a caller supplies explicit_combos AND the pipeline contains any
    sample_collapsed step, expand_variant_combos must raise ValueError.

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
        expand_variant_combos(
            [collapsed],
            samples=["s1", "s2"],
            resolved_params={"merge": {"v1": {}}},
            explicit_combos=[{"sample": "s1", "variant": "v1"}],
        )


# ---------------------------------------------------------------------------
# Regression (PEV cycle 2026-05-02): generator must not inspect filesystem
# ---------------------------------------------------------------------------


@workflow(purpose="generate_snakefile for a fan-in pipeline emits --collapsed-sample flags without inspecting data/samples/ on disk")
def test_generate_snakefile_collapsed_fanin_no_filesystem_inspection(
    tmp_path, wfc_root, monkeypatch
):
    """The collapsed-fan-in root branch in _generate_rule must derive
    per-sample identities from step.collapsed_samples (the pipeline
    contract) rather than from a Path.iterdir() walk over data/samples/.

    Why this test exists: prior to this fix _generate_rule called
    iterdir() at Snakefile-generation time. restore_sample is itself a
    Snakemake rule whose outputs don't exist yet at that moment, so the
    walk silently no-op'd and the resulting Snakefile dropped the
    per-sample --ref-input flags. wfc run-step then errored at runtime
    with "root node has no input data".

    The guard installed here makes any iterdir() call from the generator
    fail loudly, so a future regression to filesystem inspection is
    caught immediately.
    """
    pipeline_json = _minimal_fan_in_pipeline(["s1", "s2", "s3"])
    path = _write_pipeline(tmp_path, pipeline_json)

    # Crucially: NO data/samples/<s>/ directories pre-staged. The fix
    # must work for the realistic case where samples live only in the
    # DVC cache until restore_sample materializes them at runtime.

    # Guard: any iterdir() call during generate_snakefile is a regression.
    import pathlib
    original_iterdir = pathlib.Path.iterdir
    iterdir_calls: list[str] = []

    def _forbidden_iterdir(self):
        iterdir_calls.append(str(self))
        # Allow the call to proceed so the test can show *what* was
        # walked when it fails, but raise after generation completes.
        return original_iterdir(self)

    monkeypatch.setattr(pathlib.Path, "iterdir", _forbidden_iterdir)

    pipeline = load_pipeline(path)
    snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="no-stage")

    # Filter calls to those that touched data/samples/<s>/ (the bug).
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
    tmp_path, monkeypatch, capsys
):
    """When wfc run-step is invoked with --sample __all__ and one
    --collapsed-sample <s> per bundled sample, it walks each sample's
    data/samples/<s>/ directory at execution time and merges the
    per-sample data files into the fan-in slot.

    This test drives the input-resolution branch directly without a
    full Snakemake invocation -- it monkeypatches the pre_run/method
    subprocess pieces and inspects the slot_paths the resolver builds.
    """
    from sqlmodel import SQLModel, create_engine

    # Stand up an isolated DB so pre_run can register a row.
    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    from wfc.database import reset_engine
    reset_engine()
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)

    # Project root for the run: data/samples lives here. Create the
    # .wfc/wf-canvas.toml marker so wfc.database.project_root() resolves.
    project_root = tmp_path / "proj"
    project_root.mkdir()
    (project_root / ".wfc").mkdir()
    (project_root / ".wfc" / "wf-canvas.toml").write_text("")
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(project_root))
    monkeypatch.chdir(project_root)

    # Pre-stage three sample data files (simulating the post-restore_sample
    # state). The runtime fallback should pick the first non-dotfile in
    # each directory.
    for s in ("s1", "s2", "s3"):
        sd = project_root / "data" / "samples" / s
        sd.mkdir(parents=True)
        (sd / ".sample_ready").touch()  # sentinel must be skipped
        (sd / "data.csv").write_text(f"sample,{s}\n")

    # Author a fan-in pipeline JSON that the runtime can introspect.
    # ADR-019 Cycle H: run_step is container-only and resolves the node's
    # env to a built container image BEFORE the input-resolution branch this
    # test exercises. Give merge-1 a manifest-backed container env and write a
    # placeholder ``fixture-env`` record so resolution passes without a Docker
    # build (the actual ``docker run`` is short-circuited by the
    # ``_run_method_subprocess`` monkeypatch below).
    pipeline_dict = _minimal_fan_in_pipeline(["s1", "s2", "s3"])
    for n in pipeline_dict["nodes"]:
        if n["id"] == "merge-1":
            n["env"] = "fixture-env"
    (project_root / ".wfc" / "envs.json").write_text(json.dumps({
        "schema_version": 1,
        "envs": {
            "fixture-env": {
                "backend": "pixi",
                "source": "pixi.toml",
                "container": "docker://local/wfc-test-minimal@sha256:" + "a" * 64,
                "env_fingerprint": "a" * 64,
                "built_from_lock": "pixi.lock",
                "built_at": "2026-06-23T00:00:00Z",
            }
        },
    }))
    pipeline_path = tmp_path / "pipe.json"
    pipeline_path.write_text(json.dumps(pipeline_dict))

    # Patch the slow/heavy pieces of run_step. We only care about the
    # input resolution branch -- short-circuit pre_run, the subprocess,
    # complete_run, and DVC.
    from wfc import cli as wfc_cli

    captured: dict[str, dict] = {}

    def fake_pre_run(**kwargs):
        return ("NEW", 1)

    class _FakeResult:
        returncode = 0

    def fake_subprocess(cmd, cwd, env, stdout_log, stderr_log):
        # Capture the slot_paths the resolver wrote into _run_context.json.
        run_dir = wfc_cli._run_archive_dir(int(env["WFC_RUN_ID"]))
        ctx = json.loads((run_dir / "_run_context.json").read_text())
        captured["slot_paths"] = ctx["slot_paths"]
        # Touch the declared output so the slot-scanner doesn't fail.
        out = run_dir / "output.parquet"
        out.write_text("ok")
        # Touch metrics.
        (run_dir / "metrics.json").write_text("{}")
        return _FakeResult()

    def fake_complete_run(**kwargs):
        return None

    monkeypatch.setattr(wfc_cli, "pre_run", fake_pre_run)
    monkeypatch.setattr(wfc_cli, "_run_method_subprocess", fake_subprocess)
    monkeypatch.setattr(wfc_cli, "complete_run", fake_complete_run)

    rc = wfc_cli.run_step(
        node_id="merge-1",
        sample="__all__",
        variant="default",
        method_name="csv_merge",
        module_name="csv_tools",
        script_path="modules/_builtin/csv_merge/csv_merge.py",
        pipeline_json=str(pipeline_path),
        pipeline_id="pid-1",
        collapsed_samples=["s1", "s2", "s3"],
    )
    assert rc == 0, "run_step should succeed with all sample dirs populated"

    slot_paths = captured["slot_paths"]
    # The link target_slot is "sources" (from _minimal_fan_in_pipeline).
    assert "sources" in slot_paths, (
        f"Expected 'sources' key (the fan-in target_slot); got {slot_paths}"
    )
    paths = slot_paths["sources"]
    assert len(paths) == 3
    # Order must match collapsed_samples order (cache-key stability).
    for i, s in enumerate(("s1", "s2", "s3")):
        assert f"data{os.sep}samples{os.sep}{s}{os.sep}data.csv" in paths[i] \
            or f"data/samples/{s}/data.csv" in paths[i], (
            f"Path {i} should reference sample {s}; got {paths[i]}"
        )

    # Now: drop one sample directory and rerun -- the resolver must
    # raise a meaningful error rather than silently dropping the sample.
    import shutil
    shutil.rmtree(project_root / "data" / "samples" / "s2")

    rc2 = wfc_cli.run_step(
        node_id="merge-1",
        sample="__all__",
        variant="default",
        method_name="csv_merge",
        module_name="csv_tools",
        script_path="modules/_builtin/csv_merge/csv_merge.py",
        pipeline_json=str(pipeline_path),
        pipeline_id="pid-2",
        collapsed_samples=["s1", "s2", "s3"],
    )
    assert rc2 == 1, "run_step should fail when a bundled sample dir is missing"
    err = capsys.readouterr().err
    assert "s2" in err, f"Error must name the missing sample; got: {err}"
    assert "collapsed-fan-in root" in err or "restore_sample" in err


# ---------------------------------------------------------------------------
# Tier 3 (PEV cycle 2026-05-02): subprocess-level integration test.
#
# Closes the gap that direct in-process run_step() calls miss:
# the Tier 2 test above invokes wfc.cli.run_step() Python-to-Python with
# collapsed_samples already in the call signature, which short-circuits
# the argparse parser, the subprocess invocation chain, and the
# generator-output -> CLI-input handoff. This test:
#   1. Generates a Snakefile from a real fan-in pipeline (NO disk staging).
#   2. Parses out the literal `wfc run-step` argv from the Snakefile's
#      `shell:` line for the collapsed root rule.
#   3. Stages per-sample data files (simulating post-restore_sample state).
#   4. subprocess.run([sys.executable, "-m", "wfc", "run-step", *args])
#   5. Asserts exit 0 + the merged output file exists in the expected
#      .runs/workspace/... path AND contains rows from all 3 samples.
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
    pipeline_factory, register_fixture_methods
):
    project_dir = register_fixture_methods

    _ = Step(
        step_num=1, name="Stage per-sample data files",
        purpose="Simulate post-restore_sample on-disk state. Files are "
                "written to data/samples/<s>/data.csv WITHOUT calling the "
                "real DVC restore_sample rule -- this matches what the "
                "runtime resolver in wfc.cli.run_step expects to see.",
    )
    # Each sample contributes 3 rows; merge should produce 9 total.
    from tests.fixtures.conftest import create_sample_csv
    for sample in ("s1", "s2", "s3"):
        create_sample_csv(project_dir, sample, num_rows=3)
        # The .sample_ready sentinel exists in the real runtime to gate
        # Snakemake dependency ordering. The runtime resolver skips dotfiles,
        # so this sentinel must NOT be picked up as the sample data file.
        (project_dir / "data" / "samples" / sample / ".sample_ready").write_text("")

    _ = Step(
        step_num=2, name="Build fan-in pipeline JSON",
        purpose="Single fan-in selector (3 samples) feeding the `merge` "
                "fixture method via target_slot='sources'. NO pre-staging "
                "beyond the sample CSVs above -- the generator must not "
                "inspect the filesystem.",
    )
    pipeline_path = pipeline_factory(
        name="fan_in_subproc",
        nodes=[
            {"id": "selector_1", "type": "input_selector",
             "samples": ["s1", "s2", "s3"], "fan_mode": "in"},
            {"id": "merge_1", "method": "merge", "module": "test_pipeline",
             "params": {}, "env": "container:fixture-env"},
        ],
        links=[
            {"source": "selector_1", "target": "merge_1",
             "target_slot": "sources"},
        ],
        samples=[],
    )

    _ = Step(
        step_num=3, name="Generate Snakefile and extract wfc run-step argv",
        purpose="The generator's literal CLI emission is the integration "
                "boundary the in-process Tier 2 tests skip. Parsing the "
                "shell line gives us the exact argv a real Snakemake "
                "invocation would hand to `python -m wfc run-step`.",
    )
    pipeline = load_pipeline(pipeline_path)
    pipeline_id = "subproc-pid"
    snakefile = generate_snakefile(
        pipeline, str(project_dir), pipeline_id=pipeline_id,
    )
    argv = _extract_wfc_run_step_argv(snakefile, "merge_1")
    # Sanity: the generator must have emitted one --collapsed-sample per bundle
    # member. If a regression renames the flag (--collapsed_sample) or drops
    # action="append", argparse below will reject the invocation.
    assert argv.count("--collapsed-sample") == 3, (
        f"Expected 3 --collapsed-sample flags in generator argv; got: {argv}"
    )
    for s in ("s1", "s2", "s3"):
        assert s in argv, f"Sample {s} missing from argv: {argv}"
    # Must also carry --pipeline-json + --pipeline-id so the runtime can
    # introspect the topology (fan-in target_slot resolution).
    extra_args = [
        "--pipeline-json", str(pipeline_path),
        "--pipeline-id", pipeline_id,
    ]

    _ = Step(
        step_num=4, name="Invoke `python -m wfc run-step` as a real subprocess",
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
    # Make sure the worktree's wfc package wins on PYTHONPATH so `-m wfc`
    # resolves to the source we just edited (not an installed wheel).
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
        step_num=5, name="Verify merged output exists with all 3 samples' rows",
        purpose="Confirms the runtime fan-in resolver routed each sample's "
                "data.csv into the 'sources' slot and the merge method "
                "concatenated them. Without this, exit 0 alone could still "
                "mask a silently-empty fan-in slot.",
    )
    # ADR-018: workspace dir is gone. Locate the merge run's output via
    # RunOutput.artifact_path (the run-archive path is the source of truth).
    from wfc.database import get_session
    from wfc.models import Method, Run, RunOutput
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
