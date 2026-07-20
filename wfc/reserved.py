"""Reserved-name guard for the ``__demo__`` namespace.

``wfc demo`` populates a project with entities whose names carry the
``__demo__`` prefix (module ``__demo__``, samples ``__demo__ctrl_01`` ...,
env ``__demo__env``). ``wfc demo --remove`` later deletes by that tag, so
the tag must be PROOF of demo ownership: no user-driven registration path
may ever create a ``__demo__``-prefixed name. This module is the single
guard seam — the library-level registration functions
(:func:`wfc.register.register_module`, :func:`wfc.register.register_method`,
:func:`wfc.cli.register_sample`, :func:`wfc.envs.register`) all call
:func:`check_reserved_name` by default, which covers both the CLI commands
and the Canvas Registry endpoints that wrap them. The demo itself passes
``allow_reserved=True``.
"""

RESERVED_DEMO_PREFIX = "__demo__"


def check_reserved_name(name: str, kind: str, allow_reserved: bool = False) -> None:
    """Reject *name* when it uses the reserved ``__demo__`` prefix.

    Args:
        name: The user-supplied entity name to check.
        kind: Human-readable entity kind for the error message
            (e.g. ``"module"``, ``"method"``, ``"sample"``, ``"env"``).
        allow_reserved: Explicit opt-in used only by ``wfc demo`` itself.
            When ``True``, the check is skipped entirely.

    Raises:
        ValueError: If *name* starts with ``__demo__`` and
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
