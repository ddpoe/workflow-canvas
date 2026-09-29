"""Layout unit: the mount-table agreement oracle.

This oracle states the requirement: the Docker argv, the Apptainer argv and
host-to-container translation all derive from one mount table. Every row
the table carries appears in both argv's bind flags and translates under
its container root; a bind that one builder carries and the table does not
cannot exist.

Requirement: ``docs/system/layout.json``, section ``testing.requirements``
("Agreement oracle: one mount table").
"""
from __future__ import annotations

from pathlib import Path

from axiom_annotations import workflow

from wfc import layout
from wfc.environments.argv import build_apptainer_command, build_docker_command

IMAGE_REF = "ghcr.io/dante/image-io@sha256:" + ("a" * 64)


def _flag_values(argv: list[str], flag: str) -> list[str]:
    """Return the value following every occurrence of ``flag`` in ``argv``."""
    return [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == flag]


@workflow(purpose="With the Layout mount table as the single source, the "
                  "Docker argv, the Apptainer argv and host-to-container "
                  "translation reflect the same bind rows, row for row",
          inputs="A project root and its DVC cache dir",
          outputs="Both engines' bind flags equal to the table's bind specs, "
                  "and every row translating under its container root")
def test_mount_table_is_the_single_source(tmp_path):
    """One table; three consumers; no consumer with a row of its own."""
    # The cache sits beside the project here, not under it: with the default
    # nested cache the project row (first in the table) also covers every
    # cache path, and the cache row's own translation could not be observed
    # on its own.
    project = tmp_path / "proj"
    cache = tmp_path / "dvc cache"
    project.mkdir()
    cache.mkdir()
    table = layout.mount_table(project, cache)
    expected_binds = [layout.bind_spec(row) for row in table]

    docker = build_docker_command(IMAGE_REF, project, cache, ["python"], uid=1000, gid=1000)
    apptainer = build_apptainer_command(IMAGE_REF, project, cache, ["python"])

    # Every bind either engine carries is a table row, in table order —
    # so a mount added to one builder alone is not possible.
    assert _flag_values(docker, "-v") == expected_binds
    assert _flag_values(apptainer, "--bind") == expected_binds

    # The workdir both engines set is the project row's container root.
    assert _flag_values(docker, "-w") == [table[0].container]
    assert _flag_values(apptainer, "--pwd") == [table[0].container]

    # Translation walks the same rows: each host root maps to its container
    # root, a file beneath it to the same root plus the POSIX relative path.
    for row in table:
        assert layout.translate_host_path(str(row.host), table) == row.container
        beneath = Path(row.host) / "sub dir" / "f.txt"
        assert (layout.translate_host_path(str(beneath), table)
                == f"{row.container}/sub dir/f.txt")

    # Outside the table: a sibling whose name merely starts with the root's
    # name is not under it, and passes through unchanged.
    sibling = tmp_path / "proj-other" / "f.txt"
    assert layout.translate_host_path(str(sibling), table) == str(sibling)
