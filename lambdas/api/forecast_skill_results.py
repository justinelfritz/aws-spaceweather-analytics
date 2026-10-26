"""GET /forecast-skill/{target}/{error_type} -- precomputed forecast-skill
backtest result (see ml/forecast_skill.py, ml/classification_skill.py),
passed through from S3 as-is.

Path params:
    target      "kp", "dst_index", or "ae_index"
    error_type  "signed_error", "abs_error", or "classification"

Query params:
    severity    optional, one of "minor"/"strong"/"severe"
                (ml/forecast_skill.py's SEVERITY_BANDS) -- restricts the
                error_type=signed_error/abs_error backtest to a storm-class
                band instead of the full catalog. Omit for the unfiltered
                result (unchanged from before severity existed). Not valid
                with error_type=classification -- that result is pointwise
                over the whole test period, not storm-aligned, so a
                severity band doesn't apply to it (see
                ml/classification_skill.py's module docstring).
"""

import os

import boto3

from .common import error_response, fetch_precomputed_json

s3 = boto3.client("s3")

VALID_TARGETS = {"kp", "dst_index", "ae_index"}
VALID_ERROR_TYPES = {"signed_error", "abs_error", "classification"}
VALID_SEVERITIES = {"minor", "strong", "severe"}


def result_key(target: str, error_type: str, severity: str = None) -> str:
    if error_type == "classification":
        return f"curated/ml_classification_skill/{target}.json"
    if severity is None:
        return f"curated/ml_forecast_skill/{target}/{error_type}.json"
    return f"curated/ml_forecast_skill/{target}/{error_type}/{severity}.json"


def handler(event, context):
    params = event.get("pathParameters") or {}
    target = params.get("target")
    error_type = params.get("error_type")
    query = event.get("queryStringParameters") or {}
    severity = query.get("severity")

    if target not in VALID_TARGETS:
        return error_response(400, f"unknown target {target!r}; valid targets: {sorted(VALID_TARGETS)}")
    if error_type not in VALID_ERROR_TYPES:
        return error_response(400, f"unknown error_type {error_type!r}; valid: {sorted(VALID_ERROR_TYPES)}")
    if severity is not None:
        if error_type == "classification":
            return error_response(400, "severity is not supported for error_type=classification")
        if severity not in VALID_SEVERITIES:
            return error_response(
                400, f"unknown severity {severity!r}; valid: {sorted(VALID_SEVERITIES)} (omit for unfiltered)"
            )

    bucket = os.environ["CURATED_BUCKET_NAME"]
    return fetch_precomputed_json(s3, bucket, result_key(target, error_type, severity))
