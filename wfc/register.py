"""
Register modules and methods in the database.

``register_module`` — create or update a module row.
``register_method`` — AST-scan a method's script and populate
  Method, TrackedFunction, and ParamDef rows.

These are the production functions behind ``wfc register-module``
and ``wfc register-method`` CLI commands.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from sqlmodel import select

from axiom_annotations import workflow, task, Step

from .ast_scanner import scan_script, ScriptInfo
from .contracts import parse_method_yaml, parse_module_yaml
from .database import get_session
from .models import (
    Module,
    Method,
    MethodContract,
    ModuleContract,
    TrackedFunction,
    ParamDef,
)


# =============================================================================
# Environment resolution
# =============================================================================

def _find_python_in_env(env_dir: Path) -> Path:
    """Locate the python executable inside a conda/pixi environment directory.

    Handles both Unix (``bin/python``) and Windows (``python.exe`` at env
    root, or ``Scripts/python.exe``) layouts.

    Returns:
        Absolute ``Path`` to the python executable.

    Raises:
        ValueError: If no python binary is found.
    """
    import sys as _sys

    if _sys.platform == "win32":
        candidates = [env_dir / "python.exe", env_dir / "Scripts" / "python.exe"]
    else:
        candidates = [env_dir / "bin" / "python"]

    for c in candidates:
        if c.exists():
            return c.resolve()

    searched = ", ".join(str(c) for c in candidates)
    raise ValueError(
        f"Python executable not found. Searched: {searched}. "
        f"The environment directory {env_dir} exists but the python "
        f"binary is missing."
    )


def _local_pixi_env_dir(env: str, project_dir: Path | None = None) -> Path | None:
    """Return ``<project_dir>/.pixi/envs/<env>`` when it exists, else ``None``.

    Pixi's per-project local layout puts envs at ``<project>/.pixi/envs/<env>``
    with no project-name prefix in the path. Used as a fallback after the
    configured ``[pixi].root`` glob fails, so locally-installed envs
    work even when the user hasn't set a global pixi root.
    """
    base = Path(project_dir) if project_dir is not None else Path.cwd()
    candidate = base / ".pixi" / "envs" / env
    return candidate if candidate.exists() else None


def _resolve_pixi_standalone(
    name: str,
    pixi_root: str | Path | None,
    project_dir: Path | None = None,
) -> Path:
    """Resolve a standalone pixi project env.

    Resolution cascade:

    1. Configured ``[pixi].root`` glob: ``{pixi_root}/{name}-*/envs/default``.
    2. Local fallback: ``<project_dir>/.pixi/envs/default`` (when the env
       was installed via ``pixi install`` inside the project itself).
    """
    pixi_root_path: Path | None = Path(pixi_root) if pixi_root else None
    pattern = f"{name}-*/envs/default"
    matches = sorted(pixi_root_path.glob(pattern)) if pixi_root_path else []

    if len(matches) == 1:
        return _find_python_in_env(matches[0])
    if len(matches) > 1:
        listing = "\n  ".join(str(m) for m in matches)
        raise ValueError(
            f"Multiple pixi environments match '{name}':\n  {listing}\n"
            f"Remove duplicates so only one {name}-* directory exists "
            f"under {pixi_root_path}."
        )

    # Zero matches in pixi_root — try the local project fallback.
    local = _local_pixi_env_dir("default", project_dir)
    if local is not None:
        return _find_python_in_env(local)

    searched: list[str] = []
    if pixi_root_path is not None:
        searched.append(str(pixi_root_path / pattern))
    searched.append(str((project_dir or Path.cwd()) / ".pixi" / "envs" / "default"))
    raise ValueError(
        f"No pixi environment found for '{name}'.\n"
        f"Searched:\n  " + "\n  ".join(searched) + "\n"
        f"Run `pixi install` in the environment directory first, "
        f"or set [pixi] root in .wfc/wf-canvas.toml."
    )


def _resolve_pixi_project_env(
    project: str,
    env: str,
    pixi_root: str | Path | None,
    project_dir: Path | None = None,
) -> Path:
    """Resolve a pixi project + env.

    Resolution cascade:

    1. Configured ``[pixi].root`` glob: ``{pixi_root}/{project}-*/envs/{env}``.
    2. Local fallback: ``<project_dir>/.pixi/envs/{env}`` (when the pixi
       project lives inside the wfc project itself).
    """
    pixi_root_path: Path | None = Path(pixi_root) if pixi_root else None
    pattern = f"{project}-*/envs/{env}"
    matches = sorted(pixi_root_path.glob(pattern)) if pixi_root_path else []

    if len(matches) == 1:
        return _find_python_in_env(matches[0])
    if len(matches) > 1:
        listing = "\n  ".join(str(m) for m in matches)
        raise ValueError(
            f"Multiple pixi environments match '{project}:{env}':\n  {listing}\n"
            f"Remove duplicates so only one {project}-* directory exists "
            f"under {pixi_root_path}."
        )

    # Zero matches in pixi_root — try the local project fallback.
    local = _local_pixi_env_dir(env, project_dir)
    if local is not None:
        return _find_python_in_env(local)

    searched: list[str] = []
    if pixi_root_path is not None:
        searched.append(str(pixi_root_path / pattern))
    searched.append(str((project_dir or Path.cwd()) / ".pixi" / "envs" / env))
    raise ValueError(
        f"No pixi environment found for project '{project}', env '{env}'.\n"
        f"Searched:\n  " + "\n  ".join(searched) + "\n"
        f"Run `pixi install` in the project directory first, "
        f"or set [pixi] root in .wfc/wf-canvas.toml."
    )


def _resolve_conda(name: str, conda_root: str | Path | None) -> Path:
    """Resolve a conda environment by name.

    Checks ``{conda_root}/envs/{name}`` first.  If ``conda_root`` is not
    configured, auto-detects via ``conda info --base``.
    """
    if not conda_root:
        conda_root = _detect_conda_root()
    conda_root = Path(conda_root)
    env_dir = conda_root / "envs" / name

    if not env_dir.exists():
        raise ValueError(
            f"No conda environment '{name}' found at {env_dir}. "
            f"Create it with `conda create -n {name} python` first."
        )
    return _find_python_in_env(env_dir)


def _detect_conda_root() -> str:
    """Auto-detect conda base directory via ``conda info --base``."""
    try:
        result = subprocess.run(
            ["conda", "info", "--base"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    raise ValueError(
        "Cannot auto-detect conda root (conda not found or not on PATH). "
        "Set [conda] root in .wfc/wf-canvas.toml."
    )


def resolve_python_for_env(
    env_spec: str,
    pixi_root: str | Path | None = None,
    conda_root: str | Path | None = None,
    project_dir: Path | None = None,
) -> Path:
    """Resolve a python executable from a typed environment specifier.

    Supported prefixes::

        pixi:<project>:<env>   pixi project with explicit env name
        pixi:<name>            standalone pixi project (default env)
        conda:<name>           conda environment by name

    Bare names (no prefix) raise ``ValueError`` with guidance.

    For pixi: prefixes, resolution cascades from the configured
    ``[pixi].root`` glob to a local ``<project_dir>/.pixi/envs/<env>``
    fallback so locally-installed envs work even without a configured
    global pixi root.

    Args:
        env_spec: Typed env string (e.g. ``"pixi:image-io"``).
        pixi_root: Absolute path to pixi environment root (for pixi: prefixes).
        conda_root: Absolute path to conda base dir (for conda: prefix).
            Auto-detected via ``conda info --base`` if not provided.
        project_dir: wfc project root, used to find a local
            ``.pixi/envs/<env>`` fallback when ``pixi_root`` is unset or
            empty. Defaults to cwd.

    Returns:
        Absolute ``Path`` to the python executable.

    Raises:
        ValueError: If the prefix is missing/unrecognized, or the env
            cannot be found.
    """
    parts = env_spec.split(":")
    if parts[0] == "pixi" and len(parts) == 3:
        # pixi:<project>:<env>
        return _resolve_pixi_project_env(
            parts[1], parts[2], pixi_root, project_dir=project_dir
        )
    elif parts[0] == "pixi" and len(parts) == 2:
        # pixi:<name>  (standalone, default env)
        return _resolve_pixi_standalone(
            parts[1], pixi_root, project_dir=project_dir
        )
    elif parts[0] == "conda" and len(parts) == 2:
        return _resolve_conda(parts[1], conda_root)
    else:
        raise ValueError(
            f"Unknown env spec '{env_spec}'. Use a typed prefix: "
            f"'pixi:<project>:<env>', 'pixi:<name>', or 'conda:<name>'."
        )


def _resolve_env(env_spec: str, project_dir: Path) -> str:
    """Resolve and validate a named shared environment.

    Supports three prefix families:

    - ``container:<envname>`` — look up *envname* in ``.wfc/envs.json``
      (ADR-019). The record's ``container`` field must be digest-pinned;
      the image itself is **not** pulled at registration time (decision #8).
    - ``container:docker://<host>/<path>@sha256:<hex>`` — direct digest-pinned
      ref (ADR-019 decision #12 escape hatch). Validated for shape only.
    - ``pixi:<...>`` / ``conda:<name>`` — existing typed env spec, resolved
      to a python executable via :func:`resolve_python_for_env`.

    Args:
        env_spec: The env spec from method.yaml (not ``"inherit"``).
        project_dir: Root directory of the wfc project.

    Returns:
        The validated env spec (same as input).

    Raises:
        ValueError: If the env cannot be found, is ambiguous, or references
            a container that isn't digest-pinned.
    """
    # ADR-019 Cycle H: execution is container-only. A method env is either the
    # direct digest-pinned escape hatch or a built container env named in the
    # manifest. Strip an optional ``container:`` prefix; bare names and
    # ``container:<name>`` both resolve to a manifest-backed container env.
    value = (
        env_spec[len("container:"):]
        if env_spec.startswith("container:")
        else env_spec
    )

    if value.startswith("docker://"):
        # Direct digest-pinned ref — shape check only, no manifest lookup
        # and no image pull (ADR-019 #8, #12).
        from .envs import validate_container_ref
        validate_container_ref(value)
        return env_spec

    # Manifest-backed ref: <name> must exist in .wfc/envs.json and its
    # container field must itself be digest-pinned.
    from .envs import get as _env_get, validate_container_ref
    env_record = _env_get(value, project_dir)
    if env_record is None:
        raise ValueError(
            f"Container env '{value}' not found in .wfc/envs.json. "
            f"Build it with `wfc register-env {value}` first, or declare a "
            f"direct ref as `env: container:docker://...@sha256:...`. "
            f"Host execution was removed in ADR-019 Cycle H."
        )
    validate_container_ref(env_record.container)
    return env_spec


# =============================================================================
# Git integration
# =============================================================================

def _git_commit_registration(
    method_dir: Path,
    method_name: str,
    module_name: str,
    project_root: Path | None = None,
) -> str | None:
    """Commit the method directory to the project git repo.

    Stages the method directory and creates a commit.  Does nothing if the
    project root is not a git repo (prints a warning instead).

    Args:
        method_dir: Directory containing the registered method scripts.
        method_name: Method name (used in commit message).
        module_name: Module name (used in commit message).
        project_root: Root of the project git repo.  Defaults to cwd.

    Returns:
        Commit SHA if committed, or ``None`` if not in a git repo.
    """
    root = str(project_root or Path.cwd())

    # Check if inside a git repo
    check = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=root, capture_output=True, text=True,
    )
    if check.returncode != 0:
        raise RuntimeError(
            f"{root!r} is not a git repository.\n"
            "Run `wfc init --git` before registering methods.\n"
            "wfc requires git to track method versions for cache-key computation."
        )

    # Stage the method directory
    stage = subprocess.run(
        ["git", "add", str(method_dir)],
        cwd=root, capture_output=True, text=True,
    )
    if stage.returncode != 0:
        raise RuntimeError(f"git add failed: {stage.stderr.strip()}")

    # Check if there is anything to commit (no-op if dir was already staged)
    status = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=root, capture_output=True,
    )
    if status.returncode == 0:
        # Nothing new staged — method was already identical
        print(f"  git: no changes to commit for '{method_name}'")
        return None

    # Commit — use inline identity so repo doesn't need global git config
    commit = subprocess.run(
        [
            "git",
            "-c", "user.email=wfc@wfc",
            "-c", "user.name=wfc",
            "commit",
            "-m", f"Register method {module_name}/{method_name}",
        ],
        cwd=root, capture_output=True, text=True,
    )
    if commit.returncode != 0:
        raise RuntimeError(f"git commit failed: {commit.stderr.strip()}")

    # Return the new HEAD SHA
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root, capture_output=True, text=True,
    )
    sha_str = sha.stdout.strip()
    print(f"  git: committed as {sha_str[:12]}")
    return sha_str


@task(purpose="Create or update a module in the database")
def register_module(
    name: str,
    contracts: list[dict] | None = None,
    description: str | None = None,
    module_dir: Path | None = None,
) -> int:
    """Create or update a module row.

    If a module with the same name already exists, updates its description
    and contracts. Otherwise creates a new row.

    Contracts can come from three sources (highest priority first):
      1. The ``contracts`` argument (explicit CLI JSON)
      2. A ``module.yaml`` file in ``module_dir``
      3. If neither is provided, raises TypeError

    Args:
        name: Module name (e.g. 'data_preprocessing').
        contracts: List of contract dicts (pass ``[]`` for no contracts).
            Each dict must have:
            - ``type``: 'output' or 'metric'
            - ``name``: artifact/metric name
            - ``value_type``: file extension or data type (optional)
            - ``required``: bool (default True)
        description: Optional human-readable description.
        module_dir: Optional path to module directory containing ``module.yaml``.
            When provided and ``contracts`` is None, contracts and description
            are read from the YAML file.

    Returns:
        The module ID (existing or newly created).

    Raises:
        TypeError: If neither ``contracts`` nor a valid ``module.yaml`` is available.
    """
    # Resolve contracts from module.yaml if not explicitly provided
    if contracts is None and module_dir is not None:
        module_yaml_data = parse_module_yaml(module_dir)
        if module_yaml_data is not None:
            contracts = module_yaml_data["contracts"]
            if description is None:
                description = module_yaml_data.get("description")
            print(f"Loaded contracts from {module_dir / 'module.yaml'}")

    if contracts is None:
        raise TypeError(
            "register_module() requires 'contracts' argument or a module.yaml "
            "file in module_dir. Pass contracts=[] for no contracts."
        )
    口 = Step(step_num=1, name="Upsert module row",
             purpose="Create or update the module record in the database")
    with get_session() as session:
        module = session.exec(
            select(Module).where(Module.name == name)
        ).first()

        if module is None:
            module = Module(name=name, description=description)
            session.add(module)
            session.commit()
            session.refresh(module)
            print(f"Created module '{name}' (id={module.id})")
        else:
            if description is not None:
                module.description = description
            session.commit()
            session.refresh(module)
            print(f"Updated module '{name}' (id={module.id})")

        module_id = module.id

        口 = Step(step_num=2, name="Sync contracts",
                 purpose="Replace module-level output and metric contracts with the provided list")
        # Always sync — clear existing rows then insert the new set.
        # An empty list is a valid explicit choice (no contracts for this module).
        existing = session.exec(
            select(ModuleContract).where(
                ModuleContract.module_id == module_id
            )
        ).all()
        for c in existing:
            session.delete(c)

        for c in contracts:
            mc = ModuleContract(
                module_id=module_id,
                contract_type=c["type"],
                name=c["name"],
                value_type=c.get("value_type"),
                required=c.get("required", True),
            )
            session.add(mc)

        session.commit()
        print(f"  {len(contracts)} contract(s) registered")

    return module_id


@workflow(purpose="AST-scan a method script and register it with tracked functions and parameters")
def register_method(
    method_dir: Path,
    module_name: str,
    method_name: str | None = None,
    script_name: str | None = None,
) -> int:
    """Scan a method script and register it in the database.

    Performs:
      1. Find and AST-parse the script
      2. Upsert Method row (linked to the named module)
      3. Extract tracked functions and their parameter definitions
      4. Populate TrackedFunction + ParamDef rows

    Args:
        method_dir: Directory containing the method script.
        module_name: Name of the module this method belongs to.
        method_name: Method name (defaults to directory name).
        script_name: Script filename to scan (default: '{method_name}.py').

    Returns:
        The method ID.

    Raises:
        FileNotFoundError: If the script is not found.
        ValueError: If the module doesn't exist in the database.
    """
    method_dir = Path(method_dir).resolve()
    if method_name is None:
        method_name = method_dir.name
    if script_name is None:
        script_name = f"{method_name}.py"

    口 = Step(step_num=1, name="Locate and scan script",
             purpose="Find method script and extract function signatures via AST parsing")
    script_path = method_dir / script_name
    if not script_path.exists():
        raise FileNotFoundError(
            f"Script not found: {script_path}\n"
            f"Expected '{script_name}' in {method_dir}"
        )

    script_info = scan_script(script_path)
    print(f"Scanned {script_path}: {len(script_info.functions)} function(s)")

    口 = Step(step_num=2, name="Resolve module",
             purpose="Find the parent module in the database")
    with get_session() as session:
        module = session.exec(
            select(Module).where(Module.name == module_name)
        ).first()
        if module is None:
            raise ValueError(
                f"Module '{module_name}' not found in DB. "
                f"Run 'wfc register-module --name {module_name}' first."
            )

        口 = Step(step_num=3, name="Upsert method row",
                 purpose="Create or update the method record linked to its module")
        # Compute script_path relative to project root (cwd)
        try:
            rel_script = script_path.relative_to(Path.cwd())
        except ValueError:
            rel_script = script_path

        method = session.exec(
            select(Method).where(
                Method.module_id == module.id,
                Method.name == method_name,
            )
        ).first()

        # Resolve env from method.yaml contract. ADR-019 Cycle H: execution is
        # container-only, so a method must name a built container env. A method
        # with no method.yaml (hence no env) is rejected here.
        # ``parse_method_yaml`` already rejects a present-but-missing/`inherit`
        # env, so any contract that parses carries a usable env value.
        contract_data = parse_method_yaml(method_dir)
        if contract_data is None:
            raise ValueError(
                f"Method '{method_name}' has no method.yaml, so it does not "
                f"name a built container env. Add a method.yaml with "
                f"`env: <name>` (build it with `wfc register-env <name>`). Host "
                f"execution was removed in ADR-019 Cycle H."
            )
        env_name = contract_data["env"]

        # Validate the named env against the project manifest.
        project_dir = Path.cwd()
        _resolve_env(env_name, project_dir)
        print(f"  Env: {env_name} (validated)")

        if method is None:
            method = Method(
                module_id=module.id,
                name=method_name,
                script_path=str(rel_script),
                env=env_name,
            )
            session.add(method)
            session.commit()
            session.refresh(method)
            print(f"Created method '{method_name}' (id={method.id}, env={env_name})")
        else:
            method.script_path = str(rel_script)
            method.env = env_name
            session.commit()
            session.refresh(method)
            print(f"Updated method '{method_name}' (id={method.id}, env={env_name})")

        method_id = method.id

        口 = Step(step_num=4, name="Sync tracked functions and parameters",
                 purpose="Replace tracked function and parameter rows with fresh AST scan results")
        # Clear existing tracked functions (cascade to param_defs)
        existing_tfs = session.exec(
            select(TrackedFunction).where(
                TrackedFunction.method_id == method_id
            )
        ).all()
        for tf in existing_tfs:
            # Delete param_defs for this tracked function
            existing_pds = session.exec(
                select(ParamDef).where(
                    ParamDef.tracked_function_id == tf.id
                )
            ).all()
            for pd in existing_pds:
                session.delete(pd)
            session.delete(tf)
        session.commit()

        # Insert fresh tracked functions from AST scan
        for ordinal, func in enumerate(script_info.functions, start=1):
            tf = TrackedFunction(
                method_id=method_id,
                function_name=func.name,
                ordinal=ordinal,
            )
            session.add(tf)
            session.commit()
            session.refresh(tf)

            for param in func.params:
                pd_row = ParamDef(
                    tracked_function_id=tf.id,
                    param_name=param.name,
                    param_type=param.type_annotation,
                    default_value=param.default_value,
                )
                session.add(pd_row)

            session.commit()

            param_summary = ", ".join(
                f"{p.name}: {p.type_annotation or '?'}" + (f" = {p.default_value}" if p.default_value else "")
                for p in func.params
            )
            main_tag = " [main]" if func.is_main else ""
            ctx_tag = " [RunContext]" if func.uses_run_context else ""
            print(f"  {func.name}({param_summary}){main_tag}{ctx_tag}")

        口 = Step(step_num=5, name="Store method contract",
                 purpose="Store method.yaml slot definitions in the database (already parsed at step 3)")
        if contract_data is not None:
            if not contract_data["inputs"]:
                raise ValueError(
                    f"Method '{method_name}' has no input slots in method.yaml. "
                    f"Every method must declare at least one input so it can be "
                    f"wired to an upstream node in the canvas. If this method "
                    f"reads from WFC_INPUT_PATHS, declare that as an input slot "
                    f"(e.g. 'data: {{type: csv, required: true}}')."
                )

            # Remove existing contract for this method (upsert)
            existing_contract = session.exec(
                select(MethodContract).where(
                    MethodContract.method_id == method_id
                )
            ).first()
            if existing_contract is not None:
                session.delete(existing_contract)
                session.commit()

            mc = MethodContract(
                method_id=method_id,
                input_slots=contract_data["inputs"],
                output_slots=contract_data["outputs"],
                params_schema=contract_data["params"],
                executor=contract_data["executor"],
            )
            session.add(mc)
            session.commit()
            out_names = list(contract_data["outputs"].keys())
            print(f"  contract: {len(contract_data['inputs'])} input(s), "
                  f"{len(out_names)} output(s): {out_names}")

            # ADR-020: Tier-1 only — when the script uses the @wfc.method
            # decorator, statically validate ctx.save_artifact() literal names
            # against declared outputs. Tier-2 (no decorator) methods are
            # validated against method.yaml only (no AST save scan needed).
            if script_info.uses_wfc_method:
                from .method_ast import validate_save_artifacts
                required = [
                    name for name, spec in contract_data["outputs"].items()
                    if (spec or {}).get("required", True)
                ]
                save_warnings = validate_save_artifacts(
                    script_path,
                    declared_outputs=out_names,
                    required_outputs=required,
                )
                for w in save_warnings:
                    print(f"  WARNING: {w}")
        else:
            print("  contract: no method.yaml found — skipped")

        口 = Step(step_num=6, name="Validate method against module contract",
                 purpose="Check that method outputs conform to the module's required output contracts")
        module_contracts = session.exec(
            select(ModuleContract).where(
                ModuleContract.module_id == module.id
            )
        ).all()
        required_outputs = [
            mc for mc in module_contracts
            if mc.contract_type == "output" and mc.required
        ]
        if required_outputs and contract_data is not None:
            method_output_names = set(contract_data["outputs"].keys())
            missing = []
            for req in required_outputs:
                if req.name not in method_output_names:
                    missing.append(req.name)
            if missing:
                raise ValueError(
                    f"Method '{method_name}' is missing required module output(s): "
                    f"{missing}. Module '{module_name}' requires outputs: "
                    f"{[mc.name for mc in required_outputs]}. "
                    f"Method declares: {sorted(method_output_names)}"
                )
            print(f"  module contract: validated ({len(required_outputs)} required output(s))")
        elif required_outputs and contract_data is None:
            print(f"  module contract: skipped (no method.yaml to validate)")

    口 = Step(step_num=7, name="Commit method to git",
             purpose="Stage the method directory and create a git commit so the "
                     "method code version is captured in the cache key")
    _git_commit_registration(method_dir, method_name, module_name)

    口 = Step(step_num=8, name="Copy source files to registered location",
             purpose="Snapshot method source files into methods/{method_name}/ "
                     "so the code fingerprint is always computed from the "
                     "registered copy, not whatever is on disk at run time")
    project_dir = Path.cwd()
    registered_dir = project_dir / "methods" / method_name
    # Only copy if the source directory differs from the registered location
    if method_dir.resolve() != registered_dir.resolve():
        registered_dir.mkdir(parents=True, exist_ok=True)
        for py_file in method_dir.rglob("*.py"):
            rel = py_file.relative_to(method_dir)
            dest = registered_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(py_file, dest)
        # Also copy method.yaml if it exists (contract spec)
        yaml_file = method_dir / "method.yaml"
        if yaml_file.exists():
            shutil.copy2(yaml_file, registered_dir / "method.yaml")
        print(f"  source snapshot: copied to {registered_dir}")
    else:
        print(f"  source snapshot: already at registered location")

    return method_id
