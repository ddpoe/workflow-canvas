"""The sample registration manifest: an optional YAML file of sample metadata.

A manifest is a YAML mapping.  The only key it may carry is ``description``,
a string.  Metadata is never identity: the description is stored on the
sample row and never enters the content hash or a run's cache key.

Registration owns this reader.  Surfaces pass a manifest path (the CLI's
``--manifest``) or a plain description string (the canvas route); both are
validated here, in registration's refusal step, before anything is cached.
"""

from __future__ import annotations

from pathlib import Path

#: The keys a sample manifest may carry.
MANIFEST_KEYS: frozenset[str] = frozenset({"description"})


class SampleManifestError(ValueError):
    """A sample manifest, or a description, that registration refuses.

    Attributes:
        path: The manifest file, or None for a description passed directly.
        reason: What is wrong with it, in one sentence.
    """

    def __init__(self, reason: str, path: Path | None = None):
        self.path = path
        self.reason = reason
        where = f"sample manifest {path}" if path is not None else "sample description"
        super().__init__(f"Invalid {where}: {reason}")


def check_description(description: object, path: Path | None = None) -> str | None:
    """Validate a sample description.

    Args:
        description: The value to validate; None means no description.
        path: The manifest it came from, for the refusal's message.

    Returns:
        The description, or None.

    Raises:
        SampleManifestError: The description is not a string.
    """
    if description is None:
        return None
    if not isinstance(description, str):
        raise SampleManifestError(
            f"'description' must be a string, got {type(description).__name__}",
            path,
        )
    return description


def read_sample_manifest(path: Path | str) -> str | None:
    """Read a sample manifest and return its description.

    Args:
        path: The YAML manifest file.

    Returns:
        The manifest's description, or None when it has none (an empty
        file is a manifest with no keys).

    Raises:
        SampleManifestError: The file is missing or unreadable, is not
            YAML, is not a mapping, carries a key other than
            ``description``, or its description is not a string.
    """
    import yaml

    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SampleManifestError("the file does not exist", path) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise SampleManifestError(f"the file cannot be read ({exc})", path) from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SampleManifestError(f"it is not valid YAML ({exc})", path) from None
    if data is None:
        return None
    if not isinstance(data, dict):
        raise SampleManifestError(
            f"it must be a mapping of keys to values, got {type(data).__name__}",
            path,
        )
    unknown = sorted(str(k) for k in data if k not in MANIFEST_KEYS)
    if unknown:
        raise SampleManifestError(
            f"unknown key(s) {', '.join(repr(k) for k in unknown)}; "
            f"the only key allowed is 'description'",
            path,
        )
    return check_description(data.get("description"), path)
