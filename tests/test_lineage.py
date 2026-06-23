"""
Integration tests: get_lineage() DAG traversal and git_commit surfacing
(Gap 8 + Gap 16).

Coverage:
  - Fan-in node collects all parent run IDs in parent_run_ids (Gap 8)
  - Fan-in node appears exactly once in the chain despite multiple CTE rows (Gap 8)
  - Root node returns parent_run_ids == [] (Gap 8)
  - "git_commit" key is present in every node dict, versioned or not (Gap 16 gotcha)
  - get_all_runs() includes "git_commit" per run (Gap 16)

All tests insert DB rows directly (no CLI scaffolding) using tmp_project +
get_session(). This keeps setup minimal and focused on the models under test.
"""

from dflow.core.decorators import workflow, Step

from wfc.database import get_session
from wfc.lineage import get_lineage, get_all_runs
from wfc.models import Method, MethodVersion, Module, Run, RunInput


# =============================================================================
# Helpers
# =============================================================================

def _make_method(session, module_name: str = "lin_mod", method_name: str = "lin_method") -> int:
    """Insert a minimal Module + Method row and return method.id."""
    mod = Module(name=module_name, description="lineage test module")
    session.add(mod)
    session.commit()
    session.refresh(mod)
    method = Method(name=method_name, module_id=mod.id)
    session.add(method)
    session.commit()
    session.refresh(method)
    return method.id  # type: ignore[return-value]


def _make_run(session, method_id: int, sample: str = "S1", version_id=None) -> int:
    """Insert a completed Run row and return run.id."""
    run = Run(method_id=method_id, sample=sample, status="completed", version_id=version_id)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run.id  # type: ignore[return-value]


def _link(session, run_id: int, source_run_id: int, slot: str = "sources") -> None:
    """Insert a RunInput row linking run_id ← source_run_id."""
    ri = RunInput(run_id=run_id, source_run_id=source_run_id, input_name=slot)
    session.add(ri)
    session.commit()


# =============================================================================
# Gap 8: fan-in parent collection
# =============================================================================

@workflow(
    purpose="A fan-in node (csv_merge with two upstream runs) reports both parent run IDs "
            "in parent_run_ids — not just the first one"
)
def test_fan_in_node_has_multiple_parent_run_ids(tmp_project):
    """Fan-in lineage: merge run has two RunInput rows → parent_run_ids has length 2."""
    口 = Step(
        step_num=1,
        name="Seed three runs",
        purpose="Insert two upstream filter runs and one downstream merge run")
    with get_session() as session:
        method_id = _make_method(session)
        parent_a = _make_run(session, method_id, sample="S1")
        parent_b = _make_run(session, method_id, sample="S1")
        merge = _make_run(session, method_id, sample="S1")
        _link(session, merge, parent_a)
        _link(session, merge, parent_b)

    口 = Step(
        step_num=2,
        name="Call get_lineage on merge run",
        purpose="Traverse the DAG upward from the merge node")
    chain = get_lineage(merge)

    口 = Step(
        step_num=3,
        name="Verify both parents are collected",
        purpose="The merge node must report exactly two parent run IDs — one per upstream fan-in source")
    merge_node = next(n for n in chain if n["run_id"] == merge)
    assert set(merge_node["parent_run_ids"]) == {parent_a, parent_b}


@workflow(
    purpose="A fan-in node that produces two CTE rows (one per parent) appears "
            "exactly once in the deduplicated lineage chain"
)
def test_fan_in_node_appears_exactly_once(tmp_project):
    """Fan-in deduplication: merge run ID must occur exactly once in the returned chain."""
    口 = Step(
        step_num=1,
        name="Seed fan-in topology",
        purpose="Insert two parent runs and one merge run with two RunInput rows")
    with get_session() as session:
        method_id = _make_method(session, module_name="lin_mod2", method_name="lin_method2")
        parent_a = _make_run(session, method_id, sample="S2")
        parent_b = _make_run(session, method_id, sample="S2")
        merge = _make_run(session, method_id, sample="S2")
        _link(session, merge, parent_a)
        _link(session, merge, parent_b)

    口 = Step(
        step_num=2,
        name="Traverse lineage",
        purpose="Walk the DAG from the merge node to its ancestors")
    chain = get_lineage(merge)

    口 = Step(
        step_num=3,
        name="Assert no duplicate entries",
        purpose="Each run must appear exactly once regardless of how many parent CTE rows it generated")
    merge_occurrences = [n for n in chain if n["run_id"] == merge]
    assert len(merge_occurrences) == 1


@workflow(
    purpose="A root run with no upstream dependencies returns an empty parent_run_ids list"
)
def test_root_node_has_empty_parent_run_ids(tmp_project):
    """Root node (no RunInput rows) → parent_run_ids == []."""
    口 = Step(
        step_num=1,
        name="Seed root run",
        purpose="Insert a single Run with no RunInput rows — simulates a pipeline root node")
    with get_session() as session:
        method_id = _make_method(session, module_name="lin_mod3", method_name="lin_method3")
        root = _make_run(session, method_id, sample="S3")

    口 = Step(
        step_num=2,
        name="Call get_lineage on root",
        purpose="Walk ancestry from a run that has no parents")
    chain = get_lineage(root)

    口 = Step(
        step_num=3,
        name="Verify no parents reported",
        purpose="A root node must return an empty parent list, not None or a missing key")
    root_node = next(n for n in chain if n["run_id"] == root)
    assert root_node["parent_run_ids"] == []


# =============================================================================
# Gap 16: git_commit surfacing
# =============================================================================

@workflow(
    purpose="Every node in the lineage chain contains a 'git_commit' key — "
            "versioned runs carry the commit string, unversioned runs carry None"
)
def test_git_commit_present_in_every_node(tmp_project):
    """Gap 16 gotcha: git_commit must be in every node dict, not silently absent.

    Two-node chain: root has no version_id, leaf has a MethodVersion row.
    Both must have the 'git_commit' key; only the leaf's value is non-None.
    """
    口 = Step(
        step_num=1,
        name="Create method and version record",
        purpose="Insert a Method and a MethodVersion row representing a specific code commit")
    COMMIT = "a" * 40
    with get_session() as session:
        method_id = _make_method(session, module_name="lin_mod4", method_name="lin_method4")
        version = MethodVersion(method_id=method_id, code_fingerprint="f" * 64, git_commit=COMMIT)
        session.add(version)
        session.commit()
        session.refresh(version)
        version_id = version.id

    口 = Step(
        step_num=2,
        name="Seed a two-run chain",
        purpose="Insert an unversioned root run and a versioned leaf run linked by a RunInput row")
    with get_session() as session:
        root = _make_run(session, method_id, sample="S4", version_id=None)
        leaf = _make_run(session, method_id, sample="S4", version_id=version_id)
        _link(session, leaf, root, slot="data")

    口 = Step(
        step_num=3,
        name="Traverse lineage and inspect git_commit",
        purpose="Confirm 'git_commit' is present in every returned node, "
                "with the correct value for versioned runs and None for unversioned ones")
    chain = get_lineage(leaf)

    # Key must exist in every node — the common omission is adding the JOIN
    # but forgetting to thread the value through the dict construction (Gap 16 gotcha).
    for node in chain:
        assert "git_commit" in node, (
            f"Run {node['run_id']} is missing 'git_commit' key — "
            "check that both CTE arms SELECT mv.git_commit and Step 3 includes it in the dict"
        )

    root_node = next(n for n in chain if n["run_id"] == root)
    leaf_node = next(n for n in chain if n["run_id"] == leaf)
    assert root_node["git_commit"] is None
    assert leaf_node["git_commit"] == COMMIT


@workflow(
    purpose="get_all_runs() includes a 'git_commit' key for every run, "
            "carrying the commit string for versioned runs and None for unversioned ones"
)
def test_get_all_runs_includes_git_commit(tmp_project):
    """get_all_runs() gap 16 coverage: git_commit present, correct values, not silently absent."""
    口 = Step(
        step_num=1,
        name="Seed versioned and unversioned runs",
        purpose="Insert one run with a MethodVersion and one without to cover both branches")
    COMMIT = "b" * 40
    with get_session() as session:
        method_id = _make_method(session, module_name="lin_mod5", method_name="lin_method5")
        version = MethodVersion(method_id=method_id, code_fingerprint="f" * 64, git_commit=COMMIT)
        session.add(version)
        session.commit()
        session.refresh(version)
        version_id = version.id

        unversioned = _make_run(session, method_id, sample="S5a", version_id=None)
        versioned = _make_run(session, method_id, sample="S5b", version_id=version_id)

    口 = Step(
        step_num=2,
        name="Call get_all_runs",
        purpose="Retrieve the full run list and check git_commit across both runs")
    runs = get_all_runs()

    口 = Step(
        step_num=3,
        name="Assert git_commit present and correct",
        purpose="Every dict must have the 'git_commit' key; versioned run must match the stored commit")
    run_map = {r["run_id"]: r for r in runs}

    assert unversioned in run_map
    assert versioned in run_map

    for r in runs:
        assert "git_commit" in r, (
            f"Run {r['run_id']} is missing 'git_commit' key in get_all_runs() output"
        )

    assert run_map[unversioned]["git_commit"] is None
    assert run_map[versioned]["git_commit"] == COMMIT
