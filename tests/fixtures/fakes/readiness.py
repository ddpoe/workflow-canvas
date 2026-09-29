"""Readiness boundary: the ``wfc doctor`` probes and the run gate they feed.

The probes (``check_git``, ``check_dvc``, ``check_docker``, ``check_samples``)
shell out to the developer's machine -- ``docker info`` alone is a 15-second
wait when the daemon is down -- so every test that reaches the gate but is
not about the gate stubs them. One fake, with a status per probe, plus the
three variants the census needed: restoring one probe to real, the demo
scaffold's own ``check_docker`` binding, and the DVC-importable flag.
"""

from __future__ import annotations

from ._entry import fake

# The real probe functions, captured when this module is first imported --
# which is at collection, before any test's autouse fixture has replaced
# them -- so a test can hand one probe back to production.
_ORIGINAL_PROBES: dict[str, object] = {}


def _original_probe(name: str):
    """Return the real ``check_<name>`` as it was before any stub."""
    if not _ORIGINAL_PROBES:
        from wfc.execution import readiness

        for probe in ("git", "dvc", "docker", "samples"):
            _ORIGINAL_PROBES[probe] = getattr(readiness, f"check_{probe}")
    return _ORIGINAL_PROBES[name]


# Prime the capture at import so no stub installed later is mistaken for the
# original.
_original_probe("git")


@fake(
    boundary="wfc.execution.readiness.check_git / check_dvc / check_docker / "
             "check_samples -- the four probes of the developer's machine that "
             "wfc doctor, wfc init's summary, run-step, register-env and the "
             "canvas submission gate consult",
    preserves="the CheckResult shape (name, status, message, fix_hint), one "
              "status per probe, and any probe the caller leaves at None",
    not_proven="the real gate: that a stopped daemon, a missing git repo or "
               "an absent DVC is detected and reframed into the one-door "
               "message",
    backed_by="pm_mvp::tests.test_host_tool_witnesses::"
              "test_a_machine_that_is_not_ready_is_detected_and_reframed_at_"
              "the_door",
)
def stub_readiness_probes(
    monkeypatch,
    *,
    git: str | None = "ok",
    dvc: str | None = None,
    docker: str | None = "ok",
    samples: str | None = None,
) -> None:
    """Stub the run-readiness probes, one status per probe.

    The one readiness fake. Each keyword is the status the named probe
    reports (``"ok"``, ``"warn"`` or ``"fail"``); ``None`` leaves that probe
    real, so a caller can stub the daemon and still let ``check_git`` run
    against the project it serves. The defaults stub the two probes the run
    gate consults -- docker (a 15-second ``docker info`` when the daemon is
    down) and git -- and leave the DVC and sample probes alone.

    Plain function (no pytest fixture dependency) so a fixture, a helper or
    the scenario harness's stub rung can call it with any active
    ``MonkeyPatch``. Every consumer resolves the probes from
    ``wfc.execution.readiness``'s own module globals, so patching the
    attributes there reaches them all.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch`` (the fixture, or a
            context-managed instance).
        git: Status for the git probe, or ``None`` to keep it real.
        dvc: Status for the DVC probe, or ``None`` to keep it real.
        docker: Status for the Docker probe, or ``None`` to keep it real.
        samples: Status for the sample-reachability probe, or ``None`` to
            keep it real.
    """
    from wfc.execution import readiness

    for name, status in (("git", git), ("dvc", dvc), ("docker", docker),
                         ("samples", samples)):
        if status is None:
            continue

        def probe(*a, _name=name, _status=status, **k):
            return readiness.CheckResult(_name, _status, f"{_name} msg",
                                         f"{_name} hint")

        monkeypatch.setattr(readiness, f"check_{name}", probe)


@fake(
    boundary="wfc.execution.readiness.check_<name> -- the inverse of "
             "stub_readiness_probes: one probe handed back to production "
             "under a module-wide autouse stub",
    preserves="the real probe, exactly as imported before any stub",
    not_proven="nothing about that probe -- it runs for real; the other "
               "probes stay whatever the autouse stub made them",
    backed_by="pm_mvp::tests.test_canvas_run::TestRunEndpoint."
              "test_real_path_submit_passes_real_git_gate",
)
def restore_readiness_probe(monkeypatch, name: str) -> None:
    """Re-bind one readiness probe to the real function for this test.

    For a module whose autouse fixture stubs every probe and one test that
    wants the real gate for a single probe (the real ``git`` over a
    committed fixture repo, say) while the daemon probe stays stubbed.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        name: ``"git"``, ``"dvc"``, ``"docker"`` or ``"samples"``.
    """
    from wfc.execution import readiness

    monkeypatch.setattr(readiness, f"check_{name}", _original_probe(name))


@fake(
    boundary="wfc.demo.scaffold.check_docker -- the demo's own binding of the "
             "docker probe, imported by name so the readiness stub does not "
             "reach it",
    preserves="the CheckResult shape; the DVC gate before it and the "
              "scaffolding after it are left real",
    not_proven="that a stopped daemon is what the demo's preflight sees",
    backed_by="pm_mvp::tests.integration.test_demo_integration::"
              "test_demo_scaffold_shape",
)
def stub_demo_docker_probe(monkeypatch, status: str = "fail", *,
                           reached: list[str] | None = None) -> None:
    """Stub the docker probe as the demo scaffold binds it.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        status: The status the probe reports.
        reached: When given, the probe appends ``"docker preflight"`` to it
            on every call, so a test can assert the preflight was the last
            thing reached.
    """
    import wfc.demo.scaffold as scaffold
    from wfc.execution import CheckResult

    def probe():
        if reached is not None:
            reached.append("docker preflight")
        return CheckResult(name="docker", status=status,
                           message=f"stubbed daemon {status}", fix_hint="")

    monkeypatch.setattr(scaffold, "check_docker", probe)


@fake(
    boundary="wfc.execution.readiness._dvc_available -- whether the DVC "
             "package imports on this interpreter",
    preserves="the probe's own warn-never-fail logic over the flag",
    not_proven="that an interpreter without DVC is detected as such",
    backed_by="pm_mvp::tests.test_host_tool_witnesses::"
              "test_check_dvc_warns_in_an_interpreter_without_dvc",
)
def stub_dvc_available(monkeypatch, available: bool) -> None:
    """Pin the DVC-importable flag the DVC probe reads.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        available: What ``_dvc_available()`` reports.
    """
    from wfc.execution import readiness

    monkeypatch.setattr(readiness, "_dvc_available", lambda: available)
