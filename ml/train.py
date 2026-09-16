#!/usr/bin/env python3
"""Trains and evaluates the Kp(t+3h) / Dst(t+3h) forecasting models.

Two things happen here, both required before anything fancier per the
platform to-do (section 6):
  1. A persistence baseline (predict no change from the current value) --
     the bar any real model has to clear to be worth deploying.
  2. A gradient-boosted tree model (sklearn HistGradientBoostingRegressor,
     one per target) trained on the engineered features from ml/features.py.

Split is chronological (never shuffled) since this is a time series --
training on 1995-2020 and evaluating on 2021-2025, per the EDA finding
that solar wind/IMF fields are consistently well-populated (<5% null)
only from 1995 onward (ml/eda/findings.md). 2026 is excluded from both
splits: it's the current, still-in-progress year, so OMNI2's provisional
processing lag leaves a large fraction of it null (see EDA).

Usage:
    python train.py --curated-bucket ... --profile sw-bootstrap
"""

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

from data import load_omniweb, make_s3_client
from features import DEFAULT_HORIZON_HOURS, build_feature_table, feature_column_names

DEFAULT_MODEL_DIR = Path(__file__).resolve().parent / "models"
EXPERIMENT_LOG_PATH = Path(__file__).resolve().parent / "experiments" / "log.jsonl"
DEFAULT_TRAIN_START_YEAR = 1995
DEFAULT_TRAIN_END_YEAR = 2020
DEFAULT_TEST_START_YEAR = 2021
DEFAULT_TEST_END_YEAR = 2025


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--curated-bucket", required=True)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--horizon-hours", type=int, default=DEFAULT_HORIZON_HOURS)
    parser.add_argument("--train-start-year", type=int, default=DEFAULT_TRAIN_START_YEAR)
    parser.add_argument("--train-end-year", type=int, default=DEFAULT_TRAIN_END_YEAR)
    parser.add_argument("--test-start-year", type=int, default=DEFAULT_TEST_START_YEAR)
    parser.add_argument("--test-end-year", type=int, default=DEFAULT_TEST_END_YEAR)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    return parser.parse_args()


def persistence_predictions(table: pd.DataFrame) -> dict[str, np.ndarray]:
    """The baseline: forecast = current value, i.e. assume no change over
    the horizon. `kp`/`dst_index` are the *current* (time t) columns --
    still present in the feature table alongside the future `*_target`
    columns build_feature_table adds."""
    return {"kp": table["kp"].to_numpy(), "dst_index": table["dst_index"].to_numpy()}


def evaluate(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    return {
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "mae": float(mean_absolute_error(y_true, y_pred)),
    }


def train_and_evaluate(train_table: pd.DataFrame, test_table: pd.DataFrame, feature_columns: list[str]) -> dict:
    x_train = train_table[feature_columns]
    x_test = test_table[feature_columns]

    results = {}
    models = {}
    for target_name, target_column in [("kp", "kp_target"), ("dst_index", "dst_target")]:
        baseline_pred = persistence_predictions(test_table)[target_name]
        baseline_metrics = evaluate(test_table[target_column].to_numpy(), baseline_pred)

        model = HistGradientBoostingRegressor(random_state=42)
        model.fit(x_train, train_table[target_column])
        model_pred = model.predict(x_test)
        model_metrics = evaluate(test_table[target_column].to_numpy(), model_pred)

        results[target_name] = {"baseline_persistence": baseline_metrics, "model_gradient_boosting": model_metrics}
        models[target_name] = model

    return {"metrics": results, "models": models}


def git_commit_hash() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True, cwd=Path(__file__).parent
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def log_experiment(s3, args, feature_count: int, train_rows: int, test_rows: int, metrics: dict) -> None:
    """Records one entry per run -- a deliberately lightweight alternative
    to SageMaker Experiments/MLflow for a project this size: plain JSON,
    diffable, queryable with jq, no extra service to run.

    Written to two places: locally to a git-tracked JSONL file (useful for
    runs during local development) and to S3 under the curated bucket
    (the durable copy -- the only one that survives a run inside an
    ephemeral SageMaker Training Job container)."""
    record = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit_hash(),
        "horizon_hours": args.horizon_hours,
        "train_years": [args.train_start_year, args.train_end_year],
        "test_years": [args.test_start_year, args.test_end_year],
        "train_rows": train_rows,
        "test_rows": test_rows,
        "feature_count": feature_count,
        "model": "HistGradientBoostingRegressor(random_state=42)",
        "metrics": metrics,
    }

    try:
        EXPERIMENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with EXPERIMENT_LOG_PATH.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as exc:
        print(f"Skipping local experiment log (not writable here): {exc}")

    s3.put_object(
        Bucket=args.curated_bucket,
        Key=f"curated/ml_experiments/{record['run_at']}.json",
        Body=json.dumps(record, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def main():
    args = parse_args()
    s3 = make_s3_client(args.profile)

    raw = load_omniweb(s3, args.curated_bucket, start_year=args.train_start_year, end_year=args.test_end_year)
    table = build_feature_table(raw, horizon_hours=args.horizon_hours)

    train_table = table[
        (table["timestamp"].dt.year >= args.train_start_year) & (table["timestamp"].dt.year <= args.train_end_year)
    ]
    test_table = table[
        (table["timestamp"].dt.year >= args.test_start_year) & (table["timestamp"].dt.year <= args.test_end_year)
    ]
    print(
        f"Train: {len(train_table):,} rows ({args.train_start_year}-{args.train_end_year}), "
        f"Test: {len(test_table):,} rows ({args.test_start_year}-{args.test_end_year})"
    )

    feature_columns = feature_column_names()
    outcome = train_and_evaluate(train_table, test_table, feature_columns)

    print(json.dumps(outcome["metrics"], indent=2))

    args.model_dir.mkdir(parents=True, exist_ok=True)
    for target_name, model in outcome["models"].items():
        joblib.dump(model, args.model_dir / f"{target_name}_gradient_boosting.joblib")
    metrics_path = args.model_dir / "metrics.json"
    metrics_path.write_text(
        json.dumps(
            {
                "horizon_hours": args.horizon_hours,
                "train_years": [args.train_start_year, args.train_end_year],
                "test_years": [args.test_start_year, args.test_end_year],
                "feature_count": len(feature_columns),
                "results": outcome["metrics"],
            },
            indent=2,
        )
    )
    log_experiment(s3, args, len(feature_columns), len(train_table), len(test_table), outcome["metrics"])
    print(f"Saved models and metrics to {args.model_dir}")
    print(f"Appended run to {EXPERIMENT_LOG_PATH}")


if __name__ == "__main__":
    main()
