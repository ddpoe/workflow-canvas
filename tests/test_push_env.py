"""wfc push-env pushes what is still local, and sets the registry base once.

``wfc push-env <name>`` (or ``--all``) pushes every image an env's current
record or any method version records, when it is on this machine and not yet
pushed, and reports by name (exit 1) an image that is neither. The first run
with no ``[registry] url`` proposes ``ghcr.io/<owner>/<repo>`` from a GitHub
remote (or takes ``--registry``), states it, asks, writes the base and
commits it with the push records; a later change of git remote never moves
it, and a ``--registry`` naming another base is refused.

Projects come from the shared fixtures (``tmp_project``, which runs ``wfc
init``, and the harness's ``build_project`` for method versions); envs and
methods are registered through production, the git remote with real ``git
remote``. Docker is faked at the ``wfc.environments.docker`` wrappers.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    FAKE_PUSHED_DIGEST,
    refuse_docker,
    stub_docker_build,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_docker_image_labels,
    stub_docker_pull,
    stub_docker_push,
    stub_docker_repo_digest,
    stub_docker_tag,
    stub_readiness_probes,
    stub_terminal,
)
from tests.harness import Scenario, build_project, node, selector, wire
from wfc.environments.docker import ImageNotFoundError

ENV = "image-io"
BASE = "ghcr.io/lab/tools-project"
#: The image the harness registers the env as (its stub digest), which the
#: harness's first method versions record; the older image in these tests.
OLDER = "c" * 64
GONE, CURRENT = "1" * 64, "3" * 64
HELD = {f"sha256:{OLDER}", f"sha256:{CURRENT}"}
PIXI_LOCK = "version: 6\nenvironments:\n  image-io:\n    packages: {}\n"
PIXI_TOML = ('[project]\nname = "image-io"\n'
             'channels = ["conda-forge"]\nplatforms = ["linux-64"]\n')


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file under *root* by relative path, with its bytes."""
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _push_env(*argv: str) -> int:
    from wfc.cli import cli_main

    return cli_main(["push-env", *argv])


def _register_env_cli(*argv: str) -> int:
    from wfc.cli import cli_main

    return cli_main(["register-env", *argv])


def _record(project: Path, env: str) -> dict:
    return json.loads((project / ".wfc" / "envs.json").read_text(
        encoding="utf-8"))["envs"][env]


def _pushed(project: Path, env: str, fingerprint: str) -> Path:
    return project / ".wfc" / "captures" / env / fingerprint / "pushed.json"


def _committed(project: Path) -> set[str]:
    return set(_git(project, "show", "--name-only", "--format=", "HEAD").split())


def _config(project: Path) -> Path:
    return project / ".wfc" / "wf-canvas.toml"


class _Docker:
    """A daemon holding the image IDs in *held*; tag and push recorded.

    A build gives the image it makes (the current one) the labels it was
    handed; any other image carries none. A tag is another name for its
    source, and a build of a ``FROM``-only context (the labelled copy a push
    makes) has its ``FROM`` image's content: the content stub installed
    before this daemon is asked for the name's source.
    """

    def __init__(self, monkeypatch, held: set[str] | None = None) -> None:
        from wfc.environments import docker as docker_runner

        self.held = held
        self.tags: list[tuple[str, str]] = []
        self.pushes: list[str] = []
        self.built_labels: dict[str, str] = {}
        self.copies: dict[str, dict[str, str]] = {}
        self.names: dict[str, str] = {}
        content = docker_runner.image_content
        stub_docker_build(monkeypatch, self._build)
        stub_docker_pull(monkeypatch)
        stub_docker_image_inspect(monkeypatch, self._inspect)
        stub_docker_image_content(monkeypatch,
                                  lambda ref: content(self._source(ref)))
        stub_docker_image_labels(monkeypatch, self._labels)
        stub_docker_repo_digest(monkeypatch, FAKE_PUSHED_DIGEST)
        stub_docker_tag(monkeypatch, self._tag)
        stub_docker_push(monkeypatch, self._push)

    def _build(self, build_dir, tag: str, labels=None, **kwargs) -> None:
        dockerfile = Path(build_dir) / "Dockerfile"
        lines = (dockerfile.read_text(encoding="utf-8").splitlines()
                 if dockerfile.is_file() else [])
        if len(lines) == 1 and lines[0].startswith("FROM "):
            self.copies[tag] = dict(labels or {})
        else:
            self.built_labels = dict(labels or {})

    def _tag(self, source: str, target: str) -> None:
        self.tags.append((source, target))
        if target != source:
            self.names[target] = source

    def _source(self, ref: str) -> str:
        while ref in self.names:
            ref = self.names[ref]
        return ref

    def _labels(self, ref: str) -> dict[str, str]:
        if ref in self.copies:
            return dict(self.copies[ref])
        return dict(self.built_labels) if CURRENT in self._source(ref) else {}

    def _inspect(self, ref: str) -> str:
        if self.held is None or ref.endswith(":_wfc-build"):
            return "sha256:" + CURRENT  # a build makes the current image
        for image_id in self.held:
            if image_id in ref:
                return image_id
        raise ImageNotFoundError(f"Error: No such image: {ref}")

    def _push(self, ref: str) -> str:
        self.pushes.append(ref)
        return FAKE_PUSHED_DIGEST


def _lock(tmp_path_factory) -> Path:
    lock = tmp_path_factory.mktemp("lock") / "pixi.lock"
    lock.write_text("version: 6\n", encoding="utf-8", newline="\n")
    return lock


def _register_image(root: Path, monkeypatch, image_id: str):
    """Register ENV through production as the image *image_id*, unpushed."""
    from wfc import environments as envs_mod

    stub_docker_build(monkeypatch, lambda build_dir, tag, **kwargs: None)
    stub_docker_image_content(monkeypatch,
                              lambda ref: {"layers": [f"layer-of-{ref}"]})
    stub_docker_image_inspect(monkeypatch, f"sha256:{image_id}")
    return envs_mod.register(
        name=ENV, backend="pixi", project_dir=root, force=True, push=False,
        source={"pixi_lock_content": PIXI_LOCK, "pixi_toml_content": PIXI_TOML,
                "pip_freeze_content": "numpy==1.26.4\n"})


def _register_method(project, node_id: str):
    """Register the node's method through production; return its newest entry."""
    from tests.harness.project import method_source_dir
    from wfc.registration import register_method, version_entry

    spec = project.scenario.node_by_id(node_id)
    register_method(method_dir=method_source_dir(project.root, spec),
                    module_name=spec.module, assume_yes=True)
    return version_entry(spec.module, spec.method_name)


def _versions_project(git_project, monkeypatch, nodes: list[str]):
    return build_project(
        Scenario(nodes=[selector(), *(node(n, inputs=[wire("sel")]) for n in nodes)],
                 samples=["S1"], pipeline_id="pipe-push-env", env_name=ENV,
                 env_backend="pixi"),
        root=git_project, monkeypatch=monkeypatch)


@workflow(purpose="An env registered with --no-push and then pushed with "
                  "push-env keeps its identity byte for byte: the env "
                  "fingerprint, envs.json, the method version lists and "
                  "image.json are unchanged, and the push adds only "
                  "pushed.json, committed alone")
def test_a_later_push_changes_no_identity(git_project, monkeypatch, capsys,
                                          tmp_path_factory):
    project = _versions_project(git_project, monkeypatch, ["a"])
    root = project.root
    stub_readiness_probes(monkeypatch, docker="ok")
    stub_docker_image_content(monkeypatch,
                              lambda ref: {"layers": [f"layer-of-{ref}"]})
    docker = _Docker(monkeypatch, held=HELD)
    from wfc.persistence import set_registry_url

    口 = Step(step_num=1, name="Register the env with --no-push, then its method",
             purpose="The base is set, but the image stays on this machine; "
                     "a method version records it (and v1 the harness's "
                     "first image)")
    set_registry_url(root, BASE)
    _git(root, "add", "--", ".wfc/wf-canvas.toml")
    _git(root, "commit", "-q", "-m", "set the registry base")
    assert _register_env_cli(ENV, "--from", str(_lock(tmp_path_factory)),
                             "--backend", "pixi", "--no-push", "--force") == 0
    _register_method(project, "a")
    assert docker.pushes == []
    fingerprint = _record(root, ENV)["env_fingerprint"]
    captures = root / ".wfc" / "captures" / ENV
    before = {"envs.json": (root / ".wfc" / "envs.json").read_bytes(),
              "versions": _tree_bytes(root / ".wfc" / "registry"),
              "captures": _tree_bytes(captures)}
    assert before["versions"] and f"{fingerprint}/image.json" in before["captures"]

    口 = Step(step_num=2, name="push-env <name>",
             purpose="Both images the env and its versions record are pushed "
                     "and their pushes recorded")
    assert _push_env(ENV, "--yes") == 0
    pushed = {name.split("/", 1)[0] for name in before["captures"]}
    assert sorted(docker.pushes) == sorted(f"{BASE}/{ENV}:fp-{fp[:12]}"
                                           for fp in pushed)
    assert fingerprint in pushed and len(pushed) == 2

    口 = Step(step_num=3, name="Nothing that names the image moved",
             purpose="Byte-identical envs.json, version lists and captures "
                     "files; pushed.json the only new files and the commit's "
                     "only paths")
    assert _record(root, ENV)["env_fingerprint"] == fingerprint
    assert (root / ".wfc" / "envs.json").read_bytes() == before["envs.json"]
    assert _tree_bytes(root / ".wfc" / "registry") == before["versions"]
    after = _tree_bytes(captures)
    added = {f"{fp}/pushed.json" for fp in pushed}
    assert set(after) == {*before["captures"], *added}
    assert {k: v for k, v in after.items() if k not in added} == before["captures"]
    assert _committed(root) == {f".wfc/captures/{ENV}/{name}" for name in added}


@workflow(purpose="push-env --all pushes every local, unpushed image the env's "
                  "current record and two methods' versions record, each "
                  "tagged <base>/<env>:fp-<12 hex> from its image ID, reports "
                  "the image no longer on this machine by name with the "
                  "version that needs it, and exits 1 after the rest are "
                  "pushed and committed")
def test_push_all_pushes_what_is_local_and_names_what_is_gone(
        git_project, monkeypatch, capsys):
    project = _versions_project(git_project, monkeypatch, ["a", "b"])
    root = project.root

    口 = Step(step_num=1, name="Three images of one env, recorded by versions",
             purpose="a v1 and b v1 record the harness's first image, still "
                     "here; b v2 one since removed from this machine; a v2 "
                     "the current one")
    fingerprints = {OLDER: _record(root, ENV)["env_fingerprint"]}
    for image_id, node_id in ((GONE, "b"), (CURRENT, "a")):
        record = _register_image(root, monkeypatch, image_id)
        fingerprints[image_id] = record.env_fingerprint
        _register_method(project, node_id)
    assert len(set(fingerprints.values())) == 3

    口 = Step(step_num=2, name="push-env --all with a first-run base",
             purpose="The daemon holds the older and the current image only")
    docker = _Docker(monkeypatch, held=HELD)
    capsys.readouterr()
    assert _push_env("--all", "--registry", BASE, "--yes") == 1
    out, err = capsys.readouterr()

    口 = Step(step_num=3, name="The local images are pushed, each by its tag",
             purpose="Every push carries fp-<first 12 hex of its fingerprint>, "
                     "tagged from the image's own ID")
    expected = {f"sha256:{image_id}": f"{BASE}/{ENV}:fp-{fingerprints[image_id][:12]}"
                for image_id in (OLDER, CURRENT)}
    assert sorted(docker.tags) == sorted(expected.items())
    assert sorted(docker.pushes) == sorted(expected.values())
    for image_id in (OLDER, CURRENT):
        assert _pushed(root, ENV, fingerprints[image_id]).is_file()
    assert not _pushed(root, ENV, fingerprints[GONE]).exists()
    committed = _committed(root)
    assert {f".wfc/captures/{ENV}/{fingerprints[i]}/pushed.json"
            for i in (OLDER, CURRENT)} | {".wfc/wf-canvas.toml"} == committed

    口 = Step(step_num=4, name="The gone image is reported by name",
             purpose="Its tag and the version recording it are named")
    gone = f"{ENV}:fp-{fingerprints[GONE][:12]}"
    assert gone in err and "/b v2" in err and "not on this machine" in err
    assert f"\n  push: {gone}" not in out


@workflow(purpose="With no registry base, the first push-env proposes "
                  "ghcr.io/<owner>/<repo> from an SSH GitHub remote, refuses "
                  "with no terminal and no --yes before anything is written, "
                  "and with --yes writes the base and commits it with the push "
                  "record; a later change of remote does not move the base, "
                  "and a --registry naming another base is refused, saying "
                  "the base cannot be changed, while the same base is accepted")
def test_the_first_push_sets_the_base_once(tmp_project, monkeypatch, capsys,
                                           tmp_path_factory):
    lock = _lock(tmp_path_factory)
    stub_readiness_probes(monkeypatch, docker="ok")
    stub_docker_image_content(monkeypatch,
                              lambda ref: {"layers": [f"layer-of-{ref}"]})
    docker = _Docker(monkeypatch)

    口 = Step(step_num=1, name="An env registered before any base",
             purpose="The project's remote is SSH on GitHub; the image stays local")
    _git(tmp_project, "remote", "add", "origin", "git@github.com:Lab/Tools.git")
    assert _register_env_cli("tools", "--from", str(lock), "--backend", "pixi") == 0
    fingerprint = _record(tmp_project, "tools")["env_fingerprint"]
    config_before = _config(tmp_project).read_bytes()
    head = _git(tmp_project, "rev-parse", "HEAD")

    口 = Step(step_num=2, name="No terminal and no --yes",
             purpose="The plan is shown, then refused: nothing pushed, written "
                     "or committed")
    stub_terminal(monkeypatch, interactive=False)
    capsys.readouterr()
    assert _push_env("tools") == 1
    out, err = capsys.readouterr()
    assert "ghcr.io/lab/tools" in out and "--yes" in err
    assert docker.pushes == []
    assert _config(tmp_project).read_bytes() == config_before
    assert _git(tmp_project, "rev-parse", "HEAD") == head

    口 = Step(step_num=3, name="--yes: propose, write, push, commit",
             purpose="The base is written to wf-canvas.toml and committed with "
                     "pushed.json in one commit")
    assert _push_env("tools", "--yes") == 0
    from wfc.persistence import read_config
    assert read_config(tmp_project)["registry"] == {"url": "ghcr.io/lab/tools"}
    assert docker.pushes == [f"ghcr.io/lab/tools/tools:fp-{fingerprint[:12]}"]
    assert _committed(tmp_project) == {
        ".wfc/wf-canvas.toml", f".wfc/captures/tools/{fingerprint}/pushed.json"}
    config_set = _config(tmp_project).read_bytes()

    口 = Step(step_num=4, name="The remote changes; the base does not",
             purpose="A second env pushed after the remote moved goes to the "
                     "base already set")
    _git(tmp_project, "remote", "set-url", "origin",
         "https://github.com/Other/Thing.git")
    assert _register_env_cli("other", "--from", str(lock), "--backend", "pixi",
                             "--no-push") == 0
    other = _record(tmp_project, "other")["env_fingerprint"]
    assert _push_env("other", "--yes") == 0
    assert docker.pushes[-1] == f"ghcr.io/lab/tools/other:fp-{other[:12]}"
    assert _config(tmp_project).read_bytes() == config_set

    口 = Step(step_num=5, name="--registry against a set base",
             purpose="Another base is refused, saying the base cannot be "
                     "changed; the same one is accepted")
    head = _git(tmp_project, "rev-parse", "HEAD")
    capsys.readouterr()
    assert _push_env("other", "--registry", "ghcr.io/other/thing", "--yes") == 1
    assert "cannot be changed once set" in capsys.readouterr().err
    assert _push_env("other", "--registry", "ghcr.io/lab/tools", "--yes") == 0
    assert _config(tmp_project).read_bytes() == config_set
    assert _git(tmp_project, "rev-parse", "HEAD") == head
    assert len(docker.pushes) == 2


@workflow(purpose="With no base and no GitHub remote, push-env refuses naming "
                  "--registry <url> before anything is pushed or written; "
                  "--registry then sets any base through the same "
                  "confirm-write-commit path")
def test_no_github_remote_needs_registry(tmp_project, monkeypatch, capsys,
                                         tmp_path_factory):
    stub_readiness_probes(monkeypatch, docker="ok")
    stub_docker_image_content(monkeypatch,
                              lambda ref: {"layers": [f"layer-of-{ref}"]})
    docker = _Docker(monkeypatch)
    assert _register_env_cli("tools", "--from", str(_lock(tmp_path_factory)),
                             "--backend", "pixi") == 0
    config_before = _config(tmp_project).read_bytes()
    capsys.readouterr()

    assert _push_env("tools", "--yes") == 1
    assert "--registry <url>" in capsys.readouterr().err
    assert docker.pushes == [] and _config(tmp_project).read_bytes() == config_before

    assert _push_env("tools", "--registry", "localhost:5000/lab", "--yes") == 0
    from wfc.persistence import read_config
    assert read_config(tmp_project)["registry"] == {"url": "localhost:5000/lab"}
    assert docker.pushes[0].startswith("localhost:5000/lab/tools:fp-")


def test_push_env_needs_a_name_or_all(tmp_project, monkeypatch, capsys):
    refuse_docker(monkeypatch)

    assert _push_env() == 1
    err = capsys.readouterr().err
    assert "<name>" in err and "--all" in err
    assert _push_env("tools", "--all") == 1
    assert "not both" in capsys.readouterr().err
