"""The userdocs sidebar is laid out by the nav file's ``sidebar:`` block.

``userdocs/_sphinx/group_index.py`` runs after ``axiom-graph render-site``: it
writes the site's ``index.md`` as one captioned toctree per sidebar group, and
nests a page's children under it, numbered when the page asks for it. Every
rendered page must sit in the sidebar exactly once, so a page cannot drop out of
the navigation unnoticed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "userdocs" / "_sphinx" / "group_index.py"


def _group_index():
    spec = importlib.util.spec_from_file_location("group_index", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _site(tmp_path: Path, pages: list[str], sidebar: str) -> tuple[Path, Path]:
    """A rendered site holding *pages* and a nav file listing them in show."""
    out = tmp_path / "site"
    for page in pages:
        path = out / f"{page}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {page}\n\nBody of {page}.\n", encoding="utf-8")
    nav = tmp_path / "nav.yml"
    show = "\n".join(f"  - {p}" for p in pages)
    nav.write_text(f"site_name: Docs\nshow:\n{show}\n{sidebar}", encoding="utf-8")
    return nav, out


def _toctrees(text: str) -> list[list[str]]:
    """The toctree blocks of a page, each as its option and entry lines."""
    blocks, current = [], None
    for line in text.splitlines():
        if line.startswith("```{toctree}"):
            current = []
        elif line.startswith("```") and current is not None:
            blocks.append(current)
            current = None
        elif current is not None and line.strip():
            current.append(line.strip())
    return blocks


def test_the_sidebar_block_sets_the_groups_and_nests_numbered_steps(tmp_path):
    pages = ["tutorials/first", "how-to/build", "how-to/step-one", "how-to/step-two",
             "how-to/aside", "reference/schema"]
    nav, out = _site(tmp_path, pages, """sidebar:
  - caption: Start here
    pages:
      - tutorials/first
  - caption: Build
    pages:
      - page: how-to/build
        numbered: true
        children:
          - how-to/step-one
          - how-to/step-two
        extra:
          - how-to/aside
  - caption: Look up
    pages:
      - reference/schema
""")

    _group_index().build_index(nav, out)

    index = _toctrees((out / "index.md").read_text(encoding="utf-8"))
    assert index == [
        [":caption: Start here", ":maxdepth: 2", "tutorials/first"],
        [":caption: Build", ":maxdepth: 2", "how-to/build"],
        [":caption: Look up", ":maxdepth: 2", "reference/schema"],
    ]
    parent = (out / "how-to/build.md").read_text(encoding="utf-8")
    assert parent.startswith("# how-to/build\n\nBody of how-to/build.")
    assert _toctrees(parent) == [
        [":hidden:", ":numbered: 1", "/how-to/step-one", "/how-to/step-two"],
        [":hidden:", "/how-to/aside"],
    ]


@pytest.mark.parametrize("sidebar, named", [
    ("sidebar:\n  - caption: Only\n    pages:\n      - how-to/a\n", "how-to/b"),
    ("sidebar:\n  - caption: One\n    pages:\n      - how-to/a\n      - how-to/b\n"
     "  - caption: Two\n    pages:\n      - how-to/b\n", "how-to/b"),
], ids=["missing", "listed-twice"])
def test_a_page_missing_from_or_repeated_in_the_sidebar_stops_the_build(tmp_path, sidebar, named):
    nav, out = _site(tmp_path, ["how-to/a", "how-to/b"], sidebar)
    flat = "# Docs\n\n```{toctree}\nhow-to/a\nhow-to/b\n```\n"
    (out / "index.md").write_text(flat, encoding="utf-8")

    with pytest.raises(SystemExit) as stopped:
        _group_index().build_index(nav, out)

    assert named in str(stopped.value)
    assert (out / "index.md").read_text(encoding="utf-8") == flat
