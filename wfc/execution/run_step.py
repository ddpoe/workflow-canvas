"""Run-step orchestrator: the five-phase execution sequence.

Claim decides whether work happens at all; materialize turns declared
inputs into local paths; dispatch assembles the container invocation and
runs it; collect turns the method's exit into outputs and metrics; record
writes the rows, sentinel, and sidecars every other phase's exits funnel
into.

Exit routing:

- A claim failure returns before the record tail runs: no run is
  registered, and inside a pipeline only the refused target's
  claim-refusal outcome sidecar is written for the pipeline-end walk.
- Every other ending — cache hit, a materialize failure, dispatch
  failures, the SLURM carve-out, a missing declared slot, a refused
  output, and success — flows through the single record tail, which
  composes each ending's exact writes. A materialize failure skips
  dispatch and collect, and its message lands on the failed run row.
"""
from __future__ import annotations

from axiom_annotations import workflow, AutoStep

from .. import layout
from .claim import run_claim
from .collect import run_collect
from .dispatch import run_dispatch
from .materialize import run_materialize
from .record import run_record
from ..persistence import project_root as get_project_root


@workflow(purpose="Execute a single pipeline step: claim, materialize, "
                  "dispatch, collect, record")
def run_step(
    node_id: str,
    sample: str,
    variant: str = "default",
    method_name: str | None = None,
    module_name: str | None = None,
    script_path: str | None = None,
    params: dict | None = None,
    parent_run_ids: list | None = None,
    pipeline_id: str | None = None,
    pipeline_json: str | None = None,
    git_commit: str | None = None,
    ref_inputs: list[str] | None = None,
    collapsed_samples: list[str] | None = None,
) -> int:
    """Execute a single pipeline step end-to-end.

    Runs the five-phase execution protocol:

    1. Claim — resolve step config and parent runs from sentinel sidecars,
       register the run via ``pre_run``, carry its cache answer.
    2. Materialize — classify each input slot's source (parent-slot /
       sample-via-selector / reference) and resolve it to local paths; a
       failure skips to the record tail, which fails the run row with its
       message.
    3. Dispatch — write ``_run_context.json`` and the WFC_* env, resolve
       the node's container image, build the ``docker run`` command, and
       run the method subprocess.
    4. Collect — read ``_wfc_results.json``, scan declared output slots,
       create RunOutput rows.
    5. Record — flip the run row, touch the sentinel, write the run-id and
       outcome sidecars, and enqueue outputs for async DVC push
       (non-fatal); on a cache hit only the sentinel and sidecars are
       written.

    Args:
        node_id: Unique node identity within the pipeline.
        sample: Sample identifier.
        variant: Parameter variant name (default: "default").
        method_name: Method name (inline fallback).
        module_name: Module name (inline fallback).
        script_path: Path to method script (inline fallback).
        params: Parameter dict (inline fallback).
        parent_run_ids: Parent run IDs as slot:id or plain id strings.
        pipeline_id: Pipeline execution ID.
        pipeline_json: Path to pipeline JSON file.
        git_commit: Pre-computed git commit SHA.
        ref_inputs: Static ``label=path`` ref-input flags from
            ``run_reference`` nodes. Pre-resolved by the orchestrator.
        collapsed_samples: For collapsed-fan-in roots (sample is
            ``COLLAPSED_SAMPLE``),
            the bundled sample identities. The runtime resolver walks
            ``data/samples/<s>/`` per name and accumulates the per-sample
            data files into the fan-in slot. Order is preserved.
            Both phases receive it, for different reasons: claim folds the
            bundle into the run's cache key (it is what the root's result was
            computed from), materialize resolves it into data files.

    Returns:
        0 on success, 1 on failure.
    """

    口 = AutoStep(step_num=1, name="Claim")
    claim = run_claim(
        node_id=node_id,
        sample=sample,
        variant=variant,
        method_name=method_name,
        module_name=module_name,
        script_path=script_path,
        params=params,
        parent_run_ids=parent_run_ids,
        pipeline_id=pipeline_id,
        pipeline_json=pipeline_json,
        git_commit=git_commit,
        collapsed_samples=collapsed_samples,
    )
    if not claim["ok"]:
        return claim["rc"]

    pipeline_id = claim["pipeline_id"]
    run_id = claim["run_id"]

    # Outcome sidecar directory: created for every path that survives the
    # claim — including a materialize failure, whose failed outcome the
    # record tail writes, and the SLURM carve-out, which writes no outcome
    # but leaves the directory in place.
    outcomes_dir = layout.pipeline_outcomes_dir(get_project_root(), pipeline_id)
    outcomes_dir.mkdir(parents=True, exist_ok=True)

    ending: dict = {"ending": "completed"}
    output_files: list[str] | None = None
    metrics: dict | None = None

    if claim["flag"] == "CACHED":
        # Outputs already live in the DVC cache (the authoritative
        # store). Downstream nodes use ``resolve_input`` to read directly
        # from the cache; the record tail only signals Snakemake via the
        # sentinel and writes the audit-row sidecar for lineage.
        ending = {"ending": "cached"}
    else:
        口 = AutoStep(step_num=2, name="Materialize")
        mat = run_materialize(
            node_id=node_id,
            sample=sample,
            pipeline_data=claim["pipeline_data"],
            parent_run_ids=claim["parent_run_ids"],
            ref_inputs=ref_inputs,
            collapsed_samples=collapsed_samples,
        )
        # A materialize failure skips dispatch and collect; the record tail
        # marks the row failed with its message.
        if not mat["ok"]:
            ending = mat

    if ending["ending"] == "completed" and claim["flag"] != "CACHED":
        口 = AutoStep(step_num=3, name="Dispatch")
        dsp = run_dispatch(
            node_id=node_id,
            sample=sample,
            variant=variant,
            method_name=claim["method_name"],
            script_path=claim["script_path"],
            module_name=claim["module_name"],
            params=claim["params"],
            pipeline_id=pipeline_id,
            pipeline_json=claim["pipeline_json"],
            run_id=run_id,
            slot_paths=mat["slot_paths"],
            slot_outputs=claim["slot_outputs"],
            slot_types=claim["slot_types"],
        )
        if dsp["ok"]:
            口 = AutoStep(step_num=4, name="Collect")
            col = run_collect(
                method_name=claim["method_name"],
                run_id=run_id,
                node_cfg=claim["node_cfg"],
                slot_outputs=claim["slot_outputs"],
            )
            if col["ok"]:
                output_files = col["output_files"]
                metrics = col["metrics"]
            else:
                ending = col
        else:
            ending = dsp

    口 = AutoStep(step_num=5, name="Record")
    rc = run_record(
        ending=ending["ending"],
        run_id=run_id,
        node_id=node_id,
        sample=sample,
        variant=variant,
        pipeline_id=pipeline_id,
        outcomes_dir=outcomes_dir,
        error_message=ending.get("error_message"),
        error_traceback=ending.get("error_traceback"),
        output_files=output_files,
        metrics=metrics,
    )
    return rc
