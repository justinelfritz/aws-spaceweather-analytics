#!/usr/bin/env python3
"""Exploratory data analysis over the curated OMNIWeb OMNI2 hourly dataset.

Loads every year's curated Parquet file from S3 into one DataFrame and
checks three things called out in the platform to-do (section 6):
gaps, outliers, and seasonal / solar-cycle effects. Prints a summary to
stdout and writes a few plots to ml/eda/plots/ for the write-up.

Usage:
    python eda.py --curated-bucket ... --profile sw-bootstrap
    python eda.py --curated-bucket ... --output-dir ./plots --profile sw-bootstrap
"""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from data import load_omniweb, make_s3_client

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "eda" / "plots"

# Rough physically-plausible ranges for a sanity check -- NOT used to drop
# or clip data, just to flag anything that survived fill-nulling but still
# looks implausible (which would indicate a parsing bug, not real weather).
PLAUSIBLE_RANGES = {
    "kp": (0.0, 9.0),
    "dst_index": (-700, 100),
    "plasma_speed": (200.0, 2000.0),
    "proton_density": (0.0, 150.0),
    "field_magnitude_avg": (0.0, 100.0),
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--curated-bucket", required=True)
    parser.add_argument("--prefix", default="curated/omniweb_omni2_hourly/")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--profile", default=None, help="AWS profile to use (default: default credential chain)")
    return parser.parse_args()


def report_gaps(df: pd.DataFrame) -> pd.Series:
    full_range = pd.date_range(df["timestamp"].min(), df["timestamp"].max(), freq="h")
    missing = full_range.difference(df["timestamp"])
    print(f"\n=== Gaps ===")
    print(f"Expected hourly timestamps: {len(full_range):,}")
    print(f"Present timestamps:         {len(df):,}")
    print(f"Missing timestamps:         {len(missing):,} ({len(missing) / len(full_range):.4%})")
    if len(missing):
        gap_years = missing.to_series().dt.year.value_counts().sort_index()
        print("Missing timestamps by year (top 10):")
        print(gap_years.sort_values(ascending=False).head(10).to_string())
    return missing.to_series()


def report_nulls(df: pd.DataFrame) -> None:
    print(f"\n=== Nulls (fill-value sentinels already converted to NaN) ===")
    null_pct = (df.isna().mean() * 100).sort_values(ascending=False)
    print(null_pct[null_pct > 0].round(2).to_string())


def report_outliers(df: pd.DataFrame) -> None:
    print(f"\n=== Outlier sanity check (physically-plausible range) ===")
    for column, (low, high) in PLAUSIBLE_RANGES.items():
        if column not in df:
            continue
        series = df[column].dropna()
        out_of_range = series[(series < low) | (series > high)]
        print(
            f"{column}: min={series.min():.2f} max={series.max():.2f} "
            f"out_of_range=[{low},{high}] -> {len(out_of_range)} rows"
        )


def plot_solar_cycle(df: pd.DataFrame, output_dir: Path) -> Path:
    yearly = df.set_index("timestamp").resample("YE").agg(
        sunspot_number_r=("sunspot_number_r", "mean"),
        kp=("kp", "mean"),
        dst_index=("dst_index", "mean"),
    )
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    ax1.plot(yearly.index.year, yearly["sunspot_number_r"], color="tab:orange")
    ax1.set_ylabel("Sunspot number (yearly mean)")
    ax1.set_title("Solar cycle vs. geomagnetic activity, 1963-2026")
    ax2.plot(yearly.index.year, yearly["kp"], label="Kp (yearly mean)", color="tab:blue")
    ax2b = ax2.twinx()
    ax2b.plot(yearly.index.year, yearly["dst_index"], label="Dst (yearly mean)", color="tab:red")
    ax2.set_ylabel("Kp", color="tab:blue")
    ax2b.set_ylabel("Dst (nT)", color="tab:red")
    ax2.set_xlabel("Year")
    fig.tight_layout()
    path = output_dir / "solar_cycle_vs_geomagnetic_activity.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_seasonal_climatology(df: pd.DataFrame, output_dir: Path) -> Path:
    monthly = df.groupby(df["timestamp"].dt.month).agg(
        kp=("kp", "mean"),
        dst_index=("dst_index", "mean"),
    )
    fig, ax1 = plt.subplots(figsize=(8, 5))
    ax1.plot(monthly.index, monthly["kp"], marker="o", color="tab:blue", label="Kp")
    ax1.set_xlabel("Month")
    ax1.set_ylabel("Kp (mean)", color="tab:blue")
    ax1.set_xticks(range(1, 13))
    ax2 = ax1.twinx()
    ax2.plot(monthly.index, monthly["dst_index"], marker="s", color="tab:red", label="Dst")
    ax2.set_ylabel("Dst (mean, nT)", color="tab:red")
    ax1.set_title("Semiannual (Russell-McPherron) seasonal effect, all years")
    fig.tight_layout()
    path = output_dir / "seasonal_climatology.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def main():
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    s3 = make_s3_client(args.profile)

    df = load_omniweb(s3, args.curated_bucket, args.prefix)
    print(f"Loaded {len(df):,} rows spanning {df['timestamp'].min()} to {df['timestamp'].max()}")

    report_gaps(df)
    report_nulls(df)
    report_outliers(df)

    solar_cycle_path = plot_solar_cycle(df, args.output_dir)
    seasonal_path = plot_seasonal_climatology(df, args.output_dir)
    print(f"\nSaved plots:\n  {solar_cycle_path}\n  {seasonal_path}")


if __name__ == "__main__":
    main()
