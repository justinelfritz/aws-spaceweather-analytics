"""GET /sea/{field}/{normalization} -- precomputed superposed epoch
analysis result (see sea/run_sea_job.py), passed through from S3 as-is.

Path params:
    field           an OMNIWeb curated column, e.g. "dst_index"
    normalization   one of sea/normalization.py's STRATEGIES keys

A field/normalization combination that hasn't been run yet (e.g. after
adding a new field to VALID_FIELDS below, before the next backfill/weekly
run computes it -- see infra/stacks/sea_stack.py) returns 404, not an
error -- it's a valid combination, just not computed yet.
"""

import os

import boto3

from .common import error_response, fetch_precomputed_json

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


def result_key(field: str, normalization: str) -> str:
    return f"curated/sea_results/{field}/{normalization}.json"


def handler(event, context):
    params = event.get("pathParameters") or {}
    field = params.get("field")
    normalization = params.get("normalization")

    if field not in VALID_FIELDS:
        return error_response(400, f"unknown field {field!r}; valid fields: {sorted(VALID_FIELDS)}")
    if normalization not in VALID_NORMALIZATIONS:
        return error_response(400, f"unknown normalization {normalization!r}; valid: {sorted(VALID_NORMALIZATIONS)}")

    bucket = os.environ["CURATED_BUCKET_NAME"]
    return fetch_precomputed_json(s3, bucket, result_key(field, normalization))
