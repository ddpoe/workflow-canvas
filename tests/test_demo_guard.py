"""Reserved ``__demo__`` prefix guard (safety-chain link 1).

``wfc demo --remove`` deletes by the ``__demo__`` tag, so the tag must be
proof of demo ownership: no user-driven registration path may create a
``__demo__``-prefixed name. The guard lives on the library-level functions
that both the CLI dispatches and the Canvas Registry endpoints wrap, with an
explicit ``allow_reserved=True`` opt-in used only by ``wfc demo`` itself.
"""

import pytest
from axiom_annotations import workflow

from wfc.environments import register as register_env
from wfc.registration import register_method, register_module
from wfc.reserved import DEMO_ENV_NAME


def test_the_demo_env_is_recreated_by_wfc_demo_force_and_no_other_name_is():
    """Tier 1: only the demo env's exact name is recreated by
    ``wfc demo --force``; a ``__demo__``-prefixed env (an older demo's) gets
    the generic register-env command, as every other env does."""
    from wfc.environments.manifest import recreate_command

    assert recreate_command(DEMO_ENV_NAME) == "wfc demo --force"
    assert recreate_command("__demo__env") == "wfc register-env __demo__env --force"
    assert recreate_command("tools") == "wfc register-env tools --force"


@workflow(purpose="Every user-facing registration path refuses a __demo__* name "
                  "and the demo env's name wfc-demo-env, and a module or "
                  "method name that would leave its own directory (../x, a/b) "
                  "before any write; the demo's explicit opt-in registers a "
                  "reserved __demo__ module and a method in it")
def test_reserved_prefix_guard_refuses_and_opt_in_allows(tmp_project):
    # -- a name the one name rule refuses never reaches a write ---------------
    import subprocess

    from sqlmodel import select

    from wfc import layout
    from wfc.persistence import Method, Module, get_session

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_project,
                          check=True, capture_output=True).stdout
    registry = layout.registry_dir(tmp_project)
    registry_before = sorted(registry.rglob("*")) if registry.exists() else []
    source = tmp_project / "methods" / "transform"
    for bad in ("../x", "a/b"):
        with pytest.raises(ValueError, match="module name .* is not valid"):
            register_module(name=bad, contracts=[])
        with pytest.raises(ValueError, match="module name .* is not valid"):
            register_method(method_dir=source, module_name=bad, assume_yes=True)
        with pytest.raises(ValueError, match="method name .* is not valid"):
            register_method(method_dir=source, module_name="mymod",
                            method_name=bad, assume_yes=True)
        with pytest.raises(ValueError, match="method name .* is not valid"):
            layout.method_dir(tmp_project, "mymod", bad)
    with get_session() as session:
        assert session.exec(select(Module)).all() == []
        assert session.exec(select(Method)).all() == []
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_project,
                          check=True, capture_output=True).stdout == head
    after = sorted(registry.rglob("*")) if registry.exists() else []
    assert after == registry_before
    assert not (tmp_project / ".wfc" / "x").exists()
    assert not (layout.module_methods_dir(tmp_project, "a")).exists()

    # -- register_module refuses a reserved name ------------------------------
    with pytest.raises(ValueError, match="reserved"):
        register_module(name="__demo__", contracts=[])

    # -- register_method refuses a reserved METHOD name (guarded before any
    #    filesystem or DB work, so a bare dir path suffices) ------------------
    bad_dir = tmp_project / "methods" / "__demo__thing"
    bad_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match="reserved"):
        register_method(method_dir=bad_dir, module_name="mymod", assume_yes=True)

    # -- register_method refuses targeting a __demo__* module -----------------
    with pytest.raises(ValueError, match="reserved"):
        register_method(
            method_dir=tmp_project / "methods" / "transform",
            module_name="__demo__",
            assume_yes=True,
        )

    # -- register_sample refuses a reserved name (guarded before the DVC gate
    #    and before any file ops) --------------------------------------------
    from wfc.registration import register_sample
    src = tmp_project / "input.csv"
    src.write_text("id,value\n1,2\n")
    with pytest.raises(ValueError, match="reserved"):
        register_sample(name="__demo__s1", source_path=src)

    # -- envs.register refuses a reserved env name (guarded before any docker
    #    interaction — a RuntimeError here would mean docker was touched) -----
    for env_name in ("__demo__env", DEMO_ENV_NAME):
        with pytest.raises(ValueError, match="reserved"):
            register_env(
                name=env_name,
                backend="byo",
                source={"image": "docker://local/x:latest"},
                project_dir=tmp_project,
            )

    # -- the demo env's exact name is reserved for every kind ----------------
    with pytest.raises(ValueError, match="reserved"):
        register_module(name=DEMO_ENV_NAME, contracts=[])

    # -- opt-in path: the demo registers the same reserved names --------------
    module_id = register_module(name="__demo__", contracts=[], allow_reserved=True)
    assert module_id is not None

    # A real fixture method registered INTO the reserved module via opt-in.
    method_id = register_method(
        method_dir=tmp_project / "methods" / "transform",
        module_name="__demo__",
        allow_reserved=True,
        assume_yes=True,
    ).method_id
    assert method_id is not None


@workflow(purpose="wfc demo --remove takes a restored demo sample's read-only "
                  "folder (its view of the cache object and its ready "
                  "sentinel) through Storage's view removal, counts it, and "
                  "leaves the cache entry and its read-only object in place",
          inputs="a demo-named file sample registered through the demo's opt-in "
                 "and restored as a pipeline's restore rule restores it",
          outputs="no sample folder or stamp left; the folder counted in the "
                  "removed file paths; the cache object unchanged and read-only")
def test_demo_remove_takes_a_restored_sample_folder(tmp_project, capsys):
    import os

    from sqlmodel import select

    from wfc import layout
    from wfc.demo.remove import remove_demo
    from wfc.persistence import Sample, get_session
    from wfc.registration import register_sample
    from wfc.storage import restore_sample

    name = "__demo__ctrl_01"
    src = tmp_project.parent / "ctrl_01.csv"
    src.write_bytes(b"id,value\n1,ctrl\n")
    register_sample(name=name, source_path=src, project_root=tmp_project,
                    allow_reserved=True)
    restore_sample(name, project_root=tmp_project)
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == name)).one()
    folder = layout.sample_dir(tmp_project, name)
    view = layout.restored_sample_path(tmp_project, name, row.registered_path)
    obj = layout.dvc_cache_entry(tmp_project, row.content_hash)
    assert view.is_file() and layout.sample_ready_sentinel(tmp_project, name).exists()

    capsys.readouterr()
    assert remove_demo(tmp_project, assume_yes=True) == 0
    out, err = capsys.readouterr()
    assert not folder.exists(), f"the restored sample folder outlived the removal: {err}"
    assert not layout.sample_stamp(tmp_project, name).exists()
    assert "1 file path(s)" in out, out
    assert obj.read_bytes() == b"id,value\n1,ctrl\n"
    assert not os.access(obj, os.W_OK), "the cache object stays read-only"


def test_demo_sample_selection_escapes_like_wildcards(tmp_project, capsys):
    """Tier 1: `_` is a single-char SQL LIKE wildcard — the __demo__ prefix
    query must escape it, or a user sample like 'mydemo__x' (chars 3-6 spell
    'demo', so it matches unescaped LIKE '__demo__%') would be reported as an
    existing demo by scaffold and DELETED by `wfc demo --remove`."""
    from sqlmodel import select

    from wfc.persistence import get_session
    from wfc.demo.remove import remove_demo
    from wfc.demo.scaffold import _existing_demo_entities, _project_env
    from wfc.persistence import Sample

    user_name = "mydemo__x"       # collides with the unescaped LIKE pattern
    demo_name = "__demo__ctrl_01"
    with get_session() as session:
        for name in (user_name, demo_name):
            session.add(Sample(
                name=name, source_path=f"{name}.csv",
                registered_path=f"{name}.csv",
                file_type="csv",
            ))
        session.commit()

    # Scaffold's already-present probe selects ONLY the true demo sample.
    with _project_env(tmp_project):
        found = _existing_demo_entities(tmp_project)
    assert f"sample {demo_name}" in found
    assert not any(user_name in label for label in found)

    # Teardown deletes ONLY the true demo sample; the user's survives.
    rc = remove_demo(tmp_project, assume_yes=True)
    assert rc == 0
    with get_session() as session:
        remaining = {s.name for s in session.exec(select(Sample)).all()}
    assert remaining == {user_name}
