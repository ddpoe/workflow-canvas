"""The shared tail every generated Dockerfile ends with.

Each backend generator renders its recipe as a list of lines and hands it
to :func:`finish_recipe`, which appends :func:`recipe_tail` and joins the
result. Instructions every wfc-built image should carry go in the tail, last
in the file, so adding one reuses every layer above it from the build cache.
"""

from __future__ import annotations


def recipe_tail() -> list[str]:
    """Return the lines every generated Dockerfile ends with.

    Each instruction is followed by a blank line, the generators'
    convention, so the rendered file still ends with a newline. There are
    none today.

    Returns:
        The tail's lines, possibly empty.
    """
    return []


def finish_recipe(lines: list[str]) -> str:
    """Append the shared tail to a generator's recipe and render it.

    Args:
        lines: The backend's recipe lines, ending with a blank line.

    Returns:
        The Dockerfile text: the recipe, then the tail, joined with
        newlines.
    """
    return "\n".join([*lines, *recipe_tail()])
