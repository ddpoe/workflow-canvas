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
from pathlib import Path
from unittest.mock import patch

from ._entry import fake

#: The three injectable seams of the canvas registry routes.
REGISTRY_SEAMS = ("_register_sample_fn", "_register_module_fn",
                  "_register_method_fn")


@fake(
    boundary="wfc.canvas.routes.registry._register_sample_fn / "
             "_register_module_fn / _register_method_fn -- the routes' "
             "injectable seams to registration. "
             "Why a test may take it: a refusal or a raise from "
             "registration needs a purpose-built broken project to produce "
             "for real, and the route's mapping of it is what is under test.",
    preserves="the route's own request parsing, error mapping and response "
              "shape; a dry-run's pre-checks",
    not_proven="that registration does what the route reports; only how the "
               "route maps what the seam returns or raises",
    backed_by="pm_mvp::tests.test_canvas_registry_endpoints::"
              "test_method_dry_run_states_the_plan_and_register_applies_it",
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
    boundary="wfc.version's importlib.metadata.version -- the installed "
             "distribution's metadata lookup. "
             "Why a test may take it: a test runs from an installed wfc, so "
             "metadata that is missing (a bare source tree) or unreadable "
             "cannot be produced for real, and the helper's fallback is what "
             "is under test.",
    preserves="installed_wfc_version's own handling of a missing or "
              "unreadable distribution: the None it returns and the warning "
              "it logs",
    not_proven="which installs lack the metadata or hold it unreadable; "
               "that the installed version is right",
    backed_by="pm_mvp::tests.test_wfc_version::"
              "test_returns_the_installed_distribution_version",
)
def stub_distribution_metadata_missing(monkeypatch,
                                       error: Exception | None = None) -> None:
    """Make wfc's distribution metadata lookup raise.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        error: The exception the lookup raises; ``None`` raises
            ``PackageNotFoundError`` (the metadata is not installed).
    """
    import importlib.metadata

    from wfc import version

    def _missing(name):
        if error is not None:
            raise error
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(version.importlib.metadata, "version", _missing)


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


@fake(
    boundary="wfc.storage.cache._hashing_copy -- the cache's one hashing "
             "copy of a file into a tmp beside the cache root. "
             "Why a test may take it: a source that changes while it is being "
             "copied, or a write that fails part-way, is a race or a full disk "
             "no test can produce on demand; the archive's handling of each is "
             "what is under test.",
    preserves="the archive pass's checks before and after the copy, its "
              "refusal, the changed mark and the per-row commit; the fault "
              "receives the real copy and may call it",
    not_proven="that a real concurrent writer or a real disk failure "
               "surfaces exactly as the fault does",
    backed_by="pm_mvp::tests.test_output_integrity::"
              "test_the_archive_reuses_collects_hash_and_still_verifies_the_copy",
)
def fault_at_hashing_copy(monkeypatch, fault: Callable) -> None:
    """Replace the cache's hashing copy with a fault that wraps it.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        fault: ``fault(real, src, target) -> str``; ``real`` is the
            production copy, which the fault may call before or after doing
            its damage, or not at all.
    """
    from wfc.storage import cache

    real = cache._hashing_copy
    monkeypatch.setattr(cache, "_hashing_copy",
                        lambda src, target: fault(real, src, target))


@fake(
    boundary="wfc.execution.collect.hash_path -- collect's hash of a file "
             "output under [integrity] inputs = \"hash\". "
             "Why a test may take it: an output the method wrote but the "
             "runner cannot read needs file permissions a root user or "
             "Windows does not enforce; collect's ending for it is what is "
             "under test.",
    preserves="collect's slot location, its refusal ending and message, and "
              "the record tail",
    not_proven="that a real unreadable file raises the same OSError",
    backed_by="pm_mvp::tests.test_output_integrity::"
              "test_a_pre_archive_output_edit_is_caught_before_the_read",
)
def unreadable_output_hash(monkeypatch) -> None:
    """Make collect's hash of a file output raise ``PermissionError``.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
    """
    from wfc.execution import collect

    def refuse(path):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(collect, "hash_path", refuse)


@fake(
    boundary="wfc.execution.collect.record_output -- Storage's stat record of "
             "an output, as collect takes it before writing the rows. "
             "Why a test may take it: a stat failure between the method's "
             "exit and the row cannot be produced on demand; the run's "
             "failure naming the slot is what is under test.",
    preserves="collect's slot location, its unrecorded-output ending and the "
              "record tail, which fails the run naming the slot; no output "
              "row is written",
    not_proven="that a real stat or database failure raises what the "
               "replacement raises",
    backed_by="pm_mvp::tests.test_output_integrity::"
              "test_a_pre_archive_output_edit_is_caught_before_the_read",
)
def fault_at_record_output(monkeypatch, error: Exception) -> None:
    """Make collect's stat record of an output raise.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        error: The exception the record raises.
    """
    from wfc.execution import collect

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(collect, "record_output", fail)


@fake(
    boundary="wfc.storage.integrity.entry_record -- the integrity check's stat "
             "read of an entry (size, mtime, change time and, on Windows, the "
             "file index), as check_entry calls it before any byte is read. "
             "Why a test may take it: every write moves the change time on "
             "Linux, macOS and Windows (NTFS) and no ordinary API puts it back, "
             "so an edited entry whose stat record still matches its stamp or "
             "collect's record cannot be produced for real; that such a record "
             "does not vouch under [integrity] inputs = \"hash\" is what is "
             "under test.",
    preserves="the entry's real stat record, read once when the hold begins "
              "and returned for that one entry on every later check; every "
              "other entry's read, the stamps and collect's records, the "
              "setting, the re-hash and the reader's repair or refusal",
    not_proven="that a real edit leaves the stat record unchanged (none does "
               "on a volume that keeps a change time); what the check decides "
               "under the default for a record that matches",
    backed_by="pm_mvp::tests.test_directory_cache_entry::"
              "test_a_same_size_checkout_edit_with_the_mtime_restored_is_caught",
)
def hold_stat_record(monkeypatch, entry) -> dict:
    """Hold the integrity check's stat read of one entry at its record now.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        entry: The file or directory whose stat record is held.

    Returns:
        The held record, as ``entry_record`` shapes it.
    """
    import os

    from wfc.storage import integrity

    real = integrity.entry_record

    def key(path) -> str:
        return os.path.normcase(os.path.abspath(os.fspath(path)))

    held_key = key(entry)
    held = real(entry)

    def holding(path):
        return dict(held) if key(path) == held_key else real(path)

    monkeypatch.setattr(integrity, "entry_record", holding)
    return held


@fake(
    boundary="wfc.storage.cache._make_read_only -- the chmod check_sample's "
             "protect step applies to an intact restored copy that is still "
             "writable. "
             "Why a test may take it: a write landing between the check's "
             "verdict and the protect step is a race no test can time on "
             "demand; what the next check finds after it is what is under test.",
    preserves="the check's verdict and the stamp it wrote, the real chmod "
              "(the fault receives it and calls it after its write), the next "
              "check, its re-hash and the repair",
    not_proven="that a real concurrent writer lands in exactly that window",
    backed_by="pm_mvp::tests.test_cache_integrity_edges::"
              "test_an_intact_writable_directory_sample_is_made_read_only_and_re_hashed_once",
)
def fault_at_protect_chmod(monkeypatch, fault: Callable) -> None:
    """Replace the cache's read-only chmod with a fault that wraps it.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        fault: ``fault(real, path) -> None``; ``real`` is the production
            chmod, which the fault calls after doing its damage.
    """
    from wfc.storage import cache

    real = cache._make_read_only
    monkeypatch.setattr(cache, "_make_read_only", lambda path: fault(real, path))


@fake(
    boundary="wfc.storage.resolve.pull_again -- pulling a pushed entry again, "
             "as wfc verify --repair and the sample restore call it. "
             "Why a test may take it: a remote that refuses one pull and "
             "serves the next cannot be produced on demand; how verify "
             "records the later success is what is under test.",
    preserves="every later pull (the real one runs from the second call on), "
              "the entry's changed objects, which the refused pull leaves in "
              "place, verify's walk, its memo and its report",
    not_proven="that a real failed pull leaves the local objects as the "
               "refusal does (the real one sets them aside first)",
    backed_by="pm_mvp::tests.test_verify::"
              "test_a_changed_sample_entry_is_pulled_again_when_pushed_and_listed_when_not",
)
def fail_first_pull_again(monkeypatch) -> list[str]:
    """Make the first pull again refuse with ``NotInCacheError``; later ones run.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        The list the pulled entries' hashes accumulate into, the refused
        one first.
    """
    from wfc.storage import resolve

    pulled: list[str] = []
    real = resolve.pull_again

    def pulling(project_dir, content_hash, *args, **kwargs):
        pulled.append(content_hash)
        if len(pulled) == 1:
            raise resolve.NotInCacheError(
                f"the pull of {content_hash} did not reach the remote (test refusal)")
        return real(project_dir, content_hash, *args, **kwargs)

    monkeypatch.setattr(resolve, "pull_again", pulling)
    return pulled


@fake(
    boundary="wfc.storage.links.replace_over -- the one rename that puts a "
             "view built beside its destination (a restored sample, a "
             "checkout member) over that destination. "
             "Why a test may take it: a crash or a refused rename between the "
             "view's build and its rename cannot be produced on demand; what "
             "the destination holds after it is what is under test.",
    preserves="the real check of the object, the real view built beside the "
              "destination and its clean-up, every rename before the "
              "interrupted one and every one after it; the chosen rename "
              "raises before it moves anything",
    not_proven="that a real crash or a real refused rename leaves the "
               "filesystem as the raise does (one rename on one filesystem "
               "leaves the old file or the new one standing, never neither)",
    backed_by="pm_mvp::tests.test_cache_provenance_primitives::"
              "test_corruption_detected_and_replaced",
)
def interrupt_view_rename(monkeypatch, *, at_call: int = 1,
                          error: Exception | None = None) -> list[tuple[Path, Path]]:
    """Make the Nth rename of a built view over its destination raise.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        at_call: Which call (1-based) raises; the others rename for real.
        error: The exception raised; ``None`` raises an ``OSError`` (EIO).

    Returns:
        A list that receives the ``(built view, destination)`` pair whose
        rename raised.
    """
    import errno
    from pathlib import Path as _Path

    from wfc.storage import links

    real = links.replace_over
    calls: list[int] = []
    interrupted: list[tuple[Path, Path]] = []

    def replace_over(src, dst):
        calls.append(1)
        if len(calls) == at_call:
            interrupted.append((_Path(src), _Path(dst)))
            raise error if error is not None else OSError(
                errno.EIO, "the rename over the destination was interrupted (test fault)",
                str(dst))
        return real(src, dst)

    monkeypatch.setattr(links, "replace_over", replace_over)
    return interrupted


@fake(
    boundary="wfc.storage.links.reflink / hardlink -- the two link tiers "
             "make_view and the doctor's view probe try before the copy. "
             "Why a test may take it: a cache and a workspace on different "
             "filesystems, or a filesystem that refuses links, cannot be "
             "mounted on demand; the copy fallback and the doctor's warning "
             "are what is under test.",
    preserves="the real hashing copy, its read-only marking and its rename "
              "over the destination; the probe's real scratch files, its "
              "reason for the refusal and the readiness row's wording",
    not_proven="that a real cross-device link raises the error the "
               "replacement raises, or that a real filesystem without links "
               "is detected",
    backed_by="pm_mvp::tests.test_views::"
              "test_a_view_made_and_removed_costs_its_object_no_re_hash",
)
def refuse_view_links(monkeypatch, code: int | None = None) -> None:
    """Make every reflink and hardlink of a view raise ``OSError``.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        code: The ``errno`` raised; ``None`` raises ``EXDEV`` (the two
            directories are on different filesystems).
    """
    import errno
    import os

    from wfc.storage import links

    err = errno.EXDEV if code is None else code

    def refuse(src, dst):
        raise OSError(err, os.strerror(err), str(dst))

    monkeypatch.setattr(links, "reflink", refuse)
    monkeypatch.setattr(links, "hardlink", refuse)


@fake(
    boundary="wfc.storage.integrity.stat_fields -- the stat fields the "
             "integrity check records for one file, as entry_record calls it "
             "for every stamp and every check. "
             "Why a test may take it: a volume that keeps no change time "
             "(FAT32, exFAT) cannot be mounted on demand; what check_sample "
             "does with a stamp that records none is what is under test.",
    preserves="the real fields (size, mtime and, on Windows, the file index) "
              "read from the real file, for stamps and checks alike, the "
              "check's verdict, the walk and the protect step",
    not_proven="that a real FAT32 or exFAT volume reports exactly these "
               "fields (a Windows read of one returns no change time)",
    backed_by="pm_mvp::tests.test_cache_integrity_edges::"
              "test_an_intact_copy_is_walked_for_a_write_bit_only_when_re_hashed",
)
def volume_without_change_time(monkeypatch) -> None:
    """Make every stat record leave out the change time, as FAT32 and exFAT do.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
    """
    from wfc.storage import integrity

    real = integrity.stat_fields

    def without_change_time(*args, **kwargs):
        fields = real(*args, **kwargs)
        fields.pop("ctime_ns", None)
        return fields

    monkeypatch.setattr(integrity, "stat_fields", without_change_time)


@fake(
    boundary="wfc.execution.collect._write_output_row -- one slot's RunOutput "
             "row write inside collect's write transaction",
    preserves="everything: the real stat records, the real transaction, every "
              "row write before the failing one (flushed into the "
              "transaction) and the record tail; only the chosen write raises",
    not_proven="that a real database error raises what the replacement "
               "raises",
    backed_by="undrivable: a write failing part-way through one transaction "
              "cannot be produced on demand on a healthy database",
)
def fault_at_output_row_write(monkeypatch, error: Exception, *,
                              at_write: int = 2) -> list[str]:
    """Make collect's Nth output-row write raise, inside its transaction.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        error: The exception the write raises.
        at_write: Which write (1-based) raises; the earlier ones are written.

    Returns:
        A list that receives the slot whose write raised.
    """
    from wfc.execution import collect

    real = collect._write_output_row
    calls: list[str] = []
    failed: list[str] = []

    def write(session, run_id, slot, *args, **kwargs):
        calls.append(slot)
        if len(calls) == at_write:
            failed.append(slot)
            raise error
        return real(session, run_id, slot, *args, **kwargs)

    monkeypatch.setattr(collect, "_write_output_row", write)
    return failed


@fake(
    boundary="wfc.execution.record.release_run -- the release of a run's "
             "in-use records, the record transaction's last write",
    preserves="everything before it in the record transaction: the real flip "
              "and the real push enqueue, which the raise then rolls back",
    not_proven="that a real database error raises what the replacement "
               "raises",
    backed_by="undrivable: a write failing inside one transaction cannot be "
              "produced on demand on a healthy database",
)
def fault_at_run_release(monkeypatch, error: Exception) -> None:
    """Make the record phase's release of a run's in-use records raise.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        error: The exception the release raises.
    """
    from wfc.execution import record

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(record, "release_run", fail)


@fake(
    boundary="wfc.registration.registered_code.sync_rows_in -- the version-row "
             "writes, the last writes of registration's row transaction "
             "(_write_registration_rows imports it at call time)",
    preserves="everything: the real module lookup, the real method row, the "
              "real tracked functions and parameters, and the real version "
              "rows, all flushed into the real transaction; the raise comes "
              "after them and the transaction rolls them back",
    not_proven="that a real database error raises what the replacement "
               "raises",
    backed_by="undrivable: a write failing inside one transaction cannot be "
              "produced on demand on a healthy database",
)
def fault_at_version_rows_write(monkeypatch, error: Exception) -> list[list[int]]:
    """Make registration's version-row writes raise once they are flushed.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        error: The exception raised after the real writes.

    Returns:
        A list that receives, per raise, the labels of the version rows the
        transaction held when it raised.
    """
    from wfc.registration import registered_code

    real = registered_code.sync_rows_in
    flushed: list[list[int]] = []

    def write_then_fail(*args, **kwargs):
        synced = real(*args, **kwargs)
        flushed.append([s.entry.label for s in synced])
        raise error

    monkeypatch.setattr(registered_code, "sync_rows_in", write_then_fail)
    return flushed


@fake(
    boundary="wfc.execution.claim.lookup_cache_hit -- wrapped to answer for "
             "real, then run a real prune of the code copies once, between "
             "the claim's last look at its copy and its run row",
    preserves="everything: the real claim (the in-use record, the resolver, "
              "the verify rule, the hit rule, the claim transaction) and the "
              "real prune_code_copies; only the moment the prune runs is chosen",
    not_proven="that a prune can interleave with a claim in a real run; only "
               "what the claim and the prune do when one has",
    backed_by="undrivable: the window is between two statements of one "
              "pre_run call, a race no production path sequences",
)
def prune_during_claim(monkeypatch) -> list[list[Path]]:
    """Run ``prune_code_copies`` once, inside a claim, before its run row.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        A list that receives the prune's result (the copies it removed).
    """
    import importlib

    from wfc.persistence import project_root
    from wfc.storage import prune_code_copies

    claim_mod = importlib.import_module("wfc.execution.claim")
    real = claim_mod.lookup_cache_hit
    pruned: list[list[Path]] = []

    def answer_then_prune(*args, **kwargs):
        answer = real(*args, **kwargs)
        if not pruned:
            pruned.append(prune_code_copies(project_root()))
        return answer

    monkeypatch.setattr(claim_mod, "lookup_cache_hit", answer_then_prune)
    return pruned


@fake(
    boundary="wfc.execution.claim.record_run_inputs -- the claim's input-row "
             "write inside its claim transaction, on the hit path (after the "
             "audit row) and the miss path (after the running row)",
    preserves="everything: the real run row and the real input rows, flushed "
              "into the real claim transaction; the raise comes after them "
              "and the transaction rolls them back",
    not_proven="that a real database error raises what the replacement "
               "raises",
    backed_by="undrivable: a write failing inside one transaction cannot be "
              "produced on demand on a healthy database",
)
def fault_after_claim_input_rows(monkeypatch) -> None:
    """Make the claim's input-row write raise once its rows are flushed.

    Installed in a child process by its own ``pytest.MonkeyPatch`` (the
    claim-concurrency witnesses run each claim in a process of its own).

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
    """
    import importlib

    claim_mod = importlib.import_module("wfc.execution.claim")
    real = claim_mod.record_run_inputs

    def inputs_then_fail(session, *args, **kwargs):
        real(session, *args, **kwargs)
        session.flush()
        raise RuntimeError("injected after the claim's first rows")

    monkeypatch.setattr(claim_mod, "record_run_inputs", inputs_then_fail)


@fake(
    boundary="wfc.version._git -- every git command registration runs, wrapped "
             "so the first `git commit -m` of two registering processes waits "
             "on a file barrier: each waits for the other to reach its own "
             "commit, and B's runs only after A's has landed",
    preserves="everything: every git command runs for real, with its real "
              "arguments and result; only the moment the first commit runs "
              "is chosen",
    not_proven="that two real registrations interleave in exactly this "
               "order; only what the second does when they have",
    backed_by="undrivable: the window is between two processes' commits, a "
              "race no production path sequences",
)
def order_registration_commits(monkeypatch, name: str, signals: Path) -> None:
    """Order two processes' first registration commits through a file barrier.

    Installed in each of two child processes (``A`` and ``B``) by its own
    ``pytest.MonkeyPatch``. Each writes ``<name>-ready`` at its first
    ``git commit -m`` and waits for its peer's; B then waits for
    ``A-done``, which A writes once its commit returned.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        name: ``"A"`` or ``"B"``.
        signals: The directory the barrier files are written in.
    """
    import time

    from wfc import version

    peer = "B" if name == "A" else "A"
    real_git = version._git
    first = [True]

    def wait_for(path: Path) -> None:
        deadline = time.monotonic() + 120
        while not path.exists():
            if time.monotonic() > deadline:
                raise SystemExit(f"{name}: no {path.name} within 120 s")
            time.sleep(0.02)

    def ordered_git(args, cwd, *rest, **kwargs):
        if first[0] and "commit" in args and "-m" in args:
            first[0] = False
            (signals / f"{name}-ready").write_text("")
            wait_for(signals / f"{peer}-ready")
            if name == "B":
                wait_for(signals / "A-done")
            result = real_git(args, cwd, *rest, **kwargs)
            if name == "A":
                (signals / "A-done").write_text("")
            return result
        return real_git(args, cwd, *rest, **kwargs)

    monkeypatch.setattr(version, "_git", ordered_git)
