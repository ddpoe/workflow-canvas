"""The launch's bind check against a real Docker daemon.

Case 4: a real project's check passes in one container start and writes
nothing to the project or the cache. Case 3: with the project row pointed at
an empty folder (the view a network drive gives a step) the check reports
the project bind empty, and the folder stays empty.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import requires_docker
from tests.fixtures.fakes import fake_subprocess_run, real_subprocess_run
from wfc import layout
from wfc.environments import BIND_EMPTY, BIND_VISIBLE, check_binds, host_bind_expectations

pytestmark = [pytest.mark.integration, requires_docker]


def _ref(image: str) -> str:
    return image if image.startswith("sha256:") else f"sha256:{image}"


def _tree(root: Path) -> list[tuple[str, int, int]]:
    return sorted((p.relative_to(root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
                  for p in root.rglob("*"))


def test_a_real_project_is_visible_in_one_container_start_and_nothing_is_written(
    register_fixture_methods, fixture_container_image, monkeypatch,
):
    """Case 4: the real probe sees a real project; one docker run, no write."""
    root = register_fixture_methods
    cache = layout.dvc_cache_dir(root)
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "files").mkdir(exist_ok=True)
    project_before, cache_before = _tree(root), _tree(cache)
    starts: list = []

    def record(cmd, *args, **kwargs):
        if list(cmd[:2]) == ["docker", "run"]:
            starts.append(cmd)
        return real_subprocess_run(cmd, *args, **kwargs)

    fake_subprocess_run(monkeypatch, record, only="docker")
    verdict = check_binds(_ref(fixture_container_image), root, cache,
                          host_bind_expectations(root, cache))
    assert verdict.outcome == BIND_VISIBLE, verdict
    assert len(starts) == 1
    assert _tree(root) == project_before
    assert _tree(cache) == cache_before


def test_the_project_row_on_an_empty_folder_is_reported_empty(
    register_fixture_methods, fixture_container_image, tmp_path,
):
    """Case 3: the project row on an empty folder is empty, and stays empty."""
    root = register_fixture_methods
    cache = layout.dvc_cache_dir(root)
    cache.mkdir(parents=True, exist_ok=True)
    empty = tmp_path / "share-view"
    empty.mkdir()
    verdict = check_binds(_ref(fixture_container_image), empty, cache,
                          host_bind_expectations(root, cache))
    assert verdict.outcome == BIND_EMPTY
    assert (verdict.container, verdict.host) == ("/work", empty.resolve().as_posix())
    assert list(empty.iterdir()) == []
