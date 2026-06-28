"""Tier-1 fixture method: merge -- multi-input fan-in via wfc-client.

Exercises the fan-in path: a single input slot (``sources``) resolves to
multiple paths via ``ctx.input("sources")``. The method concatenates all
source CSVs (unioning fieldnames) and declares one output. No return value —
the single ``merged`` output flows through ``ctx.save_artifact``.
"""

import csv

import wfc_client as wfc


@wfc.method
def merge(ctx):
    source_paths = ctx.input("sources")
    if not source_paths:
        raise ValueError("No input paths provided for merge")

    all_rows = []
    fieldnames: list[str] = []
    seen = set()
    for sp in source_paths:
        with open(sp, newline="") as f:
            reader = csv.DictReader(f)
            for name in (reader.fieldnames or []):
                if name not in seen:
                    fieldnames.append(name)
                    seen.add(name)
            for row in reader:
                all_rows.append(row)

    output_path = ctx.run_dir / "merged.csv"
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames or [])
        writer.writeheader()
        writer.writerows(all_rows)

    ctx.save_artifact("merged", output_path)
    ctx.log_metric("merged_rows", len(all_rows))
    ctx.log_metric("source_count", len(source_paths))


if __name__ == "__main__":
    wfc.run()
