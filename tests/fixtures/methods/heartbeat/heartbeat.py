"""Fixture method: heartbeat -- emit timed stdout/stderr for streaming dogfood.

Exercises the SSE log-stream endpoint (step 4) and the Builder Output tab
(step 8) in `.superpowers/brainstorm/pipeline-output-visibility/plan.md`.
The method passes the input CSV through to the output unchanged; the only
point is producing visible, incremental log output during the run.

Reads WFC_RUN_DIR, WFC_INPUT_PATHS, and WFC_PARAMS from environment.
"""

import csv
import json
import os
import shutil
import sys
import time
from pathlib import Path


def _emit(stream, line: str) -> None:
    stream.write(line + "\n")
    stream.flush()


def main():
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    slot_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{}"))
    params = json.loads(os.environ.get("WFC_PARAMS", "{}"))

    duration_s = float(params.get("duration_s", 5.0))
    stdout_lines = max(1, int(params.get("stdout_lines", 15)))
    stderr_every = int(params.get("stderr_every", 4))

    data_paths = slot_paths.get("data", [])
    if not data_paths or not Path(data_paths[0]).exists():
        raise FileNotFoundError(f"Input file not found: {data_paths}")
    input_path = Path(data_paths[0])

    with open(input_path, newline="") as f:
        rows = list(csv.DictReader(f))

    _emit(sys.stdout, f"[heartbeat] starting · duration={duration_s:.1f}s · ticks={stdout_lines} · rows={len(rows)}")

    t0 = time.monotonic()
    sleep_s = duration_s / stdout_lines
    for i in range(1, stdout_lines + 1):
        time.sleep(sleep_s)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _emit(sys.stdout, f"[heartbeat] tick {i}/{stdout_lines} · t={elapsed_ms}ms · rows={len(rows)}")
        if stderr_every > 0 and i % stderr_every == 0:
            _emit(sys.stderr, f"[heartbeat warn] tick {i}/{stdout_lines} hit stderr checkpoint at t={elapsed_ms}ms")

    output_path = run_dir / "output.csv"
    shutil.copyfile(input_path, output_path)
    _emit(sys.stdout, f"[heartbeat] done · wrote {output_path.name} · total={int((time.monotonic() - t0) * 1000)}ms")


if __name__ == "__main__":
    main()
