"""Method registration: scan a method's script, plan its registration, then write it.

Registration first works out what it will do, before anything is written
(``plan_method_registration``): the would-be snapshot is assembled outside
the project, fingerprinted and compared with the newest entry of the
method's version list, and the outcome is stated in one message: the method
is registered as v1, a new version is added (naming what changed), or the
content matches the newest version and nothing is added. Applying the plan
(``apply_method_registration``) writes the rows, rebuilds the snapshot in
the registry and, for a new version, appends an entry to the version list
and commits it with the snapshot in one git commit. ``register_method`` is
the two for Python callers, which confirm a new version explicitly with
``assume_yes``; ``wfc register-method`` asks the user between them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from axiom_annotations import AutoStep, Step, task, workflow
from sqlmodel import Session, select

from .. import layout
from ..contracts import declared_default_refusals, parse_method_yaml
from ..persistence import (
    Method,
    Module,
    ModuleContract,
    ParamDef,
    TrackedFunction,
    get_session,
)
from ..persistence import (
    project_root as get_project_root,
)
from .advice import not_copied_message
from .ast_scanner import ScriptInfo, scan_script
from .discovery import (
    _check_no_undeclared_scripts,
    _locate_method_script,
    _resolve_declared_helpers,
    check_method_folder,
    snapshot_selected_script,
)
from .method_version import (
    newest_entry,
    read_version_list,
    snapshot_code_fingerprint,
)
from .registered_code import restore_changed_snapshot
from .snapshot import _write_snapshot


@dataclass(frozen=True)
class MethodRegistration:
    """What a method registration did, returned by ``register_method``.

    Attributes:
        method_id: The registered method's id.
        not_copied: Source-relative POSIX paths of the files in the source
            directory the snapshot did not copy, sorted; empty when every
            file was copied.
        outcome: ``"new"``, ``"changed"`` or ``"same"``: the plan applied.
        label: The version added (the plan's label, or the next one when a
            concurrent registration committed that label first), or the
            newest version matched.
        message: The plan's message, the text the CLI printed.
    """

    method_id: int
    not_copied: tuple[str, ...] = ()
    outcome: str = "new"
    label: int | None = None
    message: str = ""

    @property
    def advice(self) -> str:
        """The advice naming the files not copied, or ``""`` when none were left out."""
        return not_copied_message(self.not_copied)


@dataclass(frozen=True)
class _LocatedScript:
    """What step 2 found, read by the later steps.

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
    """Step 2: find the method's script and, for Python, scan it.

    ``method.yaml`` is parsed first: its optional ``script:`` key takes part
    in discovery, and parsing here keeps every discovery error ahead of any
    database write. A folder that does not clearly hold one method (a second
    ``method.yaml``, or more than one script with none named) is refused
    before the script is located. Steps 4, 6 and 7 reuse the parsed
    contract.

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

    check_method_folder(method_dir, method_name, script_named=script_name is not None)
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
    """Step 3: find the module the method registers under.

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
    purpose="Refuse a method with no method.yaml, or whose env the project "
            "manifest does not hold, before anything is written",
    inputs="method name, parsed method.yaml",
    outputs="nothing; a refusal raises",
)
def _check_method_env(method_name: str, contract_data: dict | None) -> None:
    """Check the method names a built container env.

    Execution is container-only, so a method must name a built container
    env; one without a ``method.yaml`` is refused. ``parse_method_yaml``
    already refuses a present-but-missing or ``inherit`` env, so any
    contract that parses carries a usable env value.

    Args:
        method_name: The method's name.
        contract_data: The parsed ``method.yaml``, or ``None``.

    Raises:
        ValueError: If the method has no ``method.yaml``. An env the project
            manifest does not hold is refused by ``check_method_env``.
    """
    if contract_data is None:
        raise ValueError(
            f"Method '{method_name}' has no method.yaml, so it does not "
            f"name a built container env. Add a method.yaml with "
            f"`env: <name>` (build it with `wfc register-env <name>`). "
            f"Methods run only in a container env."
        )
    env_name = contract_data["env"]
    from ..environments import check_method_env
    check_method_env(env_name, get_project_root())
    print(f"  Env: {env_name} (validated)")


@task(
    purpose="Create or update the method record linked to its module",
    inputs="open session, module row, method name, the script's snapshot-relative path, parsed method.yaml",
    outputs="the method id; the row is flushed (committed with the registration)",
)
def _upsert_method(
    session: Session,
    module: Module,
    method_name: str,
    script_path: Path,
    contract_data: dict | None,
) -> int:
    """Write the method's row (the plan already checked its env).

    The row is flushed before the later steps run.

    Args:
        session: The registration's open session.
        module: The module row.
        method_name: The method's name.
        script_path: The main script's path inside the source directory,
            which is also its path inside the snapshot.
        contract_data: The parsed ``method.yaml``, checked by
            :func:`_check_method_env`.

    Returns:
        The method id.
    """
    rel_script = script_path.as_posix()

    method = session.exec(
        select(Method).where(
            Method.module_id == module.id,
            Method.name == method_name,
        )
    ).first()
    assert contract_data is not None  # the plan refused a method with none
    env_name = contract_data["env"]

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
        script_info: The AST scan from step 2, or ``None``.
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
    purpose="Check the method.yaml contract (parsed when the script was located): refuse "
            "no input slots, a declared param default that fails its own type or "
            "constraints, and literal saves that don't match the declared outputs",
    inputs="method name, parsed method.yaml, AST scan, script path",
    outputs="nothing written; warnings printed for dynamic or unreachable saves",
)
def _check_contract(
    method_name: str,
    contract_data: dict | None,
    script_info: ScriptInfo | None,
    script_path: Path,
) -> None:
    """Check the method's contract and its saves against it, writing nothing.

    A contract with no input slots is refused, as is one whose declared
    param default fails its own type or constraints
    (``declared_default_refusals``; the message names each parameter): a
    default runs whenever a node leaves its parameter out. A script that uses the
    ``@wfc.method`` decorator then has its ``ctx.save_artifact()`` calls
    checked against the declared outputs: a literal save of an undeclared
    name, or a required output with no literal save, is refused. A dynamic
    (non-literal) name, or a save inside an obviously unreachable block
    (``if False:`` / ``if 0:``), prints a warning and does not count as a
    save of any output. The contract row is written per version, with the
    version row.

    Args:
        method_name: The method's name.
        contract_data: The parsed ``method.yaml``, or ``None``.
        script_info: The AST scan, or ``None``.
        script_path: The main script.

    Raises:
        ValueError: If the contract declares no input slots, if a declared
            param default fails its own type or constraints, if the script
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

        # A declared default runs whenever a node leaves its parameter out,
        # so one that breaks its own type or constraints is refused here.
        # parse_method_yaml stays lenient: it also reads registered
        # versions' contracts, which must stay readable.
        bad_defaults = declared_default_refusals(contract_data["params"])
        if bad_defaults:
            raise ValueError(
                f"Method '{method_name}': method.yaml " + "; ".join(bad_defaults)
                + ". A default runs whenever a node leaves its parameter out: "
                  "fix it to pass its own type and constraints, or remove it."
            )

        # The contract row is written per version at step 11, with the
        # version row; here it is only checked.
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
        module: The module row from step 3.
        module_name: The module's name.
        method_name: The method's name.
        contract_data: The parsed ``method.yaml`` from step 2, or ``None``.

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


def _version_env(env_name: str, project_dir: Path) -> tuple[str, str]:
    """Return the env fingerprint and recorded image reference a version records.

    Both are read from the env's manifest record at registration.

    Args:
        env_name: The env the method's ``method.yaml`` names (validated).
        project_dir: The project root.

    Returns:
        ``(env_fingerprint, image_ref)``.

    Raises:
        ValueError: The env has no complete record (``resolve_env_fingerprint``).
    """
    from ..environments import get as env_get
    from ..environments import resolve_env_fingerprint

    env_fingerprint = resolve_env_fingerprint(env_name, project_dir)
    record = env_get(env_name, project_dir)
    assert record is not None  # resolve_env_fingerprint refused a missing record
    return env_fingerprint, record.container


#: The three outcomes a registration plan can hold.
NEW, CHANGED, SAME = "new", "changed", "same"


class RegistrationConfirmationRequired(RuntimeError):
    """A registration would add a version and its caller did not confirm it.

    Raised by :func:`register_method` without ``assume_yes`` when the plan
    adds a version; nothing has been written. Registration never asks: the
    CLI asks through :func:`plan_method_registration` and
    :func:`apply_method_registration`, and a programmatic caller passes
    ``assume_yes=True``.
    """


class RegistrationPlanChangedError(ValueError):
    """The snapshot a confirmed plan would write no longer has the planned identity."""


@dataclass(frozen=True)
class MethodRegistrationPlan:
    """What a method registration will do, worked out before anything is written.

    Attributes:
        module_name: The module's name.
        method_name: The method's name.
        method_dir: The source directory, resolved.
        outcome: ``"new"`` (registers v1), ``"changed"`` (adds a version) or
            ``"same"`` (matches the newest version; nothing is added).
        label: The version the registration adds, or, for ``"same"``, the
            newest version it matches.
        newest_label: The newest version's label before registering, or
            ``None`` when the method has no version list.
        code_fingerprint: The would-be snapshot's code fingerprint.
        env_name: The env its ``method.yaml`` names.
        env_fingerprint: That env's fingerprint from its manifest record.
        image_ref: That env's recorded image reference.
        message: The outcome stated for the user, the same text the CLI
            prints and the canvas shows, rendered from ``changes``.
        located: The located script, scan and contract the write reuses.
        changes: What registering changes against the newest version, as
            data (:func:`_plan_changes`): every entry added for a new
            method; empty lists for the same content.
        holds: What the version holds: ``{script, helpers, ignored}``, the
            main script, the other files the snapshot copies, and the source
            files it leaves out.
    """

    module_name: str
    method_name: str
    method_dir: Path
    outcome: str
    label: int
    newest_label: int | None
    code_fingerprint: str
    env_name: str
    env_fingerprint: str
    image_ref: str
    message: str
    located: _LocatedScript = field(repr=False)
    changes: dict = field(default_factory=dict)
    holds: dict = field(default_factory=dict)

    @property
    def adds_version(self) -> bool:
        """True when applying the plan adds a version (new or changed)."""
        return self.outcome != SAME

    @property
    def token(self) -> str:
        """The plan's identity as one opaque string (see :func:`plan_token`)."""
        return plan_token(self.module_name, self.method_name,
                          self.code_fingerprint, self.env_fingerprint,
                          self.newest_label)


def plan_token(module_name: str, method_name: str, code_fingerprint: str,
               env_fingerprint: str, newest_label: int | None) -> str:
    """One opaque string naming what a registration plan would write.

    Two plans with the same token write the same snapshot under the same env
    onto the same method's version list: the module and method name, the
    would-be snapshot's code fingerprint, the env's fingerprint and the
    newest label before registering.

    Args:
        module_name: The module's name.
        method_name: The method's name.
        code_fingerprint: The would-be snapshot's code fingerprint.
        env_fingerprint: The env's fingerprint from its manifest record.
        newest_label: The newest version's label, or ``None`` when the method
            has no version list.

    Returns:
        A sha256 hex digest of the five.
    """
    joined = (f"{module_name}\0{method_name}\0{code_fingerprint}\0"
              f"{env_fingerprint}\0{newest_label}")
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


#: The contract sections whose entries are slot declarations, with each
#: section's slot kind.
_SLOT_SECTIONS = (("inputs", "input"), ("outputs", "output"))


def _declarations(projection: dict, key: str) -> dict:
    """One declaration section of a projection, ``{}`` when absent or not a mapping."""
    section = projection.get(key)
    return section if isinstance(section, dict) else {}


def _entry_changes(before: dict, after: dict) -> list[dict]:
    """Each entry added, removed or changed between two declaration sections.

    Returns:
        ``{name, change, before, after}`` per differing entry, by name; the
        declaration missing on one side is ``None``.
    """
    out: list[dict] = []
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name), after.get(name)
        if old == new:
            continue
        change = ("added" if name not in before
                  else "removed" if name not in after else "changed")
        out.append({"name": name, "change": change, "before": old, "after": new})
    return out


def _plan_changes(newest, newest_facts, staged, env_name: str,
                  env_fingerprint: str) -> dict:
    """What registering would change, as data the message and the canvas read.

    Declarations are the contract projection's (descriptions are not part of
    a version, so a reworded description is never a change).

    Args:
        newest: The newest version-list entry, or ``None`` for a new method
            (every entry is then added).
        newest_facts: :func:`_copy_facts` of the newest version's tree, or
            ``None`` when git no longer holds it.
        staged: :func:`_copy_facts` of the would-be snapshot.
        env_name: The env the would-be snapshot names.
        env_fingerprint: That env's fingerprint now.

    Returns:
        ``{code, slots, params, run, env}``: ``code`` is ``{changed, added,
        removed}`` file lists, or ``None`` when the newest version's files
        cannot be read (``slots``, ``params`` and ``run`` are then empty:
        nothing was compared); ``slots`` is ``{name, kind, change, before, after}``
        per changed slot and ``params`` the same without ``kind``; ``run`` is
        ``{key: [before, after]}`` per other top-level key that changed
        (``executor``, ``gpus``, ``script``); ``env`` is ``{before, after,
        rebuilt}``.
    """
    env = {
        "before": newest.env_name if newest is not None else None,
        "after": env_name,
        "rebuilt": (newest is not None and env_name == newest.env_name
                    and env_fingerprint != newest.env_fingerprint),
    }
    if newest is not None and newest_facts is None:
        return {"code": None, "slots": [], "params": [], "run": {}, "env": env}
    old_files, old_proj = (newest_facts[0], newest_facts[2] or {}) if newest_facts else ({}, {})
    new_files, new_proj = staged[0], staged[2] or {}
    code = {
        "changed": sorted(p for p in set(old_files) & set(new_files)
                          if old_files[p] != new_files[p]),
        "added": sorted(set(new_files) - set(old_files)),
        "removed": sorted(set(old_files) - set(new_files)),
    }
    slots = [
        {"name": e["name"], "kind": kind, **{k: e[k] for k in ("change", "before", "after")}}
        for key, kind in _SLOT_SECTIONS
        for e in _entry_changes(_declarations(old_proj, key), _declarations(new_proj, key))
    ]
    params = _entry_changes(_declarations(old_proj, "params"),
                            _declarations(new_proj, "params"))
    sections = {"inputs", "outputs", "params"}
    run = {
        key: [old_proj.get(key), new_proj.get(key)]
        for key in sorted((set(old_proj) | set(new_proj)) - sections)
        if old_proj.get(key) != new_proj.get(key)
    }
    return {"code": code, "slots": slots, "params": params, "run": run, "env": env}


def _contract_phrases(changes: dict) -> list[str]:
    """Name each contract key that changed, as the message words it.

    Returns:
        One phrase per top-level key, in key order: a declaration section
        (``inputs``, ``outputs``, ``params``) names each entry added, removed
        or changed; any other key reads ``<key> changed``.
    """
    sections: dict[str, list[dict]] = {key: [] for key, _ in _SLOT_SECTIONS}
    for slot in changes["slots"]:
        sections["inputs" if slot["kind"] == "input" else "outputs"].append(slot)
    sections["params"] = changes["params"]
    phrases: dict[str, str] = {
        key: f"{key}: " + ", ".join(f"{e['name']} {e['change']}" for e in entries)
        for key, entries in sections.items() if entries
    }
    phrases.update({key: f"{key} changed" for key in changes["run"]})
    return [phrases[key] for key in sorted(phrases)]


def _plan_holds(staging: Path, script_path: Path, method_dir: Path,
                not_copied: list[str]) -> dict:
    """What a version holds: its main script, its helpers and the files left out.

    Args:
        staging: The would-be snapshot.
        script_path: The main script in the source directory.
        method_dir: The source directory, resolved.
        not_copied: The source files the snapshot did not copy.

    Returns:
        ``{script, helpers, ignored}``: snapshot-relative POSIX paths, sorted.
    """
    script = script_path.relative_to(method_dir).as_posix()
    helpers = sorted(
        rel for rel in (p.relative_to(staging).as_posix()
                        for p in staging.rglob("*") if p.is_file())
        if rel not in (script, "method.yaml")
    )
    return {"script": script, "helpers": helpers, "ignored": list(not_copied)}


def _copy_facts(copy_dir: Path, method_name: str
                ) -> tuple[dict[str, str], str | None, dict | None, str | None]:
    """Read a copy's file digests, ``method.yaml`` text, projection and main script.

    The main script is the one the copy selects, copy-relative POSIX, or
    ``None`` when it selects none.
    """
    import json

    from ..contracts import render_contract_projection
    from ..identity import fingerprinted_file_digests

    yaml_path = copy_dir / "method.yaml"
    text = yaml_path.read_text(encoding="utf-8") if yaml_path.is_file() else None
    try:
        rendered = render_contract_projection(parse_method_yaml(copy_dir))
        projection = json.loads(rendered) if rendered else None
    except ValueError:
        projection = None
    selected = snapshot_selected_script(copy_dir, method_name)
    script = (selected.relative_to(copy_dir.resolve()).as_posix()
              if selected is not None else None)
    return fingerprinted_file_digests(copy_dir), text, projection, script


def _newest_copy_facts(project_dir: Path, tree_hash: str, method_name: str):
    """Read :func:`_copy_facts` from the newest version's git tree.

    Returns:
        The facts, or ``None`` when git no longer holds the tree.
    """
    import tempfile

    from ..version import NotAGitRepositoryError, archive_tree

    with tempfile.TemporaryDirectory(prefix="wfc-newest-") as tmp:
        dest = Path(tmp) / "copy"
        dest.mkdir()
        try:
            if not archive_tree(project_dir, tree_hash, dest):
                return None
        except (NotAGitRepositoryError, RuntimeError):
            return None
        return _copy_facts(dest, method_name)


def _lf(text: str | None) -> str | None:
    """Normalise line endings, as the fingerprint reads text."""
    return None if text is None else text.replace("\r\n", "\n").replace("\r", "\n")


def _changed_lines(newest, changes: dict) -> list[str]:
    """Name what changed since the newest version: code files, contract, env.

    Args:
        newest: The newest version-list entry.
        changes: :func:`_plan_changes` against it.

    Returns:
        Lines for the message, without indentation.
    """
    lines: list[str] = []
    since = f"v{newest.label}"
    code = changes["code"]
    if code is None:
        lines.append(f"Code: {since}'s files are no longer in git, so the "
                     f"changed files cannot be named.")
    else:
        for kind in ("changed", "added", "removed"):
            if code[kind]:
                lines.append(f"Code files {kind} since {since}: {', '.join(code[kind])}")
        contract = _contract_phrases(changes)
        if contract:
            lines.append(f"Params/contract (method.yaml) changed since {since}: "
                         f"{'; '.join(contract)}")
    env = changes["env"]
    if env["after"] != env["before"]:
        lines.append(f"Env: {since} ran in {env['before']}; this version "
                     f"runs in {env['after']}")
    elif env["rebuilt"]:
        lines.append(f"Env: {env['after']} was rebuilt since {since} (its "
                     f"fingerprint changed)")
    return lines


def _refuse_crlf_shell_scripts(staging: Path, ref: str) -> None:
    r"""Refuse a ``.sh`` in the would-be snapshot that holds a CRLF.

    A shell script with Windows line endings fails in a Linux container
    (``bash: $'\r': command not found``), so it is refused at registration,
    with the fix, rather than at its first run.

    Args:
        staging: The would-be snapshot, assembled outside the project.
        ref: ``<module>/<method>``, for the message.

    Raises:
        ValueError: Naming each such script and how to convert it to LF.
    """
    crlf = sorted(
        p.relative_to(staging).as_posix()
        for p in staging.rglob("*")
        if p.is_file() and p.suffix.lower() == ".sh" and b"\r\n" in p.read_bytes()
    )
    if crlf:
        raise ValueError(
            f"Cannot register {ref}: {', '.join(crlf)} "
            f"{'has' if len(crlf) == 1 else 'have'} Windows (CRLF) line endings, "
            f"which a shell in a Linux container cannot run. Convert "
            f"{'it' if len(crlf) == 1 else 'them'} to LF (your editor's line-ending "
            f"setting, or `dos2unix <file>`) and register again."
        )


@workflow(purpose="Work out what registering a method would do before anything is "
                  "written: register it as v1, add a version, or match the newest "
                  "version and add nothing, with the message that says so")
def plan_method_registration(
    method_dir: Path,
    module_name: str,
    method_name: str | None = None,
    script_name: str | None = None,
    allow_reserved: bool = False,
) -> MethodRegistrationPlan:
    """Plan a method registration: the outcome and its message, nothing written.

    Restore-on-change runs first (step 1) and, when the registered copy was
    changed outside wfc, restores it and stops the registration as before:
    it adds no version and is not confirmed. Every other refusal that reads
    only (a folder that does not clearly hold one method, script discovery,
    the env, the contract and its saves, an unknown module, the module's
    required outputs, a new version whose main script has another file name
    than the newest version's) is made here, so a user is never asked and
    then refused. The would-be snapshot is assembled in a temporary
    directory outside the project, fingerprinted and compared with the
    newest entry of the method's version list; no row is flushed, no
    snapshot or list is written and nothing is committed.

    The outcome is one of three. New: the method has no version list (a
    method registered before versions were kept takes this outcome too,
    worded so it stays true). Changed: its content identity (code and env
    fingerprints) differs from the newest version's, even when it equals an
    earlier version's; the message names the code files, the params or
    contract and the env that changed, rendered from the plan's ``changes``
    (the same data the canvas shows). Same: it equals the newest version's;
    when the would-be ``method.yaml`` differs from the newest version's only
    in what runs do not use (descriptions, comments, ordering), the message
    says those edits are not kept.

    Args:
        method_dir: Directory containing the method script.
        module_name: Name of the module this method belongs to.
        method_name: Method name (defaults to directory name).
        script_name: Explicit script filename (overrides method.yaml
            ``script:`` and the extension probe).
        allow_reserved: Allow a reserved ``__demo__*`` method or module
            name (``wfc demo`` only).

    Returns:
        The plan, for :func:`apply_method_registration`.

    Raises:
        FileNotFoundError: If the script is not found.
        ValueError: A refusal (the source is inside the registry, the module
            is unknown, the script is ambiguous, the env or contract is
            refused), or the snapshot was changed outside wfc and has been
            restored (``SnapshotChangedError``).
    """
    import shutil
    import tempfile

    method_dir = Path(method_dir).resolve()
    if method_name is None:
        method_name = method_dir.name

    # Reserved-namespace guard: refuse a __demo__* method name AND
    # refuse attaching a method to a __demo__* module, before any filesystem
    # or DB work. `wfc demo` opts in via allow_reserved=True.
    from ..contracts import validate_env_name
    from ..reserved import check_reserved_name
    validate_env_name(method_name, "method")
    validate_env_name(module_name, "module")
    check_reserved_name(method_name, "method", allow_reserved)
    check_reserved_name(module_name, "module", allow_reserved)

    project_dir = get_project_root()
    registry = layout.methods_dir(project_dir).resolve()
    if method_dir == registry or registry in method_dir.parents:
        raise ValueError(
            f"Cannot register from {method_dir}: it is inside the registry "
            f"({registry}), which holds wfc's registered copies. Register "
            f"from your own source directory with `wfc register-method`."
        )

    口 = AutoStep(step_num=1, name="Restore a snapshot changed outside wfc")
    restore_changed_snapshot(module_name, method_name, project_dir)

    口 = AutoStep(step_num=2, name="Locate and scan script")
    located = _locate_and_scan(method_dir, method_name, script_name)

    口 = AutoStep(step_num=3, name="Check the method names a built env")
    _check_method_env(method_name, located.contract_data)

    口 = AutoStep(step_num=4, name="Check the contract and the script's saves")
    _check_contract(method_name, located.contract_data, located.script_info,
                    located.script_path)

    with get_session() as session:
        口 = AutoStep(step_num=5, name="Resolve module")
        module = _resolve_module(session, module_name)

        口 = AutoStep(step_num=6, name="Validate method against module contract")
        _check_module_contract(
            session, module, module_name, method_name, located.contract_data,
        )
        has_row = session.exec(select(Method).where(
            Method.module_id == module.id, Method.name == method_name,
        )).first() is not None

    口 = Step(step_num=7, name="Assemble and fingerprint the would-be snapshot",
             purpose="The snapshot registration would write, built the same way "
                     "in a temporary directory, and its content identity",
             critical="Outside the project: the registered copy, the version "
                      "list, the rows and git are untouched")
    staging_root = Path(tempfile.mkdtemp(prefix="wfc-plan-"))
    try:
        staging = staging_root / method_name
        not_copied = _write_snapshot(
            method_dir, staging, located.script_path,
            located.strict_helpers, located.helper_paths, method_name,
        )
        _refuse_crlf_shell_scripts(staging, f"{module_name}/{method_name}")
        code_fingerprint = snapshot_code_fingerprint(staging)
        env_name = located.contract_data["env"]
        env_fingerprint, image_ref = _version_env(env_name, project_dir)
        newest = newest_entry(read_version_list(project_dir, module_name, method_name))
        holds = _plan_holds(staging, located.script_path, method_dir, not_copied)
        staged = _copy_facts(staging, method_name)
        newest_facts = (_newest_copy_facts(project_dir, newest.tree_hash, method_name)
                        if newest is not None else None)

        口 = Step(step_num=8, name="Refuse a new version whose main script has another file name",
                 purpose="The newest version's main script and the would-be "
                         "snapshot's must be the same file, so a folder holding "
                         "another method's script never registers as its version",
                 critical="Read from the newest version's tree; a tree git no "
                          "longer holds cannot be compared and is not refused")
        ref = f"{module_name}/{method_name}"
        if (newest is not None and newest_facts is not None
                and newest_facts[3] not in (None, holds["script"])):
            raise ValueError(
                f"Cannot register {ref} from {method_dir}: its main script is "
                f"{holds['script']}, but v{newest.label}'s is {newest_facts[3]}. "
                f"A new version keeps its main script's file name: rename "
                f"{holds['script']} to {newest_facts[3]}, or check that this "
                f"folder holds {ref}."
            )

        口 = Step(step_num=9, name="Compare with the newest version and state the outcome",
                 purpose="New, changed (naming the code files, params/contract "
                         "and env that changed) or same; the changes are worked "
                         "out once, as data, and the message is rendered from them",
                 critical="Only the newest version counts: an earlier version's "
                          "content makes a new label")
        changes = _plan_changes(newest, newest_facts, staged, env_name, env_fingerprint)
        if newest is None:
            outcome, label = NEW, 1
            message = (
                f"{ref} has no versions yet; this registers it as v1."
                if has_row else
                f"{ref} is not registered yet; this registers it as v1."
            )
        else:
            if (newest.code_fingerprint == code_fingerprint
                    and newest.env_fingerprint == env_fingerprint):
                outcome, label = SAME, newest.label
                lines = [f"{ref} matches its newest version v{label}; no "
                         f"version is added."]
                if newest_facts is not None and _lf(newest_facts[1]) != _lf(staged[1]):
                    lines.append(
                        f"  Your method.yaml edits change nothing a run uses "
                        f"(such as descriptions, comments or ordering), so they "
                        f"are not kept: the registered copy keeps "
                        f"v{label}'s method.yaml."
                    )
            else:
                outcome, label = CHANGED, newest.label + 1
                lines = [f"{ref} is already registered (newest v{newest.label}); "
                         f"this adds v{label}."]
                lines += [f"  {line}" for line in _changed_lines(newest, changes)]
            message = "\n".join(lines)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

    return MethodRegistrationPlan(
        module_name=module_name, method_name=method_name, method_dir=method_dir,
        outcome=outcome, label=label,
        newest_label=newest.label if newest is not None else None,
        code_fingerprint=code_fingerprint, env_name=env_name,
        env_fingerprint=env_fingerprint, image_ref=image_ref,
        message=message, located=located, changes=changes, holds=holds,
    )


def check_still_planned(plan: MethodRegistrationPlan, token: str) -> None:
    """Refuse when a registration's identity is not the one its plan stated.

    The one rule every confirmation goes through: the plan's token
    (:attr:`MethodRegistrationPlan.token`) must equal the token checked
    against it. The CLI checks the rebuilt snapshot's identity against the
    plan it showed; the canvas checks a fresh plan against the token its dry
    run returned.

    Args:
        plan: A registration plan.
        token: The identity checked against it: the rebuilt snapshot's, or
            the one a dry run returned and the user confirmed.

    Raises:
        RegistrationPlanChangedError: The source, the env or the version list
            changed after the plan was made.
    """
    if plan.token != token:
        raise RegistrationPlanChangedError(
            f"Cannot register '{plan.module_name}/{plan.method_name}': its "
            f"source, its env or its version list changed after registration "
            f"said what it would do. Nothing was registered. Plan it again "
            f"(`wfc register-method`, or Check again in the canvas) to see "
            f"what it will do now."
        )


def _check_still_planned(plan: MethodRegistrationPlan, code_fingerprint: str,
                         env_fingerprint: str, newest) -> None:
    """Refuse when the rebuilt snapshot is not the one the plan stated.

    Raises:
        RegistrationPlanChangedError: The source, the env or the version list
            changed after the plan was made.
    """
    newest_label = newest.label if newest is not None else None
    check_still_planned(plan, plan_token(plan.module_name, plan.method_name,
                                         code_fingerprint, env_fingerprint,
                                         newest_label))


@task(
    purpose="Write a registration's rows in one transaction: the module's "
            "method row, its tracked functions and parameters, and the version "
            "rows the version list holds, its own version's with its commit "
            "and contract",
    inputs="the plan, the entry registration committed and its commit",
    outputs="the method id; every row or none",
    critical="One write transaction, locked from its start; a unique-label "
             "conflict with a concurrent writer reads the list again and "
             "retries once",
)
def _write_registration_rows(plan: MethodRegistrationPlan, entry, commit_sha: str | None,
                             project_dir: Path) -> int:
    """Write the method rows and the version rows together, or none of them.

    Args:
        plan: The registration plan applied.
        entry: The entry registration appended, or ``None`` for the same
            content.
        commit_sha: The registration commit, or ``None``.
        project_dir: The project root.

    Returns:
        The method id.
    """
    from sqlalchemy.exc import IntegrityError

    from ..persistence import write_transaction
    from .registered_code import sync_rows_in

    located = plan.located
    for attempt in range(2):
        try:
            with write_transaction() as session:
                module = _resolve_module(session, plan.module_name)
                method_id = _upsert_method(
                    session, module, plan.method_name,
                    located.script_path.relative_to(plan.method_dir),
                    located.contract_data,
                )
                _sync_tracked_functions(session, method_id, located.script_info)
                sync_rows_in(session, plan.module_name, plan.method_name,
                             read_version_list(project_dir, plan.module_name,
                                               plan.method_name),
                             project_dir, registered=entry,
                             registered_commit=commit_sha,
                             registered_contract=located.contract_data)
            return method_id
        except IntegrityError:
            if attempt:
                raise
    raise AssertionError("unreachable")  # pragma: no cover


@workflow(purpose="Write what a registration plan states: build the snapshot in "
                  "a staging copy, check it is the planned one, commit exactly the "
                  "tree it verified with its version list, put the registry in "
                  "step with HEAD, then write the rows in one transaction; a "
                  "refusal changes nothing")
def apply_method_registration(plan: MethodRegistrationPlan) -> MethodRegistration:
    """Apply a registration plan the caller has confirmed (or that needs none).

    The snapshot is built from the source in a private staging directory
    (Layout's registry staging directory, on the registry's filesystem) and
    fingerprinted: when its content identity, or the newest version, is not
    the plan's, the registration is refused and nothing is written, so
    content nobody confirmed is never registered. The commit
    (:func:`~wfc.registration.git_commit.commit_registration`) first checks
    that the tree git would commit for the copy, extracted as a run extracts
    it, is the copy, and refuses naming each file and the reason otherwise;
    then commits it with the version list from a temporary index on HEAD,
    renames the list and then each file of the copy over the snapshot (a
    reader finds each file's old bytes or its new ones, never a partial
    file or a missing one) and sets the index to HEAD at the registry
    paths. The method rows and the version rows are written last, in one
    transaction. A refusal at any step commits nothing and leaves
    the snapshot, the version list and the user's index as they were;
    anything the user staged stays staged and uncommitted.

    Args:
        plan: What :func:`plan_method_registration` returned.

    Returns:
        A :class:`MethodRegistration`: the method ID, the source files the
        snapshot did not copy, and the outcome applied with its message.

    Raises:
        RegistrationPlanChangedError: The snapshot is not the planned one.
        ValueError: git would not commit the snapshot as it was checked.
        NotAGitRepositoryError: The project is not a git repository.
    """
    import shutil
    import tempfile

    from .git_commit import commit_registration

    located = plan.located
    module_name, method_name = plan.module_name, plan.method_name
    project_dir = get_project_root()
    staging_root = layout.registry_staging_dir(project_dir)
    staging_root.mkdir(parents=True, exist_ok=True)
    holder = Path(tempfile.mkdtemp(dir=staging_root, prefix="m-"))
    try:
        staging = holder / method_name

        口 = AutoStep(step_num=1, name="Build the snapshot in a staging copy")
        not_copied = _write_snapshot(
            plan.method_dir, staging, located.script_path,
            located.strict_helpers, located.helper_paths, method_name,
        )

        口 = Step(step_num=2, name="Check the staging copy is the one the plan stated",
                 purpose="Fingerprint the staging copy and read the newest version "
                         "again; either differing from the plan refuses the "
                         "registration",
                 critical="The source can change while the user is asked; "
                          "unconfirmed content is never registered")
        code_fingerprint = snapshot_code_fingerprint(staging)
        env_fingerprint, image_ref = _version_env(plan.env_name, project_dir)
        newest = newest_entry(read_version_list(project_dir, module_name, method_name))
        _check_still_planned(plan, code_fingerprint, env_fingerprint, newest)

        口 = AutoStep(step_num=3, name="Verify and commit the tree, then put the registry in step")
        committed = commit_registration(
            project_dir, module_name, method_name, staging,
            same=plan.outcome == SAME, newest=newest,
            code_fingerprint=code_fingerprint, env_name=plan.env_name,
            env_fingerprint=env_fingerprint, image_ref=image_ref,
        )
        if committed.entry is not None:
            if committed.entry.label != plan.label:
                print(f"  another registration of {module_name}/{method_name} "
                      f"took v{plan.label} first; this version is "
                      f"v{committed.entry.label}")
            print(f"  source snapshot: copied to "
                  f"{layout.method_dir(project_dir, module_name, method_name)} "
                  f"and committed as v{committed.entry.label}")
    finally:
        shutil.rmtree(holder, ignore_errors=True)

    口 = AutoStep(step_num=4, name="Write the method rows and the version rows")
    method_id = _write_registration_rows(plan, committed.entry, committed.commit,
                                         project_dir)

    registration = MethodRegistration(
        method_id=method_id, not_copied=tuple(not_copied),
        outcome=plan.outcome,
        label=committed.entry.label if committed.entry is not None else plan.label,
        message=plan.message,
    )
    if registration.advice:
        print(f"  {registration.advice}")
    return registration


@workflow(purpose="Register a method from Python: plan the outcome before any write, "
                  "refuse an unconfirmed new version, then apply the plan")
def register_method(
    method_dir: Path,
    module_name: str,
    method_name: str | None = None,
    script_name: str | None = None,
    allow_reserved: bool = False,
    *,
    assume_yes: bool = False,
    planned: str | None = None,
) -> MethodRegistration:
    """Register a method script: plan, confirm, apply.

    The registration is planned first (:func:`plan_method_registration`:
    restore-on-change, discovery and every read-only refusal, the would-be
    snapshot's identity compared with the newest version), and its message
    printed. Registration never asks: a plan that adds a version (a new
    method, or a new version) is applied only with ``assume_yes=True``, the
    explicit confirmation every programmatic caller (the demo scaffold, the
    canvas route, test fixtures) passes; without it nothing is written and
    :class:`RegistrationConfirmationRequired` is raised. A plan that matches
    the newest version needs no confirmation: it adds nothing, and still
    commits the method row and tracked functions and syncs any version rows
    the database lacks. ``wfc register-method`` asks the user between the
    plan and :func:`apply_method_registration` instead of calling this.

    Registration always copies: the source directory is the author's, may
    live anywhere, and is never committed or read again. A source inside
    the registry is refused.

    Script discovery: an explicit *script_name* argument (CLI ``--script``)
    wins, then the optional ``script:`` key in method.yaml, then a probe for
    ``{method_name}.<ext>`` across the recognized extensions
    (``.py``/``.R``/``.r``/``.sh``). A folder holding more than one
    recognized script must name the main one (``script:`` or ``--script``),
    a second ``method.yaml`` in the folder is refused, and zero or multiple
    probe matches error, all before any write. Non-Python scripts register with empty
    tracked-function metadata — no AST scan is attempted.

    Args:
        method_dir: Directory containing the method script.
        module_name: Name of the module this method belongs to.
        method_name: Method name (defaults to directory name).
        script_name: Explicit script filename (overrides method.yaml
            ``script:`` and the extension probe).
        allow_reserved: Allow a reserved ``__demo__*`` method or module
            name (``wfc demo`` only).
        assume_yes: Confirm a plan that adds a version.
        planned: The plan token (:attr:`MethodRegistrationPlan.token`) of a
            plan the caller showed and the user confirmed, as the canvas's
            dry run returns it. Given, the registration applies only when
            the fresh plan has that token (:func:`check_still_planned`);
            otherwise it is refused having written nothing.

    Returns:
        A :class:`MethodRegistration`: the method ID, the source files the
        snapshot did not copy (the same files the printed advice names), and
        the outcome applied with its message.

    Raises:
        FileNotFoundError: If the script is not found.
        ValueError: If the module doesn't exist in the database, the script
            is ambiguous, or an explicit script has an unrecognized extension.
        RegistrationConfirmationRequired: The plan adds a version and
            ``assume_yes`` is not set.
        RegistrationPlanChangedError: ``planned`` is given and the fresh
            plan's token differs from it.
    """
    口 = AutoStep(step_num=1, name="Plan the registration before any write")
    plan = plan_method_registration(
        method_dir, module_name, method_name, script_name, allow_reserved,
    )
    print(plan.message)

    口 = Step(step_num=2, name="Confirmation boundary",
             purpose="A plan that adds a version is applied only with the "
                     "caller's explicit assume_yes; the same-as-newest plan "
                     "needs none. A caller that names the plan it confirmed "
                     "gets that plan or a refusal",
             critical="Registration never prompts, reads stdin or checks for a "
                      "terminal; the CLI asks the user instead")
    if planned is not None:
        check_still_planned(plan, planned)
    if plan.adds_version and not assume_yes:
        raise RegistrationConfirmationRequired(
            f"{plan.message}\nThis registration adds a version and was not "
            f"confirmed, so nothing was registered. Pass assume_yes=True to "
            f"register it."
        )

    口 = AutoStep(step_num=3, name="Apply the plan")
    registration = apply_method_registration(plan)
    return registration
