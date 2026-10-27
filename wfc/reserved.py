"""Reserved-name guard for the names ``wfc demo`` owns.

``wfc demo`` populates a project with entities whose names carry the
``__demo__`` prefix (module ``__demo__``, samples ``__demo__ctrl_01`` ...,
methods ``__demo__preprocess`` ...) and one env, :data:`DEMO_ENV_NAME`, whose
name has no prefix because Docker must accept it as an image name.
``wfc demo --remove`` later deletes by that tag and by the module cascade, so
those names must be PROOF of demo ownership: no user-driven registration path
may ever create a ``__demo__``-prefixed name or the demo env's name. This
module is the single guard seam — the library-level registration functions
(:func:`wfc.registration.register_module`, :func:`wfc.registration.register_method`,
:func:`wfc.registration.register_sample`, :func:`wfc.environments.register`) all call
:func:`check_reserved_name` by default, which covers both the CLI commands
and the Canvas Registry endpoints that wrap them. The demo itself passes
``allow_reserved=True``.
"""

RESERVED_DEMO_PREFIX = "__demo__"

#: The env ``wfc demo`` registers. It is a name Docker can push (the image
#: ``wfc demo`` builds is ``local/wfc-demo-env:latest``), and it is reserved
#: exactly, as the ``__demo__`` prefix is.
DEMO_ENV_NAME = "wfc-demo-env"


def check_reserved_name(name: str, kind: str, allow_reserved: bool = False) -> None:
    """Reject *name* when it is one ``wfc demo`` owns.

    A name is the demo's when it starts with the ``__demo__`` prefix or is
    exactly :data:`DEMO_ENV_NAME`, whatever the entity kind.

    Args:
        name: The user-supplied entity name to check.
        kind: Human-readable entity kind for the error message
            (e.g. ``"module"``, ``"method"``, ``"sample"``, ``"env"``).
        allow_reserved: Explicit opt-in used only by ``wfc demo`` itself.
            When ``True``, the check is skipped entirely.

    Raises:
        ValueError: If *name* is reserved for ``wfc demo`` and
            ``allow_reserved`` is ``False``.
    """
    if allow_reserved:
        return
    if name and name.startswith(RESERVED_DEMO_PREFIX):
        raise ValueError(
            f"{kind} name {name!r} uses the reserved '{RESERVED_DEMO_PREFIX}' "
            f"prefix. That prefix is reserved for `wfc demo`, whose teardown "
            f"(`wfc demo --remove`) deletes every '{RESERVED_DEMO_PREFIX}' "
            f"entity — a user entity with that name would be destroyed with "
            f"it. Choose a different name."
        )
    if name == DEMO_ENV_NAME:
        raise ValueError(
            f"{kind} name {name!r} is reserved for `wfc demo`, whose teardown "
            f"(`wfc demo --remove`) deletes it — a user entity with that name "
            f"would be destroyed with it. Choose a different name."
        )
