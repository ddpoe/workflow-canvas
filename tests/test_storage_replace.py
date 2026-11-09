"""Replacing a shared file while other processes read it (Storage's replace).

A file wfc rewrites while other processes read it -- a draft's
draft-run.json during a parallel draft-mode pipeline, a code copy's stamp
while parallel steps of one method version verify it -- is replaced whole,
and the replace succeeds although readers hold the file open.
"""

from __future__ import annotations

import multiprocessing
import os
import threading
import time
from pathlib import Path

import pytest
from axiom_annotations import workflow

_CTX = multiprocessing.get_context("spawn")


def _read_until_stopped(path: str, reading, stop) -> None:
    """A reader that polls *path* about a thousand times a second until stopped.

    A stress bound: wfc's own readers (a canvas poll, a step reading its
    draft's record) pause far longer between reads. A reader that never
    closes the file at all would refuse every replace whatever the retry,
    and wfc has none.
    """
    target = Path(path)
    reading.release()
    while not stop.is_set():
        try:
            target.read_bytes()
        except OSError:
            pass
        time.sleep(0.001)


class _Readers:
    """Reader processes over one file, started and stopped as a context."""

    def __init__(self, path: Path, count: int = 2) -> None:
        self.stop, self.reading = _CTX.Event(), _CTX.Semaphore(0)
        self.procs = [_CTX.Process(target=_read_until_stopped,
                                   args=(str(path), self.reading, self.stop), daemon=True)
                      for _ in range(count)]

    def __enter__(self) -> _Readers:
        for proc in self.procs:
            proc.start()
        for _ in self.procs:
            assert self.reading.acquire(timeout=60), "a reader never started"
        return self

    def __exit__(self, *exc) -> None:
        self.stop.set()
        for proc in self.procs:
            proc.join(timeout=30)
            if proc.is_alive():
                proc.terminate()


@workflow(purpose="Replacing a shared file whole succeeds every time while two "
                  "other processes keep reading it")
def test_a_replace_succeeds_while_other_processes_read_the_file(tmp_path):
    """Every replace lands, none refused, while two processes keep reading."""
    from wfc.storage import write_file

    target = tmp_path / "shared.json"
    target.write_bytes(b"0")
    readers = _Readers(target)
    try:
        with readers:
            for i in range(1, 301):
                write_file(target, str(i).encode(), temporary=tmp_path / f".shared.{i}.tmp")
    finally:
        for proc in readers.procs:
            if proc.is_alive():
                proc.terminate()

    assert target.read_bytes() == b"300"
    assert [p.name for p in tmp_path.iterdir()] == ["shared.json"]


@workflow(purpose="A file another handle holds for about a second, then "
                  "releases, is still replaced: a hold that long is waited out")
def test_a_file_held_for_a_second_is_still_replaced(tmp_path):
    """Held open for about a second, released: the replace lands, no temp file left."""
    from wfc.storage import write_file

    target = tmp_path / "shared.json"
    target.write_bytes(b"before")
    holder = open(target, "rb")  # noqa: SIM115 -- closed by the timer
    release = threading.Timer(1.0, holder.close)
    release.start()
    try:
        write_file(target, b"after", temporary=tmp_path / ".shared.tmp")
    finally:
        release.join()
        holder.close()

    assert target.read_bytes() == b"after"
    assert [p.name for p in tmp_path.iterdir()] == ["shared.json"]


@pytest.mark.skipif(os.name != "nt", reason="only Windows refuses to replace "
                                             "a file another handle holds open")
@workflow(purpose="A file another handle holds past every retry: the replace "
                  "raises PermissionError, the old bytes stay and no temporary "
                  "file is left")
def test_a_file_held_past_every_try_raises_and_keeps_its_bytes(tmp_path):
    """A handle held throughout: PermissionError, the old bytes kept, no temp file left."""
    from wfc.storage import write_file

    target = tmp_path / "shared.json"
    target.write_bytes(b"before")
    with open(target, "rb"), pytest.raises(PermissionError):
        write_file(target, b"after", temporary=tmp_path / ".shared.tmp")

    assert target.read_bytes() == b"before"
    assert [p.name for p in tmp_path.iterdir()] == ["shared.json"]
