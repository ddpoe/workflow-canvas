"""``wfc list-envs`` and ``wfc show-env`` say where each env's image lives.

list-envs's REGISTRY column and show-env's registry lines are read from the
same Environments function as the canvas's Registry chip
(``registry_chip``), so the CLI and the canvas never disagree: the list-envs
steps read the canvas row too. Neither asks the registry; Docker not
answering is ``unknown``, never a traceback.

Projects come from the scenario harness and production register, push and
config paths (the helpers of ``tests/test_canvas_env_registry_state.py`` and
``tests/test_env_push_name_rule.py``); Docker and the registry are faked at
the ``wfc.environments.docker`` wrappers.
"""

from __future__ import annotations

from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    FAKE_PUSHED_DIGEST,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_docker_pull,
    stub_docker_push,
    stub_docker_read_manifest,
    stub_docker_repo_digest,
    stub_docker_tag,
)
from tests.fixtures.routes import canvas_client
from tests.test_canvas_env_registry_state import (
    BASE,
    BYO_REF,
    CURRENT,
    ENV,
    OLDER,
    _chip,
    _held,
    _never_the_registry,
    _set_base,
    _three_images,
)

RECREATE = (f"recreate it with `wfc register-env {ENV} --force`, then "
            f"`wfc register-method`")
PUSH = f"`wfc push-env {ENV}`"
DOCKER_DOWN = RuntimeError("Cannot connect to the Docker daemon")


def _list_envs(capsys) -> dict[str, str]:
    """Run ``wfc list-envs`` and return each env's REGISTRY cell."""
    from wfc.cli import cli_main

    capsys.readouterr()
    assert cli_main(["list-envs"]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    start = lines[0].index("REGISTRY")
    return {ln.split()[0]: ln[start:].strip() for ln in lines[1:]}


def _show_env(capsys, name: str) -> dict[str, list[str]]:
    """Run ``wfc show-env <name>`` and return each key's values, in order."""
    from wfc.cli import cli_main

    capsys.readouterr()
    assert cli_main(["show-env", name]) == 0
    fields: dict[str, list[str]] = {}
    for line in capsys.readouterr().out.splitlines():
        key, sep, value = line.partition(" : ")
        if sep:
            fields.setdefault(key.strip(), []).append(value)
    return fields


@workflow(purpose="wfc list-envs's REGISTRY column says what the canvas's "
                  "Registry chip says, in the chip's words, at every state: no "
                  "registry, unknown with Docker down, missing, not pushed with "
                  "a base, pushed after wfc push-env, own registry for a byo "
                  "registry image; older images not pushed and versions with "
                  "no image counted as the canvas counts them; the registry is "
                  "never asked")
def test_list_envs_registry_column_matches_the_canvas_chip(git_project,
                                                           monkeypatch, capsys):
    """Each step reads list-envs's cell and the canvas row's chip state."""
    from wfc import environments as envs_mod
    from wfc.cli import cli_main

    project = _three_images(git_project, monkeypatch)
    root = project.root
    stub_docker_read_manifest(monkeypatch, _never_the_registry)
    client = canvas_client(root, monkeypatch)

    def both() -> tuple[str, str]:
        return _list_envs(capsys)[ENV], _chip(client)["state"]

    口 = Step(step_num=1, name="No registry base",
             purpose="no registry; the gone image's version counted")
    stub_docker_image_inspect(monkeypatch, _held(OLDER, CURRENT))
    assert both() == ("no registry, 1 older missing", "none")

    口 = Step(step_num=2, name="Docker does not answer",
             purpose="unknown, nothing counted, exit 0")
    stub_docker_image_inspect(monkeypatch, DOCKER_DOWN)
    assert both() == ("unknown", "unknown")

    口 = Step(step_num=3, name="Every image gone from this machine",
             purpose="missing, the three older versions counted")
    stub_docker_image_inspect(monkeypatch, _held())
    assert both() == ("missing, 3 older missing", "missing")

    口 = Step(step_num=4, name="A base set, nothing pushed",
             purpose="not pushed; the older image here counted as not pushed")
    _set_base(root)
    stub_docker_image_inspect(monkeypatch, _held(OLDER, CURRENT))
    assert both() == ("not pushed, 1 older not pushed, 1 older missing",
                      "not_pushed")

    口 = Step(step_num=5, name="After wfc push-env --all",
             purpose="pushed; the gone image's version still counted")
    stub_docker_tag(monkeypatch)
    stub_docker_push(monkeypatch, FAKE_PUSHED_DIGEST)
    assert cli_main(["push-env", "--all", "--yes"]) == 1  # the gone image
    assert both() == ("pushed, 1 older missing", "pushed")

    口 = Step(step_num=6, name="The env re-registered as a byo registry image",
             purpose="own registry, where the canvas chip says pushed and "
                     "names the image's own registry")
    stub_docker_image_content(monkeypatch)
    stub_docker_image_inspect(monkeypatch, f"sha256:{'e' * 64}")
    stub_docker_repo_digest(monkeypatch, BYO_REF.split("@")[1])
    stub_docker_pull(monkeypatch, None)
    envs_mod.register(name=ENV, backend="byo", project_dir=root, force=True,
                      push=False, source={"image": f"docker://{BYO_REF}"})
    stub_docker_image_inspect(monkeypatch, _held(OLDER, CURRENT))
    assert both() == ("own registry, 1 older missing", "pushed")
    assert _chip(client)["own_registry"] == BYO_REF


@workflow(purpose="wfc show-env ends with the env's registry lines: the chip's "
                  "word, the base, then the canvas tooltip's lines naming the "
                  "fixing command (push-env, or the recreate command); the "
                  "pushed ref and digest once pushed; the byo image's own "
                  "registry ref; Docker down says Docker is not running")
def test_show_env_prints_where_the_image_is_and_the_fix(git_project,
                                                       monkeypatch, capsys):
    """show-env's registry lines at each state of the same project."""
    from wfc import environments as envs_mod
    from wfc.cli import cli_main

    project = _three_images(git_project, monkeypatch)
    root = project.root
    stub_docker_read_manifest(monkeypatch, _never_the_registry)

    口 = Step(step_num=1, name="No registry base",
             purpose="no registry; push-env sets one; the gone version's fix")
    stub_docker_image_inspect(monkeypatch, _held(OLDER, CURRENT))
    lines = _show_env(capsys, ENV)
    assert lines["registry_status"] == ["no registry, 1 older missing"]
    assert lines["registry_base"] == [""]
    assert lines["registry_note"] == [
        f"No registry: this image stays on this machine; {PUSH} sets one "
        f"and pushes it",
        f"1 older version with no image: {RECREATE}"]
    assert lines["name"] == [ENV]

    口 = Step(step_num=2, name="Docker does not answer",
             purpose="unknown, saying Docker is not running")
    stub_docker_image_inspect(monkeypatch, DOCKER_DOWN)
    lines = _show_env(capsys, ENV)
    assert lines["registry_status"] == ["unknown"]
    assert lines["registry_note"] == [
        "Docker is not running: cannot tell whether the image is on this "
        "machine"]

    口 = Step(step_num=3, name="Every image gone",
             purpose="missing, naming the recreate command")
    stub_docker_image_inspect(monkeypatch, _held())
    assert _show_env(capsys, ENV)["registry_note"] == [
        f"Not on this machine or in the registry: {RECREATE}",
        f"3 older versions with no image: {RECREATE}"]

    口 = Step(step_num=4, name="A base set, nothing pushed",
             purpose="not pushed to the base: run push-env, older ones too")
    _set_base(root)
    stub_docker_image_inspect(monkeypatch, _held(OLDER, CURRENT))
    lines = _show_env(capsys, ENV)
    assert lines["registry_base"] == [BASE]
    assert lines["registry_note"] == [
        f"Not pushed to {BASE}: run {PUSH}",
        f"1 older image not pushed: run {PUSH}",
        f"1 older version with no image: {RECREATE}"]
    assert "pushed_ref" not in lines

    口 = Step(step_num=5, name="After wfc push-env --all",
             purpose="the pushed ref and digest the push recorded")
    stub_docker_tag(monkeypatch)
    stub_docker_push(monkeypatch, FAKE_PUSHED_DIGEST)
    assert cli_main(["push-env", "--all", "--yes"]) == 1  # the gone image
    lines = _show_env(capsys, ENV)
    assert lines["registry_status"] == ["pushed, 1 older missing"]
    assert lines["pushed_ref"] == [f"{BASE}/{ENV}@{FAKE_PUSHED_DIGEST}"]
    assert lines["pushed_digest"] == [FAKE_PUSHED_DIGEST]
    assert lines["registry_note"][0] == f"Image is in {BASE}"

    口 = Step(step_num=6, name="A byo registry image",
             purpose="its own registry ref, no pushed ref")
    stub_docker_image_content(monkeypatch)
    stub_docker_image_inspect(monkeypatch, f"sha256:{'e' * 64}")
    stub_docker_repo_digest(monkeypatch, BYO_REF.split("@")[1])
    stub_docker_pull(monkeypatch, None)
    envs_mod.register(name=ENV, backend="byo", project_dir=root, force=True,
                      push=False, source={"image": f"docker://{BYO_REF}"})
    stub_docker_image_inspect(monkeypatch, _held(OLDER, CURRENT))
    lines = _show_env(capsys, ENV)
    assert lines["registry_status"] == ["own registry, 1 older missing"]
    assert lines["own_registry"] == [BYO_REF]
    assert lines["registry_note"][0] == f"Image is in its own registry: {BYO_REF}"
    assert "pushed_ref" not in lines


@workflow(purpose="An env whose name no registry accepts: list-envs says "
                  "cannot push beside a pushable env's no registry, and "
                  "show-env names the reason; with Docker down list-envs "
                  "still prints every row, each unknown, and exits 0")
def test_list_envs_cannot_push_and_docker_down_keeps_every_row(
        tmp_project, monkeypatch, capsys):
    """An unpushable name beside pushable ones; Docker down keeps every row."""
    from tests.test_env_push_name_rule import _setup

    _setup(tmp_project, monkeypatch, ["tools", "MyTools"])

    口 = Step(step_num=1, name="Both images on this machine, no base",
             purpose="tools: no registry; MyTools: cannot push, with the reason")
    # tmp_project's seeded fixture-env is a local image too.
    assert _list_envs(capsys) == {"MyTools": "cannot push",
                                  "fixture-env": "no registry",
                                  "tools": "no registry"}
    note = _show_env(capsys, "MyTools")["registry_note"]
    assert len(note) == 1
    assert note[0].startswith("Cannot be pushed: env 'MyTools' cannot be pushed")
    assert "`wfc delete-env MyTools`" in note[0]

    口 = Step(step_num=2, name="Docker does not answer",
             purpose="every row printed, each unknown, no traceback")
    stub_docker_image_inspect(monkeypatch, DOCKER_DOWN)
    assert _list_envs(capsys) == {"MyTools": "unknown", "fixture-env": "unknown",
                                  "tools": "unknown"}
