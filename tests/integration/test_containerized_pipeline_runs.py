"""Tier 3 integration test: two-node container pipeline (WFC_INPUT_PATHS handoff).

Builds on Task 7's single-node smoke test by exercising:
  - cross-container artifact handoff via bind-mount round-trip
  - WFC_INPUT_PATHS host->/work path translation end-to-end
  - --user $UID:$GID propagation across multiple containerized invocations

Two methods registered via the production registration helper
(:func:`tests.fixtures.conftest.register_test_method`):

  producer: writes "payload-v1" to WFC_RUN_DIR/output.txt
  consumer: reads WFC_INPUT_PATHS["data"], asserts the path exists inside the
            container, writes <payload>-consumed to WFC_RUN_DIR/transformed.txt

Driver: ONE ``python -m wfc run-pipeline`` subprocess invocation. Snakemake
orchestrates the producer->consumer DAG end-to-end through production code
(Snakefile generation, dependency resolution, and WFC_INPUT_PATHS materialization
for the consumer step). This deliberately differs from Task 7's driver
(``wfc run-step``): Task 7 isolates container-exec; Task 8 proves the
Snakemake-on-top-of-containers path works.

Satisfies: end-to-end coverage gap exposed by fix-pass 2 retry — Task 7 only
exercises WFC_RUN_DIR translation (empty inputs); this test exercises the
WFC_INPUT_PATHS host->/work translation.
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


# The ``minimal_image`` session-scoped fixture is defined in
# ``tests/integration/conftest.py`` and is auto-injected here by pytest.


def _materialize_project(tmp_path: Path, image_digest: str, monkeypatch) -> Path:
    """Create a tmp wfc project with a two-node linear container pipeline.

    Both producer and consumer are registered via the production code path
    (:func:`register_test_method`) so the DB rows and on-disk state mirror
    what a real ``wfc register`` invocation would produce.
    """
    proj = tmp_path / "proj"
    proj.mkdir()

    # Initialize as a git repo BEFORE registering methods. run-pipeline reads
    # git SHAs during Snakefile generation, so committed state is required.
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

    monkeypatch.setenv("WFC_PROJECT_ROOT", str(proj))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{proj / '.wfc' / 'wfc.db'}")

    # Container env manifest. Digest-pinned per ADR-019. Written BEFORE
    # registration so _resolve_env can find the entry during register_method.
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

    # Producer: writes "payload-v1" to WFC_RUN_DIR/output.txt.
    # Optional `trigger` input slot satisfies register_method's "every method
    # must declare at least one input" invariant; the pipeline JSON below
    # links nothing to `trigger`, so WFC_INPUT_PATHS for the producer is "{}"
    # at run time.
    producer_dir = proj / "methods" / "producer"
    producer_dir.mkdir(parents=True)
    (producer_dir / "producer.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "\n"
        "def main():\n"
        "    run_dir = Path(os.environ['WFC_RUN_DIR'])\n"
        "    (run_dir / 'output.txt').write_text('payload-v1')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    (producer_dir / "method.yaml").write_text(
        "inputs:\n"
        "  trigger:\n"
        "    type: txt\n"
        "    required: false\n"
        "outputs:\n"
        "  output:\n"
        "    type: txt\n"
        "    required: true\n"
        "params: {}\n"
        "executor: local\n"
        "env: container:smoke-env\n"
    )

    # Consumer: reads WFC_INPUT_PATHS["data"], asserts it exists inside the
    # container, writes derived payload to WFC_RUN_DIR/transformed.txt.
    consumer_dir = proj / "methods" / "consumer"
    consumer_dir.mkdir(parents=True)
    (consumer_dir / "consumer.py").write_text(
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        "\n"
        "def main():\n"
        "    slot_paths = json.loads(os.environ['WFC_INPUT_PATHS'])\n"
        "    data_entry = slot_paths['data']\n"
        "    # entry may be str or list[str]; normalize to single path.\n"
        "    if isinstance(data_entry, list):\n"
        "        data_path = data_entry[0]\n"
        "    else:\n"
        "        data_path = data_entry\n"
        "    p = Path(data_path)\n"
        "    assert p.exists(), f'WFC_INPUT_PATHS[\"data\"] does not exist: {p}'\n"
        "    payload = p.read_text()\n"
        "    run_dir = Path(os.environ['WFC_RUN_DIR'])\n"
        "    (run_dir / 'transformed.txt').write_text(payload + '-consumed')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    (consumer_dir / "method.yaml").write_text(
        "inputs:\n"
        "  data:\n"
        "    type: txt\n"
        "    required: true\n"
        "outputs:\n"
        "  transformed:\n"
        "    type: txt\n"
        "    required: true\n"
        "params: {}\n"
        "executor: local\n"
        "env: container:smoke-env\n"
    )

    # Register both methods via the production code path.
    register_test_method(
        project_dir=proj,
        module_name="pipe",
        method_dir=producer_dir,
        method_name="producer",
    )
    register_test_method(
        project_dir=proj,
        module_name="pipe",
        method_dir=consumer_dir,
        method_name="consumer",
    )

    # Sample data: a placeholder file under data/samples/s1/. Producer is a
    # method-node root, so run-step's D-2 invariant requires an upstream
    # input_selector with sample data on disk. The producer itself doesn't
    # read this file (its WFC_INPUT_PATHS["trigger"] is unused).
    sample_dir = proj / "data" / "samples" / "s1"
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "trigger.txt").write_text("trigger")

    # Pipeline: input_selector -> producer (p1) -> consumer (c1).
    # The input_selector supplies the sample list to producer; producer's
    # output feeds consumer through slot "data". Both method nodes carry
    # env: smoke-env (bare name) so run-step's container-dispatch path
    # resolves the manifest entry.
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
                "id": "p1",
                "method": "producer",
                "module": "pipe",
                "env": "smoke-env",
                "script": "methods/producer/producer.py",
                "slot_outputs": {"output": "output.txt"},
            },
            {
                "id": "c1",
                "method": "consumer",
                "module": "pipe",
                "env": "smoke-env",
                "script": "methods/consumer/consumer.py",
                "slot_outputs": {"transformed": "transformed.txt"},
            },
        ],
        "links": [
            {"source": "sel", "target": "p1", "target_slot": "trigger"},
            {
                "source": "p1",
                "source_slot": "output",
                "target": "c1",
                "target_slot": "data",
            },
        ],
        "samples": ["s1"],
        "param_sets": {},
    }
    pj = proj / "pipeline.json"
    pj.write_text(json.dumps(pipeline))

    # Final commit so the working tree is clean and run-pipeline sees a
    # committed state (Snakefile generation records git SHAs).
    subprocess.run(["git", "add", "."], cwd=proj, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=proj, check=True, capture_output=True,
    )

    return proj


def test_two_node_container_pipeline_propagates_input_paths(
    tmp_path: Path, minimal_image: str, monkeypatch
) -> None:
    """End-to-end: wfc run-pipeline orchestrates producer->consumer via Snakemake;
    each step dispatches into its own container; consumer reads producer's
    output through WFC_INPUT_PATHS (host->/work translated) via the bind-mount
    round-trip.

    Driver is ONE ``wfc run-pipeline`` invocation — Snakemake handles DAG
    resolution and input-path materialization through production code, so
    this test proves the multi-step containerized path works end-to-end and
    not just two manually-orchestrated run-step calls.
    """
    proj = _materialize_project(tmp_path, minimal_image, monkeypatch)

    env = os.environ.copy()
    env["WFC_PROJECT_ROOT"] = str(proj)
    env["DATABASE_URL"] = f"sqlite:///{proj / '.wfc' / 'wfc.db'}"
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [
            sys.executable, "-m", "wfc", "run-pipeline",
            "--pipeline", str(proj / "pipeline.json"),
            "--project-root", str(proj),
            "--no-archive",
            "--cores", "1",
        ],
        cwd=proj,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )

    assert result.returncode == 0, (
        f"wfc run-pipeline failed (rc={result.returncode})\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    # Producer's output.txt should materialize under .runs/.
    producer_outputs = list((proj / ".runs").rglob("output.txt"))
    assert producer_outputs, (
        f"output.txt not found under {proj / '.runs'}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert producer_outputs[0].read_text() == "payload-v1", (
        f"Unexpected producer output contents: "
        f"{producer_outputs[0].read_text()!r}"
    )

    # Consumer's transformed.txt should materialize and carry the derived
    # payload — proves WFC_INPUT_PATHS was translated correctly and the
    # consumer container could read the producer's output via the bind mount.
    consumer_outputs = list((proj / ".runs").rglob("transformed.txt"))
    assert consumer_outputs, (
        f"transformed.txt not found under {proj / '.runs'}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert consumer_outputs[0].read_text() == "payload-v1-consumed", (
        f"Unexpected consumer output contents: "
        f"{consumer_outputs[0].read_text()!r}"
    )

    # Run dirs must be distinct — each step gets its own run directory.
    assert producer_outputs[0].parent != consumer_outputs[0].parent, (
        f"Producer and consumer share a run dir: "
        f"{producer_outputs[0].parent}"
    )

    # On Linux, both files should be owned by the host user (proves
    # --user $UID:$GID flowed through both container invocations). Skip on
    # Windows/macOS where Docker Desktop's VM rewrites ownership.
    if sys.platform.startswith("linux"):
        for output in (producer_outputs[0], consumer_outputs[0]):
            st = os.stat(output)
            assert st.st_uid == os.getuid(), (
                f"Expected file owner UID={os.getuid()}, got {st.st_uid} on "
                f"{output}. docker --user flag did not propagate correctly."
            )
