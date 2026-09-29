"""
Tests for project-root resolution.

wfc subprocesses can inherit a foreign cwd from Snakemake (under Windows UNC
paths it is ``C:\\Windows``), so no project path may be derived from the cwd.
``wfc.persistence.project_root()`` is the explicit resolver: it prefers the
``WFC_PROJECT_ROOT`` env var, falls back to walking upward for the
``.wfc/wf-canvas.toml`` marker, and raises if neither succeeds.
``_default_db_url`` routes through it, never through ``Path.cwd()``.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.fakes import (
    stub_cache_writers,
    stub_dvc_setup,
    stub_sample_health_loader,
)

from wfc.persistence import (
    project_root,
    reset_engine,
)
from wfc.persistence.engine import _default_db_url


def _make_project(root: Path) -> Path:
    """Create a minimal wfc project marker (.wfc/wf-canvas.toml)."""
    wfc_dir = root / ".wfc"
    wfc_dir.mkdir(parents=True, exist_ok=True)
    (wfc_dir / "wf-canvas.toml").write_text(
        '[project]\nname = "test"\n'
    )
    return root


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Each test starts with a clean env — no inherited WFC_PROJECT_ROOT / DATABASE_URL."""
    monkeypatch.delenv("WFC_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_engine()
    yield
    reset_engine()


# =============================================================================
# project_root() resolver
# =============================================================================

class TestProjectRootResolver:
    def test_env_var_wins(self, tmp_path, monkeypatch):
        """WFC_PROJECT_ROOT env var is the canonical override."""
        proj = _make_project(tmp_path / "real_project")
        foreign = tmp_path / "foreign_cwd"
        foreign.mkdir()
        monkeypatch.chdir(foreign)
        monkeypatch.setenv("WFC_PROJECT_ROOT", str(proj))

        assert project_root() == proj.resolve()

    def test_walks_up_to_find_marker(self, tmp_path, monkeypatch):
        """When env var not set, walk up from cwd looking for .wfc/wf-canvas.toml."""
        proj = _make_project(tmp_path / "proj")
        deep = proj / "a" / "b" / "c"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)

        assert project_root() == proj.resolve()

    def test_raises_when_no_marker_and_no_env(self, tmp_path, monkeypatch):
        """No env var, no marker found by walking up → raise, don't silently
        create .wfc/ in the wrong place."""
        nowhere = tmp_path / "nowhere"
        nowhere.mkdir()
        monkeypatch.chdir(nowhere)

        with pytest.raises(RuntimeError, match="project root"):
            project_root()

    def test_env_var_pointing_at_non_project_raises(self, tmp_path, monkeypatch):
        """WFC_PROJECT_ROOT must point at a real wfc project (have wf-canvas.toml)."""
        bogus = tmp_path / "bogus"
        bogus.mkdir()
        monkeypatch.setenv("WFC_PROJECT_ROOT", str(bogus))

        with pytest.raises(RuntimeError, match="wf-canvas.toml"):
            project_root()


# =============================================================================
# _default_db_url routes through project_root
# =============================================================================

class TestDatabaseRoutesThroughProjectRoot:
    def test_default_db_url_uses_project_root_not_cwd(self, tmp_path, monkeypatch):
        """cwd is foreign (e.g. C:\\Windows under cmd.exe UNC
        rejection), but WFC_PROJECT_ROOT points at the real project. The DB URL
        must point inside the project, and no stray .wfc/ may appear under cwd.
        """
        proj = _make_project(tmp_path / "real_project")
        foreign = tmp_path / "foreign_cwd"
        foreign.mkdir()
        monkeypatch.chdir(foreign)
        monkeypatch.setenv("WFC_PROJECT_ROOT", str(proj))

        url = _default_db_url()

        expected_db = proj.resolve() / ".wfc" / "wfc.db"
        assert url == f"sqlite:///{expected_db}"
        assert not (foreign / ".wfc").exists(), \
            "must not create .wfc/ under foreign cwd"


# =============================================================================
# End-to-end: real subprocess inherits foreign cwd + WFC_PROJECT_ROOT
# =============================================================================

class TestCLIRoutesThroughProjectRoot:
    """The functions behind CLI commands resolve the project root through
    wfc.persistence.project_root(), never Path.cwd(), before handing it to the
    DVC/provenance layer. On Windows-UNC-cmd.exe scenarios cwd is C:\\Windows,
    and a cwd-derived root makes `wfc restore-sample` fail with
    'No wfc project found at C:\\Windows'.

    These tests simulate the scenario by chdir-ing to a foreign tmp directory
    and setting WFC_PROJECT_ROOT to point at the real project, then verifying
    that the functions resolve the project root via wfc.persistence.project_root()
    and hand the real path to the layers they delegate to.
    """

    def _setup_foreign_cwd(self, tmp_path, monkeypatch):
        proj = _make_project(tmp_path / "real_project")
        foreign = tmp_path / "foreign_cwd"
        foreign.mkdir()
        monkeypatch.chdir(foreign)
        monkeypatch.setenv("WFC_PROJECT_ROOT", str(proj))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{proj / '.wfc' / 'wfc.db'}")
        return proj, foreign

    def test_restore_sample_uses_project_root_not_cwd(
        self, tmp_path, monkeypatch
    ):
        """restore_sample must pass the real project_root — not Path.cwd() —
        into provenance.restore_from_cache / pull_cache."""
        from wfc.persistence import Sample, get_session
        from wfc.storage import restore_sample

        proj, foreign = self._setup_foreign_cwd(tmp_path, monkeypatch)

        with get_session() as session:
            session.add(Sample(
                name="sample_x",
                source_path="/orig/data.csv",
                registered_path="data/samples/sample_x/data.csv",
                file_type="csv",
                registration_mode="copy",
                content_hash="abc123",
            ))
            session.commit()

        seen_roots: list[Path] = []

        def fake_restore(hash_val, dest, project_root):
            seen_roots.append(project_root)
            return True

        stub_cache_writers(monkeypatch, restore_from_cache=fake_restore)
        restore_sample(name="sample_x")

        assert seen_roots, "restore_from_cache was not called"
        assert Path(seen_roots[0]).resolve() == proj.resolve()
        assert Path(seen_roots[0]).resolve() != foreign.resolve()

    def test_restore_sample_creates_sentinel_under_project_root(
        self, tmp_path, monkeypatch
    ):
        """After a successful restore, wfc.storage.restore_sample must touch
        ``<project_root>/data/samples/<name>/.sample_ready`` so the Snakemake
        rule's declared output appears. The parent directory must be created
        if missing (init doesn't materialize data/samples/<name>/ eagerly).

        Writing the sentinel here, from the resolved project root, keeps it
        independent of the shell rule's cwd, which Windows UNC cmd.exe
        rewrites."""
        from wfc.persistence import Sample, get_session
        from wfc.storage import restore_sample

        proj, foreign = self._setup_foreign_cwd(tmp_path, monkeypatch)

        with get_session() as session:
            session.add(Sample(
                name="sample_sentinel",
                source_path="/orig/data.csv",
                registered_path="data/samples/sample_sentinel/data.csv",
                file_type="csv",
                registration_mode="copy",
                content_hash="abc123",
            ))
            session.commit()

        expected_sentinel = proj / "data" / "samples" / "sample_sentinel" / ".sample_ready"
        foreign_sentinel = foreign / "data" / "samples" / "sample_sentinel" / ".sample_ready"
        assert not expected_sentinel.exists()
        assert not expected_sentinel.parent.exists(), (
            "parent dir must not pre-exist — restore_sample is required to mkparents"
        )

        stub_cache_writers(monkeypatch,
                           restore_from_cache=lambda *a, **k: True)
        restore_sample(name="sample_sentinel")

        assert expected_sentinel.exists(), (
            f"sentinel not created at expected path {expected_sentinel}"
        )
        assert not foreign_sentinel.exists(), (
            "sentinel must not appear under foreign cwd"
        )

    def test_register_sample_uses_project_root_not_cwd(
        self, tmp_path, monkeypatch
    ):
        """register_sample's default project_root must come from
        wfc.persistence.project_root(), not Path.cwd()."""
        from wfc.registration import register_sample

        proj, foreign = self._setup_foreign_cwd(tmp_path, monkeypatch)
        src = tmp_path / "input.csv"
        src.write_text("a,b\n1,2\n")

        seen_roots: list[Path] = []

        def fake_ensure_dvc_ready(project_root):
            seen_roots.append(project_root)
            raise SystemExit(0)  # bail out — we only care about the root arg

        stub_dvc_setup(monkeypatch, ensure_ready=fake_ensure_dvc_ready)
        with pytest.raises(SystemExit):
            register_sample(name="sample_y", source_path=src)

        assert seen_roots, "ensure_dvc_ready was not called"
        assert Path(seen_roots[0]).resolve() == proj.resolve()


@pytest.mark.slow
def test_subprocess_with_foreign_cwd_honors_env_var(tmp_path):
    """Spawn a real python subprocess with cwd=foreign tempdir and
    WFC_PROJECT_ROOT=real project. Importing wfc.persistence and calling
    _default_db_url must use the project, not cwd. Mirrors the Snakemake
    shell-rule scenario.
    """
    proj = _make_project(tmp_path / "real_project")
    foreign = tmp_path / "foreign_cwd"
    foreign.mkdir()

    env = os.environ.copy()
    env["WFC_PROJECT_ROOT"] = str(proj)
    env.pop("DATABASE_URL", None)

    code = (
        "from wfc.persistence.engine import _default_db_url; "
        "print(_default_db_url())"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=foreign,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    out = result.stdout.strip().splitlines()
    expected_db = (proj.resolve() / ".wfc" / "wfc.db")
    assert str(expected_db) in out[0]
    assert not (foreign / ".wfc").exists()


# =============================================================================
# use_project(): another project bound for a block
# =============================================================================

def test_use_project_binds_database_and_root_together_then_restores(
    tmp_path, monkeypatch
):
    """Tier 1: inside ``use_project`` the process writes the other project's
    database and resolves the other project's root; on exit it is back on the
    project it held, with the same engine and none of the block's rows."""
    from sqlmodel import Session, select

    from wfc import layout
    from wfc.persistence import (
        Sample,
        bootstrap_engine,
        get_engine,
        get_session,
        use_project,
    )

    home = _make_project(tmp_path / "home").resolve()
    other = _make_project(tmp_path / "other").resolve()
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(home))
    home_engine = get_engine()

    with use_project(other):
        assert project_root() == other
        with get_session() as session:
            session.add(Sample(
                name="s1", source_path="s1.csv",
                registered_path="data/samples/s1/s1.csv", file_type="csv",
            ))
            session.commit()

    assert project_root() == home
    assert get_engine() is home_engine
    with get_session() as session:
        assert session.exec(select(Sample)).all() == []
    other_engine = bootstrap_engine(layout.database_url(other))
    try:
        with Session(other_engine) as session:
            assert [s.name for s in session.exec(select(Sample))] == ["s1"]
    finally:
        other_engine.dispose()


# =============================================================================
# check_samples(): the rows and the files come from the SAME project
# =============================================================================

def test_check_samples_reads_the_rows_of_the_project_it_was_given(
    tmp_path, monkeypatch
):
    """``check_samples(other)`` reports ``other``'s registry, not the process's.

    Every other input the check has is path-resolved against its argument —
    the config probe, and the cache-presence test each verdict turns on — so
    reading rows from the process-bound engine paired one project's registry
    with another project's files. ``wfc init`` builds the new project's
    database through a one-off engine and disposes of it, leaving the process
    bound wherever it was, so ``wfc init --dir <elsewhere>`` run from outside
    a project reported on a database it had not created.

    A row is written into one project's database to prove the later read
    reached that database and not the other — the reason this module holds
    its SAMPLE_ROW_ALLOWLIST entry.
    """
    from wfc import layout
    from wfc.execution.readiness import check_samples
    from wfc.persistence import Sample, bootstrap_engine, get_session

    home = _make_project(tmp_path / "home").resolve()
    other = _make_project(tmp_path / "other").resolve()
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(home))

    with get_session() as session:
        session.add(Sample(
            name="home_s", source_path="home_s.csv",
            registered_path="data/samples/home_s/home_s.csv",
            file_type="csv", content_hash="a" * 32,
        ))
        session.commit()
    bootstrap_engine(layout.database_url(other)).dispose()

    assert "1 registered" in check_samples(home).message

    result = check_samples(other)
    assert "No samples registered" in result.message, (
        "check_samples(other) reported the process-bound project's rows; the "
        f"row source and the path source must be the same project. Got: {result}"
    )
    assert result.status == "ok", result


def test_check_samples_outside_a_project_fails_and_touches_nothing(
    tmp_path, monkeypatch
):
    """No project, no registry to read, and no database created on the way out."""
    from wfc.execution.readiness import check_samples

    home = _make_project(tmp_path / "home").resolve()
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(home))
    bare = tmp_path / "bare"
    bare.mkdir()

    result = check_samples(bare)

    assert result.status == "fail", result
    assert "No wfc project here" in result.message
    assert "wfc init" in result.fix_hint
    assert not (bare / ".wfc").exists()


def test_check_samples_reports_a_missing_registry_rather_than_creating_one(
    tmp_path, monkeypatch
):
    """Binding the project must not make a health check create the database.

    ``use_project`` bootstraps the schema, so binding unconditionally would
    have the check CREATE the registry it came to report on and then call it
    empty. The path is tested first and the absence is reported as it is.
    """
    from wfc import layout
    from wfc.execution.readiness import check_samples

    home = _make_project(tmp_path / "home").resolve()
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(home))
    fresh = _make_project(tmp_path / "fresh").resolve()

    result = check_samples(fresh)

    assert result.status == "warn", result
    assert "does not exist" in result.message
    assert "wfc init" in result.fix_hint
    assert not layout.db_path(fresh).exists(), (
        "the check created the database it came to report on"
    )


def test_check_samples_warn_does_not_blame_a_database_that_is_fine(
    tmp_path, monkeypatch
):
    """The failed-read hint must not send the user after a healthy file.

    A read can fail while the database file is perfectly fine (here, a schema
    mismatch), so the hint points at the raised message as the cause and names
    the file only as what was read. A false accusation about a specific file
    is worse than no hint at all.
    """
    from wfc import layout
    from wfc.execution.readiness import check_samples
    from wfc.persistence import bootstrap_engine


    proj = _make_project(tmp_path / "proj").resolve()
    bootstrap_engine(layout.database_url(proj)).dispose()
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(proj))

    def _boom(session, project_dir):
        raise RuntimeError("no such column: sample.push_status")

    stub_sample_health_loader(monkeypatch, _boom)

    result = check_samples(proj)

    assert result.status == "warn", result
    assert "no such column" in result.message
    assert layout.db_path(proj).exists(), "the database file itself is fine"
    assert "message above" in result.fix_hint, result.fix_hint
    assert "names the failure" in result.fix_hint, result.fix_hint
    assert str(layout.db_path(proj)) in result.fix_hint
