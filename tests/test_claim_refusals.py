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

Tier 2: the real ``pre_run`` over a really-registered method contract, no
Snakemake, no Docker.
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
from wfc.execution.claim import pre_run
from wfc.persistence import get_session, MethodVersion, Run

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
            "params: {}\nexecutor: python\nenv: fixture-env\n"
        )
    result = cli("register-method", method_dir, "--module", module)
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
