"""The preview cache: a converted PNG is kept on disk, within cache_mb, and wfc cache prune empties it.

Tier 2 entered at the image route over a real cache (catalog case 5, its
second half): ``spy_image_conversions`` counts the conversions, so a request
answered from the cache is seen not to convert. Tier 3 for catalog case 9:
the route fills the cache past ``[previews] cache_mb``, and ``wfc cache
prune`` runs in-process through the CLI, as the other prune tests drive it.
Tier 1 tests enter :class:`~wfc.canvas.previews.PreviewCache` directly for
the edges a route cannot set up (a cap smaller than one PNG, a file another
reader holds open).

Every image is generated small: the largest is 700 x 700 RGB noise, about
1.5 MB as a PNG (noise does not compress, so each preview's size is known).
"""

from __future__ import annotations

import random
import sys

from axiom_annotations import Step, workflow
from PIL import Image

from tests.fixtures.fakes import spy_image_conversions
from tests.test_artifact_image_previews import _encode, _set_previews, image_run  # noqa: F401
from wfc import layout
from wfc.canvas.previews import PreviewCache

MIB = 1024 * 1024


def _noise(seed: int, side: int = 700) -> bytes:
    """A ``side`` x ``side`` RGB PNG of noise: its preview is about ``side * side * 3`` bytes."""
    pixels = random.Random(seed).randbytes(side * side * 3)
    return _encode(Image.frombytes("RGB", (side, side), pixels), "PNG")


def _image(client, run_id, name, size="view") -> bytes:
    resp = client.get(f"/api/wfc/run/{run_id}/image/out/{name}", params={"size": size})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "image/png"
    return resp.content


def _cached(root) -> list[bytes]:
    """The PNGs the preview cache holds now."""
    folder = layout.preview_cache_dir(root)
    return [p.read_bytes() for p in folder.glob("*.png")] if folder.is_dir() else []


def _cached_names(root, served: dict[str, bytes]) -> list[str]:
    """Which of the ``served`` previews (name to the PNG the route answered) the cache holds now."""
    held = _cached(root)
    return sorted(name for name, png in served.items() if png in held)


@workflow(purpose="A second request for the same image and size is answered from the preview "
                  "cache with the same PNG and no conversion; another size of it is converted "
                  "once more, and both stay cached")
def test_a_second_request_is_served_from_the_cache_without_converting(
        canvas_client, image_run, tmp_project, monkeypatch):  # noqa: F811
    """The same image and size twice converts once; another size converts again."""
    conversions = spy_image_conversions(monkeypatch)
    run_id = image_run({"cells.tif": _encode(Image.new("L", (600, 400), 90), "TIFF")})

    first = _image(canvas_client, run_id, "cells.tif")
    again = _image(canvas_client, run_id, "cells.tif")
    assert again == first
    assert conversions.started == ["out/cells.tif"]

    thumb = _image(canvas_client, run_id, "cells.tif", "thumb")
    assert thumb != first
    assert conversions.started == ["out/cells.tif"] * 2
    assert _cached_names(tmp_project, {"view": first, "thumb": thumb}) == ["thumb", "view"]
    assert len(_cached(tmp_project)) == 2


@workflow(purpose="Filling the preview cache past cache_mb keeps it under the cap by dropping "
                  "the least recently used preview first (a cache hit counts as a use); "
                  "wfc cache prune lists the cache in its dry run, then empties it")
def test_the_cache_stays_under_cache_mb_and_wfc_cache_prune_empties_it(
        canvas_client, image_run, tmp_project, cli, monkeypatch):  # noqa: F811
    """Past cache_mb the least recently used preview goes first; wfc cache prune empties the cache."""
    口 = Step(step_num=1, name="Archive three 1.5 MB previews' worth, cache_mb = 4",
             purpose="A run whose folder output holds three noise images, each previewed as "
                     "a PNG of about 1.5 MB: two fit under 4 MB, three do not")
    conversions = spy_image_conversions(monkeypatch)
    run_id = image_run({"a.png": _noise(1), "b.png": _noise(2), "c.png": _noise(3)})
    _set_previews(tmp_project, "cache_mb = 4\n")

    口 = Step(step_num=2, name="Fill the cache with a and b",
             purpose="Both previews are converted and kept")
    a = _image(canvas_client, run_id, "a.png")
    b = _image(canvas_client, run_id, "b.png")
    assert 2 * len(a) < 4 * MIB < 3 * len(a)
    assert _cached_names(tmp_project, {"a": a, "b": b}) == ["a", "b"]

    口 = Step(step_num=3, name="Use a again",
             purpose="A hit: no conversion, and a becomes the most recently used")
    assert _image(canvas_client, run_id, "a.png") == a
    assert len(conversions.started) == 2

    口 = Step(step_num=4, name="Add c past the cap",
             purpose="The cache drops b, the least recently used, and stays under cache_mb")
    c = _image(canvas_client, run_id, "c.png")
    assert _cached_names(tmp_project, {"a": a, "b": b, "c": c}) == ["a", "c"]
    assert len(_cached(tmp_project)) == 2
    assert sum(len(png) for png in _cached(tmp_project)) <= 4 * MIB
    assert _image(canvas_client, run_id, "b.png") == b
    assert len(conversions.started) == 4

    口 = Step(step_num=5, name="wfc cache prune empties the cache",
             purpose="The dry run lists the preview cache and keeps it; the prune empties it, "
                     "and a second prune has nothing left to do")
    dry = cli("cache", "prune", "--dry-run")
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert str(layout.preview_cache_dir(tmp_project)) in dry.stdout
    assert len(_cached(tmp_project)) == 2

    pruned = cli("cache", "prune", "--force")
    assert pruned.returncode == 0, pruned.stdout + pruned.stderr
    assert _cached(tmp_project) == []
    assert "Nothing to prune." in cli("cache", "prune", "--force").stdout


def test_a_cap_smaller_than_one_png_keeps_nothing_and_a_missing_folder_is_made(tmp_path):
    """A PNG over the whole cap is not kept; the cache folder is made when the first preview is."""
    folder = tmp_path / "state" / "previews"
    cache = PreviewCache(folder, cap_mb=1)

    cache.put("hash-a", "view", b"x" * (2 * MIB))
    assert cache.get("hash-a", "view") is None

    cache.put("hash-b", "view", b"small png")
    assert cache.get("hash-b", "view") == b"small png"
    assert cache.get("hash-b", "thumb") is None
    assert [p.suffix for p in folder.iterdir()] == [".png"]


def test_eviction_skips_a_preview_it_cannot_remove(tmp_path):
    """On Windows a PNG being read cannot be deleted: eviction skips it and removes others."""
    cache = PreviewCache(tmp_path, cap_mb=1)
    chunk = b"x" * (600 * 1024)
    cache.put("oldest", "view", chunk)
    with cache.path_for("oldest", "view").open("rb"):
        cache.put("middle", "view", chunk)
        cache.put("newest", "view", chunk)
        kept = sorted(p.name for p in tmp_path.glob("*.png"))
    assert sum(p.stat().st_size for p in tmp_path.glob("*.png")) <= 1 * MIB
    assert len(kept) == 1
    if sys.platform == "win32":
        assert cache.get("oldest", "view") == chunk
    else:
        assert cache.get("newest", "view") == chunk
