"""Cache-key sensitivity matrix.

The provenance promise is that a run's cache key reacts to *everything that
matters* (code, params, inputs, env) and to *nothing that doesn't* (the order
in which param keys or upstream inputs happen to be enumerated). This single
parametrized test drives the real fingerprint functions in ``wfc/identity/``
end-to-end (the inputs axis through the row fetch beside ``pre_run``) and
proves both halves of that promise.

Each parametrized case is a behavior spec, not a snapshot of current output: a
failing assertion means a fingerprint function stopped reacting (or started
over-reacting) to one of its load-bearing inputs.

Axes proven LOAD-BEARING (changing the axis busts the cache key):
  - code    -- edit a .py in the registered method source dir
  - params  -- change a param *value*
  - inputs  -- change an upstream ``Run.cache_key`` (NOT ``RunOutput.content_hash``;
               the input fingerprint chains on cache_key, so deferred archiving /
               NULL content_hash never perturbs it)
  - env     -- change the env content blob hashed by ``store_env_content``
  - collapsed fan-in bundle -- change WHICH samples a ``sample="__all__"`` root
               bundles.  A collapsed root has no per-sample identity to fall back
               on: ``Run.sample`` is ``__all__`` on both sides of any comparison,
               so if the bundle does not reach the key nothing else separates two
               different bundles.

Axes proven IRRELEVANT (reordering does NOT bust the cache key):
  - param-key order         -- json.dumps(params, sort_keys=True)
  - upstream-input order    -- sorted() inside the input-fingerprint digest
                               (load-bearing); witnessed against a literal
                               digest in tests/test_identity.py
  - bundled-sample order    -- same sorted(); reordering a fan-in selector changes
                               nothing about the computation, so it must not
                               invalidate the cache

The first four axes drive the fingerprint functions in ``wfc/identity/``
directly.  The collapsed-bundle cases drive the real claim phase instead,
through the run route (``completed_run`` over a declared scenario), because
the claim *assembles* the sample-id list that ``build_input_fingerprint``
composes — a fingerprint-level test cannot see a bundle that never reaches
the fingerprint.

No Docker. Uses the in-process ``tmp_project`` fixture (git repo + DB).
"""

from pathlib import Path

import pytest

from axiom_annotations import workflow, Step

# seed_sample_row, not create_sample_csv, deliberately: the contract cases
# drive compose_cache_key over the rows, where the sample row's content_hash
# IS the independent variable. Nothing restores or runs, so the row needs no
# cached bytes behind it.
from tests.conftest import seed_sample_row
from tests.fixtures.routes import completed_run
from tests.harness import Scenario, build_project, node, reference, selector, wire
from tests.harness.scenario import SELECTOR_ID
from wfc.persistence import get_session, Method, Module, Run, RunOutput
# ``layout.run_archive_dir`` is imported rather than re-derived: pre_run treats a
# missing archive directory as a cache miss, so the one test that completes a
# claim by hand has to build the same path pre_run checks. Re-spelling the
# ``{id:08d}`` layout here would let the two drift apart silently.
from wfc.persistence import project_root as get_project_root
from wfc.layout import run_archive_dir
from wfc.execution.claim import pre_run, input_fingerprint_from_rows
from wfc.execution.parents import ParentEntry
from wfc.contracts import parse_method_yaml, render_contract_projection
from wfc.identity import build_cache_key, build_code_fingerprint
from wfc.storage import store_env_content


# =============================================================================
# Helpers
# =============================================================================

def _setup_dvc_config(project_root: Path) -> None:
    """Write a [dvc] config and init the local DVC cache (for store_env_content)."""
    remote_dir = project_root.parent / f"{project_root.name}-dvc-remote"
    remote_dir.mkdir(parents=True, exist_ok=True)
    config_path = project_root / ".wfc" / "wf-canvas.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = (project_root / ".wfc" / "wfc.db").as_posix()
    config_path.write_text(
        f'[database]\nurl = "sqlite:///{db_path}"\n\n'
        f'[project]\nname = "test"\n\n'
        f'[dvc]\nremote_type = "local"\n'
        f'remote_path = "{remote_dir.as_posix()}"\nauto_init = true\n'
    )
    from wfc.storage import init_dvc
    init_dvc(project_root, {"url": str(remote_dir)})


def _make_method_source(project_dir: Path, name: str = "m_sens") -> Path:
    """Create a minimal registered method dir: one .py file and a contract.

    Both halves are the method's code identity, so a registered copy with no
    ``method.yaml`` is refused at fingerprint time. The contract is held fixed
    here -- this module drives the other axes.
    """
    method_dir = Path(project_dir) / "methods" / name
    method_dir.mkdir(parents=True, exist_ok=True)
    (method_dir / f"{name}.py").write_text("def main():\n    return 1\n")
    (method_dir / "method.yaml").write_text(
        "env: fixture-env\n"
        "inputs:\n  data:\n    required: false\n"
        "outputs:\n  result:\n    type: .csv\n"
    )
    return method_dir


def _seed_upstream_run(cache_key: str) -> int:
    """Insert a Module/Method/Run chain with a given Run.cache_key; return run id.

    The input fingerprint chains on ``Run.cache_key``, so a unique cache_key per
    upstream run is what makes the input axis observable. The run records one
    output slot, which is what lets a parent entry naming no source slot
    (``ref:<id>``) resolve.
    """
    with get_session() as session:
        module = Module(name=f"mod_{cache_key[:6]}", description="x")
        session.add(module)
        session.commit()
        session.refresh(module)
        method = Method(
            name=f"meth_{cache_key[:6]}",
            module_id=module.id,
            script_path="methods/x/x.py",
            env="container:demo",
        )
        session.add(method)
        session.commit()
        session.refresh(method)
        run = Run(method_id=method.id, status="completed", cache_key=cache_key)
        session.add(run)
        session.commit()
        session.refresh(run)
        session.add(RunOutput(
            run_id=run.id,
            slot="result",
            output_name="result.csv",
            artifact_path=f".runs/{run.id:08d}/result.csv",
            artifact_type="method_file",
        ))
        session.commit()
        return run.id


# A fixed baseline for the three components we hold constant while varying one axis.
_CODE_FP = "a" * 64
_INPUT_FP = "b" * 64
_ENV_FP = "c" * 32
_METHOD_ID = "sens_mod.m_sens"
_PARAMS = {"threshold": 0.5, "normalize": True}


# =============================================================================
# cache-key sensitivity matrix
# =============================================================================

@pytest.mark.parametrize(
    "axis",
    ["code", "params", "inputs", "env"],
)
def test_cache_key_reacts_to_load_bearing_axis(tmp_project, axis):
    """Changing any of code/params/inputs/env busts the cache key.

    Each axis is exercised through its *real* fingerprint function so the test
    fails if any of build_code_fingerprint / build_input_fingerprint /
    store_env_content / build_cache_key stops folding its component into the key.
    """
    if axis == "code":
        method_dir = _make_method_source(tmp_project)
        fp_before = build_code_fingerprint(
            method_dir, render_contract_projection(parse_method_yaml(method_dir)))
        # Edit a .py in the method source dir -> code identity must change.
        (method_dir / "m_sens.py").write_text("def main():\n    return 2\n")
        fp_after = build_code_fingerprint(
            method_dir, render_contract_projection(parse_method_yaml(method_dir)))
        assert fp_before != fp_after, "editing method source did not change code fingerprint"
        key_before = build_cache_key(fp_before, _PARAMS, _INPUT_FP, _ENV_FP, _METHOD_ID)
        key_after = build_cache_key(fp_after, _PARAMS, _INPUT_FP, _ENV_FP, _METHOD_ID)
        assert key_before != key_after

    elif axis == "params":
        key_before = build_cache_key(_CODE_FP, {"threshold": 0.5}, _INPUT_FP, _ENV_FP, _METHOD_ID)
        key_after = build_cache_key(_CODE_FP, {"threshold": 0.9}, _INPUT_FP, _ENV_FP, _METHOD_ID)
        assert key_before != key_after, "changing a param value did not change the key"

    elif axis == "inputs":
        # Vary an upstream Run.cache_key (NOT content_hash): the input fingerprint
        # chains on cache_key, so deferred archiving / NULL content_hash is
        # irrelevant here. Changing the upstream cache_key MUST bust the key.
        # Stays on typed rows: the literal cache_key is this axis's independent
        # variable, and a route-produced run's key is composed, not chosen.
        up_a = _seed_upstream_run("0" * 64)
        up_b = _seed_upstream_run("1" * 64)
        fp_before = input_fingerprint_from_rows(
            [ParentEntry("data", "result", up_a)], step="m_sens")
        fp_after = input_fingerprint_from_rows(
            [ParentEntry("data", "result", up_b)], step="m_sens")
        assert fp_before != fp_after, "distinct upstream cache_keys produced the same input fingerprint"
        key_before = build_cache_key(_CODE_FP, _PARAMS, fp_before, _ENV_FP, _METHOD_ID)
        key_after = build_cache_key(_CODE_FP, _PARAMS, fp_after, _ENV_FP, _METHOD_ID)
        assert key_before != key_after

    elif axis == "env":
        _setup_dvc_config(tmp_project)
        fp_before = store_env_content("packages=numpy==1.0\n", tmp_project)
        fp_after = store_env_content("packages=numpy==2.0\n", tmp_project)
        assert fp_before != fp_after, "distinct env content produced the same env fingerprint"
        key_before = build_cache_key(_CODE_FP, _PARAMS, _INPUT_FP, fp_before, _METHOD_ID)
        key_after = build_cache_key(_CODE_FP, _PARAMS, _INPUT_FP, fp_after, _METHOD_ID)
        assert key_before != key_after


@pytest.mark.parametrize(
    "axis",
    ["param_key_order"],
)
def test_cache_key_ignores_irrelevant_ordering(axis):
    """Reordering param keys does NOT change the cache key.

    Proves the json.dumps(sort_keys=True) guarantee in build_cache_key.
    Removing it would silently break cache stability across enumeration
    orderings — this test is the guard. The upstream-input-order half of the
    promise is witnessed against a literal digest, over every permutation, in
    ``tests/test_identity.py``.
    """
    if axis == "param_key_order":
        # Same params, keys supplied in a different insertion order.
        key_1 = build_cache_key(_CODE_FP, {"a": 1, "b": 2}, _INPUT_FP, _ENV_FP, _METHOD_ID)
        key_2 = build_cache_key(_CODE_FP, {"b": 2, "a": 1}, _INPUT_FP, _ENV_FP, _METHOD_ID)
        assert key_1 == key_2, "param-key order changed the cache key (params not serialized with sort_keys)"


# =============================================================================
# collapsed fan-in: the bundled sample set is part of the root's identity
# =============================================================================
#
# These drive the real claim phase through the run route. A collapsed fan-in
# root bundles N samples into one run carrying the ``__all__`` sentinel, so
# the per-sample identity every other root relies on is gone: ``Run.sample``
# is ``__all__`` on both sides of any comparison between two collapsed roots,
# and only the cache key can tell them apart.
#
# A NEW claim is a run row with no ``cache_source_run_id``; a CACHED claim is
# an audit row whose ``cache_source_run_id`` names the run it was served from.


def _bundle_scenario(samples) -> Scenario:
    """Declare a fan-in selector over ``samples`` collapsing into one root.

    The selector lists the samples in the order given, which is what the
    order test varies.
    """
    return Scenario(
        nodes=[selector(fan_mode="in", samples=list(samples)),
               node("m_bundle", module="bundle_mod",
                    inputs=[wire(SELECTOR_ID, bundle=True)],
                    params={"threshold": 0.5})],
        samples=list(samples), pipeline_id="bundle", name="bundle",
    )


def _bundle_run(root, monkeypatch, samples):
    """Drive the collapsed root over ``samples`` to completion through the route."""
    return completed_run(root, monkeypatch=monkeypatch,
                         scenario=_bundle_scenario(samples), target="m_bundle")


@workflow(
    purpose="Adding a sample to a fan-in selector makes the collapsed root "
            "recompute instead of returning the previous bundle's result"
)
def test_collapsed_root_key_reacts_to_the_bundled_sample_set(tmp_project, monkeypatch):
    """Two bundles differing only in membership must not share a cache key.

    Equal keys would mean a researcher who adds a sample to a fan-in selector
    gets handed the old bundle's output -- a result computed from data that no
    longer describes the input.
    """
    口 = Step(step_num=1, name="Run the root bundling {s1, s2} to completion",
             purpose="The route registers the samples (distinct content, so "
                     "membership is visible to the fingerprint) and the "
                     "bundling method, and leaves a completed run with an "
                     "archive -- the state a later cache hit would resolve "
                     "against")
    run_two = _bundle_run(tmp_project, monkeypatch, ["s1", "s2"])
    assert run_two.run_row["cache_source_run_id"] is None

    口 = Step(step_num=2, name="Widen the selector to {s1, s2, s3} and re-run",
             purpose="Identical code, params and env; the sample set is the "
                     "only thing that moved")
    run_three = _bundle_run(tmp_project, monkeypatch, ["s1", "s2", "s3"])

    口 = Step(step_num=3, name="The wider bundle is a miss with its own key",
             purpose="A CACHED claim here is the defect itself: the root would "
                     "return a result computed without s3")
    assert run_three.run_row["cache_source_run_id"] is None
    assert run_three.cache_key != run_two.cache_key


@workflow(
    purpose="An unchanged collapsed root still hits its own prior run -- the "
            "bundle entering the key must not make fan-in uncacheable"
)
def test_collapsed_root_rerun_unchanged_hits_its_own_prior_run(tmp_project, monkeypatch):
    """Re-running the same bundle returns CACHED.

    This is the control for the reacts-to-membership test above: without it, a
    change that made every collapsed root permanently miss would satisfy that
    test vacuously while destroying fan-in caching.
    """
    口 = Step(step_num=1, name="Run the bundle to completion",
             purpose="Same starting state as the membership test; produces the "
                     "run a second identical call should resolve to")
    run_first = _bundle_run(tmp_project, monkeypatch, ["s1", "s2"])
    assert run_first.run_row["cache_source_run_id"] is None

    口 = Step(step_num=2, name="Re-run the identical bundle",
             purpose="Nothing about the computation changed, so the stored "
                     "result is still the right answer")
    run_second = _bundle_run(tmp_project, monkeypatch, ["s1", "s2"])

    口 = Step(step_num=3, name="The re-run is a hit against the first run",
             purpose="CACHED returns an audit row pointing back at the source")
    assert run_second.run_row["cache_source_run_id"] == run_first.run_id


@workflow(purpose="Reordering a fan-in selector's samples does not invalidate "
                  "the collapsed root's cache")
def test_collapsed_root_key_ignores_bundled_sample_order(tmp_project, monkeypatch):
    """The bundle enters the key as a set, not as the selector's listing order.

    Asserted through the cache lookup rather than key equality alone, so it
    also proves the whole hit path treats the two orderings as one run.
    """
    run_first = _bundle_run(tmp_project, monkeypatch, ["s1", "s2"])
    assert run_first.run_row["cache_source_run_id"] is None

    run_reordered = _bundle_run(tmp_project, monkeypatch, ["s2", "s1"])
    assert run_reordered.run_row["cache_source_run_id"] == run_first.run_id, (
        "reordering the selector's samples invalidated the cache "
        "(build_input_fingerprint no longer sorts its parts)"
    )


@workflow(
    purpose="A collapsed root that also has a run_reference still keys on its "
            "bundle -- the reference must not displace the sample set"
)
def test_collapsed_root_with_a_run_reference_still_keys_on_the_bundle(tmp_project, monkeypatch):
    """The bundle contributes even when an upstream parent is present.

    A ``run_reference`` wired into a collapsed root reaches registration as an
    upstream parent, so this root is collapsed *and* has a non-empty
    ``upstream_run_ids``. Composing the bundle inside the no-upstream branch
    would drop it for exactly this shape, leaving two different bundles sharing
    one key while the simpler cases above still pass.
    """
    口 = Step(step_num=1, name="Complete the referenced run; register the samples "
                               "and the bundling method",
             purpose="The reference names a completed run that contributes its "
                     "cache_key as an upstream part; the bundle's samples and "
                     "method are registered by building the declared project")
    referenced = completed_run(tmp_project, monkeypatch=monkeypatch,
                               method="m_ref", module="bundle_mod",
                               sample="s_ref", outputs={"result": ".csv"})
    build_project(_bundle_scenario(["s1", "s2", "s3"]), root=tmp_project,
                  monkeypatch=monkeypatch)

    # Stays as a direct claim: production's document loader refuses this
    # shape (``check_legality``: a fan-in selector must be its consumer's
    # sole upstream), so no route can declare a collapsed root that also
    # has a run_reference upstream, and the claim is called here as the
    # generator would call it if the loader admitted the document. The
    # eligibility flip below is the same shortcut. What this proves: the
    # claim's composition rule for that shape. What it does not: that any
    # pipeline reaches it.
    def _collapsed_claim(samples):
        return pre_run(
            method_name="m_bundle", module_name="bundle_mod", sample="__all__",
            params={"threshold": 0.5}, parent_run_ids=[f"ref:{referenced.run_id}"],
            git_commit="b7" * 20, collapsed_samples=samples,
        )

    def _complete(run_id: int) -> None:
        # status completed + archive present: what pre_run's lookup needs
        # before it can hand a later identical claim this run.
        with get_session() as session:
            run = session.get(Run, run_id)
            run.status = "completed"
            session.add(run)
            session.commit()
        run_archive_dir(get_project_root(), run_id).mkdir(parents=True, exist_ok=True)

    def _key_of(run_id: int) -> str:
        with get_session() as session:
            return session.get(Run, run_id).cache_key

    口 = Step(step_num=2, name="Claim the referencing root over {s1, s2}",
             purpose="Both channels contribute: the reference as an upstream "
                     "part, the bundle as sample parts")
    flag_two, run_two = _collapsed_claim(["s1", "s2"])
    assert flag_two == "NEW"
    _complete(run_two)

    口 = Step(step_num=3, name="Widen the bundle, holding the reference fixed",
             purpose="The reference is unchanged, so the sample set is the only "
                     "moving input")
    flag_three, run_three = _collapsed_claim(["s1", "s2", "s3"])

    口 = Step(step_num=4, name="The bundle still moved the key",
             purpose="Equal keys would mean the reference's presence silently "
                     "switched the bundle off as a cache input")
    assert flag_three == "NEW"
    assert _key_of(run_three) != _key_of(run_two)


@workflow(
    purpose="A root fed by an input selector and ALSO wired to a "
            "run_reference still keys on its sample: moving the sample's "
            "content moves the key, while a node rooted only at the "
            "reference is unaffected"
)
def test_selector_slot_root_with_a_run_reference_keys_on_its_sample(tmp_project, monkeypatch):
    """The sample contributes even when an upstream parent is present.

    The sibling of the collapsed case above, and the one the collapsed test
    does not reach: a selector feeding ONE sample into a named slot, on a
    node that also has a ``run_reference`` parent. Registration merges the
    reference channel into the parent entries, so ``parent_entries`` is
    non-empty; re-nesting the selector arm under ``if not parent_entries``
    would drop the sample from the key for exactly this shape and leave the
    collapsed half green. Two runs over different sample content would then
    share one key, and the second would be handed the first's output.
    """
    口 = Step(step_num=1, name="Complete the referenced run and declare both shapes",
             purpose="The reference contributes an upstream part; the sample "
                     "is what the selector feeds into `data` on one node, and "
                     "nothing feeds it on the other")
    referenced = completed_run(tmp_project, monkeypatch=monkeypatch,
                               method="m_ref", module="bundle_mod",
                               sample="s_ref", outputs={"result": ".csv"})

    def _both_shapes(**scenario_kwargs) -> Scenario:
        # Two method names: a method has one contract, and the selector-fed
        # node requires a slot the reference-only node has no source for.
        return Scenario(
            nodes=[
                selector(),
                reference("ref", run_id=str(referenced.run_id)),
                node("m_sel", module="bundle_mod", label="sel_node",
                     inputs=[wire(SELECTOR_ID),
                             wire("ref", source_slot=None, target_slot="ref")],
                     params={"threshold": 0.5}),
                # No selector edge: nothing feeds the sample into a slot, so
                # it is not an input and must not enter the key.
                node("m_ref_only", module="bundle_mod", label="ref_node",
                     inputs=[wire("ref", source_slot=None)],
                     params={"threshold": 0.5}),
            ],
            samples=["s_sel"], pipeline_id="selector", name="selector",
            **scenario_kwargs,
        )

    口 = Step(step_num=2, name="Run both shapes to completion",
             purpose="Leaves two completed runs with archives -- the state a "
                     "later cache hit resolves against")
    run_sel = completed_run(tmp_project, monkeypatch=monkeypatch,
                            scenario=_both_shapes(), target="m_sel")
    assert run_sel.run_row["cache_source_run_id"] is None
    run_ref = completed_run(tmp_project, monkeypatch=monkeypatch,
                            scenario=_both_shapes(), target="m_ref_only")
    assert run_ref.run_row["cache_source_run_id"] is None

    口 = Step(step_num=3, name="Move the sample's content",
             purpose="The reference, the params, the code and the env are all "
                     "held fixed, so the sample is the only moving input; the "
                     "rebuild re-registers the sample over its new bytes, "
                     "which is what a re-registration records")
    moved = _both_shapes(sample_content={"s_sel": "id,value\n1,moved\n"})

    口 = Step(step_num=4, name="The selector-fed root recomputes",
             purpose="An equal key would mean the reference's presence "
                     "silently switched the sample off as a cache input, and "
                     "the researcher would get the previous content's result")
    run_moved = completed_run(tmp_project, monkeypatch=monkeypatch,
                              scenario=moved, target="m_sel")
    assert run_moved.run_row["cache_source_run_id"] is None
    assert run_moved.cache_key != run_sel.cache_key

    口 = Step(step_num=5, name="The reference-only root does not",
             purpose="The negative control: with no selector slot the sample "
                     "is not an input, so its content must not move the key. "
                     "Without this, a key that moved on everything would pass "
                     "step 4 just as well")
    run_unmoved = completed_run(tmp_project, monkeypatch=monkeypatch,
                                scenario=moved, target="m_ref_only")
    assert run_unmoved.run_row["cache_source_run_id"] == run_ref.run_id
    assert run_unmoved.cache_key == run_ref.cache_key


# =============================================================================
# The declared contract as a cache-key axis
#
# An output slot's name and `type` decide the filename a run writes
# (``contracts.slots.output_slot_filename``), so a result cached under the
# previous contract is an artifact named for a promise the method no longer
# makes. The contract therefore enters the CODE fingerprint -- not
# ``build_cache_key`` directly -- so ``MethodVersion.code_fingerprint`` sees
# the change too and two contract-different registrations are two versions.
#
# What enters is a projection of the computation-bearing fields, not a hash of
# the file: a description edit, a key reorder or a comment must leave every
# key exactly where it was, which is the whole reason a projection is worth
# having over hashing method.yaml.
# =============================================================================

_CONTRACT_METHOD = "m_contract"
_CONTRACT_SAMPLE = "samp_contract"

_CONTRACT_BEFORE = (
    "# a leading comment\n"
    "env: fixture-env\n"
    "description: what this method is for\n"
    "script: m_contract.py\n"
    "inputs:\n"
    "  data:\n"
    "    required: false\n"
    "    description: the table to summarise\n"
    "outputs:\n"
    "  result:\n"
    "    type: .csv\n"
    "    description: the summary\n"
)

# edit label -> (the method.yaml that replaces the baseline, does the key move?)
_CONTRACT_EDITS = {
    "output_slot_type": (
        _CONTRACT_BEFORE.replace("    type: .csv\n", "    type: .parquet\n"), True),
    "output_slot_name": (
        _CONTRACT_BEFORE.replace("  result:\n", "  summary:\n"), True),
    "script_selector": (
        _CONTRACT_BEFORE.replace("script: m_contract.py\n", "script: alt.py\n"), True),
    "description": (
        _CONTRACT_BEFORE
        .replace("description: what this method is for", "description: rewritten prose")
        .replace("description: the summary", "description: also rewritten"), False),
    "key_order_and_comment": (
        "outputs:\n"
        "  result:\n"
        "    description: the summary\n"
        "    type: .csv\n"
        "inputs:\n"
        "  data:\n"
        "    description: the table to summarise\n"
        "    required: false\n"
        "script: m_contract.py\n"
        "description: what this method is for\n"
        "# a trailing comment, in a different place\n"
        "env: fixture-env\n", False),
}


def _contract_method_key() -> str:
    """Compose the cache key for the contract fixture's one step."""
    from wfc.execution.claim import compose_cache_key

    return compose_cache_key(
        method_name=_CONTRACT_METHOD,
        method_env="fixture-env",
        module_name="contract_mod",
        sample=_CONTRACT_SAMPLE,
    ).cache_key


@pytest.mark.parametrize("edit", sorted(_CONTRACT_EDITS))
@workflow(
    purpose="Editing what a method declares it produces moves its cache key, so "
            "the next run recomputes instead of returning an artifact named for "
            "the replaced contract; editing what the contract only describes, or "
            "reordering the YAML, leaves every key exactly where it was"
)
def test_contract_edit_moves_the_key_only_when_it_changes_what_a_run_produces(
    tmp_project, edit
):
    """The contract axis of the cache key is a projection, not a file hash."""
    replacement, should_move = _CONTRACT_EDITS[edit]

    口 = Step(step_num=1, name="Write the method's snapshot and register a sample",
             purpose="The contract that enters the key is the snapshot's "
                     "method.yaml beside the scripts, not the MethodContract row "
                     "-- a refused registration can leave the row out of step "
                     "with the snapshot, and code identity follows the snapshot")
    method_dir = Path(tmp_project) / "methods" / _CONTRACT_METHOD
    method_dir.mkdir(parents=True, exist_ok=True)
    (method_dir / f"{_CONTRACT_METHOD}.py").write_text("def main():\n    return 1\n")
    # Both scripts exist from the start, so flipping `script:` between them
    # moves nothing in the snapshot's script digest -- only the selector.
    (method_dir / "alt.py").write_text("def main():\n    return 2\n")
    (method_dir / "method.yaml").write_text(_CONTRACT_BEFORE)
    seed_sample_row(_CONTRACT_SAMPLE, content_hash="c" * 32)

    口 = Step(step_num=2, name="Key the step, edit the contract, key it again",
             purpose="Only method.yaml changes between the two keys; the scripts, "
                     "the sample, the params and the env are all held fixed")
    key_before = _contract_method_key()
    (method_dir / "method.yaml").write_text(replacement)
    key_after = _contract_method_key()

    口 = Step(step_num=3, name="Compare",
             purpose="A computation-bearing edit recomputes; a describing edit or "
                     "a reorder hits the same cache entry it did before")
    if should_move:
        assert key_before != key_after, (
            f"'{edit}' changes what the method produces but left the key unmoved: "
            f"a cached result would be served under the replaced contract"
        )
    else:
        assert key_before == key_after, (
            f"'{edit}' only describes the method, but moved the key: every user "
            f"of this method would recompute for a prose edit"
        )
