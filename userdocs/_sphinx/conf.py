# Sphinx config for the axiom-graph-rendered consumer docs site (guide
# tracks + reference in one build, so the sidebar and search cover both).
#
# Source pages are MyST, produced by axiom-graph's render target ("guide" in
# axiom-graph.toml -> userdocs/guide), regrouped by group_index.py, then
# compiled to a Sphinx Book themed HTML site:
#   poetry run sphinx-build -c userdocs/_sphinx -b html \
#     userdocs/guide <output-dir>

import os
import sys
from collections import Counter
from pathlib import Path

from docutils import nodes
from sphinx.transforms import SphinxTransform

# The API reference pages document the code in this repository, not an
# installed copy: autodoc reads wfc_client from wfc-client/, and
# sphinx-argparse reads the CLI parser from wfc/cli.py.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(_REPO_ROOT), str(_REPO_ROOT / "wfc-client")]

project = "Workflow Canvas"
author = "Dante Poe"
copyright = "2026, Dante Poe"
release = "0.6.1"

REPO_URL = "https://github.com/ddpoe/workflow-canvas"
PYPI_URL = "https://pypi.org/project/workflow-canvas/"

html_title = "Workflow Canvas"
html_logo = "static/wfc-logo.svg"

extensions = [
    "myst_parser",
    "sphinxcontrib.mermaid",
    "sphinx_sitemap",
    "sphinxext.opengraph",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinxarg.ext",
]

# Docstrings are Google style.
napoleon_google_docstring = True
napoleon_numpy_docstring = False

# Importing wfc.cli (for the CLI reference) loads the database models at
# module level, so userdocs/requirements.txt installs sqlmodel (the only
# third-party package on that import path besides axiom-annotations). The
# models subclass SQLModel with table=True, which autodoc_mock_imports
# cannot stand in for.
autodoc_typehints = "signature"
autodoc_member_order = "bysource"

# Read the Docs sets the canonical URL of the version being built
# (e.g. https://workflow-canvas.readthedocs.io/en/latest/); local builds fall
# back to the default version.
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "https://workflow-canvas.readthedocs.io/en/latest/")
sitemap_url_scheme = "{link}"

ogp_site_url = html_baseurl
ogp_site_name = "Workflow Canvas"
ogp_image = "https://raw.githubusercontent.com/ddpoe/workflow-canvas/main/userdocs/guide/_images/builder-demo-pipeline.png"
ogp_image_alt = "The Workflow Canvas pipeline Builder"
ogp_social_cards = {"enable": False}

LLMS_SUMMARY = (
    "Workflow Canvas (wfc) manages reproducible computational pipelines from the command line "
    "or a browser-based visual Canvas. Methods run in containers (pixi, conda, or bring-your-own), "
    "declare input/output contracts in method.yaml, and are wired into pipelines that compile to "
    "Snakemake. Every run is recorded in a local SQLite database with full lineage, outputs are "
    "stored content-addressed with DVC, and unchanged steps are skipped via a SHA-256 cache key. "
    "Install with `pip install workflow-canvas`. Source: https://github.com/ddpoe/workflow-canvas"
)

source_suffix = {".md": "markdown"}
root_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# MyST features used by the axiom-graph renderer (colon-fence admonitions, etc.)
myst_enable_extensions = ["colon_fence", "deflist", "tasklist"]
myst_heading_anchors = 3

html_theme = "sphinx_book_theme"
html_static_path = ["static"]
templates_path = ["_templates"]
# Copied verbatim to the site root (Google Search Console ownership file).
html_extra_path = ["extra"]
html_css_files = ["custom.css"]
# Dark mode on first visit; the header's theme button switches it and the
# reader's choice is remembered.
html_context = {"default_mode": "dark"}
html_theme_options = {
    # Right-hand "Contents" panel lists the page's sections two levels deep.
    "show_toc_level": 2,
    # The intro page is already listed under the Overview caption that
    # group_index.py writes, so the theme does not add it a second time.
    "home_page_in_toc": False,
    # The logo carries its own wordmark; no project-name text beside it. The
    # light-mode copy has a dark wordmark (the original's is white).
    "logo": {
        "text": "",
        "image_light": "static/wfc-logo-light.svg",
        "image_dark": "static/wfc-logo.svg",
    },
    # No search field in the top bar.
    "navbar_persistent": [],
    # GitHub and PyPI icons under the logo in the left sidebar.
    "icon_links": [
        {"name": "GitHub", "url": REPO_URL, "icon": "fa-brands fa-github"},
        {"name": "PyPI", "url": PYPI_URL, "icon": "fa-brands fa-python"},
    ],
    # Footer: one line with the copyright, license, version and project
    # links. The theme's separate "By <author>" and copyright lines are dropped.
    "footer_content_items": ["extra-footer.html"],
    "extra_footer": (
        f"<p>&copy; 2026 {author}"
        f' &middot; <a href="{REPO_URL}/blob/main/LICENSE">BSD 3-Clause license</a>'
        f" &middot; Version {release}"
        f' &middot; <a href="{REPO_URL}">GitHub</a>'
        f' &middot; <a href="{PYPI_URL}">PyPI</a>'
        f' &middot; <a href="{REPO_URL}/issues">Report an issue</a>'
        f' &middot; <a href="{REPO_URL}/blob/main/CITATION.cff">How to cite</a></p>'
    ),
}
# No search field at the top of the left sidebar (the search icon in the page
# header stays). project-links.html adds a "Project" section under the
# navigation on every page, linking the repository, the PyPI package and the
# issue tracker.
html_sidebars = {
    "**": ["navbar-logo.html", "icon-links.html", "sbt-sidebar-nav.html", "project-links.html"],
}


def _write_search_files(app, exception):
    """Write llms.txt (an index for AI search agents) and robots.txt to the site root.

    Both are derived from the pages this build found, so they track added and
    removed pages without hand edits.
    """
    if exception is not None or app.builder.format != "html":
        return
    env = app.env
    sections: dict[str, list[str]] = {}
    for doc in sorted(env.found_docs):
        if doc == root_doc:
            continue
        section = doc.split("/", 1)[0] if "/" in doc else "other"
        title = env.titles[doc].astext() if doc in env.titles else doc
        sections.setdefault(section, []).append(f"- [{title}]({html_baseurl}{doc}.html)")
    lines = [f"# {project}", "", f"> {LLMS_SUMMARY}", ""]
    for section in sorted(sections):
        lines += [f"## {section.replace('-', ' ').title()}", "", *sections[section], ""]
    outdir = Path(app.outdir)
    (outdir / "llms.txt").write_text("\n".join(lines), encoding="utf-8")
    (outdir / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {html_baseurl}sitemap.xml\n", encoding="utf-8")


class _TidyCliArguments(SphinxTransform):
    """Fit sphinx-argparse output under the CLI reference's command headings.

    sphinx-argparse puts each argument group ("Positional Arguments",
    "Named Arguments") in its own section, which would add two headings per
    command to the page's contents panel. Each group becomes a rubric (a
    label that is not a heading) instead.

    It also shows each argument's help exactly as `wfc <command> --help`
    prints it: without this, smart quotes turn `--remove` into an en dash
    plus a word and curl the quotes in JSON examples. Runs before Sphinx's
    SmartQuotes transform (750) and before the contents panel is collected.
    """

    default_priority = 700

    def apply(self, **kwargs):
        groups = []
        for option_list in self.document.findall(nodes.option_list):
            option_list["support_smartquotes"] = False
            section = option_list.parent
            if isinstance(section, nodes.section) and isinstance(section[0], nodes.title):
                groups.append(section)
        # Each group has an id unique to its command (wfc-init-named-arguments)
        # and one that repeats on every command; only unique ids are kept.
        id_counts = Counter(i for section in groups for i in section["ids"])
        for section in groups:
            ids = [i for i in section["ids"] if id_counts[i] == 1]
            rubric = nodes.rubric("", "", *section[0].children)
            section.replace_self([rubric, *section.children[1:]])
            # replace_self copies the section's ids onto the rubric; reset them.
            rubric["ids"] = ids


def setup(app):
    app.add_transform(_TidyCliArguments)
    app.connect("build-finished", _write_search_files)
