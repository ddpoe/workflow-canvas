"""The disk row: a project on a network drive is named before a run.

Steps run in containers, and Docker Desktop's containers see a folder on a
Windows network drive as empty. The ``disk`` row of ``run_all_checks``
(``wfc init``'s summary and ``wfc doctor``) warns when the project's root is
on one -- its drive answers ``DRIVE_REMOTE``, or the root is a UNC path --
and says to move the project to a local disk, keeping the share for the
archive. It never fails and never stops ``wfc init``. Every other root, and
every root on a host that is not Windows, is ``ok``.

The host and the drive-type answer are faked (``stub_drive_type``); the
row's reading of the path is real.
"""

from __future__ import annotations

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.conftest import project_archive_dir
from tests.fixtures.fakes import stub_readiness_probes
from tests.fixtures.fakes.drive_type import stub_drive_type
from wfc.execution.readiness import check_disk


def _assert_network_warning(row) -> None:
    """The warn row names the network drive, the empty folder and both fixes."""
    assert row.name == "disk"
    assert row.status == "warn", row
    assert "network drive" in row.message
    assert "empty folder" in row.message
    assert "local disk" in row.fix_hint
    assert "wfc init --archive <share path>" in row.fix_hint
    assert "docker" not in f"{row.message} {row.fix_hint}".lower()


@pytest.mark.parametrize(("root", "answer"), [
    (r"Z:\projects\plates", "remote"),
    (r"\\labserver\share\projects\plates", "fixed"),
    (r"\\wsl.localhost\Ubuntu\home\me\plates", "fixed"),
    (r"\\?\UNC\labserver\share\plates", "fixed"),
    (r"\\.\UNC\labserver\share\plates", "fixed"),
    ("//labserver/share/plates", "fixed"),
], ids=["remote-drive-letter", "unc", "wsl-share", "extended-unc", "device-unc",
        "forward-slash-unc"])
def test_a_root_on_a_network_drive_warns(monkeypatch, root, answer):
    """A remote drive, or a UNC root whatever its drive answers, warns.

    The warning names a local disk and --archive.
    """
    stub_drive_type(monkeypatch, answer)
    row = check_disk(root)
    _assert_network_warning(row)
    assert root in row.message


@pytest.mark.parametrize("root", [
    r"C:\Users\me\projects\plates",
    r"\\?\C:\Users\me\projects\plates",
], ids=["local-disk", "extended-length-local"])
def test_a_root_on_a_local_disk_is_ok(monkeypatch, root):
    """A local drive, also in its extended-length form, is ok."""
    stub_drive_type(monkeypatch, "fixed")
    row = check_disk(root)
    assert (row.name, row.status) == ("disk", "ok"), row
    assert "local disk" in row.message


def test_a_root_that_looks_local_but_resolves_to_a_share_is_named_resolved(monkeypatch):
    """A local-looking root (a link, a relative path) resolving to a share is network.

    The root as given is read first; only a root that looks local is
    resolved, and the share it resolves to is the spelling named. The
    resolution itself is handed in: CI has no share to resolve to.
    """
    from wfc.execution.readiness import _network_root

    stub_drive_type(monkeypatch, "fixed")
    given, share = r"C:\Users\me\plates", r"\\labserver\share\plates"
    assert _network_root(given, lambda: share) == share
    assert _network_root(given, lambda: r"D:\real\plates") is None
    assert _network_root(given, lambda: given) is None

    def never_resolved() -> str:
        raise AssertionError("a network root as given is never resolved")
    assert _network_root(share, never_resolved) == share


def test_a_host_that_is_not_windows_is_ok_and_never_asks(monkeypatch, tmp_path):
    """Off Windows the row is ok and the Windows drive query is never made."""
    stub_drive_type(monkeypatch, None, windows=False)
    row = check_disk(tmp_path)
    assert (row.name, row.status) == ("disk", "ok"), row
    assert str(tmp_path) in row.message


def test_the_real_drive_query_reads_this_disk_as_local(tmp_path):
    """Unfaked: the test machine's temp directory is not on a network drive.

    On Windows this asks GetDriveTypeW; elsewhere the query is skipped.
    """
    row = check_disk(tmp_path)
    assert (row.name, row.status) == ("disk", "ok"), row


@workflow(purpose="wfc init on a project root whose drive answers as a "
                  "network drive still creates the project and exits 0; its "
                  "health summary prints the disk row as a warning naming "
                  "a local disk and wfc init --archive, and wfc doctor prints "
                  "the same row and still exits 0")
def test_init_and_doctor_warn_about_a_project_on_a_network_drive(
        tmp_path, monkeypatch, capsys):
    """Init and doctor on a faked network drive: created, warned, exit 0."""
    from wfc import layout
    from wfc.cli import cli_main

    root = tmp_path / "plates"

    口 = Step(step_num=1, name="Fake a network drive",
             purpose="The host is Windows and the project's drive answers "
                     "DRIVE_REMOTE; the other probes report ok so the "
                     "summary does not wait on the Docker daemon")
    stub_drive_type(monkeypatch, "remote")
    stub_readiness_probes(monkeypatch, git="ok", dvc="ok", docker="ok",
                          samples="ok")

    口 = Step(step_num=2, name="wfc init creates the project",
             purpose="The warning does not refuse init: the project is "
                     "scaffolded and the command exits 0")
    rc = cli_main(["init", "--dir", str(root),
                   "--archive", str(project_archive_dir(root)), "--yes"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert layout.marker_path(root).exists()
    assert layout.db_path(root).exists()

    口 = Step(step_num=3, name="The summary prints the warning",
             purpose="The disk row is WARN and its fix names a local "
                     "disk and wfc init --archive")
    row = _printed_row(out, "disk")
    assert "[WARN]" in row and "network drive" in row, out
    assert "local disk" in row and "wfc init --archive <share path>" in row

    口 = Step(step_num=4, name="wfc doctor prints it and exits 0",
             purpose="A warning does not flip doctor's exit code")
    monkeypatch.chdir(root)
    rc = cli_main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0, out
    row = _printed_row(out, "disk")
    assert "[WARN]" in row and "network drive" in row, out
    assert "wfc init --archive <share path>" in row


def _printed_row(out: str, name: str) -> str:
    """The health-table row named *name* and its fix-hint line, as printed."""
    lines = out.splitlines()
    at = next(i for i, line in enumerate(lines)
              if line.startswith("  [") and line[9:].split()[:1] == [name])
    rest = lines[at + 1:at + 2]
    hint = rest if rest and "->" in rest[0] else []
    return "\n".join([lines[at], *hint])
