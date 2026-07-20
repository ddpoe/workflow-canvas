"""wfc export CLI + read-only cache protection + Canvas provider repair.

Covers (per Architect test plan, cycle pev-2026-07-09-wfc-export-cli):
  - US-1 Tier 3: export copy semantics (bytes identical, copy writable,
    --force, dest-directory placement, --all per-output naming).
  - US-2 + US-5 Tier 3: --path prints exactly the cache path; the cache
    entry is read-only (write attempt fails); the archive sweep
    re-protects entries.
  - US-3 Tier 2: discovery / explicit-name error taxonomy — never a
    wrong path, never a file produced on error.
  - US-4 Tier 2: Canvas provider artifact methods resolve archived runs
    from the DVC cache (post-ADR-018, nothing left in .runs/).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from axiom_annotations import workflow, Step


# ---------------------------------------------------------------------------
# Seeding helpers (mirrors tests/integration/test_cache_provenance.py::
# _seed_archived_output — ORM rows + the real archive pass, never hand DDL)
# ---------------------------------------------------------------------------

def _seed_run(tmp_project, outputs: dict[str, Path]) -> int:
    """Create a completed Run with one RunOutput per (name -> staging path).

    Rows are left un-archived (content_hash NULL); callers archive via
    ``archive_outputs`` or ``wfc cache archive`` so the real writer /
    protection path is exercised.
    """
    from wfc.database import get_session
    from wfc.models import Method, Module, Run, RunOutput

    with get_session() as session:
        module = Module(name="expmod", description="x")
        session.add(module)
        session.commit()
        session.refresh(module)
        method = Method(name="expmeth", module_id=module.id, env="container:demo")
        session.add(method)
        session.commit()
        session.refresh(method)
        run = Run(method_id=method.id, status="completed", sample="s1")
        session.add(run)
        session.commit()
        session.refresh(run)
        for name, staging in outputs.items():
            artifact_type = (
                "method_directory" if staging.is_dir() else "method_file"
            )
            session.add(RunOutput(
                run_id=run.id, output_name=name,
                artifact_path=str(staging), artifact_type=artifact_type,
            ))
        session.commit()
        return run.id


def _cache_path_for(tmp_project, run_id: int, output_name: str) -> Path:
    """Return the DVC cache path recorded for a run output's content hash."""
    from sqlmodel import select

    from wfc.database import get_session
    from wfc.models import RunOutput

    with get_session() as session:
        ro = next(
            r for r in session.exec(
                select(RunOutput).where(RunOutput.run_id == run_id)
            ).all()
            if r.output_name == output_name
        )
        content_hash = ro.content_hash
    assert content_hash, f"output {output_name!r} was not archived"
    return (
        tmp_project / ".dvc" / "cache" / "files" / "md5"
        / content_hash[:2] / content_hash[2:]
    )


# ---------------------------------------------------------------------------
# US-1 Tier 3: export copy semantics
# ---------------------------------------------------------------------------

@workflow(
    purpose="A researcher exports a run output as a file they own: the copy's "
            "bytes are identical to the archived output, the copy is writable "
            "(the copy2-preserves-0444 trap), an existing destination is "
            "refused without --force, a directory destination places the file "
            "under its original name, and --all exports every output under "
            "predictable per-output names.",
)
def test_export_copy_semantics(cli, tmp_project):
    口 = Step(step_num=1, name="Seed and archive a run",
             purpose="Completed run with a file output and a directory output, "
                     "archived through the real cache writer")
    payload = b"mask-bytes-" * 100
    masks = tmp_project / "staging" / "masks.tif"
    masks.parent.mkdir(parents=True)
    masks.write_bytes(payload)
    tiles = tmp_project / "staging" / "tiles"
    tiles.mkdir()
    (tiles / "tile_0.png").write_bytes(b"\x89PNG-t0")
    (tiles / "tile_1.png").write_bytes(b"\x89PNG-t1")
    run_id = _seed_run(tmp_project, {"masks": masks, "tiles": tiles})

    from wfc.provenance import archive_outputs
    archive_outputs(tmp_project, run_id=run_id)

    口 = Step(step_num=2, name="Export one output to a file",
             purpose="Copy comes out byte-identical and writable")
    dest = tmp_project / "exports" / "my_masks.tif"
    result = cli("export", str(run_id), "masks", str(dest))
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == payload
    # Writable — the whole point of an export (cache entries are 0444 and
    # copy2 preserves mode bits; export must chmod the copy back).
    with open(dest, "ab") as fh:
        fh.write(b"!")

    口 = Step(step_num=3, name="Refuse to overwrite without --force",
             purpose="Existing destination file is never silently replaced")
    result = cli("export", str(run_id), "masks", str(dest))
    assert result.returncode != 0
    assert "--force" in result.stderr
    # dest untouched by the refused export (still has our appended byte)
    assert dest.read_bytes() == payload + b"!"

    result = cli("export", str(run_id), "masks", str(dest), "--force")
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == payload

    口 = Step(step_num=4, name="Directory destination placement",
             purpose="An existing directory dest receives the file under "
                     "its original name")
    dest_dir = tmp_project / "exports" / "into_dir"
    dest_dir.mkdir(parents=True)
    result = cli("export", str(run_id), "masks", str(dest_dir))
    assert result.returncode == 0, result.stderr
    assert (dest_dir / "masks.tif").read_bytes() == payload

    口 = Step(step_num=5, name="Export --all into a directory",
             purpose="File outputs land as <name><suffix>, directory outputs "
                     "as <dest>/<name>/, all writable")
    all_dir = tmp_project / "exports" / "all"
    result = cli("export", str(run_id), "--all", str(all_dir))
    assert result.returncode == 0, result.stderr
    assert (all_dir / "masks.tif").read_bytes() == payload
    tile = all_dir / "tiles" / "tile_0.png"
    assert tile.read_bytes() == b"\x89PNG-t0"
    with open(tile, "ab") as fh:  # directory-output copies writable too
        fh.write(b"!")


# ---------------------------------------------------------------------------
# US-2 + US-5 Tier 3: --path mode + read-only cache protection
# ---------------------------------------------------------------------------

@workflow(
    purpose="A researcher gets a path to a huge output without duplicating "
            "it: `wfc export --path` prints exactly the cache path on stdout "
            "(script-friendly) with a read-only warning on stderr; opening "
            "the path for write fails instead of corrupting the store; the "
            "archive sweep re-protects entries that lost their guard.",
)
def test_export_path_and_readonly_cache(cli, tmp_project):
    口 = Step(step_num=1, name="Seed and archive via `wfc cache archive`",
             purpose="The real CLI archive pass protects the fresh entry")
    staging = tmp_project / "staging" / "big.parquet"
    staging.parent.mkdir(parents=True)
    staging.write_bytes(b"huge-output" * 64)
    run_id = _seed_run(tmp_project, {"big": staging})

    result = cli("cache", "archive")
    assert result.returncode == 0, result.stderr
    cache_path = _cache_path_for(tmp_project, run_id, "big")
    assert cache_path.exists()

    口 = Step(step_num=2, name="Export --path prints exactly the cache path",
             purpose="stdout carries only the path; the warning goes to stderr")
    result = cli("export", str(run_id), "big", "--path")
    assert result.returncode == 0, result.stderr
    # Exactly one line on stdout: the path (script-friendly). Compared via
    # samefile — Windows resolves the temp dir's username segment with
    # different casing than the fixture path (same file either way).
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1
    assert Path(lines[0]).samefile(cache_path)
    assert "read-only" in result.stderr.lower()

    口 = Step(step_num=3, name="Writing to the printed path fails",
             purpose="Freshly archived entries are read-only — the df.to_csv "
                     "footgun raises instead of corrupting the cache")
    with pytest.raises(PermissionError):
        open(cache_path, "ab")

    口 = Step(step_num=4, name="Archive sweep re-protects stripped entries",
             purpose="A previously-writable entry becomes read-only again on "
                     "the next archive pass")
    os.chmod(cache_path, 0o644)
    with open(cache_path, "ab"):
        pass  # writable again — protection stripped
    result = cli("cache", "archive")  # nothing to archive; sweep still runs
    assert result.returncode == 0, result.stderr
    with pytest.raises(PermissionError):
        open(cache_path, "ab")


# ---------------------------------------------------------------------------
# US-3 Tier 2: discovery and safe failure — never wrong bytes
# ---------------------------------------------------------------------------

@workflow(
    purpose="Wrong or missing output names never produce a path or file: "
            "bare `wfc export <id>` and a mistyped name exit nonzero listing "
            "the run's actual output names; an un-archived output points at "
            "`wfc cache archive`; an unknown run errors clearly.",
)
def test_export_errors_and_discovery(cli, tmp_project):
    from wfc.database import get_session
    from wfc.models import RunOutput
    from wfc.provenance import archive_outputs

    alpha = tmp_project / "staging" / "alpha.csv"
    alpha.parent.mkdir(parents=True)
    alpha.write_text("a,b\n1,2\n")
    run_id = _seed_run(tmp_project, {"alpha": alpha})
    archive_outputs(tmp_project, run_id=run_id)

    # Add a second, NEVER-archived output (legacy row: content_hash NULL).
    beta = tmp_project / "staging" / "beta.csv"
    beta.write_text("c\n3\n")
    with get_session() as session:
        session.add(RunOutput(
            run_id=run_id, output_name="beta",
            artifact_path=str(beta), artifact_type="method_file",
        ))
        session.commit()

    # Bare run id: the error IS the discovery listing.
    result = cli("export", str(run_id))
    assert result.returncode != 0
    assert "alpha" in result.stderr and "beta" in result.stderr
    assert result.stdout == ""

    # Wrong name: nonzero, lists available names, prints no path.
    result = cli("export", str(run_id), "nope", "--path")
    assert result.returncode != 0
    assert "alpha" in result.stderr and "beta" in result.stderr
    assert result.stdout == ""

    # Wrong name in copy mode: no file is ever produced.
    dest = tmp_project / "exports" / "never.csv"
    result = cli("export", str(run_id), "nope", str(dest))
    assert result.returncode != 0
    assert not dest.exists()

    # Un-archived output: distinct error pointing at `wfc cache archive` —
    # never an artifact_path fallback.
    result = cli("export", str(run_id), "beta", "--path")
    assert result.returncode != 0
    assert "wfc cache archive" in result.stderr
    assert result.stdout == ""

    # Unknown run id.
    result = cli("export", "99999", "alpha", "--path")
    assert result.returncode != 0
    assert "99999" in result.stderr
    assert result.stdout == ""


def test_export_all_audit_row_exports_source_outputs(cli, tmp_project):
    """`wfc export <audit-id> --all` enumerates and resolves the SOURCE
    run's outputs — cache-hit audit rows own no RunOutput rows."""
    from wfc.database import get_session
    from wfc.models import Run
    from wfc.provenance import archive_outputs

    alpha = tmp_project / "staging" / "alpha.csv"
    alpha.parent.mkdir(parents=True)
    alpha.write_text("a,b\n1,2\n")
    source_id = _seed_run(tmp_project, {"alpha": alpha})
    archive_outputs(tmp_project, run_id=source_id)

    with get_session() as session:
        source = session.get(Run, source_id)
        audit = Run(
            method_id=source.method_id,
            status="completed",
            sample="s1",
            cache_source_run_id=source_id,
        )
        session.add(audit)
        session.commit()
        session.refresh(audit)
        audit_id = audit.id

    result = cli("export", str(audit_id), "--all", "--path")
    assert result.returncode == 0
    cache_path = _cache_path_for(tmp_project, source_id, "alpha")
    assert "alpha" in result.stdout
    assert str(cache_path) in result.stdout


# ---------------------------------------------------------------------------
# US-4 Tier 2: Canvas provider artifact surfaces resolve from the cache
# ---------------------------------------------------------------------------

@workflow(
    purpose="The Canvas History-tab export flow and RunDetailPanel artifact "
            "browser work for post-ADR-018 archived runs: get_artifacts, "
            "list_artifacts, and get_artifact_path resolve RunOutput rows to "
            "DVC cache paths (frozen dict shapes, no .runs/ globbing, local "
            "cache only).",
)
def test_provider_artifacts_resolve_from_cache(cli, tmp_project):
    import shutil

    from wfc.canvas.wfc_provider import WfcProvider
    from wfc.provenance import archive_outputs

    report = tmp_project / "staging" / "report.csv"
    report.parent.mkdir(parents=True)
    report.write_text("x,y\n1,2\n")
    tiles = tmp_project / "staging" / "tiles"
    tiles.mkdir()
    (tiles / "t0.png").write_bytes(b"\x89PNG-a")
    (tiles / "t1.png").write_bytes(b"\x89PNG-bb")
    run_id = _seed_run(tmp_project, {"report": report, "tiles": tiles})
    archive_outputs(tmp_project, run_id=run_id)

    # Post-ADR-018 reality: staging consumed, nothing under .runs/{id:08d}.
    shutil.rmtree(tmp_project / "staging")
    assert not (tmp_project / ".runs" / f"{run_id:08d}").exists()

    provider = WfcProvider(str(tmp_project))
    rid = str(run_id)

    # get_artifacts: frozen dict shape, cache-resolved file_path.
    arts = provider.get_artifacts([rid])
    assert {a["artifact_name"] for a in arts} == {
        "report.csv", "tiles/t0.png", "tiles/t1.png",
    }
    cache_root = tmp_project / ".dvc" / "cache"
    for a in arts:
        assert set(a) == {
            "run_id", "run_name", "method", "artifact_name",
            "file_path", "extension", "size_bytes",
        }
        assert a["run_id"] == rid
        assert a["method"] == "expmeth"
        assert Path(a["file_path"]).exists()
        assert str(cache_root) in a["file_path"]
        assert a["size_bytes"] > 0

    # Extension filter tests the ACTUAL file suffix (csv alias endpoint).
    csvs = provider.get_csv_artifacts([rid])
    assert [a["artifact_name"] for a in csvs] == ["report.csv"]

    # list_artifacts: dir row first with count/children, frozen shapes.
    listing = provider.list_artifacts(rid)
    dir_row = listing[0]
    assert dir_row["name"] == "tiles/"
    assert dir_row["type"] == "dir"
    assert dir_row["count"] == 2
    assert {c["name"] for c in dir_row["children"]} == {"t0.png", "t1.png"}
    file_row = next(a for a in listing if a["type"] == "file")
    assert file_row["name"] == "report.csv"
    assert file_row["extension"] == "csv"
    assert file_row["size"] > 0

    # get_artifact_path: per-file download endpoint feeder.
    p = provider.get_artifact_path(rid, "report.csv")
    assert p is not None and p.exists()
    member = provider.get_artifact_path(rid, "tiles/t0.png")
    assert member is not None and member.read_bytes() == b"\x89PNG-a"
    assert provider.get_artifact_path(rid, "missing.bin") is None
