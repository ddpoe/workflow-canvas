"""Tier-1 fixture method: faulty -- configurable failure via wfc-client.

Exercises the error path under the single-results-channel model. Failure
modes (set via ``ctx.params["failure_mode"]``):

  - "crash": raises RuntimeError -> the container exits non-zero -> the host
        marks the run failed and skips the manifest read + archive entirely.
  - "save_outside": writes a file OUTSIDE WFC_RUN_DIR and tries to declare it
        with ctx.save_artifact -> the client raises ValueError immediately
        (the path-boundary guard), before any manifest is written.
  - "missing_output": exits cleanly without writing/declaring the required
        output -> the manifest records no output -> the host's declared-slot
        scan fails the run.
  - "succeed": writes and declares the output normally (control case).

No return value — outputs flow through ctx.save_artifact, never a return.
"""

import csv

import wfc_client as wfc


@wfc.method
def faulty(ctx):
    failure_mode = ctx.params.get("failure_mode", "crash")

    if failure_mode == "crash":
        raise RuntimeError("faulty: intentional crash for testing")

    if failure_mode == "save_outside":
        # Write a file in the parent of run_dir, then try to declare it.
        outside = ctx.run_dir.parent / "escape.csv"
        outside.write_text("id\n1\n")
        # This raises ValueError before any manifest is written.
        ctx.save_artifact("output", outside)
        return

    if failure_mode == "missing_output":
        # Exit cleanly without declaring the required output.
        return

    if failure_mode == "succeed":
        data_paths = ctx.input("data")
        if not data_paths or not data_paths[0].exists():
            raise FileNotFoundError(f"Input file not found: {data_paths}")
        with open(data_paths[0], newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            fieldnames = list(reader.fieldnames or [])
        output_path = ctx.run_dir / "output.csv"
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        ctx.save_artifact("output", output_path)
        return

    raise ValueError(f"Unknown failure_mode: {failure_mode}")


if __name__ == "__main__":
    wfc.run()
