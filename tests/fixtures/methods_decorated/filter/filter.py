"""filter (decorated) — keep rows where value >= threshold."""
from wfc.method import wfc_method, wfc_method_main
import pandas as pd


@wfc_method
def filter(inputs, params):
    print(f"filter: reading {inputs['data'][0]}")
    df = pd.read_csv(inputs["data"][0])
    threshold = float(params.get("threshold", 10.0))
    drop_na = bool(params.get("drop_na", True))
    print(f"filter: loaded {len(df)} rows; threshold={threshold} drop_na={drop_na}")

    values = pd.to_numeric(df.get("value"), errors="coerce")
    if drop_na:
        mask = values.notna() & (values >= threshold)
    else:
        mask = values.isna() | (values >= threshold)
    kept = df[mask].reset_index(drop=True)
    print(f"filter: kept {len(kept)}/{len(df)} rows")

    return (
        {"output": kept},
        {
            "rows_in": int(len(df)),
            "rows_out": int(len(kept)),
            "threshold": float(threshold),
        },
    )


if __name__ == "__main__":
    wfc_method_main()
