"""Fixture method: echo_params -- writes the WFC_PARAMS it read to output.json.

Reads WFC_RUN_DIR, WFC_INPUT_PATHS and WFC_PARAMS from the environment and
writes the parsed WFC_PARAMS to <run_dir>/output.json, so a test can compare
what the method ran with against the run row and the frozen document.
"""

import json
import os
from pathlib import Path


def main():
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    data_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{}")).get("data", [])
    if not data_paths or not Path(data_paths[0]).exists():
        raise FileNotFoundError(f"Input file not found: {data_paths}")
    params = json.loads(os.environ["WFC_PARAMS"])
    (run_dir / "output.json").write_text(json.dumps(params), encoding="utf-8")
    print(f"echo_params: wrote {sorted(params)} to output.json")


if __name__ == "__main__":
    main()
