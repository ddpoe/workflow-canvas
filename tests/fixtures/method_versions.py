"""Give a registered method more versions through Registration's own register.

A test that needs a method with several registered versions (v1..v3, say)
gets them the way a user would: the method's script changes on disk and
the method is registered again, once per version. Registration decides
that the changed script is a new version and writes the version list.

Pins: each new version's change is one comment line appended to the
method's script in its source folder; registration confirmed
(``assume_yes``), as every programmatic caller does.
Does not prove: ``wfc register-method``'s argument parsing or its prompt.
"""

from __future__ import annotations

import os
from pathlib import Path


def register_versions(root: Path, module: str, method: str, method_dir: Path,
                      count: int) -> list[str]:
    """Register the method again until it has *count* registered versions.

    Each pass appends a comment line to ``<method_dir>/<method>.py`` and
    calls :func:`wfc.registration.register_method`, the function behind
    ``wfc register-method``, from the project root (registration commits
    from the current directory's repository).

    Args:
        root: The project root.
        module: The method's module, already registered.
        method: The method, registered at least once from *method_dir*.
        method_dir: The method's source folder (the one it was registered
            from).
        count: The number of registered versions the method ends with.

    Returns:
        The registered version labels afterwards, oldest first.

    Raises:
        AssertionError: A registration after a script change added no
            version.
    """
    from wfc.registration import read_version_list, register_method

    def labels() -> list[str]:
        return [str(e.label) for e in read_version_list(root, module, method) or []]

    script = Path(method_dir) / f"{method}.py"
    prev_cwd = os.getcwd()
    try:
        os.chdir(root)
        while (have := len(labels())) < count:
            with script.open("a", encoding="utf-8") as fh:
                fh.write(f"# version {have + 1}\n")
            register_method(method_dir=Path(method_dir), module_name=module,
                            method_name=method, assume_yes=True)
            if len(labels()) == have:
                raise AssertionError(f"registering {module}.{method} after a "
                                     f"script change added no version")
    finally:
        os.chdir(prev_cwd)
    return labels()
