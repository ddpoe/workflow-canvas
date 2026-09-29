"""wfc export CLI + read-only cache protection + Canvas provider artifacts.

Covers:
  - Tier 3: export copy semantics (bytes identical, copy writable,
    --force, dest-directory placement, --all per-output naming).
  - Tier 3: --path prints exactly the cache path; the cache
    entry is read-only (write attempt fails); the archive sweep
    re-protects entries.
  - Tier 2: discovery / explicit-name error taxonomy — never a
    wrong path, never a file produced on error.
  - Tier 2: Canvas provider artifact methods resolve archived runs
    from the DVC cache (nothing left in .runs/).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from axiom_annotations import workflow, Step

from tests.fixtures.fakes import stub_export_engine
from tests.fixtures.routes import completed_run
from tests.harness.scenario import Behavior


# ---------------------------------------------------------------------------
# The run under export: produced by the route, archived by the real pass
# ---------------------------------------------------------------------------

def _run_with_outputs(
    tmp_project,
    monkeypatch,
    outputs: dict[str, tuple[str, str | dict[str, str]]],
    *,
    module_name: str = "expmod",
    method_name: str = "expmeth",
):
    """A completed run through the route, one output slot per entry.

    ``outputs`` maps a slot to ``(published filename, content)``; a mapping
    as the content declares a directory slot and its children. The collect
    phase records the rows un-archived (content_hash NULL); callers archive
    via ``archive_outputs`` or ``wfc cache archive`` so the real writer /
    protection path is exercised.

    Args:
        tmp_project: The temporary project root fixture.
        monkeypatch: The test's monkeypatch.
        outputs: Slot -> (filename, content) for each output.
        module_name: Module name; override when one test runs two methods.
        method_name: Method name, likewise.

    Returns:
        The driven run; ``.run_id`` is the completed run's id.
    """
    return completed_run(
        tmp_project, monkeypatch=monkeypatch,
        method=method_name, module=module_name, sample="s1",
        outputs={slot: ("directory" if isinstance(content, dict)
                        else Path(filename).suffix)
                 for slot, (filename, content) in outputs.items()},
        output_files={slot: filename for slot, (filename, _) in outputs.items()},
        behavior=Behavior(outputs={slot: content
                                   for slot, (_, content) in outputs.items()}),
    )


def _cache_path_for(tmp_project, run_id: int, output_name: str) -> Path:
    """Return the DVC cache path recorded for a run output's content hash."""
    from sqlmodel import select

    from wfc.persistence import get_session, RunOutput

    with get_session() as session:
        # By slot: the collect phase records ``output_name`` with the
        # published file's suffix, and the slot is what the caller declared.
        ro = next(
            r for r in session.exec(
                select(RunOutput).where(RunOutput.run_id == run_id)
            ).all()
            if r.slot == output_name
        )
        content_hash = ro.content_hash
    assert content_hash, f"output {output_name!r} was not archived"
    return (
        tmp_project / ".dvc" / "cache" / "files" / "md5"
        / content_hash[:2] / content_hash[2:]
    )


# ---------------------------------------------------------------------------
# Tier 3: export copy semantics
# ---------------------------------------------------------------------------

@workflow(
    purpose="A researcher exports a run output as a file they own: the copy's "
            "bytes are identical to the archived output, the copy is writable "
            "(the copy2-preserves-0444 trap), an existing destination is "
            "refused without --force, a directory destination places the file "
            "under its original name, and --all exports every output under "
            "predictable per-output names.",
)
def test_export_copy_semantics(cli, tmp_project, monkeypatch):
    口 = Step(step_num=1, name="Run and archive a run",
             purpose="Completed run with a file output and a directory output, "
                     "archived through the real cache writer")
    payload = b"mask-bytes-" * 100
    run_id = _run_with_outputs(tmp_project, monkeypatch, {
        "masks": ("masks.tif", payload.decode()),
        "tiles": ("tiles", {"tile_0.png": "PNG-t0", "tile_1.png": "PNG-t1"}),
    }).run_id

    from wfc.storage import archive_outputs
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
    assert tile.read_bytes() == b"PNG-t0"
    with open(tile, "ab") as fh:  # directory-output copies writable too
        fh.write(b"!")


# ---------------------------------------------------------------------------
# Tier 3: --path mode + read-only cache protection
# ---------------------------------------------------------------------------

@workflow(
    purpose="A researcher gets a path to a huge output without duplicating "
            "it: `wfc export --path` prints exactly the cache path on stdout "
            "(script-friendly) with a read-only warning on stderr; opening "
            "the path for write fails instead of corrupting the store; the "
            "archive sweep re-protects entries that lost their guard.",
)
def test_export_path_and_readonly_cache(cli, tmp_project, monkeypatch):
    口 = Step(step_num=1, name="Run, then archive via `wfc cache archive`",
             purpose="The real CLI archive pass protects the fresh entry")
    run_id = _run_with_outputs(
        tmp_project, monkeypatch, {"big": ("big.parquet", "huge-output" * 64)}
    ).run_id

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
# Tier 2: discovery and safe failure — never wrong bytes
# ---------------------------------------------------------------------------

@workflow(
    purpose="Wrong or missing output names never produce a path or file: "
            "bare `wfc export <id>` and a mistyped name exit nonzero listing "
            "the run's actual output names; an un-archived output points at "
            "`wfc cache archive`; an unknown run errors clearly.",
)
def test_export_errors_and_discovery(cli, tmp_project, monkeypatch):
    from wfc.persistence import get_session, RunOutput
    from wfc.storage import archive_outputs

    run_id = _run_with_outputs(
        tmp_project, monkeypatch, {"alpha": ("alpha.csv", "a,b\n1,2\n")}
    ).run_id
    archive_outputs(tmp_project, run_id=run_id)

    # Add a second, NEVER-archived output (content_hash NULL).
    beta = tmp_project / "staging" / "beta.csv"
    beta.parent.mkdir(parents=True)
    beta.write_text("c\n3\n")
    with get_session() as session:
        session.add(RunOutput(
            run_id=run_id, slot="beta", output_name="beta",
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


def test_export_all_audit_row_exports_source_outputs(cli, tmp_project, monkeypatch):
    """`wfc export <audit-id> --all` enumerates and resolves the SOURCE
    run's outputs — cache-hit audit rows own no RunOutput rows."""
    from wfc.storage import archive_outputs

    outputs = {"alpha": ("alpha.csv", "a,b\n1,2\n")}
    source = _run_with_outputs(tmp_project, monkeypatch, outputs)
    # The same declaration again is a cache hit: the audit row the claim's
    # cache-hit branch writes -- completed, pointing at the source, owning
    # no outputs of its own.
    audit = _run_with_outputs(tmp_project, monkeypatch, outputs)
    assert audit.run_row["cache_source_run_id"] == source.run_id
    assert audit.output_rows == []
    source_id, audit_id = source.run_id, audit.run_id
    archive_outputs(tmp_project, run_id=source_id)

    result = cli("export", str(audit_id), "--all", "--path")
    assert result.returncode == 0
    cache_path = _cache_path_for(tmp_project, source_id, "alpha")
    assert "alpha" in result.stdout
    assert str(cache_path) in result.stdout


@workflow(
    purpose="One unresolvable output among several stops the whole export: "
            "`wfc export --all` over a run with an un-archived row exits "
            "nonzero with the archive hint on stderr, prints nothing on "
            "stdout and creates no destination directory, in copy mode and "
            "in --path mode alike; and a destination whose targets already "
            "exist is refused by name without --force, overwriting nothing.",
)
def test_export_all_is_all_or_nothing(cli, tmp_project, monkeypatch):
    from wfc.persistence import get_session, RunOutput
    from wfc.storage import archive_outputs

    口 = Step(step_num=1, name="One un-archived row among several",
             purpose="Resolution fails before anything is printed or written, "
                     "in copy mode and in --path mode alike")
    run_id = _run_with_outputs(
        tmp_project, monkeypatch, {"alpha": ("alpha.csv", "a,b\n1,2\n")}
    ).run_id
    archive_outputs(tmp_project, run_id=run_id)

    # Second row, never archived (content_hash NULL) — resolvable only
    # after `wfc cache archive`.
    beta = tmp_project / "staging" / "beta.csv"
    beta.parent.mkdir(parents=True)
    beta.write_text("c\n3\n")
    with get_session() as session:
        session.add(RunOutput(
            run_id=run_id, slot="beta", output_name="beta",
            artifact_path=str(beta), artifact_type="method_file",
        ))
        session.commit()

    dest_dir = tmp_project / "exports" / "partial"
    result = cli("export", str(run_id), "--all", str(dest_dir))
    assert result.returncode == 1
    assert "wfc cache archive" in result.stderr
    assert result.stdout == ""
    # The resolve phase fails before the destination is created — no
    # half-exported directory holding only the archived output.
    assert not dest_dir.exists()

    result = cli("export", str(run_id), "--all", "--path")
    assert result.returncode == 1
    assert "wfc cache archive" in result.stderr
    assert result.stdout == ""

    口 = Step(step_num=2, name="Existing targets are refused by name",
             purpose="--all without --force lists every colliding target and "
                     "overwrites none of them")
    second_id = _run_with_outputs(
        tmp_project, monkeypatch,
        {"gamma": ("gamma.csv", "g\n1\n"), "delta": ("delta.csv", "d\n2\n")},
        module_name="expmod2", method_name="expmeth2",
    ).run_id
    archive_outputs(tmp_project, run_id=second_id)

    occupied = tmp_project / "exports" / "occupied"
    occupied.mkdir(parents=True)
    (occupied / "gamma.csv").write_text("MINE\n")
    (occupied / "delta.csv").write_text("MINE TOO\n")

    result = cli("export", str(second_id), "--all", str(occupied))
    assert result.returncode == 1
    assert "--force" in result.stderr
    assert "gamma.csv" in result.stderr and "delta.csv" in result.stderr
    assert (occupied / "gamma.csv").read_text() == "MINE\n"
    assert (occupied / "delta.csv").read_text() == "MINE TOO\n"


# ---------------------------------------------------------------------------
# Tier 2: argument validation happens before anything resolves
# ---------------------------------------------------------------------------

@workflow(
    purpose="The export branch refuses four argument shapes before it reaches "
            "the engine: `--all` with an output name and a destination, "
            "`--path` with a destination, an output name with no destination "
            "and no `--path`, and `--all` with neither a destination nor "
            "`--path`. Each exits 2 with its own message, nothing is "
            "resolved, and no destination is written.",
)
def test_export_argument_shapes_are_refused_before_resolving(
    cli, tmp_project, monkeypatch
):
    # The branch resolves the engine with `from .export import export_output`
    # at call time, so the package attribute is what it reaches. Anything
    # recorded here is an argument shape that got past validation.
    reached: list[dict] = []
    stub_export_engine(monkeypatch,
                       lambda **kwargs: reached.append(kwargs) or 0)

    dest = tmp_project / "exports" / "refused"
    refused = [
        (
            ("export", "1", "masks", str(dest), "--all"),
            "--all exports every output; do not pass a slot",
        ),
        (
            ("export", "1", "masks", str(dest), "--path"),
            "--path cannot be combined with a destination",
        ),
        (
            ("export", "1", "masks"),
            "destination required (or use --path to print the cache path)",
        ),
        (
            ("export", "1", "--all"),
            "--all requires a destination directory (or --path)",
        ),
    ]

    for argv, message in refused:
        result = cli(*argv)
        assert result.returncode == 2, f"{argv} -> {result!r}"
        assert message in result.stderr, f"{argv} -> {result.stderr!r}"
        assert result.stdout == ""

    assert reached == [], (
        "the export engine ran for a refused argument shape — validation must "
        "come first"
    )
    assert not dest.exists()


# ---------------------------------------------------------------------------
# Tier 2: Canvas provider artifact surfaces resolve from the cache
# ---------------------------------------------------------------------------

@workflow(
    purpose="The Canvas History-tab export flow and RunDetailPanel artifact "
            "browser work for archived runs: get_artifacts, "
            "list_artifacts, and get_artifact_path resolve RunOutput rows to "
            "DVC cache paths (frozen dict shapes, no .runs/ globbing, local "
            "cache only).",
)
def test_provider_artifacts_resolve_from_cache(cli, tmp_project, monkeypatch):
    from wfc.canvas.wfc_provider import WfcProvider
    from wfc.storage import archive_outputs

    run = _run_with_outputs(tmp_project, monkeypatch, {
        "report": ("report.csv", "x,y\n1,2\n"),
        "tiles": ("tiles", {"t0.png": "PNG-a", "t1.png": "PNG-bb"}),
    })
    run_id = run.run_id
    archive_outputs(tmp_project, run_id=run_id)

    # Post-archive reality: the archive pass copies the run archive's files
    # into the cache and leaves them where the collect phase wrote them, so
    # a provider that globbed the run archive would still find files here.
    # Every path asserted below is therefore checked to be a cache path.
    assert [p for p in run.archive_dir.rglob("*") if p.is_file()]

    provider = WfcProvider(str(tmp_project))
    rid = str(run_id)

    # get_artifacts: frozen dict shape, cache-resolved file_path.
    arts = provider.get_artifacts([rid])
    assert {a["artifact_name"] for a in arts} == {
        "report.csv", "tiles/t0.png", "tiles/t1.png",
    }
    # A file output resolves to its cache object; a directory output's
    # members to its checkout (built from the cache); never the run archive.
    from wfc import layout
    cache_root = tmp_project / ".dvc" / "cache"
    checkouts = layout.checkouts_dir(tmp_project)
    for a in arts:
        assert set(a) == {
            "run_id", "run_name", "method", "artifact_name",
            "file_path", "extension", "size_bytes",
        }
        assert a["run_id"] == rid
        assert a["method"] == "expmeth"
        assert Path(a["file_path"]).exists()
        root = checkouts if a["artifact_name"].startswith("tiles/") else cache_root
        assert str(root) in a["file_path"], a
        assert str(run.archive_dir) not in a["file_path"]
        assert a["size_bytes"] > 0

    # Extension filter tests the ACTUAL file suffix (a CSV-only export).
    csvs = provider.get_artifacts([rid], extensions=["csv"])
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
    assert member is not None and member.read_bytes() == b"PNG-a"
    assert provider.get_artifact_path(rid, "missing.bin") is None
