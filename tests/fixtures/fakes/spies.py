"""Spies: patch sites that wrap the real callable and call through.

Nothing here replaces behaviour; each entry records what a production
function was called with, or observes state mid-call, and delegates. A spy
proves nothing less than the unpatched call would, which is why its
``backed_by`` is the harness witness that runs with the spy installed.
"""

from __future__ import annotations

import pathlib
import threading

from ._entry import spy


@spy(
    boundary="wfc.execution.run_step's five phase entry points, rebound to "
             "record-then-delegate wrappers (tests.harness.drivers._interpose)",
    preserves="everything: production sequences the phases, the wrapper "
              "records each phase reached and its arguments, and raises the "
              "harness's private stop signal only past the stopping point",
    not_proven="nothing",
    backed_by="pm_mvp::tests.test_harness_smoke::"
              "test_no_argument_scenario_is_a_valid_one_node_run",
)
def interpose_phases(orchestrator, record, through, monkeypatch):
    """Wrap every phase entry point on the orchestrator module.

    Delegates to the harness's own context manager; the harness keeps the
    body because the wrappers close over its ``TargetRun`` record.

    Args:
        orchestrator: The ``wfc.execution.run_step`` module.
        record: The target's execution record.
        through: The stopping point, or ``None``.
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        The context manager.
    """
    from tests.harness.drivers import _interpose

    return _interpose(orchestrator, record, through, monkeypatch)


@spy(
    boundary="wfc.execution.record.complete_run, wrapped to snapshot the "
             "RunOutput rows either side of the call "
             "(tests.harness.drivers._snapshot_output_writers)",
    preserves="everything: the real writer runs; the rows before and after "
              "are read for the invariant pack",
    not_proven="nothing",
    backed_by="pm_mvp::tests.test_harness_smoke::"
              "test_no_argument_scenario_is_a_valid_one_node_run",
)
def snapshot_output_writers(record, monkeypatch):
    """Snapshot the output rows either side of the second writer's call.

    Args:
        record: The target's execution record.
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        The context manager.
    """
    from tests.harness.drivers import _snapshot_output_writers

    return _snapshot_output_writers(record, monkeypatch)


@spy(
    boundary="wfc.canvas.wfc_provider.Session -- the session class the "
             "provider's reload opens",
    preserves="everything: the subclass observes the provider's state on "
              "entry and delegates to the real session",
    not_proven="nothing",
    backed_by="pm_mvp::tests.test_wfc_provider_atomic_reload::"
              "test_readers_mid_reload_see_previous_complete_state",
)
def probing_provider_session(monkeypatch, session_cls) -> None:
    """Bind a ``Session`` subclass the provider's reload opens.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        session_cls: A ``sqlmodel.Session`` subclass whose ``__enter__``
            records what a concurrent reader would see, then delegates.
    """
    monkeypatch.setattr("wfc.canvas.wfc_provider.Session", session_cls)


@spy(
    boundary="pathlib.Path.iterdir -- every directory listing during a call",
    preserves="everything: each listing proceeds; the paths listed are "
              "recorded",
    not_proven="nothing",
    backed_by="pm_mvp::tests.test_fan_in_single_selector::"
              "test_generate_snakefile_collapsed_fanin_no_filesystem_inspection",
)
def spy_directory_listings(monkeypatch) -> list[str]:
    """Record every directory the code under test lists.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        The list the recorded paths accumulate into.
    """
    listed: list[str] = []
    original_iterdir = pathlib.Path.iterdir

    def recording_iterdir(self):
        listed.append(str(self))
        return original_iterdir(self)

    monkeypatch.setattr(pathlib.Path, "iterdir", recording_iterdir)
    return listed


@spy(
    boundary="wfc.identity.content_hash.hash_file (and its binding in "
             "wfc.storage.cache) and wfc.storage.cache._hashing_copy -- every "
             "whole-file hash Identity computes and every hashing copy",
    preserves="everything: each call proceeds; the paths read are recorded",
    not_proven="nothing",
    backed_by="pm_mvp::tests.test_output_integrity::"
              "test_the_archive_reuses_collects_hash_and_still_verifies_the_copy",
)
def spy_content_reads(monkeypatch) -> dict[str, list[str]]:
    """Record every file hashed and every file copied with the hashing copy.

    ``hash_path`` and ``directory_manifest`` reach ``hash_file`` through
    Identity's module global, so one patch there sees every whole-file hash.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        ``{"hashed": [...], "copied": [...]}``, filled as calls happen.
    """
    from wfc.identity import content_hash
    from wfc.storage import cache

    reads: dict[str, list[str]] = {"hashed": [], "copied": []}
    real_hash = content_hash.hash_file
    real_copy = cache._hashing_copy

    def hashing(path):
        reads["hashed"].append(str(path))
        return real_hash(path)

    def copying(src, target):
        reads["copied"].append(str(src))
        return real_copy(src, target)

    monkeypatch.setattr(content_hash, "hash_file", hashing)
    monkeypatch.setattr(cache, "hash_file", hashing)
    monkeypatch.setattr(cache, "_hashing_copy", copying)
    return reads


@spy(
    boundary="wfc.storage.integrity.read_config -- the config read behind "
             "inputs_setting, every reader's [integrity] inputs lookup",
    preserves="everything: each read proceeds; the project read is recorded",
    not_proven="nothing",
    backed_by="pm_mvp::tests.test_output_integrity::"
              "test_the_setting_is_read_once_per_hit_pass_and_once_per_push",
)
def spy_setting_reads(monkeypatch) -> list[str]:
    """Record every read of ``[integrity] inputs`` through ``inputs_setting``.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        The list the project directories read accumulate into.
    """
    from wfc.storage import integrity

    reads: list[str] = []
    real = integrity.read_config

    def reading(project_dir):
        reads.append(str(project_dir))
        return real(project_dir)

    monkeypatch.setattr(integrity, "read_config", reading)
    return reads


@spy(
    boundary="wfc.storage.restore._writable_anywhere -- check_sample's walk of "
             "an intact restored copy for a write bit, before it is protected",
    preserves="everything: each walk proceeds; the copy walked is recorded",
    not_proven="nothing",
    backed_by="pm_mvp::tests.test_cache_integrity_edges::"
              "test_an_intact_copy_is_walked_for_a_write_bit_only_when_re_hashed",
)
def spy_writable_walks(monkeypatch) -> list[str]:
    """Record every restored copy check_sample walks for a write bit.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        The list the walked copies accumulate into.
    """
    from wfc.storage import restore

    walked: list[str] = []
    real = restore._writable_anywhere

    def walking(path):
        walked.append(str(path))
        return real(path)

    monkeypatch.setattr(restore, "_writable_anywhere", walking)
    return walked


@spy(
    boundary="wfc.execution.claim.resolve_claim_params -- the claim's params "
             "door, the one place the claim reads a method's version list "
             "(version_contracts)",
    preserves="everything: each resolution proceeds; its module, method, "
              "version and node are recorded",
    not_proven="nothing",
    backed_by="pm_mvp::tests.test_claim_declared_defaults::"
              "test_a_sparse_pipeline_records_runs_and_keys_its_filled_params",
)
def spy_claim_resolutions(monkeypatch) -> list[tuple]:
    """Record every pass through the claim's params door.

    ``run_claim`` and ``pre_run`` reach the door through the claim module's
    global, so one patch there sees every resolution a claim makes.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        The list the ``(module, method, version, node_id)`` tuples accumulate
        into.
    """
    from wfc.execution import claim

    resolved: list[tuple] = []
    real = claim.resolve_claim_params

    def resolving(module_name, method_name, params, version=None, node_id=None):
        resolved.append((module_name, method_name, version, node_id))
        return real(module_name, method_name, params, version=version,
                    node_id=node_id)

    monkeypatch.setattr(claim, "resolve_claim_params", resolving)
    return resolved


@spy(
    boundary="wfc.storage.resolve.pull_again -- pulling a pushed entry again "
             "after its changed objects are set aside, as wfc verify --repair "
             "and the sample restore call it",
    preserves="everything: each pull proceeds; the entry pulled is recorded",
    not_proven="nothing",
    backed_by="pm_mvp::tests.test_verify::"
              "test_a_changed_sample_entry_is_pulled_again_when_pushed_and_listed_when_not",
)
def spy_pull_again(monkeypatch) -> list[str]:
    """Record every entry pulled again.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        The list the pulled entries' hashes accumulate into.
    """
    from wfc.storage import resolve

    pulled: list[str] = []
    real = resolve.pull_again

    def pulling(project_dir, content_hash, *args, **kwargs):
        pulled.append(content_hash)
        return real(project_dir, content_hash, *args, **kwargs)

    monkeypatch.setattr(resolve, "pull_again", pulling)
    return pulled


class ConversionLog:
    """What ``spy_image_conversions`` saw: the conversions in flight, and a hold on them.

    Attributes:
        started: The displayed names converted, in the order they started.
        running: Conversions in flight now.
        peak_running: The most conversions ever in flight at once.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started: list[str] = []
        self.running = 0
        self.peak_running = 0
        self._turn = threading.Condition(self.lock)
        # How many held conversions may still go on; ``None`` while nothing is held.
        self._permits: int | None = None

    def hold(self) -> None:
        """Stop every conversion that starts from now on before it decodes."""
        with self._turn:
            self._permits = 0

    def let(self, count: int = 1) -> None:
        """Let ``count`` held conversions go on, whichever reach their turn first."""
        with self._turn:
            self._permits = (self._permits or 0) + count
            self._turn.notify_all()

    def release(self) -> None:
        """Let every held conversion go on, and hold none from now on."""
        with self._turn:
            self._permits = None
            self._turn.notify_all()

    def _wait_turn(self) -> None:
        with self._turn:
            self._turn.wait_for(lambda: self._permits is None or self._permits > 0,
                                timeout=30)
            if self._permits:
                self._permits -= 1


@spy(
    boundary="wfc.canvas.previews.convert_image -- every conversion the image "
             "route runs, called on the conversion pool's threads in its turn",
    preserves="everything: each conversion runs; the spy counts the "
              "conversions in flight and the order they start in and, while "
              "the log holds them, stops each one before it decodes until "
              "the test lets it go",
    not_proven="the process's real peak memory during a decode (measured "
               "once outside the suite)",
    backed_by="pm_mvp::tests.test_artifact_image_gate::"
              "test_no_more_than_two_conversions_run_at_once",
)
def spy_image_conversions(monkeypatch) -> ConversionLog:
    """Record every image conversion in flight, and hold them on demand.

    Considered: ``spy_claim_resolutions``'s call-through pattern, which this
    follows (the real conversion still runs); no existing entry stands at
    the conversion pool's boundary or can hold a conversion while others
    queue.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        The log the conversions are recorded in.
    """
    from wfc.canvas import previews

    log = ConversionLog()
    real = previews.convert_image

    def converting(path, name, size, memory_mb):
        with log.lock:
            log.started.append(name)
            log.running += 1
            log.peak_running = max(log.peak_running, log.running)
        try:
            log._wait_turn()
            return real(path, name, size, memory_mb)
        finally:
            with log.lock:
                log.running -= 1

    monkeypatch.setattr(previews, "convert_image", converting)
    return log


class CheckoutBuildLog:
    """What ``spy_checkout_builds`` saw: the checkout builds in flight.

    Attributes:
        lock: Guards the counts, which the building threads update.
        builds: How many times a checkout's members were placed.
        running: Builds in flight now.
        peak_running: The most builds ever in flight at once.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.builds = 0
        self.running = 0
        self.peak_running = 0


@spy(
    boundary="wfc.storage.cache._place_members -- the step of the one checkout "
             "that writes a directory entry's members into its destination",
    preserves="everything: each build places every member; the spy counts the "
              "builds and the builds in flight, and keeps each one in flight "
              "for ``hold_s`` seconds before it places, so that overlapping "
              "builds of one destination are seen on every OS",
    not_proven="which interleaving of two unserialised builds fails, and how "
               "(on POSIX with reflinked or copied views, a rename into a "
               "directory another build has just made read-only)",
    backed_by="pm_mvp::tests.test_views::"
              "test_concurrent_first_reads_of_a_directory_build_its_checkout_once",
)
def spy_checkout_builds(monkeypatch, hold_s: float = 0.2) -> CheckoutBuildLog:
    """Count every checkout build and the builds in flight at once.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        hold_s: How long each build stays in flight before it places its members.

    Returns:
        The log the builds are recorded in.
    """
    import time

    from wfc.storage import cache

    log = CheckoutBuildLog()
    real = cache._place_members

    def placing(*args, **kwargs):
        with log.lock:
            log.builds += 1
            log.running += 1
            log.peak_running = max(log.peak_running, log.running)
        try:
            time.sleep(hold_s)
            return real(*args, **kwargs)
        finally:
            with log.lock:
                log.running -= 1

    monkeypatch.setattr(cache, "_place_members", placing)
    return log
