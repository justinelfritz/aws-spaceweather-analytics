import json

import boto3
from moto import mock_aws

from lambdas.api import events as events_handler

BUCKET = "curated-test"

CATALOG = [
    {"gst_id": "evt-1", "start_time": "2024-01-01T00:00:00+00:00", "max_kp": 6.0, "storm_class": "G2"},
    {"gst_id": "evt-2", "start_time": "2024-06-15T00:00:00+00:00", "max_kp": 8.0, "storm_class": "G4"},
    {"gst_id": "evt-3", "start_time": "2025-01-01T00:00:00+00:00", "max_kp": 5.0, "storm_class": "G1"},
]


def _write_catalog(s3):
    s3.put_object(
        Bucket=BUCKET,
        Key="curated/event_catalog/geomagnetic_storms.json",
        Body=json.dumps(CATALOG).encode("utf-8"),
    )


@mock_aws
def test_handler_returns_full_catalog_with_no_filters(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(events_handler, "s3", s3)
    _write_catalog(s3)

    result = events_handler.handler({}, None)
    body = json.loads(result["body"])
    assert result["statusCode"] == 200
    assert body["count"] == 3


@mock_aws
def test_handler_filters_by_start_and_end(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(events_handler, "s3", s3)
    _write_catalog(s3)

    result = events_handler.handler(
        {"queryStringParameters": {"start": "2024-01-01", "end": "2024-12-31"}}, None
    )
    body = json.loads(result["body"])
    assert body["count"] == 2
    assert {e["gst_id"] for e in body["events"]} == {"evt-1", "evt-2"}


def test_handler_rejects_bad_date():
    result = events_handler.handler({"queryStringParameters": {"start": "not-a-date"}}, None)
    assert result["statusCode"] == 400


def test_handler_rejects_end_before_start():
    result = events_handler.handler(
        {"queryStringParameters": {"start": "2024-06-01", "end": "2024-01-01"}}, None
    )
    assert result["statusCode"] == 400
