"""Can wfc still get this sample's bytes? The one table, and its two surfaces.

Local-cache presence x ``push_status`` classifies every registered sample
with zero network I/O, and the classification drives two things: the
pipeline-start refusal (``preflight_sample_content``) and ``wfc doctor``'s
``samples`` check.  Both read the same
``wfc.registration.sample_health.classify_sample``, so these tests assert the
table once and then assert what each surface does with it.

The organizing principle, which is what the assertions here actually pin:
**can the system recover this state by itself?**

- A sample cached locally and pushed is ``healthy``.
- A sample cached locally whose push never landed is ``local_only``: it runs
  here and the project is not reproducible anywhere else.  Worth reporting,
  never worth refusing over.
- A sample **not** cached locally but pushed is ``cold`` -- a cold start
  on a fresh machine.  The emitted restore rule pulls it, so it must neither
  refuse nor warn.  **The silent quadrants are asserted, not assumed**: a
  classifier whose stay-silent branch is untested eventually starts shouting.
- A sample neither cached nor pushed is ``unreachable``: the content is
  nowhere wfc can reach, and that is the one state that refuses.

**How each quadrant is reached.** Every row here is written by production
``register_sample`` (through ``create_sample_csv``), which in a project with
an archive lands ``push_status=pushed`` with the bytes in the local cache --
the ``healthy`` quadrant, free.  The other three are reached by moving one
variable at a time off that baseline:

- pruning the local cache entry is what a ``dvc gc`` or a cleaned checkout
  does, and it is exactly how ``cold`` happens in the field;
- setting ``push_status`` to ``deferred`` or ``failed`` is a field update on
  a real row, not a hand-built one.  ``deferred`` is the row default when no
  remote is configured at insert time and ``failed`` is what the push worker
  leaves after it exhausts its retries; neither is reachable from a project
  that *does* have a working archive, which is the project these tests need
  in order to reach ``pushed`` at all.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest
from sqlmodel import select

from axiom_annotations import workflow, Step

from tests.fixtures.conftest import create_sample_csv, run_cli
from tests.fixtures.fakes import stub_readiness_probes


def _row(name: str):
    """Read one sample row by name.

    Args:
        name: The registered sample name.

    Returns:
        The ``Sample`` row, detached from its session.
    """
    from wfc.persistence import Sample, get_session

    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == name)).first()
        assert row is not None, f"no samples row for '{name}'"
        session.expunge(row)
        return row


def _set_push_status(name: str, status: str) -> None:
    """Move one real sample row's ``push_status``.

    A field update on a row production wrote -- not a hand-built row. It is
    the only way to reach ``deferred`` / ``failed`` in a project that has a
    working archive, and a working archive is what makes ``pushed``
    reachable in the first place.

    Args:
        name: The registered sample name.
        status: The ``PushStatus`` value to store.
    """
    from wfc.persistence import Sample, get_session

    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == name)).first()
        row.push_status = status
        session.add(row)
        session.commit()


def register_healthy(project_dir: Path, name: str, num_rows: int):
    """Register one sample and assert it landed in the ``healthy`` quadrant.

    Registration standalone, with an archive wired and ``WFC_PIPELINE_ID``
    unset, pushes synchronously: the bytes are in the local cache *and* the
    archive and the row reads ``pushed``. Every other quadrant here is that
    baseline with one variable moved, so a silent failure to push would
    quietly turn the whole table into a different test.

    Args:
        project_dir: The project root.
        name: The sample name to register.
        num_rows: CSV row count -- distinct per sample, so each gets its own
            content-addressed cache entry.

    Raises:
        AssertionError: If the registration did not land ``pushed``.
    """
    create_sample_csv(project_dir, name, num_rows=num_rows)
    row = _row(name)
    assert row.push_status == "pushed", (
        f"'{name}' registered with push_status={row.push_status!r} "
        f"(push_error={row.push_error!r}); this module's quadrants are all "
        f"derived from a healthy baseline, so a registration that did not "
        f"push makes every case below test something else."
    )


def _prune_cache_entry(project_dir: Path, name: str) -> None:
    """Delete the local DVC cache entry a sample's content hash addresses.

    What ``dvc gc`` or a fresh checkout leaves behind. DVC clears the write
    bit on cache entries, so the mode is restored before the delete.

    Args:
        project_dir: The project root.
        name: The registered sample name.
    """
    from wfc import layout

    entry = layout.dvc_cache_entry(project_dir, _row(name).content_hash)
    if entry.is_dir():
        shutil.rmtree(entry, ignore_errors=True)
        return
    os.chmod(entry, stat.S_IREAD | stat.S_IWRITE)
    entry.unlink()


@pytest.fixture
def four_quadrants(tmp_project, monkeypatch):
    """Register four samples and move each into one quadrant of the table.

    Args:
        tmp_project: A real ``init_project`` output with an archive wired.
        monkeypatch: Used to clear ``WFC_PIPELINE_ID``.

    Returns:
        The project root.  The four names are ``healthy_s``,
        ``local_only_s``, ``cold_s`` and ``unreachable_s``.
    """
    # Standalone registration, the shape a user's `wfc register-sample` has:
    # inside a pipeline run the push is left to the worker and the row reads
    # `pending` instead. Same clear as test_dvc_sample_registration,
    # test_remote_integration and test_real_path_sample_lifecycle.
    monkeypatch.delenv("WFC_PIPELINE_ID", raising=False)

    # Distinct row counts, so each sample has distinct content and therefore
    # its own cache entry. Identical CSVs would share one content-addressed
    # entry and pruning "one" sample's would prune every sample's.
    for rows, name in enumerate(
        ("healthy_s", "local_only_s", "cold_s", "unreachable_s"), start=2
    ):
        register_healthy(tmp_project, name, rows)

    # present + not pushed -> local_only.
    _set_push_status("local_only_s", "deferred")
    # missing + pushed -> cold.
    _prune_cache_entry(tmp_project, "cold_s")
    # missing + not pushed -> unreachable.
    _set_push_status("unreachable_s", "failed")
    _prune_cache_entry(tmp_project, "unreachable_s")
    return tmp_project


@workflow(purpose="Local-cache presence x push_status places a registered "
                  "sample in exactly one of healthy / local-only / cold / "
                  "unreachable, with pending and in_flight falling on the "
                  "not-pushed side and a hashless row unreachable whatever "
                  "its push status")
def test_the_reachability_table_places_every_push_status(four_quadrants):
    from wfc.registration import classify_sample

    project = four_quadrants

    healthy = classify_sample(_row("healthy_s"), project)
    assert (healthy.cached, healthy.push_status, healthy.state) == (
        True, "pushed", "healthy"
    ), healthy

    local_only = classify_sample(_row("local_only_s"), project)
    assert (local_only.cached, local_only.push_status, local_only.state) == (
        True, "deferred", "local_only"
    ), local_only

    cold = classify_sample(_row("cold_s"), project)
    assert (cold.cached, cold.push_status, cold.state) == (
        False, "pushed", "cold"
    ), cold
    assert cold.reachable, "a cold start is recoverable; the restore pulls it"

    unreachable = classify_sample(_row("unreachable_s"), project)
    assert (unreachable.cached, unreachable.push_status, unreachable.state) == (
        False, "failed", "unreachable"
    ), unreachable
    assert not unreachable.reachable

    # pending and in_flight are not incidental: a push reads the bytes out of
    # the local cache, so a row whose entry is gone before its push completed
    # can never be pushed and the archive has nothing. `pushed` is the only
    # status that makes a local miss recoverable.
    for status in ("pending", "in_flight"):
        _set_push_status("local_only_s", status)
        assert classify_sample(_row("local_only_s"), project).state == "local_only"
        _set_push_status("unreachable_s", status)
        assert classify_sample(_row("unreachable_s"), project).state == "unreachable"

    # A row with no content hash is a malformed record: content-addressed
    # storage with no address. Unreachable however its push status reads.
    _set_push_status("healthy_s", "pushed")
    hashless = _row("healthy_s")
    hashless.content_hash = None
    assert classify_sample(hashless, project).state == "unreachable"


@workflow(purpose="The pipeline-start preflight refuses only samples whose "
                  "content is nowhere wfc can reach, naming every offender "
                  "with its hash and re-registration command in one message, "
                  "and stays silent on both recoverable quadrants")
def test_the_preflight_refuses_only_unreachable_samples(four_quadrants):
    from wfc.registration import UnreachableSampleError, preflight_sample_content
    from wfc.persistence import get_session

    project = four_quadrants

    口 = Step(step_num=1, name="The recoverable quadrants are silent",
             purpose="A cold start and a local-only sample both start the "
                     "pipeline: the restore pulls the first and the second "
                     "runs here",
             critical="Reporting a recoverable state trains a user to ignore "
                      "the report, so this asserts no exception AND no "
                      "offender list")
    with get_session() as session:
        health = preflight_sample_content(
            ["healthy_s", "local_only_s", "cold_s"], session, project
        )
    assert [e.state for e in health] == ["healthy", "local_only", "cold"]

    口 = Step(step_num=2, name="An unreachable sample refuses",
             purpose="One message names the offender, its content hash and "
                     "the command that fixes it -- and names neither of the "
                     "recoverable samples beside it")
    with get_session() as session:
        with pytest.raises(UnreachableSampleError) as exc:
            preflight_sample_content(
                ["healthy_s", "cold_s", "unreachable_s"], session, project
            )
    message = str(exc.value)
    assert "unreachable_s" in message
    assert _row("unreachable_s").content_hash in message
    assert "malformed record" in message
    assert "wfc register-sample --name unreachable_s --source" in message
    assert "cold_s" not in message, message
    assert "healthy_s" not in message, message

    口 = Step(step_num=3, name="Every offender in one message",
             purpose="The refusal is the whole repair, not the first step of "
                     "it: N unreachable samples produce one message listing N "
                     "of them, not N runs each ending in one")
    _set_push_status("local_only_s", "failed")
    _prune_cache_entry(project, "local_only_s")
    with get_session() as session:
        with pytest.raises(UnreachableSampleError) as exc:
            preflight_sample_content(
                ["healthy_s", "local_only_s", "cold_s", "unreachable_s"],
                session, project,
            )
    both = str(exc.value)
    assert "local_only_s" in both and "unreachable_s" in both
    assert len(exc.value.unreachable) == 2

    口 = Step(step_num=4, name="An unregistered name is not this refusal",
             purpose="A name with no row at all is the claim's refusal (it is "
                     "cheap and needs no I/O); the preflight passes over it")
    with get_session() as session:
        assert preflight_sample_content(["never_registered"], session, project) == []


@workflow(purpose="wfc doctor reports the four sample counts, fails only on "
                  "an unreachable sample, warns on a local-only one, and "
                  "stays ok through a cold start")
def test_doctor_reports_sample_health(tmp_project, monkeypatch):
    import wfc.execution.readiness as readiness
    from wfc.execution.readiness import check_samples

    # Standalone registration; see the four_quadrants fixture.
    monkeypatch.delenv("WFC_PIPELINE_ID", raising=False)

    口 = Step(step_num=1, name="No samples registered",
             purpose="An empty registry is ok and says so, rather than being "
                     "an empty table row")
    assert check_samples(tmp_project).status == "ok"
    assert "No samples registered" in check_samples(tmp_project).message

    口 = Step(step_num=2, name="Three healthy samples",
             purpose="Registration through the production path with an "
                     "archive wired lands every row healthy")
    # Distinct content per sample, so each gets its own cache entry.
    for rows, name in enumerate(("a_s", "b_s", "c_s"), start=2):
        register_healthy(tmp_project, name, rows)
    result = check_samples(tmp_project)
    assert result.status == "ok", result
    assert "3 healthy" in result.message

    口 = Step(step_num=3, name="A cold start does not flip the gate",
             purpose="A pruned cache entry on a pushed row is counted and "
                     "reported, and is neither a warn nor a fail -- the next "
                     "run's restore pulls it",
             critical="This is the assertion that keeps the report worth "
                      "reading")
    _prune_cache_entry(tmp_project, "c_s")
    result = check_samples(tmp_project)
    assert result.status == "ok", result
    assert "2 healthy" in result.message
    assert "1 cached elsewhere only" in result.message

    口 = Step(step_num=4, name="A local-only sample warns",
             purpose="The project still runs here and is not reproducible "
                     "elsewhere: worth saying, never worth refusing over")
    _set_push_status("b_s", "deferred")
    result = check_samples(tmp_project)
    assert result.status == "warn", result
    assert "1 local-only" in result.message
    assert "b_s" in result.fix_hint
    assert "wfc register-sample --name b_s --source" in result.fix_hint

    口 = Step(step_num=5, name="An unreachable sample fails",
             purpose="Content in neither the local cache nor the archive is "
                     "the one state that fails the check, with a fix hint "
                     "naming the sample and the re-registration command")
    _set_push_status("a_s", "failed")
    _prune_cache_entry(tmp_project, "a_s")
    result = check_samples(tmp_project)
    assert result.status == "fail", result
    assert "1 unreachable" in result.message
    assert "a_s" in result.fix_hint
    assert "wfc register-sample --name a_s --source" in result.fix_hint

    口 = Step(step_num=6, name="wfc doctor prints the row and exits non-zero",
             purpose="run_all_checks carries the samples result into the "
                     "shared health table, so `wfc doctor` and `wfc init`'s "
                     "closing summary both show it",
             critical="git / dvc / docker are stubbed because they probe the "
                      "developer's machine; the samples probe is the real one")
    stub_readiness_probes(monkeypatch, git="ok", dvc="ok", docker="ok")
    monkeypatch.chdir(tmp_project)
    doctor = run_cli("doctor")
    assert doctor.returncode == 1, doctor
    assert "samples" in doctor.stdout
    assert "1 unreachable" in doctor.stdout
    assert "wfc register-sample --name a_s" in doctor.stdout
