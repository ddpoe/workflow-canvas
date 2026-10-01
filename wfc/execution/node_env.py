"""The env a node runs in, and the pre-flights that make it runnable.

One rule names a node's env, and dispatch and both pre-flights ask it: the
document's value for the node when the invocation carries a document,
otherwise the env the method's own ``method.yaml`` declares. The value goes
through the one grammar (``parse_env_spec``) and the name is looked up in
the project's env manifest; only a record with a container image counts.

Dispatch turns the record into the reference Docker is handed and never
rebuilds. The pre-flights ask Environments' ``ensure_runnable`` for each
env instead, which rebuilds a locally built image the Docker daemon lost:
``run_pipeline`` once per distinct env before the run records its commit,
and a standalone ``run-step`` for its one env before Claim. A node whose
env does not resolve to a container record is left to dispatch's own
no-container refusal.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from axiom_annotations import Step, task

from ..environments import EnvRecord, ensure_runnable


@dataclass(frozen=True)
class NodeEnv:
    """A node's env value, resolved against the env manifest.

    Attributes:
        value: The env value as written (the document's, or the method's
            declaration); ``""`` when the node names none.
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


def document_node_env(document: dict, node_id: str) -> str:
    """Return the env value a pipeline document gives *node_id*.

    The node is found by its id, else (legacy documents) by its method name.
    ``env`` wins over the legacy ``env_strategy``.

    Args:
        document: The pipeline document.
        node_id: The node's id.

    Returns:
        The node's env value, or ``""`` when the node or its value is absent.
    """
    by_id = {str(n["id"]): n for n in document["nodes"]}
    by_method = {n["method"]: n for n in document["nodes"] if n.get("method")}
    node = by_id.get(node_id) or by_method.get(node_id) or {}
    return node.get("env", node.get("env_strategy", "")) or ""


def node_env_value(node_id: str, pipeline_json: str | None,
                   script_path: str | None) -> str:
    """Return the env value a node runs in.

    With a document on disk, the document's value for the node (a document
    naming no env stays empty; the method's declaration is not consulted).
    With no document, the env the method's ``method.yaml`` declares.

    Args:
        node_id: The node's id.
        pipeline_json: Path to the pipeline document, or ``None``.
        script_path: The method script's path, whose directory holds
            ``method.yaml``; ``None`` when not yet resolved.

    Returns:
        The env value, or ``""`` when nothing names one.
    """
    if pipeline_json and Path(pipeline_json).exists():
        document = json.loads(Path(pipeline_json).read_text())
        return document_node_env(document, node_id)
    if not script_path:
        return ""
    from ..contracts import parse_method_yaml

    try:
        contract = parse_method_yaml(Path(script_path).resolve().parent)
    except Exception:
        return ""
    return (contract or {}).get("env") or ""


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


def _ensure_envs(values: Iterable[str], project_root: Path) -> dict[str, str]:
    """Ask ``ensure_runnable`` once per distinct container env in *values*.

    Args:
        values: Env values, in the order the nodes name them.
        project_root: The project root.

    Returns:
        The runnable daemon ref per env name.

    Raises:
        EnvNotRunnableError: An env's image is missing and cannot be rebuilt.
        RuntimeError: A rebuild's ``docker build`` failed.
    """
    refs: dict[str, str] = {}
    for value in values:
        env = resolve_node_env(value, project_root)
        if env.record is None or env.name in refs:
            continue
        refs[env.name] = ensure_runnable(env.name, env.record, project_root)
    return refs


@task(purpose="Make every container env a pipeline's nodes name runnable, "
              "asking ensure_runnable once per distinct env so a missing "
              "local image is rebuilt once, before the run records its commit",
      inputs="the frozen (substituted) document, the nodes the engine runs, "
             "project root",
      outputs="the runnable daemon ref per env name, or EnvNotRunnableError",
      critical="nodes whose env resolves to no container record are skipped: "
               "dispatch's no-container ending refuses each of them")
def preflight_pipeline_envs(document: dict, node_ids: Iterable[str],
                            project_root: Path | str) -> dict[str, str]:
    """Make every env the pipeline's nodes run in runnable.

    Args:
        document: The document the nodes' ``run-step`` reads (the frozen,
            substituted one).
        node_ids: The ids of the nodes the engine runs.
        project_root: The project root.

    Returns:
        The runnable daemon ref per env name.

    Raises:
        EnvNotRunnableError: An env's image is missing and cannot be
            rebuilt. Nothing has been launched.
        RuntimeError: A rebuild's ``docker build`` failed.
    """
    口 = Step(step_num=1, name="Ask for each distinct env",
             purpose="Resolve each node's env through the rule dispatch uses "
                     "and ask ensure_runnable once per env name")
    return _ensure_envs(
        (document_node_env(document, node_id) for node_id in node_ids),
        Path(project_root),
    )


@task(purpose="Make a standalone run-step's env runnable before Claim, so the "
              "claim folds the rebuilt env's fingerprint into the cache key",
      inputs="node id, pipeline document path or None, method script path, "
             "project root",
      outputs="the runnable daemon ref, None when the node names no container "
              "env, or EnvNotRunnableError")
def preflight_step_env(node_id: str, pipeline_json: str | None,
                       script_path: str | None,
                       project_root: Path | str) -> str | None:
    """Make the env one standalone step runs in runnable.

    Args:
        node_id: The node's id.
        pipeline_json: Path to the pipeline document, or ``None``.
        script_path: The method script's path, or ``None``.
        project_root: The project root.

    Returns:
        The runnable daemon ref, or ``None`` when the node's env does not
        resolve to a container record (dispatch refuses it).

    Raises:
        EnvNotRunnableError: The env's image is missing and cannot be
            rebuilt.
        RuntimeError: A rebuild's ``docker build`` failed.
    """
    口 = Step(step_num=1, name="Ask for the node's env",
             purpose="Resolve the node's env through the rule dispatch uses "
                     "and ask ensure_runnable for it")
    refs = _ensure_envs(
        [node_env_value(node_id, pipeline_json, script_path)],
        Path(project_root),
    )
    return next(iter(refs.values()), None)
