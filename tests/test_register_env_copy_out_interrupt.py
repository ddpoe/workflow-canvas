"""Ctrl-C during register-env's copy-out ends with one plain line, exit 130.

A conda env on a Windows host is registered through the production verb
(``wfc.environments.verbs.register_env``), so the registration runs the solve
build and then copies the solved list out of it. Docker is faked at its
subprocess edges as in ``test_register_env_log_commands`` (the same edges,
reused); the copy-out's ``docker cp`` raises ``KeyboardInterrupt``, as
``subprocess.run`` raises it when the console's Ctrl-C reaches wfc.
"""

from __future__ import annotations

import json

from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    fake_conda_list_explicit,
    fake_conda_solve_capture,
    fake_pip_freeze,
    stub_host_conda_platform,
)
from tests.test_register_env_log_commands import ENV, FIXTURES, _conda_env, _Edges
from wfc import layout
from wfc.environments import progress


class _CopyStoppedEdges(_Edges):
    """The same edges, but the copy-out's ``docker cp`` is stopped by Ctrl-C."""

    def _answer(self, argv):
        if argv[1] == "cp":
            raise KeyboardInterrupt
        return super()._answer(argv)


def _recorded(project) -> bool:
    manifest = layout.env_manifest_path(project)
    return manifest.exists() and ENV in json.loads(
        manifest.read_text(encoding="utf-8")).get("envs", {})


@workflow(purpose="Ctrl-C during register-env's copy-out ends with the same one "
                  "plain line the capture's Ctrl-C gets, naming the log, exit "
                  "130, no traceback; the copy-out's container is still removed "
                  "and nothing is recorded")
def test_ctrl_c_during_the_copy_out_ends_with_one_line_and_records_nothing(
        tmp_project, monkeypatch, capsys, tmp_path_factory):
    """A Ctrl-C in the copy-out ends as the capture's does: one line, 130."""
    from wfc.environments import verbs

    口 = Step(step_num=1, name="A Windows conda env whose copy-out is stopped",
             purpose="The host solves for linux-64, so the registration copies "
                     "the solved list out; that copy raises KeyboardInterrupt")
    conda_root = _conda_env(tmp_path_factory.mktemp("host"))
    stub_host_conda_platform(monkeypatch, "win-64")
    fake_conda_list_explicit(monkeypatch,
                             (FIXTURES / "win-64-explicit-list.txt").read_text(
                                 encoding="utf-8"))
    fake_conda_solve_capture(
        monkeypatch,
        from_history=(FIXTURES / "from-history.yml").read_text(encoding="utf-8"),
        list_json=json.loads((FIXTURES / "conda-list.json").read_text(
            encoding="utf-8")))
    fake_pip_freeze(monkeypatch, "")
    edges = _CopyStoppedEdges(monkeypatch, conda_root)

    口 = Step(step_num=2, name="Register the env", purpose="The production verb")
    rc = verbs.register_env(ENV, spec=f"conda:{ENV}", docker_gate=lambda: None)
    err = capsys.readouterr().err

    口 = Step(step_num=3, name="One plain line naming the log, exit 130",
             purpose="Besides the phase lines, stderr holds only the Stopped "
                     "line, which names the log; no traceback")
    assert rc == 130, err
    (log,) = sorted(layout.register_env_log_dir(tmp_project).glob(f"{ENV}-*.log"))
    others = [ln for ln in err.splitlines()
              if not ln.startswith(progress.LINE_PREFIX)]
    assert others == ["Stopped: the copy-out was stopped and nothing was "
                      f"recorded. (log: {log})"]
    assert "Traceback" not in err

    口 = Step(step_num=4, name="The container is removed, nothing is recorded",
             purpose="A forced remove of the copy-out's container follows the "
                     "stopped cp; no build after it, and no record")
    builds = [argv for argv in edges.docker if argv[:2] == ["docker", "build"]]
    runs = [argv for argv in edges.docker if argv[:2] != ["docker", "build"]]
    cp_at = [argv[1] for argv in runs].index("cp")
    name = runs[cp_at][2].split(":", 1)[0]
    assert runs[cp_at - 1][:2] == ["docker", "create"]
    assert runs[cp_at + 1:] == [["docker", "rm", "--force", name]]
    assert len(builds) == 1, builds
    assert not _recorded(tmp_project)
