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

import pytest
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


@workflow(purpose="A run's extracted code copy is one more mount-table row, "
                  "bound read-only at the /work path translation already gives "
                  "it, rendered with :ro; the rows before it and the script "
                  "path the container sees are unchanged",
          inputs="A project root, its DVC cache dir and a code copy under the "
                 "artifact store",
          outputs="The base rows, then a read-only code row whose bind spec "
                  "ends in :ro")
def test_code_copy_row_is_bound_read_only_at_its_work_path(tmp_path):
    project = tmp_path / "proj"
    cache = tmp_path / "dvc cache"
    project.mkdir()
    cache.mkdir()
    copy = layout.code_copy_dir(project, "4b825dc6")

    base = layout.mount_table(project, cache)
    table = layout.mount_table(project, cache, code_copy=copy)

    # Without a copy nothing is read-only; with one, the base rows come
    # first, unchanged, and the copy's row is last.
    assert not any(row.read_only for row in base)
    assert table[:-1] == base
    code_row = table[-1]
    assert code_row.read_only
    assert code_row.container == "/work/.wfc/runs/code/4b825dc6"
    assert layout.bind_spec(code_row) == (
        f"{Path(copy).resolve().as_posix()}:/work/.wfc/runs/code/4b825dc6:ro")
    assert layout.bind_spec(base[0]) == f"{project.resolve().as_posix()}:/work"

    # The script path the container sees keeps its form: translation over
    # the table with the code row equals translation over the base table.
    script = copy / "sub dir" / "main.py"
    assert (layout.translate_host_path(str(script), table)
            == layout.translate_host_path(str(script), base)
            == "/work/.wfc/runs/code/4b825dc6/sub dir/main.py")


def test_a_code_copy_outside_the_project_root_is_refused(tmp_path):
    """A copy translation cannot place under /work has no container path."""
    project = tmp_path / "proj"
    project.mkdir()
    with pytest.raises(ValueError, match="not under the project root"):
        layout.mount_table(project, tmp_path / "cache",
                           code_copy=tmp_path / "elsewhere" / "4b825dc6")
