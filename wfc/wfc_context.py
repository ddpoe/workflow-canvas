"""wfc_context.py -- Helper for wfc-managed method scripts.

Zero-dependency RunContext: only imports os, json, pathlib.  No database,
models, or sqlmodel imports.  Method scripts can use RunContext in isolated
pixi environments without needing access to wfc internals.

The ``run-step`` command (ADR 008) handles all post-execution DB writes:
it reads ``metrics.json`` written by ``finalize()``, scans output files,
and creates RunOutput rows.

Usage:
    from wfc_context import RunContext

    ctx = RunContext()           # reads from env vars
    inputs = ctx.load_input()   # reads WFC_INPUT_PATHS → dict[str, list[Path]]
    ctx.save_module("feature_set_list", df, ext=".csv")
    ctx.save("qc_plot.png", fig)
    ctx.log_metrics({"n_features": 42})
    ctx.finalize()              # writes metrics.json to WFC_RUN_DIR
"""

import json
import os
from pathlib import Path


class RunContext:
    """Runtime context for a wfc-managed method script.

    Reads WFC_* environment variables set by the wfc engine (or
    ``run-step`` command) before launching the subprocess.  Provides
    convenience methods for loading inputs, saving outputs, recording
    metrics, and writing ``metrics.json`` that ``run-step`` reads
    after execution.

    Attributes:
        run_id: Integer run ID from the database.
        run_dir: Path to the run's archive directory (.runs/{id:08d}).
        sample: Sample identifier string.
        params: Dict of parameters for this run.
    """

    def __init__(self):
        self.run_id = int(os.environ["WFC_RUN_ID"])
        self.run_dir = Path(os.environ["WFC_RUN_DIR"])
        self.sample = os.environ.get("WFC_SAMPLE", "")
        self.params = json.loads(os.environ.get("WFC_PARAMS", "{}"))

        # Also try loading the richer context JSON if present
        ctx_file = self.run_dir / "_run_context.json"
        if ctx_file.exists():
            self._context = json.loads(ctx_file.read_text())
        else:
            self._context = {}

        self._module_outputs: dict[str, str] = {}
        self._method_outputs: dict[str, str] = {}
        self._metrics: dict[str, object] = {}

    def load_input(self):
        """Load input paths.

        Returns ``dict[str, list[Path]]`` — one key per input slot,
        each value a list of resolved file/directory paths.  Methods
        own their I/O: the slot type in method.yaml describes what's
        at the path, not how to deserialize it.

        Returns ``None`` when the node has no upstream input.
        """
        multi = os.environ.get("WFC_INPUT_PATHS")
        if not multi:
            return None

        slot_paths = json.loads(multi)
        result: dict[str, list[Path]] = {}
        for slot, paths in slot_paths.items():
            result[slot] = [Path(p) for p in paths]
        return result

    def save_module(self, name: str, data, ext: str):
        """Save a module-contract-required output.

        The file is written to ``WFC_RUN_DIR/{name}{ext}``.
        The engine validates these against module_contracts after execution.
        The ``run-step`` command creates ``RunOutput`` rows post-execution
        by scanning the run directory and matching against the contract.

        Args:
            name: Contract output name (e.g. 'feature_set_list').
            data: Data to write (DataFrame, figure, or bytes).
            ext: File extension (e.g. '.csv', '.parquet'). Required -- must match
                the filename declared in the pipeline's slot_outputs contract.
        """
        filename = f"{name}{ext}"
        path = self.run_dir / filename
        self._write(path, data)
        self._module_outputs[name] = filename

    def save(self, filename: str, data):
        """Save a free-form method output (not contract-validated).

        The ``run-step`` command creates ``RunOutput`` rows post-execution.

        Args:
            filename: Output filename (written under WFC_RUN_DIR).
            data: Data to write.
        """
        path = self.run_dir / filename
        self._write(path, data)
        self._method_outputs[filename] = filename

    def log_metrics(self, metrics: dict):
        """Record metrics. Can be called multiple times (merges)."""
        self._metrics.update(metrics)

    def finalize(self):
        """Write metrics.json to WFC_RUN_DIR. Must be called at end of script.

        The ``run-step`` command reads ``metrics.json`` after method execution
        and passes the metrics dict to ``complete_run()``.  Non-Python methods
        can write ``metrics.json`` directly without using RunContext.
        """
        metrics_path = self.run_dir / "metrics.json"
        metrics_path.write_text(json.dumps(self._metrics, default=str))

    def _write(self, path: Path, data) -> tuple[int, float]:
        """Dispatch writes by data type and file extension.

        Returns:
            ``(file_size, file_mtime)`` of the written file.
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        # pandas DataFrame
        try:
            import pandas as pd
            if isinstance(data, pd.DataFrame):
                ext = path.suffix.lower()
                if ext == ".csv":
                    data.to_csv(path, index=False)
                else:
                    data.to_parquet(path, index=False)
                stat = path.stat()
                return stat.st_size, stat.st_mtime
        except ImportError:
            pass

        # matplotlib figure
        try:
            import matplotlib.figure
            if isinstance(data, matplotlib.figure.Figure):
                data.savefig(path, dpi=150, bbox_inches="tight")
                stat = path.stat()
                return stat.st_size, stat.st_mtime
        except ImportError:
            pass

        # bytes
        if isinstance(data, bytes):
            path.write_bytes(data)
            stat = path.stat()
            return stat.st_size, stat.st_mtime

        # string
        if isinstance(data, str):
            path.write_text(data)
            stat = path.stat()
            return stat.st_size, stat.st_mtime

        # Path — method already wrote the file/dir; copy if needed, otherwise just stat
        if isinstance(data, Path):
            source = Path(data).resolve()
            if source != path.resolve():
                import shutil
                if source.is_dir():
                    if path.exists():
                        shutil.rmtree(path)
                    shutil.copytree(source, path)
                else:
                    shutil.copy2(source, path)
            stat = path.stat()
            return stat.st_size, stat.st_mtime

        raise TypeError(f"Don't know how to write {type(data).__name__} to {path}")

    @staticmethod
    def _infer_ext(data) -> str:
        """Infer file extension from data type."""
        try:
            import pandas as pd
            if isinstance(data, pd.DataFrame):
                return ".parquet"
        except ImportError:
            pass

        try:
            import matplotlib.figure
            if isinstance(data, matplotlib.figure.Figure):
                return ".png"
        except ImportError:
            pass

        if isinstance(data, bytes):
            return ".bin"
        if isinstance(data, str):
            return ".txt"

        return ".bin"
