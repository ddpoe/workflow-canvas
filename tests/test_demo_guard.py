"""Reserved ``__demo__`` prefix guard (US-4, safety-chain link 1).

``wfc demo --remove`` deletes by the ``__demo__`` tag, so the tag must be
proof of demo ownership: no user-driven registration path may create a
``__demo__``-prefixed name. The guard lives on the library-level functions
that both the CLI dispatches and the Canvas Registry endpoints wrap, with an
explicit ``allow_reserved=True`` opt-in used only by ``wfc demo`` itself.
"""

import pytest
from axiom_annotations import workflow

from wfc.envs import register as register_env
from wfc.register import register_method, register_module


@workflow(purpose="Every user-facing registration path refuses a __demo__* name; "
                  "the demo's explicit opt-in registers the same names")
def test_reserved_prefix_guard_refuses_and_opt_in_allows(tmp_project):
    # -- register_module refuses a reserved name ------------------------------
    with pytest.raises(ValueError, match="reserved"):
        register_module(name="__demo__", contracts=[])

    # -- register_method refuses a reserved METHOD name (guarded before any
    #    filesystem or DB work, so a bare dir path suffices) ------------------
    bad_dir = tmp_project / "methods" / "__demo__thing"
    bad_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match="reserved"):
        register_method(method_dir=bad_dir, module_name="mymod")

    # -- register_method refuses targeting a __demo__* module -----------------
    with pytest.raises(ValueError, match="reserved"):
        register_method(
            method_dir=tmp_project / "methods" / "transform",
            module_name="__demo__",
        )

    # -- register_sample refuses a reserved name (guarded before the DVC gate
    #    and before any file ops) --------------------------------------------
    from wfc.cli import register_sample
    src = tmp_project / "input.csv"
    src.write_text("id,value\n1,2\n")
    with pytest.raises(ValueError, match="reserved"):
        register_sample(name="__demo__s1", source_path=src)

    # -- envs.register refuses a reserved env name (guarded before any docker
    #    interaction — a RuntimeError here would mean docker was touched) -----
    with pytest.raises(ValueError, match="reserved"):
        register_env(
            name="__demo__env",
            backend="byo",
            source={"image": "docker://local/x:latest"},
            project_dir=tmp_project,
        )

    # -- opt-in path: the demo registers the same reserved names --------------
    module_id = register_module(name="__demo__", contracts=[], allow_reserved=True)
    assert module_id is not None

    # A real fixture method registered INTO the reserved module via opt-in.
    method_id = register_method(
        method_dir=tmp_project / "methods" / "transform",
        module_name="__demo__",
        allow_reserved=True,
    )
    assert method_id is not None


def test_demo_sample_selection_escapes_like_wildcards(tmp_project, capsys):
    """Tier 1: `_` is a single-char SQL LIKE wildcard — the __demo__ prefix
    query must escape it, or a user sample like 'mydemo__x' (chars 3-6 spell
    'demo', so it matches unescaped LIKE '__demo__%') would be reported as an
    existing demo by scaffold and DELETED by `wfc demo --remove`."""
    from sqlmodel import select

    from wfc.database import get_session
    from wfc.demo.remove import remove_demo
    from wfc.demo.scaffold import _existing_demo_entities, _project_env
    from wfc.models import Sample

    user_name = "mydemo__x"       # collides with the unescaped LIKE pattern
    demo_name = "__demo__ctrl_01"
    with get_session() as session:
        for name in (user_name, demo_name):
            session.add(Sample(
                name=name, source_path=f"{name}.csv",
                registered_path=f"data/samples/{name}/{name}.csv",
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
