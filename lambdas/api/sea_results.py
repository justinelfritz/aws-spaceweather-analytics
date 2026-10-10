"""GET/POST /sea/{field}/{normalization} -- superposed epoch analysis result.

GET: the precomputed, full-catalog result (see sea/run_sea_job.py), passed
through from S3 as-is. 404 means that combination hasn't been computed
yet -- see infra/stacks/sea_stack.py.

POST: an on-demand result computed against a caller-chosen subset of the
DONKI event catalog (docs/sea-on-demand-design.md). Body:
    {"event_ids": ["<gst_id>", ...]}
Omitting "event_ids" (or passing null) means "the full catalog", and takes
the exact same fast path a GET does -- serves the precomputed S3 JSON
directly rather than recomputing something already sitting there. An
explicit event_ids list that happens to name every event in the catalog
takes the same fast path for the same reason. Anything else is computed
synchronously: load the full catalog, filter to the requested IDs, load
only the OMNIWeb years that subset actually touches, then run the same
align -> normalize -> aggregate pipeline the batch job uses
(sea/alignment.py, normalization.py, aggregation.py, load_data.py -- pure
stdlib aside from load_data.py's pyarrow read, vendored into a Lambda layer
built directly from sea/'s own modules; see infra/stacks/api_stack.py).
"""

import json
import os
from datetime import datetime, timezone

import boto3

from aggregation import aggregate_events
from alignment import align_events
from load_data import load_event_catalog, load_omniweb_series
from normalization import DEFAULT_BASELINE_END_OFFSET, DEFAULT_BASELINE_START_OFFSET, STRATEGIES

from .common import error_response, fetch_precomputed_json, json_response

s3 = boto3.client("s3")

# Mirrors historical.py's QUERYABLE_FIELDS -- SEA can in principle run
# against any curated OMNIWeb field. Kept in sync by hand (same tradeoff as
# every other copy of this list in the project -- see historical.py's own
# comment) -- confirmed the hard way: adding bx_gsm/bx_gse to
# historical.py's QUERYABLE_FIELDS without also updating this copy left
# /sea/bx_gsm/... 400ing as "unknown field" even after they were fully
# computed and sitting in S3.
VALID_FIELDS = {
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
VALID_NORMALIZATIONS = {"raw", "baseline_deviation", "normalized_amplitude"}

# Matches sea/run_sea_job.py's own --hours-before/--hours-after defaults
# (also what infra/stacks/sea_stack.py's batch pipeline passes) -- an
# on-demand result needs the same window as the precomputed one to be a
# meaningful like-for-like comparison.
HOURS_BEFORE = 24
HOURS_AFTER = 72


def result_key(field: str, normalization: str) -> str:
    return f"curated/sea_results/{field}/{normalization}.json"


def _compute_result(bucket: str, field: str, normalization: str, events: list) -> dict:
    event_years = {event.time.year for event in events}
    years = sorted(event_years | {y - 1 for y in event_years} | {y + 1 for y in event_years})
    series = load_omniweb_series(s3, bucket, field=field, years=years)

    offsets, aligned = align_events(series, events, hours_before=HOURS_BEFORE, hours_after=HOURS_AFTER)
    normalize = STRATEGIES[normalization]
    normalized = [normalize(offsets, event) for event in aligned]
    aggregated = aggregate_events(offsets, normalized)

    return {
        "field": field,
        "normalization": normalization,
        "hours_before": HOURS_BEFORE,
        "hours_after": HOURS_AFTER,
        "baseline_start_offset": DEFAULT_BASELINE_START_OFFSET,
        "baseline_end_offset": DEFAULT_BASELINE_END_OFFSET,
        "event_count": len(events),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "offsets": [
            {
                "offset": row.offset,
                "n": row.n,
                "mean": row.mean,
                "median": row.median,
                "stderr": row.stderr,
                "ci95_lower": row.ci95_lower,
                "ci95_upper": row.ci95_upper,
                "percentiles": {str(pct): value for pct, value in row.percentiles.items()},
            }
            for row in aggregated
        ],
    }


def _handle_post(field: str, normalization: str, bucket: str, body_raw) -> dict:
    try:
        body = json.loads(body_raw) if body_raw else {}
    except json.JSONDecodeError:
        return error_response(400, "body must be valid JSON")
    if not isinstance(body, dict):
        return error_response(400, "body must be a JSON object")

    event_ids = body.get("event_ids")
    if event_ids is None:
        # Full-catalog request -- identical to a GET, one S3 lookup, no
        # need to even load the catalog.
        return fetch_precomputed_json(s3, bucket, result_key(field, normalization))
    if not isinstance(event_ids, list) or not all(isinstance(item, str) for item in event_ids):
        return error_response(400, "event_ids must be a list of strings")
    if len(event_ids) == 0:
        return error_response(400, "event_ids must not be empty -- omit the field entirely to request the full catalog")

    all_events = load_event_catalog(s3, bucket)
    full_id_set = {event.event_id for event in all_events}
    selected_ids = set(event_ids)

    if selected_ids == full_id_set:
        # An explicit "every event" selection takes the same fast path as
        # omitting event_ids entirely.
        return fetch_precomputed_json(s3, bucket, result_key(field, normalization))

    unknown_ids = sorted(selected_ids - full_id_set)
    if unknown_ids:
        return error_response(400, f"unknown event_id(s): {unknown_ids}")

    selected = [event for event in all_events if event.event_id in selected_ids]
    return json_response(200, _compute_result(bucket, field, normalization, selected))


def handler(event, context):
    params = event.get("pathParameters") or {}
    field = params.get("field")
    normalization = params.get("normalization")

    if field not in VALID_FIELDS:
        return error_response(400, f"unknown field {field!r}; valid fields: {sorted(VALID_FIELDS)}")
    if normalization not in VALID_NORMALIZATIONS:
        return error_response(400, f"unknown normalization {normalization!r}; valid: {sorted(VALID_NORMALIZATIONS)}")

    bucket = os.environ["CURATED_BUCKET_NAME"]

    if event.get("httpMethod") == "POST":
        return _handle_post(field, normalization, bucket, event.get("body"))
    return fetch_precomputed_json(s3, bucket, result_key(field, normalization))
