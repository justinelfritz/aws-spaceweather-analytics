import json
import os
from datetime import datetime, timezone
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from lambdas.ingestion import handler as ingestion_handler

FIXTURES = Path(__file__).parent / "fixtures"
BUCKET = "test-raw-bucket"


def load_fixture(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture
def fixtures():
    return {feed: load_fixture(feed) for feed in ingestion_handler.FEEDS}


def test_validate_accepts_real_sample_shapes(fixtures):
    for feed_name, records in fixtures.items():
        ingestion_handler.validate(feed_name, records)  # should not raise


def test_validate_rejects_empty_response():
    with pytest.raises(ingestion_handler.FeedError, match="non-empty"):
        ingestion_handler.validate("kp_3h", [])


def test_validate_rejects_missing_fields():
    with pytest.raises(ingestion_handler.FeedError, match="missing expected fields"):
        ingestion_handler.validate("kp_3h", [{"time_tag": "2026-01-01T00:00:00"}])


def test_raw_key_is_deterministic_per_feed_and_minute():
    ts = datetime(2026, 9, 15, 12, 7, 42, tzinfo=timezone.utc)
    key_a = ingestion_handler.raw_key("kp_1m", ts)
    key_b = ingestion_handler.raw_key("kp_1m", ts.replace(second=0, microsecond=0))
    assert key_a == key_b == "raw/kp_1m/2026/09/15/1207.json"


def test_raw_key_differs_by_feed():
    ts = datetime(2026, 9, 15, 12, 7, tzinfo=timezone.utc)
    assert ingestion_handler.raw_key("kp_1m", ts) != ingestion_handler.raw_key("kp_3h", ts)


def test_normalize_wraps_records_with_metadata(fixtures):
    ts = datetime(2026, 9, 15, 12, 7, tzinfo=timezone.utc)
    records = fixtures["kp_1m"]
    body = ingestion_handler.normalize("kp_1m", records, ts)
    assert body["source_feed"] == "kp_1m"
    assert body["record_count"] == len(records)
    assert body["records"] == records
    assert body["ingested_at"] == ts.isoformat()


@mock_aws
def test_handler_writes_all_feeds_to_s3(monkeypatch, fixtures):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("RAW_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(ingestion_handler, "s3", s3)
    monkeypatch.setattr(
        ingestion_handler,
        "fetch_json",
        lambda url, **kwargs: fixtures[_feed_name_for_url(url)],
    )

    result = ingestion_handler.handler({}, None)

    assert result["failed"] == []
    assert set(result["succeeded"]) == set(ingestion_handler.FEEDS)

    listing = s3.list_objects_v2(Bucket=BUCKET)
    keys = {obj["Key"] for obj in listing["Contents"]}
    assert len(keys) == len(ingestion_handler.FEEDS)
    for key in keys:
        assert key.startswith("raw/")
        assert key.endswith(".json")


@mock_aws
def test_handler_continues_past_a_single_failed_feed(monkeypatch, fixtures):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("RAW_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(ingestion_handler, "s3", s3)

    def flaky_fetch(url, **kwargs):
        feed_name = _feed_name_for_url(url)
        if feed_name == "kp_3h":
            raise ingestion_handler.FeedError("simulated upstream outage")
        return fixtures[feed_name]

    monkeypatch.setattr(ingestion_handler, "fetch_json", flaky_fetch)

    with pytest.raises(ingestion_handler.FeedError, match="kp_3h"):
        ingestion_handler.handler({}, None)

    listing = s3.list_objects_v2(Bucket=BUCKET)
    keys = {obj["Key"] for obj in listing.get("Contents", [])}
    # The three healthy feeds should still have landed even though kp_3h failed.
    assert len(keys) == len(ingestion_handler.FEEDS) - 1
    assert not any("kp_3h" in key for key in keys)


def _feed_name_for_url(url: str) -> str:
    return next(name for name, feed_url in ingestion_handler.FEEDS.items() if feed_url == url)
