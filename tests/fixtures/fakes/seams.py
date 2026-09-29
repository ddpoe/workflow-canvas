"""Internal seams: production functions replaced at a module attribute.

These are the "stated justification" cases: the boundary is not a process or
the OS but a production function the test's subject calls through a module
global, replaced so the test can reach one branch (a refusal, a recorder, a
raise) without the machinery behind the seam. Each entry says which seam,
why a test may take it, and which test drives the seam unfaked: the seam and
the reason are both in ``boundary``, whose last sentence starts "Why a test
may take it:"; ``backed_by`` names the unfaked test.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from ._entry import fake

#: The four injectable seams of the canvas registry routes.
REGISTRY_SEAMS = ("_register_sample_fn", "_register_module_fn",
                  "_register_method_fn", "_run_import_check_fn")


@fake(
    boundary="wfc.canvas.routes.registry._register_sample_fn / "
             "_register_module_fn / _register_method_fn / "
             "_run_import_check_fn -- the routes' injectable seams to "
             "registration and the import check. "
             "Why a test may take it: a refusal or a raise from "
             "registration or the import check needs a purpose-built "
             "broken project to produce for real, and the route's "
             "mapping of it is what is under test.",
    preserves="the route's own request parsing, error mapping and response "
              "shape; a dry-run's pre-checks",
    not_proven="that registration or the import check does what the route "
               "reports; only how the route maps what the seam returns or "
               "raises",
    backed_by="pm_mvp::tests.test_canvas_registry_endpoints::"
              "test_method_validate_does_not_run_main_block",
)
def stub_registry_seam(monkeypatch, seam: str, replacement: Callable) -> None:
    """Replace one of the registry routes' seams.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        seam: One of :data:`REGISTRY_SEAMS`.
        replacement: The callable the route calls instead.
    """
    from wfc.canvas.routes import registry as registry_routes

    if seam not in REGISTRY_SEAMS:
        raise ValueError(f"seam must be one of {REGISTRY_SEAMS}, not {seam!r}")
    monkeypatch.setattr(registry_routes, seam, replacement)


@fake(
    boundary="wfc.canvas.submission.run_pipeline_fn / fail_pipeline_fn -- the "
             "submission's handoff to run_pipeline and to the failure "
             "close-out. "
             "Why a test may take it: the submission's contract ends at "
             "the handoff; running the pipeline behind it is the run "
             "tests' subject.",
    preserves="the gate, enrichment, variable substitution and the job "
              "record before the handoff; the kwargs the handoff passes",
    not_proven="that the pipeline runs; the row states a real run leaves",
    backed_by="pm_mvp::tests.test_canvas_run::TestRunEndpoint."
              "test_real_path_submit_passes_real_git_gate",
)
@contextmanager
def stub_pipeline_submission(*, run_pipeline=None, fail_pipeline=None):
    """Replace the submission's handoff functions for a ``with`` block.

    Args:
        run_pipeline: What ``run_pipeline_fn()`` returns -- a callable the
            background thread invokes with the submission's kwargs.
        fail_pipeline: What ``fail_pipeline_fn()`` returns.

    Yields:
        None, with the patches active.
    """
    with ExitStack() as stack:
        if run_pipeline is not None:
            stack.enter_context(patch("wfc.canvas.submission.run_pipeline_fn",
                                      return_value=run_pipeline))
        if fail_pipeline is not None:
            stack.enter_context(patch("wfc.canvas.submission.fail_pipeline_fn",
                                      return_value=fail_pipeline))
        yield


@fake(
    boundary="wfc.storage.transport.push / pull_cache -- the DVC remote "
             "transfer. "
             "Why a test may take it: the DVC remote is a network "
             "boundary, and the caller's own queue and retry logic is "
             "what is under test.",
    preserves="the hashes and project asked for, recorded by a recording "
              "replacement; the push worker's own queue and retry logic",
    not_proven="that bytes reach or leave the remote",
    backed_by="pm_mvp::tests.test_dvc_sample_registration::"
              "test_push_pull_sample_round_trip",
)
def stub_transport(monkeypatch, *, push=None, pull_cache=None) -> None:
    """Replace the transport's push and/or pull.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        push: ``(hashes, project_dir, repairs=None) -> PushOutcome-like``
            (``.failed`` and optional ``.errors``), or raising.
        pull_cache: ``(hashes, project_dir) -> bool``, or raising.
    """
    from wfc.storage import transport

    if push is not None:
        monkeypatch.setattr(transport, "push", push)
    if pull_cache is not None:
        monkeypatch.setattr(transport, "pull_cache", pull_cache)


@fake(
    boundary="wfc.storage.cache.cache_file / restore_from_cache -- the "
             "cache's writers. "
             "Why a test may take it: a real cache write cannot be made "
             "to fail on demand, and the caller's cleanup and error "
             "handling around it is what is under test.",
    preserves="the caller's own cleanup and error handling around the write",
    not_proven="that the cache entry is written or restored",
    backed_by="pm_mvp::tests.test_content_hash::TestCacheOperations."
              "test_cache_file_creates_two_level_structure",
)
def stub_cache_writers(monkeypatch, *, cache_file=None,
                       restore_from_cache=None) -> None:
    """Replace the cache's file writers.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        cache_file: Replacement for ``cache_file``.
        restore_from_cache: Replacement for ``restore_from_cache``.
    """
    import wfc.storage.cache as cache

    if cache_file is not None:
        monkeypatch.setattr(cache, "cache_file", cache_file)
    if restore_from_cache is not None:
        monkeypatch.setattr(cache, "restore_from_cache", restore_from_cache)


@fake(
    boundary="wfc.storage.ensure_dvc_ready / init_dvc -- the DVC setup gate "
             "and initialiser as the package re-exports them. "
             "Why a test may take it: initialising DVC is slow and "
             "external, and what is under test is the caller's use of "
             "the root it passes.",
    preserves="the argument the caller passes (the project root), recorded",
    not_proven="that DVC is initialised or ready",
    backed_by="pm_mvp::tests.test_dvc_sample_registration::"
              "test_register_sample_stores_hash_and_caches",
)
def stub_dvc_setup(monkeypatch, *, ensure_ready=None, init=None) -> None:
    """Replace the DVC setup functions on the storage package.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        ensure_ready: Replacement for ``wfc.storage.ensure_dvc_ready``.
        init: Replacement for ``wfc.storage.init_dvc``.
    """
    import wfc.storage as storage

    if ensure_ready is not None:
        monkeypatch.setattr(storage, "ensure_dvc_ready", ensure_ready)
    if init is not None:
        monkeypatch.setattr(storage, "init_dvc", init)


@fake(
    boundary="wfc.export.export_output -- the export engine behind the CLI "
             "verb. "
             "Why a test may take it: the verb's validation and the "
             "kwargs it forwards are what is under test; the engine is "
             "tested on its own.",
    preserves="the verb's argument validation; the kwargs that reach the "
              "engine, recorded",
    not_proven="that anything is exported",
    backed_by="pm_mvp::tests.test_export_cli::test_export_copy_semantics",
)
def stub_export_engine(monkeypatch, replacement: Callable) -> None:
    """Replace the export engine the CLI resolves at call time.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        replacement: ``(**kwargs) -> int``.
    """
    monkeypatch.setattr("wfc.export.export_output", replacement)


@fake(
    boundary="wfc.registration.method._refuse_name_held_by_another_module -- "
             "the one-module-per-name interlock. "
             "Why a test may take it: the interlock forbids the state "
             "the reader under test must handle, so no production path "
             "builds it.",
    preserves="everything else about the second registration",
    not_proven="that the interlock refuses; the entry suspends it so a "
               "state it forbids can be built for a reader under test",
    backed_by="pm_mvp::tests.test_registration::"
              "test_method_name_held_by_another_module_is_refused",
)
def suspend_name_conflict_check(monkeypatch) -> None:
    """Suspend the one-module-per-name interlock for one registration.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
    """
    import wfc.registration.method as _registration

    monkeypatch.setattr(_registration, "_refuse_name_held_by_another_module",
                        lambda session, module, method_name: None)


@fake(
    boundary="scripts.dev_routes._submit_seed -- the demo's canvas submission "
             "of a reference-seeding pipeline. "
             "Why a test may take it: a real canvas submission needs a "
             "running canvas server, and the demo's own lookup, dedup "
             "and error surfacing is what is under test.",
    preserves="_ensure_reference_run's own lookup, dedup and error surfacing "
              "around the submission",
    not_proven="that the seed runs through the canvas",
    backed_by="pm_mvp::tests.integration.test_demo_seed_submission_unfaked::"
              "test_the_reference_root_demo_seeds_a_real_run_through_the_canvas",
)
def stub_seed_submission(monkeypatch, replacement: Callable) -> None:
    """Replace the demo's seed submission.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        replacement: ``(payload) -> job_id``.
    """
    from scripts import dev_routes

    monkeypatch.setattr(dev_routes, "_submit_seed", replacement)


@fake(
    boundary="wfc.registration.sample_health.load_sample_health -- the samples "
             "probe's row loader. "
             "Why a test may take it: a database failure of a chosen "
             "class cannot be produced on demand, and the probe's "
             "classification of it is what is under test.",
    preserves="check_samples' own classification of what the loader raises",
    not_proven="that a real database failure raises what the replacement "
               "raises",
    backed_by="pm_mvp::tests.test_project_root::"
              "test_check_samples_reads_the_rows_of_the_project_it_was_given",
)
def stub_sample_health_loader(monkeypatch, replacement: Callable) -> None:
    """Replace the samples probe's row loader.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        replacement: ``(session, project_dir) -> rows``, or raising.
    """
    from wfc.registration import sample_health

    monkeypatch.setattr(sample_health, "load_sample_health", replacement)
