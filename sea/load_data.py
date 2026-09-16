"""Glue between the curated S3 data and the plain shapes alignment.py,
normalization.py, and aggregation.py expect: a {timestamp: value} series and
a list of Event objects. This is a preview of what the eventual Fargate/Glue
packaging (space-weather-platform-todo.md section 5) will do to actually run
the pipeline in production.
"""

import io
import json
from datetime import datetime, timezone

from botocore.exceptions import ClientError
import pyarrow.parquet as pq

from alignment import Event


def load_event_catalog(s3, curated_bucket: str, min_start_time: datetime = None, max_start_time: datetime = None) -> list:
    obj = s3.get_object(Bucket=curated_bucket, Key="curated/event_catalog/geomagnetic_storms.json")
    catalog = json.loads(obj["Body"].read())

    events = []
    for entry in catalog:
        start_time = datetime.fromisoformat(entry["start_time"])
        if min_start_time and start_time < min_start_time:
            continue
        if max_start_time and start_time > max_start_time:
            continue
        events.append(Event(event_id=entry["gst_id"], time=start_time))
    return events


def load_omniweb_series(s3, curated_bucket: str, field: str, years: list) -> dict:
    """Skips years with no backfilled data (e.g. a year beyond the current
    OMNIWeb backfill's range, such as one requested only because it's within
    an event's post-onset window) rather than failing the whole load -- the
    alignment/aggregation logic already tolerates the resulting gaps."""
    series = {}
    for year in years:
        key = f"curated/omniweb_omni2_hourly/year={year}/data.parquet"
        try:
            obj = s3.get_object(Bucket=curated_bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "NoSuchKey":
                print(f"  (no curated OMNIWeb data for {year} yet -- skipping)")
                continue
            raise
        table = pq.read_table(io.BytesIO(obj["Body"].read()), columns=["timestamp", field])
        for timestamp, value in zip(table["timestamp"].to_pylist(), table[field].to_pylist()):
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            series[timestamp] = value
    return series
