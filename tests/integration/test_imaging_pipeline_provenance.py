"""Layer B — imaging marquee: end-to-end skip-link provenance (US-1, US-2).

The 7-node imaging DAG is the load-bearing system-level integrity test. It runs
the full skip-link topology containerized via ``run_pipeline`` and proves two
invariants that the provenance primitives never prove together:

  US-1 (bytes survive the round-trip): for every produced output, after the
        deferred-archive pass, RunOutput.content_hash equals md5(staging bytes)
        equals md5(the DVC cache file). Nothing is silently re-hashed or swapped.

  US-2 (input slot resolves to its wired node): export_final fans in THREE
        slots (measurements immediate, stitched 3-hop skip, masks 2-hop skip).
        The completed fixture scripts tag each output row by the ``source_slot``
        it arrived through and carry the upstream lineage chain. We assert all
        three slots are present AND that the two SKIP-LINK sources carry
        DISTINGUISHABLE content (stitched-rows trace through ``stitch``, masks-
        rows trace through ``segment``). Because dedup is content-addressed, a
        mis-route would only be detectable if the sources differ — they do, so
        a swapped/dropped skip-link surfaces in the content.

Topology (skip-links beyond the linear spine):
    input_selector -> build_config -> tile_export -> illum_correct -> stitch
                                                                        |
    stitch <- config (3-hop skip from build_config)                     v
    stitch <- corrected (immediate from illum_correct)                segment
    quantify <- stitched (2-hop skip from stitch) + masks (immediate from segment)
    export_final <- measurements (immediate) + stitched (3-hop skip) + masks (2-hop skip)

Marked integration + requires_docker (Docker build via register_imaging_methods).
"""

import csv
from pathlib import Path

import pytest
from sqlmodel import select

from axiom_annotations import workflow, Step

from wfc.cli import run_pipeline
from wfc.database import get_session
from wfc.models import Method, Run, RunOutput
from wfc.provenance import _cache_path, archive_outputs, hash_path
from tests.fixtures.conftest import create_sample_csv as _create_sample_csv
from tests.conftest import requires_docker

WFC_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = [pytest.mark.integration, requires_docker]


def _imaging_pipeline_nodes_and_links():
    """The 7-node skip-link DAG: nodes + links (with skip-link target_slots)."""
    nodes = [
        {"id": "sel", "type": "input_selector", "samples": ["img_sample"]},
        {"id": "build_config", "method": "build_config", "module": "imaging"},
        {"id": "tile_export", "method": "tile_export", "module": "imaging"},
        {"id": "illum_correct", "method": "illum_correct", "module": "imaging"},
        {"id": "stitch", "method": "stitch", "module": "imaging"},
        {"id": "segment", "method": "segment", "module": "imaging"},
        {"id": "quantify", "method": "quantify", "module": "imaging"},
        {"id": "export_final", "method": "export_final", "module": "imaging"},
    ]
    links = [
        # Linear spine.
        {"source": "sel", "target": "build_config", "target_slot": "manifest"},
        {"source": "build_config", "source_slot": "config",
         "target": "tile_export", "target_slot": "config"},
        {"source": "tile_export", "source_slot": "tiles",
         "target": "illum_correct", "target_slot": "tiles"},
        {"source": "illum_correct", "source_slot": "corrected",
         "target": "stitch", "target_slot": "corrected"},
        # Skip-link: stitch <- config (3 hops up from build_config).
        {"source": "build_config", "source_slot": "config",
         "target": "stitch", "target_slot": "config"},
        {"source": "stitch", "source_slot": "stitched",
         "target": "segment", "target_slot": "stitched"},
        # quantify fan-in: masks immediate + stitched 2-hop skip.
        {"source": "segment", "source_slot": "masks",
         "target": "quantify", "target_slot": "masks"},
        {"source": "stitch", "source_slot": "stitched",
         "target": "quantify", "target_slot": "stitched"},
        # export_final 3-input fan-in.
        {"source": "quantify", "source_slot": "measurements",
         "target": "export_final", "target_slot": "measurements"},
        {"source": "stitch", "source_slot": "stitched",
         "target": "export_final", "target_slot": "stitched"},
        {"source": "segment", "source_slot": "masks",
         "target": "export_final", "target_slot": "masks"},
    ]
    return nodes, links


def _node_run_output(node_method: str) -> RunOutput:
    """Return the latest RunOutput for a method node (by Method.name)."""
    with get_session() as session:
        stmt = (
            select(RunOutput)
            .join(Run, RunOutput.run_id == Run.id)
            .join(Method, Run.method_id == Method.id)
            .where(Method.name == node_method)
            .where(Run.status == "completed")
        )
        rows = session.exec(stmt).all()
    assert rows, f"no completed RunOutput for method {node_method!r}"
    return rows[-1]


@workflow(
    purpose="Imaging marquee: end-to-end byte integrity (US-1) + correct skip-link "
            "source attribution (US-2) across the 7-node fan-in DAG",
    inputs="7-node imaging DAG with 4 skip-links, run containerized via run_pipeline",
    outputs="every output's content_hash == md5(staging) == md5(cache); export_final "
            "rows correctly attributed to each wired slot with distinguishable skip-link content",
)
def test_imaging_skip_link_provenance(imaging_pipeline_factory, register_imaging_methods):
    project_dir = register_imaging_methods

    s = Step(step_num=1, name="Seed root sample + build the 7-node DAG",
             purpose="input_selector seeds build_config's manifest; skip-links wire stitch/quantify/export_final")
    _create_sample_csv(project_dir, "img_sample", num_rows=3)
    nodes, links = _imaging_pipeline_nodes_and_links()
    pipeline_path = imaging_pipeline_factory(
        name="imaging_marquee", nodes=nodes, links=links, samples=[],
    )

    s = Step(step_num=2, name="Run the pipeline containerized",
             purpose="Execute all 7 nodes end-to-end via run_pipeline (no archive yet)")
    run_pipeline(
        pipeline_path=str(pipeline_path),
        project_root=str(project_dir),
        wfc_root=str(WFC_ROOT),
        cores=1,
        archive=False,
    )

    s = Step(step_num=3, name="US-1: archive then verify byte integrity",
             purpose="content_hash == md5(staging bytes) == md5(cache file) for every output")
    # NULL content_hash is the live deferred-archiving state; run the archive
    # pass BEFORE asserting integrity (per the pitch's edge case).
    archive_outputs(project_dir)
    with get_session() as session:
        outputs = session.exec(
            select(RunOutput)
            .join(Run, RunOutput.run_id == Run.id)
            .where(Run.status == "completed")
        ).all()
        # Snapshot the fields we need while the session is open.
        snap = [(ro.output_name, ro.artifact_path, ro.content_hash) for ro in outputs]
    assert snap, "no completed RunOutput rows after archive"
    for output_name, artifact_path, content_hash in snap:
        assert content_hash, f"{output_name}: content_hash not populated after archive"
        staging = Path(artifact_path)
        assert staging.exists(), f"{output_name}: staging artifact missing at {staging}"
        assert hash_path(staging) == content_hash, (
            f"{output_name}: md5(staging) != RunOutput.content_hash"
        )
        cache_file = _cache_path(project_dir, content_hash)
        assert cache_file.exists(), f"{output_name}: cache file missing at {cache_file}"
        assert hash_path(cache_file) == content_hash, (
            f"{output_name}: md5(cache file) != content_hash"
        )

    s = Step(step_num=4, name="US-2: export_final attributes every wired slot correctly",
             purpose="all 3 slots present; the two skip-link sources carry distinguishable lineage")
    final_ro = _node_run_output("export_final")
    final_path = Path(final_ro.artifact_path)
    with final_path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows, "export_final produced no rows"

    by_slot: dict[str, list[dict]] = {}
    for r in rows:
        by_slot.setdefault(r["source_slot"], []).append(r)

    # All three declared slots must be present and attributed.
    assert set(by_slot) == {"measurements", "stitched", "masks"}, (
        f"export_final fan-in attribution wrong: saw slots {sorted(by_slot)}, "
        f"expected measurements/stitched/masks"
    )

    # The two SKIP-LINK sources must carry DISTINGUISHABLE content: stitched
    # rows trace through 'stitch'; masks rows trace through 'segment'. If a
    # skip-link were mis-wired (both pointing at the same producer), these
    # lineage sets would collapse — content-addressed dedup hides identical
    # bytes, so distinguishable lineage is what makes a mis-route detectable.
    stitched_lineage = {r["lineage"] for r in by_slot["stitched"]}
    masks_lineage = {r["lineage"] for r in by_slot["masks"]}
    assert any("stitch" in lin for lin in stitched_lineage), (
        f"stitched slot rows do not trace through stitch: {stitched_lineage}"
    )
    assert any("segment" in lin for lin in masks_lineage), (
        f"masks slot rows do not trace through segment: {masks_lineage}"
    )
    assert stitched_lineage != masks_lineage, (
        "skip-link sources 'stitched' and 'masks' are byte-indistinguishable — "
        "a mis-route would be undetectable"
    )
