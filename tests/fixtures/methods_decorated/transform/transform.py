"""transform (decorated) — add a computed column; also emit a per-row JSON log.

Demonstrates multi-output from a single @wfc_method: a DataFrame + a string
payload that the framework saves with the extension declared in method.yaml.
"""
import json

from wfc.method import wfc_method, wfc_method_main
import pandas as pd


@wfc_method
def transform(inputs, params):
    print(f"transform: reading {inputs['data'][0]}")
    df = pd.read_csv(inputs["data"][0])
    suffix = str(params.get("suffix", "_transformed"))
    new_col = f"computed{suffix}"
    print(f"transform: loaded {len(df)} rows; adding column '{new_col}'")
    df[new_col] = df["id"].astype(str).map(lambda x: f"v_{x}")
    print(f"transform: done ({len(df)} rows)")

    log = [{"id": row["id"], new_col: row[new_col]} for _, row in df.iterrows()]

    return (
        {"output": df, "transform_log": json.dumps(log, indent=2)},
        {"rows": int(len(df)), "suffix_used": suffix},
    )


if __name__ == "__main__":
    wfc_method_main()
