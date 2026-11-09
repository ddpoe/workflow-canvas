"""What registration tells the user it did not do, and where to report a gap."""

from __future__ import annotations

from collections.abc import Sequence

from .. import release


def issues_url() -> str:
    """Return the project's issues URL from the installed package's metadata.

    Returns:
        The ``Issues`` project URL, or the repository's issues page when the
        metadata does not name one (Project git's :func:`wfc.release.issues_url`).
    """
    return release.issues_url()


def not_copied_message(names: Sequence[str]) -> str:
    """Return the advice registration prints for the files it did not copy.

    Args:
        names: Source-relative POSIX paths of the files left out.

    Returns:
        The message naming each file, or ``""`` when *names* is empty.
    """
    if not names:
        return ""
    listed = ", ".join(f"`{name}`" for name in names)
    return (
        f"Not registered (only scripts, `method.yaml` and declared helpers are "
        f"copied): {listed}. If your method reads one of these at run time, "
        f"register it as a sample and wire it as an input. If that doesn't "
        f"fit your case, please open an issue: {issues_url()}."
    )
