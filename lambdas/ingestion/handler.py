"""Ingestion Lambda for the live NOAA SWPC feeds in v1 scope.

Fetches all four feeds on one schedule (they're cheap, fast HTTP GETs hitting
the same upstream), validates each response against the field shape we saw
when we last verified the endpoints (see docs/data-sources.md), and lands the
raw, untouched response in S3. One feed's failure doesn't block the others —
each is fetched/validated/written independently and the invocation only
raises (to trip the DLQ) after all feeds have been attempted.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

FEEDS = {
    "kp_3h": "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
    "kp_1m": "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json",
    "solar_wind_plasma_1m": "https://services.swpc.noaa.gov/json/rtsw/rtsw_wind_1m.json",
    "solar_wind_mag_1m": "https://services.swpc.noaa.gov/json/rtsw/rtsw_mag_1m.json",
}

# Fields we require to be present on the most recent record before trusting a
# response — a stand-in for "did NOAA change the schema on us" detection.
REQUIRED_FIELDS = {
    "kp_3h": {"time_tag", "Kp", "a_running", "station_count"},
    "kp_1m": {"time_tag", "kp_index", "estimated_kp", "kp"},
    "solar_wind_plasma_1m": {"time_tag", "source", "active", "proton_speed", "proton_density", "proton_temperature"},
    "solar_wind_mag_1m": {"time_tag", "source", "active", "bt", "bz_gsm"},
}


class FeedError(Exception):
    """A single feed failed to fetch or didn't match the expected shape."""


def fetch_json(url: str, attempts: int = 3, backoff_seconds: float = 1.0) -> list:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(backoff_seconds * (2 ** (attempt - 1)))
    raise FeedError(f"failed to fetch {url} after {attempts} attempts: {last_error}")


def validate(feed_name: str, records: list) -> None:
    if not isinstance(records, list) or not records:
        raise FeedError(f"{feed_name}: expected a non-empty JSON array, got {type(records).__name__}")
    missing = REQUIRED_FIELDS[feed_name] - records[-1].keys()
    if missing:
        raise FeedError(f"{feed_name}: response missing expected fields {sorted(missing)} — possible upstream schema drift")


def raw_key(feed_name: str, ingested_at: datetime) -> str:
    """Deterministic key: same feed + same minute always maps to the same S3
    object, so a retried/duplicate invocation overwrites in place instead of
    creating a duplicate record."""
    return (
        f"raw/{feed_name}/{ingested_at:%Y}/{ingested_at:%m}/{ingested_at:%d}/"
        f"{ingested_at:%H%M}.json"
    )


def normalize(feed_name: str, records: list, ingested_at: datetime) -> dict:
    return {
        "source_feed": feed_name,
        "ingested_at": ingested_at.isoformat(),
        "record_count": len(records),
        "records": records,
    }


def handler(event, context):
    bucket = os.environ["RAW_BUCKET_NAME"]
    ingested_at = datetime.now(timezone.utc)

    succeeded, failed = [], []
    for feed_name, url in FEEDS.items():
        try:
            records = fetch_json(url)
            validate(feed_name, records)
            body = normalize(feed_name, records, ingested_at)
            s3.put_object(
                Bucket=bucket,
                Key=raw_key(feed_name, ingested_at),
                Body=json.dumps(body).encode("utf-8"),
                ContentType="application/json",
            )
            succeeded.append(feed_name)
        except FeedError as exc:
            logger.error("feed %s failed: %s", feed_name, exc)
            failed.append(feed_name)

    logger.info("ingestion complete: succeeded=%s failed=%s", succeeded, failed)

    if failed:
        raise FeedError(f"feeds failed this invocation: {failed}")

    return {"succeeded": succeeded, "failed": failed}
