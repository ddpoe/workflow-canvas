"""Tier-1 fixture method: qc -- multi-output + metrics via wfc-client.

The canonical Tier-1 authoring shape: ``import wfc_client as wfc``, a single
``@wfc.method`` entry point that takes ``ctx``, writes its own files to the
``ctx.workdir`` scratch dir (``WFC_RUN_DIR/_workdir/``), and declares each
output with ``ctx.save_artifact(name, path)`` plus scalar
``ctx.log_metric(name, value)`` calls. No return value — all results flow
through the ``_wfc_results.json`` manifest the client writes at exit, which
records each output's ``_workdir``-relative path so the host archives the
nested file (a plain ``run_dir`` scan would miss it).

Drops rows whose 'value' column is missing or non-numeric. Emits three
declared outputs (report.json, clean.csv, dropped.csv) and two metrics
(kept_rows, dropped_rows). Mirrors the Tier-2 canonical ``qc`` fixture's
contract so the two can be compared for archive parity.
"""

import csv
import json

import wfc_client as wfc


def _read_csv(path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


@wfc.method
def qc(ctx):
    data_paths = ctx.input("data")
    if not data_paths or not data_paths[0].exists():
        raise FileNotFoundError(f"Input file not found: {data_paths}")
    rows = _read_csv(data_paths[0])
    fieldnames = list(rows[0].keys()) if rows else ["id", "value"]

    clean: list[dict] = []
    dropped: list[dict] = []
    for row in rows:
        raw = row.get("value", "")
        try:
            float(raw)
            clean.append(row)
        except (TypeError, ValueError):
            row["__drop_reason"] = "non_numeric_value"
            dropped.append(row)

    # Canonical ADR-020 pattern: write outputs to the ctx.workdir scratch dir
    # (WFC_RUN_DIR/_workdir/) and declare each via save_artifact. The manifest
    # records the _workdir-relative path; the host resolves it to archive the
    # nested file.
    clean_path = ctx.workdir / "clean.csv"
    with open(clean_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(clean)

    dropped_path = ctx.workdir / "dropped.csv"
    drop_fields = fieldnames + ["__drop_reason"]
    with open(dropped_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=drop_fields)
        w.writeheader()
        w.writerows(dropped)

    report = {"kept": len(clean), "dropped": len(dropped)}
    report_path = ctx.workdir / "report.json"
    report_path.write_text(json.dumps(report, indent=2))

    ctx.save_artifact("report", report_path)
    ctx.save_artifact("clean", clean_path)
    ctx.save_artifact("dropped", dropped_path)

    ctx.log_metric("kept_rows", len(clean))
    ctx.log_metric("dropped_rows", len(dropped))


if __name__ == "__main__":
    wfc.run()
