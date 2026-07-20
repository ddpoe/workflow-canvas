"""Scaffold logic for ``wfc demo`` (US-1).

Drives the 9-step sequence from the request design: every fallible check
runs BEFORE the first state change, so a failed ``wfc demo`` leaves the
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
import sys
import threading
import webbrowser
from pathlib import Path

from ..preflight import check_docker
from ..provenance import DvcNotConfiguredError, ensure_dvc_ready

DEMO_MODULE = "__demo__"
DEMO_ENV = "__demo__env"
DEMO_IMAGE_TAG = "local/wfc-demo-env:latest"
DEMO_METHODS = ("preprocess", "filter_cells", "label", "summarize", "plot")
DEMO_SAMPLES = ("ctrl_01", "treat_01", "treat_02")
ASSETS_DIR = Path(__file__).parent / "assets"


class DemoError(Exception):
    """User-facing failure: the CLI prints the message and exits non-zero."""


@contextlib.contextmanager
def _project_env(target: Path):
    """Bind the process to *target* as the active wfc project.

    ``register_module`` / ``register_method`` resolve the project root from
    the current working directory, and the DB engine is cached per-process —
    so ``--dir`` support needs chdir + env vars + an engine reset, restored
    on exit.

    Args:
        target: Resolved project root directory.
    """
    from ..database import reset_engine

    old_cwd = Path.cwd()
    old_root = os.environ.get("WFC_PROJECT_ROOT")
    old_db = os.environ.get("DATABASE_URL")
    os.chdir(target)
    os.environ["WFC_PROJECT_ROOT"] = str(target)
    os.environ["DATABASE_URL"] = f"sqlite:///{target / '.wfc' / 'wfc.db'}"
    reset_engine()
    try:
        yield
    finally:
        os.chdir(old_cwd)
        for key, old in (("WFC_PROJECT_ROOT", old_root), ("DATABASE_URL", old_db)):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        reset_engine()


def _existing_demo_entities(target: Path) -> list[str]:
    """Return human-readable labels of demo entities already present.

    Tolerates partial scaffolds — each probe is independent. Must be called
    inside :func:`_project_env`.

    Args:
        target: Resolved project root directory.

    Returns:
        Labels like ``"module __demo__"`` for everything found.
    """
    from sqlmodel import select

    from ..database import get_session
    from ..envs import load_manifest
    from ..models import Module, Sample

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
                Sample.name.startswith(DEMO_MODULE, autoescape=True)  # type: ignore[attr-defined]
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
    ``methods/<method_name>/`` keyed by method name alone, so a user method
    named e.g. ``preprocess`` shares that directory with the demo's. Copying
    demo code there would silently clobber the user's registered snapshot —
    refuse up front instead. Must be called inside :func:`_project_env`.

    Args:
        target: Resolved project root directory.

    Returns:
        Human-readable collision descriptions (empty when safe to proceed).
    """
    from sqlmodel import select

    from ..database import get_session
    from ..models import Method, Module

    collisions: list[str] = []
    with get_session() as session:
        rows = session.exec(
            select(Method, Module)
            .join(Module, Method.module_id == Module.id)  # type: ignore[arg-type]
            .where(Method.name.in_(DEMO_METHODS))  # type: ignore[attr-defined]
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
        # above) is a user's unregistered work-in-progress — never overwrite.
        claimed = {c.split("methods/")[-1].rstrip("/") for c in collisions}
        for m in DEMO_METHODS:
            if m not in claimed and (target / "methods" / m).exists():
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

    from .. import docker_runner

    template = (ASSETS_DIR / "Dockerfile.template").read_text(encoding="utf-8")
    # Thin-container contract: the image needs only what the demo methods
    # import — wfc-client (Tier-1 authoring sugar; ships independently on
    # PyPI) and matplotlib. Pin wfc-client to the host-installed version.
    wfc_client_version = importlib.metadata.version("wfc-client")
    build_dir = target / ".wfc" / "build" / DEMO_ENV
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

    # ---- Preflight: initialised project (never init here — locked) ----
    marker = target / ".wfc" / "wf-canvas.toml"
    db_path = target / ".wfc" / "wfc.db"
    if not marker.exists() or not db_path.exists():
        raise DemoError(
            f"{target} is not a Workflow Canvas project — run: wfc init"
        )

    # git repo required: registration commits method code for cache keys.
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=target, capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        raise DemoError(
            f"git did not respond within 10s while probing {target} — "
            f"check your git installation and retry."
        )
    if probe.returncode != 0:
        raise DemoError(
            f"{target} is not a git repository — method registration commits "
            f"code for cache-key computation. Run `git init` (a normal "
            f"`wfc init` does this for you)."
        )

    # DVC configured (register_sample hard-requires it, ADR-009/018).
    try:
        ensure_dvc_ready(target)
    except DvcNotConfiguredError as exc:
        raise DemoError(f"DVC is not configured for this project: {exc}")

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
        # `env: container:__demo__env` is validated at register_method) ----
        from ..envs import register as register_env

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
        from ..register import register_method, register_module

        register_module(
            name=DEMO_MODULE,
            contracts=[],
            description=(
                "Demo pipeline created by `wfc demo` — remove with "
                "`wfc demo --remove`"
            ),
            allow_reserved=True,
        )
        for m in DEMO_METHODS:
            dest = target / "methods" / m
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(ASSETS_DIR / "methods" / m, dest)
            register_method(
                method_dir=dest,
                module_name=DEMO_MODULE,
                method_name=m,
                allow_reserved=True,
            )

        # ---- Step 7: samples (tagged directory names, clean filenames) ----
        from ..cli import register_sample

        for s in DEMO_SAMPLES:
            src = ASSETS_DIR / "samples" / f"{s}.csv"
            tagged = f"{DEMO_MODULE}{s}"
            sample_dir = target / "data" / "samples" / tagged
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
        "__demo__env. Remove everything later with `wfc demo --remove`."
    )

    # ---- Step 9: serve the Canvas ----
    if serve:
        return _serve(target, port=port, no_open=no_open)
    return 0


def _serve(target: Path, port: int, no_open: bool) -> int:
    """Serve the Canvas for *target* on 127.0.0.1:*port* (blocking).

    Reuses the ``wfc canvas`` mechanism: project-root env binding plus an
    in-process uvicorn of ``wfc.canvas.server:app``.

    Args:
        target: Resolved project root directory.
        port: Bind port.
        no_open: Skip the browser launch.

    Returns:
        Process exit code (0 after the server stops).
    """
    try:
        import uvicorn
    except ImportError:
        raise DemoError(
            "uvicorn is required to serve the canvas — install it with: "
            "pip install 'uvicorn[standard]'"
        )

    os.environ["WFC_CANVAS_PROJECT_ROOT"] = str(target)
    url = f"http://127.0.0.1:{port}/?pipeline=demo"
    print(f"Starting Workflow Canvas at {url}")
    if not no_open:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run("wfc.canvas.server:app", host="127.0.0.1", port=port)
    return 0
