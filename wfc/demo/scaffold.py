"""Scaffold logic for ``wfc demo``.

Runs a 9-step sequence in which every fallible check runs BEFORE the
first state change, so a failed ``wfc demo`` leaves the
project byte-for-byte unchanged. All registration goes through the genuine
production paths (``envs.register``, ``register_module``,
``register_method``, ``register_sample``) with the explicit
``allow_reserved=True`` opt-in — the ``__demo__`` prefix is refused
everywhere else, which is what makes teardown-by-tag safe.
"""
from __future__ import annotations

import contextlib
import importlib.metadata
import os
import shutil
import subprocess
import threading
import webbrowser
from pathlib import Path

from .. import layout
from ..execution.readiness import check_docker
from ..storage import DvcNotConfiguredError, ensure_dvc_ready

DEMO_MODULE = "__demo__"
DEMO_ENV = "__demo__env"
DEMO_IMAGE_TAG = "local/wfc-demo-env:latest"
#: Asset directory names under ``assets/methods/`` — the names the demo's
#: source is authored and shipped under. Each holds ``<name>.py``.
DEMO_METHOD_SOURCES = ("preprocess", "filter_cells", "label", "summarize", "plot")
#: The names the demo's methods are REGISTERED under, and the names of the
#: directories it stages under the project's ``methods/``.
#:
#: They carry the reserved prefix because ``register_method`` snapshots every
#: method into ``methods/<method_name>/``, a single flat namespace shared by
#: every module, and refuses a name another module already holds. Unprefixed,
#: the demo would consume ``preprocess`` — so the first thing a new user runs
#: would stop them registering a method of their own by that name.
#:
#: This is a workaround for the flat snapshot namespace, not a design. Remove
#: the prefix (and this indirection) when per-module snapshot directories land.
DEMO_METHODS = tuple(f"{DEMO_MODULE}{n}" for n in DEMO_METHOD_SOURCES)
DEMO_SAMPLES = ("ctrl_01", "treat_01", "treat_02")
ASSETS_DIR = Path(__file__).parent / "assets"


class DemoError(Exception):
    """User-facing failure: the CLI prints the message and exits non-zero."""


@contextlib.contextmanager
def _project_env(target: Path):
    """Bind the process to *target* as the active wfc project.

    The registration calls resolve the project root and open sessions through
    Persistence, so ``--dir`` support binds both to *target* with
    :func:`wfc.persistence.use_project`. The working directory and
    ``WFC_PROJECT_ROOT`` are set as well, for the subprocesses the scaffold
    starts. All three are restored on exit.

    Args:
        target: Resolved project root directory.
    """
    from ..persistence import use_project

    old_cwd = Path.cwd()
    old_root = os.environ.get(layout.ROOT_ENV_VAR)
    os.chdir(target)
    os.environ[layout.ROOT_ENV_VAR] = str(target)
    try:
        with use_project(target):
            yield
    finally:
        os.chdir(old_cwd)
        if old_root is None:
            os.environ.pop(layout.ROOT_ENV_VAR, None)
        else:
            os.environ[layout.ROOT_ENV_VAR] = old_root


def _same_file(a: str | Path, b: str | Path) -> bool:
    """Return whether two paths name the same file.

    Compared as real paths, case-folded where the platform folds case, the
    way the canvas provider compares its bound database to its project's.
    """
    return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(
        os.path.realpath(str(b))
    )


def _refuse_a_foreign_database(target: Path) -> None:
    """Refuse when ``DATABASE_URL`` names a database other than *target*'s.

    The demo canvas reads *target*'s database through
    :func:`wfc.persistence.use_project`, which leaves the environment alone.
    The runs the canvas starts inherit ``DATABASE_URL``, so an override that
    names another database would send their rows there while the canvas reads
    *target*'s: new runs would never appear, and nothing would error. An
    override naming *target*'s own database, in any spelling, passes.

    Args:
        target: Resolved project root directory.

    Raises:
        DemoError: ``DATABASE_URL`` is set and names another database.
    """
    override = os.environ.get("DATABASE_URL")
    if not override:
        return
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    try:
        url = make_url(override)
    except ArgumentError:
        named, shown = None, "a value that is not a database URL"
    else:
        named = url.database if url.get_backend_name() == "sqlite" else None
        shown = url.render_as_string(hide_password=True)
    expected = layout.db_path(target)
    if named and _same_file(named, expected):
        return
    raise DemoError(
        f"DATABASE_URL is set to {shown}, which is not this project's "
        f"database ({expected}). The demo canvas reads this project's "
        f"database, but the runs it starts would inherit DATABASE_URL and "
        f"record to the other one, so they would never appear. Unset "
        f"DATABASE_URL, or set it to {layout.database_url(target)}, and "
        f"re-run `wfc demo`."
    )


def _existing_demo_entities(target: Path) -> list[str]:
    """Return human-readable labels of demo entities already present.

    Tolerates partial scaffolds — each probe is independent. Must be called
    inside :func:`_project_env`.

    Args:
        target: Resolved project root directory.

    Returns:
        Labels like ``"module __demo__"`` for everything found.
    """
    from sqlmodel import col, select

    from ..environments import load_manifest
    from ..persistence import Module, Sample, get_session

    found: list[str] = []
    with get_session() as session:
        module = session.exec(
            select(Module).where(Module.name == DEMO_MODULE)
        ).first()
        if module is not None:
            found.append(f"module {DEMO_MODULE}")
        # autoescape: `_` is a single-char LIKE wildcard, so an unescaped
        # LIKE '__demo__%' would also match user samples like 'mydemo__x'.
        samples = session.exec(
            select(Sample).where(
                col(Sample.name).startswith(DEMO_MODULE, autoescape=True)
            )
        ).all()
        found.extend(f"sample {s.name}" for s in samples)
    try:
        manifest = load_manifest(target)
    except Exception:
        manifest = {}
    if DEMO_ENV in manifest.get("envs", {}):
        found.append(f"env {DEMO_ENV}")
    if (target / "demo-pipeline.json").exists():
        found.append("demo-pipeline.json")
    return found


def _method_name_collisions(target: Path) -> list[str]:
    """Detect user-owned claims on the demo's method names.

    ``register_method`` snapshots every method's code into
    ``methods/<method_name>/`` keyed by method name alone, so two methods
    sharing a name share that directory and copying demo code there would
    silently clobber the other one's registered snapshot. Refuse up front
    instead. Must be called inside :func:`_project_env`.

    Since the demo's method names carry the reserved ``__demo__`` prefix, the
    DB arm can no longer fire: ``check_reserved_name`` refuses a ``__demo__*``
    method name to every caller but ``wfc demo`` itself. It is kept because it
    is the arm that becomes live again the day the prefix is removed. The
    on-disk arm below is still reachable today — an aborted scaffold or a
    reset database can leave a ``methods/__demo__<name>/`` directory with no
    demo module to own it.

    Args:
        target: Resolved project root directory.

    Returns:
        Human-readable collision descriptions (empty when safe to proceed).
    """
    from sqlmodel import col, select

    from ..persistence import Method, Module, get_session

    collisions: list[str] = []
    with get_session() as session:
        rows = session.exec(
            select(Method, Module)
            .join(Module, col(Method.module_id) == col(Module.id))
            .where(col(Method.name).in_(DEMO_METHODS))
            .where(Module.name != DEMO_MODULE)
        ).all()
        for method, module in rows:
            collisions.append(
                f"method '{module.name}/{method.name}' already uses "
                f"methods/{method.name}/"
            )
        demo_registered = session.exec(
            select(Module).where(Module.name == DEMO_MODULE)
        ).first() is not None
    if not demo_registered:
        # A methods/<m>/ dir with no demo module in the DB (and no DB claim
        # above) is unattributable — residue from an aborted scaffold or a
        # reset database, or hand-authored work. Refuse rather than guess;
        # the scaffold's copy step deletes whatever it lands on.
        claimed = {c.split("methods/")[-1].rstrip("/") for c in collisions}
        for m in DEMO_METHODS:
            if m not in claimed and layout.method_dir(target, m).exists():
                collisions.append(
                    f"directory methods/{m}/ exists but is not demo-owned"
                )
    return collisions


def _build_demo_image(target: Path) -> str:
    """Render the Dockerfile and build the demo image; return the byo ref.

    When ``WFC_DEMO_IMAGE`` is set (integration tests / unreleased dev
    versions that cannot ``pip install wfc-client==<version>`` yet),
    the render + build is skipped and that image ref is registered instead.

    Args:
        target: Resolved project root directory.

    Returns:
        A ``docker://...`` ref for ``envs.register`` (byo backend).
    """
    override = os.environ.get("WFC_DEMO_IMAGE")
    if override:
        ref = override if override.startswith("docker://") else f"docker://{override}"
        print(f"WFC_DEMO_IMAGE set — skipping image build, using {ref}")
        return ref

    from ..environments import docker as docker_runner

    template = (ASSETS_DIR / "Dockerfile.template").read_text(encoding="utf-8")
    # Thin-container contract: the image needs only what the demo methods
    # import — wfc-client (the authoring helper library; ships independently
    # on PyPI) and matplotlib. Pin wfc-client to the host-installed version.
    wfc_client_version = importlib.metadata.version("wfc-client")
    build_dir = layout.env_build_dir(target, DEMO_ENV)
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "Dockerfile").write_text(
        template.format(wfc_client_version=wfc_client_version), encoding="utf-8"
    )
    print(f"Building demo image {DEMO_IMAGE_TAG} (first build takes a few minutes)…")
    docker_runner.build(build_dir, DEMO_IMAGE_TAG)
    return f"docker://{DEMO_IMAGE_TAG}"


def run_demo(
    target_dir: str | Path | None = None,
    port: int = 8500,
    no_open: bool = False,
    force: bool = False,
    serve: bool = True,
) -> int:
    """Scaffold the demo into an existing initialised project and serve it.

    Args:
        target_dir: Existing initialised project directory (default: cwd).
        port: Canvas port (default 8500, matching ``wfc canvas``).
        no_open: Scaffold and serve without launching a browser.
        force: Re-register over an existing demo (tears the old one down
            first, keeping the DVC cache and archive untouched).
        serve: When ``False``, scaffold only (used by tests).

    Returns:
        Process exit code (0 on success).

    Raises:
        DemoError: On any preflight failure — the project is unchanged.
    """
    target = Path(target_dir or Path.cwd()).resolve()

    # ---- Preflight: initialised project (never init here) ----
    marker = layout.marker_path(target)
    db_path = layout.db_path(target)
    if not marker.exists() or not db_path.exists():
        raise DemoError(
            f"{target} is not a Workflow Canvas project — run: wfc init"
        )

    # ---- Preflight: no DATABASE_URL naming another database ----
    _refuse_a_foreign_database(target)

    # git repo required: registration commits method code for cache keys.
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=target, capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise DemoError(
            f"git did not respond within 10s while probing {target} — "
            f"check your git installation and retry."
        ) from exc
    if probe.returncode != 0:
        raise DemoError(
            f"{target} is not a git repository — method registration commits "
            f"code for cache-key computation. Run `git init` (a normal "
            f"`wfc init` does this for you)."
        )

    # DVC configured (register_sample hard-requires it).
    try:
        ensure_dvc_ready(target)
    except DvcNotConfiguredError as exc:
        raise DemoError(f"DVC is not configured for this project: {exc}") from exc

    # ---- Preflight: Docker ----
    docker = check_docker()
    if docker.status == "fail":
        hint = f" {docker.fix_hint}" if docker.fix_hint else ""
        raise DemoError(f"Docker preflight failed: {docker.message}{hint}")

    with _project_env(target):
        # ---- Preflight: existing demo / --force ----
        existing = _existing_demo_entities(target)
        if existing and not force:
            raise DemoError(
                "demo already present in this project ("
                + ", ".join(existing)
                + ") — re-run with --force to re-register, or "
                "`wfc demo --remove` to clear it"
            )
        if existing and force:
            from .remove import remove_demo

            print("--force: removing the existing demo first…")
            remove_demo(target, assume_yes=True)

        # ---- Preflight: user-owned method-name collisions ----
        collisions = _method_name_collisions(target)
        if collisions:
            raise DemoError(
                "cannot scaffold the demo — its method names would overwrite "
                "user-owned method snapshots under methods/: "
                + "; ".join(collisions)
            )

        # ---- Step 4: Dockerfile + image build ----
        image_ref = _build_demo_image(target)

        # ---- Step 5: register the env (before methods — method.yaml
        # `env: __demo__env` is validated at register_method) ----
        from ..environments import register as register_env

        register_env(
            name=DEMO_ENV,
            backend="byo",
            source={"image": image_ref},
            project_dir=target,
            force=True,
            allow_reserved=True,
        )
        print(f"Registered env {DEMO_ENV}")

        # ---- Step 6: module + methods ----
        from ..registration import register_method, register_module

        register_module(
            name=DEMO_MODULE,
            contracts=[],
            description=(
                "Demo pipeline created by `wfc demo` — remove with "
                "`wfc demo --remove`"
            ),
            allow_reserved=True,
        )
        # The assets keep their bare names; the prefix is applied on the way
        # in, so the staged directory — which IS the snapshot directory, since
        # the demo registers in place — is methods/__demo__<name>/ and no demo
        # method ever claims a bare name a user might want.
        for src_name, m in zip(DEMO_METHOD_SOURCES, DEMO_METHODS):
            dest = layout.method_dir(target, m)
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(ASSETS_DIR / layout.METHODS_DIR_NAME / src_name, dest)
            register_method(
                method_dir=dest,
                module_name=DEMO_MODULE,
                method_name=m,
                # The script keeps its authored name, so the
                # `{method_name}.py` probe would miss it — name it outright.
                script_name=f"{src_name}.py",
                allow_reserved=True,
            )

        # ---- Step 7: samples (tagged directory names, clean filenames) ----
        from ..registration import register_sample

        for s in DEMO_SAMPLES:
            src = ASSETS_DIR / layout.SAMPLES_DIR_NAME / f"{s}.csv"
            tagged = f"{DEMO_MODULE}{s}"
            sample_dir = layout.sample_dir(target, tagged)
            sample_dir.mkdir(parents=True, exist_ok=True)
            copied = sample_dir / src.name
            shutil.copy2(src, copied)
            register_sample(
                name=tagged,
                source_path=copied,
                project_root=target,
                allow_reserved=True,
            )
            print(f"Registered sample {tagged}")

        # ---- Step 8: pipeline document ----
        shutil.copy2(ASSETS_DIR / "pipeline.json", target / "demo-pipeline.json")
        print("Wrote demo-pipeline.json")

    print(
        "\nDemo scaffolded: module __demo__ (5 methods), 3 samples, env "
        "__demo__env. Every demo entity carries the __demo__ prefix — "
        "including the method names — so the demo never takes a name you "
        "want for your own work. Remove everything later with "
        "`wfc demo --remove`."
    )

    # ---- Step 9: serve the Canvas ----
    if serve:
        return _serve(target, port=port, no_open=no_open)
    return 0


def _serve(target: Path, port: int, no_open: bool) -> int:
    """Serve the Canvas for *target* on 127.0.0.1:*port* (blocking).

    Reuses the ``wfc canvas`` mechanism: project-root env binding plus an
    in-process uvicorn of ``wfc.canvas.server:app``. The server runs
    inside :func:`wfc.persistence.use_project`, so it reads *target*'s
    database whatever this process had bound before. The runs it starts
    inherit ``DATABASE_URL``; :func:`run_demo` has already refused one that
    names another database.

    Args:
        target: Resolved project root directory.
        port: Bind port.
        no_open: Skip the browser launch.

    Returns:
        Process exit code (0 after the server stops).
    """
    try:
        import uvicorn
    except ImportError as exc:
        raise DemoError(
            "uvicorn is required to serve the canvas — install it with: "
            "pip install 'uvicorn[standard]'"
        ) from exc

    os.environ[layout.ROOT_ENV_VAR] = str(target)
    url = f"http://127.0.0.1:{port}/?pipeline=demo"
    print(f"Starting Workflow Canvas at {url}")
    if not no_open:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    from ..persistence import use_project

    with use_project(target):
        uvicorn.run("wfc.canvas.server:app", host="127.0.0.1", port=port)
    return 0
