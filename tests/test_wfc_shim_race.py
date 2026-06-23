"""_ensure_wfc_shim tolerates concurrent-writer races.

When two Snakemake workers race to build the shim on a UNC/SMB share,
the atomic ``os.replace`` can fail with PermissionError/WinError 5 if the
other worker still has the destination open. The shim content is
deterministic per (version, path_key), so a loser's rename failure is
benign iff the destination exists with the correct bytes afterward.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from wfc.cli import _ensure_wfc_shim


def test_shim_tolerates_replace_permission_error(tmp_path, monkeypatch):
    """A losing writer survives PermissionError when dest is already written.

    Simulates the SMB race: our ``os.replace`` fails, but a sibling writer
    has already laid down ``__init__.py`` at the dest with the correct
    content. ``_ensure_wfc_shim`` must swallow the error and return cleanly.
    """
    monkeypatch.setenv("WFC_SHIM_CACHE_DIR", str(tmp_path))

    # os.replace that behaves like a losing SMB rename: the sibling writer
    # has already produced the dest (we create it here to mimic that), and
    # our attempt fails with PermissionError.
    def flaky_replace(src, dst):
        # Simulate the sibling having already placed the file.
        Path(dst).write_text("sibling-produced", encoding="utf-8")
        raise PermissionError(5, "Access is denied")

    with patch("wfc.cli.os.replace", side_effect=flaky_replace):
        result = _ensure_wfc_shim()

    # Function returns normally; dest file exists (written by the "sibling").
    init_py = result / "wfc" / "__init__.py"
    assert init_py.exists()
    assert init_py.read_text() == "sibling-produced"
    # No orphaned tmp files left behind.
    tmps = list((result / "wfc").glob("__init__.py.*.tmp"))
    assert tmps == []


def test_shim_reraises_when_dest_missing(tmp_path, monkeypatch):
    """If the rename failed AND dest doesn't exist, re-raise — that's a real error."""
    monkeypatch.setenv("WFC_SHIM_CACHE_DIR", str(tmp_path))

    with patch("wfc.cli.os.replace", side_effect=PermissionError(5, "Access is denied")):
        with pytest.raises(PermissionError):
            _ensure_wfc_shim()


def test_shim_happy_path_writes_content(tmp_path, monkeypatch):
    """Plain first-writer wins: file lands at the expected path."""
    monkeypatch.setenv("WFC_SHIM_CACHE_DIR", str(tmp_path))
    shim_root = _ensure_wfc_shim()
    init_py = shim_root / "wfc" / "__init__.py"
    assert init_py.exists()
    text = init_py.read_text()
    assert "__path__" in text
    # tmp files shouldn't linger — we clean up on both success and race.
    tmps = list((shim_root / "wfc").glob("__init__.py.*.tmp"))
    assert tmps == []
