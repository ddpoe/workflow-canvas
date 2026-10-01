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
  stub_dvc_available.

**process** -- subprocesses, binaries, sockets, files, the prompt, the clock.
  fake_subprocess_run (+ canned_process, refusing_process,
  real_subprocess_run), fake_docker_build_process, stub_binary_lookup,
  always_busy_socket, fake_os_rename, fake_path_replace, fake_os_chmod,
  recording_file_copy, redirect_home, stub_interactive_prompt, stub_terminal,
  stub_server_bind, stub_wall_clock, fake_conda_list_explicit,
  fake_pip_freeze.

**docker** -- register-env's registry calls and the dev loop's launch.
  stub_docker_image_inspect, stub_docker_repo_digest, stub_docker_pull,
  stub_docker_build, refuse_docker, stub_docker_command_builder,
  stub_dev_loop_launch.

**engine** -- the method process, the Snakemake spawn, the phases.
  stub_method_process (the harness's stub rung), mocked_snakemake,
  fake_engine_process, stub_pipeline_stage, stub_record_writers,
  stub_runtime_phases.

**seams** -- production functions replaced at a module attribute.
  stub_registry_seam, stub_pipeline_submission, stub_transport,
  stub_cache_writers, stub_dvc_setup, stub_export_engine,
  suspend_name_conflict_check, stub_seed_submission,
  stub_sample_health_loader.

**server_state** -- the canvas process's module-level state.
  bind_provider, stub_reference_seeding, seed_active_job.

**shortcuts** -- product state written directly, bypassing its writer.
  write_wfc_marker, make_marker_project, fixture_env_record,
  seed_sample_row, typed_rows, flip_push_status, direct_claim.

**spies** -- wrap and call through.
  interpose_phases, snapshot_output_writers, probing_provider_session,
  spy_directory_listings.
"""

from __future__ import annotations

from ._entry import FIELDS, KINDS, fake, is_entry, shortcut, spy
from .docker import (
    refuse_docker,
    stub_dev_loop_launch,
    stub_docker_build,
    stub_docker_command_builder,
    stub_docker_image_inspect,
    stub_docker_pull,
    stub_docker_repo_digest,
)
from .engine import (
    fake_engine_process,
    mocked_snakemake,
    stub_method_process,
    stub_pipeline_stage,
    stub_record_writers,
    stub_runtime_phases,
)
from .process import (
    always_busy_socket,
    canned_process,
    fake_conda_list_explicit,
    fake_docker_build_process,
    fake_os_chmod,
    fake_os_rename,
    fake_path_replace,
    fake_pip_freeze,
    fake_subprocess_run,
    real_subprocess_run,
    recording_file_copy,
    redirect_home,
    refusing_process,
    stub_binary_lookup,
    stub_interactive_prompt,
    stub_server_bind,
    stub_terminal,
    stub_wall_clock,
)
from .readiness import (
    restore_readiness_probe,
    stub_demo_docker_probe,
    stub_dvc_available,
    stub_readiness_probes,
)
from .seams import (
    REGISTRY_SEAMS,
    stub_cache_writers,
    stub_dvc_setup,
    stub_export_engine,
    stub_pipeline_submission,
    stub_registry_seam,
    stub_sample_health_loader,
    stub_seed_submission,
    stub_transport,
    suspend_name_conflict_check,
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
    spy_directory_listings,
)

__all__ = [
    # the entry shape
    "FIELDS", "KINDS", "fake", "is_entry", "shortcut", "spy",
    # readiness
    "stub_readiness_probes", "restore_readiness_probe",
    "stub_demo_docker_probe", "stub_dvc_available",
    # process
    "fake_subprocess_run", "canned_process", "refusing_process",
    "real_subprocess_run", "fake_docker_build_process", "stub_binary_lookup",
    "always_busy_socket", "fake_os_rename", "fake_path_replace",
    "fake_os_chmod", "recording_file_copy", "redirect_home",
    "stub_interactive_prompt", "stub_terminal", "stub_server_bind", "stub_wall_clock",
    "fake_conda_list_explicit", "fake_pip_freeze",
    # docker
    "stub_docker_image_inspect", "stub_docker_repo_digest",
    "stub_docker_pull", "stub_docker_build",
    "refuse_docker", "stub_docker_command_builder", "stub_dev_loop_launch",
    # engine
    "stub_method_process", "mocked_snakemake", "fake_engine_process",
    "stub_pipeline_stage", "stub_record_writers", "stub_runtime_phases",
    # seams
    "REGISTRY_SEAMS", "stub_registry_seam", "stub_pipeline_submission",
    "stub_transport", "stub_cache_writers", "stub_dvc_setup",
    "stub_export_engine", "suspend_name_conflict_check",
    "stub_seed_submission", "stub_sample_health_loader",
    # server_state
    "bind_provider", "stub_reference_seeding", "seed_active_job",
    # shortcuts
    "write_wfc_marker", "make_marker_project", "fixture_env_record",
    "seed_sample_row", "typed_rows", "flip_push_status", "direct_claim",
    # spies
    "interpose_phases", "snapshot_output_writers",
    "probing_provider_session", "spy_directory_listings",
]
