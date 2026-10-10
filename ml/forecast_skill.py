#!/usr/bin/env python3
"""Forecast-skill backtest: superposed epoch analysis of the Kp(t+3h)/
Dst(t+3h) model's *prediction error*, aligned on the same DONKI geomagnetic
storm catalog SEA uses (section 5) -- answers "how does forecast skill
evolve through a typical storm" (e.g. is the model worse right at onset,
or during the slow recovery tail) rather than "what's the average error."

Reuses SEA's alignment/aggregation code as-is (sea-lib, see
ml/requirements.txt) with a different input series: instead of aligning a
raw OMNIWeb field, this aligns the model's per-timestamp error. Restricted
to the test period only (test_start_year..test_end_year) so every event
analyzed is one the model never saw during training -- a real backtest,
not an in-sample fit check.

This deliberately replaces the "live current forecast" framing floated
earlier in section 6: the dashboard doesn't show real-time data, so a
forecast that only means something when continuously recomputed from live
conditions would be the live-readings view again by another name (and
would just duplicate NOAA SWPC's own published Kp forecast). Backtesting
against known historical storms is the framing that's actually consistent
with the rest of the dashboard (SEA, historical explorer): retrospective
analysis of known events, not a live feed.

Usage:
    python forecast_skill.py --curated-bucket ... --profile sw-bootstrap
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from aggregation import aggregate_events
from alignment import align_events
from load_data import load_event_catalog
from normalization import normalize_raw

from data import load_omniweb, make_s3_client
from features import build_feature_table, feature_column_names
from train import DEFAULT_TEST_END_YEAR, DEFAULT_TEST_START_YEAR, git_commit_hash

DEFAULT_MODEL_DIR = Path(__file__).resolve().parent / "models"
DEFAULT_HOURS_BEFORE = 24
DEFAULT_HOURS_AFTER = 72
DEFAULT_HORIZON_HOURS = 3
TARGETS = [("kp", "kp_target"), ("dst_index", "dst_target")]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--curated-bucket", required=True)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--horizon-hours", type=int, default=DEFAULT_HORIZON_HOURS)
    parser.add_argument("--test-start-year", type=int, default=DEFAULT_TEST_START_YEAR)
    parser.add_argument("--test-end-year", type=int, default=DEFAULT_TEST_END_YEAR)
    parser.add_argument("--hours-before", type=int, default=DEFAULT_HOURS_BEFORE)
    parser.add_argument("--hours-after", type=int, default=DEFAULT_HOURS_AFTER)
    return parser.parse_args()


def build_test_predictions(s3, args) -> pd.DataFrame:
    """Held-out predictions for the test period only, with a little margin
    on either side: earlier years so the first test rows still have full
    lag/rolling history, and a year past test-end so events near the end
    of the test period still get a full post-onset window."""
    raw = load_omniweb(s3, args.curated_bucket, start_year=args.test_start_year - 1, end_year=args.test_end_year + 1)
    table = build_feature_table(raw, horizon_hours=args.horizon_hours)
    test_table = table[
        (table["timestamp"].dt.year >= args.test_start_year) & (table["timestamp"].dt.year <= args.test_end_year)
    ].reset_index(drop=True)

    feature_columns = feature_column_names()
    x_test = test_table[feature_columns]
    for target_name, target_column in TARGETS:
        model = joblib.load(args.model_dir / f"{target_name}_gradient_boosting.joblib")
        test_table[f"{target_name}_predicted"] = model.predict(x_test)
        test_table[f"{target_name}_error"] = test_table[f"{target_name}_predicted"] - test_table[target_column]
    return test_table


def load_model_version(model_dir: Path) -> dict:
    """Traceability metadata for whichever model artifacts produced these
    predictions: which training run (git commit, training data window)
    they came from, read from the metrics.json ml/train.py writes
    alongside the joblib files -- so a backtest result can always be
    traced back to the exact model version and data window that made it,
    without re-deriving that from the (mutable, git-ignored) model files."""
    metrics_path = model_dir / "metrics.json"
    if not metrics_path.exists():
        return {"git_commit": git_commit_hash(), "train_years": None}
    metrics = json.loads(metrics_path.read_text())
    return {"git_commit": git_commit_hash(), "train_years": metrics.get("train_years")}


def error_series(predictions: pd.DataFrame, column: str, absolute: bool) -> dict:
    values = predictions[column].abs() if absolute else predictions[column]
    return dict(zip(predictions["timestamp"], values))


def run_backtest(s3, args, predictions: pd.DataFrame) -> dict:
    min_start = datetime(args.test_start_year, 1, 1, tzinfo=timezone.utc)
    max_start = datetime(args.test_end_year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    events = load_event_catalog(s3, args.curated_bucket, min_start_time=min_start, max_start_time=max_start)
    print(f"Backtesting against {len(events)} held-out storms ({args.test_start_year}-{args.test_end_year})")
    model_version = load_model_version(args.model_dir)

    results = {}
    for target_name, _target_column in TARGETS:
        for error_type, absolute in [("signed_error", False), ("abs_error", True)]:
            series = error_series(predictions, f"{target_name}_error", absolute=absolute)
            offsets, aligned = align_events(series, events, hours_before=args.hours_before, hours_after=args.hours_after)
            normalized = [normalize_raw(offsets, event) for event in aligned]
            aggregated = aggregate_events(offsets, normalized)

            results[(target_name, error_type)] = {
                "target": target_name,
                "error_type": error_type,
                "horizon_hours": args.horizon_hours,
                "hours_before": args.hours_before,
                "hours_after": args.hours_after,
                "event_count": len(events),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "model_version": model_version,
                "offsets": [
                    {
                        "offset": row.offset,
                        "n": row.n,
                        "mean": row.mean,
                        "median": row.median,
                        "percentiles": {str(pct): value for pct, value in row.percentiles.items()},
                    }
                    for row in aggregated
                ],
            }
    return results


def result_key(target: str, error_type: str) -> str:
    return f"curated/ml_forecast_skill/{target}/{error_type}.json"


def main():
    args = parse_args()
    s3 = make_s3_client(args.profile)

    predictions = build_test_predictions(s3, args)
    print(f"Held-out predictions: {len(predictions):,} rows ({args.test_start_year}-{args.test_end_year})")

    results = run_backtest(s3, args, predictions)
    for (target_name, error_type), result in results.items():
        key = result_key(target_name, error_type)
        s3.put_object(
            Bucket=args.curated_bucket,
            Key=key,
            Body=json.dumps(result, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        onset_row = next(row for row in result["offsets"] if row["offset"] == 0)
        print(
            f"{target_name}/{error_type}: onset (offset=0) mean={onset_row['mean']:.3f} n={onset_row['n']} "
            f"-> s3://{args.curated_bucket}/{key}"
        )


if __name__ == "__main__":
    main()
