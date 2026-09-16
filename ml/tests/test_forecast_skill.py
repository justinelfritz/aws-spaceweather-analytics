import json
from argparse import Namespace
from datetime import datetime, timedelta, timezone

import boto3
import joblib
import pyarrow as pa
import pyarrow.parquet as pq
from moto import mock_aws

from forecast_skill import build_test_predictions, error_series, result_key, run_backtest

BUCKET = "curated-test"
BASE_DATE = datetime(2021, 6, 1, tzinfo=timezone.utc)
N_HOURS = 90
HORIZON = 3
HOURS_BEFORE = 2
HOURS_AFTER = 2
EVENT_ANCHORS = [40, 70]


class ConstantPredictor:
    """Ignores its input and always predicts the same value -- makes the
    backtest's error values a pure function of the known target signature,
    so the expected aggregate at each offset can be computed by hand."""

    def __init__(self, value: float):
        self.value = value

    def predict(self, x):
        return [self.value] * len(x)


def _kp_signature(offset: int) -> float:
    return 10.0 * offset


def _dst_signature(offset: int) -> float:
    return 50.0 * offset


def _write_synthetic_data(s3):
    timestamps = [BASE_DATE + timedelta(hours=h) for h in range(N_HOURS)]
    kp = [0.0] * N_HOURS
    dst_index = [0.0] * N_HOURS
    for anchor in EVENT_ANCHORS:
        for offset in range(-HOURS_BEFORE, HOURS_AFTER + 1):
            target_hour = anchor + offset + HORIZON
            kp[target_hour] = _kp_signature(offset)
            dst_index[target_hour] = _dst_signature(offset)

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

    catalog = [
        {"gst_id": f"evt-{i}", "start_time": (BASE_DATE + timedelta(hours=anchor)).isoformat()}
        for i, anchor in enumerate(EVENT_ANCHORS)
    ]
    s3.put_object(
        Bucket=BUCKET,
        Key="curated/event_catalog/geomagnetic_storms.json",
        Body=json.dumps(catalog).encode("utf-8"),
    )


def test_result_key_is_namespaced_by_target_and_error_type():
    assert result_key("kp", "signed_error") == "curated/ml_forecast_skill/kp/signed_error.json"
    assert result_key("dst_index", "abs_error") == "curated/ml_forecast_skill/dst_index/abs_error.json"


def test_error_series_signed_and_absolute():
    import pandas as pd

    predictions = pd.DataFrame(
        {"timestamp": [1, 2, 3], "kp_error": [-2.0, 0.0, 3.0]},
    )
    signed = error_series(predictions, "kp_error", absolute=False)
    absolute = error_series(predictions, "kp_error", absolute=True)
    assert signed == {1: -2.0, 2: 0.0, 3: 3.0}
    assert absolute == {1: 2.0, 2: 0.0, 3: 3.0}


@mock_aws
def test_run_backtest_matches_hand_computed_offset_pattern(tmp_path):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    _write_synthetic_data(s3)

    kp_constant, dst_constant = 5.0, 20.0
    joblib.dump(ConstantPredictor(kp_constant), tmp_path / "kp_gradient_boosting.joblib")
    joblib.dump(ConstantPredictor(dst_constant), tmp_path / "dst_index_gradient_boosting.joblib")

    args = Namespace(
        curated_bucket=BUCKET,
        model_dir=tmp_path,
        horizon_hours=HORIZON,
        test_start_year=2021,
        test_end_year=2021,
        hours_before=HOURS_BEFORE,
        hours_after=HOURS_AFTER,
    )

    predictions = build_test_predictions(s3, args)
    results = run_backtest(s3, args, predictions)

    kp_signed = results[("kp", "signed_error")]
    kp_abs = results[("kp", "abs_error")]
    dst_signed = results[("dst_index", "signed_error")]

    assert kp_signed["event_count"] == 2
    assert [row["offset"] for row in kp_signed["offsets"]] == [-2, -1, 0, 1, 2]

    # Both events share the identical relative signature, so every offset
    # should have exactly 2 contributing events with a known error value.
    for row in kp_signed["offsets"]:
        expected = kp_constant - _kp_signature(row["offset"])
        assert row["n"] == 2
        assert row["mean"] == expected
        assert row["median"] == expected

    for row in kp_abs["offsets"]:
        assert row["mean"] == abs(kp_constant - _kp_signature(row["offset"]))

    for row in dst_signed["offsets"]:
        assert row["mean"] == dst_constant - _dst_signature(row["offset"])
