#!/usr/bin/env python3
"""Pull NASA DONKI's geomagnetic storm (GST) catalog into S3 — the event
catalog SEA aligns time-series segments against.

DONKI's GST coverage starts 2010-04-05 (confirmed by probing: querying
1996-2010 returns zero events, and summing several chunked sub-range
queries gives exactly the same total as one query spanning the whole
range, so 200 total events is a real count, not an API page-size cap
silently truncating something larger). There is no earlier automated
source in this project's current scope — SEA analysis using this catalog
is limited to 2010+ storms even though the OMNIWeb time series backing it
goes back to 1963 (decided 2026-09-15, see chat/todo section 5).

Lands the raw API response in S3 (keyed by pull date, for provenance) and
writes a normalized, flattened catalog to the curated bucket at a fixed
key. Re-running overwrites both with the latest data — safe to schedule
periodically (e.g. daily) to pick up newly issued storms.

Usage:
    python backfill_donki_gst.py --raw-bucket ... --curated-bucket ... --profile sw-bootstrap
"""

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import boto3

DONKI_GST_URL = "https://kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get/GST"
CATALOG_START_DATE = "2010-01-01"  # a few months before the earliest confirmed event, for safety


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-bucket", required=True)
    parser.add_argument("--curated-bucket", required=True)
    parser.add_argument("--start-date", default=CATALOG_START_DATE)
    parser.add_argument("--end-date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    parser.add_argument("--profile", default=None, help="AWS profile to use (default: default credential chain)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and report without writing to S3")
    return parser.parse_args()


def fetch_gst_events(start_date: str, end_date: str, attempts: int = 3, backoff_seconds: float = 2.0) -> list[dict]:
    url = f"{DONKI_GST_URL}?startDate={start_date}&endDate={end_date}"
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(backoff_seconds * attempt)
    raise RuntimeError(f"failed to fetch {url} after {attempts} attempts: {last_error}")


def _parse_donki_time(value: str) -> datetime:
    # DONKI timestamps look like "2010-04-05T12:00Z" — minute precision,
    # with a trailing "Z" that Python 3.10's fromisoformat doesn't accept
    # directly (that support was added in 3.11).
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def storm_class(max_kp: float) -> str:
    """NOAA's G-scale: Kp 5/6/7/8/9 -> G1/G2/G3/G4/G5."""
    g_number = max(1, min(5, round(max_kp) - 4))
    return f"G{g_number}"


def normalize_event(event: dict) -> dict:
    kp_readings = [
        {
            "time": _parse_donki_time(reading["observedTime"]).isoformat(),
            "kp": reading["kpIndex"],
            "source": reading["source"],
        }
        for reading in (event.get("allKpIndex") or [])
    ]
    max_reading = max(kp_readings, key=lambda r: r["kp"], default=None)

    linked_event_ids = [linked["activityID"] for linked in (event.get("linkedEvents") or [])]
    linked_cme_ids = [activity_id for activity_id in linked_event_ids if "-CME-" in activity_id]

    return {
        "gst_id": event["gstID"],
        "start_time": _parse_donki_time(event["startTime"]).isoformat(),
        "max_kp": max_reading["kp"] if max_reading else None,
        "max_kp_time": max_reading["time"] if max_reading else None,
        "storm_class": storm_class(max_reading["kp"]) if max_reading else None,
        "kp_readings": kp_readings,
        "linked_event_ids": linked_event_ids,
        "linked_cme_ids": linked_cme_ids,
        "source_link": event.get("link"),
        "submission_time": event.get("submissionTime"),
        "version_id": event.get("versionId"),
    }


def build_catalog(raw_events: list[dict]) -> list[dict]:
    catalog = [normalize_event(event) for event in raw_events]
    catalog.sort(key=lambda e: e["start_time"])
    return catalog


def main():
    args = parse_args()
    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    s3 = session.client("s3")

    raw_events = fetch_gst_events(args.start_date, args.end_date)
    catalog = build_catalog(raw_events)

    pulled_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw_key = f"raw/donki_gst/pulled_at={pulled_at}/gst_events.json"
    curated_key = "curated/event_catalog/geomagnetic_storms.json"

    if args.dry_run:
        print(f"[dry-run] {len(raw_events)} raw events -> {raw_key}")
        print(f"[dry-run] {len(catalog)} normalized events -> {curated_key}")
        return

    s3.put_object(
        Bucket=args.raw_bucket,
        Key=raw_key,
        Body=json.dumps(raw_events).encode("utf-8"),
        ContentType="application/json",
    )
    s3.put_object(
        Bucket=args.curated_bucket,
        Key=curated_key,
        Body=json.dumps(catalog, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

    print(f"{len(raw_events)} raw events -> s3://{args.raw_bucket}/{raw_key}")
    print(f"{len(catalog)} normalized events -> s3://{args.curated_bucket}/{curated_key}")


if __name__ == "__main__":
    main()
