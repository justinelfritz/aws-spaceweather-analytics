import json
from argparse import Namespace
from datetime import datetime, timedelta, timezone

import boto3
import joblib
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from moto import mock_aws

from classification_skill import (
    CLASSIFIABLE_TARGETS,
    STORM_COMPARISONS,
    classification_metrics,
    result_key,
    run_classification_skill,
)
from forecast_skill import build_test_predictions

BUCKET = "curated-test"
BASE_DATE = datetime(2021, 1, 1, tzinfo=timezone.utc)
N_HOURS = 60  # long enough to survive build_feature_table's 24h rolling-window warmup drop
HORIZON = 3


class ConstantPredictor:
    def __init__(self, value: float):
        self.value = value

    def predict(self, x):
        return [self.value] * len(x)


def test_result_key_is_one_file_per_target():
    assert result_key("kp") == "curated/ml_classification_skill/kp.json"
    assert result_key("dst_index") == "curated/ml_classification_skill/dst_index.json"


def test_storm_comparisons_handle_kp_and_dst_sign_directions():
    import pandas as pd

    series = pd.Series([3.0, 5.0, 7.0])
    assert list(STORM_COMPARISONS["kp"](series, 5.0)) == [False, True, True]

    dst_series = pd.Series([-10.0, -50.0, -100.0])
    assert list(STORM_COMPARISONS["dst_index"](dst_series, -50.0)) == [False, True, True]


def test_classification_metrics_matches_hand_computed_confusion_matrix():
    # 1 TP, 1 FP, 1 TN, 1 FN
    y_true = np.array([True, False, False, True])
    y_pred = np.array([True, True, False, False])

    result = classification_metrics(y_true, y_pred)

    assert result["confusion_matrix"] == {"tp": 1, "fp": 1, "tn": 1, "fn": 1}
    assert result["precision"] == 0.5  # tp / (tp + fp)
    assert result["recall"] == 0.5  # tp / (tp + fn)
    assert result["f1"] == 0.5
    assert result["false_alarm_rate"] == 0.5  # fp / (fp + tn)


def test_classification_metrics_handles_no_positives_without_dividing_by_zero():
    y_true = np.array([False, False, False])
    y_pred = np.array([False, False, False])

    result = classification_metrics(y_true, y_pred)

    assert result["confusion_matrix"] == {"tp": 0, "fp": 0, "tn": 3, "fn": 0}
    assert result["precision"] == 0.0
    assert result["recall"] == 0.0
    assert result["false_alarm_rate"] == 0.0


@mock_aws
def test_run_classification_skill_end_to_end(tmp_path):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)

    timestamps = [BASE_DATE + timedelta(hours=h) for h in range(N_HOURS)]
    # Storm every 4th hour (kp=6>=5, dst=-60<=-50), quiet otherwise.
    kp = [6.0 if h % 4 == 0 else 1.0 for h in range(N_HOURS)]
    dst_index = [-60.0 if h % 4 == 0 else 1.0 for h in range(N_HOURS)]

    table = pa.table(
        {
            "timestamp": timestamps,
            "plasma_speed": [1.0] * N_HOURS,
            "proton_density": [1.0] * N_HOURS,
            "field_magnitude_avg": [1.0] * N_HOURS,
            "bz_gsm": [1.0] * N_HOURS,
            "by_gsm": [1.0] * N_HOURS,
            "kp": kp,
            "dst_index": dst_index,
            "ae_index": [1.0] * N_HOURS,
        }
    )
    import io

    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    s3.put_object(Bucket=BUCKET, Key="curated/omniweb_omni2_hourly/year=2021/data.parquet", Body=buffer.getvalue())

    # A model that always predicts "quiet" -- every actual storm is a
    # false negative, nothing is ever a true/false positive.
    joblib.dump(ConstantPredictor(1.0), tmp_path / "kp_gradient_boosting.joblib")
    joblib.dump(ConstantPredictor(1.0), tmp_path / "dst_index_gradient_boosting.joblib")
    joblib.dump(ConstantPredictor(1.0), tmp_path / "ae_index_gradient_boosting.joblib")

    args = Namespace(
        curated_bucket=BUCKET,
        model_dir=tmp_path,
        horizon_hours=HORIZON,
        test_start_year=2021,
        test_end_year=2021,
        kp_storm_threshold=5.0,
        dst_storm_threshold=-50.0,
    )

    predictions = build_test_predictions(s3, args)
    thresholds = {"kp": args.kp_storm_threshold, "dst_index": args.dst_storm_threshold}
    results = run_classification_skill(predictions, thresholds, {"git_commit": "abc123", "train_years": [1995, 2020]}, args)

    assert set(results) == {name for name, _ in CLASSIFIABLE_TARGETS}

    kp_result = results["kp"]
    assert kp_result["storm_definition"] == "kp >= 5.0"
    assert kp_result["confusion_matrix"]["tp"] == 0
    assert kp_result["confusion_matrix"]["fp"] == 0
    assert kp_result["confusion_matrix"]["fn"] > 0
    assert kp_result["recall"] == 0.0

    dst_result = results["dst_index"]
    assert dst_result["storm_definition"] == "dst_index <= -50.0"
    assert dst_result["confusion_matrix"]["fn"] > 0
