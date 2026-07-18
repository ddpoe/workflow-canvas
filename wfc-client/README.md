# wfc-client

Pure-stdlib Tier-1 sugar for writing [Workflow Canvas](https://github.com/ddpoe/workflow-canvas) (`wfc`) methods.

`wfc-client` is the *opt-in ergonomic* way to write a wfc method. The
canonical interface is the Tier-2 env-var + file contract — `wfc-client`
is a thin, zero-dependency wrapper over it. It never installs `wfc`,
pandas, or any third-party package into your environment, and it never
copies, reads, or serializes your data bytes: it is a metadata recorder.

```python
import wfc_client as wfc

@wfc.method
def qc(ctx):
    clean_path = ctx.workdir / "clean.csv"
    # ... write your file with whatever library you like ...
    ctx.save_artifact("clean", clean_path)
    ctx.log_metric("kept_rows", 100)

if __name__ == "__main__":
    wfc.run()
```

## The `ctx` surface

| Member | Purpose |
|---|---|
| `ctx.input(slot)` | Resolved input paths (`list[Path]`) for an input slot. |
| `ctx.params` | Parsed params dict. |
| `ctx.workdir` | Scratch dir at `WFC_RUN_DIR/_workdir/` (auto-created). |
| `ctx.run_dir` | `WFC_RUN_DIR` (advanced). |
| `ctx.save_artifact(name, path)` | Record that `path` is the declared output `name`. Path only. |
| `ctx.log_metric(name, value)` | Record a scalar metric. |

At `wfc.run()` exit, `wfc-client` writes one `_wfc_results.json` manifest
of `{outputs, metrics}` with run-dir-relative paths. The host reads it.

Exactly one `@wfc.method` per method module is required.
