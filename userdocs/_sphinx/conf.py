# Sphinx config for the axiom-graph-rendered consumer docs site (guide
# tracks + reference in one build, so the sidebar and search cover both).
#
# Source pages are MyST, produced by axiom-graph's render target ("guide" in
# axiom-graph.toml -> userdocs/guide), regrouped by group_index.py, then
# compiled to an RTD-themed HTML site:
#   poetry run sphinx-build -c userdocs/_sphinx -b html \
#     userdocs/guide <output-dir>

project = "Workflow Canvas"
author = "Workflow Canvas"
release = "0.5.0"

html_logo = "static/wfc-logo.svg"

extensions = ["myst_parser", "sphinxcontrib.mermaid"]

source_suffix = {".md": "markdown"}
root_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# MyST features used by the axiom-graph renderer (colon-fence admonitions, etc.)
myst_enable_extensions = ["colon_fence", "deflist", "tasklist"]
myst_heading_anchors = 3

html_theme = "sphinx_rtd_theme"
html_static_path = ["static"]
html_css_files = ["custom.css"]
html_theme_options = {
    "navigation_depth": 4,
    "collapse_navigation": False,
    "sticky_navigation": True,
    # The logo carries its own wordmark; suppress the theme's project-name text.
    "logo_only": True,
}
