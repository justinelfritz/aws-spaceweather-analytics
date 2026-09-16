import io
import json
from datetime import datetime, timedelta, timezone

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from moto import mock_aws

from lambdas.api import historical as historical_handler

BUCKET = "curated-test"


def _write_year(s3, year: int, hours: list, kp: list, dst_index: list) -> None:
    base = datetime(year, 1, 1, tzinfo=timezone.utc)
    timestamps = [base + timedelta(hours=h) for h in hours]
    table = pa.table({"timestamp": timestamps, "kp": kp, "dst_index": dst_index})
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    s3.put_object(
        Bucket=BUCKET, Key=f"curated/omniweb_omni2_hourly/year={year}/data.parquet", Body=buffer.getvalue()
    )


def _query_params(**kwargs):
    return {"queryStringParameters": kwargs}


@mock_aws
def test_handler_filters_by_date_range_and_field(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(historical_handler, "s3", s3)

    _write_year(s3, 2024, hours=[0, 1, 2, 3, 4], kp=[1.0, 2.0, 3.0, 4.0, 5.0], dst_index=[-1, -2, -3, -4, -5])

    result = historical_handler.handler(
        _query_params(fields="kp", start="2024-01-01T01:00:00+00:00", end="2024-01-01T03:00:00+00:00"), None
    )

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["fields"] == ["kp"]
    assert body["count"] == 3
    assert [row["kp"] for row in body["data"]] == [2.0, 3.0, 4.0]
    assert "dst_index" not in body["data"][0]


@mock_aws
def test_handler_supports_multiple_fields(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(historical_handler, "s3", s3)

    _write_year(s3, 2024, hours=[0, 1], kp=[1.0, 2.0], dst_index=[-1, -2])

    result = historical_handler.handler(
        _query_params(fields="kp,dst_index", start="2024-01-01T00:00:00+00:00", end="2024-01-01T01:00:00+00:00"),
        None,
    )
    body = json.loads(result["body"])
    assert body["data"][0]["kp"] == 1.0
    assert body["data"][0]["dst_index"] == -1


@mock_aws
def test_handler_skips_missing_year_partitions(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(historical_handler, "s3", s3)

    result = historical_handler.handler(
        _query_params(fields="kp", start="2024-01-01T00:00:00+00:00", end="2024-01-01T02:00:00+00:00"), None
    )
    body = json.loads(result["body"])
    assert result["statusCode"] == 200
    assert body["count"] == 0


def test_handler_rejects_missing_fields_param():
    result = historical_handler.handler(_query_params(start="2024-01-01", end="2024-01-02"), None)
    assert result["statusCode"] == 400


def test_handler_rejects_unknown_field():
    result = historical_handler.handler(
        _query_params(fields="not_a_real_field", start="2024-01-01", end="2024-01-02"), None
    )
    assert result["statusCode"] == 400


def test_handler_rejects_bad_date():
    result = historical_handler.handler(_query_params(fields="kp", start="not-a-date", end="2024-01-02"), None)
    assert result["statusCode"] == 400


def test_handler_rejects_end_before_start():
    result = historical_handler.handler(_query_params(fields="kp", start="2024-01-02", end="2024-01-01"), None)
    assert result["statusCode"] == 400


def test_handler_rejects_range_too_large():
    result = historical_handler.handler(_query_params(fields="kp", start="2000-01-01", end="2024-01-01"), None)
    assert result["statusCode"] == 400
