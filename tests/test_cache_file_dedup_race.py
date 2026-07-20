"""Tier 1: cache_file dedups when a concurrent writer already landed dest.

Deterministic replays of the parallel-jobs race observed on Windows: several
Snakemake jobs store the identical content-addressed blob (shared env), the
winner lands a read-only dest, and the loser's rename/replace then fails.
Content-addressed principle under test: any failure path where dest exists
is a success — return dest, never raise, never touch the winner's file.
"""
from __future__ import annotations

import os
import pathlib
from pathlib import Path


def _land_winner(dest: Path, content: bytes) -> None:
    """Simulate the winning writer: dest appears, read-only, mid-race."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    os.chmod(dest, 0o444)


def test_move_mode_dedups_when_rename_loses_race(tmp_path, monkeypatch):
    """Loser's os.rename hits a just-created read-only dest -> dedup.

    The winner lands dest between cache_file's entry exists-check and the
    rename (so the entry-dedup cannot fire), and the rename raises the
    Windows-style PermissionError. Expected: no raise, dest returned
    intact, staging source consumed.
    """
    from wfc.provenance import cache_file, hash_file

    content = b"shared env blob"
    src = tmp_path / "staging.blob"
    src.write_bytes(content)
    md5 = hash_file(src)

    def racing_rename(s, d):
        _land_winner(Path(d), content)
        raise PermissionError(5, "Access is denied", str(s))

    monkeypatch.setattr(os, "rename", racing_rename)

    dest = cache_file(src, md5, tmp_path, move=True)

    expected = (
        tmp_path.resolve() / ".dvc" / "cache" / "files" / "md5" / md5[:2] / md5[2:]
    )
    assert dest == expected
    assert dest.read_bytes() == content
    assert not src.exists()


def test_copy_mode_dedups_when_replace_loses_race(tmp_path, monkeypatch):
    """Loser's tmp.replace(dest) hits the winner's read-only dest -> dedup.

    Copy mode (move=False): expected no raise, dest returned intact, the
    user-owned source preserved, and the process-unique staging tmp
    cleaned up.
    """
    from wfc.provenance import cache_file, hash_file

    content = b"shared env blob"
    src = tmp_path / "user_owned.blob"
    src.write_bytes(content)
    md5 = hash_file(src)

    def racing_replace(self, target):
        _land_winner(Path(target), content)
        raise PermissionError(5, "Access is denied", str(self))

    monkeypatch.setattr(pathlib.Path, "replace", racing_replace)

    dest = cache_file(src, md5, tmp_path, move=False)

    assert dest.read_bytes() == content
    assert src.exists()
    assert not list(dest.parent.glob("*.tmp"))
