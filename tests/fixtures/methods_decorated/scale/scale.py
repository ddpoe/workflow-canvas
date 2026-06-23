"""scale (decorated) — multiply 'value' column by factor, round to precision."""
from wfc.method import wfc_method, wfc_method_main
import pandas as pd


@wfc_method
def scale(inputs, params):
    print(f"scale: reading {inputs['data'][0]}")
    df = pd.read_csv(inputs["data"][0])
    factor = float(params.get("factor", 1.0))
    precision = int(params.get("precision", 2))
    print(f"scale: loaded {len(df)} rows; applying factor={factor} precision={precision}")

    values = pd.to_numeric(df.get("value"), errors="coerce")
    scaled = (values * factor).round(precision)
    df["value"] = scaled
    print(f"scale: scaled {len(scaled.dropna())} values")

    non_na = scaled.dropna()
    return (
        {"output": df},
        {
            "factor": float(factor),
            "min_scaled": float(non_na.min()) if len(non_na) else 0.0,
            "max_scaled": float(non_na.max()) if len(non_na) else 0.0,
        },
    )


if __name__ == "__main__":
    wfc_method_main()
