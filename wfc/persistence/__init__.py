"""Persistence: the project's relational store.

- :mod:`.schema` holds the twelve table classes and ``PushStatus``.
- :mod:`.engine` holds the process engine and its sessions;
  :func:`bootstrap_engine`, the one path that builds an engine; and
  :func:`use_project`, which binds another project for a block.
- :mod:`.backfill` holds the additive schema backfill.
- :mod:`.accessor` holds the project-root accessor and its per-process cache.
- :mod:`.config` holds the reader for the project's ``wf-canvas.toml``.

Inside ``wfc`` the package imports only Layout.
"""

from .accessor import project_root
from .backfill import ensure_schema
from .config import read_config
from .engine import (
    bootstrap_engine,
    build_engine,
    database_url,
    get_engine,
    get_session,
    reset_engine,
    use_project,
)
from .schema import (
    Method,
    MethodContract,
    MethodVersion,
    Module,
    ModuleContract,
    ParamDef,
    PushStatus,
    Run,
    RunAnnotation,
    RunInput,
    RunOutput,
    Sample,
    TrackedFunction,
)

__all__ = [
    "Method",
    "MethodContract",
    "MethodVersion",
    "Module",
    "ModuleContract",
    "ParamDef",
    "PushStatus",
    "Run",
    "RunAnnotation",
    "RunInput",
    "RunOutput",
    "Sample",
    "TrackedFunction",
    "bootstrap_engine",
    "build_engine",
    "database_url",
    "ensure_schema",
    "get_engine",
    "get_session",
    "project_root",
    "read_config",
    "reset_engine",
    "use_project",
]
