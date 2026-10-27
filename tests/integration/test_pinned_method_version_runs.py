"""Tier 3 integration test: a pinned method version runs in a real container.

One method is registered twice (its source changed between the two), and the
pipeline's node is pinned to the first version. ``wfc run-pipeline`` then runs
the first version's extracted copy, bound read-only, in the image that version
recorded: the method's output carries the first version's marker, and its
attempt to write into its own code directory is refused by the read-only bind.
Unpinned, the same node runs the newest version under another key.

``wfc register-env --force`` then rebuilds the env as a different image: it
mints no method version and lists the method whose newest version uses the
env. Once the first version's image is gone from the daemon, the pinned run is
refused before it starts, naming ``wfc register-method``.

Every step goes through production: ``git init`` and ``wfc init``, the CLI's
``register-env`` (byo, a locally built image), production method registration,
the canvas enrichment for the pipeline document, and ``wfc run-pipeline`` as a
subprocess.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.conftest import requires_docker
from tests.fixtures.conftest import (
    init_test_project,
    register_sample_row,
    register_test_method,
    sample_source_dir,
)
from wfc import layout

pytestmark = [pytest.mark.integration, requires_docker]

ENV = "pin-env"
MODULE = "pin"
METHOD = "stamp"

_METHOD_SOURCE = """\
import errno
import os
from pathlib import Path

MARK = {mark!r}


def main():
    here = Path(__file__).resolve().parent
    try:
        (here / 'scratch.txt').write_text('x')
        outcome = 'code-dir-writable'
    except OSError as exc:
        outcome = errno.errorcode.get(exc.errno, str(exc.errno))
    run_dir = Path(os.environ['WFC_RUN_DIR'])
    (run_dir / 'stamp.txt').write_text(MARK + ' ' + outcome)


if __name__ == '__main__':
    main()
"""

_METHOD_YAML = """\
inputs:
  trigger:
    type: .txt
    required: false
outputs:
  stamp:
    type: .txt
    required: true
params: {}
executor: local
env: pin-env
"""


def _run(argv: list[str], cwd: Path, env: dict | None = None,
         timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)


def _git(proj: Path, *args: str) -> None:
    result = _run(["git", *args], proj)
    assert result.returncode == 0, result.stderr


def _commit(proj: Path, message: str) -> None:
    _git(proj, "add", ".")
    _git(proj, "commit", "--allow-empty", "-m", message)


def _build_image(context: Path, tag: str, mark: str) -> str:
    """Build a one-line image on the minimal one; return its ``sha256:`` id.

    The ``ENV`` line makes each mark a distinct image (another config, so
    another id and another env fingerprint).
    """
    context.mkdir(parents=True, exist_ok=True)
    (context / "Dockerfile").write_text(
        f"FROM local/wfc-test-minimal:latest\nENV WFC_PIN_IMAGE={mark}\n",
        encoding="utf-8")
    build = _run(["docker", "build", "-t", tag, str(context)], context)
    assert build.returncode == 0, f"{build.stdout}\n{build.stderr}"
    inspect = _run(["docker", "image", "inspect", "--format", "{{.Id}}", tag],
                   context, timeout=60)
    assert inspect.returncode == 0, inspect.stderr
    return inspect.stdout.strip()


def _wfc(proj: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return _run([sys.executable, "-m", "wfc", *args], proj, env)


def _run_pipeline(proj: Path, env: dict) -> subprocess.CompletedProcess:
    return _wfc(proj, env, "run-pipeline", "--pipeline", str(proj / "pipeline.json"),
                "--project-root", str(proj), "--no-archive", "--cores", "1")


def _write_pipeline(proj: Path, version: str | None) -> None:
    from wfc.canvas.models import PipelineInput, PipelineLink, PipelineNode
    from wfc.canvas.submission import _enrich_pipeline

    enriched = _enrich_pipeline(PipelineInput(
        name="pinned",
        nodes=[
            PipelineNode(id="sel", type="input_selector", samples=["s1"]),
            PipelineNode(id="st", type="method", method=METHOD, module=MODULE),
        ],
        links=[PipelineLink(source="sel", target="st", targetHandle="trigger")],
        samples=["s1"],
    ))
    node = next(n for n in enriched["nodes"] if n["id"] == "st")
    if version is not None:
        node["version"] = version
    (proj / "pipeline.json").write_text(json.dumps(enriched, indent=2),
                                        encoding="utf-8")
    _commit(proj, f"pipeline: version {version}")


def _stamp_runs() -> list:
    from wfc.persistence import Method, Run, get_session

    with get_session() as session:
        method_id = session.exec(
            select(Method.id).where(Method.name == METHOD)).one()
        return list(session.exec(
            select(Run).where(Run.method_id == method_id).order_by(Run.id)).all())


def _version_row_id(entry) -> int:
    from wfc.persistence import MethodVersion, get_session

    with get_session() as session:
        return session.exec(select(MethodVersion.id).where(
            MethodVersion.label == entry.label,
            MethodVersion.tree_hash == entry.tree_hash)).one()


def _stamp_text(run_id: int, proj: Path) -> str:
    from wfc.persistence import RunOutput, get_session

    with get_session() as session:
        output = session.exec(select(RunOutput).where(
            RunOutput.run_id == run_id, RunOutput.slot == "stamp")).one()
    path = Path(output.artifact_path)
    path = path if path.is_absolute() else proj / path
    return path.read_text(encoding="utf-8")


@workflow(purpose="A node pinned to an older method version runs that version's "
                  "tree under its key in a real container: the copy is bound "
                  "read-only so the method cannot write into its code "
                  "directory; register-env --force lists the method whose "
                  "newest version uses the env and mints no version; once the "
                  "old version's image is gone the pinned run is refused naming "
                  "`wfc register-method`")
def test_a_pinned_version_runs_its_read_only_tree_and_its_lost_image_is_refused(
        tmp_path: Path, minimal_image: str, monkeypatch) -> None:
    from wfc.registration.method_version import read_version_list

    # One tag per image: removing image one is then removing its only tag.
    repo = f"local/wfc-test-pin-{uuid.uuid4().hex[:8]}"
    tags = (f"{repo}:one", f"{repo}:two")
    image_ids: list[str] = []
    proj = tmp_path / "proj"
    proj.mkdir()
    try:
        口 = Step(step_num=1, name="Make the project and register the env",
                 purpose="git init, wfc init, then `wfc register-env --backend "
                         "byo` of a locally built image (image one)")
        _git(proj, "init")
        _git(proj, "config", "user.email", "wfc@wfc")
        _git(proj, "config", "user.name", "wfc")
        _git(proj, "config", "core.autocrlf", "false")
        _git(proj, "config", "core.fileMode", "false")
        (proj / ".gitattributes").write_text("* -text\n", encoding="utf-8")
        monkeypatch.setenv("WFC_PROJECT_ROOT", str(proj))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{proj / '.wfc' / 'wfc.db'}")
        init_test_project(proj)
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)

        image_ids.append(_build_image(tmp_path / "image-one", tags[0], "one"))
        registered = _wfc(proj, env, "register-env", ENV, "--backend", "byo",
                          "--image", f"docker://{tags[0]}")
        assert registered.returncode == 0, registered.stderr
        assert "method(s) run env" not in registered.stdout

        口 = Step(step_num=2, name="Register the method twice",
                 purpose="v1 marks its output 'one', v2 'two'; both record "
                         "image one")
        source = sample_source_dir(proj) / "_methods" / METHOD
        source.mkdir(parents=True)
        (source / "method.yaml").write_text(_METHOD_YAML, encoding="utf-8")
        for mark in ("one", "two"):
            (source / f"{METHOD}.py").write_text(
                _METHOD_SOURCE.format(mark=mark), encoding="utf-8")
            register_test_method(project_dir=proj, module_name=MODULE,
                                 method_dir=source, method_name=METHOD)
        v1, v2 = read_version_list(proj, MODULE, METHOD)
        assert v1.tree_hash != v2.tree_hash
        assert v1.image_ref == v2.image_ref and image_ids[0] in v1.image_ref

        sample = sample_source_dir(proj) / "s1"
        sample.mkdir(parents=True)
        (sample / "trigger.txt").write_text("trigger", encoding="utf-8")
        register_sample_row(proj, "s1", sample / "trigger.txt")

        口 = Step(step_num=3, name="Run the pipeline pinned to v1",
                 purpose="v1's copy runs read-only in image one, under v1's row")
        _write_pipeline(proj, v1.identity)
        pinned = _run_pipeline(proj, env)
        assert pinned.returncode == 0, f"{pinned.stdout}\n{pinned.stderr}"
        (run,) = _stamp_runs()
        assert run.status == "completed", run.error_message
        assert run.version_id == _version_row_id(v1)
        assert run.env_fingerprint == v1.env_fingerprint
        # The method could not write beside its own script: the bind is
        # read-only (EROFS), and nothing landed in the copy.
        assert _stamp_text(run.id, proj) == "one EROFS"
        v1_copy = layout.code_copy_dir(proj, v1.tree_hash)
        assert v1_copy.is_dir()
        assert not list(v1_copy.rglob("scratch.txt"))
        assert not layout.code_copy_dir(proj, v2.tree_hash).exists()

        口 = Step(step_num=4, name="Run the pipeline unpinned",
                 purpose="The newest version, v2, runs under another key")
        _write_pipeline(proj, None)
        newest = _run_pipeline(proj, env)
        assert newest.returncode == 0, f"{newest.stdout}\n{newest.stderr}"
        first, second = _stamp_runs()
        assert second.status == "completed", second.error_message
        assert second.version_id == _version_row_id(v2)
        assert second.cache_key != first.cache_key
        assert _stamp_text(second.id, proj) == "two EROFS"

        口 = Step(step_num=5, name="Rebuild the env with --force",
                 purpose="The env now records image two; no version is minted, "
                         "and the method whose newest version uses the env is "
                         "listed with the instruction to register it again")
        image_ids.append(_build_image(tmp_path / "image-two", tags[1], "two"))
        assert image_ids[1] != image_ids[0]
        forced = _wfc(proj, env, "register-env", ENV, "--backend", "byo",
                      "--image", f"docker://{tags[1]}", "--force")
        assert forced.returncode == 0, forced.stderr
        assert image_ids[1] in forced.stdout
        assert f"1 method(s) run env '{ENV}'" in forced.stdout
        assert f"  - {MODULE}/{METHOD}" in forced.stdout
        assert "wfc register-method" in forced.stdout
        assert [e.identity for e in read_version_list(proj, MODULE, METHOD)] == [
            v1.identity, v2.identity]
        _commit(proj, "env rebuilt")

        口 = Step(step_num=6, name="Lose v1's image and run pinned to v1",
                 purpose="Image one is not the env's current image, so it is not "
                         "rebuilt: the run is refused naming the version and "
                         "`wfc register-method`, before any run row")
        removed = _run(["docker", "rmi", tags[0]], tmp_path, timeout=120)
        assert removed.returncode == 0, removed.stderr
        gone = _run(["docker", "image", "inspect", image_ids[0]], tmp_path,
                    timeout=60)
        assert gone.returncode != 0, "image one is still in the daemon"
        _write_pipeline(proj, v1.identity)
        refused = _run_pipeline(proj, env)
        message = refused.stdout + refused.stderr
        assert refused.returncode != 0, message
        assert f"Version v{v1.label} of method '{MODULE}/{METHOD}'" in message
        assert "wfc register-method" in message
        assert len(_stamp_runs()) == 2
    finally:
        for ref in (*tags, *image_ids):
            _run(["docker", "rmi", "-f", ref], tmp_path, timeout=120)
