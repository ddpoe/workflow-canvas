"""The claim phase's refusals: an input that resolves to nothing, and a
declared input slot that nothing feeds.

Both land before any ``Run`` or ``MethodVersion`` row exists, because both
describe a step whose cache key would not be over one of its inputs:

  - an input that resolves to no row contributes no part at all, so every
    step whose inputs all resolve to nothing would register under the digest
    of the empty string and share that key;
  - a required input slot nothing feeds is a file the method reads that the
    key does not account for, so two runs over different data share a key
    and the second is served the first's outputs.

And a third, in the claim's sidecar walk: the run a wired upstream's
``run_id.txt`` names must be that node's completed run for this sample and
this pipeline. A sidecar naming any other run (or none) is a record wfc owns
disagreeing with the pipeline, and is refused before the consumer reads a
byte of it.

Tier 2: the real ``pre_run`` over a really-registered method contract, no
Snakemake, no Docker; the sidecar refusals drive production ``run_step``
through the harness's stub rung.
"""

import os

import pytest
from sqlmodel import select

from axiom_annotations import workflow, Step

# seed_sample_row, not create_sample_csv, deliberately: these tests are about
# the claim's row resolution, so the Sample row IS the input under test — each
# refusal turns on whether a row is found, never on the bytes behind it.
from tests.conftest import seed_sample_row
from tests.fixtures.routes import completed_run
from tests.harness import (
    DEFAULT_MODULE,
    Scenario,
    build_project,
    drive_target,
    exits,
    node,
    selector,
    wire,
)
from tests.harness.scenario import SELECTOR_ID
from wfc import layout
from wfc.execution.claim import pre_run
from wfc.persistence import get_session, InUseRecord, MethodVersion, Run, RunInput

#: pre_run's git check is bypassed by supplying the commit directly.
_COMMIT = "9" * 40


def _register_method(cli, inputs_yaml: str, module="refuse_mod", method="refuse_method"):
    """Register a module and a method whose contract declares ``inputs_yaml``.

    Goes through the real ``register-method`` verb so the ``MethodContract``
    row ``pre_run`` reads is the one registration writes.

    Args:
        cli: The in-process CLI runner fixture.
        inputs_yaml: The body of the method.yaml ``inputs:`` block.
        module: Module name to register under.
        method: Method name to register.
    """
    result = cli("register-module", "--name", module,
                 "--description", "claim refusal fixtures", "--contracts", "[]")
    assert result.returncode == 0, result.stderr
    method_dir = os.path.join("methods", method)
    os.makedirs(method_dir, exist_ok=True)
    with open(os.path.join(method_dir, f"{method}.py"), "w") as fh:
        fh.write("def main(df, params): return df\n")
    with open(os.path.join(method_dir, "method.yaml"), "w") as fh:
        fh.write(
            f"inputs:\n{inputs_yaml}"
            "outputs:\n  result:\n    type: .csv\n    required: true\n"
            # The claims pass ``k``; the claim's parameter check needs it declared.
            "params:\n  k:\n    type: int\nexecutor: python\nenv: fixture-env\n"
        )
    result = cli("register-method", "--yes", method_dir, "--module", module)
    assert result.returncode == 0, result.stderr


def _row_counts() -> tuple[int, int]:
    """(Run count, MethodVersion count) — the rows a refusal must precede."""
    with get_session() as session:
        runs = len(session.exec(select(Run)).all())
        versions = len(session.exec(select(MethodVersion)).all())
    return runs, versions


@workflow(
    purpose="A claim over an input that resolves to no row is refused naming "
            "the node and the input, before any Run or MethodVersion row "
            "exists; a claim over registered inputs still registers"
)
def test_claim_refuses_an_input_that_resolves_to_no_row(cli, tmp_project, monkeypatch):
    """A skipped input contributes no part, so it must be a refusal."""
    口 = Step(step_num=1, name="Register a one-input method",
             purpose="The claim resolves its sample and its parent entries "
                     "against real rows; the contract declares only `data`")
    _register_method(cli, "  data:\n    type: .csv\n    required: true\n")

    口 = Step(step_num=2, name="Claim over an unregistered sample",
             purpose="No Sample row means no identity at all, so the key "
                     "would be blind to whatever the step reads")
    before = _row_counts()
    with pytest.raises(ValueError) as excinfo:
        pre_run(method_name="refuse_method", module_name="refuse_mod",
                sample="never_registered", params={"k": 1},
                git_commit=_COMMIT, nid="consumer_node")
    message = str(excinfo.value)
    assert "never_registered" in message
    assert "data" in message
    assert _row_counts() == before, (
        "the refusal must land before a Run or MethodVersion row is written"
    )

    口 = Step(step_num=3, name="Claim whose entry names no output of a "
                              "multi-output upstream",
             purpose="Two recorded outputs and no named source slot is an "
                     "ambiguous wiring; an unqualified part would let two "
                     "different wirings share a key")
    seed_sample_row("registered_sample")
    ambiguous = completed_run(
        tmp_project, monkeypatch=monkeypatch, method="two_outputs",
        module="refuse_mod", sample="up",
        outputs={"left": ".csv", "right": ".csv"}).run_id
    before = _row_counts()
    with pytest.raises(ValueError) as excinfo:
        pre_run(method_name="refuse_method", module_name="refuse_mod",
                sample="registered_sample", params={"k": 1},
                parent_run_ids=[f"data:{ambiguous}"],
                git_commit=_COMMIT, nid="consumer_node")
    message = str(excinfo.value)
    assert str(ambiguous) in message
    assert "left" in message and "right" in message
    assert _row_counts() == before

    口 = Step(step_num=4, name="A claim over registered inputs registers",
             purpose="The refusals are about unresolvable inputs, not about "
                     "the claim path itself")
    flag, run_id = pre_run(
        method_name="refuse_method", module_name="refuse_mod",
        sample="registered_sample", params={"k": 1},
        git_commit=_COMMIT, nid="consumer_node")
    assert flag == "NEW"
    assert isinstance(run_id, int)


@workflow(
    purpose="A claim that feeds only some of the input slots its method "
            "declares required is refused naming the node and the unfed "
            "slot; feeding every declared slot registers"
)
def test_claim_refuses_a_required_input_slot_nothing_feeds(cli, tmp_project, monkeypatch):
    """A slot the key is not over lets two runs over different data collide."""
    口 = Step(step_num=1, name="Register a method declaring `data` and `ref`",
             purpose="Both are required, so both have to be fed by something "
                     "the cache key is over")
    _register_method(
        cli,
        "  data:\n    type: .csv\n    required: true\n"
        "  ref:\n    type: .csv\n    required: true\n",
    )
    seed_sample_row("s_unfed")

    口 = Step(step_num=2, name="Claim feeding only `data`",
             purpose="The sample feeds `data`; nothing feeds `ref`, and the "
                     "document wires only `data`, so the unfed slot is the "
                     "author's to wire -- not a delivery wfc failed")
    before = _row_counts()
    with pytest.raises(ValueError) as excinfo:
        pre_run(method_name="refuse_method", module_name="refuse_mod",
                sample="s_unfed", params={"k": 1},
                git_commit=_COMMIT, nid="unfed_node",
                wired_slots={"data": "sel"})
    message = str(excinfo.value)
    assert "unfed_node" in message
    assert "'ref'" in message
    assert "which nothing feeds" in message
    assert "wfc defect" not in message
    assert _row_counts() == before, (
        "the refusal must land before a Run or MethodVersion row is written"
    )
    # pre_run held the code copy under its own token before composing the
    # key; the refusal releases it.
    with get_session() as session:
        assert session.exec(select(InUseRecord)).all() == []

    口 = Step(step_num=3, name="Claim feeding both slots",
             purpose="With `ref` wired to a completed upstream and the "
                     "document's selector slot naming `data`, every declared "
                     "slot is fed and the claim registers")
    upstream = completed_run(
        tmp_project, monkeypatch=monkeypatch, method="one_output",
        module="refuse_mod", sample="up", outputs={"result": ".csv"}).run_id
    flag, run_id = pre_run(
        method_name="refuse_method", module_name="refuse_mod",
        sample="s_unfed", params={"k": 1},
        parent_run_ids=[f"ref:result:{upstream}"],
        selector_slot="data",
        git_commit=_COMMIT, nid="unfed_node")
    assert flag == "NEW"
    assert isinstance(run_id, int)


@workflow(
    purpose="A slot declared `required: false` is the codebase's 'declared "
            "but deliberately unfed' convention and is not refused"
)
def test_claim_allows_an_unfed_optional_slot(cli, tmp_project):
    """A trigger-only method declares a slot it never reads.

    A method must declare at least one input slot, so a method whose inputs
    are all sequencing triggers declares one and marks it optional. Refusing
    it would make that shape unrunnable.
    """
    口 = Step(step_num=1, name="Register a method with one optional slot",
             purpose="`required: false` says the author knows nothing feeds it")
    _register_method(
        cli,
        "  data:\n    type: .csv\n    required: true\n"
        "  trigger:\n    type: .csv\n    required: false\n",
    )
    seed_sample_row("s_optional")

    口 = Step(step_num=2, name="Claim feeding only `data`",
             purpose="The optional slot is unfed and the claim still registers")
    flag, run_id = pre_run(
        method_name="refuse_method", module_name="refuse_mod",
        sample="s_optional", params={"k": 1},
        git_commit=_COMMIT, nid="optional_node")
    assert flag == "NEW"
    assert isinstance(run_id, int)


# =============================================================================
# The run a sidecar names
# =============================================================================

HEAD, TAIL = ("head", "s1", "default"), ("tail", "s1", "default")


def _chain(pipeline_id: str = "harness-pipeline", head_fails: bool = False) -> Scenario:
    """A selector feeding head -> tail, beside a second selector-fed node."""
    head_kwargs = {"behavior_by_sample": {"s1": exits(1)}} if head_fails else {}
    return Scenario(
        nodes=[selector(),
               node("head", inputs=[wire(SELECTOR_ID)], **head_kwargs),
               node("tail", inputs=[wire("head")]),
               node("other", inputs=[wire(SELECTOR_ID)])],
        samples=["s1", "s2"], pipeline_id=pipeline_id, name=pipeline_id)


def _another_samples_run(project, root, monkeypatch) -> str:
    """head's completed run for s2: only the sample differs (B4a)."""
    target = ("head", "s2", "default")
    return str(drive_target(project, target, monkeypatch=monkeypatch).runs[target].run_id)


def _another_nodes_run(project, root, monkeypatch) -> str:
    """other's completed run for s1: only the node differs (B4b)."""
    target = ("other", "s1", "default")
    return str(drive_target(project, target, monkeypatch=monkeypatch).runs[target].run_id)


def _another_pipelines_run(project, root, monkeypatch) -> str:
    """head's completed run for s1 under another pipeline over the same root."""
    elsewhere = build_project(_chain("elsewhere"), root=root, monkeypatch=monkeypatch)
    run_id = drive_target(elsewhere, HEAD, monkeypatch=monkeypatch).runs[HEAD].run_id
    build_project(_chain(), root=root, monkeypatch=monkeypatch)
    return str(run_id)


def _a_failed_run(project, root, monkeypatch) -> str:
    """head's own run for s1 in this pipeline, which failed: only the status differs."""
    obs = drive_target(project, HEAD, monkeypatch=monkeypatch)
    assert obs.run_row(HEAD)["status"] == "failed"
    return str(obs.runs[HEAD].run_id)


def _a_run_recording_no_node(project, root, monkeypatch) -> str:
    """A completed run of head's method for s1 in this pipeline, written by
    the legacy ``register_run`` verb, which records no node."""
    from wfc.execution.claim import register_run
    from wfc.execution.record import complete_run
    run_id = register_run("head", DEFAULT_MODULE, "s1",
                          pipeline_id=project.pipeline_id)
    complete_run(run_id)
    return str(run_id)


SIDECAR_CASES = {
    "another-sample": (_another_samples_run, "for sample 's2'"),
    "another-node": (_another_nodes_run, "of node 'other'"),
    "another-pipeline": (_another_pipelines_run, "in pipeline 'elsewhere'"),
    "a-failed-run": (_a_failed_run, "a failed run"),
    "no-node": (_a_run_recording_no_node, "records no node"),
    "no-such-run": (lambda *_: "99999", "which does not exist"),
    "not-a-run-id": (lambda *_: "not-a-run", "which is not a run id"),
}


@pytest.mark.parametrize("case", list(SIDECAR_CASES))
@workflow(
    purpose="A run-id sidecar naming anything but the wired node's completed "
            "run for this sample and this pipeline -- another sample's, "
            "another node's or another pipeline's run, a failed run, a run "
            "that records no node, no run, or no run id -- is refused at the "
            "claim through the outcome sidecar, saying a record wfc owns "
            "disagrees with the pipeline and naming the sidecar and the run"
)
def test_a_sidecar_naming_any_other_run_is_refused(case, tmp_path, monkeypatch, capsys):
    """Each case differs from the genuine run in one field alone."""
    口 = Step(step_num=1, name="Write head's sidecar naming the case's run",
             purpose="head for s1 never completes in this pipeline, so the "
                     "only thing naming its run is the rewritten sidecar")
    root = tmp_path / "proj"
    project = build_project(_chain(head_fails=case == "a-failed-run"), root=root,
                            monkeypatch=monkeypatch)
    make, expected = SIDECAR_CASES[case]
    content = make(project, root, monkeypatch)
    sidecar = layout.run_id_sidecar_path(root, project.pipeline_id, *HEAD)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(content)
    capsys.readouterr()

    口 = Step(step_num=2, name="Claim tail",
             purpose="The sidecar walk reads the run the sidecar names")
    obs = drive_target(project, TAIL, monkeypatch=monkeypatch)

    口 = Step(step_num=3, name="Refused at the claim, through the outcome sidecar",
             purpose="No run row and no lineage row for tail; the message "
                     "names the sidecar, the run and what that run is")
    err = capsys.readouterr().err
    assert obs.exit_code(TAIL) == 1
    assert obs.phases_ran(TAIL) == ["claim"]
    assert obs.rows_for_node("tail") == []
    assert obs.outcomes[TAIL]["phase"] == "claim"
    message = obs.outcomes[TAIL]["error"]
    assert message in err
    assert "a record wfc owns disagrees with the pipeline" in message
    assert "harness-pipeline/head/s1/default/run_id.txt" in message.replace("\\", "/")
    assert content in message
    assert expected in message
    if content.isdigit():
        with get_session() as session:
            assert session.exec(select(RunInput).where(
                RunInput.source_run_id == int(content))).all() == []


@workflow(
    purpose="A chain claimed twice: the second launch's head is a cache hit, "
            "whose audit row carries this claim's node, sample and pipeline, "
            "so tail's claim accepts the sidecar naming it and records it as "
            "the parent"
)
def test_a_cache_hit_audit_row_passes_the_sidecar_check(tmp_path, monkeypatch):
    口 = Step(step_num=1, name="Run head then tail, then head again",
             purpose="The second head is served from the first: an audit row")
    project = build_project(_chain(), root=tmp_path / "proj", monkeypatch=monkeypatch)
    first = drive_target(project, HEAD, monkeypatch=monkeypatch)
    drive_target(project, TAIL, monkeypatch=monkeypatch)
    again = drive_target(project, HEAD, monkeypatch=monkeypatch)
    audit = again.run_row(HEAD)
    assert audit["cache_source_run_id"] == first.runs[HEAD].run_id

    口 = Step(step_num=2, name="Claim tail against the audit row",
             purpose="The sidecar names the audit row, which passes")
    obs = drive_target(project, TAIL, monkeypatch=monkeypatch)
    assert obs.exit_code(TAIL) == 0
    tail_run = obs.runs[TAIL].run_id
    assert [r["source_run_id"] for r in obs.run_input_rows
            if r["run_id"] == tail_run] == [audit["id"]]
