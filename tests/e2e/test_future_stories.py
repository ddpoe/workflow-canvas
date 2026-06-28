"""
E2E Workflow: Future Stories (Conservative Skeletons)

Only roadmap-level, user-facing stories remain as skeletons:
1) Failure and resume
2) Canvas-triggered execution
3) Cache-aware re-run
4) Contract validation on pipeline completion

Each skeleton follows the full production path in its body:
  init_project → register_module → register_method → ...
"""

import pytest
from axiom_annotations import workflow, Step, AutoStep

from wfc.init import init_project
from wfc.register import register_module, register_method

pytestmark = pytest.mark.skip(
    reason="Skeleton tests depend on deleted methods/ directory; "
           "replaced by focused pipeline tests"
)


@workflow(purpose="Pipeline fails mid-way, user resumes successfully from the failed point")
def test_error_recovery_story(wfc_project):
    tmp = wfc_project

    口 = AutoStep(step_num=1)
    init_project(tmp)

    口 = AutoStep(step_num=2)
    register_module(name="demo_pipeline", contracts=[], description="3-step demo pipeline")

    口 = Step(step_num=3, name="Register method scripts",
             purpose="AST-scan each method script and register functions and parameters")
    for method_name in ("preprocess", "filter_cells", "label"):
        register_method(
            method_dir=tmp / "methods" / method_name,
            module_name="demo_pipeline")

    口 = Step(step_num=4, name="Run pipeline with forced failure",
             purpose="A mid-pipeline step fails while prior completed work is preserved",
             critical="NOT IMPLEMENTED")

    口 = Step(step_num=5, name="Resume after fix",
             purpose="Re-run resumes from failed point and completes downstream steps",
             critical="NOT IMPLEMENTED")

    口 = Step(step_num=6, name="Verify user-visible recovery",
             purpose="Final lineage is complete and prior successful work is reused",
             critical="NOT IMPLEMENTED")

    pytest.skip("error recovery story not implemented")


@workflow(purpose="Canvas API accepts a workflow execution request and returns lineage for resulting runs")
def test_canvas_api_story(wfc_project):
    tmp = wfc_project

    口 = AutoStep(step_num=1)
    init_project(tmp)

    口 = AutoStep(step_num=2)
    register_module(name="demo_pipeline", contracts=[], description="3-step demo pipeline")

    口 = Step(step_num=3, name="Register method scripts",
             purpose="AST-scan each method script and register functions and parameters")
    for method_name in ("preprocess", "filter_cells", "label"):
        register_method(
            method_dir=tmp / "methods" / method_name,
            module_name="demo_pipeline")

    口 = Step(step_num=4, name="Submit workflow from canvas",
             purpose="POST execute payload from UI and receive execution identifier",
             critical="NOT IMPLEMENTED")

    口 = Step(step_num=5, name="Track completion",
             purpose="UI polls run state until execution finishes",
             critical="NOT IMPLEMENTED")

    口 = Step(step_num=6, name="Inspect lineage via API",
             purpose="GET lineage endpoint returns expected chain for leaf run",
             critical="NOT IMPLEMENTED")

    pytest.skip("canvas execution story not implemented")


@workflow(purpose="Re-running an unchanged pipeline uses cache and skips duplicate execution")
def test_cache_resume_story(wfc_project):
    tmp = wfc_project

    口 = AutoStep(step_num=1)
    init_project(tmp)

    口 = AutoStep(step_num=2)
    register_module(name="demo_pipeline", contracts=[], description="3-step demo pipeline")

    口 = Step(step_num=3, name="Register method scripts",
             purpose="AST-scan each method script and register functions and parameters")
    for method_name in ("preprocess", "filter_cells", "label"):
        register_method(
            method_dir=tmp / "methods" / method_name,
            module_name="demo_pipeline")

    口 = Step(step_num=4, name="Run baseline pipeline",
             purpose="Initial execution completes and stores reusable outputs",
             critical="NOT IMPLEMENTED")

    口 = Step(step_num=5, name="Run same request again",
             purpose="Second execution detects prior completed work and reuses it",
             critical="NOT IMPLEMENTED")

    口 = Step(step_num=6, name="Verify cache behavior",
             purpose="No duplicate work is executed and user still gets expected outputs",
             critical="NOT IMPLEMENTED")

    pytest.skip("cache resume story not implemented")


@workflow(purpose="Content-level contract validation catches column mismatches before/after execution (ADR-005)")
def test_contract_validation_story(wfc_project):
    """ADR-005: Content-level input/output validation end-to-end.

    Exercises the full flow: parse method.yaml with columns spec,
    register the method (storing MethodContract in DB), then validate
    input columns against an actual CSV file.
    """
    tmp = wfc_project

    口 = AutoStep(step_num=1)
    init_project(tmp)

    口 = Step(step_num=2, name="Register module with contracts",
             purpose="Create a module so the method can be registered under it")
    register_module(
        name="demo_pipeline",
        contracts=[],
        description="Pipeline for ADR-005 content validation test",
    )

    口 = Step(step_num=3, name="Register binary_labeling method",
             purpose="Parse method.yaml (with columns spec) and store MethodContract in DB")
    register_method(
        method_dir=tmp / "methods" / "binary_labeling",
        module_name="demo_pipeline",
    )

    口 = Step(step_num=4, name="Parse contract and verify columns spec preserved",
             purpose="Confirm that parse_method_yaml preserves the columns key in input slots")
    from wfc.contracts import parse_method_yaml
    contract = parse_method_yaml(tmp / "methods" / "binary_labeling")
    assert contract is not None
    data_slot = contract["inputs"]["data"]
    assert "columns" in data_slot, "columns key must be preserved in parsed contract"
    assert "from_params" in data_slot["columns"], "from_params key must be in columns spec"

    口 = Step(step_num=5, name="Validate input CSV with matching columns",
             purpose="Good CSV with required columns passes validation without error")
    from wfc.contracts import validate_input_columns
    good_csv = tmp / "good_input.csv"
    good_csv.write_text("area,R1_pRb-AF647_nuc_median\n100,50\n")

    # This should pass -- CSV has the feature_column param value
    validate_input_columns(
        good_csv,
        data_slot["columns"],
        params={"feature_column": "R1_pRb-AF647_nuc_median"},
    )

    口 = Step(step_num=6, name="Validate input CSV with missing column raises",
             purpose="Input gate blocks execution when required column is missing",
             critical="Must raise ContractViolation naming the missing column")
    from wfc_client import ContractViolation
    bad_csv = tmp / "bad_input.csv"
    bad_csv.write_text("area,centroid-0\n100,50\n")

    with pytest.raises(ContractViolation) as exc_info:
        validate_input_columns(
            bad_csv,
            data_slot["columns"],
            params={"feature_column": "R1_pRb-AF647_nuc_median"},
            method_name="binary_labeling",
        )

    error_msg = str(exc_info.value)
    assert "R1_pRb-AF647_nuc_median" in error_msg, "Error must name the missing param-derived column"

    口 = Step(step_num=7, name="Verify output soft validation warns on drift",
             purpose="Output contract drift produces a warning, not an error")
    from wfc.contracts import validate_output_columns

    output_csv = tmp / "output.csv"
    output_csv.write_text("label,area\n1,100\n")
    output_spec = contract["outputs"]["labeled"]["columns"]

    # Output validation should warn but NOT raise -- "label" is present
    # but the feature_column param-derived column is missing from output
    validate_output_columns(
        output_csv, output_spec,
        params={"feature_column": "R1_pRb-AF647_nuc_median"},
        slot_name="labeled", method_name="binary_labeling",
    )
    # If we got here without an exception, soft validation works correctly
