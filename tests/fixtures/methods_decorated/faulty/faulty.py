"""faulty (decorated) — intentional failure for error-handling demos.

Demonstrates that even in the decorated path, you can persist partial
progress before a crash by writing metrics.json directly to WFC_RUN_DIR.
The framework's finalize() would overwrite metrics.json on a successful
return, but when the function raises, this eager write is what survives.
"""
import json
import os
import sys
import time
from pathlib import Path

from wfc.method import wfc_method, wfc_method_main
import pandas as pd


@wfc_method
def faulty(inputs, params):
    mode = str(params.get("failure_mode", "crash"))
    print(f"faulty: starting (failure_mode={mode!r})")

    # Eager metric write — survives an exception because finalize() is never
    # reached on a crash. The decorator API has no pre-crash hook, so this is
    # the documented workaround (gap: wfc.method has no ctx.log_metrics access
    # inside the decorated function).
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    (run_dir / "metrics.json").write_text(json.dumps({
        "attempted": True,
        "phase": "pre_failure",
        "failure_mode": mode,
    }))

    if mode == "crash":
        print("faulty: about to raise RuntimeError", file=sys.stderr)
        raise RuntimeError("faulty: intentional crash (decorated)")

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
        # Return nothing: framework will raise ContractViolation on required output.
        return ({}, {"attempted": True, "phase": "missing_output"})

    # Success path: behave like transform.
    df = pd.read_csv(inputs["data"][0])
    return (
        {"output": df},
        {"attempted": True, "phase": "success", "rows": int(len(df))},
    )


if __name__ == "__main__":
    wfc_method_main()
