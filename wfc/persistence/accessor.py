r"""The project-root accessor.

:func:`project_root` answers which project this process works in. The rule is
Layout's (:func:`wfc.layout.resolve_project_root`); this module owns the two
process reads the rule takes, the ``WFC_PROJECT_ROOT`` environment variable
and the working directory, and caches the answer for the life of the process.

Resolution is explicit rather than taken from the working directory at each
call. That keeps a subprocess whose working directory is not the project on
the project its environment names: a Snakemake shell rule on a Windows UNC
path, for one, where cmd.exe rewrites the working directory to
``C:\Windows\``.
"""

import os
from pathlib import Path

from .. import layout

_project_root_cache: Path | None = None


def project_root() -> Path:
    """Resolve the wfc project root directory.

    Precedence:
      1. ``WFC_PROJECT_ROOT`` environment variable, if set (must point at a
         directory containing ``.wfc/wf-canvas.toml``).
      2. Walk upward from ``Path.cwd()`` looking for a ``.wfc/wf-canvas.toml``
         marker.
      3. Raise ``RuntimeError`` — do not silently create ``.wfc/`` in a wrong
         directory.

    Result is cached per-process. Call ``reset_engine()`` to clear the cache
    in tests.

    Returns:
        The resolved project root.
    """
    global _project_root_cache
    if _project_root_cache is not None:
        return _project_root_cache

    # The rule lives in wfc.layout; this function owns the process reads
    # (environment, cwd) and the per-process cache.
    _project_root_cache = layout.resolve_project_root(
        os.environ.get(layout.ROOT_ENV_VAR),
        Path.cwd(),
    )
    return _project_root_cache


def clear_cache() -> None:
    """Forget the cached project root, so the next call resolves it again."""
    global _project_root_cache
    _project_root_cache = None


def swap_cache(root: Path | None) -> Path | None:
    """Replace the cached project root and return the one it held.

    :func:`wfc.persistence.use_project` pins another project's root with this
    for the length of a block, then puts the old answer back.

    Args:
        root: The root to cache, or ``None`` to resolve again on the next call.

    Returns:
        The previously cached root, or ``None`` if nothing was cached.
    """
    global _project_root_cache
    previous = _project_root_cache
    _project_root_cache = root
    return previous
