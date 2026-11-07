"""Regroup the axiom-graph-rendered Guide index into Diataxis track sections.

axiom-graph's multi-target render emits a single FLAT ``{toctree}`` from the
flat ``show:`` list in the nav file. This rewrites the generated ``index.md``
into one *captioned* ``{toctree}`` per sidebar group, so the Sphinx/RTD sidebar
shows Tutorials / How-to / Explanation as sections instead of a flat list.

The groups, their order and any nested pages come from the nav file's
``sidebar:`` key; without one it groups by the first path segment of each
``show`` entry.

Usage (after ``axiom-graph render-site ... --output <out>``):
    poetry run python userdocs/_sphinx/group_index.py \
        --nav docs/consumer/nav-guide.yml --out userdocs/guide-html
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

# Pretty captions per track folder; unknown folders fall back to Title Case.
CAPTIONS = {
    "tutorials": "Tutorials",
    "how-to": "How-to Guides",
    "explanation": "Explanation",
    "reference": "Reference",
}


def inline_index_page(entry: str, out_dir: Path) -> str:
    """Consume the rendered page for *entry* and return its content, with
    relative links rewritten to work from the site root. The standalone page
    is removed so the content exists only at the root (no duplicate/orphan);
    render-site regenerates it on the next regen, before this script runs."""
    page = out_dir / f"{entry}.md"
    text = page.read_text(encoding="utf-8").strip()
    up = "../" * entry.count("/")
    if up:
        text = text.replace(f"]({up}", "](").replace(f'href="{up}', 'href="')
        for directive in ("figure", "image"):
            text = text.replace(f"{{{directive}}} {up}", f"{{{directive}}} ")
    page.unlink()
    if not any(page.parent.iterdir()):
        page.parent.rmdir()
    return text


NEST_MARKER = "<!-- group_index: nested pages -->"


def caption_for(entry: str) -> str:
    """The sidebar caption for *entry*'s folder (Title Case when unknown)."""
    track = entry.split("/", 1)[0] if "/" in entry else ""
    return CAPTIONS.get(track, track.replace("-", " ").title() or "Pages")


def sidebar_groups(nav: dict, show: list[str], index_page: str | None
                   ) -> tuple[dict[str, list[str]], dict[str, dict]]:
    """Return (caption -> top-level pages, parent -> its nested pages), from the
    nav file's ``sidebar:`` key, or one group per folder when it has none.

    A parent's entry is ``{"pages": [...], "numbered": bool, "extra": [...]}``:
    ``pages`` are its children, numbered 1, 2, ... when ``numbered`` is set;
    ``extra`` are unnumbered pages listed after them.
    """
    sidebar = nav.get("sidebar")
    nested: dict[str, dict] = {}
    if not sidebar:
        groups: dict[str, list[str]] = {}
        for entry in show:
            groups.setdefault(caption_for(entry), []).append(entry)
        return groups, nested

    placed: list[str] = [index_page] if index_page else []

    def page_of(item) -> str:
        page = item if isinstance(item, str) else item.get("page") if isinstance(item, dict) else None
        if not page:
            raise SystemExit(f"sidebar: entry has no page: {item!r}")
        return page

    def walk(item) -> str:
        """Record *item* and, recursively, its nested pages; return its page."""
        page = page_of(item)
        placed.append(page)
        if isinstance(item, dict) and (item.get("children") or item.get("extra")):
            nested[page] = {
                "pages": [walk(child) for child in item.get("children") or []],
                "numbered": bool(item.get("numbered")),
                "extra": [walk(child) for child in item.get("extra") or []],
            }
        return page

    groups = {}
    for group in sidebar:
        if not isinstance(group, dict) or not group.get("caption"):
            raise SystemExit(f"sidebar: group has no caption: {group!r}")
        tops = groups.setdefault(group["caption"], [])
        tops.extend(walk(item) for item in group.get("pages") or [])
    problems = []
    dupes = sorted({p for p in placed if placed.count(p) > 1})
    if dupes:
        problems.append(f"listed more than once: {dupes}")
    missing = [p for p in show if p not in placed]
    if missing:
        problems.append(f"show entries missing from the sidebar: {missing}")
    if problems:
        raise SystemExit("sidebar: " + "; ".join(problems))
    unrendered = [p for p in placed if p != index_page and p not in show]
    if unrendered:
        print(f"warning: sidebar pages not in show (not rendered from DocJSON): {unrendered}")
    return groups, nested


def nest_children(nested: dict[str, dict], out_dir: Path) -> None:
    """Write each parent page's nested pages as hidden toctrees at its end, so
    the sidebar shows them under it: a numbered one for its children when
    ``numbered`` is set, then an unnumbered one for its extras. A block left by
    an earlier run is replaced."""
    for parent, spec in nested.items():
        page = out_dir / f"{parent}.md"
        if not page.exists():
            raise SystemExit(f"sidebar: parent page {parent} has no rendered file {page}")
        text = page.read_text(encoding="utf-8").split(NEST_MARKER)[0].rstrip()
        block = ["", NEST_MARKER]
        for pages, numbered in ((spec["pages"], spec["numbered"]), (spec["extra"], False)):
            if pages:
                block += ["```{toctree}", ":hidden:"] + ([":numbered: 1"] if numbered else []) + [""]
                block += [f"/{k}" for k in pages] + ["```", ""]
        page.write_text(text + "\n" + "\n".join(block), encoding="utf-8")


def build_index(nav_path: Path, out_dir: Path) -> Path:
    nav = yaml.safe_load(nav_path.read_text(encoding="utf-8")) or {}
    index_page = nav.get("index_page")
    show = [e for e in (nav.get("show", []) or []) if e != index_page]

    groups, nested = sidebar_groups(nav, show, index_page)
    nest_children(nested, out_dir)

    if index_page:
        lines = [inline_index_page(index_page, out_dir), ""]
        # List the landing page itself in the sidebar under its original
        # track caption, so it stays findable in the nav ('self' is Sphinx's
        # reference to the containing document).
        lines += ["```{toctree}", f":caption: {caption_for(index_page)}", ":hidden:", "", "self", "```", ""]
    else:
        lines = [f"# {nav.get('site_name', 'Guide')}", ""]
    for caption, entries in groups.items():
        lines += ["```{toctree}", f":caption: {caption}", ":maxdepth: 2"]
        if index_page:
            # The inlined index page carries the body content (including its
            # own curated links); the toctrees only need to feed the sidebar.
            lines += [":hidden:"]
        lines += [""]
        lines += entries
        lines += ["```", ""]

    index = out_dir / "index.md"
    index.write_text("\n".join(lines), encoding="utf-8")
    print(f"Regrouped {len(show)} pages into {len(groups)} captioned toctree(s) -> {index}")
    return index


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--nav", default="docs/consumer/nav-guide.yml")
    ap.add_argument("--out", default="userdocs/guide-html")
    args = ap.parse_args()
    build_index(Path(args.nav), Path(args.out))


if __name__ == "__main__":
    main()
