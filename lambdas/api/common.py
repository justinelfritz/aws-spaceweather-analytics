"""Shared response helpers for the API Gateway Lambda proxy resolvers
(historical.py, events.py, sea_results.py, forecast_skill_results.py). Every
resolver returns plain read-only JSON over Lambda proxy integration, so the
response envelope and error shape are the only things worth sharing --
each handler's actual query/lookup logic is different enough not to
abstract further.
"""

import json

from botocore.exceptions import ClientError

CORS_HEADERS = {
    "Content-Type": "application/json",
    # Public, read-only API (see infra/stacks/api_stack.py) -- no auth, so
    # no credentialed-request restrictions needed on the origin.
    "Access-Control-Allow-Origin": "*",
}


def json_response(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": CORS_HEADERS, "body": json.dumps(body)}


def error_response(status: int, message: str) -> dict:
    return json_response(status, {"error": message})


def fetch_precomputed_json(s3, bucket: str, key: str) -> dict:
    """Passes a precomputed result JSON (SEA, forecast-skill) straight
    through from S3 -- both are written by their own batch jobs
    (sea/run_sea_job.py, ml/forecast_skill.py), never computed at request
    time, so the resolver's only job is a validated key lookup."""
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            return json_response(404, {"error": f"no result found at {key} -- has this combination been computed yet?"})
        raise
    return json_response(200, json.loads(obj["Body"].read()))
