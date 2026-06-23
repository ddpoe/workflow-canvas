"""
Lineage query — the core feature.

Recursive CTE that walks run_inputs.source_run_id to trace
any result back through the full pipeline.

Usage:
    python -m wfc.lineage --run-id 5
    python -m wfc.lineage --all
"""

import argparse
import json
import sys
from datetime import datetime

from sqlmodel import select, text

from dflow.core.decorators import task, Step

from .database import get_session
from .models import Run, RunInput, RunOutput, Method, MethodVersion


@task(purpose="Walk the lineage DAG for a run via recursive CTE — returns root-first ancestry with fan-in support and git commit per node")
def get_lineage(run_id: int) -> list[dict]:
    """Walk the full lineage DAG for a given run (recursive CTE).

    Fan-in nodes (e.g., csv_merge with two parent feature_qc runs) are
    deduplicated: each run appears once with parent_run_ids listing ALL parents.
    Each node includes the git_commit of the code version that produced it
    (None if the run predates versioning or was never version-stamped).
    """
    口 = Step(step_num=1, name="Execute recursive CTE",
             purpose="Walk upstream runs via SQL recursive CTE, joining code version per run — may return duplicate rows for fan-in nodes")
    with get_session() as session:
        # SQLite supports recursive CTEs.
        # Fan-in nodes produce multiple rows (one per parent via LEFT JOIN);
        # we deduplicate in Python (Step 2) rather than in SQL.
        # Gap 16: LEFT JOIN method_versions in both arms to surface git_commit.
        query = text("""
            WITH RECURSIVE lineage AS (
                -- Start from the requested run
                SELECT r.id, r.method_id, r.sample, r.status, r.params,
                       r.started_at, r.finished_at, r.nf_process_name,
                       ri.source_run_id, 0 as depth,
                       mv.git_commit
                FROM runs r
                LEFT JOIN run_inputs ri ON ri.run_id = r.id
                LEFT JOIN method_versions mv ON mv.id = r.version_id
                WHERE r.id = :run_id

                UNION ALL

                -- Walk up the chain
                SELECT r.id, r.method_id, r.sample, r.status, r.params,
                       r.started_at, r.finished_at, r.nf_process_name,
                       ri.source_run_id, l.depth + 1,
                       mv.git_commit
                FROM runs r
                LEFT JOIN run_inputs ri ON ri.run_id = r.id
                LEFT JOIN method_versions mv ON mv.id = r.version_id
                JOIN lineage l ON l.source_run_id = r.id
            )
            SELECT * FROM lineage ORDER BY depth DESC;
        """)
        result = session.exec(query, params={"run_id": run_id})
        rows = result.all()

        口 = Step(step_num=2, name="Deduplicate fan-in rows",
                 purpose="Fan-in nodes appear once per parent from CTE; group by run_id and collect all parent_run_ids")
        from collections import defaultdict
        run_parents: dict[int, list[int]] = defaultdict(list)
        run_depth: dict[int, int] = {}
        run_row: dict = {}

        for row in rows:
            rid = row.id
            if rid not in run_row:
                run_row[rid] = row
                run_depth[rid] = row.depth
            else:
                # Keep the minimum (shallowest) depth seen for this run
                run_depth[rid] = min(run_depth[rid], row.depth)
            if row.source_run_id is not None and row.source_run_id not in run_parents[rid]:
                run_parents[rid].append(row.source_run_id)

        # Root-first order (ascending depth)
        sorted_ids = sorted(run_row.keys(), key=lambda rid: run_depth[rid])

        口 = Step(step_num=3, name="Build chain",
                 purpose="Enrich each deduplicated run with method name, all parent IDs, code version, outputs, and timestamps")
        chain = []
        for rid in sorted_ids:
            row = run_row[rid]
            method = session.get(Method, row.method_id)
            method_name = method.name if method else f"method_{row.method_id}"

            outputs_stmt = select(RunOutput).where(RunOutput.run_id == rid)
            outputs = session.exec(outputs_stmt).all()

            # git_commit comes from the CTE JOIN on method_versions;
            # None when version_id is absent (run predates versioning).
            chain.append({
                "run_id": rid,
                "method": method_name,
                "sample": row.sample,
                "status": row.status,
                "params": json.loads(row.params) if isinstance(row.params, str) else row.params,
                "parent_run_ids": run_parents[rid],   # list; empty [] for root nodes
                "git_commit": row.git_commit,         # None if run has no version_id
                "nf_process": row.nf_process_name,
                "started_at": str(row.started_at) if row.started_at else None,
                "finished_at": str(row.finished_at) if row.finished_at else None,
                "outputs": [{"name": o.output_name, "path": o.artifact_path} for o in outputs],
                "depth": run_depth[rid],
            })
        return chain


def get_all_runs() -> list[dict]:
    """List all runs with their method names, all parent IDs, and git commit."""
    with get_session() as session:
        runs = session.exec(select(Run).order_by(Run.id)).all()
        result = []
        for r in runs:
            method = session.get(Method, r.method_id)
            input_stmt = select(RunInput).where(RunInput.run_id == r.id)
            run_inputs_rows = session.exec(input_stmt).all()
            parent_ids = [ri.source_run_id for ri in run_inputs_rows if ri.source_run_id is not None]
            version = session.get(MethodVersion, r.version_id) if r.version_id else None
            result.append({
                "run_id": r.id,
                "method": method.name if method else "?",
                "sample": r.sample,
                "status": r.status,
                "params": r.params,
                "parent_run_ids": parent_ids,   # list; [] for root nodes
                "git_commit": version.git_commit if version else None,
                "nf_process": r.nf_process_name,
            })
        return result


def print_lineage_tree(chain: list[dict]):
    """Pretty-print a lineage DAG as a tree (fan-in nodes show all parents)."""
    if not chain:
        print("No lineage found.")
        return

    leaf = chain[-1]
    print(f"\n{'='*70}")
    print(f"LINEAGE for Run {leaf['run_id']} ({leaf['method']} / {leaf['sample']})")
    print(f"{'='*70}")

    for i, node in enumerate(chain):
        indent = "  " * i
        arrow = "→ " if i > 0 else "  "
        status_icon = "✅" if node["status"] == "completed" else "❌"
        parents = node["parent_run_ids"]
        fan_in = f" [fan-in: {parents}]" if len(parents) > 1 else ""
        commit = node.get("git_commit")
        commit_str = f" @{commit[:8]}" if commit else ""
        print(f"{indent}{arrow}{status_icon} Run {node['run_id']}: {node['method']} ({node['sample']}){fan_in}{commit_str}")
        print(f"{indent}   params: {json.dumps(node['params'], separators=(',', ':'))}")
        if node["outputs"]:
            for out in node["outputs"]:
                print(f"{indent}   output: {out['name']}")
    print()


def print_all_runs(runs: list[dict]):
    """Print all runs as a table."""
    if not runs:
        print("No runs in database.")
        return

    print(f"\n{'='*100}")
    print(f"{'ID':>4}  {'Method':<16}  {'Sample':<10}  {'Status':<10}  {'Parents':>6}  {'Commit':<10}  {'NF Process':<20}  Params")
    print(f"{'-'*100}")
    for r in runs:
        parent = ",".join(str(p) for p in r["parent_run_ids"]) if r["parent_run_ids"] else "-"
        nf = r["nf_process"] or "-"
        commit = r.get("git_commit")
        commit_str = commit[:8] if commit else "-"
        params_short = json.dumps(r["params"], separators=(",", ":")) if r["params"] else "-"
        if len(params_short) > 30:
            params_short = params_short[:27] + "..."
        print(f"{r['run_id']:>4}  {r['method']:<16}  {r['sample']:<10}  {r['status']:<10}  {parent:>6}  {commit_str:<10}  {nf:<20}  {params_short}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Query run lineage")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-id", type=int, help="Show lineage for a specific run")
    group.add_argument("--all", action="store_true", help="List all runs")
    args = parser.parse_args()

    if args.all:
        runs = get_all_runs()
        print_all_runs(runs)
    else:
        chain = get_lineage(args.run_id)
        print_lineage_tree(chain)


if __name__ == "__main__":
    main()
