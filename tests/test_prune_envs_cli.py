"""``wfc prune-envs``, the ``list-envs`` footer and the doctor's leftovers row.

Case 7's confirm paths through the CLI: a yes stops what the plan lists, a
no, Ctrl-C at the question, ``--dry-run`` and no terminal without ``--yes``
change nothing, ``--yes`` skips the question, and nothing to stop asks
nothing. Case 9: with leftovers, ``list-envs`` ends with a footer and the
doctor's row warns, both naming ``wfc prune-envs``; with Docker unreachable
there is no footer. Case 10: none of these messages, and none of the verbs'
help, names a ``docker`` command. Execution's liveness is the production
one: a run is still running while its pipeline's record names a live owner
(written here through ``write_pipeline_record``). Docker and the process
table are the registered fakes at Environments' edges.
"""

from __future__ import annotations

import re

from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    fake_build_processes,
    fake_leftover_daemon,
    stub_docker_image_inspect,
    stub_interactive_prompt,
    stub_prompt_interrupt,
    stub_terminal,
)
from wfc.cli import build_parser, cli_main
from wfc.environments import container_labels
from wfc.environments.docker import DockerTimeoutError

#: A Docker command named in a message (case 10 forbids it).
_DOCKER_COMMAND = re.compile(
    r"\bdocker\s+(ps|kill|run|rm|rmi|stop|container|image|images|build|buildx|"
    r"builder|system|inspect|prune)\b", re.IGNORECASE)


def _leftovers(root, monkeypatch):
    """An ended run's container, a live pipeline's container, an orphaned build."""
    from wfc.execution.pipeline_record import ENTRY_POINT_CLI, write_pipeline_record

    daemon = fake_leftover_daemon(monkeypatch)
    procs = fake_build_processes(monkeypatch)
    ended = daemon.start(container_labels(root, pipeline_id="p-ended", run_id="7"),
                         name="step-7")
    from wfc import layout

    # The launch makes the pipeline's run folder before it writes the record.
    (layout.pipelines_dir(root) / "p-live").mkdir(parents=True)
    write_pipeline_record(root, "p-live", ENTRY_POINT_CLI)  # owned by this process
    live = daemon.start(container_labels(root, pipeline_id="p-live", run_id="8"),
                        name="step-8")
    procs.build(root / ".wfc" / "build" / "alpha", "alpha")
    return daemon, procs, ended, live


def _answer(monkeypatch, reply):
    """Answer the terminal's question with *reply*; ``None`` is Ctrl-C at it."""
    asked: list[str] = []
    if reply is None:
        stub_prompt_interrupt(monkeypatch, prompts=asked)
    else:
        stub_interactive_prompt(monkeypatch, reply, prompts=asked)
    return asked


def _prune(capsys, *argv) -> tuple[int, str]:
    capsys.readouterr()
    rc = cli_main(["prune-envs", *argv])
    captured = capsys.readouterr()
    return rc, captured.out + captured.err


@workflow(purpose="Case 7's confirm paths: --dry-run, no terminal without "
                  "--yes, a no and Ctrl-C at Proceed? stop nothing; a yes "
                  "stops the ended run's container and the orphaned build and "
                  "leaves the live pipeline's container; --yes stops without "
                  "asking; nothing to stop asks nothing; no message names a "
                  "docker command")
def test_prune_envs_changes_exactly_what_each_answer_says(tmp_project, monkeypatch, capsys):
    """Every confirm path of wfc prune-envs through the CLI."""
    daemon, procs, ended, live = _leftovers(tmp_project, monkeypatch)
    said: list[str] = []

    口 = Step(step_num=1, name="Dry run",
             purpose="The plan names the container and the build; nothing stops")
    rc, out = _prune(capsys, "--dry-run")
    said.append(out)
    assert rc == 0, out
    assert "step-7 (pipeline p-ended, run 7)" in out and "alpha (running for" in out
    assert "step-8" not in out and "Dry run: nothing was stopped." in out
    assert daemon.killed == [] and procs.events == []

    口 = Step(step_num=2, name="No terminal and no --yes",
             purpose="Refused naming --yes; nothing stops")
    stub_terminal(monkeypatch, False)
    rc, out = _prune(capsys)
    said.append(out)
    assert rc == 1 and "--yes" in out and "Nothing was stopped" in out, out
    assert daemon.killed == [] and procs.events == []

    口 = Step(step_num=3, name="A no, then Ctrl-C at the question",
             purpose="Each says nothing was done and exits 1; nothing stops")
    stub_terminal(monkeypatch, True)
    for reply in ("n", None):
        asked = _answer(monkeypatch, reply)
        rc, out = _prune(capsys)
        said.append(out)
        assert rc == 1 and asked == ["Proceed? [y/N]: "], out
        assert "Nothing was stopped." in out
        assert daemon.killed == [] and procs.events == []

    口 = Step(step_num=4, name="A yes",
             purpose="The ended run's container and the build stop; the live "
                     "pipeline's container keeps running")
    _answer(monkeypatch, "y")
    rc, out = _prune(capsys)
    said.append(out)
    assert rc == 0, out
    assert daemon.killed == [ended] and live in daemon.running
    assert [e[0] for e in procs.events] == ["interrupt", "wait"]
    assert "Stopped run container step-7" in out and "Stopped env build alpha" in out

    口 = Step(step_num=5, name="--yes, then nothing to stop",
             purpose="--yes stops a new leftover without asking; with nothing "
                     "left the plan prints and exits 0 without asking")
    again = daemon.start(container_labels(tmp_project, pipeline_id="p-ended", run_id="9"))
    asked = _answer(monkeypatch, "n")
    rc, out = _prune(capsys, "--yes")
    said.append(out)
    assert rc == 0 and daemon.killed == [ended, again] and asked == [], out
    stub_terminal(monkeypatch, False)
    rc, out = _prune(capsys)
    said.append(out)
    assert rc == 0 and "Nothing to stop." in out, out
    assert live in daemon.running

    text = "\n".join(said)
    assert not _DOCKER_COMMAND.search(text), text


@workflow(purpose="Case 9: with an ended run's container and an orphaned build "
                  "left, list-envs ends with a footer and the doctor's "
                  "leftovers row warns, both giving the counts and naming wfc "
                  "prune-envs; with Docker unreachable list-envs prints its "
                  "table with no footer; none names a docker command")
def test_list_envs_footer_and_the_doctor_row_name_prune_envs(tmp_project, monkeypatch, capsys):
    """The footer and the row with leftovers; no footer with Docker unreachable."""
    from wfc.execution import check_leftovers, env_image_checks

    daemon, _, _, _ = _leftovers(tmp_project, monkeypatch)
    stub_docker_image_inspect(monkeypatch, RuntimeError("Cannot connect to the Docker daemon"))

    口 = Step(step_num=1, name="Leftovers",
             purpose="The footer and the warn row give the counts and name wfc prune-envs")
    capsys.readouterr()
    assert cli_main(["list-envs"]) == 0
    out = capsys.readouterr().out
    footer = out.rstrip().splitlines()[-1]
    assert footer.startswith("Left running by an earlier run or crash: "), out
    assert "1 run container to stop" in footer and "1 env build to stop" in footer
    assert "`wfc prune-envs`" in footer
    row = check_leftovers(tmp_project)
    assert (row.name, row.status) == ("leftovers", "warn"), row
    assert "1 run container to stop, 1 env build to stop" in row.message
    assert "wfc prune-envs" in row.fix_hint
    assert "check_leftovers" in env_image_checks.__doc__

    口 = Step(step_num=2, name="Docker unreachable",
             purpose="The table prints as today, with no footer")
    daemon.failure = DockerTimeoutError(
        "listing wfc's containers did not finish within 15 s: Docker is not answering")
    assert cli_main(["list-envs"]) == 0
    quiet = capsys.readouterr().out
    assert "prune-envs" not in quiet and "Left running" not in quiet, quiet
    assert quiet.splitlines()[0] == out.splitlines()[0]  # the table's header, as before

    text = "\n".join([out, row.message, row.fix_hint, quiet])
    assert not _DOCKER_COMMAND.search(text), text


def test_the_leftovers_rows_never_fail_and_say_what_is_kept(tmp_project, monkeypatch):
    """Nothing to stop is ok (what prune-envs also counts named, no numbers); a failed liveness lists no container."""
    from wfc.execution import RunLiveness, check_leftovers

    daemon = fake_leftover_daemon(monkeypatch)
    daemon.cache_size = "2GB"
    row = check_leftovers(tmp_project)
    assert row.status == "ok" and row.message.startswith("Nothing an earlier run"), row
    assert "`wfc prune-envs` also counts the env images" in row.message
    assert "build cache" in row.message and "2.0 GB" not in row.message, row
    daemon.failure = DockerTimeoutError("Docker is not answering")
    row = check_leftovers(tmp_project)
    assert row.status == "ok" and "not checked" in row.message, row
    broken = RunLiveness(tmp_project, error="the database is locked")
    assert broken("p", "1") and broken(None, None)
    assert not RunLiveness(tmp_project)("p-gone", "3")


def test_help_names_only_wfc_commands():
    """Case 10's help half: prune-envs, list-envs and doctor help name no docker command."""
    parser = build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    texts = [sub.choices[verb].format_help() for verb in ("prune-envs", "list-envs", "doctor")]
    texts += [a.help or "" for a in sub._choices_actions
              if a.dest in ("prune-envs", "list-envs", "doctor")]
    assert "prune-envs" in parser.format_help()
    for text in texts:
        assert not _DOCKER_COMMAND.search(text), text
