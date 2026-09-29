"""Declared shortcuts: product state written directly, bypassing its writer.

A shortcut is not a fake -- nothing is replaced -- but it produces a state a
route would otherwise produce, and so inherits the same duty: to say what it
writes that the writer would not have, and what a witness built on it cannot
claim. The routes are the default; each entry here says when the shortcut is
the honest choice instead.
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import SQLModel, create_engine

from ._entry import shortcut


@shortcut(
    boundary="wfc.init.init_project -- the marker .wfc/wf-canvas.toml alone, "
             "with no [dvc] section, no schema, no archive",
    preserves="what the canonical project resolver needs to accept the "
              "directory",
    not_proven="anything init lays down; nothing built on it can register a "
               "sample",
    backed_by="pm_mvp::tests.test_init_wizard::"
              "test_init_yes_lands_runnable_project",
)
def write_wfc_marker(project_dir: Path) -> None:
    """Write the bare ``.wfc/wf-canvas.toml`` the project resolver needs.

    A deliberate stub, not a project: the config carries no ``[dvc]``
    section, so ``ensure_dvc_ready`` refuses and nothing built on it can
    call ``register_sample``. Serves a directory to a reader (the canvas
    resolver, the Snakefile emitter) that never registers anything. A
    fixture that hands a project to production code goes through
    ``init_test_project`` instead; ``tmp_project`` does.

    Args:
        project_dir: The directory to mark.
    """
    wfc_dir = project_dir / ".wfc"
    wfc_dir.mkdir(parents=True, exist_ok=True)
    marker = wfc_dir / "wf-canvas.toml"
    if not marker.exists():
        marker.write_text('[project]\nname = "test"\n')


@shortcut(
    boundary="wfc.init.init_project -- the marker plus every table built on "
             "an empty .wfc/wfc.db",
    preserves="what the canvas resolver needs to accept the directory and "
              "a server session needs to open it",
    not_proven="anything init lays down: no [dvc] section, no methods/, "
               "nothing registered",
    backed_by="pm_mvp::tests.test_init_wizard::"
              "test_init_yes_lands_runnable_project",
)
def make_marker_project(project_dir: Path) -> Path:
    """Lay the marker shortcut and an empty project database at ``project_dir``.

    ``write_wfc_marker`` plus every table built on an empty ``.wfc/wfc.db``:
    the least a directory needs for the canvas resolver to accept it and for
    a server session to open it. The same deliberate stub as the marker --
    no ``[dvc]`` section, no ``methods/``, nothing registered -- so it serves
    a test that exercises the resolver and the server's binding, not one
    that hands the project to production. A test that needs a project a
    user could have builds one through ``init_test_project`` or the harness
    instead.

    Args:
        project_dir: The directory to make resolvable. Created when missing.

    Returns:
        The directory, resolved.
    """
    import wfc.persistence  # noqa: F401  (registers every table on the metadata)

    project_dir = Path(project_dir).resolve()
    project_dir.mkdir(parents=True, exist_ok=True)
    write_wfc_marker(project_dir)
    engine = create_engine(f"sqlite:///{project_dir / '.wfc' / 'wfc.db'}")
    SQLModel.metadata.create_all(engine)
    engine.dispose()
    return project_dir


@shortcut(
    boundary="wfc.environments.register -- the fixture-env record in "
             ".wfc/envs.json, written through production serialization with "
             "a placeholder digest instead of a build or a pull",
    preserves="the record's shape, fingerprints and manifest serialization "
              "(write_env_record is the route); only the digest is a "
              "placeholder",
    not_proven="that the env image exists; registration and dispatch check "
               "the container ref's shape only",
    backed_by="pm_mvp::tests.integration.test_containerized_step_runs::"
              "test_run_step_in_container_writes_output_to_host",
)
def fixture_env_record(project_dir: Path) -> None:
    """Write a placeholder ``fixture-env`` container record into ``.wfc/envs.json``.

    The fixture methods declare ``env: fixture-env``; registration only
    validates the container ref SHAPE (no image pull), so a placeholder
    digest lets Docker-free unit tests register fixture methods. Real
    end-to-end execution (tests/e2e, tests/integration) overwrites this with
    the session-built image digest via ``register_fixture_methods``.

    Only called from ``tmp_project`` (which copies fixture methods) -- NOT
    from ``write_wfc_marker``, so env-listing tests that expect a pristine
    empty manifest are unaffected.

    Args:
        project_dir: The project root.
    """
    from tests.fixtures.conftest import FIXTURE_ENV_NAME
    from tests.fixtures.routes import write_env_record

    envs_json = project_dir / ".wfc" / "envs.json"
    if not envs_json.exists():
        # Placeholder digest; the record itself goes through production
        # serialization (byo backend -- the fixture image is a raw docker
        # build attached by digest, with `python` on PATH).
        write_env_record(project_dir, FIXTURE_ENV_NAME, digest="a" * 64)


@shortcut(
    boundary="wfc.registration.register_sample -- one Sample row inserted "
             "directly",
    preserves="the row's columns as registration would fill them, with a "
              "content_hash derived from the name",
    not_proven="that any bytes exist: the content_hash addresses nothing in "
               "the DVC cache, so a pipeline or restore over this sample "
               "fails on bytes that are nowhere",
    backed_by="pm_mvp::tests.test_dvc_sample_registration::"
              "test_register_sample_stores_hash_and_caches",
)
def seed_sample_row(name: str, content_hash: str | None = None) -> None:
    """Insert one ``Sample`` row directly, bypassing registration.

    A declared shortcut, not the default route. ``tmp_project`` is a real
    ``init_project`` output, so a test that needs a sample a *user* could
    have calls ``create_sample_csv`` and gets one: cached bytes, a content
    hash that addresses them, and a ``registered_path`` a restore has to
    write. Use this only where the row itself is the input under test --
    a cache-key composition, a cache lookup, a claim's row resolution, a
    canvas reader over the registry -- or where the row is a deviation
    production cannot produce, such as the legacy NULL-hash shape. Every
    caller says which in a comment at its import.

    Plain function (no fixture dependency) so tests at any depth can call it
    with a database already open.

    Args:
        name: The sample name the drive passes.
        content_hash: MD5 hex for the row. Defaults to a hash derived from
            the name, so distinct samples carry distinct content the way
            registered samples do.
    """
    import hashlib

    from wfc.persistence import get_session, Sample

    if content_hash is None:
        content_hash = hashlib.md5(name.encode()).hexdigest()

    with get_session() as session:
        session.add(Sample(
            name=name,
            source_path=f"/src/{name}.csv",
            registered_path=f"data/samples/{name}/{name}.csv",
            file_type="csv",
            file_size=len(name),
            file_mtime=1.0,
            registration_mode="copy",
            content_hash=content_hash,
        ))
        session.commit()


@shortcut(
    boundary="the persistence models' writers (the claim, collect and record "
             "phases; registration) -- rows typed in and committed as given",
    preserves="only the columns the caller typed",
    not_proven="that any writer produces the rows: ids, paths and statuses "
               "are literals, and a reader of the archive or cache finds "
               "nothing the caller did not also write. The four stays: a "
               "reader over archived runs, the nullable-started_at cancelled "
               "row, a corrupted contract, and rows no route can produce",
    backed_by="pm_mvp::tests.test_harness_smoke::"
              "test_no_argument_scenario_is_a_valid_one_node_run",
)
def typed_rows(session, *rows) -> None:
    """Add typed-in model rows to a session and commit.

    The one sanctioned way to write a persistence row by hand where the row
    itself is the input under test or a state no writer produces; the
    module's ``ROW_BUILDER_ALLOWLIST`` entry carries the reason.

    Args:
        session: An open ``Session``.
        *rows: Model instances to add.
    """
    for row in rows:
        session.add(row)
    session.commit()


@shortcut(
    boundary="wfc.execution.record's pending flip -- push_status set to "
             "pending by hand on an archived RunOutput row",
    preserves="the row and its real content_hash and cache blob, produced "
              "by the route and archive_outputs",
    not_proven="that the record phase flips the status when a remote is "
               "configured at run time",
    backed_by="pm_mvp::tests.test_host_tool_witnesses::"
              "test_a_step_run_with_a_remote_configured_leaves_its_outputs_pending",
)
def flip_push_status(run_id: int, status: str) -> None:
    """Set every ``RunOutput`` row of a run to one push status.

    Args:
        run_id: The run whose output rows are flipped.
        status: The ``PushStatus`` value to write.
    """
    from sqlmodel import select

    from wfc.persistence import RunOutput, get_session

    with get_session() as session:
        for row in session.exec(select(RunOutput).where(RunOutput.run_id == run_id)):
            row.push_status = status
            session.add(row)
        session.commit()


@shortcut(
    boundary="wfc.execution.run_step -- the claim phase (pre_run) called "
             "directly, outside the sequencer",
    preserves="the claim itself: the real row, the real cache key",
    not_proven="anything the phases after the claim do, or that run_step "
               "would have reached the claim with these arguments",
    backed_by="pm_mvp::tests.test_harness_smoke::"
              "test_no_argument_scenario_is_a_valid_one_node_run",
)
def direct_claim(**pre_run_kwargs):
    """Call the claim phase directly, for a test about the key it composes.

    Args:
        **pre_run_kwargs: ``pre_run``'s own keyword arguments.

    Returns:
        What ``pre_run`` returns.
    """
    from wfc.execution.claim import pre_run

    return pre_run(**pre_run_kwargs)
