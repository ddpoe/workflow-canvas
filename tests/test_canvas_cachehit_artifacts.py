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
from tests.fixtures.routes import register_test_method, sample_source_dir
from wfc.canvas.wfc_provider import WfcProvider
from wfc.execution.claim import pre_run
from wfc.persistence import get_session, Run, RunOutput
from wfc.storage import archive_outputs


def _register_method(project, module_name: str = "cachehit_mod",
                     method_name: str = "cachehit_method") -> None:
    """Register a minimal method (script + contract) through production registration.

    The source is written beside the project; registration copies it into
    the module-qualified snapshot, commits it and mints its version.
    """
    src = sample_source_dir(project) / "_methods" / module_name / method_name
    src.mkdir(parents=True, exist_ok=True)
    (src / f"{method_name}.py").write_text("def main():\n    pass\n")
    (src / "method.yaml").write_text(
        "env: fixture-env\n"
        "inputs:\n  data:\n    required: false\n"
        "outputs:\n  result:\n    type: .csv\n"
        # The claims pass ``gamma``; a claim's parameter check refuses a
        # parameter its method does not declare.
        "params:\n  gamma:\n    type: int\n"
    )
    register_test_method(project_dir=project, module_name=module_name,
                         method_dir=src, method_name=method_name)


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
        name="Register the method and seed the sample",
        purpose="Register the method pre_run claims through production "
                "registration (snapshot, commit, version) and seed the sample")
    _register_method(tmp_project)
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

    # Where collect writes a run's outputs: its run archive.
    from wfc.layout import run_archive_dir
    staging = run_archive_dir(tmp_project, source_id)
    staging.mkdir(parents=True, exist_ok=True)
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
