"""Scaffold logic for ``wfc demo``.

Runs a 9-step sequence in which every fallible check runs BEFORE the
first state change, so a failed ``wfc demo`` leaves the
project byte-for-byte unchanged. All registration goes through the genuine
production paths (``envs.register``, ``register_module``,
``register_method``, ``register_sample``) with the explicit
``allow_reserved=True`` opt-in — the ``__demo__`` prefix and the demo env's
name are refused everywhere else, which is what makes teardown by tag and
module cascade safe.

The demo env is registered with no push, whether or not the project has a
registry base: rebuilding the demo never needs the network, and pushing its
image is a step the user takes (``wfc push-env wfc-demo-env``). The demo
schema stamp (``.wfc/demo.json``) records which version of the demo built
the project's demo; ``wfc demo`` and ``wfc doctor`` read it.
"""
from __future__ import annotations

import contextlib
import importlib.metadata
import json
import os
import shutil
import threading
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

from .. import layout
from ..execution.readiness import check_docker
from ..reserved import DEMO_ENV_NAME
from ..storage import DvcNotConfiguredError, ensure_dvc_ready

if TYPE_CHECKING:
    from ..execution.readiness import CheckResult

DEMO_MODULE = "__demo__"
DEMO_ENV = DEMO_ENV_NAME
DEMO_IMAGE_TAG = "local/wfc-demo-env:latest"
#: The demo schema: what ``wfc demo`` creates, as a number. Bump it when a
#: release changes what ``wfc demo`` creates in a way an existing demo
#: project does not have (a renamed entity, a changed asset contract), so
#: ``wfc demo`` and ``wfc doctor`` send that project to ``wfc demo --force``.
#: A project with no stamp counts as 1; the env rename to ``wfc-demo-env``
#: is the first bump.
DEMO_SCHEMA = 2
#: The schema a demo with no stamp, or an unreadable one, counts as.
_UNSTAMPED_SCHEMA = 1
#: The demo's pipeline document, at the project root.
DEMO_PIPELINE_FILENAME = "demo-pipeline.json"
#: Asset directory names under ``assets/methods/`` — the names the demo's
#: source is authored and shipped under. Each holds ``<name>.py``.
DEMO_METHOD_SOURCES = ("preprocess", "filter_cells", "label", "summarize", "plot")
#: The names the demo's methods are REGISTERED under.
#:
#: They carry the reserved demo prefix. ``register_method`` snapshots each
#: method into the module-qualified ``.wfc/registry/methods/<module>/<name>/``,
#: so a user's method of the same name in another module does not collide with
#: a demo method; the prefix only marks the names as the demo's. Dropping it
#: renames the demo's methods.
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

    from ..environments import load_manifest_for_register
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
        # The register path's read: the demo's own register converts an
        # older (version 1 or 2) manifest, so its demo env must still be
        # seen here.
        manifest = load_manifest_for_register(target)
    except Exception:
        manifest = {}
    for env in demo_owned_envs(target):
        if env in manifest.get("envs", {}):
            found.append(f"env {env}")
    if (target / DEMO_PIPELINE_FILENAME).exists():
        found.append(DEMO_PIPELINE_FILENAME)
    stamp = layout.demo_stamp_path(target)
    if stamp.exists():
        found.append(stamp.relative_to(target).as_posix())
    return found


def demo_owned_envs(target: Path) -> list[str]:
    """Return the envs the demo owns: :data:`DEMO_ENV` and every env its methods name.

    Read before anything is deleted: each demo Method row's ``env`` and every
    entry of every version list under the demo module's snapshot directory.
    Demo methods are registered only from the bundled assets with the
    opt-in, so every env they name is the demo's, including an older wfc's
    demo env. Values that are not env names are skipped. Must be called
    inside :func:`_project_env`.

    Args:
        target: Resolved project root directory.

    Returns:
        The env names, sorted.
    """
    from sqlmodel import select

    from ..contracts import parse_env_spec
    from ..persistence import Method, Module, get_session
    from ..registration import read_version_list

    named: list[str | None] = []
    with get_session() as session:
        module = session.exec(
            select(Module).where(Module.name == DEMO_MODULE)
        ).first()
        if module is not None:
            named.extend(m.env for m in session.exec(
                select(Method).where(Method.module_id == module.id)
            ).all())
    snapshot_dir = layout.module_methods_dir(target, DEMO_MODULE)
    suffix = layout.METHOD_VERSIONS_SUFFIX
    for path in sorted(snapshot_dir.glob(f"*{suffix}")):
        try:
            entries = read_version_list(
                target, DEMO_MODULE, path.name[: -len(suffix)]) or []
        except (OSError, ValueError):
            continue
        named.extend(e.env_name for e in entries)
    envs = {DEMO_ENV}
    for value in named:
        if not value:
            continue
        try:
            envs.add(parse_env_spec(value))
        except ValueError:
            continue
    return sorted(envs)


def demo_schema_of(target: Path) -> int:
    """Return the demo schema *target*'s stamp records.

    Args:
        target: The project root.

    Returns:
        The stamp's ``demo_schema``; 1 when there is no stamp or it cannot
        be read.
    """
    try:
        data = json.loads(layout.demo_stamp_path(target).read_text("utf-8"))
        return int(data["demo_schema"])
    except (OSError, ValueError, KeyError, TypeError):
        return _UNSTAMPED_SCHEMA


def write_demo_stamp(target: Path) -> Path:
    """Write the current demo schema stamp and commit it with the pipeline document.

    Both files are committed by pathspec, so the tree is clean after
    ``wfc demo``: every path wfc creates is committed or ignored.

    Args:
        target: The project root.

    Returns:
        The stamp's path.
    """
    from ..version import commit_paths

    stamp = layout.demo_stamp_path(target)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_bytes(
        (json.dumps({"demo_schema": DEMO_SCHEMA}) + "\n").encode("utf-8"))
    commit_paths(target, [target / DEMO_PIPELINE_FILENAME, stamp],
                 "Record the wfc demo pipeline and schema stamp",
                 require_repo=False)
    return stamp


def demo_schema_check(project_dir: Path | str) -> CheckResult | None:
    """Return ``wfc doctor``'s demo row: a warn for a demo an older wfc made.

    A demo is present when the project has the ``__demo__`` module or a
    stamp. The row appears only when one is present and its stamp is
    missing or records another schema.

    Args:
        project_dir: The project root.

    Returns:
        A warn :class:`~wfc.execution.readiness.CheckResult` naming
        ``wfc demo --force``, or ``None`` (no demo, or a current one).
    """
    from sqlmodel import select

    from ..execution.readiness import CheckResult
    from ..persistence import Module, get_session, use_project

    target = Path(project_dir)
    present = layout.demo_stamp_path(target).exists()
    if not present:
        try:
            with use_project(target), get_session() as session:
                present = session.exec(
                    select(Module).where(Module.name == DEMO_MODULE)
                ).first() is not None
        except Exception:
            present = False
    if not present or demo_schema_of(target) == DEMO_SCHEMA:
        return None
    return CheckResult(
        name="demo", status="warn",
        message="This project's demo was made by an older wfc.",
        fix_hint="Run `wfc demo --force` to rebuild it for this version.",
    )


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
    from ..version import GitTimeoutError, in_work_tree
    try:
        is_repo = in_work_tree(target, timeout=10)
    except GitTimeoutError as exc:
        raise DemoError(
            f"git did not respond within 10s while probing {target} — "
            f"check your git installation and retry."
        ) from exc
    if not is_repo:
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
            if demo_schema_of(target) != DEMO_SCHEMA:
                advice = (
                    "this demo project was made by an older wfc; run "
                    "`wfc demo --force` to rebuild it for this version"
                )
            else:
                advice = (
                    "`wfc demo --force` removes the demo and builds it again "
                    "for this wfc version; `wfc demo --remove` clears it"
                )
            raise DemoError(
                "demo already present in this project ("
                + ", ".join(existing)
                + ") — " + advice
            )
        if existing and force:
            from .remove import remove_demo

            print("--force: removing the existing demo first…")
            remove_demo(target, assume_yes=True)

        # ---- Step 4: Dockerfile + image build ----
        image_ref = _build_demo_image(target)

        # ---- Step 5: register the env (before methods — method.yaml
        # `env: wfc-demo-env` is validated at register_method). Never
        # pushed here, base or not: pushing is the user's step. ----
        from ..environments import register as register_env

        register_env(
            name=DEMO_ENV,
            backend="byo",
            source={"image": image_ref},
            project_dir=target,
            force=True,
            allow_reserved=True,
            push=False,
        )
        print(f"Registered env {DEMO_ENV}; its image stays on this machine. "
              f"`wfc push-env {DEMO_ENV}` pushes it to the project's registry.")

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
        # Registered straight from the bundled assets: registration copies
        # them into the demo module's snapshots. The assets keep their bare
        # names; the prefix is applied on the way in, so no demo method ever
        # claims a bare name a user might want.
        for src_name, m in zip(DEMO_METHOD_SOURCES, DEMO_METHODS):
            register_method(
                method_dir=ASSETS_DIR / layout.METHODS_DIR_NAME / src_name,
                module_name=DEMO_MODULE,
                method_name=m,
                # The script keeps its authored name, so the
                # `{method_name}.py` probe would miss it — name it outright.
                script_name=f"{src_name}.py",
                allow_reserved=True,
                assume_yes=True,
            )

        # ---- Step 7: samples (tagged names, clean filenames) ----
        # Registered straight from the bundled assets: registration caches
        # the bytes wherever the source is, and the sample workspace holds
        # only what a run's restore writes there.
        from ..registration import register_sample

        for s in DEMO_SAMPLES:
            src = ASSETS_DIR / layout.SAMPLES_DIR_NAME / f"{s}.csv"
            tagged = f"{DEMO_MODULE}{s}"
            register_sample(
                name=tagged,
                source_path=src,
                project_root=target,
                allow_reserved=True,
            )
            print(f"Registered sample {tagged}")

        # ---- Step 8: pipeline document ----
        shutil.copy2(ASSETS_DIR / "pipeline.json", target / DEMO_PIPELINE_FILENAME)
        print(f"Wrote {DEMO_PIPELINE_FILENAME}")

        # ---- Step 8b: the demo schema stamp, last; committed with the
        # pipeline document so the tree is clean ----
        write_demo_stamp(target)

    print(
        f"\nDemo scaffolded: module {DEMO_MODULE} (5 methods), 3 samples, env "
        f"{DEMO_ENV}. The module, its methods and the samples carry the "
        f"{DEMO_MODULE} prefix, so the demo never takes a name you want for "
        f"your own work. Remove everything later with `wfc demo --remove`."
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
