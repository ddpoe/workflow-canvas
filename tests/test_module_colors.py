"""Module colours: the colour standard's six tests, ``modules.json`` and the colour routes.

Witnesses catalog cases 16 (setting a module colour), 17 (the colour tests
and default assignment) and the colour half of 18 (modules registered
before colours were stored get theirs).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.routes import canvas_client
from wfc import layout
from wfc.registration import check_color, read_module_colors, register_module
from wfc.registration.color_standard import (
    PALETTE,
    PROTECTED,
    STATUS_MESSAGE,
    color_distance,
    css_name,
    normalize_hex,
)
from wfc.registration.module_color import default_color

PALETTE_HEXES = [hx for _name, hx in PALETTE]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD")


def _head_files(root: Path) -> set[str]:
    out = _git(root, "show", "--name-only", "--pretty=format:", "HEAD")
    return {ln for ln in out.splitlines() if ln}


def _modules_json(root: Path) -> str:
    return layout.module_colors_path(root).relative_to(root).as_posix()


# ---- case 17: the six tests -------------------------------------------------

@workflow(purpose="All sixteen palette colours pass the six colour tests; a "
                  "colour inside each protected colour's hue guard, each "
                  "protected colour itself, and a colour failing each contrast "
                  "or grey test is refused with its reason and a suggested "
                  "shade that passes")
def test_palette_passes_and_each_guard_refuses():
    for name, hx in PALETTE:
        result = check_color(hx)
        assert result.ok, f"palette colour {name} {hx} refused: {result}"
        assert result.suggested is None

    # Inside each hue guard (green, amber, red, cached violet, system teal).
    inside_guards = {"#607c39": "completed", "#747729": "running",
                     "#a1576e": "failed", "#995981": "cached results",
                     "#1f8468": "system nodes"}
    for hx, meaning in inside_guards.items():
        result = check_color(hx)
        assert not result.ok
        assert result.failed_test in {"distance", "hue_guard"}
        assert result.reserved == meaning, hx
        assert result.message == STATUS_MESSAGE
        assert check_color(result.suggested).ok
        assert result.suggested_name and result.suggested_name == result.suggested_name.lower()
    assert check_color("#607c39").failed_test == "hue_guard"

    for protected in PROTECTED:
        result = check_color(protected.hex.upper())
        assert (result.failed_test, result.reserved) == ("distance", protected.meaning)

    for hx, test in {"#666666": "chroma", "#c080a0": "white_text",
                     "#283888": "background"}.items():
        result = check_color(hx)
        assert (result.ok, result.failed_test, result.reserved) == (False, test, None)
        assert check_color(result.suggested).ok


def test_css_names_are_separate_lowercase_words():
    assert css_name("#db7093") == "pale violet red"
    assert css_name("#663399") == "rebecca purple"
    assert css_name("#da7194") == "pale violet red"


def test_a_value_that_is_not_a_colour_is_refused_by_name():
    assert normalize_hex("#ABC") == "#aabbcc"
    with pytest.raises(ValueError, match="'blue' is not a colour"):
        check_color("blue")


# ---- case 17: default assignment ---------------------------------------------

@workflow(purpose="register-module stores each new module's default colour in "
                  "modules.json in its registration commit: the first module "
                  "its name's palette slot, each later one a free palette "
                  "colour; colours already stored never move as more modules "
                  "are added, past sixteen too")
def test_default_colours_are_stable_as_modules_are_added(tmp_project):
    names = [f"mod{i:02d}" for i in range(18)]
    seen: dict[str, str] = {}
    for count, name in enumerate(names, start=1):
        口 = Step(step_num=1.1, name="Register a module",
                 purpose="register_module commits modules.json with the new colour")
        register_module(name=name, contracts=[])
        assert _modules_json(tmp_project) in _head_files(tmp_project)
        assert _git(tmp_project, "log", "-1", "--format=%s") == f"Register module {name}"
        口 = Step(step_num=1.2, name="Earlier colours stay",
                 purpose="every colour stored before is unchanged")
        stored = {n: e.color for n, e in read_module_colors(tmp_project).items()}
        assert len(stored) == count
        assert {n: stored[n] for n in seen} == seen
        seen[name] = stored[name]

    assert seen[names[0]] == default_color(names[0])
    assert set(seen.values()) <= set(PALETTE_HEXES)
    assert len({seen[n] for n in names[:16]}) == 16


@workflow(purpose="Modules registered one after another get default colours "
                  "that do not look alike: each later module takes the free "
                  "palette colour most unlike the colours already taken, so "
                  "every pair of the first four is at least a CIEDE2000 "
                  "distance of 15 apart")
def test_each_new_module_gets_a_default_unlike_the_others(tmp_project):
    names = ["mif_csv", "images", "qc", "stats"]
    for name in names:
        register_module(name=name, contracts=[])
    stored = {n: e.color for n, e in read_module_colors(tmp_project).items()}

    first, second = stored[names[0]], stored[names[1]]
    assert second == max((hx for hx in PALETTE_HEXES if hx != first),
                         key=lambda hx: color_distance(hx, first))
    colours = [stored[n] for n in names]
    closest = min(color_distance(a, b) for i, a in enumerate(colours)
                  for b in colours[i + 1:])
    assert closest >= 15, {n: stored[n] for n in names}


# ---- case 16: setting a colour through the routes ----------------------------

@workflow(purpose="Setting a module colour from the Registry: a palette colour "
                  "is stored in modules.json in its own commit and returned by "
                  "GET; a colour too close to a status colour is refused with "
                  "its reserved meaning and a suggested shade, writing nothing; "
                  "null puts back the module's default")
def test_set_module_colour_through_the_routes(tmp_project, monkeypatch):
    register_module(name="seg", contracts=[])
    client = canvas_client(tmp_project, monkeypatch)
    modules_json = layout.module_colors_path(tmp_project)

    口 = Step(step_num=1, name="Pick a palette colour",
             purpose="PATCH stores it in its own commit and GET returns it")
    default = client.get("/api/registry/modules").json()["modules"][0]["color"]
    assert default in PALETTE_HEXES
    target = next(hx for hx in PALETTE_HEXES if hx != default)
    resp = client.patch("/api/registry/modules/seg", json={"color": target.upper()})
    assert resp.status_code == 200, resp.text
    assert (resp.json()["name"], resp.json()["color"]) == ("seg", target)
    assert _git(tmp_project, "log", "-1", "--format=%s") == "Set the colour of module seg"
    assert _head_files(tmp_project) == {_modules_json(tmp_project)}
    assert client.get("/api/registry/modules").json()["modules"][0]["color"] == target
    assert read_module_colors(tmp_project)["seg"].color == target

    口 = Step(step_num=2, name="Type a status colour",
             purpose="PATCH refuses it with the check's answer and writes nothing")
    head, before = _head(tmp_project), modules_json.read_bytes()
    resp = client.patch("/api/registry/modules/seg", json={"color": "#50E7A3"})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert (detail["ok"], detail["failedTest"], detail["reserved"]) == (
        False, "distance", "completed")
    assert detail["message"] == STATUS_MESSAGE
    assert check_color(detail["suggested"]).ok
    assert detail["suggestedName"] == css_name(detail["suggested"])
    assert (_head(tmp_project), modules_json.read_bytes()) == (head, before)

    口 = Step(step_num=3, name="Ask the check route",
             purpose="color-check gives the same answer and writes nothing")
    resp = client.post("/api/registry/modules/color-check", json={"color": "#50e7a3"})
    assert resp.status_code == 200
    assert resp.json() == detail
    passing = client.post("/api/registry/modules/color-check", json={"color": target}).json()
    assert (passing["ok"], passing["suggested"]) == (True, None)
    assert client.post("/api/registry/modules/color-check",
                       json={"color": "nope"}).status_code == 400

    口 = Step(step_num=4, name="Reset",
             purpose="null puts back the default; an unknown module is a 404")
    resp = client.patch("/api/registry/modules/seg", json={"color": None})
    assert resp.json()["color"] == default
    assert client.patch("/api/registry/modules/ghost",
                        json={"color": target}).status_code == 404


# ---- case 18 (colour half): modules from before colours were stored -----------

@workflow(purpose="A project whose modules were registered before colours were "
                  "stored: the Registry's module list gives every module its "
                  "default in registration order, in one commit, and listing "
                  "again changes nothing")
def test_modules_registered_before_colours_get_them_once(tmp_project, monkeypatch):
    names = ["tiles", "normalize", "cluster", "annotate"]
    # The release before stored module rows only: register the modules the
    # production way, then take away the colour file they wrote, so the
    # project holds the modules and no modules.json.
    for name in names:
        register_module(name=name, contracts=[])
    _git(tmp_project, "rm", "-q", _modules_json(tmp_project))
    _git(tmp_project, "commit", "-q", "-m", "A project from before module colours")
    assert read_module_colors(tmp_project) == {}
    client = canvas_client(tmp_project, monkeypatch)
    head = _head(tmp_project)

    body = client.get("/api/registry/modules").json()
    colors = {m["name"]: m["color"] for m in body["modules"]}
    expected: dict[str, str] = {}
    for name in names:
        expected[name] = default_color(name, list(expected.values()))
    assert colors == expected
    assert _git(tmp_project, "rev-list", "--count", f"{head}..HEAD") == "1"
    assert _git(tmp_project, "log", "-1", "--format=%s") == "Assign module colours"
    assert _head_files(tmp_project) == {_modules_json(tmp_project)}

    again = client.get("/api/registry/modules").json()
    assert {m["name"]: m["color"] for m in again["modules"]} == expected
    assert _git(tmp_project, "rev-list", "--count", f"{head}..HEAD") == "1"
