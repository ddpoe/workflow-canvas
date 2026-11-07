"""The variables every container wfc starts is given.

One builder, :func:`container_variables`, answers for a step's container
(dispatch), and for the dev loop's (``wfc shell``, ``wfc exec``, ``wfc
jupyter``, which the CLI builds and hands to Environments): the ``WFC_*``
values a step has, ``PYTHONUNBUFFERED=1`` so a method's prints reach the step
log as they happen, the method's declared names (``WFC_DECLARED_NAMES``) and
the env's own ``bin`` first on ``PATH`` so ``python`` there is the env's.

Every value is set when the container starts, as ``-e`` flags: none is written
into an image recipe, so none enters an env fingerprint or a cache key.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from axiom_annotations import task

from .. import layout
from ..contracts import WFC_ENV_VARS

#: The search path a Linux image runs with when it sets none (Docker's own
#: default); the env's ``bin`` is put in front of it. The images wfc builds
#: (uv, pixi, conda) add nothing to it but their env's ``bin``.
DEFAULT_CONTAINER_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def step_variables(*, run_id: int | str, run_dir: str, sample: str,
                   params: Mapping, node_id: str, pipeline_id: str,
                   variant: str, slot_paths: Mapping | None) -> dict[str, str]:
    """Return a step's ``WFC_*`` values, host-side.

    Args:
        run_id: The step's run id.
        run_dir: The run folder, a host path.
        sample: The sample the step runs on.
        params: The step's resolved parameters.
        node_id: The node the step runs.
        pipeline_id: The pipeline execution id.
        variant: The parameter variant name.
        slot_paths: The materialized input paths by slot (lists of host
            paths); ``WFC_INPUT_PATHS`` is set only when there are any.

    Returns:
        ``{name: value}`` over names in ``WFC_ENV_VARS``.
    """
    values = {
        "WFC_RUN_ID": str(run_id),
        "WFC_RUN_DIR": str(run_dir),
        "WFC_SAMPLE": sample,
        "WFC_PARAMS": json.dumps(params),
        "WFC_NODE_ID": node_id,
        "WFC_PIPELINE_ID": pipeline_id,
        "WFC_VARIANT": variant,
    }
    if slot_paths:
        values["WFC_INPUT_PATHS"] = json.dumps(slot_paths)
    return values


def _translated(name: str, value: str, mounts: tuple[layout.Mount, ...]) -> str:
    """Return a path-carrying ``WFC_*`` value with its host paths made container paths."""
    if name == "WFC_RUN_DIR":
        return layout.translate_host_path(value, mounts)
    if name != "WFC_INPUT_PATHS":
        return value
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return value

    def leaf(p):
        return layout.translate_host_path(p, mounts) if isinstance(p, str) else p

    if isinstance(decoded, dict):
        return json.dumps({
            slot: ([leaf(p) for p in paths] if isinstance(paths, list) else leaf(paths))
            for slot, paths in decoded.items()
        })
    if isinstance(decoded, list):
        return json.dumps([leaf(p) for p in decoded])
    return value


@task(purpose="Build every variable a container wfc starts is given: the "
              "step's WFC_* values (container paths), the declared names, "
              "PYTHONUNBUFFERED=1 and the env's own bin first on PATH",
      inputs="a step's WFC_* values (or none, for the dev loop), the "
             "method's declared names, the env's bin directory, the mount "
             "table",
      outputs="{name: value}, in the order they are forwarded as -e flags",
      critical="Set when the container starts, never in an image recipe: "
               "nothing here enters an env fingerprint or a cache key")
def container_variables(*, step: Mapping[str, str] | None = None,
                        declared: Mapping | None = None,
                        env_bin: str | None = None,
                        mounts: tuple[layout.Mount, ...] | None = None,
                        write_bytecode: bool = True) -> dict[str, str]:
    """Return the variables a container is started with.

    Args:
        step: The step's ``WFC_*`` values (:func:`step_variables`), host-side;
            ``None`` for a dev-loop container, which runs no step. Only names
            in ``WFC_ENV_VARS`` are taken: nothing else from the host, and
            ``PYTHONPATH`` in particular, crosses into the container.
        declared: The method's declared names
            (:func:`wfc.contracts.declared_names`), sent as
            ``WFC_DECLARED_NAMES``; ``None`` sends none.
        env_bin: The env image's ``bin`` directory
            (:func:`wfc.environments.version_bin_dir`), put first on
            ``PATH``; ``None`` leaves the image's own ``PATH`` (a
            bring-your-own image).
        mounts: Layout's mount table for the container: ``WFC_RUN_DIR`` and
            each ``WFC_INPUT_PATHS`` path are translated through it.
            ``None`` keeps them host paths (what the host process that
            launches the container is given).
        write_bytecode: False adds ``PYTHONDONTWRITEBYTECODE=1``, for a code
            copy a backend binds read-write.

    Returns:
        ``{name: value}``.
    """
    values: dict[str, str] = {}
    for name in WFC_ENV_VARS:
        if step is not None and name in step:
            value = step[name]
            values[name] = _translated(name, value, mounts) if mounts is not None else value
    if declared is not None:
        values["WFC_DECLARED_NAMES"] = json.dumps(declared)
    values["PYTHONUNBUFFERED"] = "1"
    if not write_bytecode:
        values["PYTHONDONTWRITEBYTECODE"] = "1"
    if env_bin:
        values["PATH"] = f"{env_bin}:{DEFAULT_CONTAINER_PATH}"
    return values


def env_flags(variables: Mapping[str, str]) -> list[str]:
    """Render variables as ``docker run`` ``-e NAME=VALUE`` flags, in order.

    Args:
        variables: What :func:`container_variables` returned.

    Returns:
        ``["-e", "NAME=VALUE", ...]``.
    """
    flags: list[str] = []
    for name, value in variables.items():
        flags.extend(["-e", f"{name}={value}"])
    return flags
