"""Two conversions at once, each within half of memory_mb, in arrival order.

Tier 2 entered at the image route over a real cache (catalog case 7). The
requests overlap through an ``httpx.AsyncClient`` over the app in one event
loop, as a browser's would; ``spy_image_conversions`` counts the conversions
in flight and the order they start in, and holds them before they decode so
the two conversion threads are seen busy. A request waiting for one of them
sits in the conversion pool's work queue, read as ``_queued()``.

Every image is tiny (the largest takes 640 KB to decode).
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from axiom_annotations import workflow
from PIL import Image

from tests.fixtures.fakes import spy_image_conversions
from tests.test_artifact_image_previews import _encode, _set_previews, image_run  # noqa: F401
from wfc import layout
from wfc.canvas import previews, server


def _rgb(side: int) -> bytes:
    """A ``side`` x ``side`` RGB PNG: ``side * side * 4`` bytes decoded."""
    return _encode(Image.new("RGB", (side, side), (side % 256, 90, 160)), "PNG")


def _queued() -> int:
    """The conversions waiting for one of the two conversion threads."""
    return previews._CONVERT_POOL._work_queue.qsize()


@pytest.fixture
def conversions(monkeypatch):
    """The spy, with the conversion pool idle: every request a test makes is answered before it ends."""
    assert _queued() == 0
    log = spy_image_conversions(monkeypatch)
    yield log
    log.release()


def _finished_early(pending) -> str | None:
    """Name a request that already answered or raised while the test still waits on it."""
    for request in pending:
        if not request.done():
            continue
        if request.exception() is not None:
            return f"a request raised {request.exception()!r}"
        answer = request.result()
        return f"a request answered {answer.status_code}: {answer.text[:300]}"
    return None


async def _until(condition, what, pending=()) -> None:
    """Wait, letting the requests run, until ``condition()`` holds; ``what()`` names it on a timeout.

    Every request in ``pending`` is held at a conversion or waiting for one,
    so one that has already answered or raised fails the wait at once,
    naming what it got. The deadline is a liveness bound: it only has to
    catch a hang.
    """
    deadline = time.monotonic() + 60
    while not condition():
        early = _finished_early(pending)
        if early is not None:
            raise AssertionError(f"while waiting for {what()}: {early}")
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what()}")
        await asyncio.sleep(0.01)


async def _settle(log, running: int, queued: int, pending=()) -> None:
    """Wait until ``running`` conversions are in flight and ``queued`` more wait for a thread."""
    await _until(lambda: (log.running, _queued()) == (running, queued),
                 lambda: f"{running} converting and {queued} queued "
                         f"(now {log.running} and {_queued()})",
                 pending)


def _started(log) -> list[str]:
    """The file names converted, in the order they started (the run's folder output is ``out``)."""
    return [name.removeprefix("out/") for name in log.started]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                             base_url="http://canvas")


def _image(client, run_id, name, size="view"):
    return asyncio.ensure_future(
        client.get(f"/api/wfc/run/{run_id}/image/out/{name}", params={"size": size}))


@workflow(purpose="Five image requests at once: two conversions run and the other three "
                  "wait for a conversion thread; all five answer once they are let go, and "
                  "never more than two were in flight")
def test_no_more_than_two_conversions_run_at_once(canvas_client, image_run, conversions):
    names = ["a.png", "b.png", "c.png", "d.png", "e.png"]
    run_id = image_run({name: _rgb(100 + i) for i, name in enumerate(names)})

    async def scenario():
        async with _client() as client:
            conversions.hold()
            pending = [_image(client, run_id, name) for name in names]
            await _settle(conversions, running=2, queued=3, pending=pending)
            conversions.release()
            return await asyncio.gather(*pending)

    answers = asyncio.run(scenario())
    assert [a.status_code for a in answers] == [200] * 5
    assert conversions.peak_running == 2
    assert sorted(_started(conversions)) == names


@workflow(purpose="An image whose conversion needs more than half of memory_mb is refused "
                  "from its header with a 422 naming the smallest memory_mb that admits it "
                  "and where to set it; set to that value, the same image converts")
def test_an_image_over_half_of_memory_mb_is_refused_naming_the_memory_mb_it_needs(
        canvas_client, image_run, tmp_project, conversions):
    run_id = image_run({"big.png": _rgb(400)})
    info = canvas_client.get(f"/api/wfc/run/{run_id}/image-info/out/big.png").json()
    needed = info["memory_mb_needed"]
    assert info["refused"] is None
    assert info["memory_bytes"] > info["decoded_bytes"] == 400 * 400 * 4
    assert needed * 1024 * 1024 >= 2 * info["memory_bytes"] > (needed - 1) * 1024 * 1024

    marker = layout.marker_path(tmp_project)
    before = marker.read_text(encoding="utf-8")
    _set_previews(tmp_project, f"memory_mb = {needed - 1}\n")
    refused = canvas_client.get(f"/api/wfc/run/{run_id}/image/out/big.png")
    assert refused.status_code == 422, refused.text
    body = refused.json()
    assert (body["width"], body["height"], body["memory_mb_needed"]) == (400, 400, needed)
    assert f"memory_mb = {needed - 1}" in body["detail"]
    assert f"set [previews] memory_mb to at least {needed} in wf-canvas.toml" in body["detail"]
    assert "download the file" in body["detail"]
    assert conversions.started == []

    marker.write_text(before, encoding="utf-8")
    _set_previews(tmp_project, f"memory_mb = {needed}\n")
    converted = canvas_client.get(f"/api/wfc/run/{run_id}/image/out/big.png")
    assert converted.status_code == 200, converted.text
    assert _started(conversions) == ["big.png"]


@workflow(purpose="Requests that wait for a conversion thread start in the order they "
                  "arrived, a large image included: none is passed by later small ones")
def test_waiting_conversions_start_in_arrival_order(canvas_client, image_run, conversions):
    arrivals = ["large.png", "s1.png", "s2.png", "s3.png"]
    run_id = image_run({"first.png": _rgb(100), "second.png": _rgb(101),
                        "large.png": _rgb(400), "s1.png": _rgb(60),
                        "s2.png": _rgb(61), "s3.png": _rgb(62)})

    async def scenario():
        async with _client() as client:
            conversions.hold()
            pending = [_image(client, run_id, "first.png")]
            await _settle(conversions, running=1, queued=0, pending=pending)
            pending.append(_image(client, run_id, "second.png"))
            await _settle(conversions, running=2, queued=0, pending=pending)
            for i, name in enumerate(arrivals, start=1):
                pending.append(_image(client, run_id, name))
                await _settle(conversions, running=2, queued=i, pending=pending)
            # One held conversion at a time goes on: only one thread is ever free
            # to take the next waiting request, so the start order is the queue's.
            for started in range(3, 3 + len(arrivals)):
                conversions.let(1)
                await _until(lambda n=started: len(conversions.started) == n,
                             lambda n=started: f"conversion {n} to start")
            conversions.release()
            return await asyncio.gather(*pending)

    answers = asyncio.run(scenario())
    assert [a.status_code for a in answers] == [200] * 6
    assert _started(conversions)[:2] == ["first.png", "second.png"]
    assert _started(conversions)[2:] == arrivals


@workflow(purpose="While two conversions run and dozens of thumbnail requests wait for "
                  "them, more than the server's shared worker threads, a plain route (the "
                  "artifact listing, image-info) still answers")
def test_other_routes_answer_while_two_conversions_run(canvas_client, image_run, conversions):
    run_id = image_run({"thumb.png": _rgb(100)})
    queued = 50  # past anyio's 40 shared worker threads

    async def scenario():
        async with _client() as client:
            conversions.hold()
            pending = [_image(client, run_id, "thumb.png", "thumb") for _ in range(queued)]
            await _settle(conversions, running=2, queued=queued - 2, pending=pending)
            listing = await asyncio.wait_for(
                client.get(f"/api/wfc/run/{run_id}/artifacts"), timeout=10)
            info = await asyncio.wait_for(
                client.get(f"/api/wfc/run/{run_id}/image-info/out/thumb.png"), timeout=10)
            conversions.release()
            return listing, info, await asyncio.gather(*pending)

    listing, info, thumbs = asyncio.run(scenario())
    assert listing.status_code == 200 and info.status_code == 200
    assert [t.status_code for t in thumbs] == [200] * queued
    assert conversions.peak_running <= 2


@workflow(purpose="A preview already in the disk cache answers while both conversion "
                  "threads are busy and others wait: a cache hit never queues")
def test_a_cached_preview_never_waits_for_a_conversion(canvas_client, image_run, conversions):
    run_id = image_run({"seen.png": _rgb(100), "a.png": _rgb(101), "b.png": _rgb(102),
                        "c.png": _rgb(103)})

    async def scenario():
        async with _client() as client:
            first = await _image(client, run_id, "seen.png")
            conversions.hold()
            pending = [_image(client, run_id, name) for name in ("a.png", "b.png", "c.png")]
            await _settle(conversions, running=2, queued=1, pending=pending)
            again = await asyncio.wait_for(_image(client, run_id, "seen.png"), timeout=10)
            busy = (conversions.running, _queued())
            conversions.release()
            return first, again, busy, await asyncio.gather(*pending)

    first, again, busy, others = asyncio.run(scenario())
    assert first.status_code == again.status_code == 200
    assert again.content == first.content
    assert busy == (2, 1)
    assert [o.status_code for o in others] == [200] * 3
    assert _started(conversions).count("seen.png") == 1
