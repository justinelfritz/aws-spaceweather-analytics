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

# Bx is the one queryable field that isn't a 1:1 column name match: GSE and
# GSM share the same X-axis by definition (both point from Earth to the
# Sun; only Y/Z differ, rotated about that shared axis), so OMNI2 only
# publishes a single physical Bx column (`bx_gse_gsm` -- omni2_format.py)
# rather than separate GSM/GSE ones. `bx_gsm`/`bx_gse` are exposed as two
# separate queryable fields anyway (matching how By/Bz are split) and will
# always return numerically identical values -- expected, not a bug.
# Mirrors lambdas/api/historical.py's copy of this same mapping.
FIELD_COLUMN = {"bx_gsm": "bx_gse_gsm", "bx_gse": "bx_gse_gsm"}


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
        column = FIELD_COLUMN.get(field, field)
        table = pq.read_table(io.BytesIO(obj["Body"].read()), columns=["timestamp", column])
        for timestamp, value in zip(table["timestamp"].to_pylist(), table[column].to_pylist()):
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            series[timestamp] = value
    return series
