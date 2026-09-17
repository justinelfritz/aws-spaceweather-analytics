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
Year-partition fetches run concurrently (see _fetch_years) so a long range
doesn't mean dozens of sequential S3 round-trips.

Ranges over HOURLY_RESOLUTION_MAX_DAYS get aggregated to one row per UTC
day (daily mean per field) instead of raw hourly rows -- the response
includes a "resolution" field ("hourly" or "daily") so callers know which
they got.
Needs pyarrow, which isn't in the base Lambda runtime -- provided by the
AWS-maintained "AWSSDKPandas" layer (see infra/stacks/api_stack.py) rather
than bundling our own, since it's the standard, AWS-published way to get
pandas/numpy/pyarrow into a Lambda without a custom Docker build.
"""

import io
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from statistics import fmean

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
    "bx_gsm",
    "by_gsm",
    "bz_gsm",
    "bx_gse",
    "by_gse",
    "bz_gse",
    "flow_pressure",
    "electric_field",
    "f107_index",
}

# Most queryable fields map 1:1 to a curated Parquet column of the same
# name. Bx is the one exception: GSE and GSM share the same X-axis by
# definition (both point from Earth to the Sun; only Y and Z differ,
# rotated about that shared axis to account for Earth's dipole tilt), so
# OMNI2 only ever publishes a single physical Bx column (`bx_gse_gsm` --
# see scripts/omni2_format.py) rather than separate GSM/GSE ones, unlike
# By/Bz, which really do differ between the two frames. `bx_gsm` and
# `bx_gse` are exposed as two separate queryable fields anyway (matching
# how By/Bz are split) and will always return numerically identical
# values -- that's expected, not a bug. Mirrors sea/load_data.py's copy of
# this same mapping.
FIELD_COLUMN = {"bx_gsm": "bx_gse_gsm", "bx_gse": "bx_gse_gsm"}

# Full hourly resolution is only returned for ranges up to this length.
# Beyond it, the response is aggregated to one row per UTC day (daily mean
# per field, see _aggregate_daily) instead of raw hourly rows -- otherwise a
# multi-year request would both risk an oversized JSON payload (API
# Gateway's response limit is 10MB) and be a fundamentally wrong resolution
# to chart anyway. 90 days comfortably covers the historical explorer's
# storm-scale use case (weeks-to-months); anything longer is inherently a
# multi-year trend view (e.g. the ~11-year solar cycle), where daily means
# are the right resolution regardless of payload size.
HOURLY_RESOLUTION_MAX_DAYS = 90

# Outer bound on any request, hourly or aggregated. Generous enough to cover
# the entire OMNI2 dataset (1963-present) many times over -- this exists
# only to reject pathological inputs (e.g. a 5000-year range), not to
# constrain real usage.
MAX_RANGE_DAYS = 100 * 365


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


def _load_year(bucket: str, year: int, fields: list) -> list:
    key = f"curated/omniweb_omni2_hourly/year={year}/data.parquet"
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            return []
        raise
    # Read each requested field's *physical* column (deduplicated, since
    # bx_gsm and bx_gse both read the same one), then reshape each row back
    # to the requested *logical* field names so callers never need to know
    # about the underlying column mapping.
    physical_columns = sorted({FIELD_COLUMN.get(field, field) for field in fields})
    table = pq.read_table(io.BytesIO(obj["Body"].read()), columns=["timestamp"] + physical_columns)
    return [
        {"timestamp": row["timestamp"], **{field: row[FIELD_COLUMN.get(field, field)] for field in fields}}
        for row in table.to_pylist()
    ]


def _fetch_years(bucket: str, years: range, fields: list) -> list:
    # Each year is an independent S3 GET + Parquet parse -- I/O-bound, so
    # threads help even under Lambda's fractional vCPU allocation. A long
    # (e.g. full 63-year) range would otherwise mean dozens of sequential
    # round-trips and risk the Lambda's timeout.
    with ThreadPoolExecutor(max_workers=min(len(years), 16)) as pool:
        results = pool.map(lambda year: _load_year(bucket, year, fields), years)
    return [row for year_rows in results for row in year_rows]


def _aggregate_daily(rows: dict, fields: list) -> list:
    buckets = {}
    for timestamp, row in rows.items():
        day = timestamp.date()
        bucket = buckets.setdefault(day, {field: [] for field in fields})
        for field in fields:
            value = row[field]
            if value is not None:
                bucket[field].append(value)
    return [
        {
            "timestamp": datetime(day.year, day.month, day.day, tzinfo=timezone.utc).isoformat(),
            **{field: (fmean(values) if values else None) for field, values in bucket.items()},
        }
        for day, bucket in sorted(buckets.items())
    ]


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
    years = range(start.year, end.year + 1)
    rows = {}
    for row in _fetch_years(bucket, years, fields):
        timestamp = row["timestamp"]
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if start <= timestamp <= end:
            rows[timestamp] = row

    if (end - start) <= timedelta(days=HOURLY_RESOLUTION_MAX_DAYS):
        resolution = "hourly"
        data = [
            {"timestamp": timestamp.isoformat(), **{field: row[field] for field in fields}}
            for timestamp, row in sorted(rows.items())
        ]
    else:
        resolution = "daily"
        data = _aggregate_daily(rows, fields)

    return json_response(
        200,
        {
            "fields": fields,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "resolution": resolution,
            "count": len(data),
            "data": data,
        },
    )
