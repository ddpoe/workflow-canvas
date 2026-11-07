"""The canvas colour standard's module-colour tests: the one implementation.

The SOP ``docs/sops/canvas-colours`` (Module Colours) names sixteen palette
colours, seven protected colours and six tests a module colour must pass.
This module holds them and the maths they need, in the standard library
only: sRGB to CIELAB (D65) for CIEDE2000 distance, sRGB to OKLab/OKLCh for
the hue guards and chroma, and the WCAG 2 contrast ratio. A refused colour
gets the nearest shade that passes (searched over OKLCh lightness, chroma
and hue shifts, kept by the smallest CIEDE2000 distance) and the nearest
CSS Color Module Level 4 named colour, written as separate lowercase words.

The canvas holds no colour logic: the registry routes call
:func:`check_color` and the picker shows its answer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: The sixteen palette colours, in slot order: (name, ``#rrggbb``).
PALETTE: tuple[tuple[str, str], ...] = (
    ("moss", "#687b00"),
    ("drab", "#627143"),
    ("petrol", "#06809c"),
    ("steel", "#3f708d"),
    ("denim", "#4d6b98"),
    ("cobalt", "#3c65de"),
    ("dusk", "#676794"),
    ("rose", "#aa4b70"),
    ("indigo", "#6e5cd6"),
    ("magenta", "#b53887"),
    ("mauve", "#8a5c75"),
    ("plum", "#ac5b89"),
    ("raspberry", "#ce3c7c"),
    ("iris", "#6467b4"),
    ("olive", "#737a38"),
    ("rosewood", "#a06579"),
)


@dataclass(frozen=True)
class ProtectedColor:
    """A reserved colour and what it means.

    Attributes:
        hex: The colour, ``#rrggbb``.
        meaning: The meaning in plain words, as the picker names it.
        hue_guard: The OKLCh hue guard in degrees, or ``None`` when the
            colour has none (only test 1 applies).
    """

    hex: str
    meaning: str
    hue_guard: float | None


#: The protected colours of test 1, with their hue guards (test 2).
PROTECTED: tuple[ProtectedColor, ...] = (
    ProtectedColor("#50e7a3", "completed", 35.0),
    ProtectedColor("#f1b620", "running", 30.0),
    ProtectedColor("#f97163", "failed", 30.0),
    ProtectedColor("#7f8ea3", "cancelled", None),
    ProtectedColor("#eab8ff", "cached results", 30.0),
    ProtectedColor("#4a90d9", "buttons and links", None),
    ProtectedColor("#0d857c", "system nodes", 20.0),
)

#: Test thresholds, as the SOP states them.
MIN_DISTANCE = 15.0
HUE_GUARD_MIN_CHROMA = 0.04
MIN_CHROMA = 0.06
MIN_WHITE_TEXT = 4.5
#: (background, minimum contrast) pairs of test 5.
BACKGROUNDS: tuple[tuple[str, float], ...] = (
    ("#1e1e1e", 3.0), ("#202020", 3.0), ("#2b2b2b", 2.5),
)
#: ``--module-ink``: the module colour mixed with this much white (sRGB).
INK_WHITE = 0.45
INK_BACKGROUND = "#252526"
MIN_INK = 4.5

STATUS_MESSAGE = "Too close to a colour Workflow Canvas uses to show status."
#: The refusal in plain words, by failed test.
MESSAGES: dict[str, str] = {
    "distance": STATUS_MESSAGE,
    "hue_guard": STATUS_MESSAGE,
    "chroma": "Too grey: the module would look as if it had no colour.",
    "white_text": "Too light: white text on it would be hard to read.",
    "background": "Too dark: it would be hard to see on the canvas.",
    "module_ink": "Too dark: the module's name would be hard to read.",
}


@dataclass(frozen=True)
class ColorCheck:
    """The answer to "can this be a module colour?".

    Attributes:
        ok: Whether the colour passes all six tests.
        color: The colour checked, normalised to ``#rrggbb``.
        failed_test: The first test it fails (``distance``, ``hue_guard``,
            ``chroma``, ``white_text``, ``background``, ``module_ink``), or
            ``None``.
        reserved: The meaning of the protected colour it is too close to
            (tests 1 and 2), or ``None``.
        message: The refusal in plain words, or ``None``.
        suggested: The nearest passing shade, ``#rrggbb``, or ``None``.
        suggested_name: The CSS named colour nearest the suggested shade,
            as separate lowercase words, or ``None``.
    """

    ok: bool
    color: str
    failed_test: str | None = None
    reserved: str | None = None
    message: str | None = None
    suggested: str | None = None
    suggested_name: str | None = None


# ---- parsing and conversions ------------------------------------------------

def normalize_hex(color: str) -> str:
    """Return *color* as lowercase ``#rrggbb``.

    Args:
        color: ``#rgb`` or ``#rrggbb``, any case.

    Returns:
        The six-digit lowercase form.

    Raises:
        ValueError: *color* is not a hex colour.
    """
    text = color.strip() if isinstance(color, str) else ""
    digits = text[1:] if text.startswith("#") else ""
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    if len(digits) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in digits):
        raise ValueError(f"{color!r} is not a colour: give it as #rrggbb.")
    return "#" + digits.lower()


def _rgb(hex_color: str) -> tuple[float, float, float]:
    """Return the sRGB channels of a normalised hex colour, each 0..1."""
    return (int(hex_color[1:3], 16) / 255.0,
            int(hex_color[3:5], 16) / 255.0,
            int(hex_color[5:7], 16) / 255.0)


def _to_hex(rgb: tuple[float, float, float]) -> str:
    """Return ``#rrggbb`` for sRGB channels in 0..1 (clamped, rounded)."""
    return "#" + "".join(
        f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)


def _linear(c: float) -> float:
    """Decode one channel through the sRGB transfer function."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gamma(c: float) -> float:
    """Encode one channel through the sRGB transfer function."""
    if c <= 0.0031308:
        return 12.92 * c
    return 1.055 * (c ** (1 / 2.4)) - 0.055


def _luminance(rgb: tuple[float, float, float]) -> float:
    """WCAG 2 relative luminance."""
    r, g, b = (_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    """WCAG 2 contrast ratio between two sRGB colours.

    Args:
        a: One colour's sRGB channels, 0..1.
        b: The other's.

    Returns:
        The ratio, 1 to 21.
    """
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


_XN, _YN, _ZN = 0.95047, 1.0, 1.08883


def _lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """CIELAB (D65 white point) of an sRGB colour."""
    r, g, b = (_linear(c) for c in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / _XN
    y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / _YN
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / _ZN

    def f(t: float) -> float:
        return t ** (1 / 3) if t > (6 / 29) ** 3 else t / (3 * (6 / 29) ** 2) + 4 / 29

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def ciede2000(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    """CIEDE2000 colour difference (kL = kC = kH = 1).

    Args:
        lab1: One colour in CIELAB.
        lab2: The other.

    Returns:
        The distance.
    """
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2
    c1 = math.hypot(a1, b1)
    c2 = math.hypot(a2, b2)
    c_bar7 = ((c1 + c2) / 2) ** 7
    g = 0.5 * (1 - math.sqrt(c_bar7 / (c_bar7 + 25 ** 7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if c1p else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if c2p else 0.0

    dlp = l2 - l1
    dcp = c2p - c1p
    if c1p * c2p == 0:
        dhp = 0.0
    else:
        dhp = h2p - h1p
        if dhp > 180:
            dhp -= 360
        elif dhp < -180:
            dhp += 360
    dhp_big = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp / 2))

    lbp = (l1 + l2) / 2
    cbp = (c1p + c2p) / 2
    if c1p * c2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hbp = (h1p + h2p + 360) / 2
    else:
        hbp = (h1p + h2p - 360) / 2

    t = (1 - 0.17 * math.cos(math.radians(hbp - 30))
         + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6))
         - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    d_theta = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    cbp7 = cbp ** 7
    rc = 2 * math.sqrt(cbp7 / (cbp7 + 25 ** 7))
    sl = 1 + (0.015 * (lbp - 50) ** 2) / math.sqrt(20 + (lbp - 50) ** 2)
    sc = 1 + 0.045 * cbp
    sh = 1 + 0.015 * cbp * t
    rt = -math.sin(math.radians(2 * d_theta)) * rc
    return math.sqrt((dlp / sl) ** 2 + (dcp / sc) ** 2 + (dhp_big / sh) ** 2
                     + rt * (dcp / sc) * (dhp_big / sh))


def _oklch(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """OKLCh (lightness 0..1, chroma, hue in degrees) of an sRGB colour."""
    r, g, b = (_linear(c) for c in rgb)
    lc = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    mc = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    sc = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    lc, mc, sc = (math.copysign(abs(v) ** (1 / 3), v) for v in (lc, mc, sc))
    lightness = 0.2104542553 * lc + 0.7936177850 * mc - 0.0040720468 * sc
    a = 1.9779984951 * lc - 2.4285922050 * mc + 0.4505937099 * sc
    bb = 0.0259040371 * lc + 0.7827717662 * mc - 0.8086757660 * sc
    return lightness, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def _from_oklch(lightness: float, chroma: float,
                hue: float) -> tuple[float, float, float] | None:
    """Return the sRGB channels (0..1) of an OKLCh colour, or ``None`` outside the gamut."""
    a = chroma * math.cos(math.radians(hue))
    b = chroma * math.sin(math.radians(hue))
    lc = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    mc = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    sc = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    lin = (4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc,
           -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc,
           -0.0041960863 * lc - 0.7034186147 * mc + 1.7076147010 * sc)
    if any(v < -1e-4 or v > 1 + 1e-4 for v in lin):
        return None
    return tuple(_gamma(min(1.0, max(0.0, v))) for v in lin)  # type: ignore[return-value]


def _hue_gap(h1: float, h2: float) -> float:
    """Angular distance between two hues, 0..180 degrees."""
    d = abs(h1 - h2) % 360
    return 360 - d if d > 180 else d


def module_ink(color: str) -> str:
    """The ``--module-ink`` of a module colour: mixed with 45% white in sRGB.

    Args:
        color: The module colour, ``#rgb`` or ``#rrggbb``.

    Returns:
        The ink, ``#rrggbb``.
    """
    rgb = _rgb(normalize_hex(color))
    return _to_hex(tuple(c * (1 - INK_WHITE) + INK_WHITE for c in rgb))  # type: ignore[arg-type]


# ---- the six tests ------------------------------------------------------------

_PROTECTED_LAB = tuple((p, _lab(_rgb(p.hex))) for p in PROTECTED)
_PROTECTED_HUE = tuple((p, _oklch(_rgb(p.hex))[2]) for p in PROTECTED
                       if p.hue_guard is not None)
_WHITE = (1.0, 1.0, 1.0)
_BACKGROUND_RGB = tuple((_rgb(bg), minimum) for bg, minimum in BACKGROUNDS)
_INK_BACKGROUND_RGB = _rgb(INK_BACKGROUND)


def _first_failure(rgb: tuple[float, float, float],
                   lab: tuple[float, float, float] | None = None
                   ) -> tuple[str, str | None] | None:
    """The first of the six tests *rgb* fails, with the reserved meaning.

    Returns:
        ``(test, reserved meaning or None)``, or ``None`` when it passes.
    """
    lab = lab if lab is not None else _lab(rgb)
    nearest: tuple[float, ProtectedColor] | None = None
    for protected, plab in _PROTECTED_LAB:
        distance = ciede2000(lab, plab)
        if distance < MIN_DISTANCE and (nearest is None or distance < nearest[0]):
            nearest = (distance, protected)
    if nearest is not None:
        return "distance", nearest[1].meaning

    _lightness, chroma, hue = _oklch(rgb)
    if chroma >= HUE_GUARD_MIN_CHROMA:
        guarded: tuple[float, ProtectedColor] | None = None
        for protected, phue in _PROTECTED_HUE:
            gap = _hue_gap(hue, phue)
            assert protected.hue_guard is not None
            if gap <= protected.hue_guard and (guarded is None or gap < guarded[0]):
                guarded = (gap, protected)
        if guarded is not None:
            return "hue_guard", guarded[1].meaning
    if chroma < MIN_CHROMA:
        return "chroma", None
    if contrast(rgb, _WHITE) < MIN_WHITE_TEXT:
        return "white_text", None
    if any(contrast(rgb, bg) < minimum for bg, minimum in _BACKGROUND_RGB):
        return "background", None
    ink = tuple(c * (1 - INK_WHITE) + INK_WHITE for c in rgb)
    if contrast(ink, _INK_BACKGROUND_RGB) < MIN_INK:  # type: ignore[arg-type]
        return "module_ink", None
    return None


def check_color(color: str) -> ColorCheck:
    """Check a colour against the six module-colour tests.

    The tests, in order: CIEDE2000 distance of 15 or more to every protected
    colour; outside each protected colour's OKLCh hue guard (at chroma 0.04
    or more); OKLCh chroma 0.06 or more; white text on it at least 4.5:1;
    at least 3:1 on ``#1e1e1e`` and ``#202020`` and 2.5:1 on ``#2b2b2b``;
    its ``--module-ink`` at least 4.5:1 on ``#252526``. A refused colour
    carries the nearest passing shade and that shade's CSS name.

    Args:
        color: ``#rgb`` or ``#rrggbb``.

    Returns:
        The check's answer.

    Raises:
        ValueError: *color* is not a hex colour.
    """
    hex_color = normalize_hex(color)
    failure = _first_failure(_rgb(hex_color))
    if failure is None:
        return ColorCheck(ok=True, color=hex_color)
    test, reserved = failure
    suggested = nearest_passing(hex_color)
    return ColorCheck(ok=False, color=hex_color, failed_test=test,
                      reserved=reserved, message=MESSAGES[test],
                      suggested=suggested, suggested_name=css_name(suggested))


# ---- the nearest passing shade -------------------------------------------------

def _frange(start: float, stop: float, step: float) -> list[float]:
    """Inclusive float range."""
    count = int(round((stop - start) / step))
    return [start + i * step for i in range(count + 1)]


def _nearest_in(target_lab: tuple[float, float, float],
                grid: list[tuple[float, float, float]],
                seen: set[str]) -> tuple[float, str] | None:
    """The passing in-gamut grid colour nearest *target_lab*, by CIEDE2000.

    Candidates are rounded to 8-bit hex first, so the tests run on the
    colour that would be returned.
    """
    ranked: list[tuple[float, str, tuple[float, float, float], tuple[float, float, float]]] = []
    for lightness, chroma, hue in grid:
        rgb = _from_oklch(lightness, chroma, hue)
        if rgb is None:
            continue
        hex_color = _to_hex(rgb)
        if hex_color in seen:
            continue
        seen.add(hex_color)
        rgb = _rgb(hex_color)
        lab = _lab(rgb)
        ranked.append((ciede2000(target_lab, lab), hex_color, rgb, lab))
    ranked.sort(key=lambda item: (item[0], item[1]))
    for distance, hex_color, rgb, lab in ranked:
        if _first_failure(rgb, lab) is None:
            return distance, hex_color
    return None


def nearest_passing(color: str) -> str:
    """The shade nearest *color* that passes all six tests.

    Searches OKLCh lightness, chroma and hue shifts of up to 90 degrees
    around the colour, coarse then fine around the best, and keeps the
    passing shade with the smallest CIEDE2000 distance from it. When no
    searched shade passes, the nearest palette colour is returned (every
    palette colour passes).

    Args:
        color: ``#rgb`` or ``#rrggbb``.

    Returns:
        The shade, ``#rrggbb``.
    """
    hex_color = normalize_hex(color)
    rgb = _rgb(hex_color)
    target = _lab(rgb)
    _l0, _c0, hue0 = _oklch(rgb)
    seen: set[str] = set()
    coarse = [(lightness, chroma, (hue0 + shift) % 360)
              for shift in _frange(-90, 90, 5)
              for lightness in _frange(0.30, 0.90, 0.025)
              for chroma in _frange(0.06, 0.30, 0.02)]
    best = _nearest_in(target, coarse, seen)
    if best is None:
        return min((p for _n, p in PALETTE),
                   key=lambda p: ciede2000(target, _lab(_rgb(p))))
    l1, c1, h1 = _oklch(_rgb(best[1]))
    fine = [(lightness, chroma, (h1 + shift) % 360)
            for shift in _frange(-5, 5, 1)
            for lightness in _frange(l1 - 0.025, l1 + 0.025, 0.005)
            for chroma in _frange(max(0.06, c1 - 0.02), c1 + 0.02, 0.005)]
    finer = _nearest_in(target, fine, seen)
    if finer is not None and finer[0] < best[0]:
        return finer[1]
    return best[1]


# ---- CSS named colours -------------------------------------------------------

#: CSS Color Module Level 4 named colours (``transparent`` aside), each
#: name written as separate lowercase words; the keyword is the words
#: joined.
CSS_NAMED_COLORS: tuple[tuple[str, str], ...] = (
    ("alice blue", "#f0f8ff"), ("antique white", "#faebd7"), ("aqua", "#00ffff"),
    ("aquamarine", "#7fffd4"), ("azure", "#f0ffff"), ("beige", "#f5f5dc"),
    ("bisque", "#ffe4c4"), ("black", "#000000"), ("blanched almond", "#ffebcd"),
    ("blue", "#0000ff"), ("blue violet", "#8a2be2"), ("brown", "#a52a2a"),
    ("burly wood", "#deb887"), ("cadet blue", "#5f9ea0"), ("chartreuse", "#7fff00"),
    ("chocolate", "#d2691e"), ("coral", "#ff7f50"), ("cornflower blue", "#6495ed"),
    ("cornsilk", "#fff8dc"), ("crimson", "#dc143c"), ("cyan", "#00ffff"),
    ("dark blue", "#00008b"), ("dark cyan", "#008b8b"),
    ("dark goldenrod", "#b8860b"), ("dark gray", "#a9a9a9"),
    ("dark green", "#006400"), ("dark grey", "#a9a9a9"), ("dark khaki", "#bdb76b"),
    ("dark magenta", "#8b008b"), ("dark olive green", "#556b2f"),
    ("dark orange", "#ff8c00"), ("dark orchid", "#9932cc"), ("dark red", "#8b0000"),
    ("dark salmon", "#e9967a"), ("dark sea green", "#8fbc8f"),
    ("dark slate blue", "#483d8b"), ("dark slate gray", "#2f4f4f"),
    ("dark slate grey", "#2f4f4f"), ("dark turquoise", "#00ced1"),
    ("dark violet", "#9400d3"), ("deep pink", "#ff1493"),
    ("deep sky blue", "#00bfff"), ("dim gray", "#696969"), ("dim grey", "#696969"),
    ("dodger blue", "#1e90ff"), ("firebrick", "#b22222"),
    ("floral white", "#fffaf0"), ("forest green", "#228b22"), ("fuchsia", "#ff00ff"),
    ("gainsboro", "#dcdcdc"), ("ghost white", "#f8f8ff"), ("gold", "#ffd700"),
    ("goldenrod", "#daa520"), ("gray", "#808080"), ("green", "#008000"),
    ("green yellow", "#adff2f"), ("grey", "#808080"), ("honeydew", "#f0fff0"),
    ("hot pink", "#ff69b4"), ("indian red", "#cd5c5c"), ("indigo", "#4b0082"),
    ("ivory", "#fffff0"), ("khaki", "#f0e68c"), ("lavender", "#e6e6fa"),
    ("lavender blush", "#fff0f5"), ("lawn green", "#7cfc00"),
    ("lemon chiffon", "#fffacd"), ("light blue", "#add8e6"),
    ("light coral", "#f08080"), ("light cyan", "#e0ffff"),
    ("light goldenrod yellow", "#fafad2"), ("light gray", "#d3d3d3"),
    ("light green", "#90ee90"), ("light grey", "#d3d3d3"),
    ("light pink", "#ffb6c1"), ("light salmon", "#ffa07a"),
    ("light sea green", "#20b2aa"), ("light sky blue", "#87cefa"),
    ("light slate gray", "#778899"), ("light slate grey", "#778899"),
    ("light steel blue", "#b0c4de"), ("light yellow", "#ffffe0"),
    ("lime", "#00ff00"), ("lime green", "#32cd32"), ("linen", "#faf0e6"),
    ("magenta", "#ff00ff"), ("maroon", "#800000"),
    ("medium aquamarine", "#66cdaa"), ("medium blue", "#0000cd"),
    ("medium orchid", "#ba55d3"), ("medium purple", "#9370db"),
    ("medium sea green", "#3cb371"), ("medium slate blue", "#7b68ee"),
    ("medium spring green", "#00fa9a"), ("medium turquoise", "#48d1cc"),
    ("medium violet red", "#c71585"), ("midnight blue", "#191970"),
    ("mint cream", "#f5fffa"), ("misty rose", "#ffe4e1"), ("moccasin", "#ffe4b5"),
    ("navajo white", "#ffdead"), ("navy", "#000080"), ("old lace", "#fdf5e6"),
    ("olive", "#808000"), ("olive drab", "#6b8e23"), ("orange", "#ffa500"),
    ("orange red", "#ff4500"), ("orchid", "#da70d6"),
    ("pale goldenrod", "#eee8aa"), ("pale green", "#98fb98"),
    ("pale turquoise", "#afeeee"), ("pale violet red", "#db7093"),
    ("papaya whip", "#ffefd5"), ("peach puff", "#ffdab9"), ("peru", "#cd853f"),
    ("pink", "#ffc0cb"), ("plum", "#dda0dd"), ("powder blue", "#b0e0e6"),
    ("purple", "#800080"), ("rebecca purple", "#663399"), ("red", "#ff0000"),
    ("rosy brown", "#bc8f8f"), ("royal blue", "#4169e1"),
    ("saddle brown", "#8b4513"), ("salmon", "#fa8072"), ("sandy brown", "#f4a460"),
    ("sea green", "#2e8b57"), ("seashell", "#fff5ee"), ("sienna", "#a0522d"),
    ("silver", "#c0c0c0"), ("sky blue", "#87ceeb"), ("slate blue", "#6a5acd"),
    ("slate gray", "#708090"), ("slate grey", "#708090"), ("snow", "#fffafa"),
    ("spring green", "#00ff7f"), ("steel blue", "#4682b4"), ("tan", "#d2b48c"),
    ("teal", "#008080"), ("thistle", "#d8bfd8"), ("tomato", "#ff6347"),
    ("turquoise", "#40e0d0"), ("violet", "#ee82ee"), ("wheat", "#f5deb3"),
    ("white", "#ffffff"), ("white smoke", "#f5f5f5"), ("yellow", "#ffff00"),
    ("yellow green", "#9acd32"),
)

_CSS_LAB = tuple((name, _lab(_rgb(hex_color))) for name, hex_color in CSS_NAMED_COLORS)


def css_name(color: str) -> str:
    """The CSS named colour nearest *color* by CIEDE2000, as separate words.

    Args:
        color: ``#rgb`` or ``#rrggbb``.

    Returns:
        E.g. ``"pale violet red"``; of names sharing one colour, the first
        in alphabetical order.
    """
    lab = _lab(_rgb(normalize_hex(color)))
    return min(_CSS_LAB, key=lambda item: ciede2000(lab, item[1]))[0]
