"""The registration-time env check: a method's ``env:`` must name a registered container env.

Method registration (``wfc.registration``) calls :func:`check_method_env`
from the package surface, through a deferred import.
"""

from __future__ import annotations

from pathlib import Path

from .. import layout


def check_method_env(env_spec: str, project_dir: Path) -> str:
    """Resolve and validate the env a method.yaml names.

    The value is the bare name of an env registered in ``.wfc/envs.json`` —
    the write-side entry of the env-spec grammar, which applies the name
    rule alone (no prefix, no scheme, no digest). The record's ``container``
    field must be digest-pinned; the image itself is **not** pulled at
    registration time.

    Args:
        env_spec: The ``env`` value from method.yaml.
        project_dir: Root directory of the wfc project.

    Returns:
        The value, unchanged.

    Raises:
        ValueError: The value is not an env name, names no registered env,
            or names a record whose container is not digest-pinned. The
            message states what was given, the form, the rule (when the
            value is not a name), and the registered names — or how to
            register one when there are none, or that the manifest could
            not be read when it is present but fails to load.
    """
    from ..contracts import ENV_SPEC_FORM, validate_container_ref, validate_env_name
    from .manifest import get as _env_get, load_manifest

    def _refusal(problem: str) -> ValueError:
        try:
            names = sorted(load_manifest(project_dir).get("envs", {}))
        except Exception as exc:
            manifest = f"{layout.STATE_DIR_NAME}/{layout.ENV_MANIFEST_FILENAME}"
            options = f"{manifest} could not be read: {exc}."
        else:
            options = (
                "Registered envs: " + ", ".join(names) + "."
                if names
                else "No envs are registered yet; build one with `wfc register-env <name>`."
            )
        form = ENV_SPEC_FORM[0].upper() + ENV_SPEC_FORM[1:]
        return ValueError(f"{problem} {form}. {options}")

    try:
        name = validate_env_name(env_spec)
    except ValueError as exc:
        raise _refusal(str(exc)) from None

    env_record = _env_get(name, project_dir)
    if env_record is None:
        raise _refusal(f"env name {name!r} is not found in .wfc/envs.json.")
    validate_container_ref(env_record.container)
    return env_spec
