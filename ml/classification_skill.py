#!/usr/bin/env python3
"""Storm/no-storm classification skill: thresholds the already-trained
Kp(t+3h)/Dst(t+3h) regression models' predictions into a binary storm call
and scores it as a confusion matrix -- the classifier "available for free
by thresholding" space-weather-platform-todo.md section 6 already called
out, not a separate model.

Computed pointwise over every hourly row in the held-out test period, NOT
epoch-aligned on storm onset like forecast_skill.py's backtest:
precision/recall/F1 need a large, representative contingency table
(including quiet-time true negatives) to mean anything; pooling down to
per-offset bins as small as a dozen events (see forecast_skill.py's
SEVERITY_BANDS) would make them statistically meaningless. Deliberately a
"basic" classification check, not a full ROC/threshold-sweep analysis --
and deliberately doesn't compose with severity bands for the same reason.

ae_index is excluded: unlike Kp (NOAA's own G1 definition is Kp>=5) and
Dst (a widely used literature cutoff, see DEFAULT_DST_STORM_THRESHOLD
below), there's no established "storm" threshold for AE.

Usage:
    python classification_skill.py --curated-bucket ... --profile sw-bootstrap
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score

from data import make_s3_client
from forecast_skill import DEFAULT_HORIZON_HOURS, DEFAULT_MODEL_DIR, build_test_predictions, load_model_version
from train import DEFAULT_TEST_END_YEAR, DEFAULT_TEST_START_YEAR

# Kp>=5 is NOAA's own G1 ("minor storm") definition. Dst has no official
# NOAA G-scale cutoff -- -50nT is the commonly cited literature threshold
# for a moderate storm (Gonzalez et al. 1994). Both are CLI-overridable
# below rather than fixed, since a different cutoff is a legitimate thing
# to want for a one-off run.
DEFAULT_KP_STORM_THRESHOLD = 5.0
DEFAULT_DST_STORM_THRESHOLD = -50.0

CLASSIFIABLE_TARGETS = [("kp", "kp_target"), ("dst_index", "dst_target")]

# Dst is negative for storms (more negative = more intense), Kp is
# positive (higher = more intense) -- the "is this a storm" comparison
# points opposite directions for the two targets.
STORM_COMPARISONS = {
    "kp": lambda series, threshold: series >= threshold,
    "dst_index": lambda series, threshold: series <= threshold,
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--curated-bucket", required=True)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--horizon-hours", type=int, default=DEFAULT_HORIZON_HOURS)
    parser.add_argument("--test-start-year", type=int, default=DEFAULT_TEST_START_YEAR)
    parser.add_argument("--test-end-year", type=int, default=DEFAULT_TEST_END_YEAR)
    parser.add_argument("--kp-storm-threshold", type=float, default=DEFAULT_KP_STORM_THRESHOLD)
    parser.add_argument("--dst-storm-threshold", type=float, default=DEFAULT_DST_STORM_THRESHOLD)
    return parser.parse_args()


def classification_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[False, True]).ravel()
    return {
        "confusion_matrix": {"tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn)},
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "false_alarm_rate": float(fp / (fp + tn)) if (fp + tn) > 0 else None,
    }


def run_classification_skill(predictions, thresholds: dict, model_version: dict, args) -> dict:
    results = {}
    for target_name, target_column in CLASSIFIABLE_TARGETS:
        threshold = thresholds[target_name]
        compare = STORM_COMPARISONS[target_name]
        y_true = compare(predictions[target_column], threshold).to_numpy()
        y_pred = compare(predictions[f"{target_name}_predicted"], threshold).to_numpy()
        op = ">=" if target_name == "kp" else "<="

        results[target_name] = {
            "target": target_name,
            "threshold": threshold,
            "storm_definition": f"{target_name} {op} {threshold}",
            "horizon_hours": args.horizon_hours,
            "row_count": int(len(predictions)),
            "positive_rate_actual": float(y_true.mean()),
            "positive_rate_predicted": float(y_pred.mean()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model_version": model_version,
            **classification_metrics(y_true, y_pred),
        }
    return results


def result_key(target: str) -> str:
    return f"curated/ml_classification_skill/{target}.json"


def main():
    args = parse_args()
    s3 = make_s3_client(args.profile)

    predictions = build_test_predictions(s3, args)
    print(f"Held-out predictions: {len(predictions):,} rows ({args.test_start_year}-{args.test_end_year})")

    thresholds = {"kp": args.kp_storm_threshold, "dst_index": args.dst_storm_threshold}
    model_version = load_model_version(args.model_dir)
    results = run_classification_skill(predictions, thresholds, model_version, args)

    for target_name, result in results.items():
        key = result_key(target_name)
        s3.put_object(
            Bucket=args.curated_bucket,
            Key=key,
            Body=json.dumps(result, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        print(
            f"{target_name}: precision={result['precision']:.3f} recall={result['recall']:.3f} "
            f"f1={result['f1']:.3f} far={result['false_alarm_rate']:.3f} -> s3://{args.curated_bucket}/{key}"
        )


if __name__ == "__main__":
    main()
