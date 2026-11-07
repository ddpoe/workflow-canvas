"""The image route converts every raster image to a bounded PNG and refuses, from the header, one too large to decode.

Tier 2 entered at the routes over a real cache (catalog case 5, its first
half): each test runs a method whose folder output ``out`` holds the images
it asks for and archives the run. Tier 3 for catalog case 6: a crafted TIFF header that
claims 16k x 16k with no pixel data. Tier 1 tests enter ``image_info``
directly for the decoded size of every mode.

Every image is generated small (the largest holds about 2 MB decoded); the
16k x 16k case is a 122-byte header that could not be decoded at all.
"""

from __future__ import annotations

import io
import struct

import pytest
from axiom_annotations import Step, workflow
from PIL import Image

from tests.fixtures.fakes import bind_provider
from tests.fixtures.routes import completed_run
from tests.harness.scenario import Behavior
from wfc import layout
from wfc.canvas.previews import IMAGE_SIZES, image_info
from wfc.canvas.wfc_provider import WfcProvider
from wfc.storage import archive_outputs


@pytest.fixture
def image_run(tmp_project, monkeypatch):
    """Run a method whose folder output ``out`` holds the given files, and archive it.

    The run is driven through production's phases (``completed_run``; the
    method writes each file's bytes as they are), its output archived by
    ``archive_outputs``, and a provider bound over the project.

    Returns:
        A function taking ``{file name: bytes}`` and returning the run id.
    """
    def make(members: dict[str, bytes]) -> str:
        run = completed_run(
            tmp_project, monkeypatch=monkeypatch, method="plot", module="imaging",
            sample="s1", outputs={"out": "directory"}, output_files={"out": "out"},
            behavior=Behavior(outputs={"out": members}),
        )
        archive_outputs(tmp_project, run_id=run.run_id)
        bind_provider(monkeypatch, WfcProvider(str(tmp_project)))
        return str(run.run_id)

    return make


def _encode(im: Image.Image, fmt: str, **params) -> bytes:
    buffer = io.BytesIO()
    im.save(buffer, format=fmt, **params)
    return buffer.getvalue()


def _gradient(mode: str, size: tuple[int, int], top: int) -> Image.Image:
    """A left-to-right ramp from 0 to ``top`` in a one-band mode."""
    width, height = size
    im = Image.new(mode, size)
    im.putdata([x * top // (width - 1) for _y in range(height) for x in range(width)])
    return im


def _tiff_header(width: int, height: int) -> bytes:
    """A little-endian TIFF whose one IFD claims an 8-bit grayscale image and holds no pixels.

    Its strip offset points past the end of the file: there is nothing to decode.
    """
    entries = [
        (256, 4, width), (257, 4, height), (258, 3, 8), (259, 3, 1), (262, 3, 1),
        (273, 4, 10_000_000), (277, 3, 1), (278, 4, height), (279, 4, width * height),
    ]
    out = b"II*\x00" + struct.pack("<IH", 8, len(entries))
    for tag, kind, value in entries:
        out += (struct.pack("<HHIHH", tag, kind, 1, value, 0) if kind == 3
                else struct.pack("<HHII", tag, kind, 1, value))
    return out + struct.pack("<I", 0)


def _set_previews(root, text: str) -> None:
    marker = layout.marker_path(root)
    marker.write_text(marker.read_text(encoding="utf-8") + "\n[previews]\n" + text,
                      encoding="utf-8")


def _png(client, run_id, name, size):
    resp = client.get(f"/api/wfc/run/{run_id}/image/out/{name}", params={"size": size})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "image/png"
    return Image.open(io.BytesIO(resp.content))


def _info(client, run_id, name, size="view"):
    resp = client.get(f"/api/wfc/run/{run_id}/image-info/out/{name}", params={"size": size})
    assert resp.status_code == 200, resp.text
    return resp.json()


#: Images past every bound on one side, a JPEG past ``view``, and one that fits every bound.
SIZED_IMAGES = {
    "wide.png": _encode(Image.new("RGB", (5000, 60), (200, 40, 40)), "PNG"),
    "tall.tif": _encode(Image.new("L", (60, 5000), 128), "TIFF"),
    "photo.jpg": _encode(Image.new("RGB", (3000, 2000), (10, 160, 60)), "JPEG"),
    "small.png": _encode(Image.new("RGBA", (100, 80), (0, 0, 255, 128)), "PNG"),
}


@pytest.mark.parametrize("size", ["thumb", "view", "large"])
@workflow(purpose="Each image size answers a PNG whose longer side is at most its bound "
                  "(260, 2048, 4096 px) on either side, browser formats converted too; an "
                  "image that fits keeps its own size, and image-info names both sizes")
def test_each_image_size_stays_within_its_bound(canvas_client, image_run, size):
    run_id = image_run(SIZED_IMAGES)
    bound = IMAGE_SIZES[size]

    for name, content in SIZED_IMAGES.items():
        original = Image.open(io.BytesIO(content)).size
        preview = _png(canvas_client, run_id, name, size)
        info = _info(canvas_client, run_id, name, size)

        assert preview.width <= bound and preview.height <= bound, name
        assert max(preview.size) == min(bound, max(original)), name
        assert (info["width"], info["height"]) == original, name
        assert (info["preview_width"], info["preview_height"]) == preview.size, name
        assert info["refused"] is None and info["pages"] == 1, name
    small = _png(canvas_client, run_id, "small.png", size)
    assert small.size == (100, 80) and small.getpixel((5, 5)) == (0, 0, 255, 128)


@workflow(purpose="A crafted TIFF header claiming 16k x 16k pixels, with memory_mb set "
                  "small, is refused from its header with its dimensions and never decoded",
          inputs="A 122-byte TIFF with no pixel data; [previews] memory_mb = 64",
          outputs="The image route's 422 and image-info's refusal")
def test_a_huge_image_is_refused_from_its_header(canvas_client, image_run, tmp_project):
    口 = Step(step_num=1, name="Archive the header", purpose="A run whose folder holds a "
             "TIFF claiming 16384 x 16384 8-bit pixels (256 MB decoded) and holding none")
    header = _tiff_header(16384, 16384)
    run_id = image_run({"huge.tif": header})
    _set_previews(tmp_project, "memory_mb = 64\n")

    口 = Step(step_num=2, name="Prove there is nothing to decode", purpose="Decoding the "
             "file fails: a route that decoded it could not answer with its dimensions")
    assert len(header) < 1024
    with pytest.raises(OSError):
        Image.open(io.BytesIO(header)).load()

    口 = Step(step_num=3, name="Ask for the preview", purpose="The image route answers 422 "
             "with the header's dimensions, the memory_mb it exceeds half of, and the "
             "memory_mb that would admit it")
    refused = canvas_client.get(f"/api/wfc/run/{run_id}/image/out/huge.tif",
                                params={"size": "thumb"})
    assert refused.status_code == 422, refused.text
    body = refused.json()
    assert (body["width"], body["height"]) == (16384, 16384)
    assert "16384 x 16384" in body["detail"] and "memory_mb = 64" in body["detail"]
    assert f"memory_mb to at least {body['memory_mb_needed']}" in body["detail"]

    口 = Step(step_num=4, name="Ask what the preview shows", purpose="image-info gives the "
             "same refusal, the decoded size and the memory it was judged on")
    info = _info(canvas_client, run_id, "huge.tif", "thumb")
    assert info["refused"] == body["detail"]
    assert info["memory_mb_needed"] == body["memory_mb_needed"]
    assert info["decoded_bytes"] == 16384 * 16384
    assert info["memory_bytes"] > info["decoded_bytes"]
    assert body["memory_mb_needed"] * 1024 * 1024 >= 2 * info["memory_bytes"]


@pytest.mark.parametrize("size", ["view", "thumb"])
@workflow(purpose="A 16-bit image converts to 8-bit by scaling its range, not clipping, "
                  "at its own size and downsized many times over: a full-range ramp stays "
                  "a ramp, and image-info says the depth was reduced")
def test_a_16_bit_image_is_scaled_not_blown_out(canvas_client, image_run, size):
    run_id = image_run({"ramp.tif": _encode(_gradient("I;16", (2048, 32), 65535), "TIFF")})

    preview = _png(canvas_client, run_id, "ramp.tif", size)
    info = _info(canvas_client, run_id, "ramp.tif", size)

    assert preview.width == min(2048, IMAGE_SIZES[size])
    assert preview.mode == "L"
    row = [preview.getpixel((x, 0)) for x in range(preview.width)]
    assert row[0] == 0 and row[-1] == 255
    assert row == sorted(row)
    assert row.count(255) < 8
    assert 115 <= row[len(row) // 2] <= 140
    assert (info["bit_depth"], info["bit_depth_reduced"]) == (16, True)


@workflow(purpose="An animated GIF and a multi-page TIFF preview their first frame or "
                  "page, and image-info reports how many there are")
def test_a_multi_frame_image_shows_its_first_frame_and_its_count(canvas_client, image_run):
    frames = [Image.new("RGB", (40, 30), colour)
              for colour in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    run_id = image_run({
        "anim.gif": _encode(frames[0], "GIF", save_all=True, append_images=frames[1:]),
        "pages.tif": _encode(frames[0], "TIFF", save_all=True, append_images=frames[1:]),
    })

    for name in ("anim.gif", "pages.tif"):
        preview = _png(canvas_client, run_id, name, "thumb").convert("RGB")
        assert preview.getpixel((20, 15)) == (255, 0, 0), name
        assert _info(canvas_client, run_id, name)["pages"] == 3, name


@workflow(purpose="A file that is not a readable image answers 415 with a reason, never "
                  "500; an SVG is served as it is, sandboxed; another kind is a 415")
def test_the_image_route_refuses_what_it_cannot_convert(canvas_client, image_run):
    whole = _encode(Image.new("RGB", (64, 64), (1, 2, 3)), "PNG")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>'
    run_id = image_run({
        "fake.png": b"not an image at all",
        "cut.png": whole[: len(whole) // 2],
        "eps.png": b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n",
        "plot.svg": svg,
        "table.csv": b"a,b\n1,2\n",
    })

    for name in ("fake.png", "eps.png", "table.csv"):
        for route in ("image", "image-info"):
            resp = canvas_client.get(f"/api/wfc/run/{run_id}/{route}/out/{name}")
            assert resp.status_code == 415, (route, name, resp.text)
            assert name in resp.json()["detail"], (route, name)
    # A truncated PNG's header is whole: image-info answers, the decode fails.
    assert _info(canvas_client, run_id, "cut.png")["width"] == 64
    cut = canvas_client.get(f"/api/wfc/run/{run_id}/image/out/cut.png")
    assert cut.status_code == 415, cut.text
    assert "cut.png" in cut.json()["detail"]
    served = canvas_client.get(f"/api/wfc/run/{run_id}/image/out/plot.svg")
    assert served.status_code == 200 and served.content == svg
    assert served.headers["content-type"].startswith("image/svg+xml")
    assert served.headers["content-security-policy"] == "sandbox"


@workflow(purpose="An invalid [previews] memory_mb makes the image routes answer 409 with "
                  "read_config's message; an edited limit applies without a restart")
def test_the_image_routes_read_memory_mb_per_request(canvas_client, image_run, tmp_project):
    run_id = image_run({"pic.png": _encode(Image.new("L", (300, 200), 9), "PNG")})
    assert _info(canvas_client, run_id, "pic.png")["refused"] is None

    marker = layout.marker_path(tmp_project)
    before = marker.read_text(encoding="utf-8")
    _set_previews(tmp_project, "memory_mb = 0\n")
    refused = canvas_client.get(f"/api/wfc/run/{run_id}/image/out/pic.png")
    assert refused.status_code == 409, refused.text
    assert "[previews] memory_mb" in refused.json()["detail"]

    marker.write_text(before, encoding="utf-8")
    assert canvas_client.get(f"/api/wfc/run/{run_id}/image/out/pic.png").status_code == 200


#: Each mode's bytes per decoded pixel, as Pillow stores it: 1-bit a byte per
#: pixel, 16-bit grayscale two, every multi-band, 32-bit and float mode four.
MODE_BYTES = [("1", 1), ("L", 1), ("P", 1), ("I;16", 2), ("I", 4), ("F", 4),
              ("RGB", 4), ("RGBA", 4), ("CMYK", 4), ("LA", 4)]


@pytest.mark.parametrize("mode, per_pixel", MODE_BYTES)
def test_decoded_size_is_read_from_the_header_for_every_mode(tmp_path, mode, per_pixel):
    path = tmp_path / "pic.tif"
    path.write_bytes(_encode(Image.new(mode, (30, 20)), "TIFF"))

    info = image_info(path, "pic.tif", "view", memory_mb=1)

    assert info.decoded_bytes == 30 * 20 * per_pixel
    assert info.refused is None


def test_a_jpeg_is_judged_at_the_scale_it_decodes_at(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(_encode(Image.new("RGB", (4000, 3000), (5, 5, 5)), "JPEG"))

    thumb = image_info(path, "photo.jpg", "thumb", memory_mb=4)
    large = image_info(path, "photo.jpg", "large", memory_mb=4)

    assert thumb.decoded_bytes == 500 * 375 * 4
    assert thumb.refused is None
    assert large.decoded_bytes == 4000 * 3000 * 4
    assert large.refused and "4000 x 3000" in large.refused
