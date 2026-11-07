"""Registration's listing: the registered methods, modules and samples.

The one owner of what the Registry lists. The canvas's registry routes and
the CLI's list verbs both read these functions, so they list the same
entities with the same fields. Run counts are not Registration's (it never
reads ``Run`` rows): each listed method, version and sample keeps the row id
or name a caller merges its counts on.

- :func:`list_methods`: every registered method with its env, its versions
  (label order, the newest last) and its registered ``method.yaml``. It is
  not read-only: it writes any missing ``MethodVersion`` rows first
  (``sync_version_rows``), as the registry route it moved from did.
- :func:`list_modules`: every module with its contracts, its method count and
  its stored colour. It reads the colour store and never writes it; a module
  with no stored colour lists ``None`` (the canvas assigns the defaults
  first, as its own step).
- :func:`list_samples`: every registered sample, by name.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from axiom_annotations import task
from sqlmodel import col, select

from ..persistence import Module, Sample, get_session
from ..persistence import project_root as get_project_root
from .method_version import VersionEntry
from .module_color import read_module_colors
from .registered_code import MalformedMethodRecordError, resolve_method_code, sync_version_rows


@dataclass(frozen=True)
class ListedVersion:
    """One version of a registered method, as the Registry lists it.

    Attributes:
        label: The version number (1 for v1).
        identity: The version's content identity (what a pin holds).
        registered_at: The registration time, ISO 8601 UTC.
        env: The env the version runs in.
        newest: Whether it is the method's newest version.
        commit: The registration commit its row records, or ``None`` for a
            version this database did not register (a pull, a fresh clone).
        env_fingerprint: The env's fingerprint at registration.
        image: The env's recorded image reference at registration.
        code_tree: The git tree hash of the version's snapshot.
        version_id: Its ``MethodVersion`` row id (a caller's run-count key).
    """

    label: int
    identity: str
    registered_at: str
    env: str
    newest: bool
    commit: str | None
    env_fingerprint: str
    image: str
    code_tree: str
    version_id: int

    @classmethod
    def from_entry(cls, entry: VersionEntry, version_id: int, commit: str | None,
                   newest: bool) -> ListedVersion:
        """Build the listed version of one version-list entry.

        Args:
            entry: The version-list entry.
            version_id: Its ``MethodVersion`` row id.
            commit: The registration commit its row records, or ``None``.
            newest: Whether it is the newest version.

        Returns:
            The listed version.
        """
        return cls(label=entry.label, identity=entry.identity,
                   registered_at=entry.registered_at, env=entry.env_name,
                   newest=newest, commit=commit,
                   env_fingerprint=entry.env_fingerprint, image=entry.image_ref,
                   code_tree=entry.tree_hash, version_id=version_id)


@dataclass(frozen=True)
class ListedMethod:
    """One registered method, as the Registry lists it.

    Attributes:
        module: Its module's name.
        name: The method's name.
        env: The newest version's env; the method row's env when it has no
            version list.
        versions: Every version in label order, the newest last; empty for
            a method whose record the resolver refuses.
        source: The project-relative POSIX path of its registered copy's
            ``method.yaml``, or ``None`` when the resolver refuses the record.
        method_id: Its ``Method`` row id (a caller's run-count key).
    """

    module: str
    name: str
    env: str
    versions: tuple[ListedVersion, ...]
    source: str | None
    method_id: int


@dataclass(frozen=True)
class ListedContract:
    """One module-level contract.

    Attributes:
        type: ``output`` (a required file) or ``metric`` (a metric key).
        name: The artifact or metric name.
        value_type: Its extension or value type, or ``None``.
        required: Whether every method of the module must satisfy it.
    """

    type: str
    name: str
    value_type: str | None
    required: bool


@dataclass(frozen=True)
class ListedModule:
    """One registered module, as the Registry lists it.

    Attributes:
        name: The module's name.
        description: Its description (empty when it has none).
        contracts: Its module-level contracts.
        methods: How many methods it holds.
        source: Its ``module.yaml``, project-relative.
        color: Its stored colour (``#rrggbb``), or ``None`` when none is
            stored yet.
    """

    name: str
    description: str
    contracts: tuple[ListedContract, ...]
    methods: int
    source: str
    color: str | None


@dataclass(frozen=True)
class ListedSample:
    """One registered sample, as the Registry lists it.

    Attributes:
        name: The sample's name (a caller's run-count key).
        source: The path it was registered from.
        size: Its size in bytes (a directory's files summed), or ``None``.
        hash: Its content hash, or ``None``.
        pushed: Whether a content hash is present (its bytes are in the
            cache and pushable to the remote).
        registered_at: The registration time in ISO 8601, or ``None``.
        file_type: ``directory`` or the file's type.
    """

    name: str
    source: str
    size: int | None
    hash: str | None
    pushed: bool
    registered_at: str | None
    file_type: str


def _listed_versions(module: str, method: str, root: Path) -> tuple[ListedVersion, ...]:
    """Every version of a method in label order; none when its record is refused."""
    try:
        versions = sync_version_rows(module, method, root)
    except MalformedMethodRecordError:
        return ()
    newest = len(versions) - 1
    return tuple(ListedVersion.from_entry(v.entry, v.version_id, v.git_commit,
                                          index == newest)
                 for index, v in enumerate(versions))


def _registered_yaml(module: str, method: str, root: Path) -> str | None:
    """The project-relative ``method.yaml`` of a method's registered copy, or ``None``."""
    try:
        code = resolve_method_code(module, method, root)
    except MalformedMethodRecordError:
        return None
    return (code.snapshot_dir.resolve() / "method.yaml").relative_to(root).as_posix()


@task(purpose="List every registered method with its env, its versions and its "
              "registered method.yaml, in module registration order",
      inputs="project root (the resolved one when omitted)",
      outputs="list[ListedMethod]")
def list_methods(root: Path | None = None) -> list[ListedMethod]:
    """List every registered method, its versions and its registered ``method.yaml``.

    Reads each method's versions through :func:`sync_version_rows` (so the
    rows follow the committed version list) and its source through the code
    resolver.

    Args:
        root: The project root; defaults to the resolved project root.

    Returns:
        One :class:`ListedMethod` per method, modules in registration order.
    """
    root = (root or get_project_root()).resolve()
    with get_session() as session:
        rows = [(mod.name, meth.name, meth.env, meth.id)
                for mod in session.exec(select(Module).order_by(col(Module.id))).all()
                for meth in mod.methods]
    listed: list[ListedMethod] = []
    for module, name, env, method_id in rows:
        versions = _listed_versions(module, name, root)
        listed.append(ListedMethod(
            module=module, name=name,
            env=versions[-1].env if versions else env,
            versions=versions,
            source=_registered_yaml(module, name, root),
            method_id=method_id))
    return listed


def listed_module(mod: Module, color: str | None) -> ListedModule:
    """Build the listed form of one module row (read inside its session).

    Args:
        mod: The module row, with its contracts and methods loadable.
        color: Its stored colour, or ``None``.

    Returns:
        The listed module.
    """
    return ListedModule(
        name=mod.name,
        description=mod.description or "",
        contracts=tuple(ListedContract(type=c.contract_type, name=c.name,
                                       value_type=c.value_type, required=c.required)
                        for c in mod.contracts),
        methods=len(mod.methods),
        source=f"modules/{mod.name}/module.yaml",
        color=color)


@task(purpose="List every registered module with its contracts, its method count "
              "and its stored colour, in registration order; never writes the "
              "colour store",
      inputs="project root (the resolved one when omitted)",
      outputs="list[ListedModule]")
def list_modules(root: Path | None = None) -> list[ListedModule]:
    """List every registered module, in registration order.

    Args:
        root: The project root; defaults to the resolved project root.

    Returns:
        One :class:`ListedModule` per module.

    Raises:
        ValueError: When the colour store is malformed.
    """
    root = (root or get_project_root()).resolve()
    colors = {name: entry.color for name, entry in read_module_colors(root).items()}
    with get_session() as session:
        return [listed_module(mod, colors.get(mod.name))
                for mod in session.exec(select(Module).order_by(col(Module.id))).all()]


@task(purpose="List every registered sample by name with its source, size, hash "
              "and registration time",
      inputs="project root (the resolved one when omitted)",
      outputs="list[ListedSample]")
def list_samples(root: Path | None = None) -> list[ListedSample]:
    """List every registered sample, by name.

    Takes the project root as :func:`list_methods` and :func:`list_modules`
    do; the sample rows are the project database's, which the process opened
    for that root.

    Args:
        root: The project root; defaults to the resolved project root.

    Returns:
        One :class:`ListedSample` per sample row.

    Raises:
        RuntimeError: No root is given and no project is found.
    """
    if root is None:
        get_project_root()
    with get_session() as session:
        return [
            ListedSample(
                name=s.name, source=s.source_path, size=s.file_size,
                hash=s.content_hash, pushed=s.content_hash is not None,
                registered_at=s.registered_at.isoformat() if s.registered_at else None,
                file_type=s.file_type)
            for s in session.exec(select(Sample).order_by(Sample.name)).all()]
