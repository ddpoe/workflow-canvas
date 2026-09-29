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
    (3, "artifact-store", lambda: layout.artifact_store(ROOT), "/proj/.runs"),
    # 4 — Run archive dir: run id zero-padded to 8
    (4, "run-archive-dir", lambda: layout.run_archive_dir(ROOT, 42),
     "/proj/.runs/00000042"),
    # 5 — Workspace dir (vestigial derivation)
    (5, "workspace-dir", lambda: layout.workspace_dir(ROOT), "/proj/.runs/workspace"),
    (5, "workspace-target-dir",
     lambda: layout.workspace_target_dir(ROOT, "pipe-1", "n1", "s1", "default"),
     "/proj/.runs/workspace/pipe-1/n1/s1/default"),
    # 6 — Run sentinels: the directory, .complete and run_id.txt
    (6, "run-sentinel-dir",
     lambda: layout.run_sentinel_dir(ROOT, "pipe-1", "n1", "s1", "default"),
     "/proj/.runs/sentinels/pipe-1/n1/s1/default"),
    (6, "run-sentinel",
     lambda: layout.run_sentinel_path(ROOT, "pipe-1", "n1", "s1", "default"),
     "/proj/.runs/sentinels/pipe-1/n1/s1/default/.complete"),
    (6, "run-id-sidecar",
     lambda: layout.run_id_sidecar_path(ROOT, "pipe-1", "n1", "s1", "default"),
     "/proj/.runs/sentinels/pipe-1/n1/s1/default/run_id.txt"),
    # 7 — Pipeline run dir: frozen document, outcomes sidecar dir, log dir
    (7, "pipeline-run-dir", lambda: layout.pipeline_run_dir(ROOT, "pipe-1"),
     "/proj/.runs/pipelines/pipe-1"),
    (7, "pipeline-doc", lambda: layout.pipeline_doc_path(ROOT, "pipe-1"),
     "/proj/.runs/pipelines/pipe-1/pipeline.json"),
    (7, "pipeline-editable-doc",
     lambda: layout.pipeline_editable_doc_path(ROOT, "pipe-1"),
     "/proj/.runs/pipelines/pipe-1/pipeline.editable.json"),
    (7, "pipeline-outcomes-dir", lambda: layout.pipeline_outcomes_dir(ROOT, "pipe-1"),
     "/proj/.runs/pipelines/pipe-1/outcomes"),
    (7, "pipeline-run-logs-dir", lambda: layout.pipeline_run_logs_dir(ROOT, "pipe-1"),
     "/proj/.runs/pipelines/pipe-1/runs"),
    # 8 — Sample data dir and ready sentinel
    (8, "sample-dir", lambda: layout.sample_dir(ROOT, "s1"), "/proj/data/samples/s1"),
    (8, "sample-ready-sentinel", lambda: layout.sample_ready_sentinel(ROOT, "s1"),
     "/proj/data/samples/s1/.sample_ready"),
    # 9 — Methods and modules dirs
    (9, "method-script", lambda: layout.method_script_path(ROOT, "tile_export"),
     "/proj/methods/tile_export/tile_export.py"),
    (9, "methods-dir", lambda: layout.methods_dir(ROOT), "/proj/methods"),
    (9, "modules-dir", lambda: layout.modules_dir(ROOT), "/proj/modules"),
    # 10 — Env manifest
    (10, "env-manifest", lambda: layout.env_manifest_path(ROOT), "/proj/.wfc/envs.json"),
    # 11 — Env build context
    (11, "env-build-dir", lambda: layout.env_build_dir(ROOT, "image-io"),
     "/proj/.wfc/build/image-io"),
    # 12 — DVC cache entry: a known hash splits two-then-thirty
    (12, "dvc-cache-entry",
     lambda: layout.dvc_cache_entry(ROOT, "0123456789abcdef0123456789abcdef"),
     "/proj/.dvc/cache/files/md5/01/23456789abcdef0123456789abcdef"),
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
