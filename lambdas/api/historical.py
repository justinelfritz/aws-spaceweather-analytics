"""GET /historical -- range query over the curated OMNIWeb OMNI2 hourly
Parquet dataset (see scripts/backfill_omniweb.py, scripts/omni2_format.py).

Query params:
    fields  required, comma-separated (e.g. "kp,dst_index")
    start   required, ISO 8601 date or datetime (inclusive)
    end     required, ISO 8601 date or datetime (inclusive)

Reads only the year partitions the requested range actually touches, and
only the requested columns (pyarrow column projection), the same approach
sea/load_data.py uses -- the curated dataset is small enough per year
(~27MB/64 years total) that this is cheap even inside a Lambda invocation.
Needs pyarrow, which isn't in the base Lambda runtime -- provided by the
AWS-maintained "AWSSDKPandas" layer (see infra/stacks/api_stack.py) rather
than bundling our own, since it's the standard, AWS-published way to get
pandas/numpy/pyarrow into a Lambda without a custom Docker build.
"""

import io
import os
from datetime import datetime, timedelta, timezone

import boto3
import pyarrow.parquet as pq
from botocore.exceptions import ClientError

from .common import error_response, json_response

s3 = boto3.client("s3")

# Curated OMNIWeb columns actually useful to chart -- excludes internal
# metadata (spacecraft IDs, per-hour point counts, sigma/uncertainty
# columns) that scripts/omni2_format.py carries for provenance but that a
# dashboard has no use for.
QUERYABLE_FIELDS = {
    "kp",
    "dst_index",
    "ae_index",
    "ap_index",
    "sunspot_number_r",
    "plasma_speed",
    "proton_density",
    "proton_temperature",
    "field_magnitude_avg",
    "bz_gsm",
    "by_gsm",
    "bz_gse",
    "by_gse",
    "flow_pressure",
    "electric_field",
    "f107_index",
}

# Bounds how much data one request can pull (and how many year-partition
# GETs it triggers) -- generous enough for the dashboard's historical
# explorer (a multi-month view) without letting one request scan the whole
# 64-year dataset.
MAX_RANGE_DAYS = 366


def _parse_timestamp(value, param_name: str) -> datetime:
    if not value:
        raise ValueError(f"missing required query parameter: {param_name}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{param_name} must be an ISO 8601 date/datetime, got {value!r}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _load_year(bucket: str, year: int, columns: list) -> list:
    key = f"curated/omniweb_omni2_hourly/year={year}/data.parquet"
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            return []
        raise
    table = pq.read_table(io.BytesIO(obj["Body"].read()), columns=["timestamp"] + columns)
    return table.to_pylist()


def handler(event, context):
    params = event.get("queryStringParameters") or {}

    fields_param = params.get("fields")
    if not fields_param:
        return error_response(400, "missing required query parameter: fields")
    fields = [f.strip() for f in fields_param.split(",") if f.strip()]
    unknown = sorted(set(fields) - QUERYABLE_FIELDS)
    if unknown:
        return error_response(400, f"unknown field(s) {unknown}; valid fields: {sorted(QUERYABLE_FIELDS)}")

    try:
        start = _parse_timestamp(params.get("start"), "start")
        end = _parse_timestamp(params.get("end"), "end")
    except ValueError as exc:
        return error_response(400, str(exc))

    if end < start:
        return error_response(400, "end must not be before start")
    if (end - start) > timedelta(days=MAX_RANGE_DAYS):
        return error_response(400, f"range too large: max {MAX_RANGE_DAYS} days per request")

    bucket = os.environ["CURATED_BUCKET_NAME"]
    rows = {}
    for year in range(start.year, end.year + 1):
        for row in _load_year(bucket, year, fields):
            timestamp = row["timestamp"]
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            if start <= timestamp <= end:
                rows[timestamp] = row

    data = [
        {"timestamp": timestamp.isoformat(), **{field: row[field] for field in fields}}
        for timestamp, row in sorted(rows.items())
    ]
    return json_response(
        200,
        {
            "fields": fields,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "count": len(data),
            "data": data,
        },
    )
