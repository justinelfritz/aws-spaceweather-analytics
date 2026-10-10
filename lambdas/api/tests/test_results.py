import io
import json
from datetime import datetime, timedelta, timezone

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from moto import mock_aws

from lambdas.api import forecast_skill_results as forecast_skill_handler
from lambdas.api import sea_results as sea_handler

BUCKET = "curated-test"


@mock_aws
def test_sea_handler_passes_through_precomputed_result(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(sea_handler, "s3", s3)

    payload = {"field": "dst_index", "normalization": "raw", "offsets": []}
    s3.put_object(
        Bucket=BUCKET,
        Key="curated/sea_results/dst_index/raw.json",
        Body=json.dumps(payload).encode("utf-8"),
    )

    result = sea_handler.handler({"pathParameters": {"field": "dst_index", "normalization": "raw"}}, None)
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == payload


@mock_aws
def test_sea_handler_404s_when_not_yet_computed(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(sea_handler, "s3", s3)

    result = sea_handler.handler({"pathParameters": {"field": "kp", "normalization": "raw"}}, None)
    assert result["statusCode"] == 404


def test_sea_handler_rejects_unknown_field():
    result = sea_handler.handler({"pathParameters": {"field": "not_a_field", "normalization": "raw"}}, None)
    assert result["statusCode"] == 400


def test_sea_handler_rejects_unknown_normalization():
    result = sea_handler.handler({"pathParameters": {"field": "kp", "normalization": "bogus"}}, None)
    assert result["statusCode"] == 400


@mock_aws
def test_forecast_skill_handler_passes_through_precomputed_result(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(forecast_skill_handler, "s3", s3)

    payload = {"target": "kp", "error_type": "abs_error", "offsets": []}
    s3.put_object(
        Bucket=BUCKET,
        Key="curated/ml_forecast_skill/kp/abs_error.json",
        Body=json.dumps(payload).encode("utf-8"),
    )

    result = forecast_skill_handler.handler(
        {"pathParameters": {"target": "kp", "error_type": "abs_error"}}, None
    )
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == payload


def test_forecast_skill_handler_rejects_unknown_target():
    result = forecast_skill_handler.handler(
        {"pathParameters": {"target": "bogus", "error_type": "abs_error"}}, None
    )
    assert result["statusCode"] == 400


def test_forecast_skill_handler_rejects_unknown_error_type():
    result = forecast_skill_handler.handler(
        {"pathParameters": {"target": "kp", "error_type": "bogus"}}, None
    )
    assert result["statusCode"] == 400


# --- on-demand SEA (POST /sea/{field}/{normalization}) ---
# See docs/sea-on-demand-design.md. A caller-chosen subset of the DONKI
# catalog is computed synchronously; omitting event_ids (or selecting every
# known ID) takes the same instant precomputed-S3-JSON path a GET does.

def _write_catalog(s3, event_ids_and_times):
    catalog = [{"gst_id": event_id, "start_time": start_time.isoformat()} for event_id, start_time in event_ids_and_times]
    s3.put_object(
        Bucket=BUCKET,
        Key="curated/event_catalog/geomagnetic_storms.json",
        Body=json.dumps(catalog).encode("utf-8"),
    )


def _write_omniweb_year(s3, year: int, field: str, values_by_hour_of_year: dict):
    timestamps, values = [], []
    for hour_of_year, value in sorted(values_by_hour_of_year.items()):
        timestamps.append(datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(hours=hour_of_year))
        values.append(value)
    table = pa.table({"timestamp": timestamps, field: values})
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    s3.put_object(Bucket=BUCKET, Key=f"curated/omniweb_omni2_hourly/year={year}/data.parquet", Body=buffer.getvalue())


def _post_event(field="dst_index", normalization="raw", body=None):
    event = {
        "httpMethod": "POST",
        "pathParameters": {"field": field, "normalization": normalization},
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event


@mock_aws
def test_sea_post_without_event_ids_serves_precomputed_result(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(sea_handler, "s3", s3)

    payload = {"field": "dst_index", "normalization": "raw", "offsets": []}
    s3.put_object(
        Bucket=BUCKET,
        Key="curated/sea_results/dst_index/raw.json",
        Body=json.dumps(payload).encode("utf-8"),
    )

    result = sea_handler.handler(_post_event(body={}), None)
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == payload


@mock_aws
def test_sea_post_selecting_every_known_event_id_serves_precomputed_result(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(sea_handler, "s3", s3)

    _write_catalog(s3, [("evt-1", datetime(2024, 1, 1, tzinfo=timezone.utc))])
    payload = {"field": "dst_index", "normalization": "raw", "offsets": []}
    s3.put_object(
        Bucket=BUCKET,
        Key="curated/sea_results/dst_index/raw.json",
        Body=json.dumps(payload).encode("utf-8"),
    )

    result = sea_handler.handler(_post_event(body={"event_ids": ["evt-1"]}), None)
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == payload


@mock_aws
def test_sea_post_computes_fresh_result_for_a_true_subset(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(sea_handler, "s3", s3)

    onset = datetime(2024, 6, 1, 12, tzinfo=timezone.utc)
    _write_catalog(s3, [("evt-1", onset), ("evt-2", onset + timedelta(days=10))])
    # No precomputed result written -- a true subset must never hit the
    # fast path, so this would 404/raise if it accidentally did.
    onset_hour = int((onset - datetime(2024, 1, 1, tzinfo=timezone.utc)).total_seconds() // 3600)
    _write_omniweb_year(s3, 2024, "dst_index", {h: -1.0 * h for h in range(onset_hour - 24, onset_hour + 73)})

    result = sea_handler.handler(_post_event(body={"event_ids": ["evt-1"]}), None)
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["event_count"] == 1
    assert body["field"] == "dst_index"
    assert body["normalization"] == "raw"
    assert body["hours_before"] == 24
    assert body["hours_after"] == 72
    onset_offset = next(row for row in body["offsets"] if row["offset"] == 0)
    assert onset_offset["n"] == 1
    assert onset_offset["mean"] == -1.0 * onset_hour


@mock_aws
def test_sea_post_rejects_empty_event_ids(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(sea_handler, "s3", s3)

    result = sea_handler.handler(_post_event(body={"event_ids": []}), None)
    assert result["statusCode"] == 400


@mock_aws
def test_sea_post_rejects_unknown_event_id(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(sea_handler, "s3", s3)

    _write_catalog(s3, [("evt-1", datetime(2024, 1, 1, tzinfo=timezone.utc))])

    result = sea_handler.handler(_post_event(body={"event_ids": ["not-a-real-id"]}), None)
    assert result["statusCode"] == 400


def test_sea_post_rejects_non_list_event_ids(monkeypatch):
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    result = sea_handler.handler(_post_event(body={"event_ids": "evt-1"}), None)
    assert result["statusCode"] == 400


def test_sea_post_rejects_invalid_json_body(monkeypatch):
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    event = _post_event()
    event["body"] = "{not valid json"
    result = sea_handler.handler(event, None)
    assert result["statusCode"] == 400


def test_sea_post_rejects_unknown_field():
    result = sea_handler.handler(_post_event(field="not_a_field", body={}), None)
    assert result["statusCode"] == 400
