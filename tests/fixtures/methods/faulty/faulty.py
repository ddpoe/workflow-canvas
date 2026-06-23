"""Fixture method: faulty -- configurable failure for testing error handling.

Failure modes (set via params["failure_mode"]):
  - "crash": raises RuntimeError (emits pre-crash breadcrumbs on stdout/stderr)
  - "noisy_stderr": writes many stderr lines, then raises (exercises log tail UX)
  - "streaming_fail": emits paced stdout ticks for several seconds, then raises
        mid-stream — drives the SSE record/replay "fault mid-stream" gallery
        video.  Params: pre_crash_duration_s (float, default 4.5),
        pre_crash_stdout_lines (int, default 3), crash_at_tick (int, default
        = pre_crash_stdout_lines so the crash lands on the last tick).
  - "missing_output": exits cleanly without producing the declared output file
  - "succeed": behaves like transform (reads input, writes output)

Reads WFC_RUN_DIR, WFC_INPUT_PATHS, and WFC_PARAMS from environment.
"""

import csv
import json
import os
import sys
import time
from pathlib import Path


def main():
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    params = json.loads(os.environ.get("WFC_PARAMS", "{}"))
    failure_mode = params.get("failure_mode", "crash")

    print(f"faulty: starting (failure_mode={failure_mode!r})")

    if failure_mode == "crash":
        print("faulty: preparing to crash in 3... 2... 1...")
        print("faulty: about to raise RuntimeError", file=sys.stderr)
        raise RuntimeError("faulty: intentional crash for testing")

    elif failure_mode == "noisy_stderr":
        print("faulty: generating verbose stderr before crashing")
        for i in range(200):
            print(f"faulty[warn {i:03d}]: simulated diagnostic line", file=sys.stderr)
        print("faulty: done with noise, now crashing", file=sys.stderr)
        raise RuntimeError("faulty: crashed after noisy stderr")

    elif failure_mode == "streaming_fail":
        duration_s = float(params.get("pre_crash_duration_s", 4.5))
        n_ticks = max(1, int(params.get("pre_crash_stdout_lines", 3)))
        crash_at = int(params.get("crash_at_tick", n_ticks))
        print(
            f"[stream-fail] starting · pre_crash_duration={duration_s:.1f}s · "
            f"pre_crash_ticks={n_ticks} · crash_at_tick={crash_at}",
            flush=True,
        )
        sleep_s = duration_s / n_ticks
        for i in range(1, n_ticks + 1):
            time.sleep(sleep_s)
            print(f"[stream-fail] tick {i}/{n_ticks} · still going...", flush=True)
            if i == crash_at:
                print(
                    f"[stream-fail] hit fatal condition at tick {i}",
                    file=sys.stderr,
                    flush=True,
                )
                raise RuntimeError(f"stream_fail: fatal at tick {crash_at}")

    elif failure_mode == "missing_output":
        # Exit cleanly but do not produce the declared output file
        print("faulty: exiting without producing output (missing_output mode)")
        sys.exit(0)

    elif failure_mode == "succeed":
        # Behave like transform
        slot_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{}"))
        data_paths = slot_paths.get("data", [])
        if not data_paths or not Path(data_paths[0]).exists():
            raise FileNotFoundError(f"Input file not found: {data_paths}")
        input_path = data_paths[0]

        with open(input_path, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            fieldnames = list(reader.fieldnames or [])

        output_path = run_dir / "output.csv"
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        print(f"faulty: succeeded, wrote {len(rows)} rows")

    else:
        raise ValueError(f"Unknown failure_mode: {failure_mode}")


if __name__ == "__main__":
    main()
