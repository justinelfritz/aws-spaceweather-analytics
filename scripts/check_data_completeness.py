#!/usr/bin/env python3
"""Sanity-check the raw S3 landing zone for gaps in each feed's time series.

Each ingestion tick lands a rolling multi-day window per feed (see
lambdas/ingestion/handler.py), and ticks overlap — so this merges all landed
objects for a feed, dedupes by time_tag, and looks for stretches between
consecutive records longer than expected. For the RTSW feeds (which report
one record per timestamp *per source spacecraft*), only the record flagged
`active: true` is counted — see docs/data-sources.md for why.

Usage:
    python check_data_completeness.py --bucket space-weather-raw-dev-450649088775-us-east-1
    python check_data_completeness.py --bucket ... --feed kp_1m --profile sw-bootstrap
"""

import argparse
import json
import os
from datetime import datetime, timezone

import boto3

# Keep in sync with FEEDS in lambdas/ingestion/handler.py.
EXPECTED_INTERVAL_SECONDS = {
    "kp_3h": 3 * 3600,
    "kp_1m": 60,
    "solar_wind_plasma_1m": 60,
    "solar_wind_mag_1m": 60,
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--bucket",
        default=os.environ.get("RAW_BUCKET_NAME"),
        help="Raw landing zone bucket name (or set RAW_BUCKET_NAME)",
    )
    parser.add_argument(
        "--feed",
        choices=[*EXPECTED_INTERVAL_SECONDS.keys(), "all"],
        default="all",
        help="Check a single feed, or all of them (default)",
    )
    parser.add_argument("--profile", default=None, help="AWS profile to use (default: default credential chain)")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1.5,
        help="Flag a gap when the delta between consecutive records exceeds this multiple of the expected interval (default: 1.5)",
    )
    args = parser.parse_args()
    if not args.bucket:
        parser.error("--bucket is required (or set RAW_BUCKET_NAME)")
    return args


def list_feed_keys(s3, bucket: str, feed: str) -> list[str]:
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=bucket, Prefix=f"raw/{feed}/"):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


def load_records(s3, bucket: str, key: str) -> list[dict]:
    obj = s3.get_object(Bucket=bucket, Key=key)
    return json.loads(obj["Body"].read())["records"]


def is_relevant(record: dict) -> bool:
    """RTSW feeds carry one record per timestamp per source spacecraft; only
    the one NOAA has designated authoritative should count. Feeds without an
    `active` field (the Kp feeds) have no such duplication."""
    return record.get("active", True)


def collect_unique_timestamps(s3, bucket: str, feed: str) -> tuple[list[datetime], int]:
    keys = list_feed_keys(s3, bucket, feed)
    unique = {}
    for key in keys:
        for record in load_records(s3, bucket, key):
            if not is_relevant(record):
                continue
            unique[record["time_tag"]] = record
    timestamps = sorted(datetime.fromisoformat(tag).replace(tzinfo=timezone.utc) for tag in unique)
    return timestamps, len(keys)


def find_gaps(timestamps: list[datetime], expected_interval_seconds: float, tolerance: float) -> list[tuple[datetime, datetime]]:
    threshold = expected_interval_seconds * tolerance
    gaps = []
    for previous, current in zip(timestamps, timestamps[1:]):
        if (current - previous).total_seconds() > threshold:
            gaps.append((previous, current))
    return gaps


def report_feed(s3, bucket: str, feed: str, tolerance: float) -> None:
    expected_interval = EXPECTED_INTERVAL_SECONDS[feed]
    timestamps, object_count = collect_unique_timestamps(s3, bucket, feed)

    print(f"=== {feed} ===")
    print(f"Objects scanned: {object_count}")

    if not timestamps:
        print("No records found (no landed objects yet, or none were flagged active).")
        print()
        return

    span_seconds = (timestamps[-1] - timestamps[0]).total_seconds()
    expected_count = int(span_seconds / expected_interval) + 1
    actual_count = len(timestamps)
    completeness = min(100.0, round(100 * actual_count / expected_count, 1)) if expected_count else 100.0

    print(f"Unique records:  {actual_count}")
    print(f"Time range:      {timestamps[0].isoformat()} -> {timestamps[-1].isoformat()}")
    print(f"Expected records at {expected_interval}s cadence: {expected_count}")
    print(f"Completeness:    {completeness}%")

    gaps = find_gaps(timestamps, expected_interval, tolerance)
    if not gaps:
        print(f"Gaps (> {tolerance}x expected interval): none")
    else:
        print(f"Gaps (> {tolerance}x expected interval): {len(gaps)}")
        for start, end in gaps:
            duration = end - start
            print(f"  - {start.isoformat()} -> {end.isoformat()}  ({duration})")
    print()


def main():
    args = parse_args()
    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    s3 = session.client("s3")

    feeds = list(EXPECTED_INTERVAL_SECONDS) if args.feed == "all" else [args.feed]
    for feed in feeds:
        report_feed(s3, args.bucket, feed, args.tolerance)


if __name__ == "__main__":
    main()
