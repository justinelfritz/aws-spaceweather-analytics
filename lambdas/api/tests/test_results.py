import json

import boto3
from moto import mock_aws

from lambdas.api import forecast_skill_results as forecast_skill_handler
from lambdas.api import sea_results as sea_handler

BUCKET = "curated-test"


@mock_aws
def test_sea_handler_passes_through_precomputed_result(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(sea_handler, "s3", s3)

    payload = {"field": "dst_index", "normalization": "raw", "offsets": []}
    s3.put_object(
        Bucket=BUCKET,
        Key="curated/sea_results/dst_index/raw.json",
        Body=json.dumps(payload).encode("utf-8"),
    )

    result = sea_handler.handler({"pathParameters": {"field": "dst_index", "normalization": "raw"}}, None)
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == payload


@mock_aws
def test_sea_handler_404s_when_not_yet_computed(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(sea_handler, "s3", s3)

    result = sea_handler.handler({"pathParameters": {"field": "kp", "normalization": "raw"}}, None)
    assert result["statusCode"] == 404


def test_sea_handler_rejects_unknown_field():
    result = sea_handler.handler({"pathParameters": {"field": "not_a_field", "normalization": "raw"}}, None)
    assert result["statusCode"] == 400


def test_sea_handler_rejects_unknown_normalization():
    result = sea_handler.handler({"pathParameters": {"field": "kp", "normalization": "bogus"}}, None)
    assert result["statusCode"] == 400


@mock_aws
def test_forecast_skill_handler_passes_through_precomputed_result(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("CURATED_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(forecast_skill_handler, "s3", s3)

    payload = {"target": "kp", "error_type": "abs_error", "offsets": []}
    s3.put_object(
        Bucket=BUCKET,
        Key="curated/ml_forecast_skill/kp/abs_error.json",
        Body=json.dumps(payload).encode("utf-8"),
    )

    result = forecast_skill_handler.handler(
        {"pathParameters": {"target": "kp", "error_type": "abs_error"}}, None
    )
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == payload


def test_forecast_skill_handler_rejects_unknown_target():
    result = forecast_skill_handler.handler(
        {"pathParameters": {"target": "bogus", "error_type": "abs_error"}}, None
    )
    assert result["statusCode"] == 400


def test_forecast_skill_handler_rejects_unknown_error_type():
    result = forecast_skill_handler.handler(
        {"pathParameters": {"target": "kp", "error_type": "bogus"}}, None
    )
    assert result["statusCode"] == 400
