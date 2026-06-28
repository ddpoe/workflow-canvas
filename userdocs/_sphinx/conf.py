# Sphinx config for the axiom-graph-rendered consumer Guide.
#
# Source pages are MyST, produced by axiom-graph's multi-target render:
#   poetry run axiom-graph render-site . \
#     --nav docs/consumer/nav-guide.yml --output userdocs/guide-html
# This config compiles them to an RTD-themed HTML site with a {toctree} sidebar:
#   poetry run sphinx-build -c userdocs/_sphinx -b html \
#     userdocs/guide-html userdocs/guide-html/_build/html
# (userdocs/guide-html is a regenerable build artifact and is gitignored.)
# The same flow builds the Reference site via docs/consumer/nav-reference.yml.

project = "Workflow Canvas"
author = "Workflow Canvas"
release = "0.3.0"

extensions = ["myst_parser", "sphinxcontrib.mermaid"]

source_suffix = {".md": "markdown"}
root_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# MyST features used by the axiom-graph renderer (colon-fence admonitions, etc.)
myst_enable_extensions = ["colon_fence", "deflist", "tasklist"]
myst_heading_anchors = 3

html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "navigation_depth": 4,
    "collapse_navigation": False,
    "sticky_navigation": True,
}
