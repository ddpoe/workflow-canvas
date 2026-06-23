"""heartbeat (decorated) — emit timed stdout/stderr for streaming dogfood.

Exercises the SSE log-stream endpoint + Builder Output tab from
`.superpowers/brainstorm/pipeline-output-visibility/plan.md`.
"""
import sys
import time

from wfc.method import wfc_method, wfc_method_main
import pandas as pd


def _emit(stream, line: str) -> None:
    stream.write(line + "\n")
    stream.flush()


@wfc_method
def heartbeat(inputs, params):
    duration_s = float(params.get("duration_s", 5.0))
    stdout_lines = max(1, int(params.get("stdout_lines", 15)))
    stderr_every = int(params.get("stderr_every", 4))
    fail_at_tick = int(params.get("fail_at_tick", 0))

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
        if fail_at_tick > 0 and i >= fail_at_tick:
            _emit(sys.stderr, f"[heartbeat] simulated failure at tick {i}/{stdout_lines}")
            raise RuntimeError(f"heartbeat simulated failure at tick {i} (fail_at_tick={fail_at_tick})")

    _emit(sys.stdout, f"[heartbeat] done · total={int((time.monotonic() - t0) * 1000)}ms")

    return (
        {"output": df},
        {"rows": int(len(df)), "ticks_emitted": stdout_lines},
    )


if __name__ == "__main__":
    wfc_method_main()
