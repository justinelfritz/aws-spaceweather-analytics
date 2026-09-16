#!/usr/bin/env python3
"""Plots the forecast-skill backtest results (ml/forecast_skill.py) that
are already sitting in S3 -- one figure per target, showing mean absolute
and signed error by hours-since-storm-onset.

Usage:
    python plot_forecast_skill.py --curated-bucket ... --profile sw-bootstrap
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from data import make_s3_client

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "forecast_skill" / "plots"
TARGET_LABELS = {"kp": "Kp", "dst_index": "Dst (nT)"}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated-bucket", required=True)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def load_result(s3, bucket: str, target: str, error_type: str) -> dict:
    obj = s3.get_object(Bucket=bucket, Key=f"curated/ml_forecast_skill/{target}/{error_type}.json")
    return json.loads(obj["Body"].read())


def plot_target(s3, bucket: str, target: str, output_dir: Path) -> Path:
    abs_result = load_result(s3, bucket, target, "abs_error")
    signed_result = load_result(s3, bucket, target, "signed_error")

    offsets = [row["offset"] for row in abs_result["offsets"]]
    abs_mean = [row["mean"] for row in abs_result["offsets"]]
    signed_mean = [row["mean"] for row in signed_result["offsets"]]

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.axhline(0, color="black", linewidth=0.8)
    ax.axvline(0, color="gray", linestyle="--", linewidth=1, label="storm onset")
    ax.plot(offsets, abs_mean, color="tab:red", label="mean |error| (magnitude)")
    ax.plot(offsets, signed_mean, color="tab:blue", label="mean error (bias)")
    ax.set_xlabel("Hours relative to storm onset")
    ax.set_ylabel(f"{TARGET_LABELS[target]} forecast error (predicted - actual)")
    ax.set_title(
        f"{TARGET_LABELS[target]}(t+{abs_result['horizon_hours']}h) forecast skill by storm phase\n"
        f"({abs_result['event_count']} held-out storms, {abs_result.get('test_years', '2021-2025')})"
    )
    ax.legend()
    fig.tight_layout()

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{target}_forecast_skill.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def main():
    args = parse_args()
    s3 = make_s3_client(args.profile)
    for target in ["kp", "dst_index"]:
        path = plot_target(s3, args.curated_bucket, target, args.output_dir)
        print(f"Saved {path}")


if __name__ == "__main__":
    main()
