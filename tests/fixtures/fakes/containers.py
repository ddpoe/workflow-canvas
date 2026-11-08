"""Containers boundary: the Docker calls that list and stop wfc's containers by label.

``wfc.environments.containers._docker`` is the one edge a listing or a stop
reaches. :func:`fake_container_daemon` stands a model of the daemon's
running containers there: a listing filters them by label, a kill removes
them, and every call is recorded. ``tests/conftest.py`` arms an empty one in
every test not marked ``integration``, so a cancel or an interrupted launch
never reaches the real daemon. :func:`interrupt_labelled_launch` is the
launch side: the step's container starts in that model carrying the labels
its argv gives it, and the wait is then interrupted as Ctrl-C interrupts it.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

from ._entry import fake

#: The node id of the test that drives both boundaries for real.
_REAL_STOP = ("pm_mvp::tests.integration.test_run_container_stop_docker::"
              "test_an_interrupted_step_leaves_no_container_with_its_run_label")


class ContainerDaemon:
    """A model of the Docker daemon's running containers and their labels.

    Attributes:
        running: ``{container id: {label: value}}`` for every running container.
        calls: The argument lists of every Docker call made, in order.
        killed: The ids of the containers killed, in order.
        failure: ``None``; an exception every call raises; or a stderr text
            every call answers with exit status 1.
    """

    def __init__(self) -> None:
        self.running: dict[str, dict[str, str]] = {}
        self.calls: list[list[str]] = []
        self.killed: list[str] = []
        self.failure: BaseException | str | None = None
        #: Called with each call's arguments before it is answered (to see
        #: what else had happened by then); ``None`` for nothing.
        self.on_call = None
        self._count = 0

    def start(self, labels: dict[str, str]) -> str:
        """Start a container carrying *labels*; return its id."""
        self._count += 1
        cid = f"{self._count:064x}"
        self.running[cid] = dict(labels)
        return cid

    def answer(self, args: Sequence[str], *, what: str) -> subprocess.CompletedProcess:
        """Answer one Docker call the way the daemon would."""
        args = list(args)
        self.calls.append(args)
        if self.on_call is not None:
            self.on_call(args)
        if isinstance(self.failure, BaseException):
            raise self.failure
        if isinstance(self.failure, str):
            return subprocess.CompletedProcess(["docker", *args], 1, "", self.failure)
        if args[0] == "ps":
            wanted = [value.removeprefix("label=").split("=", 1)
                      for flag, value in zip(args, args[1:]) if flag == "--filter"]
            ids = [cid for cid, labels in self.running.items()
                   if all(labels.get(k) == v for k, v in wanted)]
            return subprocess.CompletedProcess(["docker", *args], 0,
                                               "".join(f"{c}\n" for c in ids), "")
        if args[0] == "kill":
            for cid in args[1:]:
                self.running.pop(cid, None)
                self.killed.append(cid)
            return subprocess.CompletedProcess(["docker", *args], 0, "", "")
        raise AssertionError(f"a listing or a stop made an unexpected call: {args}")


@fake(
    boundary="wfc.environments.containers._docker -- the Docker calls that list "
             "the running containers carrying a label set and kill them",
    preserves="the label set asked for and every call made, recorded; a model "
              "of running containers filtered on every label at once (a "
              "container lacking one label is not listed); the stop's own "
              "sequencing, verdict and error text, and every caller's handling",
    not_proven="that Docker filters on several labels as an AND, that a kill "
               "ends the container, or that a real container carries the "
               "labels its argv gave it",
    backed_by="pm_mvp::tests.integration.test_run_container_stop_docker::"
              "test_an_interrupted_step_leaves_no_container_with_its_run_label",
)
def fake_container_daemon(monkeypatch) -> ContainerDaemon:
    """Replace the listing and stopping Docker edge with a model daemon.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        The daemon: start containers in it, read its calls and kills, or set
        its ``failure``.
    """
    from wfc.environments import containers

    daemon = ContainerDaemon()
    monkeypatch.setattr(containers, "_docker", daemon.answer)
    return daemon


def argv_labels(cmd: Sequence[str]) -> dict[str, str]:
    """The ``--label name=value`` pairs of a ``docker run`` argv."""
    return dict(value.split("=", 1) for flag, value in zip(cmd, cmd[1:])
                if flag == "--label")


@fake(
    boundary="wfc.execution.dispatch._run_method_subprocess -- the container "
             "launch: its container starts carrying its argv's labels, and the "
             "wait is then interrupted as Ctrl-C interrupts it",
    preserves="everything before the launch for real: the step's record, "
              "materialize, the argv with its labels and the variables; the "
              "container the argv labels is running in the model daemon when "
              "KeyboardInterrupt is raised",
    not_proven="a real SIGINT delivered to the wfc process mid-launch, or "
               "that Docker leaves the container running when its client goes",
    backed_by="pm_mvp::tests.integration.test_run_container_stop_docker::"
              "test_an_interrupted_step_leaves_no_container_with_its_run_label",
)
def interrupt_labelled_launch(monkeypatch, daemon: ContainerDaemon) -> list[list[str]]:
    """Make every container launch start its labelled container, then raise ``KeyboardInterrupt``.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        daemon: The model daemon the container starts in
            (:func:`fake_container_daemon`'s).

    Returns:
        The argv of every launch, appended as each starts.
    """
    from wfc.execution import dispatch as _dispatch

    launched: list[list[str]] = []

    def _interrupted(cmd, *, cwd, env, stdout_log, stderr_log):
        launched.append(list(cmd))
        daemon.start(argv_labels(cmd))
        raise KeyboardInterrupt

    monkeypatch.setattr(_dispatch, "_run_method_subprocess", _interrupted)
    return launched
