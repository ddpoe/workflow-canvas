"""Regroup the axiom-graph-rendered Guide index into Diataxis track sections.

axiom-graph's multi-target render emits a single FLAT ``{toctree}`` from the
flat ``show:`` list in the nav file. This rewrites the generated ``index.md``
into one *captioned* ``{toctree}`` per track (folder), so the Sphinx/RTD sidebar
shows Tutorials / How-to / Explanation as nested sections instead of a flat list.

It groups by the first path segment of each ``show`` entry and preserves order,
so it stays in sync with the nav file automatically.

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


def build_index(nav_path: Path, out_dir: Path) -> Path:
    nav = yaml.safe_load(nav_path.read_text(encoding="utf-8")) or {}
    index_page = nav.get("index_page")
    show = [e for e in (nav.get("show", []) or []) if e != index_page]

    groups: dict[str, list[str]] = {}
    for entry in show:
        track = entry.split("/", 1)[0] if "/" in entry else ""
        groups.setdefault(track, []).append(entry)

    if index_page:
        lines = [inline_index_page(index_page, out_dir), ""]
        # List the landing page itself in the sidebar under its original
        # track caption, so it stays findable in the nav ('self' is Sphinx's
        # reference to the containing document).
        track = index_page.split("/", 1)[0] if "/" in index_page else ""
        caption = CAPTIONS.get(track, track.replace("-", " ").title() or "Pages")
        lines += ["```{toctree}", f":caption: {caption}", ":hidden:", "", "self", "```", ""]
    else:
        lines = [f"# {nav.get('site_name', 'Guide')}", ""]
    for track, entries in groups.items():
        caption = CAPTIONS.get(track, track.replace("-", " ").title() or "Pages")
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
