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


def build_index(nav_path: Path, out_dir: Path) -> Path:
    nav = yaml.safe_load(nav_path.read_text(encoding="utf-8")) or {}
    show = nav.get("show", []) or []

    groups: dict[str, list[str]] = {}
    for entry in show:
        track = entry.split("/", 1)[0] if "/" in entry else ""
        groups.setdefault(track, []).append(entry)

    lines = [f"# {nav.get('site_name', 'Guide')}", ""]
    for track, entries in groups.items():
        caption = CAPTIONS.get(track, track.replace("-", " ").title() or "Pages")
        lines += ["```{toctree}", f":caption: {caption}", ":maxdepth: 2", ""]
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
