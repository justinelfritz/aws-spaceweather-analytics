"""Integration tests against a real, deployed API Gateway stage -- unlike
tests/unit (CDK template assertions only), these make real HTTPS requests
and so need an actual deployment to run against.

Skipped by default (no network calls in the normal unit test run). To run:

    export API_BASE_URL=$(aws cloudformation describe-stacks \
        --stack-name SpaceWeather-Api-dev --profile sw-bootstrap \
        --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)
    python -m pytest tests/integration/ -q

Only exercises validation/shape/error-contract behavior that holds
regardless of what's currently in the curated bucket (e.g. "unknown field
-> 400"), plus one endpoint (`/sea/dst_index/raw`) known to have a
precomputed result as of 2026-09-16 -- see space-weather-platform-todo.md
section 5/6.
"""

import json
import os
import urllib.error
import urllib.request

import pytest

API_BASE_URL = os.environ.get("API_BASE_URL", "").rstrip("/")

pytestmark = pytest.mark.skipif(
    not API_BASE_URL, reason="set API_BASE_URL to a deployed API's URL to run integration tests"
)


def _get(path: str):
    request = urllib.request.Request(f"{API_BASE_URL}{path}")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_events_returns_the_catalog():
    status, body = _get("/events")
    assert status == 200
    assert "events" in body and "count" in body
    assert body["count"] == len(body["events"])


def test_events_rejects_bad_date():
    status, body = _get("/events?start=not-a-date")
    assert status == 400
    assert "error" in body


def test_historical_requires_fields_param():
    status, body = _get("/historical?start=2024-01-01&end=2024-01-02")
    assert status == 400
    assert "error" in body


def test_historical_rejects_unknown_field():
    status, body = _get("/historical?fields=not_a_real_field&start=2024-01-01&end=2024-01-02")
    assert status == 400


def test_historical_returns_known_data_range():
    # 2003-10-29/30 -- the "Halloween storm" -- is well within the backfilled
    # 1963-2026 OMNIWeb range (scripts/backfill_omniweb.py), so this range
    # should always have data regardless of what's changed since.
    status, body = _get("/historical?fields=dst_index&start=2003-10-29&end=2003-10-30")
    assert status == 200
    assert body["count"] > 0
    assert any(row["dst_index"] is not None for row in body["data"])


def test_sea_results_known_combination():
    status, body = _get("/sea/dst_index/raw")
    assert status == 200
    assert body["field"] == "dst_index"
    assert body["normalization"] == "raw"
    assert "offsets" in body


def test_sea_results_rejects_unknown_field():
    status, _body = _get("/sea/not_a_field/raw")
    assert status == 400


def test_forecast_skill_known_combination():
    status, body = _get("/forecast-skill/kp/abs_error")
    assert status == 200
    assert body["target"] == "kp"
    assert body["error_type"] == "abs_error"
    assert "model_version" in body


def test_forecast_skill_rejects_unknown_error_type():
    status, _body = _get("/forecast-skill/kp/not_a_real_error_type")
    assert status == 400
