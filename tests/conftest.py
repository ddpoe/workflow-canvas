"""
Suite-wide pytest fixtures and skip-guards.

Fixtures include tmp_project (a project root with DB, config and fixture
methods) and cli (the in-process CLI runner); the tool skip-guards are the
``requires_*`` marks. Pipeline fixtures are re-exported from
tests/fixtures/conftest.py; production-path builders live in the
tests/fixtures/routes/ package.
"""

import functools
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from sqlmodel import SQLModel, Session, create_engine

# Re-export pipeline-fixture infrastructure so tests at any depth can use
# register_fixture_methods / pipeline_factory without having to live under
# tests/e2e/.  The fixtures themselves live in tests/fixtures/conftest.py;
# the production-path builders live in the tests/fixtures/routes/ package.
from tests.fixtures.conftest import (  # noqa: F401
    FIXTURE_ENV_NAME,
    register_fixture_methods,
    pipeline_factory,
    register_imaging_methods,
    imaging_pipeline_factory,
    stub_readiness_probes,
)
from tests.fixtures.fakes import (  # noqa: F401
    fixture_env_record as _write_fixture_env_record,
    make_marker_project,
    seed_sample_row,
    write_wfc_marker as _make_wfc_marker,
)
from tests.fixtures import routes
from tests.fixtures.routes import (  # noqa: F401
    create_sample_csv,
    init_test_project,
    project_archive_dir,
    register_sample_row,
    register_test_method,
    run_cli,
    sample_source_dir,
    write_env_record,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# =============================================================================
# Suite invariant: nothing lands in the developer's ~/.wfc/archives/
# =============================================================================

@pytest.fixture(scope="session", autouse=True)
def _home_archive_leak_guard():
    """Fail the session if it created a DVC archive in the real home directory.

    ``init_project`` defaults its archive to ``~/.wfc/archives/<project>`` and
    ``init_dvc`` pre-creates it, so a test that does not name one writes
    outside every temp root pytest cleans up. The leak went unnoticed for the
    life of the suite and was only ever fixed one call site at a time.

    A runtime snapshot rather than a scan of the source: it is route-agnostic,
    so it catches a leak through any path — a CLI invocation, the demo
    scaffold, a helper that never writes the token ``init_project`` — where a
    static scan only catches the calls it can see. It is autouse and
    session-scoped, so it is active in the default suite and under
    ``-m integration`` alike, which matters because the leak only manifests in
    the latter.

    Sorted listings are compared rather than counts, and the failure names the
    directories that appeared: the absolute count has been misreported more
    than once, and a number tells the next person nothing about what to delete.

    **New names are not the whole leak.** The directory the default archive
    resolves to is named after the project directory, and those names are
    deterministic pytest ``tmp_path`` basenames — ``proj``, ``engine0``,
    ``canvas_run_project0``. Once a leak has happened under one of them the
    name is already present, so a name-only comparison goes green on every
    re-leak through the same route, and a leak that only adds bytes to an
    existing directory was never visible at all. The guard therefore also
    records when the session started and fails on any archive directory whose
    tree holds an entry written after that, whatever it is named.

    The one shape it still cannot see is a bare ``mkdir(exist_ok=True)`` on an
    existing empty directory that writes nothing: there is no observable on
    disk. Everything that puts bytes in ~ is covered.

    The full rule and the suite's other invariants are in
    ``tests/test_suite_invariants.py``.
    """
    archives = Path.home() / ".wfc" / "archives"

    def listing() -> list[str]:
        if not archives.is_dir():
            return []
        return sorted(entry.name for entry in archives.iterdir())

    def written_since(root: Path, cutoff: float) -> str | None:
        """Return the first path under *root* written at or after *cutoff*."""
        for candidate in [root, *root.rglob("*")]:
            try:
                if candidate.stat().st_mtime >= cutoff:
                    return str(candidate)
            except OSError:
                continue
        return None

    before = set(listing())
    started = time.time()
    yield
    after = listing()
    added = [name for name in after if name not in before]
    grew = []
    for name in after:
        if name in added:
            continue
        witness = written_since(archives / name, started)
        if witness is not None:
            grew.append((name, witness))

    if added or grew:
        lines = []
        if added:
            lines.append(
                f"this run created {len(added)} archive director(y/ies) in "
                "the developer's home:"
            )
            lines += [f"  {archives / name}" for name in added]
        if grew:
            lines.append(
                f"this run wrote into {len(grew)} archive director(y/ies) "
                "that already existed in the developer's home:"
            )
            lines += [f"  {archives / name} (e.g. {witness})"
                      for name, witness in grew]
        raise AssertionError(
            "SUITE INVARIANT 4 — " + "\n".join(lines)
            + "\n\ninit_project resolves ~/.wfc/archives/<project> when no "
            "archive= is given, and init_dvc pre-creates it, so nothing here "
            "is cleaned up by pytest. The directory name comes from the "
            "project directory, so a repeat leak reuses a name that is "
            "already there — which is why growth counts too. Pass "
            "archive=str(project_archive_dir(project_dir)) — or go through "
            "tests.fixtures.conftest.init_test_project, which does it for you. "
            "Delete the directories listed above once the call site is fixed."
        )


# =============================================================================
# Temporary project directory
# =============================================================================

@pytest.fixture
def git_project(tmp_path):
    """A temp directory initialised as a git repo with one initial commit.

    Used by tmp_project (for registration/cache tests) and wfc_root
    (for Snakefile-generation tests) so that git operations in production
    code never hit a non-repo path.
    """
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "wfc@wfc"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "wfc"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    # resolve(): pytest builds tmp_path from getpass.getuser(), whose casing can
    # disagree with the on-disk basetemp dir on Windows (case-insensitive FS).
    # project_root() resolves paths, so unresolved fixture paths break string
    # comparisons against resolver output.
    return tmp_path.resolve()


def pin_project_root(monkeypatch, project_dir: Path) -> None:
    """Point the canonical resolver at ``project_dir`` for the test's duration.

    The canvas server resolves its project through ``wfc.persistence.project_root``
    — ``WFC_PROJECT_ROOT`` validated against ``.wfc/wf-canvas.toml`` — so a
    test that serves a temp directory gives it the marker, sets the override,
    and drops the per-process cache so the next resolution sees both.

    Args:
        monkeypatch: The test's monkeypatch fixture.
        project_dir: The directory to serve as the project root.
    """
    from wfc.persistence import reset_engine

    _make_wfc_marker(project_dir)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(project_dir))
    reset_engine()


@pytest.fixture
def wfc_root(git_project, monkeypatch):
    """Real git-repo path to pass as project_root to generate_snakefile().

    The composer's load (and run_pipeline) needs a reachable database, so we
    set up a .wfc/ marker, point WFC_PROJECT_ROOT at this tmp project and
    give it an empty SQLite database; the emitter itself reads nothing.

    cwd is pinned to the same root, as ``tmp_project`` and the fixture-method
    roots do, so anything a test does relative to the working directory
    (a generated Snakefile's preamble executed in-process makes its log
    directory at a cwd-relative path) lands in the temp project rather
    than wherever pytest was launched.
    """
    monkeypatch.chdir(git_project)
    _make_wfc_marker(git_project)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(git_project))
    db_path = git_project / ".wfc" / "wfc.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    from wfc.persistence import reset_engine
    reset_engine()
    yield str(git_project)
    reset_engine()


@pytest.fixture
def tmp_project(git_project, monkeypatch):
    """A project ``wfc init`` would have produced, in a temp directory.

    Built by production ``init_project`` (through ``init_test_project``), so
    it carries everything a real project does — the full config including the
    ``[dvc]`` section, the database schema, ``methods/``, ``modules/``,
    ``data/samples/``, ``.runs/``, ``.gitignore``, ``.dvc/`` and a remote
    named ``default`` pointing at an archive outside the tree. A test on this
    fixture can call ``register_sample`` and run a real restore.

    - already a git repo (via git_project fixture)
    - WFC_PROJECT_ROOT pinned to this dir (subprocess-safe)
    - cwd set to git_project
    - DATABASE_URL points to in-process SQLite
    - method scripts copied from real methods/
    """
    monkeypatch.chdir(git_project)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(git_project))

    db_path = git_project / ".wfc" / "wfc.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    init_test_project(git_project)

    # Fixture methods declare env: fixture-env; write the placeholder
    # env record so registration's container-env validation passes Docker-free.
    _write_fixture_env_record(git_project)

    # Copy lightweight fixture methods into tmp project. init_project has
    # already made methods/, so the copy merges into it.
    fixture_methods = PROJECT_ROOT / "tests" / "fixtures" / "methods"
    if fixture_methods.exists():
        shutil.copytree(fixture_methods, git_project / "methods",
                        ignore=shutil.ignore_patterns("__pycache__", "__init__.py"),
                        dirs_exist_ok=True)

    from wfc.persistence import reset_engine
    reset_engine()

    yield git_project

    reset_engine()


# =============================================================================
# Canvas API route tests
# =============================================================================

@pytest.fixture
def canvas_db(tmp_project):
    """An empty database with every table built, in a pinned temp project.

    Route tests over the canvas app seed their rows through the models into
    this engine; the app's own sessions reach the same file through the
    ``DATABASE_URL`` that ``tmp_project`` pins.

    Yields:
        The engine over the project's ``.wfc/wfc.db``.
    """
    import wfc.persistence  # noqa: F401  (registers every table on the metadata)

    engine = create_engine(f"sqlite:///{tmp_project / '.wfc' / 'wfc.db'}")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def canvas_client(canvas_db, tmp_project, monkeypatch):
    """The route's client (:func:`tests.fixtures.routes.canvas_client`) bound over ``canvas_db``."""
    return routes.canvas_client(tmp_project, monkeypatch)


# =============================================================================
# Run-readiness pre-flight
# =============================================================================
#
# The ``run-step`` and ``register-env`` branches of ``cli_main`` — and the
# canvas run-submission gate — probe ``check_docker`` / ``check_git`` before
# doing any work, and reframe a "fail" result into the one-door not-runnable
# message.  A test that exercises POST-gate logic (argument validation, input
# wiring, output collection) therefore reports the gate's message instead of
# its own error whenever the container daemon happens to be stopped.
#
# ``ready_preflight`` (below) is the one opt-in fixture over the one fake,
# ``tests.fixtures.conftest.stub_readiness_probes``; a test that needs a
# probe at another status, or one probe left real, calls the fake itself.
#
# This is NOT a substitute for the container markers: a test that genuinely
# needs a running daemon stays ``integration`` + ``requires_docker``.


@pytest.fixture
def ready_preflight(monkeypatch):
    """Default the run-readiness pre-flight to healthy for one test.

    Opt in with ``@pytest.mark.usefixtures("ready_preflight")`` (or by
    naming the fixture) on tests that exercise post-gate logic. A test that
    drives the reject path overrides the probes itself afterwards.
    """
    stub_readiness_probes(monkeypatch)


# =============================================================================
# CLI runner
# =============================================================================

@pytest.fixture
def cli(tmp_project):
    """The in-process ``wfc`` runner, bound to ``tmp_project``.

    Returns :func:`run_cli`: each call is one invocation
    (``cli("register_run", "--method", ...)``) and returns a
    :class:`CliResult` with ``returncode``, ``stdout`` and ``stderr``. The
    fixture exists to pin the project -- every invocation resolves
    ``tmp_project`` through the cwd and ``WFC_PROJECT_ROOT`` it set.
    """
    return run_cli


# =============================================================================
# Docker availability skip marker
# =============================================================================
#
# Container build/run tests must skip cleanly when Docker isn't reachable so CI
# on bare runners (and dev machines without a daemon) stays green.
#
# Usage:
#     pytestmark = [pytest.mark.integration, requires_docker]   # whole module
#     @requires_docker                                          # single test


@functools.lru_cache(maxsize=None)
def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


requires_docker = pytest.mark.skipif(
    not _docker_available(),
    reason="docker not reachable (required for container build/run tests)",
)

#: The Docker CLI alone, daemon up or down: a test that points the CLI at a
#: dead endpoint to drive the stopped-daemon answer needs the binary, not a
#: running daemon.
requires_docker_cli = pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="docker CLI not on PATH (required to drive the daemon probe "
           "against a dead endpoint)",
)


# =============================================================================
# Host-tool skip markers: conda, pip, DVC
# =============================================================================
#
# Built like ``requires_docker``: a cached probe that never raises, evaluated
# when the using module is imported, and a ``skipif`` whose reason names the
# missing tool. A test that drives a registry fake's real boundary takes the
# guard for the tool that boundary needs, so a machine without the tool
# skips it visibly instead of failing.
#
# Usage:
#     pytestmark = requires_conda                  # whole module
#     @requires_dvc                                # single test


def _answers(argv: list[str]) -> bool:
    """Say whether a command runs and exits 0, never raising.

    Args:
        argv: The command, spelled exactly as production spells it (no
            shell), so a tool reachable only through a shell shim reads as
            absent here just as it would to the production call.

    Returns:
        ``True`` when the command ran and exited 0.
    """
    try:
        result = subprocess.run(argv, capture_output=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return False
    return result.returncode == 0


@functools.lru_cache(maxsize=None)
def _conda_available() -> bool:
    """Say whether ``conda`` answers on PATH the way the live capture calls it."""
    return shutil.which("conda") is not None and _answers(["conda", "--version"])


@functools.lru_cache(maxsize=None)
def _pip_available() -> bool:
    """Say whether ``pip`` runs under this interpreter."""
    import sys

    return _answers([sys.executable, "-m", "pip", "--version"])


@functools.lru_cache(maxsize=None)
def _dvc_available() -> bool:
    """Say whether the DVC package imports on this interpreter."""
    import importlib.util

    try:
        return importlib.util.find_spec("dvc") is not None
    except (ImportError, ValueError):
        return False


requires_conda = pytest.mark.skipif(
    not _conda_available(),
    reason="conda not reachable on PATH (required to list a real conda env)",
)

requires_conda_lock = pytest.mark.skipif(
    shutil.which("conda-lock") is None,
    reason="conda-lock not on PATH (a separate install, not a wfc "
           "dependency; required to solve a conda spec for linux-64)",
)

requires_pip = pytest.mark.skipif(
    not _pip_available(),
    reason="pip not runnable under this interpreter (required for pip freeze)",
)

requires_dvc = pytest.mark.skipif(
    not _dvc_available(),
    reason="dvc not importable (required for DVC probe and remote tests)",
)


# =============================================================================
# Fixture-method container image
# =============================================================================
#
# Execution is container-only: every method runs inside a built container
# image. The lightweight fixture methods (transform/merge/faulty/...) need a
# real image to execute end-to-end, so ``register_fixture_methods`` references
# a session-scoped image built once from ``tests/fixtures/Dockerfile.minimal``
# (the same image the tests/integration/ suite uses). The build only fires for
# Docker-gated ``integration`` tests — the default suite deselects them via the
# ``-m "not slow and not integration"`` addopts, so no build is triggered there.

_FIXTURE_IMAGE_TAG = "local/wfc-test-minimal:latest"


@pytest.fixture(scope="session")
def fixture_container_image() -> str:
    """Build the minimal wfc image once per session; return its sha256 digest.

    Reuses ``tests/fixtures/Dockerfile.minimal`` (wfc + its runtime deps), the
    same image the ``tests/integration/`` suite builds. The digest is captured
    via ``docker image inspect`` so the env manifest can reference an immutable
    ``image@sha256:...`` ref (the shape ``wfc.contracts.validate_container_ref``
    enforces).

    Returns:
        The image digest as a bare sha256 hex string (no ``sha256:`` prefix).
    """
    dockerfile = PROJECT_ROOT / "tests" / "fixtures" / "Dockerfile.minimal"
    assert dockerfile.exists(), f"Missing fixture Dockerfile: {dockerfile}"

    build = subprocess.run(
        ["docker", "build", "-t", _FIXTURE_IMAGE_TAG,
         "-f", str(dockerfile), str(PROJECT_ROOT)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600,
    )
    if build.returncode != 0:
        pytest.fail(
            "docker build of fixture image failed:\n"
            f"STDOUT:\n{build.stdout}\nSTDERR:\n{build.stderr}"
        )

    inspect = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", _FIXTURE_IMAGE_TAG],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )
    if inspect.returncode != 0:
        pytest.fail(
            "docker image inspect failed:\n"
            f"STDOUT:\n{inspect.stdout}\nSTDERR:\n{inspect.stderr}"
        )

    # Tag the built image under the env-name repository so the manifest's
    # digest-pinned ref (docker://local/fixture-env@sha256:<id>) resolves at
    # `docker run` time — the same repo/tag pairing production's local-build
    # path creates (`local/<env>:_wfc-build`).
    tag = subprocess.run(
        ["docker", "tag", _FIXTURE_IMAGE_TAG, "local/fixture-env:_wfc-build"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )
    if tag.returncode != 0:
        pytest.fail(
            "docker tag of fixture image failed:\n"
            f"STDOUT:\n{tag.stdout}\nSTDERR:\n{tag.stderr}"
        )

    image_id = inspect.stdout.strip()
    return image_id[len("sha256:"):] if image_id.startswith("sha256:") else image_id
