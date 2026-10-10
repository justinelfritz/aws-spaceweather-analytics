"""GET /forecast-skill/{target}/{error_type} -- precomputed forecast-skill
backtest result (see ml/forecast_skill.py), passed through from S3 as-is.

Path params:
    target      "kp" or "dst_index"
    error_type  "signed_error" or "abs_error"
"""

import os

import boto3

from .common import error_response, fetch_precomputed_json

s3 = boto3.client("s3")

VALID_TARGETS = {"kp", "dst_index"}
VALID_ERROR_TYPES = {"signed_error", "abs_error"}


def result_key(target: str, error_type: str) -> str:
    return f"curated/ml_forecast_skill/{target}/{error_type}.json"


def handler(event, context):
    params = event.get("pathParameters") or {}
    target = params.get("target")
    error_type = params.get("error_type")

    if target not in VALID_TARGETS:
        return error_response(400, f"unknown target {target!r}; valid targets: {sorted(VALID_TARGETS)}")
    if error_type not in VALID_ERROR_TYPES:
        return error_response(400, f"unknown error_type {error_type!r}; valid: {sorted(VALID_ERROR_TYPES)}")

    bucket = os.environ["CURATED_BUCKET_NAME"]
    return fetch_precomputed_json(s3, bucket, result_key(target, error_type))
