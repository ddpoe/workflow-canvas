"""Declared vocabulary values: the names the system and method code share.

Every value here is data, not a path and not an execution decision. The
module imports nothing from ``wfc`` and nothing outside the stdlib, so any
backend site can read a vocabulary value without creating a cycle.
"""

from __future__ import annotations

# =============================================================================
# Collapsed-sample sentinel
# =============================================================================
#
# The sample identity a sample-collapsed (fan-in) step runs at.  It is an
# identity value, not a path: it is written to the run row's ``sample``
# column, baked into the sentinel path segment, frozen into the generated
# Snakefile's ``--sample`` argv, and enters the cache key.  Every backend
# site that decides or compares the collapsed sample reads this name; the
# generated Snakefile still emits the literal text.  Parked here because
# this package imports nothing from ``wfc`` and is already imported by
# generation, dispatch and the canvas server; permanent ownership is the
# Contracts unit's decision.
COLLAPSED_SAMPLE = "__all__"


# =============================================================================
# The method-facing WFC_* vocabulary
# =============================================================================
#
# The names a method script may read from its environment, and the only
# host-side values dispatch forwards into the container (as ``-e`` flags).
# ``WFC_INPUT_PATHS`` is present only when the node has inputs; nothing
# else from the host environment -- ``PYTHONPATH`` in particular -- crosses
# the container barrier. Dispatch assembles the values; this is the set of
# names.
WFC_ENV_VARS = (
    "WFC_RUN_ID", "WFC_RUN_DIR", "WFC_SAMPLE", "WFC_PARAMS",
    "WFC_NODE_ID", "WFC_PIPELINE_ID", "WFC_VARIANT", "WFC_INPUT_PATHS",
)


# =============================================================================
# Param-type vocabulary
# =============================================================================
#
# The closed set of types a method parameter may declare.  ``any`` is the
# explicit opt-out: the value is not checked against a type.  The set is the
# one the parameter editor renders, so a stored contract carries only these
# spellings and nothing that displays today stops displaying.
PARAM_TYPES = ("str", "int", "float", "bool", "list", "dict", "any")

# Accepted spellings of a canonical type.  Lookup is case-insensitive, so
# ``String``, ``Boolean`` and ``DICT`` resolve without being listed.  The
# parser normalises an accepted spelling to its canonical name and writes
# the canonical name back, so the stored contract and everything downstream
# -- the editor, ``WFC_PARAMS`` -- see only canonical names.  This table is
# the only copy; an addition goes here and nowhere else.
PARAM_TYPE_ALIASES = {
    "string": "str",
    "text": "str",
    "integer": "int",
    "number": "float",
    "double": "float",
    "boolean": "bool",
    "dictionary": "dict",
    "map": "dict",
    "mapping": "dict",
    "object": "dict",
}
