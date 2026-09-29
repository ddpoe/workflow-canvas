"""The env-spec grammar and the image-reference grammar.

An env spec — the value a ``method.yaml`` carries on ``env``, the value
stored on ``Method.env``, the value a pipeline node carries — is the name
of a registered env. One character rule decides what a name is; two entry
points apply it. The write side (method registration, ``register-env``)
applies the rule alone. The read side (dispatch, the claim step, the
canvas, the delete-env query) strips a legacy ``container:`` prefix once
and then applies the rule, so contract rows and frozen pipeline documents
written before the prefix was withdrawn keep resolving.

The image-reference grammar: one host/path/tag/digest core, two acceptors.

Two questions are asked of an image reference, and they have different
answers on purpose:

- What may be *stored* in the env manifest (and reach a cache key): the
  scheme, a host and path, and a digest. A floating tag is refused, so two
  different images can never masquerade as the same reference.
- What may be *typed* at ``wfc register-env`` for a bring-your-own image: a
  host and path, then a tag, a digest, or neither. The registrar resolves
  the digest against the daemon and refuses a supplied digest that
  disagrees, so the stored form is always the pinned one.

Both productions share one host/path core; neither accept/reject set is a
policy of this module — they are the manifest's and the registrar's, held
here so the grammar has one owner.
"""

from __future__ import annotations

import re

_IMAGE_HOST = r"(?P<host>[a-z0-9][a-z0-9.\-]*(?::\d+)?)"  # host[:port]
_IMAGE_DIGEST = r"@sha256:(?P<digest>[a-f0-9]{64})"

#: What may be stored: ``docker://<host>[:<port>]/<path>@sha256:<hex64>``.
_DIGEST_PINNED_REF = re.compile(
    r"^docker://"
    + _IMAGE_HOST
    + r"/(?P<path>[a-z0-9][a-z0-9._/\-]*)"            # repository path
    + _IMAGE_DIGEST
    + r"$"
)

#: What may be typed for a bring-your-own image: the path, then an optional
#: tag, an optional digest, or neither. The path is matched lazily so a tag
#: is not swallowed into it.
_BYO_INPUT_REF = re.compile(
    r"^docker://"
    + _IMAGE_HOST
    + r"/(?P<path>[a-z0-9][a-z0-9._/\-]*?)"
    + r"(?::(?P<tag>[a-zA-Z0-9._\-]+))?"
    + r"(?:" + _IMAGE_DIGEST + r")?$"
)


def validate_container_ref(ref: str) -> None:
    """Validate that *ref* is a digest-pinned ``docker://`` reference.

    Accepted shape::

        docker://<host>[:<port>]/<path>@sha256:<64-hex-digest>

    Rejects floating tags (``:latest``, ``:v1``), missing schemes, and
    anything else that would let two different images masquerade as the
    same ref. Only digest-pinned refs are allowed so cache keys and method
    registrations remain reproducible.

    Args:
        ref: The image reference to check.

    Raises:
        ValueError: If *ref* is not a digest-pinned ``docker://`` reference.
            The error message names the rejected ref and points at the
            required shape.
    """
    if not isinstance(ref, str) or not ref:
        raise ValueError(
            f"Container reference must be a non-empty string, got {ref!r}"
        )
    if not _DIGEST_PINNED_REF.match(ref):
        raise ValueError(
            f"Container reference {ref!r} is not digest-pinned. "
            f"Expected shape: docker://<host>/<path>@sha256:<hex64>. "
            f"Floating tags (e.g. :latest, :v1) are rejected: every "
            f"container ref must be digest-pinned so the image identity "
            f"is recoverable across machines."
        )


def parse_byo_ref(ref: str) -> tuple[str, str | None, str | None]:
    """Split a BYO ``docker://`` input ref into its image, tag and digest.

    Args:
        ref: The reference as typed: ``docker://<host>/<path>[:<tag>]
            [@sha256:<hex>]``.

    Returns:
        ``(image_prefix, tag_or_None, digest_or_None)`` where
        ``image_prefix`` is ``<host>/<path>`` with the scheme, tag and
        digest stripped — the daemon-resolvable ref, and the prefix the
        registrar assembles the final manifest value from as
        ``docker://<prefix>@sha256:<digest>``.

    Raises:
        ValueError: If *ref* does not carry the ``docker://`` scheme or is
            not a valid reference of that shape.
    """
    if not isinstance(ref, str) or not ref.startswith("docker://"):
        raise ValueError(
            f"BYO image ref {ref!r} must start with 'docker://' scheme."
        )
    m = _BYO_INPUT_REF.match(ref)
    if not m:
        raise ValueError(
            f"BYO image ref {ref!r} is not a valid docker:// reference."
        )
    host = m.group("host")
    path = m.group("path")
    tag = m.group("tag")
    digest = m.group("digest")
    image_prefix = f"{host}/{path}"
    return image_prefix, tag, digest


# =============================================================================
# The env-spec grammar
# =============================================================================

#: The one rule, as the refusals state it. Registration appends the options
#: only it can see (the registered names).
ENV_NAME_RULE = "an env name uses letters, digits, `_` and `-` only"
#: The one authoring form.
ENV_SPEC_FORM = "a method.yaml declares `env: <name>` where <name> is a registered env"
_ENV_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_LEGACY_PREFIX = "container:"


def is_env_name(value: object) -> bool:
    """Return whether *value* is an env name under the one rule.

    Args:
        value: The candidate.

    Returns:
        ``True`` for a non-empty string of letters, digits, ``_`` and ``-``.
    """
    return isinstance(value, str) and bool(_ENV_NAME.match(value))


def validate_env_name(value: object) -> str:
    """The write-side entry point: the name rule alone.

    Method registration and ``register-env`` call this; neither accepts a
    prefix, a scheme, a digest or any other decoration.

    Args:
        value: The value as given.

    Returns:
        The value, unchanged, when it is an env name.

    Raises:
        ValueError: Naming the value given and stating the rule.
    """
    if not is_env_name(value):
        raise ValueError(f"env name {value!r} is not valid: {ENV_NAME_RULE}.")
    return value  # type: ignore[return-value]


def parse_env_spec(value: object) -> str:
    """The read-side entry point: strip a legacy prefix once, then the rule.

    Dispatch, the claim step, the canvas and the delete-env query read
    stored rows and frozen documents through this, so a value written as
    ``container:<name>`` before the prefix was withdrawn still resolves to
    ``<name>``. The strip happens once: ``container:container:x`` is not a
    name, and neither is anything carrying a scheme or a digest.

    Args:
        value: The stored or document value.

    Returns:
        The env name.

    Raises:
        ValueError: Naming the value given and stating the rule.
    """
    if isinstance(value, str) and value.startswith(_LEGACY_PREFIX):
        value = value[len(_LEGACY_PREFIX):]
    return validate_env_name(value)
