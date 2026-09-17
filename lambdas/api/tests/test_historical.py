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
    assert body["resolution"] == "hourly"
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
    # Comfortably beyond MAX_RANGE_DAYS (100 years) -- a 24-year range like
    # 2000-2024 is legitimate now (see the daily-aggregation tests below)
    # and must NOT be rejected.
    result = historical_handler.handler(_query_params(fields="kp", start="1000-01-01", end="2024-01-01"), None)
    assert result["statusCode"] == 400


@mock_aws
def test_handler_allows_multi_year_range(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(historical_handler, "s3", s3)

    result = historical_handler.handler(
        _query_params(fields="kp", start="2000-01-01", end="2024-01-01"), None
    )
    assert result["statusCode"] == 200


@mock_aws
def test_handler_resolution_boundary(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(historical_handler, "s3", s3)

    # 2023-01-01 -> 2023-04-01 is exactly 90 days (31 + 28 + 31, non-leap year).
    at_boundary = historical_handler.handler(
        _query_params(fields="kp", start="2023-01-01T00:00:00+00:00", end="2023-04-01T00:00:00+00:00"), None
    )
    assert json.loads(at_boundary["body"])["resolution"] == "hourly"

    past_boundary = historical_handler.handler(
        _query_params(fields="kp", start="2023-01-01T00:00:00+00:00", end="2023-04-01T01:00:00+00:00"), None
    )
    assert json.loads(past_boundary["body"])["resolution"] == "daily"


@mock_aws
def test_handler_aggregates_to_daily_mean_beyond_resolution_threshold(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(historical_handler, "s3", s3)

    # Hour 0-23 = day 1 (all kp=2.0), hour 24-47 = day 2 (all kp=4.0).
    hours = list(range(48))
    kp = [2.0] * 24 + [4.0] * 24
    dst_index = [-1] * 48
    _write_year(s3, 2024, hours=hours, kp=kp, dst_index=dst_index)

    result = historical_handler.handler(
        _query_params(fields="kp", start="2024-01-01T00:00:00+00:00", end="2024-06-01T00:00:00+00:00"), None
    )

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["resolution"] == "daily"
    assert body["count"] == 2
    assert body["data"][0]["timestamp"] == "2024-01-01T00:00:00+00:00"
    assert body["data"][0]["kp"] == 2.0
    assert body["data"][1]["timestamp"] == "2024-01-02T00:00:00+00:00"
    assert body["data"][1]["kp"] == 4.0


@mock_aws
def test_handler_daily_aggregation_nulls_out_days_with_no_readings(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(historical_handler, "s3", s3)

    _write_year(s3, 2024, hours=[0, 1, 2], kp=[None, None, None], dst_index=[-1, -2, -3])

    result = historical_handler.handler(
        _query_params(fields="kp,dst_index", start="2024-01-01T00:00:00+00:00", end="2024-06-01T00:00:00+00:00"),
        None,
    )

    body = json.loads(result["body"])
    assert body["resolution"] == "daily"
    assert body["data"][0]["kp"] is None
    assert body["data"][0]["dst_index"] == -2.0


@mock_aws
def test_handler_bx_gsm_and_bx_gse_both_read_the_single_bx_column(monkeypatch):
    # GSE and GSM share the same X-axis by definition, so OMNI2 only has one
    # physical Bx column (bx_gse_gsm) -- both queryable fields should read
    # it and return identical values.
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(historical_handler, "s3", s3)

    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    table = pa.table({"timestamp": [base, base + timedelta(hours=1)], "bx_gse_gsm": [1.5, -2.5]})
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    s3.put_object(Bucket=BUCKET, Key="curated/omniweb_omni2_hourly/year=2024/data.parquet", Body=buffer.getvalue())

    result = historical_handler.handler(
        _query_params(
            fields="bx_gsm,bx_gse", start="2024-01-01T00:00:00+00:00", end="2024-01-01T01:00:00+00:00"
        ),
        None,
    )

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert [row["bx_gsm"] for row in body["data"]] == [1.5, -2.5]
    assert [row["bx_gse"] for row in body["data"]] == [1.5, -2.5]
