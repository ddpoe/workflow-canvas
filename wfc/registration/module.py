"""Module registration: create or update a module row and its contracts."""

from __future__ import annotations

from pathlib import Path

from axiom_annotations import Step, task
from sqlmodel import select

from ..contracts import parse_module_yaml
from ..persistence import (
    get_session,
    Module,
    ModuleContract,
    project_root as get_project_root,
)


@task(purpose="Create or update a module in the database")
def register_module(
    name: str,
    contracts: list[dict] | None = None,
    description: str | None = None,
    module_dir: Path | None = None,
    allow_reserved: bool = False,
) -> int:
    """Create or update a module row.

    If a module with the same name already exists, updates its description
    and contracts. Otherwise creates a new row.

    Contracts can come from three sources (highest priority first):
      1. The ``contracts`` argument (explicit CLI JSON)
      2. A ``module.yaml`` file in ``module_dir``
      3. If neither is provided, raises TypeError

    When the contracts come from a ``module.yaml`` inside the project's git
    repository, that file is committed by pathspec before the rows are, so
    the tree is clean afterwards and nothing else the user staged is swept
    in. Outside a git repository nothing is committed.

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
        ValueError: If ``name`` uses the reserved ``__demo__`` prefix and
            ``allow_reserved`` is False.
    """
    from ..reserved import check_reserved_name
    check_reserved_name(name, "module", allow_reserved)

    # Resolve contracts from module.yaml if not explicitly provided
    yaml_read: Path | None = None
    if contracts is None and module_dir is not None:
        module_yaml_data = parse_module_yaml(module_dir)
        if module_yaml_data is not None:
            yaml_read = Path(module_dir).resolve() / "module.yaml"
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
            session.flush()
            session.refresh(module)
            print(f"Created module '{name}' (id={module.id})")
        else:
            if description is not None:
                module.description = description
            session.flush()
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
        session.flush()

        口 = Step(step_num=3, name="Commit module.yaml to git",
                 purpose="Commit the module.yaml the contracts were read from, "
                         "by pathspec, when it is inside the repository; then "
                         "commit the rows, so a failed git commit leaves none")
        if yaml_read is not None:
            from ..version import commit_paths
            sha = commit_paths(
                get_project_root(), [yaml_read],
                f"Register module {name}", require_repo=False,
            )
            if sha is not None:
                print(f"  git: committed module.yaml as {sha[:12]}")

        session.commit()
        print(f"  {len(contracts)} contract(s) registered")

    return module_id
