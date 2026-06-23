"""Tier 2 + Tier 1 tests for ``wfc jupyter`` port autopick (ADR-019 Cycle E).

Covers US-2: when 8888 is occupied the command picks the next free port,
forwards it as ``-p <resolved>:8888``, and **never** chooses port 8000
even when bind would succeed (Dante's uniFLOW conflict).
"""
from __future__ import annotations

import json
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from dflow.core.decorators import workflow


VALID_DIGEST = "a" * 64
CONTAINER_REF_DOCKER = (
    f"docker://ghcr.io/dante/image-io@sha256:{VALID_DIGEST}"
)
CONTAINER_REF_BARE = (
    f"ghcr.io/dante/image-io@sha256:{VALID_DIGEST}"
)


def _setup_project(tmp_path: Path) -> Path:
    (tmp_path / ".wfc").mkdir()
    (tmp_path / ".wfc" / "wf-canvas.toml").write_text(
        '[project]\nname="t"\n[database]\nurl="sqlite:///:memory:"\n'
    )
    (tmp_path / ".wfc" / "envs.json").write_text(json.dumps({
        "schema_version": 1,
        "envs": {
            "image-io": {
                "backend": "pixi",
                "source": "pixi.toml",
                "container": CONTAINER_REF_DOCKER,
                "env_fingerprint": "f" * 64,
                "built_from_lock": "pixi.lock",
                "built_at": "2026-05-17T00:00:00Z",
            }
        },
    }))
    return tmp_path


@workflow(purpose="When port 8888 is occupied, wfc jupyter picks a different "
                  "free port, skips 8000 unconditionally, and forwards the "
                  "resolved port via `-p <resolved>:8888` (US-2)")
def test_jupyter_port_autopicks_when_default_occupied(tmp_path, monkeypatch):
    proj = _setup_project(tmp_path)
    monkeypatch.chdir(proj)

    # Hold 8888 with a real socket bind so the helper sees it as occupied.
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        try:
            holder.bind(("127.0.0.1", 8888))
        except OSError:
            pytest.skip("Cannot bind 127.0.0.1:8888 on this machine "
                        "(some other process already holds it).")

        captured_argv: list[list[str]] = []

        class _FakeResult:
            returncode = 0

        def _fake_run(argv, check=False):  # noqa: ARG001
            captured_argv.append(list(argv))
            return _FakeResult()

        with patch("wfc.dev_loop.subprocess.run", side_effect=_fake_run):
            from wfc import dev_loop
            rc = dev_loop.jupyter("image-io")

        assert rc == 0
        assert captured_argv, "subprocess.run was not invoked"
        argv = captured_argv[0]

        # The -p flag must be present and map <resolved>:8888.
        assert "-p" in argv, f"missing -p flag in argv: {argv}"
        p_idx = argv.index("-p")
        mapping = argv[p_idx + 1]
        assert mapping.endswith(":8888"), (
            f"port mapping must forward to container 8888, got {mapping}"
        )
        resolved = int(mapping.split(":")[0])

        # The resolved host port must NOT be 8888 (occupied) and MUST NOT
        # be 8000 (hard-skipped). It must fall in the autopick range.
        assert resolved != 8888, "autopick returned the occupied port"
        assert resolved != 8000, "autopick must skip port 8000 unconditionally"
        assert 8888 <= resolved <= 8999, (
            f"resolved port {resolved} outside autopick range 8888-8999"
        )
    finally:
        holder.close()


def test_autopick_skips_8000_even_if_search_starts_there():
    """Tier 1 unit test: hard-skip must hold even when start_port=8000."""
    from wfc.dev_loop import _autopick_port
    port = _autopick_port(start_port=8000, max_port=8005)
    assert port != 8000
    assert 8001 <= port <= 8005


def test_autopick_raises_when_range_exhausted(monkeypatch):
    """Tier 1: when every port in the range is unavailable, raise cleanly."""
    from wfc import dev_loop

    class _AlwaysBusy:
        def __init__(self, *args, **kwargs):
            pass

        def bind(self, addr):
            raise OSError("simulated busy")

        def close(self):
            pass

    monkeypatch.setattr(dev_loop.socket, "socket", _AlwaysBusy)
    with pytest.raises(dev_loop._DevLoopError) as exc_info:
        dev_loop._autopick_port(start_port=8888, max_port=8890)
    assert "no free port" in exc_info.value.message
