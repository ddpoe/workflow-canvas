"""Artifact previews: which viewer a run output opens in.

One classifier names the viewer kind for any displayed file name, from its
extension (case-insensitive): ``image``, ``table``, ``json``, ``text``,
``pdf``, or ``None`` (download only). Both listings, a run's artifacts and a
draft step's files, take the kind from here, so they agree on every name and
the canvas never decides a viewer from an extension.

Images come in two forms: the ones a browser shows as they are, and the ones
the canvas converts to PNG first. The list of converted formats is explicit,
never Pillow's registry: a format is previewed because wfc chose to, not
because the installed Pillow happens to read it.

The same displayed name gives the media type the file route serves it with.

Tables, JSON and text are previewed from the start of the file, within fixed
caps, and every answer says what it cut: a table shows at most 100 rows read
from at most 2 MB, text at most 1 MB, and JSON is parsed only up to 5 MB.

Raster images, browser formats included, are converted to an 8-bit PNG of at
most 260, 2048 or 4096 px on the longer side, never larger than the file.
``[previews] memory_mb`` is a ceiling on the memory image previews use at
once: at most two conversions run at once, and each may use up to half of
it. What a conversion needs (the decoded file plus the working images of
its resize, 8-bit conversion and PNG encode) is reckoned from the header
first, and an image that needs more than half is refused before any decode,
naming the ``memory_mb`` that would admit it. The others queue for one of
the two conversion threads in arrival order; a request waiting its turn
holds no thread.

A converted PNG is kept in the preview cache, keyed by the output's content,
the size and the converter's version, so a second request for it is
answered from disk without converting or queueing. The cache
stays within ``[previews] cache_mb``, dropping the least recently used
preview first.
"""

from __future__ import annotations

import asyncio
import codecs
import csv
import hashlib
import io
import json
import os
import struct
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal, cast

from PIL import Image

from .models import ArtifactPreview, ImageInfo, ImageSize, TableDelimiter, TablePreview

#: A viewer kind, as the listings carry it in ``viewer``.
ViewerKind = Literal["image", "table", "json", "text", "pdf"]

#: Images a browser shows as they are.
BROWSER_IMAGE_EXTENSIONS = frozenset(
    {"png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ico", "avif"})
#: Images the canvas converts to PNG before showing them.
CONVERTED_IMAGE_EXTENSIONS = frozenset(
    {"tif", "tiff", "jp2", "j2k", "ppm", "pgm", "pnm"})
#: Delimited tables.
TABLE_EXTENSIONS = frozenset({"csv", "tsv"})
#: JSON documents.
JSON_EXTENSIONS = frozenset({"json"})
#: Plain text, shown as it is (some with highlighting).
TEXT_EXTENSIONS = frozenset(
    {"txt", "log", "md", "yaml", "yml", "toml", "py", "sh", "r", "ini", "cfg"})
#: PDF documents, shown by the browser.
PDF_EXTENSIONS = frozenset({"pdf"})

#: Each extension's viewer kind; an extension not here is download only.
_KIND_BY_EXTENSION: dict[str, ViewerKind] = {
    **{ext: "image" for ext in BROWSER_IMAGE_EXTENSIONS | CONVERTED_IMAGE_EXTENSIONS},
    **{ext: "table" for ext in TABLE_EXTENSIONS},
    **{ext: "json" for ext in JSON_EXTENSIONS},
    **{ext: "text" for ext in TEXT_EXTENSIONS},
    **{ext: "pdf" for ext in PDF_EXTENSIONS},
}


#: The media type for anything without its own entry: the browser downloads it.
DOWNLOAD_MEDIA_TYPE = "application/octet-stream"

#: Each viewable extension's media type. A fixed table, not ``mimetypes``:
#: on Windows that reads the registry, where ``.csv`` can come back as
#: ``application/vnd.ms-excel``. Text kinds are ``text/plain`` (the server
#: adds the charset), so a browser shows a script or a log instead of
#: running or downloading it.
_MEDIA_TYPE_BY_EXTENSION: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "svg": "image/svg+xml",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "ico": "image/vnd.microsoft.icon",
    "avif": "image/avif",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "jp2": "image/jp2",
    "j2k": "image/x-jp2-codestream",
    "ppm": "image/x-portable-pixmap",
    "pgm": "image/x-portable-graymap",
    "pnm": "image/x-portable-anymap",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "json": "application/json",
    "pdf": "application/pdf",
    **{ext: "text/plain" for ext in TEXT_EXTENSIONS},
}


def extension_of(name: str) -> str:
    """Return a displayed name's extension, lower-case and without the dot.

    The last path segment decides, so ``<slot>/<file>`` and a folder member
    ``<folder>/<file>`` classify by the file. A dotfile (``.env``) and a
    name with no dot have no extension.

    Args:
        name: The displayed name, ``/``-separated.

    Returns:
        The extension, e.g. ``"tiff"``, or ``""``.
    """
    return PurePosixPath(name).suffix.lower().lstrip(".")


def viewer_kind(name: str) -> ViewerKind | None:
    """Return the viewer a displayed file name opens in, or ``None``.

    Classifies from the displayed name, never a cache path: a top-level file
    output's cache entry has no extension.

    Args:
        name: The displayed name, ``/``-separated.

    Returns:
        ``"image"``, ``"table"``, ``"json"``, ``"text"`` or ``"pdf"``; ``None``
        for a file that is download only (parquet, h5ad, no extension, ...).
    """
    return _KIND_BY_EXTENSION.get(extension_of(name))


def media_type(name: str) -> str:
    """Return the media type a file is served with, from its displayed name.

    The displayed name decides, never the cache path, whose entry for a
    top-level file output has no extension.

    Args:
        name: The displayed name, ``/``-separated.

    Returns:
        The media type, e.g. ``"text/csv"``; ``application/octet-stream`` for
        a file no viewer opens.
    """
    return _MEDIA_TYPE_BY_EXTENSION.get(extension_of(name), DOWNLOAD_MEDIA_TYPE)


def is_converted_image(name: str) -> bool:
    """Return whether a displayed name is an image the canvas converts to PNG.

    Args:
        name: The displayed name, ``/``-separated.

    Returns:
        ``True`` for the converted formats (TIFF, JPEG 2000, the PNM family);
        ``False`` for a browser image and for anything not an image.
    """
    return extension_of(name) in CONVERTED_IMAGE_EXTENSIONS


# =============================================================================
# Table, JSON and text previews
# =============================================================================

_MB = 1024 * 1024
#: The most rows a table preview shows after its header.
TABLE_ROWS = 100
#: The most bytes a table preview reads from the file.
TABLE_READ_BYTES = 2 * _MB
#: The characters at the start of a table the delimiter is guessed from.
TABLE_SAMPLE_CHARS = 64 * 1024
#: The most bytes a text preview shows.
TEXT_BYTES = 1 * _MB
#: The largest JSON file parsed; a larger one is shown as text.
JSON_PARSE_BYTES = 5 * _MB

#: Each table delimiter's character.
DELIMITER_CHARS: dict[TableDelimiter, str] = {
    "comma": ",", "tab": "\t", "semicolon": ";", "pipe": "|", "space": " ",
}
_DELIMITER_NAMES = {char: name for name, char in DELIMITER_CHARS.items()}


class NoPreviewError(ValueError):
    """A file the preview route does not answer for (an image, a PDF, download only)."""


def _read_head(stream: BinaryIO, cap: int, total_bytes: int) -> tuple[str, int]:
    """Read at most ``cap`` bytes and decode them as UTF-8, cut on a character boundary.

    One bounded ``read``: the preview never consumes more than ``cap`` bytes.
    Bytes that are not UTF-8 become U+FFFD; a character the cap splits is
    left out rather than replaced; a leading BOM is dropped.

    Args:
        stream: The file, opened in binary mode at its start.
        cap: The most bytes to read.
        total_bytes: The file's size, to tell a cut from the end of the file.

    Returns:
        ``(text, shown_bytes)``: the decoded text and the bytes it covers.
    """
    data = stream.read(cap)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    text = decoder.decode(data, final=len(data) >= total_bytes)
    pending = len(decoder.getstate()[0])
    if text.startswith("﻿"):
        text = text[1:]
    return text, len(data) - pending


def _mb(n_bytes: int) -> str:
    """Return a byte count in MB, as a reason names it (``"2 MB"``, ``"5.3 MB"``)."""
    return f"{n_bytes / _MB:.1f}".removesuffix(".0") + " MB"


def read_text(stream: BinaryIO, total_bytes: int, *,
              reason: str | None = None) -> ArtifactPreview:
    """Preview a text file: at most its first 1 MB.

    Args:
        stream: The file, opened in binary mode at its start.
        total_bytes: The file's size.
        reason: Why a file of another kind is shown as text (JSON too large
            or not valid); it leads the answer's reason.

    Returns:
        A ``text`` preview, ``truncated`` when the file is longer than 1 MB.
    """
    text, shown = _read_head(stream, TEXT_BYTES, total_bytes)
    truncated = shown < total_bytes
    reasons = [reason] if reason else []
    if truncated:
        reasons.append(f"showing the first {_mb(TEXT_BYTES)} of {_mb(total_bytes)}")
    return ArtifactPreview(kind="text", shown_bytes=shown, total_bytes=total_bytes,
                           truncated=truncated, reason="; ".join(reasons) or None, text=text)


def read_json(stream: BinaryIO, total_bytes: int) -> ArtifactPreview:
    """Preview a JSON file: the whole file when it parses within 5 MB, text otherwise.

    Args:
        stream: The file, opened in binary mode at its start.
        total_bytes: The file's size.

    Returns:
        A ``json`` preview holding the file's text; a ``text`` preview, with
        its reason, for a file over 5 MB or one that is not valid JSON.
    """
    if total_bytes > JSON_PARSE_BYTES:
        return read_text(stream, total_bytes,
                         reason=f"JSON over {_mb(JSON_PARSE_BYTES)} is shown as text")
    text, shown = _read_head(stream, JSON_PARSE_BYTES, total_bytes)
    try:
        json.loads(text)
    except (ValueError, RecursionError) as exc:
        # ValueError covers a syntax error and an integer too long to convert.
        detail = (f"{exc.msg} at line {exc.lineno} column {exc.colno}"
                  if isinstance(exc, json.JSONDecodeError) else str(exc) or type(exc).__name__)
        stream.seek(0)
        return read_text(stream, total_bytes,
                         reason=f"not valid JSON ({detail}): shown as text")
    return ArtifactPreview(kind="json", shown_bytes=shown, total_bytes=total_bytes,
                           truncated=False, text=text)


def _excel_with(char: str) -> type[csv.Dialect]:
    """Return the ``excel`` dialect with another delimiter."""
    return type("_Delimited", (csv.excel,), {"delimiter": char})


def _guess_header(sample: str, char: str) -> bool | None:
    """Guess whether a table's first row is a header, or ``None`` when it cannot tell.

    ``Sniffer.has_header`` sniffs the dialect again, unrestricted, and could
    split on a character the preview does not offer: it is given the
    delimiter already chosen instead.
    """
    sniffer = csv.Sniffer()
    sniffer.sniff = lambda *_args, **_kwargs: _excel_with(char)  # type: ignore[method-assign]
    try:
        return sniffer.has_header(sample)
    except (csv.Error, StopIteration):
        return None


def read_table(stream: BinaryIO, total_bytes: int, *, name: str,
               delimiter: TableDelimiter | None = None,
               header: bool | None = None) -> ArtifactPreview:
    """Preview a delimited table: at most 100 rows from at most its first 2 MB.

    The delimiter is guessed by ``csv.Sniffer`` from the first 64 KB, among
    the five a reader can choose; when it cannot tell, a ``.tsv`` uses tab and
    anything else a comma, and the reason says so. The header row is guessed
    the same way. ``delimiter`` and ``header`` override both guesses.

    A field longer than ``csv``'s limit (131072 characters, a 2 MB file with
    no line break) stops the rows before it rather than failing.

    Args:
        stream: The file, opened in binary mode at its start.
        total_bytes: The file's size.
        name: The displayed name: its extension picks the fallback delimiter.
        delimiter: The delimiter asked for, or ``None`` to guess.
        header: Whether the first row is the header, or ``None`` to guess.

    Returns:
        A ``table`` preview, ``truncated`` when the file holds more rows or
        more bytes than it shows.
    """
    text, shown = _read_head(stream, TABLE_READ_BYTES, total_bytes)
    reasons: list[str] = []
    cut = shown < total_bytes
    if cut:
        last_break = text.rfind("\n")
        if last_break >= 0:
            # The last line read is partial: drop it rather than show a cut row.
            text = text[:last_break + 1]
            reasons.append(f"only the rows in the first {_mb(TABLE_READ_BYTES)} are read")
        else:
            reasons.append(f"the first line is longer than {_mb(TABLE_READ_BYTES)}: it is cut")

    sample = text[:TABLE_SAMPLE_CHARS]
    if len(text) > TABLE_SAMPLE_CHARS and "\n" in sample:
        sample = sample[:sample.rfind("\n") + 1]
    delimiter_guessed = False
    if delimiter is None:
        try:
            char = csv.Sniffer().sniff(sample, delimiters="".join(DELIMITER_CHARS.values())).delimiter
            delimiter = _DELIMITER_NAMES[char]
            delimiter_guessed = True
        except (csv.Error, KeyError):
            delimiter = "tab" if extension_of(name) == "tsv" else "comma"
            reasons.append(f"the delimiter could not be guessed: {delimiter} is used")
    char = DELIMITER_CHARS[delimiter]
    header_guessed = False
    if header is None:
        guess = _guess_header(sample, char) if sample else None
        header_guessed = guess is not None
        header = True if guess is None else guess

    rows: list[list[str]] = []
    wanted = TABLE_ROWS + (1 if header else 0)
    more = False
    reader = csv.reader(io.StringIO(text, newline=""), _excel_with(char))
    try:
        for row in reader:
            if not row:
                continue
            if len(rows) == wanted:
                more = True
                break
            rows.append(row)
    except csv.Error as exc:
        more = True
        reasons.append(f"the rows stop at a field csv cannot read ({exc})")
    if more and len(rows) == wanted:
        reasons.insert(0, f"showing the first {TABLE_ROWS} rows")

    columns = rows.pop(0) if header and rows else None
    table = TablePreview(columns=columns, rows=rows, delimiter=delimiter,
                         delimiter_guessed=delimiter_guessed, header=header,
                         header_guessed=header_guessed)
    return ArtifactPreview(kind="table", shown_bytes=shown, total_bytes=total_bytes,
                           truncated=more or cut, reason="; ".join(reasons) or None,
                           table=table)


def preview_artifact(path: Path, name: str, *, delimiter: TableDelimiter | None = None,
                     header: bool | None = None) -> ArtifactPreview:
    """Preview a resolved artifact file as its displayed name's kind says.

    Args:
        path: The file, resolved through the provider.
        name: The displayed name: its extension picks the kind.
        delimiter: A table's delimiter, or ``None`` to guess.
        header: Whether a table's first row is the header, or ``None`` to guess.

    Returns:
        The preview, within the kind's caps.

    Raises:
        NoPreviewError: The name is not a table, JSON or text file, or the
            path is not a file.
    """
    kind = viewer_kind(name)
    if kind not in ("table", "json", "text") or not path.is_file():
        raise NoPreviewError(
            f"No preview for {name!r}: only tables, JSON and text files are previewed")
    total = path.stat().st_size
    with path.open("rb") as stream:
        if kind == "table":
            return read_table(stream, total, name=name, delimiter=delimiter, header=header)
        if kind == "json":
            return read_json(stream, total)
        return read_text(stream, total)


# =============================================================================
# Image previews
# =============================================================================

#: Each image size's longer side, in pixels. The browser never gets more than ``large``.
IMAGE_SIZES: dict[ImageSize, int] = {"thumb": 260, "view": 2048, "large": 4096}

#: The formats Pillow may open a file as, whatever its extension: the ones
#: behind the image extensions above (``JPEG`` also opens a camera's MPO,
#: ``PPM`` the whole PNM family). Never Pillow's whole registry: some of its
#: plugins run an outside program to decode (EPS runs Ghostscript).
_PILLOW_FORMATS = ("PNG", "JPEG", "GIF", "WEBP", "BMP", "ICO", "AVIF",
                   "TIFF", "JPEG2000", "PPM")

#: Bytes Pillow holds per decoded pixel, by mode: one for 1-bit, grayscale
#: and palette images (1-bit is stored a byte per pixel), two for 16-bit
#: grayscale; every other mode (RGB, RGBA, CMYK, LA, 32-bit ``I``, float
#: ``F``, ...) is stored in four.
_PIXEL_BYTES = {"1": 1, "L": 1, "P": 1, "I;16": 2, "I;16L": 2, "I;16B": 2, "I;16N": 2}
_DEFAULT_PIXEL_BYTES = 4

#: The modes a preview PNG is written in, as they are.
_PNG_MODES = frozenset({"1", "L", "LA", "P", "RGB", "RGBA"})

# Pillow's decompression-bomb guard refuses an image over ~179 M pixels when
# it is opened, before its decoded size can be read, so a large image would be
# refused without its dimensions. The header check in ``_read_header`` is the
# guard instead: it refuses an image whose decoding exceeds ``memory_mb``
# before anything is decoded. The setting is process-wide: it is made here,
# once, and nothing else in wfc opens images.
Image.MAX_IMAGE_PIXELS = None

#: The errors Pillow raises for a file it cannot open or decode (truncated,
#: corrupt, not the format it claims).
_PILLOW_ERRORS = (OSError, ValueError, SyntaxError, EOFError, struct.error)


class ImageTooLargeError(ValueError):
    """An image whose conversion needs more than half of ``[previews] memory_mb``; refused unread.

    Attributes:
        info: The image's facts, read from its header; ``info.refused`` is the reason.
    """

    def __init__(self, info: ImageInfo):
        super().__init__(info.refused)
        self.info = info


class ImageUnreadableError(ValueError):
    """A file Pillow cannot open or decode as an image."""


def is_raster_image(name: str) -> bool:
    """Return whether a displayed name is an image the image route converts.

    Every image kind but SVG, which is served as it is.

    Args:
        name: The displayed name, ``/``-separated.

    Returns:
        ``True`` for a raster image, browser formats included.
    """
    return viewer_kind(name) == "image" and extension_of(name) != "svg"


def fit_within(width: int, height: int, longest: int) -> tuple[int, int]:
    """Return an image's size scaled down so its longer side is at most ``longest``.

    Never scales up: an image that fits keeps its size (shown at 1:1).

    Args:
        width: The image's width in pixels.
        height: The image's height in pixels.
        longest: The most pixels the longer side may have.

    Returns:
        ``(width, height)``, each at least 1.
    """
    side = max(width, height)
    if side <= longest:
        return width, height
    scale = longest / side
    return max(1, round(width * scale)), max(1, round(height * scale))


def _bit_depth(im: Image.Image) -> int:
    """Return an open image's bits per sample: the TIFF tag when present, else from its mode.

    A 16-bit RGB TIFF opens in 8-bit ``RGB`` mode, so its mode alone would
    not tell its depth.
    """
    tags = getattr(im, "tag_v2", None)
    if tags is not None and 258 in tags:
        bits = tags[258]
        return int(max(bits)) if isinstance(bits, tuple) else int(bits)
    if im.mode == "1":
        return 1
    if im.mode.startswith("I;16"):
        return 16
    if im.mode in ("I", "F"):
        return 32
    return 8


#: Pillow's ``reducing_gap`` in :func:`_render_png` for the modes it can pre-reduce.
_REDUCING_GAP = 3.0


def _resize_bytes(mode: str, source: tuple[int, int], out: tuple[int, int]) -> int:
    """Return the most :func:`_render_png`'s resize holds besides the decoded image, its result included.

    Mirrors ``Image.resize``: 1-bit and palette images are resized by
    nearest neighbour into the result alone; ``LA`` and ``RGBA`` are first
    copied whole into premultiplied alpha; other modes may be pre-reduced by
    a whole factor (``reducing_gap``, never for a packed mode such as
    ``I;16``); then the resampler's first pass writes an image as wide as
    the result and as tall as its source before the second pass writes the
    result.
    """
    (width, height), (out_width, out_height) = source, out
    per_pixel = _PIXEL_BYTES.get(mode, _DEFAULT_PIXEL_BYTES)
    result = out_width * out_height * per_pixel
    if mode in ("1", "P"):
        return result
    premultiplied = width * height * 4 if mode in ("LA", "RGBA") else 0
    reduced = 0
    if ";" not in mode:
        factor_x = int(width / out_width / _REDUCING_GAP) or 1
        factor_y = int(height / out_height / _REDUCING_GAP) or 1
        if factor_x > 1 or factor_y > 1:
            width, height = -(-width // factor_x), -(-height // factor_y)
            reduced = width * height * per_pixel
    first_pass = out_width * height * per_pixel
    return premultiplied + reduced + first_pass + result


def _working_bytes(mode: str, source: tuple[int, int], out: tuple[int, int]) -> int:
    """Return the most memory :func:`_render_png` holds besides the decoded image.

    The peak of its three phases, each holding the resized image (when it
    resized): the resize itself (:func:`_resize_bytes`); the 8-bit
    conversion, which for 16-bit grayscale holds a 32-bit copy, the scaled
    32-bit image and the 8-bit result at once (32-bit and float images skip
    the copy), and for a mode PNG cannot hold an RGB or RGBA copy; and the
    PNG encode, whose buffer and the bytes copied out of it can each reach
    the final image's size. A decoder's own buffers are not counted.

    Args:
        mode: The image's mode as decoded.
        source: The decoded image's width and height (a JPEG's reduced scale).
        out: The preview's width and height.

    Returns:
        The bytes, at their peak.
    """
    out_pixels = out[0] * out[1]
    per_pixel = _PIXEL_BYTES.get(mode, _DEFAULT_PIXEL_BYTES)
    resizing = _resize_bytes(mode, source, out) if out != source else 0
    resized = out_pixels * per_pixel if out != source else 0
    final = out_pixels * per_pixel
    eight_bit = 0
    if mode.startswith("I") or mode == "F":
        eight_bit = out_pixels * ((4 if mode.startswith("I;16") else 0) + 4 + 1)
        final = out_pixels
    elif mode not in _PNG_MODES:
        eight_bit = final = out_pixels * 4
    encoding = (final if eight_bit else 0) + 2 * final
    return max(resizing, resized + eight_bit, resized + encoding)


def _read_header(im: Image.Image, size: ImageSize, memory_mb: int) -> ImageInfo:
    """Read an open image's facts from its header, readying a reduced JPEG decode.

    Nothing is decoded. A JPEG is set to decode at the smallest scale still
    at least the preview's size (``draft``), and its decoded size is that
    scale's. The memory the conversion needs is the decoded size plus
    :func:`_working_bytes`; the image is refused when that is more than half
    of ``memory_mb``.

    Args:
        im: The image, opened and not loaded.
        size: The preview size asked for.
        memory_mb: ``[previews] memory_mb``: twice the most one conversion may take.

    Returns:
        The image's facts; ``refused`` set when the conversion needs more
        than half of ``memory_mb``.
    """
    width, height = im.size
    preview_width, preview_height = fit_within(width, height, IMAGE_SIZES[size])
    bit_depth = _bit_depth(im)
    im.draft(im.mode, (preview_width, preview_height))
    decoded = im.size[0] * im.size[1] * _PIXEL_BYTES.get(im.mode, _DEFAULT_PIXEL_BYTES)
    need = decoded + _working_bytes(im.mode, im.size, (preview_width, preview_height))
    needed_mb = -(-2 * need // _MB)
    refused = None
    if needed_mb > memory_mb:
        refused = (f"This {width} x {height} image needs {_mb(need)} to preview at "
                   f"{preview_width} x {preview_height}, more than half of [previews] "
                   f"memory_mb = {memory_mb}. To view "
                   f"it, set [previews] memory_mb to at least {needed_mb} in "
                   f"wf-canvas.toml, or download the file.")
    return ImageInfo(width=width, height=height, preview_width=preview_width,
                     preview_height=preview_height, bit_depth=bit_depth,
                     bit_depth_reduced=bit_depth > 8, pages=getattr(im, "n_frames", 1),
                     decoded_bytes=decoded, memory_bytes=need, memory_mb_needed=needed_mb,
                     refused=refused)


def _open_image(path: Path, name: str) -> Image.Image:
    """Open a resolved raster image lazily (header only), or refuse it.

    Raises:
        NoPreviewError: ``name`` is not a raster image, or the path is not a file.
        ImageUnreadableError: Pillow cannot open the file as one of the image formats.
    """
    if not is_raster_image(name) or not path.is_file():
        raise NoPreviewError(
            f"No image preview for {name!r}: only raster images are converted")
    try:
        return Image.open(path, formats=_PILLOW_FORMATS)
    except _PILLOW_ERRORS as exc:
        raise ImageUnreadableError(f"{name!r} cannot be read as an image ({exc})") from exc


def _checked_header(im: Image.Image, name: str, size: ImageSize, memory_mb: int) -> ImageInfo:
    """Read an open image's header as ``_read_header`` does; a header Pillow cannot walk is unreadable.

    Counting the frames of a multi-page file reads every page's header, which
    a damaged file can fail.
    """
    try:
        return _read_header(im, size, memory_mb)
    except _PILLOW_ERRORS as exc:
        raise ImageUnreadableError(f"{name!r} cannot be read as an image ({exc})") from exc


def image_info(path: Path, name: str, size: ImageSize, memory_mb: int) -> ImageInfo:
    """Return what an image preview of a resolved file shows, read from its header.

    Never decodes the file.

    Args:
        path: The file, resolved through the provider.
        name: The displayed name: it must be a raster image.
        size: The preview size asked for.
        memory_mb: ``[previews] memory_mb``.

    Returns:
        The image's facts: the memory converting it takes and the smallest
        ``memory_mb`` that admits it; ``refused`` set when the image route
        would refuse it.

    Raises:
        NoPreviewError: ``name`` is not a raster image.
        ImageUnreadableError: Pillow cannot open the file.
    """
    with _open_image(path, name) as im:
        return _checked_header(im, name, size, memory_mb)


def _to_8bit(im: Image.Image) -> Image.Image:
    """Return a 16-bit, 32-bit or float grayscale image as 8-bit, its range scaled to 0-255.

    The image's own lowest and highest values map to 0 and 255, so a 12-bit
    camera image in a 16-bit file is not near black and nothing is clipped
    to white (a plain mode conversion clips every value over 255).
    """
    if im.mode.startswith("I;16"):
        im = im.convert("I")
    # A one-band image's extrema are two numbers (a multi-band one's would be pairs).
    low, high = cast("tuple[float, float]", im.getextrema())
    if high <= low:
        return Image.new("L", im.size, 0 if high <= 0 else 255)
    scale = 255 / (high - low)
    return im.point(lambda v: v * scale - low * scale).convert("L")


def _render_png(im: Image.Image, width: int, height: int) -> bytes:
    """Decode an open image's first frame and write it as an 8-bit PNG of the given size.

    Downsizes first, then reduces the bit depth, so the full-size image is
    held only once. Every intermediate image is closed when the PNG is written.
    """
    im.load()
    steps: list[Image.Image] = []
    out = im
    if out.size != (width, height):
        # Pillow's fast pre-reduction has no packed modes (``I;16``, ``BGR;15``):
        # those resample in one pass.
        gap = None if ";" in out.mode else 3.0
        out = out.resize((width, height), Image.Resampling.LANCZOS, reducing_gap=gap)
        steps.append(out)
    if out.mode.startswith("I") or out.mode == "F":
        out = _to_8bit(out)
        steps.append(out)
    elif out.mode not in _PNG_MODES:
        out = out.convert("RGBA" if out.mode in ("La", "PA", "RGBa") else "RGB")
        steps.append(out)
    buffer = io.BytesIO()
    out.save(buffer, format="PNG")
    for step in steps:
        step.close()
    return buffer.getvalue()


def convert_image(path: Path, name: str, size: ImageSize,
                  memory_mb: int) -> tuple[bytes, ImageInfo]:
    """Convert a resolved raster image's first frame or page to an 8-bit PNG preview.

    The memory the conversion needs is read from the header and checked
    before any decode. A JPEG decodes at a reduced scale when the preview is
    smaller; an animated GIF or a multi-page TIFF shows its first frame or
    page. The file is closed as soon as the PNG is written.

    Args:
        path: The file, resolved through the provider.
        name: The displayed name: it must be a raster image.
        size: The preview size: its longer side at most 260, 2048 or 4096 px.
        memory_mb: ``[previews] memory_mb``: twice the most the conversion may take.

    Returns:
        ``(png, info)``: the PNG's bytes and what it shows of the file.

    Raises:
        NoPreviewError: ``name`` is not a raster image.
        ImageTooLargeError: The conversion needs more than half of ``memory_mb``.
        ImageUnreadableError: Pillow cannot open or decode the file.
    """
    with _open_image(path, name) as im:
        info = _checked_header(im, name, size, memory_mb)
        if info.refused:
            raise ImageTooLargeError(info)
        try:
            png = _render_png(im, info.preview_width, info.preview_height)
        except _PILLOW_ERRORS as exc:
            raise ImageUnreadableError(f"{name!r} cannot be decoded ({exc})") from exc
    return png, info


# =============================================================================
# Two conversions at once
# =============================================================================

#: The most image conversions that run at once. Each may use up to half of
#: ``[previews] memory_mb``, so together they stay within it.
MAX_CONVERSIONS = 2

#: The threads conversions decode on, as many as may run at once: a decode
#: never runs on the event loop or on the server's shared worker threads.
#: Conversions waiting for one queue in its work queue, first come first served.
_CONVERT_POOL = ThreadPoolExecutor(max_workers=MAX_CONVERSIONS,
                                   thread_name_prefix="wfc-image-preview")


async def preview_image(path: Path, name: str, size: ImageSize, memory_mb: int, *,
                        cache: PreviewCache | None = None,
                        content: str | None = None) -> bytes:
    """Return a resolved raster image's preview PNG: from the cache, or converted in its turn.

    A preview the cache holds is answered at once: no header read, no
    conversion, no queueing. Otherwise the header is read first, off the
    event loop; an image whose conversion needs more than half of
    ``memory_mb`` is refused at once, never queued. The conversion then
    queues for one of the :data:`MAX_CONVERSIONS` conversion threads in
    arrival order, the request awaiting it and holding no thread. If the
    awaiting coroutine is cancelled while it queues, its conversion is
    dropped before it starts. The PNG is then stored in the cache.

    Args:
        path: The file, resolved through the provider.
        name: The displayed name: it must be a raster image.
        size: The preview size.
        memory_mb: ``[previews] memory_mb`` as this request read it.
        cache: The preview cache, or ``None`` to convert every time.
        content: The file's content key from the provider's resolution (the
            output's hash, plus the member path for a folder member), or
            ``None`` when it has none: such a file is not cached.

    Returns:
        The PNG's bytes, as :func:`convert_image` writes them.

    Raises:
        NoPreviewError: ``name`` is not a raster image.
        ImageTooLargeError: The conversion needs more than half of ``memory_mb``.
        ImageUnreadableError: Pillow cannot open or decode the file.
    """
    if cache is None or content is None:
        return await _convert_queued(path, name, size, memory_mb)
    hit = await asyncio.to_thread(cache.get, content, size)
    if hit is not None:
        return hit
    png = await _convert_queued(path, name, size, memory_mb)
    await asyncio.to_thread(cache.put, content, size, png)
    return png


async def _convert_queued(path: Path, name: str, size: ImageSize, memory_mb: int) -> bytes:
    """Refuse an image from its header, or convert it on the conversion pool in its turn; see :func:`preview_image`."""
    info = await asyncio.to_thread(image_info, path, name, size, memory_mb)
    if info.refused:
        raise ImageTooLargeError(info)
    loop = asyncio.get_running_loop()
    png, _info = await loop.run_in_executor(
        _CONVERT_POOL, convert_image, path, name, size, memory_mb)
    return png


# =============================================================================
# The preview cache
# =============================================================================

#: The converter's version, part of every cache key: raise it whenever a
#: conversion's PNG would come out differently, so an older preview is never
#: served.
CONVERTER_VERSION = 1

_recency_lock = threading.Lock()
_last_recency_ns = 0


def _next_recency_ns() -> int:
    """Return a use time later than every one given before, for a preview's recency.

    Two uses within the clock's resolution would otherwise tie, and the
    least recently used could not be told apart.
    """
    global _last_recency_ns
    with _recency_lock:
        _last_recency_ns = max(time.time_ns(), _last_recency_ns + 1000)
        return _last_recency_ns


def _mark_used(path: Path) -> None:
    """Make a cached preview the most recently used (its modification time is its recency)."""
    stamp = _next_recency_ns()
    try:
        os.utime(path, ns=(stamp, stamp))
    except OSError:
        pass


@dataclass(frozen=True)
class PreviewCache:
    """The converted PNGs on disk, each keyed by its content, size and converter version, within ``cap_mb``.

    A key is the content key the provider's resolution gives (the output's
    content hash, plus the member path for a folder member), so it changes
    when the content does and is never computed by hashing the file. Two runs
    resolving to the same content (a cache-hit run and its source) share
    their previews. A preview's recency is its file's modification time; a
    hit refreshes it. Every operation tolerates the disk: a preview that
    cannot be read is a miss, one that cannot be written is not kept, and
    one that cannot be removed (Windows refuses to delete a file being read)
    is skipped.

    Attributes:
        directory: The cache's folder (Layout's ``preview_cache_dir``), made on demand.
        cap_mb: ``[previews] cache_mb``: the most the cached PNGs may take, in MB.
    """

    directory: Path
    cap_mb: int

    def path_for(self, content: str, size: ImageSize) -> Path:
        """Return the file a preview of ``content`` at ``size`` is kept in.

        Args:
            content: The file's content key.
            size: The preview size.

        Returns:
            ``<directory>/<sha256 of version, size and content>.png``.
        """
        key = hashlib.sha256(f"{CONVERTER_VERSION}\n{size}\n{content}".encode()).hexdigest()
        return self.directory / f"{key}.png"

    def get(self, content: str, size: ImageSize) -> bytes | None:
        """Return a cached preview's bytes and mark it used, or ``None`` when it is not cached.

        Args:
            content: The file's content key.
            size: The preview size.

        Returns:
            The PNG, or ``None``.
        """
        path = self.path_for(content, size)
        try:
            png = path.read_bytes()
        except OSError:
            return None
        _mark_used(path)
        return png

    def put(self, content: str, size: ImageSize, png: bytes) -> None:
        """Keep a converted preview, then drop the least recently used until the cache fits ``cap_mb``.

        The PNG is written under a temporary name and renamed into place, so
        a reader never sees half a file. A PNG larger than the whole cap is
        not kept; a write the disk refuses keeps nothing. Never raises.

        Args:
            content: The file's content key.
            size: The preview size.
            png: The preview's bytes.
        """
        if len(png) > self.cap_mb * _MB:
            return
        path = self.path_for(content, size)
        temporary = self.directory / f".{path.stem}.{uuid.uuid4().hex}.tmp"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(png)
            os.replace(temporary, path)
        except OSError:
            # Another request is placing the same preview, or the disk refused.
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return
        _mark_used(path)
        self.evict()

    def evict(self) -> None:
        """Remove the least recently used previews until the rest fit ``cap_mb``.

        A preview that cannot be removed (being read, on Windows) is skipped
        and the next one is tried. Never raises.
        """
        entries: list[tuple[int, int, Path]] = []
        try:
            listing = list(self.directory.glob("*.png"))
        except OSError:
            return
        for path in listing:
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((stat.st_mtime_ns, stat.st_size, path))
        total = sum(size for _used, size, _path in entries)
        cap = self.cap_mb * _MB
        for _used, size, path in sorted(entries):
            if total <= cap:
                return
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            total -= size
