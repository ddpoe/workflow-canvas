"""Tier 3 integration tests: non-Python methods run end-to-end in containers.

Two E2E stories (both ``integration``-marked, skip cleanly without Docker):

  1. Bash method: registered from ``smoke.sh``, dispatched with the env
     record's ``/bin/bash`` interpreter into the digest-pinned minimal image
     (python:3.11-slim, which ships bash), driven by ``wfc run-step``.
     Proves ``[interpreter, script]`` dispatch is language-agnostic and the
     script sees ``$WFC_RUN_DIR`` / ``$WFC_INPUT_PATHS``.

  2. Mixed-language pipeline: Python producer -> R consumer (byo
     ``rocker/r-ver`` image registered through the PRODUCTION
     ``wfc.environments.register`` byo path with ``python_override="Rscript"`` — the
     exact ``wfc register-env --interpreter Rscript`` flow). One
     ``wfc run-pipeline`` invocation; the R step reads ``WFC_INPUT_PATHS`` /
     ``WFC_RUN_DIR`` via ``Sys.getenv`` and per-step cache keys are recorded.

The R test additionally needs network access on first run to pull the rocker
image (cached by the Docker daemon afterwards); it skips if the pull fails.

Project materialization mirrors tests/integration/test_containerized_*.py.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from axiom_annotations import workflow, Step

from tests.conftest import requires_docker
from tests.fixtures.conftest import (
    register_sample_row,
    register_test_method,
    sample_source_dir,
)
from wfc.storage import restore_sample


R_IMAGE_TAG = "rocker/r-ver:4.3.1"


pytestmark = [pytest.mark.integration, requires_docker]


@pytest.fixture(scope="session")
def r_image() -> str:
    """Ensure the rocker R image is local; return its tag ref.

    Pulls on first use (network); skips the requesting tests if the pull
    fails so an offline machine degrades to a skip, not a failure.
    """
    inspect = subprocess.run(
        ["docker", "image", "inspect", R_IMAGE_TAG],
        capture_output=True, timeout=30,
    )
    if inspect.returncode != 0:
        pull = subprocess.run(
            ["docker", "pull", R_IMAGE_TAG],
            capture_output=True, text=True, timeout=1800,
        )
        if pull.returncode != 0:
            pytest.skip(
                f"cannot pull {R_IMAGE_TAG} (offline?): {pull.stderr[-500:]}"
            )
    return R_IMAGE_TAG


def _init_git_project(tmp_path: Path, monkeypatch) -> Path:
    """Create a tmp git project with env vars set (mirrors sibling tests)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init"], cwd=proj, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "wfc@wfc"],
        cwd=proj, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "wfc"],
        cwd=proj, check=True, capture_output=True,
    )
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
    return proj


def _write_py_env(proj: Path, image_digest: str, *, interpreter: str) -> None:
    """Write the minimal-image env record (interpreter configurable)."""
    wfc_dir = proj / ".wfc"
    wfc_dir.mkdir(exist_ok=True)
    from tests.fixtures.conftest import write_env_record
    # byo attach of the locally built image (raw docker build, no pixi/conda
    # source); the image has the interpreter on PATH.
    write_env_record(proj, "smoke-env", image="local/wfc-test-minimal",
                     digest=image_digest, python=interpreter)


def _commit_all(proj: Path) -> None:
    subprocess.run(["git", "add", "."], cwd=proj, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=proj, check=True, capture_output=True,
    )


def _seed_trigger_sample(proj: Path) -> None:
    source_dir = sample_source_dir(proj) / "s1"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "trigger.txt").write_text("trigger")
    register_sample_row(proj, "s1", source_dir / "trigger.txt")
    # run-step drives this directly, so no Snakemake restore_sample rule
    # materializes the bytes: call the production restore, the only
    # sanctioned writer under data/samples/.
    restore_sample("s1", project_root=proj)


@workflow(
    purpose="A bash method registered via the production path is "
            "dispatched as [interpreter, script] into its digest-pinned "
            "container by `wfc run-step`, sees the WFC env-var contract, and "
            "its declared output lands on the host",
    inputs="a smoke.sh method + method.yaml in a tmp git project; env record "
           "with interpreter /bin/bash over the pinned minimal image",
    outputs="run-step exits 0; output.txt exists under .runs/ and proves the "
            "script read $WFC_INPUT_PATHS/$WFC_RUN_DIR",
)
def test_bash_method_runs_in_container(
    tmp_path: Path, minimal_image: str, monkeypatch
) -> None:
    """End-to-end bash method: `smoke.sh` registered via the production path,
    dispatched as ``[/bin/bash, script]`` into the digest-pinned image; the
    script reads ``$WFC_INPUT_PATHS``/``$WFC_RUN_DIR`` and writes the
    declared output slot, visible on the host."""
    s = Step(step_num=1, name="Materialize project + bash method",
             purpose="Tmp git project, /bin/bash env record over the pinned "
                     "minimal image, smoke.sh + method.yaml on disk")
    proj = _init_git_project(tmp_path, monkeypatch)
    # python:3.11-slim ships /bin/bash — record it as the env interpreter,
    # exactly what `wfc register-env --interpreter /bin/bash` records.
    _write_py_env(proj, minimal_image, interpreter="/bin/bash")

    method_dir = proj / "methods" / "smoke"
    method_dir.mkdir(parents=True)
    # newline="\n" is load-bearing on Windows hosts: bash treats a CRLF
    # line ending as a literal \r in the redirect filename.
    (method_dir / "smoke.sh").write_text(
        "# reads the wfc env-var contract; WFC_INPUT_PATHS is '{}' here\n"
        'printf "%s" "hello-from-bash inputs=$WFC_INPUT_PATHS" '
        '> "$WFC_RUN_DIR/output.txt"\n',
        newline="\n",
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
        "env: smoke-env\n"
    )

    s = Step(step_num=2, name="Register via production path",
             purpose="register_test_method drives the real register_module/"
                     "register_method flow (probe discovers smoke.sh, no AST scan)")
    register_test_method(
        project_dir=proj,
        module_name="smoke",
        method_dir=method_dir,
        method_name="smoke",
    )
    _seed_trigger_sample(proj)

    pipeline = {
        "nodes": [
            {
                "id": "sel",
                "type": "input_selector",
                "samples": ["s1"],
            },
            {
                "id": "n1",
                "method": "smoke",
                "module": "smoke",
                "env": "smoke-env",
                "script": "methods/smoke/smoke.sh",
                "slot_outputs": {"output": "output.txt"},
            },
        ],
        "links": [
            {"source": "sel", "target": "n1", "target_slot": "trigger"},
        ],
        "samples": ["s1"],
        "param_sets": {},
    }
    (proj / "pipeline.json").write_text(json.dumps(pipeline))
    _commit_all(proj)

    s = Step(step_num=3, name="Dispatch via wfc run-step",
             purpose="The production run-step path assembles "
                     "[/bin/bash, smoke.sh] and runs it in the container")
    env = os.environ.copy()
    env["WFC_PROJECT_ROOT"] = str(proj)
    env["DATABASE_URL"] = f"sqlite:///{proj / '.wfc' / 'wfc.db'}"
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
        cwd=proj, env=env, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, (
        f"wfc run-step failed (rc={result.returncode})\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    s = Step(step_num=4, name="Assert host-visible output + env-var contract",
             purpose="output.txt exists under .runs/ and embeds the "
                     "WFC_INPUT_PATHS value the script read in-container")
    matches = list((proj / ".runs").rglob("output.txt"))
    assert matches, (
        f"output.txt not found under {proj / '.runs'}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    content = matches[0].read_text()
    assert content.startswith("hello-from-bash"), f"Unexpected: {content!r}"
    # The script really read WFC_INPUT_PATHS from its environment.
    assert "inputs={" in content, f"WFC_INPUT_PATHS not seen: {content!r}"


@workflow(
    purpose="A mixed-language pipeline (Python producer -> R "
            "consumer in a byo rocker env registered with interpreter "
            "Rscript) runs end-to-end via wfc run-pipeline with the "
            "cross-language handoff flowing through WFC_INPUT_PATHS",
    inputs="tmp git project; byo r-env registered via the production "
           "wfc.environments.register path (python_override='Rscript'); producer.py "
           "+ consumer.R methods; pipeline exported through the GUI seam",
    outputs="run-pipeline exits 0; consumer output transforms the producer "
            "payload; both steps record non-null cache keys",
)
def test_python_to_r_pipeline_hands_off_across_languages(
    tmp_path: Path, minimal_image: str, r_image: str, monkeypatch
) -> None:
    """End-to-end mixed-language pipeline: a Python producer writes a file, an
    R consumer (byo rocker env registered via the production byo path with
    ``python_override='Rscript'``) reads it through ``WFC_INPUT_PATHS`` via
    ``Sys.getenv``, writes its output slot, and both steps record cache keys."""
    s = Step(step_num=1, name="Materialize project + register R env (byo)",
             purpose="Tmp git project; rocker image digest-pinned through the "
                     "production byo register path with interpreter Rscript")
    proj = _init_git_project(tmp_path, monkeypatch)
    _write_py_env(proj, minimal_image, interpreter="python")

    # Register the R env through the PRODUCTION byo path — pulls/inspects the
    # rocker image, digest-pins it, and records interpreter 'Rscript'
    # (the `wfc register-env r-env --backend byo --image docker://rocker/...
    # --interpreter Rscript` flow).
    from wfc.environments import register as register_env
    record = register_env(
        name="r-env",
        backend="byo",
        source={"image": f"docker://{r_image}"},
        project_dir=proj,
        python_override="Rscript",
    )
    assert record.python == "Rscript"
    assert "@sha256:" in record.container

    s = Step(step_num=2, name="Author + register both methods",
             purpose="Python producer and R consumer registered through the "
                     "production register_method flow (probe finds .py/.R)")
    # Producer (Python): writes payload to its output slot.
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
        "    type: .txt\n"
        "    required: false\n"
        "outputs:\n"
        "  output:\n"
        "    type: .txt\n"
        "    required: true\n"
        "params: {}\n"
        "env: smoke-env\n"
    )

    # Consumer (R): base-R only (no jsonlite in r-ver) — extract the slot
    # path from the WFC_INPUT_PATHS JSON with a regex, read the payload,
    # write the transformed output via WFC_RUN_DIR.
    consumer_dir = proj / "methods" / "consumer"
    consumer_dir.mkdir(parents=True)
    (consumer_dir / "consumer.R").write_text(
        'ip <- Sys.getenv("WFC_INPUT_PATHS")\n'
        'm <- regmatches(ip, regexpr(\'"data"[[:space:]]*:[[:space:]]*\\\\[?[[:space:]]*"[^"]+"\', ip))\n'
        'if (length(m) == 0) stop(paste0("no data slot in WFC_INPUT_PATHS: ", ip))\n'
        'path <- sub(\'.*"([^"]+)"$\', "\\\\1", m)\n'
        'if (!file.exists(path)) stop(paste0("input path missing in container: ", path))\n'
        'payload <- readLines(path, warn = FALSE)\n'
        'run_dir <- Sys.getenv("WFC_RUN_DIR")\n'
        'writeLines(paste0(payload, "-r-consumed"), file.path(run_dir, "transformed.txt"))\n'
    )
    (consumer_dir / "method.yaml").write_text(
        "inputs:\n"
        "  data:\n"
        "    type: .txt\n"
        "    required: true\n"
        "outputs:\n"
        "  transformed:\n"
        "    type: .txt\n"
        "    required: true\n"
        "params: {}\n"
        "env: r-env\n"
    )

    register_test_method(
        project_dir=proj, module_name="pipe",
        method_dir=producer_dir, method_name="producer",
    )
    register_test_method(
        project_dir=proj, module_name="pipe",
        method_dir=consumer_dir, method_name="consumer",
    )
    _seed_trigger_sample(proj)

    s = Step(step_num=3, name="Export pipeline via GUI seam + run-pipeline",
             purpose="_enrich_pipeline derives env/script/slot wiring from "
                     "the DB; one wfc run-pipeline invocation runs both steps")
    # Route through the real GUI /run export seam, as the sibling
    # two-node test does — env, script path (.R), and slot_outputs are all
    # derived from the registered contracts in the DB.
    from wfc.canvas.models import PipelineInput, PipelineLink, PipelineNode
    from wfc.canvas.submission import _enrich_pipeline
    pipeline_input = PipelineInput(
        name="crosslang",
        nodes=[
            PipelineNode(id="sel", type="input_selector", samples=["s1"]),
            PipelineNode(id="p1", type="method", method="producer", module="pipe"),
            PipelineNode(id="c1", type="method", method="consumer", module="pipe"),
        ],
        links=[
            PipelineLink(source="sel", target="p1", targetHandle="trigger"),
            PipelineLink(
                source="p1", target="c1",
                sourceHandle="output", targetHandle="data",
            ),
        ],
        samples=["s1"],
    )
    enriched = _enrich_pipeline(pipeline_input)
    (proj / "pipeline.json").write_text(json.dumps(enriched))
    _commit_all(proj)

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
        cwd=proj, env=env, capture_output=True, text=True, timeout=900,
    )
    assert result.returncode == 0, (
        f"wfc run-pipeline failed (rc={result.returncode})\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    s = Step(step_num=4, name="Assert cross-language handoff + cache keys",
             purpose="Consumer output transformed the producer payload; both "
                     "runs recorded non-null cache keys")
    consumer_outputs = list((proj / ".runs").rglob("transformed.txt"))
    assert consumer_outputs, (
        f"transformed.txt not found under {proj / '.runs'}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert consumer_outputs[0].read_text().strip() == "payload-v1-r-consumed", (
        f"Unexpected consumer output: {consumer_outputs[0].read_text()!r}"
    )

    # Per-step cache keys behaved normally: both runs recorded one.
    con = sqlite3.connect(proj / ".wfc" / "wfc.db")
    try:
        keys = [row[0] for row in con.execute("SELECT cache_key FROM runs")]
    finally:
        con.close()
    assert len(keys) >= 2 and all(k for k in keys), (
        f"Expected non-null cache keys for both steps, got: {keys}"
    )
