"""`wfc register-module` takes its contracts from a file, inline, or module.yaml.

The verb accepts ``--contracts`` as either a path to a JSON file or an
inline JSON string, and falls back to ``modules/<name>/module.yaml`` found
through the working directory when neither is given. Each route is driven
through ``cli_main`` and checked by reading the module's contract rows back
out of the database.

Requirement: ``docs/system/cli/catalog.json``, section
``registration-verbs.register-module-inputs``.
"""
from __future__ import annotations

import json

from axiom_annotations import Step, workflow
from sqlmodel import select

from wfc.persistence import Module, ModuleContract, get_session


def _registered_contracts(module_name: str) -> list[tuple]:
    """Return a module's contract rows as sorted comparable tuples.

    Args:
        module_name: The registered module's name.

    Returns:
        ``(contract_type, name, value_type, required)`` per row, sorted.
    """
    with get_session() as session:
        module = session.exec(
            select(Module).where(Module.name == module_name)
        ).one()
        rows = session.exec(
            select(ModuleContract).where(ModuleContract.module_id == module.id)
        ).all()
    return sorted(
        (c.contract_type, c.name, c.value_type, c.required) for c in rows
    )


@workflow(
    purpose="`wfc register-module` reads its contracts three ways and writes "
            "the named rows for each: --contracts naming a JSON file, "
            "--contracts carrying inline JSON, and neither — where the "
            "contracts come from modules/<name>/module.yaml, found through "
            "the working directory with no --module-dir given.",
    inputs="A contracts JSON file, an inline JSON string, and a module.yaml "
           "under the working directory",
    outputs="The module_contracts rows registered for each module",
)
def test_register_module_takes_contracts_from_file_inline_or_yaml(
    cli, tmp_project
):
    口 = Step(step_num=1, name="--contracts naming a JSON file",
             purpose="The path is read and parsed; the file's contracts are "
                     "what lands in the database")
    contracts_file = tmp_project / "contracts.json"
    contracts_file.write_text(
        json.dumps([
            {"type": "output", "name": "table",
             "value_type": ".parquet", "required": True},
            {"type": "metric", "name": "rows",
             "value_type": "int", "required": False},
        ]),
        encoding="utf-8",
    )

    result = cli("register-module", "--name", "file_mod",
                 "--contracts", str(contracts_file))
    assert result.returncode == 0, result.stderr
    assert _registered_contracts("file_mod") == [
        ("metric", "rows", "int", False),
        ("output", "table", ".parquet", True),
    ]

    口 = Step(step_num=2, name="--contracts as inline JSON",
             purpose="A JSON string that is no path is parsed as the "
                     "contracts themselves")
    result = cli(
        "register-module", "--name", "inline_mod",
        "--contracts",
        '[{"type": "metric", "name": "mcc", "value_type": "float"}]',
    )
    assert result.returncode == 0, result.stderr
    # `required` defaults to True when the contract does not say otherwise.
    assert _registered_contracts("inline_mod") == [
        ("metric", "mcc", "float", True),
    ]

    口 = Step(step_num=3, name="Neither, with a module.yaml in the tree",
             purpose="With no --contracts and no --module-dir the verb finds "
                     "modules/<name>/ relative to the working directory and "
                     "takes the contracts declared there",
             critical="The module directory is never named on the command "
                      "line — the working directory is what finds it")
    module_dir = tmp_project / "modules" / "yaml_mod"
    module_dir.mkdir(parents=True)
    (module_dir / "module.yaml").write_text(
        "description: Contracts declared beside the module\n"
        "contracts:\n"
        "  - type: output\n"
        "    name: model\n"
        "    value_type: model\n"
        "    required: true\n"
        "  - type: metric\n"
        "    name: auc\n"
        "    value_type: float\n"
        "    required: false\n",
        encoding="utf-8",
    )

    result = cli("register-module", "--name", "yaml_mod")
    assert result.returncode == 0, result.stderr
    assert _registered_contracts("yaml_mod") == [
        ("metric", "auc", "float", False),
        ("output", "model", "model", True),
    ]
