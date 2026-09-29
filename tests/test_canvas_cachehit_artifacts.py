"""Canvas provider artifact resolution for cache-hit runs.

Cache-hit audit rows (``cache_source_run_id`` set) own no RunOutput rows —
their outputs live on the source run they reference. The provider's
artifact surfaces (Artifacts tab listing, preview/download path lookup,
export bundle collection) must follow that pointer one hop, mirroring the
shared CLI resolver, so a cached run previews and exports by its own id.
"""

from axiom_annotations import workflow, Step

# seed_sample_row, not create_sample_csv, deliberately: this is a canvas
# reader over the registry, so the Sample row IS the input under test.
from tests.conftest import seed_sample_row
from wfc.canvas.wfc_provider import WfcProvider
from wfc.execution.claim import pre_run
from wfc.persistence import get_session, Method, Module, Run, RunOutput
from wfc.storage import archive_outputs


def _seed_method(module_name: str = "cachehit_mod",
                 method_name: str = "cachehit_method") -> int:
    """Insert a minimal Module + Method into the DB and return method.id."""
    with get_session() as session:
        mod = Module(name=module_name, description="cache-hit artifacts module")
        session.add(mod)
        session.commit()
        session.refresh(mod)
        method = Method(
            name=method_name,
            module_id=mod.id,
            script_path=f"methods/{method_name}/{method_name}.py",
            env="fixture-env",
        )
        session.add(method)
        session.commit()
        session.refresh(method)
        return method.id  # type: ignore[return-value]


@workflow(
    purpose="A cache-hit run's Canvas artifact endpoints resolve the source "
            "run's outputs by the cached run's own id"
)
def test_cachehit_run_artifacts_resolve_via_cache_source(tmp_project):
    """All three provider artifact surfaces follow the cache-source hop.

    The cache-hit row is produced by a real second ``pre_run`` (the
    production cache-hit branch), not a hand-mimicked insert, so the test
    stages exactly the shape ``run-step`` records: cache-source reference
    set, no RunOutput rows of its own.
    """
    口 = Step(
        step_num=1,
        name="Seed module, method, and source files",
        purpose="Insert the Module and Method rows pre_run requires and "
                "create the method source dir for code fingerprinting")
    _seed_method()
    method_dir = tmp_project / "methods" / "cachehit_method"
    method_dir.mkdir(parents=True, exist_ok=True)
    (method_dir / "cachehit_method.py").write_text("def main():\n    pass\n")
    # A registered copy carries its contract: both halves are the method's
    # code identity, so a copy holding no method.yaml cannot be fingerprinted.
    (method_dir / "method.yaml").write_text(
        "env: fixture-env\n"
        "inputs:\n  data:\n    required: false\n"
        "outputs:\n  result:\n    type: .csv\n"
    )
    seed_sample_row("samp_c")

    口 = Step(
        step_num=2,
        name="First pre_run — cache miss, then archive its output",
        purpose="Create the source run with a completed status and an "
                "archived RunOutput so its artifact resolves from the "
                "local cache")
    commit = "b2" * 20
    flag_1, source_id = pre_run(
        method_name="cachehit_method",
        module_name="cachehit_mod",
        sample="samp_c",
        params={"gamma": 2},
        git_commit=commit)
    assert flag_1 == "NEW"

    staging = tmp_project / "staging"
    staging.mkdir()
    out_file = staging / "result.csv"
    out_file.write_text("a,b\n1,2\n")
    with get_session() as session:
        run = session.get(Run, source_id)
        run.status = "completed"  # type: ignore[union-attr]
        session.add(run)
        session.add(RunOutput(
            run_id=source_id, slot="result", output_name="result",
            artifact_path=str(out_file), artifact_type="method_file",
        ))
        session.commit()
    archive_outputs(tmp_project, run_id=source_id)

    口 = Step(
        step_num=3,
        name="Second pre_run — cache hit",
        purpose="Produce the audit row through the production cache-hit "
                "branch; it owns no RunOutput rows of its own")
    flag_2, cached_id = pre_run(
        method_name="cachehit_method",
        module_name="cachehit_mod",
        sample="samp_c",
        params={"gamma": 2},
        git_commit=commit)
    assert flag_2 == "CACHED"
    assert cached_id != source_id

    口 = Step(
        step_num=4,
        name="Resolve artifacts by the cached run's own id",
        purpose="The Artifacts tab listing, the preview/download path "
                "lookup, and the export bundle collection must all follow "
                "the cache-source hop")
    provider = WfcProvider(str(tmp_project))

    listed = provider.list_artifacts(str(cached_id))
    assert [a["name"] for a in listed] == ["result.csv"]

    cache_path = provider.get_artifact_path(str(cached_id), "result.csv")
    assert cache_path is not None
    assert cache_path.read_text() == "a,b\n1,2\n"

    bulk = provider.get_artifacts([str(cached_id)])
    assert [a["artifact_name"] for a in bulk] == ["result.csv"]
    assert bulk[0]["run_id"] == str(cached_id)
