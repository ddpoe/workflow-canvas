"""The registry of every fake, declared shortcut and spy the suite uses.

One entry per mechanism, grouped by the boundary it stands at. Every entry
is an importable callable carrying four declared fields -- ``boundary``,
``preserves``, ``not_proven``, ``backed_by`` -- as keyword string literals on
its ``@fake`` / ``@shortcut`` / ``@spy`` decorator (see ``_entry.py``), so a
catalog row's generated Stub disclosure names the entry and the registry
holds the sentence once. Callers write ``fakes.<entry>``; every entry defined
in a submodule is re-exported here, and ``tests/test_suite_invariants.py``
fails when one is not.

Index by boundary:

**readiness** -- the ``wfc doctor`` probes and the run gate.
  stub_readiness_probes, restore_readiness_probe, stub_demo_docker_probe,
  stub_dvc_available, stub_drive_type (the host and GetDriveTypeW's answer for the disk row).

**process** -- subprocesses, binaries, sockets, files, the prompt, the clock.
  fake_subprocess_run (+ canned_process, refusing_process,
  real_subprocess_run), fake_docker_build_process, stub_binary_lookup,
  always_busy_socket, fake_os_rename, fake_path_replace, refuse_path_unlink,
  refuse_path_unlink_once, refuse_path_write, fake_os_replace,
  fake_os_chmod, recording_file_copy, redirect_home, stub_interactive_prompt,
  stub_prompt_interrupt (Ctrl-C at the prompt), stub_terminal,
  stub_server_bind, stub_wall_clock, fake_conda_list_explicit,
  fake_conda_solve_capture, stub_host_conda_platform, fake_pip_freeze,
  short_register_env_heartbeat (the wait before register-env's heartbeat).

**docker** -- register-env's registry calls, pushes and the dev loop's launch.
  stub_docker_image_inspect, stub_docker_image_content (FAKE_IMAGE_CONTENT),
  stub_docker_image_labels, stub_docker_repo_digest, stub_docker_pull, stub_docker_tag,
  stub_docker_push (FAKE_PUSHED_DIGEST), stub_docker_read_manifest,
  stub_docker_build, refuse_docker, stub_docker_command_builder,
  stub_dev_loop_launch, stub_bind_probe (the launch's bind-check container),
  fake_interruptible_build (docker_build: the docker build client's process
  tree -- its Popen, the group interrupt, psutil's docker.exe and
  docker-buildx.exe; one ordered event list of interrupts, waits,
  terminates and kills).

**containers** -- the Docker calls that list and stop wfc's containers by label.
  fake_container_daemon (a model of the daemon's running containers, and
  its ContainerDaemon; armed empty suite-wide), interrupt_labelled_launch
  (the launch's labelled container started, then the wait interrupted).

**leftovers** -- what wfc prune-envs reads: the Docker listings and the process table.
  fake_leftover_daemon (a LeftoverDaemon: running containers with labels,
  variables and names, the env images, the build cache's size; armed empty
  suite-wide), fake_build_processes (a BuildProcesses table of wfc
  commands, docker.exe clients and docker-buildx.exe plugins, and the
  interrupt, wait and terminate the apply sends; armed empty suite-wide),
  stub_posix_process_groups (the process groups os.getpgid / os.killpg see).

**gh** -- the GitHub CLI reads of ``wfc share``.
  stub_gh.

**release_http** -- the update check's HTTPS GETs (PyPI's JSON, the public changelog).
  fake_release_http (canned answers, requests recorded), refuse_release_http
  (the suite-wide network guard; armed for every test by conftest).

**engine** -- the method process, the Snakemake spawn, the phases.
  stub_method_process (the harness's stub rung), mocked_snakemake,
  fake_engine_process, stub_pipeline_stage, stub_record_writers,
  stub_runtime_phases, lose_claimed_script, register_during_claim,
  register_after_claim, short_claim_stall_window, deny_process_kill,
  short_log_stream_wall, interrupt_method_process (the container launch
  interrupted, as Ctrl-C does).

**seams** -- production functions replaced at a module attribute.
  stub_registry_seam, stub_pipeline_submission, stub_transport,
  stub_cache_writers, stub_dvc_setup, stub_export_engine,
  stub_seed_submission, stub_sample_health_loader,
  stub_distribution_metadata_missing, fault_at_hashing_copy,
  unreadable_output_hash, fault_at_record_output, hold_stat_record,
  fault_at_protect_chmod, fail_first_pull_again, volume_without_change_time,
  fault_at_output_row_write, fault_at_run_release, fault_at_version_rows_write,
  prune_during_claim, interrupt_view_rename, refuse_view_links,
  fault_after_claim_input_rows, order_registration_commits,
  run_dir_removed_at_first_lock, takeover_left_after_polls,
  takeover_renewed_before_claim.

**server_state** -- the canvas process's module-level state.
  bind_provider, stub_reference_seeding, seed_active_job.

**shortcuts** -- product state written directly, bypassing its writer.
  write_wfc_marker, make_marker_project, fixture_env_record,
  seed_sample_row, typed_rows, flip_push_status, direct_claim.

**spies** -- wrap and call through.
  interpose_phases, snapshot_output_writers, probing_provider_session,
  spy_directory_listings, spy_content_reads, spy_setting_reads,
  spy_writable_walks, spy_pull_again, spy_claim_resolutions,
  spy_image_conversions (and its ConversionLog), spy_checkout_builds (and
  its CheckoutBuildLog).
"""

from __future__ import annotations

from ._entry import FIELDS, KINDS, fake, is_entry, shortcut, spy
from .containers import ContainerDaemon, fake_container_daemon, interrupt_labelled_launch
from .leftovers import (
    BuildProcesses,
    LeftoverDaemon,
    fake_build_processes,
    fake_leftover_daemon,
    stub_posix_process_groups,
)
from .docker_build import fake_interruptible_build
from .docker import (
    FAKE_IMAGE_CONTENT,
    FAKE_PUSHED_DIGEST,
    refuse_docker,
    stub_bind_probe,
    stub_dev_loop_launch,
    stub_docker_build,
    stub_docker_command_builder,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_docker_image_labels,
    stub_docker_pull,
    stub_docker_push,
    stub_docker_read_manifest,
    stub_docker_repo_digest,
    stub_docker_tag,
)
from .engine import (
    NOT_A_RUN_ID,
    refuse_claim_after_load,
    deny_process_kill,
    fake_engine_process,
    interrupt_method_process,
    short_log_stream_wall,
    lose_claimed_script,
    mocked_snakemake,
    register_after_claim,
    register_during_claim,
    short_claim_stall_window,
    stub_method_process,
    stub_pipeline_stage,
    stub_record_writers,
    stub_runtime_phases,
)
from .gh import stub_gh
from .release_http import fake_release_http, refuse_release_http
from .process import (
    always_busy_socket,
    canned_process,
    fake_conda_list_explicit,
    fake_conda_solve_capture,
    fake_docker_build_process,
    fake_os_chmod,
    fake_os_rename,
    fake_os_replace,
    fake_path_replace,
    fake_pip_freeze,
    fake_subprocess_run,
    real_subprocess_run,
    recording_file_copy,
    redirect_home,
    refuse_path_unlink,
    refuse_path_unlink_once,
    refuse_path_write,
    refusing_process,
    stub_binary_lookup,
    stub_host_conda_platform,
    stub_interactive_prompt,
    stub_server_bind,
    stub_terminal,
)
from .prompt_interrupt import stub_prompt_interrupt  # noqa: E402
from .process import (  # noqa: E402
    stub_wall_clock,
)
from .readiness import (
    restore_readiness_probe,
    stub_demo_docker_probe,
    stub_dvc_available,
    stub_readiness_probes,
)
from .drive_type import DRIVE_ANSWERS, stub_drive_type
from .register_progress import short_register_env_heartbeat
from .spawns import Spawns, record_spawns
from .seams import (
    REGISTRY_SEAMS,
    fault_at_hashing_copy,
    stub_cache_writers,
    stub_distribution_metadata_missing,
    stub_dvc_setup,
    stub_export_engine,
    stub_pipeline_submission,
    stub_registry_seam,
    stub_sample_health_loader,
    stub_seed_submission,
    stub_transport,
    unreadable_output_hash,
    fault_at_record_output,
    fault_at_output_row_write,
    fault_at_run_release,
    fault_at_version_rows_write,
    prune_during_claim,
    hold_stat_record,
    fault_at_protect_chmod,
    fail_first_pull_again,
    volume_without_change_time,
    interrupt_view_rename,
    refuse_view_links,
    fault_after_claim_input_rows,
    order_registration_commits,
    run_dir_removed_at_first_lock,
    takeover_left_after_polls,
    takeover_renewed_before_claim,
)
from .server_state import bind_provider, seed_active_job, stub_reference_seeding
from .shortcuts import (
    direct_claim,
    fixture_env_record,
    flip_push_status,
    make_marker_project,
    seed_sample_row,
    typed_rows,
    write_wfc_marker,
)
from .spies import (
    interpose_phases,
    probing_provider_session,
    snapshot_output_writers,
    spy_content_reads,
    spy_directory_listings,
    spy_setting_reads,
    spy_writable_walks,
    spy_pull_again,
    spy_claim_resolutions,
    spy_image_conversions,
    ConversionLog,
    spy_checkout_builds,
    CheckoutBuildLog,
)

__all__ = [
    # the entry shape
    "FIELDS", "KINDS", "fake", "is_entry", "shortcut", "spy",
    # readiness
    "stub_readiness_probes", "restore_readiness_probe",
    "stub_demo_docker_probe", "stub_dvc_available",
    "stub_drive_type", "DRIVE_ANSWERS",
    # process
    "fake_subprocess_run", "canned_process", "refusing_process",
    "real_subprocess_run", "fake_docker_build_process", "stub_binary_lookup",
    "always_busy_socket", "fake_os_rename", "fake_path_replace",
    "refuse_path_unlink", "refuse_path_unlink_once", "refuse_path_write", "fake_os_replace", "fake_os_chmod", "recording_file_copy", "redirect_home",
    "stub_interactive_prompt", "stub_prompt_interrupt", "stub_terminal",
    "stub_server_bind", "stub_wall_clock",
    "fake_conda_list_explicit", "fake_conda_solve_capture",
    "stub_host_conda_platform", "fake_pip_freeze", "short_register_env_heartbeat",
    "Spawns", "record_spawns",
    # docker
    "stub_docker_image_inspect", "stub_docker_image_content",
    "FAKE_IMAGE_CONTENT", "stub_docker_image_labels", "stub_docker_repo_digest",
    "stub_docker_pull", "stub_docker_build", "stub_docker_tag",
    "stub_docker_push", "FAKE_PUSHED_DIGEST", "stub_docker_read_manifest",
    "refuse_docker", "stub_docker_command_builder", "stub_dev_loop_launch",
    "stub_bind_probe", "fake_interruptible_build",
    # containers
    "fake_container_daemon", "ContainerDaemon", "interrupt_labelled_launch",
    # leftovers
    "fake_leftover_daemon", "LeftoverDaemon", "fake_build_processes",
    "BuildProcesses", "stub_posix_process_groups",
    # gh
    "stub_gh",
    # release_http
    "fake_release_http", "refuse_release_http",
    # engine
    "stub_method_process", "mocked_snakemake", "fake_engine_process",
    "stub_pipeline_stage", "stub_record_writers", "stub_runtime_phases",
    "lose_claimed_script", "register_during_claim", "register_after_claim",
    "short_claim_stall_window",
    "refuse_claim_after_load", "NOT_A_RUN_ID",
    "deny_process_kill",
    "short_log_stream_wall",
    "interrupt_method_process",
    # seams
    "REGISTRY_SEAMS", "stub_registry_seam", "stub_pipeline_submission",
    "stub_transport", "stub_cache_writers", "stub_dvc_setup",
    "stub_export_engine", "stub_seed_submission", "stub_sample_health_loader",
    "stub_distribution_metadata_missing", "fault_at_hashing_copy",
    "unreadable_output_hash", "fault_at_record_output", "hold_stat_record",
    "fault_at_protect_chmod", "fail_first_pull_again", "volume_without_change_time",
    "fault_at_output_row_write", "fault_at_run_release",
    "fault_at_version_rows_write", "prune_during_claim",
    "interrupt_view_rename", "refuse_view_links",
    "fault_after_claim_input_rows", "order_registration_commits",
    "run_dir_removed_at_first_lock", "takeover_left_after_polls",
    "takeover_renewed_before_claim",
    # server_state
    "bind_provider", "stub_reference_seeding", "seed_active_job",
    # shortcuts
    "write_wfc_marker", "make_marker_project", "fixture_env_record",
    "seed_sample_row", "typed_rows", "flip_push_status", "direct_claim",
    # spies
    "interpose_phases", "snapshot_output_writers",
    "probing_provider_session", "spy_directory_listings", "spy_content_reads",
    "spy_setting_reads", "spy_writable_walks", "spy_pull_again",
    "spy_claim_resolutions", "spy_image_conversions", "ConversionLog",
    "spy_checkout_builds", "CheckoutBuildLog",
]
