"""Tier 3 integration smoke test for ADR-019 Cycle D container dispatch.

End-to-end: builds a minimal Docker image containing the ``wfc`` package,
registers it as a container env, materializes a one-method pipeline, and
invokes ``wfc run-step`` via subprocess. Asserts the container produced the
expected output on the host filesystem.

The test SKIPs cleanly when Docker isn't reachable. Default pytest invocation
excludes the ``integration`` marker via ``addopts`` in ``pyproject.toml``; run
explicitly with ``pytest -m integration tests/integration/``.

Method registration goes through the production code path
(:func:`tests.fixtures.conftest.register_test_method`) — no DB hand-crafting,
no stubs. This is the same path a real ``wfc register`` CLI invocation takes.

The ``minimal_image`` session-scoped fixture lives in
``tests/integration/conftest.py`` so it is shared with the multi-step test
(``test_containerized_pipeline_runs.py``); the image is built exactly once per
session.

Satisfies: US-1 end-to-end (WFC_RUN_DIR host->/work translation, --user
discipline, bind-mount semantics against real Docker).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.conftest import register_test_method


def _docker_available() -> bool:
    """True iff ``docker`` is on PATH and ``docker info`` succeeds."""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _docker_available(),
        reason="Docker not reachable on PATH",
    ),
]


def _materialize_project(tmp_path: Path, image_digest: str, monkeypatch) -> Path:
    """Create a tmp wfc project: git repo, envs.json, registered method, pipeline.

    Uses :func:`register_test_method` to route registration through the
    production code path (``wfc.init.init_project`` + ``wfc.register.register_module``
    + ``wfc.register.register_method``). This is the same path ``wfc register``
    would take, so the on-disk state and DB rows match production semantics.
    """
    proj = tmp_path / "proj"
    proj.mkdir()

    # Initialize as a git repo so wfc internals that probe git don't blow up.
    # Must happen BEFORE register_method, which calls _git_commit_registration.
    subprocess.run(["git", "init"], cwd=proj, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "wfc@wfc"],
        cwd=proj, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "wfc"],
        cwd=proj, check=True, capture_output=True,
    )
    # Force consistent line-ending handling so the in-container `git status`
    # doesn't see CRLF/LF drift as "uncommitted changes" when the host is
    # Windows and the container is Linux. .gitattributes must be committed
    # before any other files are added.
    (proj / ".gitattributes").write_text("* -text\n")
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=proj, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "core.fileMode", "false"],
        cwd=proj, check=True, capture_output=True,
    )

    # Env vars MUST be set before register_test_method (the helper and the
    # production register_method both read WFC_PROJECT_ROOT + DATABASE_URL).
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(proj))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{proj / '.wfc' / 'wfc.db'}")

    # Container env manifest. Image ref must be digest-pinned per ADR-019.
    # Write this BEFORE register_test_method runs, because _resolve_env
    # (called by register_method) validates the manifest entry's container
    # ref shape.
    wfc_dir = proj / ".wfc"
    wfc_dir.mkdir(exist_ok=True)
    container_ref = f"docker://local/wfc-test-minimal@sha256:{image_digest}"
    (wfc_dir / "envs.json").write_text(json.dumps({
        "schema_version": 1,
        "envs": {
            "smoke-env": {
                "backend": "pixi",
                "source": "pixi.toml",
                "container": container_ref,
                "env_fingerprint": image_digest,
                "built_from_lock": "pixi.lock",
                "built_at": "2026-05-17T00:00:00Z",
            }
        },
    }))

    # Method: writes "hello-from-container" to WFC_RUN_DIR/output.txt.
    # method.yaml uses env: container:smoke-env so _resolve_env (called by
    # register_method) routes through the manifest lookup path.
    #
    # The inputs.trigger slot is declared optional to satisfy register_method's
    # contract-validation invariant ("every method must declare at least one
    # input slot"). The pipeline JSON below provides no upstream link to
    # trigger, so WFC_INPUT_PATHS will be "{}" at run time — preserving Task 7's
    # single-node-no-real-inputs intent.
    method_dir = proj / "methods" / "smoke"
    method_dir.mkdir(parents=True)
    (method_dir / "smoke.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "\n"
        "def main():\n"
        "    run_dir = Path(os.environ['WFC_RUN_DIR'])\n"
        "    (run_dir / 'output.txt').write_text('hello-from-container')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    (method_dir / "method.yaml").write_text(
        "inputs:\n"
        "  trigger:\n"
        "    type: .txt\n"
        "    required: false\n"
        "outputs:\n"
        "  output:\n"
        "    type: .txt\n"
        "    required: true\n"
        "params: {}\n"
        "executor: local\n"
        "env: container:smoke-env\n"
    )

    # Register the method via the production code path.
    register_test_method(
        project_dir=proj,
        module_name="smoke",
        method_dir=method_dir,
        method_name="smoke",
    )

    # Sample data: a placeholder file under data/samples/s1/. This satisfies
    # run-step's D-2 root-input-required invariant: the method node needs
    # either --ref-input or an upstream input_selector with sample data. The
    # method itself doesn't read this file (WFC_INPUT_PATHS["trigger"] is
    # unused by smoke.py) — the file just exists so the runtime accepts the
    # method node as a valid root.
    sample_dir = proj / "data" / "samples" / "s1"
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "trigger.txt").write_text("trigger")

    # Pipeline: input_selector -> method. The input_selector is a system
    # node that declares the sample list; run-step resolves the upstream
    # sample data into WFC_INPUT_PATHS["trigger"] at execution time. The
    # method declares slot_outputs so the post-run output-collection step
    # finds output.txt. env: smoke-env (bare name) so the dispatch path's
    # _envs_get lookup finds the container record.
    pipeline = {
        "nodes": [
            {
                "id": "sel",
                "type": "input_selector",
                "method": "",
                "module": "",
                "samples": ["s1"],
            },
            {
                "id": "n1",
                "method": "smoke",
                "module": "smoke",
                "env": "smoke-env",
                "script": "methods/smoke/smoke.py",
                "slot_outputs": {"output": "output.txt"},
            },
        ],
        "links": [
            {"source": "sel", "target": "n1", "target_slot": "trigger"},
        ],
        "samples": ["s1"],
        "param_sets": {},
    }
    pj = proj / "pipeline.json"
    pj.write_text(json.dumps(pipeline))

    # Commit everything — register_method's own commit landed earlier; this
    # picks up pipeline.json and any other untracked artifacts so the working
    # tree is clean before run-step inspects HEAD.
    subprocess.run(["git", "add", "."], cwd=proj, check=True, capture_output=True)
    # The repo may have no remaining changes if register_method already
    # committed everything. Use --allow-empty to keep the call uniform.
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=proj, check=True, capture_output=True,
    )

    return proj


def test_run_step_in_container_writes_output_to_host(
    tmp_path: Path, minimal_image: str, monkeypatch
) -> None:
    """End-to-end: wfc run-step dispatches to the built container, the
    container writes ``output.txt`` to the bind-mounted run dir, and the
    file is visible on the host filesystem with the right contents.

    On Linux, also asserts the output file is owned by the host user
    (i.e. ``--user $UID:$GID`` propagated correctly). Skipped on Windows
    and macOS where Docker uses a VM and ownership semantics differ.
    """
    proj = _materialize_project(tmp_path, minimal_image, monkeypatch)

    env = os.environ.copy()
    env["WFC_PROJECT_ROOT"] = str(proj)
    env["DATABASE_URL"] = f"sqlite:///{proj / '.wfc' / 'wfc.db'}"
    # Strip PYTHONPATH from the parent env — the container dispatch path
    # already strips it, but keeping it out of the host wfc invocation
    # avoids accidental cross-pollution.
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [
            sys.executable, "-m", "wfc", "run-step",
            "--node-id", "n1",
            "--sample", "s1",
            "--variant", "default",
            "--pipeline-json", str(proj / "pipeline.json"),
            "--pipeline-id", "p1",
        ],
        cwd=proj,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0, (
        f"wfc run-step failed (rc={result.returncode})\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    # Find output.txt under the run dir. The run-dir layout is
    # .runs/<run_id>/<node>/<sample>/<variant>/output.txt but we don't
    # need to know run_id ahead of time — glob for the file.
    matches = list((proj / ".runs").rglob("output.txt"))
    assert matches, (
        f"output.txt not found under {proj / '.runs'}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    # If multiple matches (e.g. lineage subdirs), pick the deepest path
    # under the run dir.
    output = matches[0]
    assert output.read_text() == "hello-from-container", (
        f"Unexpected contents: {output.read_text()!r}"
    )

    # On Linux, the file should be owned by the host user (proves
    # --user $UID:$GID flowed through). Skip on Windows/macOS where
    # Docker Desktop's VFS rewrites ownership.
    if sys.platform.startswith("linux"):
        st = os.stat(output)
        assert st.st_uid == os.getuid(), (
            f"Expected file owner UID={os.getuid()}, got {st.st_uid}. "
            "docker --user flag did not propagate correctly."
        )
