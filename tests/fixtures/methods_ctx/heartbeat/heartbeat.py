"""heartbeat (RunContext) — emit timed stdout/stderr for streaming dogfood."""
import os
import sys
import time
from pathlib import Path

import pandas as pd

from wfc.wfc_context import RunContext


def _emit(stream, line: str) -> None:
    stream.write(line + "\n")
    stream.flush()


def heartbeat(inputs, params):
    duration_s = float(params.get("duration_s", 5.0))
    stdout_lines = max(1, int(params.get("stdout_lines", 15)))
    stderr_every = int(params.get("stderr_every", 4))

    df = pd.read_csv(inputs["data"][0])
    _emit(sys.stdout, f"[heartbeat] starting · duration={duration_s:.1f}s · ticks={stdout_lines} · rows={len(df)}")

    t0 = time.monotonic()
    sleep_s = duration_s / stdout_lines
    for i in range(1, stdout_lines + 1):
        time.sleep(sleep_s)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _emit(sys.stdout, f"[heartbeat] tick {i}/{stdout_lines} · t={elapsed_ms}ms · rows={len(df)}")
        if stderr_every > 0 and i % stderr_every == 0:
            _emit(sys.stderr, f"[heartbeat warn] tick {i}/{stdout_lines} hit stderr checkpoint at t={elapsed_ms}ms")

    run_dir = Path(os.environ["WFC_RUN_DIR"])
    out_path = run_dir / "output.csv"
    df.to_csv(out_path, index=False)

    _emit(sys.stdout, f"[heartbeat] done · wrote {out_path.name} · total={int((time.monotonic() - t0) * 1000)}ms")

    return (
        {"output": str(out_path)},
        {"rows": int(len(df)), "ticks_emitted": stdout_lines},
    )


if __name__ == "__main__":
    ctx = RunContext()
    inputs = ctx.load_input()
    _outputs, metrics = heartbeat(inputs, ctx.params)
    ctx.log_metrics(metrics)
    ctx.finalize()
