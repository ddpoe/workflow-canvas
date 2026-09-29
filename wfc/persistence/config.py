"""The project's configuration file reader.

:func:`read_config` parses ``wf-canvas.toml`` — located through Layout's
marker path — and returns the settings ``wfc`` reads back from it: the pixi
and conda environment roots and the ``[dvc]`` archive entry.

Inside ``wfc`` the module imports Layout and the standard library only: it
opens the config file, it never touches the database.
"""

from __future__ import annotations

from pathlib import Path

from axiom_annotations import Step, task

from .. import layout


@task(purpose="Read the project wf-canvas.toml and return the environment roots and the [dvc] archive entry")
def read_config(project_dir: Path) -> dict:
    """Read the project wf-canvas.toml and return parsed settings.

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        Dict with ``pixi_root``, ``conda_root``, and ``dvc``.  The two roots
        are resolved absolute path strings to the pixi and conda environment
        root directories (``conda_root`` is ``""`` when no ``[conda]`` root is
        declared); ``dvc`` is the parsed ``[dvc]`` archive entry, or ``None``.

    Raises:
        FileNotFoundError: If wf-canvas.toml does not exist.
    """
    import tomllib

    口 = Step(step_num=1, name="Locate config file",
             purpose="Find .wfc/wf-canvas.toml in the project directory")

    project_dir = Path(project_dir).resolve()
    config_path = layout.marker_path(project_dir)
    if not config_path.exists():
        raise FileNotFoundError(
            f"No wfc project found at {project_dir} — run `wfc init` first"
        )

    口 = Step(step_num=2, name="Parse config",
             purpose="Read wf-canvas.toml with tomllib and extract settings")

    with open(config_path, "rb") as f:
        parsed = tomllib.load(f)

    result: dict = {}

    口 = Step(step_num=3, name="Resolve pixi root",
             purpose="Parse [pixi] section and resolve the root path")

    pixi_section = parsed.get("pixi", {})
    pixi_root_raw = pixi_section.get("root", ".pixi")
    pixi_root_path = Path(pixi_root_raw)
    if not pixi_root_path.is_absolute():
        pixi_root_path = project_dir / pixi_root_path
    result["pixi_root"] = str(pixi_root_path.resolve())

    口 = Step(step_num=4, name="Parse conda config",
             purpose="Extract optional [conda] root for conda env resolution")

    conda_section = parsed.get("conda", {})
    conda_root_raw = conda_section.get("root", "")
    if conda_root_raw:
        conda_root_path = Path(conda_root_raw)
        if not conda_root_path.is_absolute():
            conda_root_path = project_dir / conda_root_path
        result["conda_root"] = str(conda_root_path.resolve())
    else:
        result["conda_root"] = ""

    口 = Step(step_num=5, name="Parse DVC config",
             purpose="Extract [dvc] section for provenance storage")

    dvc_section = parsed.get("dvc", None)
    if dvc_section is not None:
        # Prefer `url` (any scheme); fall back to legacy
        # `remote_path` (local-only) for backwards compatibility.
        result["dvc"] = {
            "url": dvc_section.get("url") or dvc_section.get("remote_path"),
            "remote_type": dvc_section.get("remote_type", "local"),
            "remote_path": dvc_section.get("remote_path"),
            "auto_init": dvc_section.get("auto_init", True),
        }
    else:
        result["dvc"] = None

    return result
