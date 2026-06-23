"""faulty (RunContext) — intentional failure, logs metric via ctx before raising.

The RunContext pattern makes the "log one metric before crashing" story
clean: pre-finalize() flushes metrics to disk, then the crash happens,
and run-step still reads metrics.json.
"""
import os
import sys
import time
from pathlib import Path

import pandas as pd

from wfc.wfc_context import RunContext


def faulty(inputs, params):
    mode = str(params.get("failure_mode", "crash"))
    print(f"faulty: starting (failure_mode={mode!r})")

    if mode == "crash":
        print("faulty: about to raise RuntimeError", file=sys.stderr)
        raise RuntimeError("faulty: intentional crash (RunContext)")

    if mode == "streaming_fail":
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

    if mode == "missing_output":
        # Exit without writing the declared output file.
        return ({}, {"attempted": True, "phase": "missing_output"})

    # Success path: behave like transform.
    df = pd.read_csv(inputs["data"][0])
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    out_path = run_dir / "output.csv"
    df.to_csv(out_path, index=False)
    return (
        {"output": str(out_path)},
        {"attempted": True, "phase": "success", "rows": int(len(df))},
    )


if __name__ == "__main__":
    ctx = RunContext()
    # Eagerly flush a pre-failure metric so partial progress is observable
    # even when the method raises.
    ctx.log_metrics({"attempted": True, "phase": "pre_failure"})
    ctx.finalize()

    inputs = ctx.load_input()
    _outputs, metrics = faulty(inputs, ctx.params)
    ctx.log_metrics(metrics)
    ctx.finalize()
