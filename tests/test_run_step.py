"""
Tests for the run-step execution layer.

Covers:
  - Single results channel (run-step reads _wfc_results.json)
  - run-step command (success, cache hit, error capture, inline fallback)
  - pipeline-summary counts over run rows
  - _generate_rule output
"""

import json
from pathlib import Path

import pytest

from tests.conftest import requires_docker
from tests.harness import (
    Behavior,
    Scenario,
    completed,
    node,
    run_scenario,
    run_target,
    selector,
    wire,
)

from axiom_annotations import workflow, Step


#: The single target every one-node scenario in this module expands to.
S1 = ("test_method", "S1", "default")


def _inline_project(git_project, monkeypatch):
    """Build a one-method project on the harness's default env.

    Returns:
        The built :class:`~tests.harness.Project`.
    """
    from tests.harness import build_project

    # No sample data on disk: an inline invocation carries no pipeline
    # document, so the classifier takes its legacy root-sample fallback and
    # a populated data/samples/S1/ would fill the slot — hiding the very
    # "root node with no input" condition these tests are about.
    scn = Scenario(
        nodes=[node("test_method", module="test_mod")],
        samples=["S1"],
        missing_samples=("S1",),
    )
    return build_project(scn, root=git_project, monkeypatch=monkeypatch)


def _container_project(git_project, monkeypatch, image_digest, behavior=None):
    """Build a one-method project whose env is the real fixture image.

    The scenario's env is the session-built image, registered under the
    name ``wfc-test-minimal`` with that image's digest. An inline-args
    invocation carries no document, so dispatch takes the env from the
    method's own declaration and resolves it through the manifest — the
    record that makes ``docker run`` launch a genuinely runnable image
    rather than a shape-valid fiction.

    The single output slot is declared as ``output: .parquet`` because an
    inline invocation carries no pipeline document and therefore no
    ``slot_outputs``: the collect phase takes its legacy single-output
    fallback and looks for exactly ``output.parquet``. Declaring the slot
    that way is what makes the generated method write the file the door
    under test expects.

    Args:
        git_project: Repo directory to build into.
        monkeypatch: An active ``pytest.MonkeyPatch``.
        image_digest: Bare sha256 hex of the built fixture image.
        behavior: What the containerized method process does.

    Returns:
        The built :class:`~tests.harness.Project`.
    """
    from tests.harness import build_project

    method_node = node(
        "test_method", module="test_mod",
        outputs={"output": ".parquet"},
        output_files={"output": "output.parquet"},
    )
    if behavior is not None:
        method_node.behavior = behavior
    scn = Scenario(nodes=[method_node], samples=["S1"],
                   env_name="wfc-test-minimal", image_digest=image_digest)
    return build_project(scn, root=git_project, monkeypatch=monkeypatch)


def _slot_scenario(outputs, output_files, behavior=None, **kwargs) -> Scenario:
    """A selector head feeding one method node with declared output slots.

    Args:
        outputs: Declared slot -> type string.
        output_files: Declared slot -> published filename.
        behavior: What the method process does.
        **kwargs: Any other :class:`~tests.harness.Scenario` field.

    Returns:
        The scenario.
    """
    method_node = node("test_method", module="test_mod", inputs=[wire("sel")],
                       outputs=outputs, output_files=output_files)
    if behavior is not None:
        method_node.behavior = behavior
    return Scenario(nodes=[selector(), method_node], samples=["S1"], **kwargs)


# =============================================================================
# Single results channel — metrics + outputs flow through
# `_wfc_results.json`; no `metrics.json` is read.
# =============================================================================

class TestSingleResultsChannel:
    """`run_step` collects results solely from `_wfc_results.json`.

    The client-side `RunContext` surface (env parsing, `save_artifact`,
    `log_metric`, `_finalize`) is covered by `wfc_client/tests/`. These
    tests pin the *host* contract: metrics arrive through the manifest and
    no `metrics.json` is read (so metrics cannot double-count).
    """

    def test_manifest_is_the_metrics_channel(self, tmp_path):
        """Host reads metrics from `_wfc_results.json`, not `metrics.json`."""
        from wfc.manifest import read_results_manifest

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        # A stray metrics.json must be ignored entirely.
        (run_dir / "metrics.json").write_text(json.dumps({"n_cells": 999}))
        (run_dir / "_wfc_results.json").write_text(
            json.dumps({"outputs": {}, "metrics": {"n_cells": 42}})
        )

        result = read_results_manifest(run_dir)
        assert result is not None
        assert result.metrics == {"n_cells": 42}, \
            "metrics must come from _wfc_results.json, not metrics.json"

    def test_no_manifest_yields_no_metrics(self, tmp_path):
        """A pure outputs-only Tier-2 run (no manifest) yields no metrics."""
        from wfc.manifest import read_results_manifest

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "output.parquet").write_text("fake")
        # No _wfc_results.json — outputs are located by the run_dir scan,
        # metrics default to empty. There is no metrics.json fallback.
        assert read_results_manifest(run_dir) is None


# =============================================================================
# run-step tests
# =============================================================================

class TestRunStep:
    """Tests for the wfc run-step command."""

    @pytest.mark.integration
    @requires_docker
    def test_run_step_inline_success(self, cli, git_project, monkeypatch,
                                     fixture_container_image):
        """run-step with inline args executes a method and exits 0."""
        project = _container_project(
            git_project, monkeypatch, fixture_container_image,
            Behavior(metrics={"n_cells": 42}),
        )

        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--method", "test_method",
            "--module", "test_mod",
            "--script", str(project.method_scripts["test_method"]),
            "--pipeline-id", "test-pipeline-001",
            "--git-commit", "abc123",
            "--ref-input", f"data={project.sample_file('S1')}",
        )
        assert result.returncode == 0, f"run-step failed: {result.stderr}"

    def test_run_step_inline_missing_args(self, cli, tmp_project):
        """run-step without --pipeline-json requires --method, --module, --script."""
        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
        )
        assert result.returncode != 0, "Should fail when inline args are missing"

    @pytest.mark.integration
    @requires_docker
    def test_run_step_error_capture(self, cli, git_project, monkeypatch,
                                    fixture_container_image):
        """A failing containerized method exits non-zero AND its error text is
        captured — surfaced in the Run row's error field or the per-run log,
        not swallowed into a bare non-zero rc."""
        # A method that raises inside the container.
        project = _container_project(
            git_project, monkeypatch, fixture_container_image,
            Behavior(raises="intentional test failure"),
        )

        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--method", "test_method",
            "--module", "test_mod",
            "--script", str(project.method_scripts["test_method"]),
            "--pipeline-id", "test-pipeline-002",
            "--git-commit", "abc123",
            "--ref-input", f"data={project.sample_file('S1')}",
        )
        assert result.returncode != 0, "Should fail when method raises"

        # The error content must be captured, not swallowed into a bare rc.
        # Look in the Run row's error fields, the per-run logs, and CLI output.
        from wfc.persistence import get_session, Run
        from sqlmodel import select
        with get_session() as session:
            failed = session.exec(select(Run).where(Run.status == "failed")).all()
        row_errors = " ".join(
            (r.error_message or "") + " " + (r.error_traceback or "")
            for r in failed
        )
        log_text = "\n".join(
            p.read_text(errors="replace")
            for p in project.runs_dir.rglob("*.log")
        )
        haystack = result.stdout + result.stderr + row_errors + log_text
        assert "intentional test failure" in haystack, (
            "the failing method's error text must be captured in the Run row's "
            "error field or the per-run log, not just a non-zero exit code"
        )

    def test_run_step_unresolvable_parent_fails_loudly(
        self, cli, git_project, monkeypatch, ready_preflight
    ):
        """A non-root step whose parent slot resolves to nothing exits 1 at
        wiring time with a clear error — never a silent empty slot_paths."""
        project = _inline_project(git_project, monkeypatch)

        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--method", "test_method",
            "--module", "test_mod",
            "--script", str(project.method_scripts["test_method"]),
            "--pipeline-id", "test-pipeline-006",
            "--git-commit", "abc123",
            "--parent-run-id", "data:999999",
        )
        assert result.returncode != 0, "run-step must fail on an unresolvable parent"
        # The claim refuses before any wiring happens: an input slot fed by a
        # run with no row would leave the step's cache key blind to it.
        assert "is not a registered run" in result.stderr
        assert "999999" in result.stderr

    @pytest.mark.integration
    @requires_docker
    def test_run_step_reads_results_manifest(self, cli, git_project, monkeypatch,
                                             fixture_container_image):
        """run-step reads metrics from `_wfc_results.json` and passes to complete_run."""
        project = _container_project(
            git_project, monkeypatch, fixture_container_image,
            Behavior(metrics={"n_cells": 42}),
        )

        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--method", "test_method",
            "--module", "test_mod",
            "--script", str(project.method_scripts["test_method"]),
            "--pipeline-id", "test-pipeline-005",
            "--git-commit", "abc123",
            "--ref-input", f"data={project.sample_file('S1')}",
        )
        assert result.returncode == 0, f"run-step failed: {result.stderr}"

        # Verify the metrics were stored in the run
        from wfc.persistence import get_session, Run
        from sqlmodel import select
        with get_session() as session:
            runs = session.exec(
                select(Run).where(Run.status == "completed")
            ).all()
            # At least one completed run with metrics
            completed_with_metrics = [r for r in runs if r.metrics and r.metrics.get("n_cells") == 42]
            assert len(completed_with_metrics) >= 1, \
                f"Expected a run with n_cells=42 in metrics, got: {[r.metrics for r in runs]}"

    @pytest.mark.integration
    @requires_docker
    def test_run_step_tees_stdout_to_per_run_log(self, cli, git_project, monkeypatch,
                                                 fixture_container_image):
        """run-step writes the method's stdout to .runs/<run_id>/stdout.log."""
        project = _container_project(
            git_project, monkeypatch, fixture_container_image,
            Behavior(
                stdout="hello from stdout line 1\nhello from stdout line 2\n",
                metrics={"n_cells": 1},
            ),
        )

        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--method", "test_method",
            "--module", "test_mod",
            "--script", str(project.method_scripts["test_method"]),
            "--pipeline-id", "stdout-log-test",
            "--git-commit", "abc123",
            "--ref-input", f"data={project.sample_file('S1')}",
        )
        assert result.returncode == 0, f"run-step failed: {result.stderr}"

        from wfc.persistence import get_session, Run
        from sqlmodel import select
        with get_session() as session:
            run = session.exec(
                select(Run).where(Run.pipeline_id == "stdout-log-test")
            ).first()
            assert run is not None
            run_id = run.id

        stdout_log = project.runs_dir / f"{run_id:08d}" / "stdout.log"
        assert stdout_log.exists(), f"stdout.log missing at {stdout_log}"
        content = stdout_log.read_text(encoding="utf-8")
        assert "hello from stdout line 1" in content
        assert "hello from stdout line 2" in content

    @pytest.mark.integration
    @requires_docker
    def test_run_step_tees_stderr_across_crash(self, cli, git_project, monkeypatch,
                                               fixture_container_image):
        """run-step captures stderr up to and including the crash traceback."""
        project = _container_project(
            git_project, monkeypatch, fixture_container_image,
            Behavior(stderr="about to crash\n", raises="boom"),
        )

        result = cli(
            "run-step",
            "--node-id", "test_method",
            "--sample", "S1",
            "--variant", "default",
            "--method", "test_method",
            "--module", "test_mod",
            "--script", str(project.method_scripts["test_method"]),
            "--pipeline-id", "stderr-log-test",
            "--git-commit", "abc123",
            "--ref-input", f"data={project.sample_file('S1')}",
        )
        assert result.returncode != 0

        from wfc.persistence import get_session, Run
        from sqlmodel import select
        with get_session() as session:
            run = session.exec(
                select(Run).where(Run.pipeline_id == "stderr-log-test")
            ).first()
            assert run is not None
            run_id = run.id

        stderr_log = project.runs_dir / f"{run_id:08d}" / "stderr.log"
        assert stderr_log.exists(), f"stderr.log missing at {stderr_log}"
        content = stderr_log.read_text(encoding="utf-8")
        assert "about to crash" in content
        assert "RuntimeError" in content and "boom" in content


@workflow(
    purpose="Root method node without --ref-input fails with error naming the missing flag",
)
def test_root_node_without_ref_input_raises_error(cli, git_project, monkeypatch,
                                                  ready_preflight):
    """Tier 3: Run a root method node without --ref-input, verify run_step
    raises an error with a message naming the --ref-input flag."""

    s = Step(step_num=1, name="Register a method",
             purpose="Set up a method so run-step can find it")
    project = _inline_project(git_project, monkeypatch)

    s = Step(step_num=2, name="Run root node without --ref-input",
             purpose="Call run-step on a standalone root node with no input source")
    result = cli(
        "run-step",
        "--node-id", "test_method",
        "--sample", "S1",
        "--variant", "default",
        "--method", "test_method",
        "--module", "test_mod",
        "--script", str(project.method_scripts["test_method"]),
        "--pipeline-id", "root-no-ref-input-001",
        "--git-commit", "abc123",
    )

    s = Step(step_num=3, name="Verify error mentions --ref-input",
             purpose="Confirm the error message names the --ref-input flag")
    assert result.returncode != 0, (
        "Expected non-zero exit code for root node without --ref-input"
    )
    combined = result.stderr + result.stdout
    assert "--ref-input" in combined, (
        f"Error message should name the --ref-input flag: {combined!r}"
    )


# =============================================================================
# slot-aware output path tests
# =============================================================================

class TestSlotShapes:
    """End-to-end tests: run-step honors contract-declared
    output slots as the single source of truth for output paths."""

    def test_non_parquet_file_slot_published(self, git_project, monkeypatch):
        """A method with a single .json slot runs end-to-end and
        the run archive contains the file under the slot-declared name."""
        scn = _slot_scenario(
            {"config": "JSON"},
            {"config": "extraction_config.json"},
            Behavior(outputs={"config": '{"k": 1}'}, metrics={"ok": True}),
        )
        obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)

        assert obs.exit_code(S1) == 0, f"run-step failed: {obs.runs[S1].ending}"
        # Assert the sentinel and the run-archive file.
        assert S1 in obs.sentinels, "sentinel missing"
        # Source-of-truth file lives in the run-archive dir.
        ro = obs.output_rows_for(S1)[0]
        assert ro["output_name"] == "extraction_config.json"
        assert Path(ro["artifact_path"]).read_text() == '{"k": 1}'

    def test_directory_slot_published(self, git_project, monkeypatch):
        """A directory slot — every child in run_dir/<slot> lands
        in the run archive under the slot-declared name."""
        scn = _slot_scenario(
            {"tiles_dir": "directory"},
            {"tiles_dir": "tiles_dir"},
            Behavior(
                outputs={"tiles_dir": {"tile_000.png": "img0",
                                       "tile_001.png": "img1"}},
                metrics={"n_tiles": 2},
            ),
        )
        obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)

        assert obs.exit_code(S1) == 0, f"run-step failed: {obs.runs[S1].ending}"
        # Assert the sentinel and the run-archive dir.
        assert S1 in obs.sentinels, "sentinel missing"
        ro = obs.output_rows_for(S1)[0]
        assert ro["output_name"] == "tiles_dir"
        ws_dir = Path(ro["artifact_path"])
        assert ws_dir.exists() and ws_dir.is_dir(), f"archive dir missing: {ws_dir}"
        assert (ws_dir / "tile_000.png").exists()
        assert (ws_dir / "tile_001.png").exists()

    def test_multi_slot_mixed_file_and_dir(self, git_project, monkeypatch):
        """A multi-slot method (file + directory) publishes every
        declared slot to the run archive under the declared name."""
        scn = _slot_scenario(
            {"config": "JSON", "tiles_dir": "directory"},
            {"config": "extraction_config.json", "tiles_dir": "tiles_dir"},
            Behavior(
                outputs={"config": '{"version": 2}',
                         "tiles_dir": {"tile_000.png": "t0"}},
                metrics={"ok": True},
            ),
        )
        obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)

        assert obs.exit_code(S1) == 0, f"run-step failed: {obs.runs[S1].ending}"
        # Assert the sentinel and the run-archive outputs.
        assert S1 in obs.sentinels, "sentinel missing"
        rows = {r["output_name"]: r for r in obs.output_rows_for(S1)}
        cfg_ro = rows.get("extraction_config.json")
        tiles_ro = rows.get("tiles_dir")
        assert cfg_ro is not None and Path(cfg_ro["artifact_path"]).read_text() == '{"version": 2}'
        assert tiles_ro is not None and (Path(tiles_ro["artifact_path"]) / "tile_000.png").exists()

    def test_missing_slot_raises(self, git_project, monkeypatch):
        """Negative test: a method that declares a slot but fails to
        produce it fails the run with a clear RuntimeError-style message
        naming the method and slot."""
        scn = _slot_scenario(
            {"config": "JSON", "labels": "CSV"},
            {"config": "extraction_config.json", "labels": "labels.csv"},
            Behavior(outputs={"config": "{}"}, skip_slots=("labels",)),
        )
        obs = run_target(scn, "test_method", root=git_project,
                         monkeypatch=monkeypatch)

        assert obs.exit_code(S1) != 0, "Expected failure for missing slot, got rc=0."
        # Error message should name the method and the missing slot
        combined = obs.run_row(S1)["error_message"] or ""
        assert "labels" in combined, \
            f"Error message should name the missing slot 'labels': {combined!r}"
        assert "test_method" in combined, \
            f"Error message should name the method 'test_method': {combined!r}"

    def test_cached_branch_restores_every_slot(self, git_project, monkeypatch):
        """Re-running the same multi-slot method takes the CACHED
        branch and restores every declared slot byte-identical."""
        scn = _slot_scenario(
            {"config": "JSON", "labels": "CSV"},
            {"config": "extraction_config.json", "labels": "labels.csv"},
            Behavior(outputs={"config": '{"v": 7}', "labels": "id,label\n1,A\n"}),
            prior_runs=[completed("test_method", sample="S1")],
        )
        obs = run_target(scn, "test_method", root=git_project,
                         monkeypatch=monkeypatch)

        assert obs.exit_code(S1) == 0, "Cache-hit run failed"
        assert obs.runs[S1].ending == "cached"
        # cache-hit branch touches the sentinel; outputs live in
        # the source run's archive dir (and the DVC cache once archived).
        assert S1 in obs.sentinels, "cache-hit sentinel missing"
        # The source run's outputs are still on disk; the cache hit
        # only signals Snakemake.
        rows = {r["output_name"]: r for r in obs.output_rows}
        cfg_ro = rows.get("extraction_config.json")
        labels_ro = rows.get("labels.csv")
        assert cfg_ro is not None and Path(cfg_ro["artifact_path"]).read_text() == '{"v": 7}'
        assert labels_ro is not None and Path(labels_ro["artifact_path"]).read_text() == "id,label\n1,A\n"


class TestCanonicalWorkdirOutput:
    """Canonical ``ctx.workdir`` -> ``ctx.save_artifact`` round-trip.

    The recommended Tier-1 pattern writes a declared output UNDER
    ``WFC_RUN_DIR/_workdir/`` and records its run-dir-relative path in
    ``_wfc_results.json``. The whole reason the manifest matters over a plain
    ``run_dir`` scan is this branch: a file nested in ``_workdir/`` is invisible
    to a top-level scan, so the host MUST resolve the manifest-recorded path to
    archive it.

    This drives the REAL ``run_step`` collect-outputs path host-side (no
    Docker): the scenario declares the output nested under ``_workdir/``, so
    the method process writes it there and records
    ``clean: _workdir/clean.csv`` in the manifest (save_artifact name ==
    declared slot name).
    """

    def test_run_step_archives_workdir_nested_output_from_manifest(
        self, git_project, monkeypatch
    ):
        """The declared output lives under ``_workdir/``; the host resolves it
        from ``_wfc_results.json`` into a ``RunOutput`` row, and
        ``archive_outputs`` hashes + DVC-caches THAT nested file.

        Fails under a bare ``run_dir`` scan: ``run_dir/clean.csv`` never
        exists, so the missing-slot guard would abort the run before any
        ``RunOutput`` row is written.
        """
        import hashlib

        # The single declared slot's name ("clean") == the save_artifact name
        # the method records in the manifest. The file is nested in _workdir/.
        clean_bytes = b"id,value\n1,10\n2,20\n"
        scn = _slot_scenario(
            {"clean": "CSV"},
            {"clean": "clean.csv"},
            Behavior(outputs={"clean": clean_bytes.decode()},
                     nested_outputs=("clean",),
                     metrics={"kept_rows": 2}),
        )
        obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)

        assert obs.exit_code(S1) == 0, (
            "run-step must succeed by resolving the _workdir-nested output from "
            f"the manifest (a bare run_dir scan would fail).\n"
            f"ending={obs.runs[S1].ending}"
        )

        from wfc.persistence import get_session, RunOutput
        from wfc.storage import archive_outputs
        from sqlmodel import select

        # The RunOutput row must point at the _workdir-nested file — proving the
        # host consulted the manifest, not a top-level run_dir scan.
        run_id = obs.runs[S1].run_id
        assert obs.run_row(S1)["status"] == "completed", "no completed Run row"
        assert obs.run_row(S1)["metrics"] == {"kept_rows": 2}
        ro = obs.output_rows_for(S1)[0]
        artifact = Path(ro["artifact_path"])
        assert artifact.parent.name == "_workdir", (
            f"RunOutput must point at the _workdir-nested file, got "
            f"{artifact} — a run_dir scan would have used a top-level path"
        )
        assert artifact.read_bytes() == clean_bytes
        assert ro["content_hash"] is None, "deferred archiving: hash is NULL pre-sweep"

        # The archive sweep hashes + DVC-caches the _workdir file.
        archive_outputs(project_dir=obs.project.root, run_id=run_id)
        expected_hash = hashlib.md5(clean_bytes).hexdigest()
        with get_session() as session:
            ro = session.exec(
                select(RunOutput).where(RunOutput.run_id == run_id)
            ).first()
            assert ro.content_hash == expected_hash, (
                f"archive must hash the _workdir file bytes: "
                f"{ro.content_hash} != {expected_hash}"
            )
            cache_path = (
                obs.project.root / ".dvc" / "cache" / "files" / "md5"
                / expected_hash[:2] / expected_hash[2:]
            )
            assert cache_path.exists(), (
                f"_workdir output not DVC-cached at {cache_path}"
            )


class TestRunStepNonMethodNodes:
    """run_step must resolve a method node even when the pipeline JSON
    contains non-method nodes (input_selector / run_reference) that carry no
    ``method`` key.

    run_step's node-resolution helpers build a by-method-name fallback map with
    ``{n["method"]: n for n in raw["nodes"]}`` in four places (config resolve,
    NID label, slot-output resolve, container-env resolve), each guarded to
    skip a node without a ``method`` key — every ``input_selector`` head node
    the CLI/canvas emit. An unguarded comprehension raises
    ``KeyError: 'method'`` and aborts the step, so no standard CLI pipeline
    can run.

    This drives the REAL run_step host-side (no Docker): the scenario's
    pipeline document carries the method-less selector head exactly as the
    CLI/canvas emit it, and the method process is faked at the dispatch
    boundary. All four comprehensions run before dispatch, so a KeyError in
    any of them fails this test.
    """

    def test_run_step_ignores_selector_node_without_method_key(
        self, git_project, monkeypatch
    ):
        scn = _slot_scenario({"output": ".parquet"}, {"output": "output.parquet"})
        obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)

        assert obs.exit_code(S1) == 0, (
            "run_step must skip the input_selector node when building its "
            "by-method fallback maps; a KeyError here means the "
            "{n['method']: ...} comprehension guard regressed.\n"
            f"ending={obs.runs[S1].ending}"
        )


class TestParentLookupPipelineScoped:
    """Parent run_id sidecar lookup must be pipeline-scoped.

    Run-id sidecars are written under
    ``.runs/sentinels/<pipeline_id>/<node_id>/<sample>/<variant>/``, so the
    sidecar lookup that resolves a downstream node's parent ``run_id`` must
    include the same ``pipeline_id``. Without that scoping the lookup either
    finds nothing (downstream gets treated as root) or finds a stale sidecar
    from another pipeline — leaving ``WFC_INPUT_PATHS`` pointing at the wrong
    file or empty.
    """

    def test_downstream_receives_upstream_output(self, git_project, monkeypatch):
        """A two-node pipeline linked by slot: after running node_1 and
        then node_2 under the same pipeline_id, node_2's _run_context.json
        must carry slot_paths pointing at node_1's output, and the method
        must be able to read node_1's actual output from it."""
        scn = Scenario(
            nodes=[
                selector(),
                node("node_1", method="upstream", module="upstream_mod",
                     inputs=[wire("sel")],
                     outputs={"data": "JSON"}, output_files={"data": "data.json"},
                     behavior=Behavior(outputs={"data": '{"upstream": "ok"}'})),
                node("node_2", method="downstream", module="downstream_mod",
                     inputs=[wire("node_1", source_slot="data")],
                     outputs={"echo": "JSON"}, output_files={"echo": "echo.json"},
                     behavior=Behavior(echo_input="data")),
            ],
            samples=["S1"],
        )
        obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)

        n1 = ("node_1", "S1", "default")
        n2 = ("node_2", "S1", "default")
        assert obs.exit_code(n1) == 0, "node_1 run-step failed"
        assert obs.exit_code(n2) == 0, (
            "node_2 run-step failed — parent sidecar lookup probably isn't "
            "pipeline-scoped so WFC_INPUT_PATHS was empty."
        )

        from wfc.persistence import project_root as get_project_root

        from wfc.layout import run_archive_dir

        downstream_run = run_archive_dir(get_project_root(), obs.runs[n2].run_id)
        ctx = json.loads((downstream_run / "_run_context.json").read_text())
        assert ctx["method_name"] == "downstream"
        assert ctx["slot_paths"], (
            f"downstream _run_context.json has empty slot_paths: {ctx}"
        )
        # resolve_input() returns the archived output path, so assert on the
        # filename, not the full path.
        data_paths = ctx["slot_paths"].get("data", [])
        assert data_paths and data_paths[0].endswith("data.json"), (
            f"downstream slot_paths['data'] should point at upstream's data.json "
            f"(not the sample file); got: {ctx['slot_paths']}"
        )

        echo = (downstream_run / "echo.json").read_text()
        assert echo == '{"upstream": "ok"}', (
            f"downstream should have echoed upstream's data.json content; "
            f"got: {echo!r}"
        )


class TestSnakemakeGen:
    """_generate_rule emits a single sentinel per rule.

    Directory and file slots alike emit one uniform sentinel output, never a
    per-slot ``slot=directory("...")`` or ``slot="..."`` entry — the data
    lives in the run archive / DVC cache, and the Snakefile doesn't
    reference it.
    """

    def test_directory_slot_emits_sentinel(self, wfc_root):
        """Directory slots collapse to one sentinel output line."""
        from wfc.graph import PipelineDef, StepDef
        from wfc.orchestration import generate_snakefile
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="tile_gen",
                module_name="test_mod",
                script_path="methods/tile_gen/tile_gen.py",
                params={},
                slot_outputs={"tiles_dir": "tiles_dir"},
                slot_types={"tiles_dir": "directory"},
            )],
            samples=["S1"],
        )
        content = generate_snakefile(pipeline, wfc_root)
        rule_block = content.split("rule tile_gen:")[1].split("\nrule ")[0]
        output_section = rule_block.split("output:")[1].split("params:")[0]
        output_lines = [ln.strip() for ln in output_section.splitlines() if ln.strip()]
        assert len(output_lines) == 1, output_lines
        assert ".runs/sentinels/" in output_lines[0] and ".complete" in output_lines[0], output_lines

    def test_file_slot_emits_sentinel(self, wfc_root):
        """File slots also collapse to the same sentinel shape."""
        from wfc.graph import PipelineDef, StepDef
        from wfc.orchestration import generate_snakefile
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="extractor",
                module_name="test_mod",
                script_path="methods/extractor/extractor.py",
                params={},
                slot_outputs={"config": "extraction_config.json"},
                slot_types={"config": "JSON"},
            )],
            samples=["S1"],
        )
        content = generate_snakefile(pipeline, wfc_root)
        rule_block = content.split("rule extractor:")[1].split("\nrule ")[0]
        output_section = rule_block.split("output:")[1].split("params:")[0]
        output_lines = [ln.strip() for ln in output_section.splitlines() if ln.strip()]
        assert len(output_lines) == 1, output_lines
        assert ".runs/sentinels/" in output_lines[0] and ".complete" in output_lines[0], output_lines


# =============================================================================
# pipeline-summary tests
# =============================================================================

class TestPipelineSummary:
    """Tests for the wfc pipeline-summary command."""

    @workflow(purpose="pipeline-summary counts a pipeline's run rows after "
                      "the pipeline-end walk: the step that ran, its re-run "
                      "that reused it from the cache, the step refused at "
                      "the claim and the descendant cancelled because of it, "
                      "with the refusal's message listed under the failed "
                      "runs")
    def test_pipeline_summary_aggregation(self, git_project, monkeypatch):
        from tests.fixtures.routes.clients import run_cli

        口 = Step(step_num=1, name="Run a pipeline whose middle step is refused",
                 purpose="head ran once before and its run here is a cache "
                         "hit on that run; mid's link names an output head "
                         "does not declare; tail depends on mid")
        obs = run_scenario(Scenario(nodes=[
            selector(),
            node("head", inputs=[wire("sel")], output_files={"data": "data.csv"}),
            node("mid", inputs=[wire("head", source_slot="nope")]),
            node("tail", inputs=[wire("mid")]),
        ], prior_runs=[completed("head")]), root=git_project,
            monkeypatch=monkeypatch)
        head_rows = obs.rows_for_node("head")
        assert head_rows[-1]["cache_source_run_id"] == head_rows[0]["id"]

        口 = Step(step_num=2, name="Print the summary through the CLI verb",
                 purpose="The verb reads the rows the walk left")
        result = run_cli("pipeline-summary", "--pipeline-id",
                         obs.project.pipeline_id)
        assert result.returncode == 0, f"pipeline-summary failed: {result.stderr}"

        口 = Step(step_num=3, name="The counts are the rows",
                 purpose="One of each outcome: the seeded run passed, the "
                         "re-run is counted as cached rather than passed, and "
                         "the refused step is a failure with its message")
        assert "Status: failed" in result.stdout
        assert ("Total runs: 4  |  Passed: 1  |  Failed: 1  |  Cached: 1  |  "
                "Cancelled: 1") in result.stdout
        assert "FAILED RUNS:" in result.stdout
        assert "'nope'" in result.stdout

    def test_pipeline_summary_no_rows(self, cli, tmp_project):
        """pipeline-summary for a pipeline with no run rows reports zero runs."""
        result = cli("pipeline-summary", "--pipeline-id", "test-summary-empty")
        assert result.returncode == 0, f"pipeline-summary failed: {result.stderr}"
        assert "Total runs: 0" in result.stdout


# =============================================================================
# _generate_rule tests
# =============================================================================

class TestGenerateRuleSimplified:
    """Tests for the _generate_rule output."""

    def test_rule_uses_shell_not_run(self, wfc_root):
        """Generated rules use shell: directive, not run: blocks."""
        from wfc.graph import PipelineDef, StepDef
        from wfc.orchestration import generate_snakefile
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="test_mod",
                script_path="methods/preprocess/preprocess.py",
                params={"k": 1},
            )],
            samples=["S1"],
        )
        content = generate_snakefile(pipeline, wfc_root)
        # Should contain shell: and NOT contain run:
        assert "shell:" in content, "Rule should use shell: directive"
        lines = content.split("\n")
        # Filter to just rule body lines
        in_rule = False
        for line in lines:
            if line.startswith("rule preprocess:"):
                in_rule = True
            elif in_rule and line and not line.startswith(" ") and not line.startswith("\t"):
                in_rule = False
            elif in_rule and line.strip() == "run:":
                pytest.fail("Rule should not contain 'run:' block")

    def test_preamble_has_pipeline_env_vars(self, wfc_root):
        """Generated preamble sets WFC_PIPELINE_JSON and WFC_PIPELINE_ID as env
        vars, and its onsuccess/onerror hooks use Snakemake's built-in shell()."""
        from wfc.graph import PipelineDef, StepDef
        from wfc.orchestration import generate_snakefile
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="test_mod",
                script_path="methods/preprocess/preprocess.py",
                params={"k": 1},
            )],
            samples=["S1"],
        )
        content = generate_snakefile(pipeline, wfc_root)
        assert "WFC_PIPELINE_JSON" in content, "Should set WFC_PIPELINE_JSON env var"
        assert "WFC_PIPELINE_ID" in content, "Should set WFC_PIPELINE_ID env var"
        assert "shell(" in content, "onsuccess/onerror should use shell() calls"

    def test_shell_calls_run_step(self, wfc_root):
        """Shell directive calls wfc run-step with node-id, sample, variant."""
        from wfc.graph import PipelineDef, StepDef
        from wfc.orchestration import generate_snakefile
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="test_mod",
                script_path="methods/preprocess/preprocess.py",
                params={"k": 1},
            )],
            samples=["S1"],
        )
        content = generate_snakefile(pipeline, wfc_root)
        assert "run-step" in content, "Shell should call wfc run-step"

    def test_no_brace_wildcards_in_shell(self, wfc_root):
        """Shell strings should not contain {CONSTANT} patterns that Snakemake would interpret as wildcards."""
        from wfc.graph import PipelineDef, StepDef
        from wfc.orchestration import generate_snakefile
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="test_mod",
                script_path="methods/preprocess/preprocess.py",
                params={"k": 1},
            )],
            samples=["S1"],
        )
        content = generate_snakefile(pipeline, wfc_root)
        # Find shell: lines and check for forbidden patterns
        lines = content.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('"') and "PIPELINE_ID" in stripped:
                # This is inside a shell string -- {PIPELINE_ID} is bad
                assert "{PIPELINE_ID}" not in stripped, \
                    f"Shell string contains {{PIPELINE_ID}} which Snakemake interprets as wildcard: {stripped}"
