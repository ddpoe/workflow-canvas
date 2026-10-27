"""The env a node runs in, and the pre-flights that make it runnable.

One rule names a node's env, and dispatch and both pre-flights ask it: the
env the node's method is registered in (``Method.env``, read through
Registration), the same env its cache key is over. A pipeline
document's ``env`` for the node and the registered copy's ``method.yaml``
are not read: a document naming the env of an earlier registration, or a
hand edit of the copy, changes nothing that runs. The value goes through
the one grammar (``parse_env_spec``) and the name is looked up in the
project's env manifest; only a record with a container image counts.

The pre-flights ask Environments' run-time rule
(``ensure_version_runnable``) for each version's image: the image on this
machine runs as it is, a missing one is pulled by its pushed (or byo
registry) digest and its content checked, and anything else is refused
before any run row. ``run_pipeline`` asks once per distinct image before
the run records its commit, and a standalone ``run-step`` for its one image
before Claim; nothing is ever built. Dispatch asks the rule's no-network
half (``version_run_ref``) for the same ref and never pulls. Before that,
each asks Environments whether the version's ``(env, env_fingerprint)`` has
a captures folder, and refuses one that does not (dispatch takes the
interpreter from it), naming ``wfc register-method``. A node whose env does
not resolve to a container record is left to dispatch's own no-container
refusal.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from axiom_annotations import Step, task

from ..environments import EnvRecord, ensure_version_runnable


@dataclass(frozen=True)
class NodeEnv:
    """A node's env value, resolved against the env manifest.

    Attributes:
        value: The env value as registered (the method's ``Method.env``);
            ``""`` when there is none.
        name: The env name the value parses to; ``""`` when the value is
            empty or outside the grammar.
        record: The manifest record for ``name`` when it has a container
            image; otherwise ``None``.
        error: The grammar's message for a value it refuses; otherwise
            ``""``.
    """

    value: str
    name: str
    record: EnvRecord | None
    error: str


def node_env_value(module_name: str | None, method_name: str | None,
                   version: str | None = None) -> str:
    """Return the env value a node runs in: its method version's env.

    Args:
        module_name: The method's module, or ``None`` when not known.
        method_name: The method's name, or ``None`` when not known.
        version: The node's version identity; ``None`` is the newest.

    Returns:
        The env the version's list entry records, or ``""`` when the
        method is not known or not registered, or the version is unknown
        (the claim reports that refusal).
    """
    entry = _node_version_entry(module_name, method_name, version)
    return entry.env_name if entry is not None else ""


def _node_version_entry(module_name: str | None, method_name: str | None,
                        version: str | None):
    """Return a node's version entry, or ``None`` for the claim to refuse.

    Args:
        module_name: The method's module, or ``None``.
        method_name: The method's name, or ``None``.
        version: The node's version identity; ``None`` is the newest.

    Returns:
        The version's list entry, or ``None`` when the method or version is
        unknown or the list is unreadable.
    """
    if not module_name or not method_name:
        return None
    from ..registration import version_entry

    try:
        return version_entry(module_name, method_name, version)
    except ValueError:
        return None


def _document_method(pipeline_json: str | None, node_id: str,
                     module_name: str | None,
                     method_name: str | None
                     ) -> tuple[str | None, str | None, str | None]:
    """Name a node's method and version the way the claim does.

    The given names win; a missing one is read off the document's node.
    The version is the document node's pin; ``None`` with no document.

    Args:
        pipeline_json: Path to the pipeline document, or ``None``.
        node_id: The node's id.
        module_name: The method's module, or ``None``.
        method_name: The method's name, or ``None``.

    Returns:
        ``(module_name, method_name, version)``, each ``None`` when nothing
        names it.
    """
    if not pipeline_json:
        return module_name, method_name, None
    path = Path(pipeline_json)
    if not path.exists():
        return module_name, method_name, None
    from ..graph import document_node

    try:
        node = document_node(json.loads(path.read_text()), node_id) or {}
    except Exception:
        node = {}
    return (module_name or node.get("module"),
            method_name or node.get("method"),
            node.get("version") or None)


def resolve_node_env(value: str, project_root: Path | str) -> NodeEnv:
    """Parse an env value and look up its container record.

    Args:
        value: The env value (see :func:`node_env_value`).
        project_root: The project root holding ``.wfc/envs.json``.

    Returns:
        The resolved :class:`NodeEnv`. A value outside the grammar carries
        the grammar's message in ``error``; an unknown name, an unreadable
        manifest or a record with no container leaves ``record`` ``None``.
    """
    from ..contracts import parse_env_spec
    from ..environments import get as get_env

    if not value:
        return NodeEnv(value, "", None, "")
    try:
        name = parse_env_spec(value)
    except ValueError as exc:
        return NodeEnv(value, "", None, str(exc))
    try:
        record = get_env(name, Path(project_root))
    except Exception:
        record = None
    if record is None or not record.container:
        record = None
    return NodeEnv(value, name, record, "")


def _ensure_version_image(env: NodeEnv, entry, module_name: str,
                          method_name: str, project_root: Path) -> str:
    """Make one method version's recorded image runnable, or refuse.

    Environments' run-time rule answers for the version's own
    ``(env, env_fingerprint)``, whether or not it is the env's current
    image: present, else pulled and checked, else refused. A refusal is
    prefixed with the version it stops.

    Args:
        env: The version's env, resolved against the manifest.
        entry: The version's list entry.
        module_name: The method's module.
        method_name: The method's name.
        project_root: The project root.

    Returns:
        The ref to hand Docker for the version's image.

    Raises:
        EnvNotRunnableError: The version's image is not on this machine and
            cannot be pulled and checked, or Docker cannot be asked.
    """
    from ..environments import EnvNotRunnableError

    try:
        return ensure_version_runnable(env.name, entry.env_fingerprint,
                                       project_root)
    except EnvNotRunnableError as exc:
        raise EnvNotRunnableError(
            f"Version v{entry.label} of method '{module_name}/{method_name}' "
            f"cannot run: {exc}"
        ) from exc


def _check_version_capture(env_name: str, entry, module_name: str,
                           method_name: str, project_root: Path) -> None:
    """Refuse a method version whose image has no captures folder.

    Dispatch runs a version with the interpreter its own image's captures
    record, so a version registered before wfc recorded captures cannot be
    run; registering the method again makes one that can.

    Args:
        env_name: The version's env name.
        entry: The version's list entry.
        module_name: The method's module.
        method_name: The method's name.
        project_root: The project root.

    Raises:
        EnvNotRunnableError: Environments has no captures for the version's
            ``(env, env_fingerprint)``.
    """
    from ..environments import EnvNotRunnableError, capture_exists

    if capture_exists(env_name, entry.env_fingerprint, project_root):
        return
    raise EnvNotRunnableError(
        f"Version v{entry.label} of method '{module_name}/{method_name}' runs "
        f"env '{env_name}' image {entry.env_fingerprint or '<none>'}, which has "
        f"no captures folder (the version was registered before wfc recorded "
        f"what each image installed), so it cannot be run. Register the method "
        f"again with `wfc register-method` (module '{module_name}', method "
        f"'{method_name}') to make a version that runs the env's current image."
    )


def _ensure_envs(nodes: Iterable[tuple[str | None, str | None, str | None]],
                 project_root: Path) -> dict[tuple[str, str], str]:
    """Make each distinct method version's image runnable, once.

    A version whose image has no captures folder is refused first, before
    any image is probed or pulled.

    Args:
        nodes: ``(module, method, version)`` per node, in document order.
        project_root: The project root.

    Returns:
        The ref to hand Docker per image, keyed ``(env name,
        env_fingerprint)``: two versions of one env can run two images.

    Raises:
        EnvNotRunnableError: A version's image is not on this machine and
            cannot be pulled and checked.
    """
    refs: dict[tuple[str, str], str] = {}
    for module_name, method_name, version in nodes:
        entry = _node_version_entry(module_name, method_name, version)
        if entry is None or module_name is None or method_name is None:
            # A node with no module or method has no version entry.
            continue
        env = resolve_node_env(entry.env_name, project_root)
        if env.record is None:
            continue
        _check_version_capture(env.name, entry, module_name, method_name,
                               project_root)
        image = (env.name, entry.env_fingerprint or "")
        if image in refs:
            continue
        refs[image] = _ensure_version_image(env, entry, module_name,
                                            method_name, project_root)
    return refs


@task(purpose="Make every image a pipeline's nodes run runnable -- each "
              "node's method version's recorded image -- once per distinct "
              "image, before the run records its commit: present, else "
              "pulled by its recorded digest and checked, else refused naming "
              "the recreate command and wfc register-method; a version whose "
              "image has no captures folder is refused first",
      inputs="the (module, method, version) of each node the engine runs, "
             "project root",
      outputs="the ref to hand Docker per (env, env_fingerprint), or "
              "EnvNotRunnableError",
      critical="nodes whose env resolves to no container record are skipped: "
               "dispatch's no-container ending refuses each of them")
def preflight_pipeline_envs(methods: Iterable[tuple[str, str, str | None]],
                            project_root: Path | str
                            ) -> dict[tuple[str, str], str]:
    """Make every image the pipeline's nodes run runnable.

    Args:
        methods: The ``(module_name, method_name, version)`` of each node
            the engine runs; a ``None`` version is the newest.
        project_root: The project root.

    Returns:
        The ref to hand Docker per image, keyed ``(env, env_fingerprint)``.

    Raises:
        EnvNotRunnableError: An image is not on this machine and cannot be
            pulled and checked. Nothing has been launched.
    """
    口 = Step(step_num=1, name="Ask for each distinct env",
             purpose="Read each node's version entry, the rule dispatch "
                     "uses, and make its recorded image runnable once per "
                     "distinct image")
    return _ensure_envs(methods, Path(project_root))


@task(purpose="Make a standalone run-step's image -- its method version's "
              "recorded image -- runnable before Claim; a version whose image "
              "has no captures folder is refused naming wfc register-method",
      inputs="node id, pipeline document path or None (read only to name the "
             "node's method when the caller did not), the method's module and "
             "name, project root",
      outputs="the ref to hand Docker, None when the node's env resolves to "
              "no container record, or EnvNotRunnableError")
def preflight_step_env(node_id: str, pipeline_json: str | None,
                       module_name: str | None, method_name: str | None,
                       project_root: Path | str) -> str | None:
    """Make the env one standalone step runs in runnable.

    It runs before the claim, so it names the node's method the way the
    claim does (the given names, else the document node's) and asks
    Registration for the method's registered env.

    Args:
        node_id: The node's id.
        pipeline_json: Path to the pipeline document, or ``None``.
        module_name: The method's module, or ``None``.
        method_name: The method's name, or ``None``.
        project_root: The project root.

    Returns:
        The ref to hand Docker, or ``None`` when the node's env does not
        resolve to a container record (dispatch refuses it).

    Raises:
        EnvNotRunnableError: The version's image is not on this machine
            and cannot be pulled and checked.
    """
    口 = Step(step_num=1, name="Ask for the node's env",
             purpose="Name the node's method and version as the claim does "
                     "and make the version's recorded image runnable")
    module_name, method_name, version = _document_method(
        pipeline_json, node_id, module_name, method_name)
    refs = _ensure_envs([(module_name, method_name, version)],
                        Path(project_root))
    return next(iter(refs.values()), None)
