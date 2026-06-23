"""merge (decorated) — fan-in concat of multiple CSVs.

Demonstrates the @wfc_method happy path:
  - Framework handles I/O: return a DataFrame for the 'merged' slot, framework saves it.
  - Framework validates outputs against module_contracts after return.
"""
from wfc.method import wfc_method, wfc_method_main
import pandas as pd


@wfc_method
def merge(inputs, params):
    paths = inputs["sources"]
    frames = [pd.read_csv(p) for p in paths]
    df = pd.concat(frames, ignore_index=True)
    return (
        {"merged": df},
        {
            "rows_in": int(len(df)),
            "sources_count": int(len(paths)),
        },
    )


if __name__ == "__main__":
    wfc_method_main()
