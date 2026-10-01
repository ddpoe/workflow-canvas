"""Method registration: scan a method's script, register it, snapshot and commit it.

The script is AST-scanned and registered with its tracked functions,
parameters and contract; its source snapshot is written and committed to git.

``register_method`` is the workflow, and each of its nine steps is a task.
Steps 2 to 9 share one database session, which commits only after the
snapshot is written and the git commit is made. A refusal at any step leaves
no rows, no snapshot change and no commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from axiom_annotations import AutoStep, task, workflow
from sqlmodel import Session, select

from .. import layout
from ..contracts import parse_method_yaml
from ..persistence import (
    Method,
    MethodContract,
    Module,
    ModuleContract,
    ParamDef,
    TrackedFunction,
    get_session,
)
from ..persistence import (
    project_root as get_project_root,
)
from .ast_scanner import ScriptInfo, scan_script
from .discovery import (
    _check_no_undeclared_scripts,
    _locate_method_script,
    _resolve_declared_helpers,
)
from .git_commit import _git_commit_registration
from .snapshot import (
    _backup_snapshot,
    _discard_backup,
    _restore_snapshot,
    _write_snapshot,
)


@dataclass(frozen=True)
class _LocatedScript:
    """What step 1 found, read by the later steps.

    Attributes:
        script_path: The method's main script.
        contract_data: The parsed ``method.yaml``, or ``None`` without one.
        strict_helpers: True when ``method.yaml`` has a ``helpers:`` key,
            even an empty one; the snapshot then holds exactly the declared
            set.
        helper_paths: The declared helpers, resolved; empty in permissive
            mode.
        script_info: The AST scan of a Python script; ``None`` for any
            other language.
    """

    script_path: Path
    contract_data: dict | None
    strict_helpers: bool
    helper_paths: list[Path]
    script_info: ScriptInfo | None


@task(
    purpose="Resolve the method script (explicit selector or extension probe) "
            "and, for Python scripts, extract function signatures via AST parsing",
    inputs="method directory, method name, optional explicit script name",
    outputs="the script, its parsed method.yaml, its declared helpers and its AST scan",
)
def _locate_and_scan(
    method_dir: Path,
    method_name: str,
    script_name: str | None,
) -> _LocatedScript:
    """Step 1: find the method's script and, for Python, scan it.

    ``method.yaml`` is parsed first: its optional ``script:`` key takes part
    in discovery, and parsing here keeps every discovery error ahead of any
    database write. Steps 3, 5 and 6 reuse the parsed contract.

    Args:
        method_dir: The method directory, resolved.
        method_name: The method's name.
        script_name: An explicit script filename, or ``None``.

    Returns:
        The located script, its contract, its helpers and its scan.
    """
    contract_data = parse_method_yaml(method_dir)
    if script_name is None and contract_data is not None:
        script_name = contract_data.get("script")

    script_path = _locate_method_script(method_dir, method_name, script_name)

    # Strict helpers mode: taken ONLY when the `helpers:` key is present
    # (even empty). Absent -> permissive mode.
    helpers_decl = contract_data.get("helpers") if contract_data is not None else None
    strict_helpers = helpers_decl is not None
    if helpers_decl is not None:
        helper_paths = _resolve_declared_helpers(
            method_dir, get_project_root(), helpers_decl, script_path
        )
        _check_no_undeclared_scripts(method_dir, script_path, helper_paths)
    else:
        helper_paths = []

    is_python = script_path.suffix == ".py"
    if is_python:
        script_info = scan_script(script_path)
        print(f"Scanned {script_path}: {len(script_info.functions)} function(s)")
    else:
        # Non-Python method: extension-only detection, no content sniffing.
        # Tracked-function metadata stays empty; the env record's interpreter
        # runs the script at dispatch time.
        script_info = None
        print(f"Located {script_path}: non-Python script (AST scan skipped)")

    return _LocatedScript(
        script_path=script_path,
        contract_data=contract_data,
        strict_helpers=strict_helpers,
        helper_paths=helper_paths,
        script_info=script_info,
    )


@task(
    purpose="Find the parent module in the database",
    inputs="open session, module name",
    outputs="the Module row",
)
def _resolve_module(session: Session, module_name: str) -> Module:
    """Step 2: find the module the method registers under.

    Args:
        session: The registration's open session.
        module_name: The module's name.

    Returns:
        The module row.

    Raises:
        ValueError: If no module has that name.
    """
    module = session.exec(
        select(Module).where(Module.name == module_name)
    ).first()
    if module is None:
        raise ValueError(
            f"Module '{module_name}' not found in DB. "
            f"Run 'wfc register-module --name {module_name}' first."
        )
    return module


@task(
    purpose="Refuse a method name another module already holds, because the "
            "source snapshot is keyed on the bare name and the two "
            "registrations would share one methods/<name>/ directory",
    inputs="open session, the resolved module row, the method name",
    outputs="nothing; raises when the name is held elsewhere",
    critical="Runs before the method row is written, so a refused "
             "registration leaves no rows behind. The shared snapshot "
             "directory is what makes the collision unsafe: the second "
             "registration overwrites the first's scripts AND its "
             "method.yaml, and both are read back by cache-key "
             "composition, so module A's keys would be computed from "
             "module B's code and contract.",
)
def _refuse_name_held_by_another_module(
    session: Session, module: Module, method_name: str
) -> None:
    """Step 3: refuse a method name that a different module already holds.

    The registered source snapshot lives at ``methods/<method_name>/``,
    keyed on the bare method name with no module segment. Two modules
    registering the same method name therefore write into one directory,
    and the second registration replaces the first's scripts and its
    ``method.yaml``. Cache-key composition reads both back from that
    directory, so module A's keys would silently be computed from module
    B's code and contract.

    Temporary by design: the guard exists only until the snapshot layout
    grows a module segment, which is why it is a registration check rather
    than a schema constraint a later migration would have to remove.

    Args:
        session: The registration's open session.
        module: The module row this registration resolved to.
        method_name: The method name being registered.

    Raises:
        ValueError: If a method with this name is registered under a
            different module.
    """
    held = session.exec(
        select(Method).where(
            Method.name == method_name,
            Method.module_id != module.id,
        )
    ).first()
    if held is None:
        return

    other = session.exec(
        select(Module).where(Module.id == held.module_id)
    ).first()
    other_name = other.name if other is not None else f"module id {held.module_id}"
    raise ValueError(
        f"Method '{method_name}' is already registered under module "
        f"'{other_name}', so it cannot also be registered under "
        f"'{module.name}'. Both registrations would share the one source "
        f"snapshot at methods/{method_name}/, and cache keys are computed "
        f"from that snapshot — '{other_name}' would start keying against "
        f"'{module.name}'s scripts and contract. Rename one of the two "
        f"methods. (The name will be free per-module once the snapshot "
        f"layout carries the module.)"
    )


@task(
    purpose="Create or update the method record linked to its module",
    inputs="open session, module row, method name, script path, parsed method.yaml",
    outputs="the method id; the row is flushed (committed with the registration)",
)
def _upsert_method(
    session: Session,
    module: Module,
    method_name: str,
    script_path: Path,
    contract_data: dict | None,
) -> int:
    """Step 4: validate the method's env and write its row.

    Execution is container-only, so a method must name a built container
    env; one without a ``method.yaml`` is refused here. The row is flushed
    before the later steps run.

    Args:
        session: The registration's open session.
        module: The module row from step 2.
        method_name: The method's name.
        script_path: The main script from step 1.
        contract_data: The parsed ``method.yaml`` from step 1, or ``None``.

    Returns:
        The method id.

    Raises:
        ValueError: If the method has no ``method.yaml``. An env the project
            manifest does not hold is refused by ``check_method_env``.
    """
    # Compute script_path relative to the project root
    try:
        rel_script = script_path.relative_to(get_project_root())
    except ValueError:
        rel_script = script_path

    method = session.exec(
        select(Method).where(
            Method.module_id == module.id,
            Method.name == method_name,
        )
    ).first()

    # Resolve env from method.yaml contract (parsed at step 1). Execution
    # is container-only, so a method must name a built
    # container env. A method with no method.yaml (hence no env) is
    # rejected here. ``parse_method_yaml`` already rejects a
    # present-but-missing/`inherit` env, so any contract that parses
    # carries a usable env value.
    if contract_data is None:
        raise ValueError(
            f"Method '{method_name}' has no method.yaml, so it does not "
            f"name a built container env. Add a method.yaml with "
            f"`env: <name>` (build it with `wfc register-env <name>`). "
            f"Methods run only in a container env."
        )
    env_name = contract_data["env"]

    # Validate the named env against the project manifest.
    project_dir = get_project_root()
    from ..environments import check_method_env
    check_method_env(env_name, project_dir)
    print(f"  Env: {env_name} (validated)")

    if method is None:
        method = Method(
            module_id=module.id,
            name=method_name,
            script_path=str(rel_script),
            env=env_name,
        )
        session.add(method)
        session.flush()
        session.refresh(method)
        print(f"Created method '{method_name}' (id={method.id}, env={env_name})")
    else:
        method.script_path = str(rel_script)
        method.env = env_name
        session.flush()
        session.refresh(method)
        print(f"Updated method '{method_name}' (id={method.id}, env={env_name})")

    assert method.id is not None  # assigned by the flush
    return method.id


@task(
    purpose="Replace tracked function and parameter rows with fresh AST scan results",
    inputs="open session, method id, the AST scan (None for a non-Python script)",
    outputs="the method's TrackedFunction and ParamDef rows, flushed",
)
def _sync_tracked_functions(
    session: Session,
    method_id: int,
    script_info: ScriptInfo | None,
) -> None:
    """Step 5: replace the method's tracked functions and parameters.

    The old rows are deleted and flushed first, then each scanned
    function is inserted with its parameters. A non-Python method has no
    scan and keeps no tracked rows.

    Args:
        session: The registration's open session.
        method_id: The method's id from step 4.
        script_info: The AST scan from step 1, or ``None``.
    """
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
    session.flush()

    # Insert fresh tracked functions from AST scan. Non-Python methods
    # have no AST scan — they register with empty tracked metadata (the
    # clearing loop above still ran, so a re-registered method that
    # switched language drops its stale rows).
    scanned_functions = script_info.functions if script_info is not None else []
    for ordinal, func in enumerate(scanned_functions, start=1):
        tf = TrackedFunction(
            method_id=method_id,
            function_name=func.name,
            ordinal=ordinal,
        )
        session.add(tf)
        session.flush()
        session.refresh(tf)

        for param in func.params:
            pd_row = ParamDef(
                tracked_function_id=tf.id,
                param_name=param.name,
                param_type=param.type_annotation,
                default_value=param.default_value,
            )
            session.add(pd_row)

        session.flush()

        param_summary = ", ".join(
            f"{p.name}: {p.type_annotation or '?'}" + (f" = {p.default_value}" if p.default_value else "")
            for p in func.params
        )
        main_tag = " [main]" if func.is_main else ""
        ctx_tag = " [RunContext]" if func.uses_run_context else ""
        print(f"  {func.name}({param_summary}){main_tag}{ctx_tag}")


@task(
    purpose="Store method.yaml slot definitions in the database (parsed at step 1) and refuse "
            "literal saves that don't match the declared outputs",
    inputs="open session, method id, method name, parsed method.yaml, AST scan, script path",
    outputs="the method's MethodContract row, flushed; warnings printed for dynamic or unreachable saves",
)
def _store_contract(
    session: Session,
    method_id: int,
    method_name: str,
    contract_data: dict | None,
    script_info: ScriptInfo | None,
    script_path: Path,
) -> None:
    """Step 6: store the method's contract and check its saves against it.

    A contract with no input slots is refused. An existing contract row is
    replaced. A script that uses the ``@wfc.method`` decorator then has its
    ``ctx.save_artifact()`` calls checked against the declared outputs,
    after the row is flushed: a literal save of an undeclared name, or a
    required output with no literal save, is refused. A dynamic
    (non-literal) name, or a save inside an obviously unreachable block
    (``if False:`` / ``if 0:``), prints a warning and does not count as a
    save of any output.

    Args:
        session: The registration's open session.
        method_id: The method's id from step 4.
        method_name: The method's name.
        contract_data: The parsed ``method.yaml`` from step 1, or ``None``.
        script_info: The AST scan from step 1, or ``None``.
        script_path: The main script from step 1.

    Raises:
        ValueError: If the contract declares no input slots, if the script
            saves a literal name the contract doesn't declare, or if a
            required output has no literal save.
    """
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
            session.flush()

        mc = MethodContract(
            method_id=method_id,
            input_slots=contract_data["inputs"],
            output_slots=contract_data["outputs"],
            params_schema=contract_data["params"],
            executor=contract_data["executor"],
        )
        session.add(mc)
        session.flush()
        out_names = list(contract_data["outputs"].keys())
        print(f"  contract: {len(contract_data['inputs'])} input(s), "
              f"{len(out_names)} output(s): {out_names}")

        # Tier-1 only — when the script uses the @wfc.method
        # decorator, statically validate ctx.save_artifact() literal names
        # against declared outputs. Tier-2 (no decorator) methods are
        # validated against method.yaml only (no AST save scan needed).
        # Non-Python scripts (script_info is None) get no static save
        # validation at all — method.yaml is their only contract.
        if script_info is not None and script_info.uses_wfc_method:
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


@task(
    purpose="Check that method outputs conform to the module's required output contracts",
    inputs="open session, module row, module name, method name, parsed method.yaml",
    outputs="nothing; a missing required output raises",
)
def _check_module_contract(
    session: Session,
    module: Module,
    module_name: str,
    method_name: str,
    contract_data: dict | None,
) -> None:
    """Step 7: check the method's outputs against the module's required ones.

    It runs after steps 4 to 6 have flushed; a refusal here rolls them back,
    so a refused method leaves no rows behind.

    Args:
        session: The registration's open session.
        module: The module row from step 2.
        module_name: The module's name.
        method_name: The method's name.
        contract_data: The parsed ``method.yaml`` from step 1, or ``None``.

    Raises:
        ValueError: If the method does not declare a required module output.
    """
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
        print("  module contract: skipped (no method.yaml to validate)")


@workflow(purpose="AST-scan a method script and register it with tracked functions and parameters")
def register_method(
    method_dir: Path,
    module_name: str,
    method_name: str | None = None,
    script_name: str | None = None,
    allow_reserved: bool = False,
) -> int:
    """Scan a method script and register it in the database.

    Performs nine steps, in order:
      1. Locate the script (explicit selector or extension probe) and, for
         Python scripts, AST-scan it
      2. Resolve the module
      3. Refuse a method name another module already holds
      4. Upsert the method row
      5. Sync its tracked functions and parameters (.py only)
      6. Store its method.yaml contract
      7. Check it against the module's required outputs
      8. Write the source snapshot under ``methods/<name>/``
      9. Commit the snapshot (and the source, when it is inside the
         repository) to git by pathspec

    Steps 2 to 9 share one session. Steps 4 to 6 only flush their rows; the
    session commits after step 9. A refusal at any step rolls the rows back,
    puts the snapshot directory back as it was, and makes no commit, so a
    refused registration leaves nothing behind. Anything the user staged
    before registering stays staged and uncommitted.

    Script discovery: an explicit *script_name* argument (CLI ``--script``)
    wins, then the optional ``script:`` key in method.yaml, then a probe for
    ``{method_name}.<ext>`` across the recognized extensions
    (``.py``/``.R``/``.r``/``.sh``). Zero or multiple probe matches error
    before any DB write. Non-Python scripts register with empty
    tracked-function metadata — no AST scan is attempted.

    Args:
        method_dir: Directory containing the method script.
        module_name: Name of the module this method belongs to.
        method_name: Method name (defaults to directory name).
        script_name: Explicit script filename (overrides method.yaml
            ``script:`` and the extension probe).
        allow_reserved: Allow a reserved ``__demo__*`` method or module
            name (``wfc demo`` only).

    Returns:
        The method ID.

    Raises:
        FileNotFoundError: If the script is not found.
        ValueError: If the module doesn't exist in the database, the script
            is ambiguous, or an explicit script has an unrecognized extension.
    """
    method_dir = Path(method_dir).resolve()
    if method_name is None:
        method_name = method_dir.name

    # Reserved-namespace guard: refuse a __demo__* method name AND
    # refuse attaching a method to a __demo__* module, before any filesystem
    # or DB work. `wfc demo` opts in via allow_reserved=True.
    from ..reserved import check_reserved_name
    check_reserved_name(method_name, "method", allow_reserved)
    check_reserved_name(module_name, "module", allow_reserved)

    口 = AutoStep(step_num=1, name="Locate and scan script")
    located = _locate_and_scan(method_dir, method_name, script_name)

    with get_session() as session:
        口 = AutoStep(step_num=2, name="Resolve module")
        module = _resolve_module(session, module_name)

        口 = AutoStep(step_num=3, name="Refuse a name another module holds")
        _refuse_name_held_by_another_module(session, module, method_name)

        口 = AutoStep(step_num=4, name="Upsert method row")
        method_id = _upsert_method(
            session, module, method_name, located.script_path,
            located.contract_data,
        )

        口 = AutoStep(step_num=5, name="Sync tracked functions and parameters")
        _sync_tracked_functions(session, method_id, located.script_info)

        口 = AutoStep(step_num=6, name="Store method contract")
        _store_contract(
            session, method_id, method_name, located.contract_data,
            located.script_info, located.script_path,
        )

        口 = AutoStep(step_num=7, name="Validate method against module contract")
        _check_module_contract(
            session, module, module_name, method_name, located.contract_data,
        )

        project_dir = get_project_root()
        snapshot_dir = layout.method_dir(project_dir, method_name)
        backup = _backup_snapshot(snapshot_dir)
        try:
            口 = AutoStep(step_num=8, name="Copy source files to registered location")
            _write_snapshot(
                method_dir, method_name, located.script_path,
                located.strict_helpers, located.helper_paths,
            )

            口 = AutoStep(step_num=9, name="Commit the snapshot to git by pathspec")
            _git_commit_registration(
                method_dir, snapshot_dir, method_name, module_name, project_dir,
            )

            session.commit()
        except BaseException:
            _restore_snapshot(snapshot_dir, backup)
            raise
        finally:
            _discard_backup(backup)

    return method_id
