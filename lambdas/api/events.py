"""GET /events -- the DONKI geomagnetic storm catalog (see
scripts/backfill_donki_gst.py), optionally filtered to a date range. Powers
the dashboard's storm markers on the historical explorer and the event
picker for the SEA/forecast-skill views.

Query params (both optional -- omit both to get the full catalog):
    start   ISO 8601 date/datetime, inclusive lower bound on start_time
    end     ISO 8601 date/datetime, inclusive upper bound on start_time

Returns catalog entries as-is (gst_id, start_time, max_kp, storm_class,
kp_readings, linked_cme_ids, ...) -- no reason to reshape data that's
already dashboard-ready.
"""

import json
import os
from datetime import datetime, timezone

import boto3

from .common import error_response, json_response

s3 = boto3.client("s3")

CATALOG_KEY = "curated/event_catalog/geomagnetic_storms.json"


def _parse_timestamp(value, param_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{param_name} must be an ISO 8601 date/datetime, got {value!r}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def handler(event, context):
    params = event.get("queryStringParameters") or {}

    try:
        start = _parse_timestamp(params["start"], "start") if params.get("start") else None
        end = _parse_timestamp(params["end"], "end") if params.get("end") else None
    except ValueError as exc:
        return error_response(400, str(exc))
    if start and end and end < start:
        return error_response(400, "end must not be before start")

    bucket = os.environ["CURATED_BUCKET_NAME"]
    obj = s3.get_object(Bucket=bucket, Key=CATALOG_KEY)
    catalog = json.loads(obj["Body"].read())

    events = []
    for entry in catalog:
        entry_start = _parse_timestamp(entry["start_time"], "start_time")
        if start and entry_start < start:
            continue
        if end and entry_start > end:
            continue
        events.append(entry)

    return json_response(200, {"count": len(events), "events": events})
