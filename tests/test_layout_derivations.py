"""Layout unit: the literal-derivation family.

Rows 1-12 of ``docs/system/layout.json`` section ``catalog.literal-derivations``
proven by one parametrized family. Every expectation is a literal string.

The family hands the Layout package plain values — a root, identifiers, a
hash — and touches neither disk nor environment. Expectations are never
computed with the derivation under test: a path unit's tests are uniquely
exposed to that tautology, so each expected value is typed out.

Requirement: ``docs/system/layout.json``, section ``catalog.literal-derivations``.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from axiom_annotations import workflow

from wfc import layout

#: The known root every row derives from. Rootless on purpose: ``Path("/proj")``
#: stays a plain value on every platform, and ``as_posix()`` renders it as
#: ``/proj/...`` on Windows too.
ROOT = Path("/proj")

#: Row 2's expectation is the one platform-sensitive literal:
#: ``_default_db_url`` interpolates ``str(Path)``, which carries backslashes on
#: Windows, and ``test_default_db_url_uses_project_root_not_cwd`` pins that
#: form. Two literals, selected by platform — never computed.
_DB_URL = (
    "sqlite:///\\proj\\.wfc\\wfc.db" if os.name == "nt"
    else "sqlite:////proj/.wfc/wfc.db"
)

#: (row, cell id, derivation over the Layout package, literal expectation).
#: Paths are compared on their POSIX rendering; strings verbatim.
CELLS = [
    # 1 — State dir and marker
    (1, "state-dir", lambda: layout.state_dir(ROOT), "/proj/.wfc"),
    (1, "marker", lambda: layout.marker_path(ROOT), "/proj/.wfc/wf-canvas.toml"),
    # 2 — Database URL
    (2, "database-url", lambda: layout.database_url(ROOT), _DB_URL),
    # 3 — Artifact store
    (3, "artifact-store", lambda: layout.artifact_store(ROOT), "/proj/.wfc/runs"),
    (3, "artifact-store-relpath", lambda: layout.artifact_store_relpath(), ".wfc/runs"),
    (3, "sentinels-relpath", lambda: layout.sentinels_relpath(), ".wfc/runs/sentinels"),
    (3, "pipelines-relpath", lambda: layout.pipelines_relpath(), ".wfc/runs/pipelines"),
    (3, "restore-workspace", lambda: layout.restore_workspace_dir(ROOT),
     "/proj/.wfc/workspace"),
    (3, "restore-workspace-relpath", lambda: layout.restore_workspace_relpath(),
     ".wfc/workspace"),
    # 4 — Run archive dir: run id zero-padded to 8
    (4, "run-archive-dir", lambda: layout.run_archive_dir(ROOT, 42),
     "/proj/.wfc/runs/00000042"),
    # 4 — A method version's extracted code copy, keyed by its tree hash, and
    # the stamp beside it (outside the copy, so not one of its files)
    (4, "code-copy-dir", lambda: layout.code_copy_dir(ROOT, "4b825dc6"),
     "/proj/.wfc/runs/code/4b825dc6"),
    (4, "code-copy-stamp", lambda: layout.code_copy_stamp(ROOT, "4b825dc6"),
     "/proj/.wfc/runs/code/4b825dc6.verified"),
    # 4 — Integrity stamps: a cache object's, a restored sample's, and a
    # directory output's member list, all outside the entries they describe
    (4, "cache-object-stamp",
     lambda: layout.cache_object_stamp(ROOT, "0123456789abcdef0123456789abcdef.dir"),
     "/proj/.wfc/runs/stamps/objects/0123456789abcdef0123456789abcdef.dir.verified"),
    (4, "sample-stamp", lambda: layout.sample_stamp(ROOT, "s1"),
     "/proj/.wfc/runs/stamps/samples/s1.verified"),
    (4, "output-stamps-dir", lambda: layout.output_stamps_dir(ROOT, 42),
     "/proj/.wfc/runs/stamps/outputs/00000042"),
    (4, "output-members-stamp", lambda: layout.output_members_stamp(ROOT, 42, "tiles"),
     "/proj/.wfc/runs/stamps/outputs/00000042/tiles.members.json"),
    # 5 — Workspace dir (vestigial derivation)
    (5, "workspace-dir", lambda: layout.workspace_dir(ROOT), "/proj/.wfc/runs/workspace"),
    (5, "workspace-target-dir",
     lambda: layout.workspace_target_dir(ROOT, "pipe-1", "n1", "s1", "default"),
     "/proj/.wfc/runs/workspace/pipe-1/n1/s1/default"),
    # 6 — Run sentinels: the directory, .complete and run_id.txt
    (6, "run-sentinel-dir",
     lambda: layout.run_sentinel_dir(ROOT, "pipe-1", "n1", "s1", "default"),
     "/proj/.wfc/runs/sentinels/pipe-1/n1/s1/default"),
    (6, "run-sentinel",
     lambda: layout.run_sentinel_path(ROOT, "pipe-1", "n1", "s1", "default"),
     "/proj/.wfc/runs/sentinels/pipe-1/n1/s1/default/.complete"),
    (6, "run-id-sidecar",
     lambda: layout.run_id_sidecar_path(ROOT, "pipe-1", "n1", "s1", "default"),
     "/proj/.wfc/runs/sentinels/pipe-1/n1/s1/default/run_id.txt"),
    # 7 — Pipeline run dir: frozen document, outcomes sidecar dir, log dir
    (7, "pipeline-run-dir", lambda: layout.pipeline_run_dir(ROOT, "pipe-1"),
     "/proj/.wfc/runs/pipelines/pipe-1"),
    (7, "pipeline-doc", lambda: layout.pipeline_doc_path(ROOT, "pipe-1"),
     "/proj/.wfc/runs/pipelines/pipe-1/pipeline.json"),
    (7, "pipeline-editable-doc",
     lambda: layout.pipeline_editable_doc_path(ROOT, "pipe-1"),
     "/proj/.wfc/runs/pipelines/pipe-1/pipeline.editable.json"),
    (7, "pipeline-outcomes-dir", lambda: layout.pipeline_outcomes_dir(ROOT, "pipe-1"),
     "/proj/.wfc/runs/pipelines/pipe-1/outcomes"),
    (7, "pipeline-run-logs-dir", lambda: layout.pipeline_run_logs_dir(ROOT, "pipe-1"),
     "/proj/.wfc/runs/pipelines/pipe-1/runs"),
    (7, "pipeline-record", lambda: layout.pipeline_record_path(ROOT, "pipe-1"),
     "/proj/.wfc/runs/pipelines/pipe-1/pipeline_record.json"),
    (7, "pipeline-record-lock", lambda: layout.pipeline_record_lock_path(ROOT, "pipe-1"),
     "/proj/.wfc/runs/pipelines/pipe-1/pipeline_record.json.lock"),
    (7, "pipeline-close-out-lock",
     lambda: layout.pipeline_close_out_lock_path(ROOT, "pipe-1"),
     "/proj/.wfc/runs/pipelines/pipe-1/close-out.lock"),
    # 8 — Sample data dir and ready sentinel
    (8, "sample-dir", lambda: layout.sample_dir(ROOT, "s1"), "/proj/.wfc/workspace/samples/s1"),
    (8, "sample-ready-sentinel", lambda: layout.sample_ready_sentinel(ROOT, "s1"),
     "/proj/.wfc/workspace/samples/s1/.sample_ready"),
    # 9 — The method registry: module-qualified snapshots
    (9, "method-dir", lambda: layout.method_dir(ROOT, "imaging", "tile_export"),
     "/proj/.wfc/registry/methods/imaging/tile_export"),
    (9, "methods-dir", lambda: layout.methods_dir(ROOT), "/proj/.wfc/registry/methods"),
    # 9 — A method's version list, a sibling of its snapshot dir
    (9, "method-versions",
     lambda: layout.method_versions_path(ROOT, "imaging", "tile_export"),
     "/proj/.wfc/registry/methods/imaging/tile_export.versions.json"),
    # 9 — Where registration builds a snapshot before renaming it in
    (9, "registry-staging", lambda: layout.registry_staging_dir(ROOT),
     "/proj/.wfc/build/.registry-staging"),
    # 10 — Env manifest
    (10, "env-manifest", lambda: layout.env_manifest_path(ROOT), "/proj/.wfc/envs.json"),
    # 10 — The demo schema stamp, beside the env manifest
    (10, "demo-stamp", lambda: layout.demo_stamp_path(ROOT), "/proj/.wfc/demo.json"),
    # 11 — Env build context
    (11, "env-build-dir", lambda: layout.env_build_dir(ROOT, "image-io"),
     "/proj/.wfc/build/image-io"),
    # 12 — DVC cache entry: a known hash splits two-then-thirty
    (12, "dvc-cache-entry",
     lambda: layout.dvc_cache_entry(ROOT, "0123456789abcdef0123456789abcdef"),
     "/proj/.dvc/cache/files/md5/01/23456789abcdef0123456789abcdef"),
    # 15 — An env image's captures folder, its root-relative POSIX form, and
    # the captures root and env folder above it
    (15, "captures-dir", lambda: layout.captures_dir(ROOT), "/proj/.wfc/captures"),
    (15, "env-captures-dir", lambda: layout.env_captures_dir(ROOT, "image-io"),
     "/proj/.wfc/captures/image-io"),
    (15, "env-capture-dir",
     lambda: layout.env_capture_dir(ROOT, "image-io", "0123456789abcdef0123456789abcdef"),
     "/proj/.wfc/captures/image-io/0123456789abcdef0123456789abcdef"),
    (15, "env-capture-relpath",
     lambda: layout.env_capture_relpath("image-io", "0123456789abcdef0123456789abcdef"),
     ".wfc/captures/image-io/0123456789abcdef0123456789abcdef"),
    # 16 — The captures byte-exact line, in the project's .gitattributes
    (16, "captures-gitattributes-line", lambda: layout.CAPTURES_GITATTRIBUTES_LINE,
     ".wfc/captures/** -text"),
    (16, "registry-gitattributes-line", lambda: layout.REGISTRY_GITATTRIBUTES_LINE,
     ".wfc/registry/** -text"),
    (16, "gitattributes", lambda: layout.gitattributes_path(ROOT), "/proj/.gitattributes"),
    # 18 — The drafts tree under the artifact store: a draft's folder, its
    # pointer and latest draft run, one step's results and code copy, and the
    # shell/jupyter session scratch
    (18, "drafts-dir", lambda: layout.drafts_dir(ROOT), "/proj/.wfc/runs/drafts"),
    (18, "draft-dir", lambda: layout.draft_dir(ROOT, "imaging", "tile_export"),
     "/proj/.wfc/runs/drafts/imaging/tile_export"),
    (18, "draft-pointer", lambda: layout.draft_pointer_path(ROOT, "imaging", "tile_export"),
     "/proj/.wfc/runs/drafts/imaging/tile_export/draft.json"),
    (18, "draft-run", lambda: layout.draft_run_path(ROOT, "imaging", "tile_export"),
     "/proj/.wfc/runs/drafts/imaging/tile_export/draft-run.json"),
    (18, "draft-result-dir",
     lambda: layout.draft_result_dir(ROOT, "imaging", "tile_export", "n1", "s1"),
     "/proj/.wfc/runs/drafts/imaging/tile_export/n1/s1"),
    (18, "draft-code-dir",
     lambda: layout.draft_code_dir(ROOT, "imaging", "tile_export", "n1", "s1"),
     "/proj/.wfc/runs/drafts/imaging/tile_export/.code/n1/s1"),
    (18, "draft-session-dir", lambda: layout.draft_session_dir(ROOT, "imaging", "tile_export"),
     "/proj/.wfc/runs/drafts/imaging/tile_export/.session"),
    # 19 — The module colours file, beside the method registry
    (19, "module-colors", lambda: layout.module_colors_path(ROOT),
     "/proj/.wfc/registry/modules.json"),
    # 20 — The preview cache: the canvas's converted images, under the
    # state dir's cache folder
    (20, "preview-cache-dir", lambda: layout.preview_cache_dir(ROOT),
     "/proj/.wfc/cache/previews"),
    # 21 — The project's own pipelines folder, outside the state dir, and the
    # file a one-method run saves there
    (21, "project-pipelines-dir", lambda: layout.project_pipelines_dir(ROOT),
     "/proj/pipelines"),
    (21, "one-method-pipeline",
     lambda: layout.one_method_pipeline_path(ROOT, "imaging", "tile_export", "s1"),
     "/proj/pipelines/imaging.tile_export-s1.json"),
    (21, "one-method-pipeline-fingerprint",
     lambda: layout.one_method_pipeline_path(ROOT, "imaging", "tile_export", "s1",
                                             "0a1b2c3d"),
     "/proj/pipelines/imaging.tile_export-s1-0a1b2c3d.json"),
    (21, "named-pipeline",
     lambda: layout.named_pipeline_path(ROOT, "try-n3"),
     "/proj/pipelines/try-n3.json"),
]


def _render(value: object) -> str:
    """Render a derivation result for comparison against a literal.

    Args:
        value: A ``Path`` (rendered POSIX so one literal serves every
            platform) or a string (verbatim — the URL row is a string).

    Returns:
        The comparable string.
    """
    return value.as_posix() if isinstance(value, Path) else str(value)


@pytest.mark.parametrize(
    "row,derive,expected",
    [pytest.param(row, derive, expected, id=f"{row:02d}-{cell}")
     for row, cell, derive, expected in CELLS],
)
@workflow(purpose="Every literal-derivation row of the Layout catalog, handed "
                  "a plain root and plain identifiers, yields exactly its "
                  "literal expected string",
          inputs="A catalog row: a Layout derivation over plain values and "
                 "its literal expectation",
          outputs="Equality, or the row that drifted")
def test_literal_derivations(row, derive, expected):
    """One catalog row against its literal.

    No disk, no environment: the derivation is called with values and the
    result compared to a typed-out string.
    """
    assert _render(derive()) == expected, f"catalog row {row} drifted"


@pytest.mark.parametrize("name,rule", [
    ("", "empty"), ("   ", "empty"),
    *[(f"a{ch}b", f"holds {ch};") for ch in '/\\<>:"|?*'],
    ("run.", "ends with a dot or a space"), ("run ", "ends with a dot or a space"),
])
def test_an_unusable_pipeline_name_is_refused_naming_the_rule(name, rule):
    """Layout's pipeline-name rule: the refusal names the rule; no path is derived."""
    assert rule in (layout.pipeline_name_refusal(name) or "")
    with pytest.raises(ValueError, match="pipeline name"):
        layout.named_pipeline_path(ROOT, name)


@pytest.mark.parametrize("name", ["try-n3", "my run.v2", "imaging.tile_export-s1"])
def test_an_ordinary_pipeline_name_is_accepted(name):
    """Dots, spaces and hyphens inside a name are fine."""
    assert layout.pipeline_name_refusal(name) is None
