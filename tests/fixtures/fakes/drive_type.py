"""Drive-type boundary: what Windows says a project's drive is.

The disk row of ``wfc doctor`` and ``wfc init``'s summary asks Windows
(``GetDriveTypeW``) whether the project's drive is a network drive. CI has
no network share and most hosts are not Windows, so a test that is about the
row fakes the host and the answer; the row's own reading of the path (UNC
and extended-length forms, the root as given and resolved) stays real.
"""

from __future__ import annotations

from ._entry import fake

#: The ``GetDriveTypeW`` answers a test can name.
DRIVE_ANSWERS = {"unknown": 0, "no-root": 1, "removable": 2, "fixed": 3,
                 "remote": 4, "cdrom": 5, "ramdisk": 6}


@fake(
    boundary="wfc.execution.readiness._on_windows / _drive_type -- whether "
             "the host is Windows, and what GetDriveTypeW answers for the "
             "drive of a project path",
    preserves="the disk row's own reading of the path: a UNC or "
              "extended-length form, the root as given and as resolved, and "
              "its warn-never-fail rule; the query is never reached off "
              "Windows",
    not_proven="that a real mapped share or SMB drive answers DRIVE_REMOTE "
               "(CI has no share; the manual network-drive smoke run covers it)",
    backed_by="pm_mvp::tests.test_project_location_row::"
              "test_the_real_drive_query_reads_this_disk_as_local",
)
def stub_drive_type(monkeypatch, answer: str | None, *,
                    windows: bool = True) -> None:
    """Pin the host and the drive-type answer the disk row reads.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        answer: The ``GetDriveTypeW`` answer for every path, by name (a key
            of :data:`DRIVE_ANSWERS`: ``"fixed"``, ``"remote"`` ...), or
            ``None`` for a query that must not be asked: it raises.
        windows: Whether the host is Windows.
    """
    from wfc.execution import readiness

    def drive_type(path: str) -> int:
        if answer is None:
            raise AssertionError(f"the drive type must not be asked ({path})")
        return DRIVE_ANSWERS[answer]

    monkeypatch.setattr(readiness, "_on_windows", lambda: windows)
    monkeypatch.setattr(readiness, "_drive_type", drive_type)
