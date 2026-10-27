"""Tier 3 integration tests: a rerun bundle's ``rerun.py`` against real Docker.

One project is built through production for the module: ``git init`` and
``wfc init``, the CLI's ``register-env`` (byo, a locally built image), the
methods registered through production, one sample, a canvas-enriched
pipeline and one ``wfc run-pipeline``. Its lineage::

    s1 -> src -> left
              -> right
              -> rand (random bytes) -> after (the input's length)
              -> broken (fails outside the project) -> below
    s1 -> src -> solo (its own env, an image never pushed)

Each case exports runs with ``wfc export-run --yes``, extracts the zip and
runs the bundle's own ``rerun.py`` with this interpreter in a fresh ``--work``
folder. ``broken`` reads ``/work/pipeline.json``, which the project mount
holds and a rerun's does not: a step that completed here and fails on rerun.

The registry tests build a second project whose ``[registry] url`` is a throwaway
``registry:2`` (the shared :func:`local_registry`), exports with and without
``--with-image``, stops the registry and deletes the image.

``integration``-marked (deselected by default); skips without a reachable
Docker daemon.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.conftest import requires_docker
from tests.fixtures.conftest import (
    init_test_project,
    register_sample_row,
    register_test_method,
    sample_source_dir,
)
from tests.integration.registry_fixture import image_absent, local_registry, remove_images
from tests.integration.test_pinned_method_version_runs import (
    _build_image,
    _commit,
    _git,
    _run,
    _run_pipeline,
    _wfc,
)

pytestmark = [pytest.mark.integration, requires_docker]

MODULE = "bundle"
ENV = "bundle-env"
SOLO_ENV = "solo-env"

#: rerun.py's last line when any output is DIFFERENT (Addendum 1, verbatim).
CLOSING = ("A byte difference is not always a different result: see 'If a step says "
           "DIFFERENT' in README.md.")
#: What the report says in place of the original when the bundle holds no outputs.
HASHES_ONLY = ("This bundle holds only the hashes of the original outputs. Ask the sender "
               "for a bundle exported with --with-outputs.")

#: Every method's script; ``__KIND__`` picks what it writes to ``out.txt``.
_SOURCE = """\
import json
import os
import sys
from pathlib import Path

KIND = '__KIND__'


def first(slot):
    value = json.loads(os.environ.get('WFC_INPUT_PATHS') or '{}').get(slot)
    if isinstance(value, list):
        value = value[0] if value else None
    return Path(value) if value else None


def main():
    out = Path(os.environ['WFC_RUN_DIR']) / 'out.txt'
    if KIND == 'src':
        trigger = first('trigger')
        out.write_text('src:' + (trigger.read_text() if trigger else 'none'))
    elif KIND == 'rand':
        out.write_bytes(os.urandom(32))
    else:
        data = first('data').read_bytes()
        if KIND == 'after':
            out.write_text(str(len(data)))
        elif KIND == 'broken' and not Path('/work/pipeline.json').is_file():
            sys.exit('pipeline.json is not in the project mount')
        else:
            out.write_bytes(data + b'-' + KIND.encode())


if __name__ == '__main__':
    main()
"""

_YAML = """\
inputs:
  {slot}:
    type: .txt
    required: {required}
outputs:
  out:
    type: .txt
    required: true
params: {{}}
executor: local
env: {env}
"""


@dataclass
class Project:
    """A project whose pipeline ran, with each node's run id."""

    root: Path
    env: dict
    runs: dict[str, int]
    images: list[str]


def _init(proj: Path) -> dict:
    """git init and wfc init; return the environment the CLI runs with."""
    proj.mkdir(parents=True)
    _git(proj, "init")
    _git(proj, "config", "user.email", "wfc@wfc")
    _git(proj, "config", "user.name", "wfc")
    _git(proj, "config", "core.autocrlf", "false")
    _git(proj, "config", "core.fileMode", "false")
    (proj / ".gitattributes").write_text("* -text\n", encoding="utf-8")
    init_test_project(proj)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return env


def _register_env(proj: Path, env: dict, name: str, tag: str) -> None:
    registered = _wfc(proj, env, "register-env", name, "--backend", "byo",
                      "--image", f"docker://{tag}")
    assert registered.returncode == 0, f"{registered.stdout}\n{registered.stderr}"


def _method(proj: Path, kind: str, env_name: str) -> None:
    source = sample_source_dir(proj) / "_methods" / kind
    source.mkdir(parents=True)
    slot, required = ("trigger", "false") if kind == "src" else ("data", "true")
    (source / "method.yaml").write_text(
        _YAML.format(slot=slot, required=required, env=env_name), encoding="utf-8")
    (source / f"{kind}.py").write_text(_SOURCE.replace("__KIND__", kind),
                                       encoding="utf-8")
    register_test_method(project_dir=proj, module_name=MODULE, method_dir=source,
                         method_name=kind)


def _pipeline_and_run(proj: Path, env: dict, edges: dict[str, str]) -> dict[str, int]:
    """Write the pipeline (``child -> parent``; ``src`` reads s1), run it, map nodes to runs."""
    from wfc.canvas.models import PipelineInput, PipelineLink, PipelineNode
    from wfc.canvas.submission import _enrich_pipeline
    from wfc.persistence import list_runs

    sample = sample_source_dir(proj) / "s1"
    sample.mkdir(parents=True)
    (sample / "trigger.txt").write_text("trigger", encoding="utf-8")
    register_sample_row(proj, "s1", sample / "trigger.txt")
    nodes = [PipelineNode(id="sel", type="input_selector", samples=["s1"]),
             PipelineNode(id="src", type="method", method="src", module=MODULE)]
    links = [PipelineLink(source="sel", target="src", targetHandle="trigger")]
    for child, parent in edges.items():
        nodes.append(PipelineNode(id=child, type="method", method=child, module=MODULE))
        links.append(PipelineLink(source=parent, target=child, sourceHandle="out",
                                  targetHandle="data"))
    enriched = _enrich_pipeline(PipelineInput(name="bundle", nodes=nodes, links=links,
                                              samples=["s1"]))
    (proj / "pipeline.json").write_text(json.dumps(enriched, indent=2), encoding="utf-8")
    _commit(proj, "pipeline")
    ran = _run_pipeline(proj, env)
    assert ran.returncode == 0, f"{ran.stdout}\n{ran.stderr}"
    runs = {r.node_id: r.run_id for r in list_runs() if r.status == "completed"}
    assert set(runs) == {"src", *edges}, runs
    return runs


@pytest.fixture(scope="module")
def lineage(tmp_path_factory, minimal_image: str) -> Iterator[Project]:
    """The module's project, run once; its images removed at the end."""
    base = tmp_path_factory.mktemp("bundle")
    proj = base / "proj"
    suffix = uuid.uuid4().hex[:8]
    tags = [f"local/wfc-test-bundle-{suffix}:one", f"local/wfc-test-bundle-solo-{suffix}:one"]
    images: list[str] = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("WFC_PROJECT_ROOT", str(proj))
        patch.setenv("DATABASE_URL", f"sqlite:///{proj / '.wfc' / 'wfc.db'}")
        try:
            env = _init(proj)
            images.append(_build_image(base / "image", tags[0], "bundle"))
            images.append(_build_image(base / "solo", tags[1], "solo"))
            _register_env(proj, env, ENV, tags[0])
            _register_env(proj, env, SOLO_ENV, tags[1])
            for kind in ("src", "left", "right", "rand", "after", "broken", "below"):
                _method(proj, kind, ENV)
            _method(proj, "solo", SOLO_ENV)
            runs = _pipeline_and_run(proj, env, {
                "left": "src", "right": "src", "rand": "src", "after": "rand",
                "broken": "src", "below": "broken", "solo": "src"})
            yield Project(proj, env, runs, images)
        finally:
            remove_images(*tags, *images)


def _export(project: Project, out: Path, *nodes: str, extra: tuple[str, ...] = ()) -> Path:
    """``wfc export-run --yes`` of the nodes' runs into *out*."""
    argv = ["export-run", "--out", str(out), "--yes", *extra]
    for node in nodes:
        argv += ["--run", str(project.runs[node])]
    exported = _wfc(project.root, project.env, *argv)
    assert exported.returncode == 0, f"{exported.stdout}\n{exported.stderr}"
    assert out.is_file()
    return out


def _rerun(bundle_zip: Path, where: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    """Extract the bundle into *where*/bundle and run its rerun.py into *where*/work."""
    bundle = where / "bundle"
    with zipfile.ZipFile(bundle_zip) as archive:
        archive.extractall(bundle)
    return _run([sys.executable, str(bundle / "rerun.py"), "--work", str(where / "work")],
                where, env=env, timeout=900)


def _step_line(stdout: str, run_id: int) -> str:
    """The report's first line for the step that reruns *run_id*."""
    lines = [line for line in stdout.splitlines()
             if line.startswith("Step ") and f" run {run_id} " in line]
    assert len(lines) == 1, stdout
    return lines[0]


def _rerun_file(stdout: str) -> Path:
    """The rerun's file the report names for the first difference; it exists."""
    named = [line.removeprefix("  Rerun's file: ") for line in stdout.splitlines()
             if line.startswith("  Rerun's file: ")]
    assert len(named) == 1, stdout
    path = Path(named[0])
    assert path.is_file(), named[0]
    return path


def _step_count(stdout: str) -> int:
    return sum(1 for line in stdout.splitlines() if line.startswith("Step "))


def _rerun_module():
    """``rerun.py`` as shipped, loaded as a module (it is never imported by wfc)."""
    from wfc.run_bundle.assemble import RERUN_SCRIPT

    spec = importlib.util.spec_from_file_location("shipped_rerun", RERUN_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@workflow(purpose="A deterministic two-step lineage, exported and rerun by the bundle's "
                  "rerun.py in a fresh directory under the project's interpreter: "
                  "every output is the same and the exit is 0")
def test_a_deterministic_lineage_reruns_with_every_output_the_same(
        lineage: Project, tmp_path: Path) -> None:
    口 = Step(step_num=1, name="Export the leaf",
             purpose="wfc export-run of `left` holds src and left")
    bundle = _export(lineage, tmp_path / "left.zip", "left")
    print(f"bundle size (2 steps, no image): {bundle.stat().st_size} bytes")

    口 = Step(step_num=2, name="Rerun it",
             purpose="python rerun.py --work <empty dir> in Docker")
    ran = _rerun(bundle, tmp_path)

    口 = Step(step_num=3, name="Read the report",
             purpose="Both steps ran, both outputs same, exit 0")
    assert ran.returncode == 0, f"{ran.stdout}\n{ran.stderr}"
    assert _step_count(ran.stdout) == 2, ran.stdout
    for node in ("src", "left"):
        assert _step_line(ran.stdout, lineage.runs[node]).endswith("same")
    assert "All 2 outputs are the same." in ran.stdout
    assert ran.stdout.splitlines()[-1].startswith("Logs and rerun outputs: ")
    assert CLOSING not in ran.stdout


@workflow(purpose="A step writing random bytes is the named first divergence, with the "
                  "rerun's file and the hashes-only sentence; the step below it still "
                  "runs; the last line points to the README's section; the exit is "
                  "non-zero")
def test_a_random_step_is_the_first_divergence_and_its_children_still_run(
        lineage: Project, tmp_path: Path) -> None:
    口 = Step(step_num=1, name="Export and rerun `after`",
             purpose="src, rand and after, rerun in Docker")
    ran = _rerun(_export(lineage, tmp_path / "after.zip", "after"), tmp_path)

    口 = Step(step_num=2, name="Read the report",
             purpose="rand DIFFERENT and named first; after ran (same: it records the "
                     "input's length); exit 1")
    assert ran.returncode == 1, f"{ran.stdout}\n{ran.stderr}"
    rand = lineage.runs["rand"]
    assert "DIFFERENT" in _step_line(ran.stdout, rand)
    assert _step_line(ran.stdout, lineage.runs["after"]).endswith("same")
    assert f"First difference: step 2 (run {rand}), out.txt" in ran.stdout

    口 = Step(step_num=3, name="Read the first difference's files and the last line",
             purpose="The rerun's out.txt exists; the bundle holds only hashes; the "
                     "closing line comes after the rerun-folder line")
    lines = ran.stdout.splitlines()
    assert _rerun_file(ran.stdout).name == "out.txt"
    assert "  " + HASHES_ONLY in lines
    assert lines[-2].startswith("Logs and rerun outputs: ")
    assert lines[-1] == CLOSING


@workflow(purpose="A --with-outputs bundle's first difference names the rerun's file "
                  "and the original at outputs/<step folder>/<file>, both present")
def test_a_with_outputs_rerun_names_the_original_of_the_first_difference(
        lineage: Project, tmp_path: Path) -> None:
    """Extra 1 in a bundle that holds the outputs."""
    口 = Step(step_num=1, name="Export `after` with its outputs and rerun it",
             purpose="src, rand and after, their outputs embedded")
    ran = _rerun(_export(lineage, tmp_path / "full.zip", "after",
                         extra=("--with-outputs",)), tmp_path)

    口 = Step(step_num=2, name="Read the first difference's two files",
             purpose="The rerun's file and the bundle's original differ in hash; the "
                     "original has the recorded hash")
    assert ran.returncode == 1, f"{ran.stdout}\n{ran.stderr}"
    original = [line.removeprefix("  Original: ") for line in ran.stdout.splitlines()
                if line.startswith("  Original: ")]
    assert len(original) == 1, ran.stdout
    record = json.loads((tmp_path / "bundle" / "wfc-run.json").read_text(encoding="utf-8"))
    rand = next(s for s in record["steps"] if s["run_id"] == lineage.runs["rand"])
    assert original[0] == f"outputs/{rand['folder']}/out.txt"
    recorded = tmp_path / "bundle" / original[0]
    assert _rerun_module().hash_path(recorded) == rand["outputs"][0]["content_hash"]
    assert _rerun_module().hash_path(_rerun_file(ran.stdout)) != rand["outputs"][0][
        "content_hash"]
    assert HASHES_ONLY not in ran.stdout
    assert ran.stdout.splitlines()[-1] == CLOSING


@workflow(purpose="A step that fails on rerun marks its descendants not run while a "
                  "sibling branch runs; the exit is non-zero")
def test_a_failing_step_stops_its_descendants_and_a_sibling_branch_runs(
        lineage: Project, tmp_path: Path) -> None:
    口 = Step(step_num=1, name="Export and rerun `below` and `left`",
             purpose="src, broken, below and left; broken fails outside the project")
    ran = _rerun(_export(lineage, tmp_path / "two.zip", "below", "left"), tmp_path)

    口 = Step(step_num=2, name="Read the report",
             purpose="broken FAILED, below not run naming it, left same, exit 1")
    assert ran.returncode == 1, f"{ran.stdout}\n{ran.stderr}"
    broken = lineage.runs["broken"]
    assert "FAILED" in _step_line(ran.stdout, broken)
    below = _step_line(ran.stdout, lineage.runs["below"])
    assert "not run: needs step" in below
    assert _step_line(ran.stdout, lineage.runs["left"]).endswith("same")
    assert f"(run {broken}) failed" in ran.stdout and "1 step below it did not run" in ran.stdout
    assert CLOSING not in ran.stdout, "steps not run are no byte difference"


@workflow(purpose="Two leaves over a shared upstream: the bundle and the rerun hold the "
                  "upstream step once")
def test_two_leaves_over_a_shared_upstream_rerun_it_once(
        lineage: Project, tmp_path: Path) -> None:
    口 = Step(step_num=1, name="Export and rerun `left` and `right`",
             purpose="Both read src's output")
    ran = _rerun(_export(lineage, tmp_path / "leaves.zip", "left", "right"), tmp_path)

    口 = Step(step_num=2, name="Read the report",
             purpose="Three steps; src once; exit 0")
    assert ran.returncode == 0, f"{ran.stdout}\n{ran.stderr}"
    assert _step_count(ran.stdout) == 3, ran.stdout
    _step_line(ran.stdout, lineage.runs["src"])
    assert len(list((tmp_path / "work" / "logs").iterdir())) == 3


@workflow(purpose="With a real image, rerun.py's image check gives wfc's env fingerprint "
                  "and refuses a different image")
def test_the_rerun_image_check_equals_the_env_fingerprint_on_real_images(
        lineage: Project, tmp_path: Path) -> None:
    from wfc.environments import docker as docker_runner
    from wfc.environments.fingerprint import content_fingerprint
    from wfc.persistence import Run, get_session

    shipped = _rerun_module()
    docker = shipped.Docker(shutil.which("docker"))
    with get_session() as session:
        recorded = session.get(Run, lineage.runs["src"]).env_fingerprint

    口 = Step(step_num=1, name="Check the run's own image",
             purpose="rerun.py's recipe over docker's inspect equals wfc's fingerprint "
                     "and the run's recorded one")
    inspected = docker.inspect(lineage.images[0])
    assert shipped.image_fingerprint(shipped.image_content(inspected)) == recorded
    assert content_fingerprint(docker_runner.image_content(lineage.images[0])) == recorded
    assert shipped.image_refusal(shipped.image_content(inspected), recorded) is None

    口 = Step(step_num=2, name="Check a different image",
             purpose="Another image (another ENV line) is refused, naming both fingerprints")
    other = shipped.image_content(docker.inspect(lineage.images[1]))
    refusal = shipped.image_refusal(other, recorded)
    assert refusal is not None and recorded in refusal


@workflow(purpose="A step whose image was never pushed and whose local copy is deleted "
                  "is not run on rerun, with the never-pushed reason")
def test_a_deleted_local_only_image_gives_the_never_pushed_reason(
        lineage: Project, tmp_path: Path) -> None:
    口 = Step(step_num=1, name="Export `solo` and delete its image",
             purpose="Its env's image is local only; --yes accepts the warning")
    bundle = _export(lineage, tmp_path / "solo.zip", "solo")
    remove_images(lineage.images[1])
    assert image_absent(lineage.images[1])

    口 = Step(step_num=2, name="Rerun",
             purpose="src runs; solo is not run; its image line says never pushed")
    ran = _rerun(bundle, tmp_path)
    assert ran.returncode == 1, f"{ran.stdout}\n{ran.stderr}"
    assert _step_line(ran.stdout, lineage.runs["src"]).endswith("same")
    assert "not run: its image is not available" in _step_line(ran.stdout,
                                                                lineage.runs["solo"])
    assert "it was never pushed to a registry" in ran.stdout
    assert "Re-export with --with-image" in ran.stdout


@workflow(purpose="Image lookup with a registry: once the registry is stopped and the "
                  "local image deleted, a plain bundle reports the image's steps not "
                  "run with the reason, and a --with-image bundle reruns fully offline "
                  "from its saved copy")
def test_a_pushed_image_gone_from_the_registry_needs_the_with_image_copy(
        tmp_path: Path, minimal_image: str, monkeypatch) -> None:
    from wfc.persistence.config import set_registry_url

    tag = f"local/wfc-test-bundle-reg-{uuid.uuid4().hex[:8]}:one"
    refs: list[str] = [tag]
    proj = tmp_path / "proj"
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(proj))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{proj / '.wfc' / 'wfc.db'}")
    try:
        with local_registry() as registry:
            口 = Step(step_num=1, name="Make a project that pushes to the live registry",
                     purpose="[registry] url set; register-env pushes the image")
            env = _init(proj)
            set_registry_url(proj, f"{registry}/wfcproj")
            _commit(proj, "registry")
            refs.append(_build_image(tmp_path / "image", tag, "registry"))
            _register_env(proj, env, ENV, tag)
            _method(proj, "src", ENV)
            _method(proj, "left", ENV)
            runs = _pipeline_and_run(proj, env, {"left": "src"})
            project = Project(proj, env, runs, [])

            口 = Step(step_num=2, name="Export twice",
                     purpose="A plain bundle, and one with --with-image")
            plain = _export(project, tmp_path / "plain.zip", "left")
            saved = _export(project, tmp_path / "saved.zip", "left", extra=("--with-image",))
            with zipfile.ZipFile(saved) as archive:
                assert f"images/{ENV}.tar" in archive.namelist()
            print(f"bundle size (2 steps): plain {plain.stat().st_size} bytes, "
                  f"--with-image {saved.stat().st_size} bytes")
            pushed = [ref for ref in _run(["docker", "image", "ls", "--format",
                                           "{{.Repository}}:{{.Tag}}"], tmp_path).stdout
                      .splitlines() if ref.startswith(registry)]
            refs += pushed

        口 = Step(step_num=3, name="Stop the registry and delete the image",
                 purpose="No copy on this machine under any name; no registry")
        remove_images(*refs)
        assert all(image_absent(ref) for ref in refs)

        口 = Step(step_num=4, name="Rerun the plain bundle",
                 purpose="Both steps not run; the image line names the failed pull")
        ran = _rerun(plain, tmp_path / "plain")
        assert ran.returncode == 1, f"{ran.stdout}\n{ran.stderr}"
        for node in ("src", "left"):
            assert "not run" in _step_line(ran.stdout, runs[node])
        assert "NOT AVAILABLE" in ran.stdout and "the pull failed" in ran.stdout

        口 = Step(step_num=5, name="Rerun the --with-image bundle",
                 purpose="docker load of images/<env>.tar matches the recorded env; "
                         "every output same, exit 0")
        offline = _rerun(saved, tmp_path / "saved")
        assert offline.returncode == 0, f"{offline.stdout}\n{offline.stderr}"
        assert "loaded from the bundle; contents match the recorded env" in offline.stdout
        assert "All 2 outputs are the same." in offline.stdout
    finally:
        remove_images(*refs)


@workflow(purpose="With no docker on PATH, or a daemon that does not answer, rerun.py "
                  "names it, exits 2 and runs nothing")
def test_rerun_without_docker_or_a_daemon_names_it_and_runs_nothing(tmp_path: Path) -> None:
    from wfc.run_bundle.assemble import RERUN_SCRIPT

    script = tmp_path / "rerun.py"
    shutil.copyfile(RERUN_SCRIPT, script)

    口 = Step(step_num=1, name="No docker on PATH",
             purpose="PATH holds only this interpreter's folder")
    bare = {k: v for k, v in os.environ.items() if k.upper() != "PATH"}
    bare["PATH"] = str(Path(sys.executable).parent)
    no_cli = _run([sys.executable, str(script), "--work", str(tmp_path / "a")],
                  tmp_path, env=bare, timeout=120)
    assert no_cli.returncode == 2, f"{no_cli.stdout}\n{no_cli.stderr}"
    assert "docker is not on PATH" in no_cli.stderr
    assert not (tmp_path / "a").exists()

    口 = Step(step_num=2, name="A daemon that does not answer",
             purpose="DOCKER_HOST names a port nothing listens on")
    dead = dict(os.environ, DOCKER_HOST="tcp://127.0.0.1:9")
    dead.pop("DOCKER_CONTEXT", None)
    no_daemon = _run([sys.executable, str(script), "--work", str(tmp_path / "b")],
                     tmp_path, env=dead, timeout=120)
    assert no_daemon.returncode == 2, f"{no_daemon.stdout}\n{no_daemon.stderr}"
    assert "The Docker daemon does not answer" in no_daemon.stderr
    assert not (tmp_path / "b").exists()
