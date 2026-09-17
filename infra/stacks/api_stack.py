from pathlib import Path

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from constructs import Construct

LAMBDAS_ROOT = Path(__file__).resolve().parents[2] / "lambdas"

# AWS-maintained "AWS SDK for pandas" layer (pandas/numpy/pyarrow prebuilt
# for the Lambda runtime) -- used instead of bundling our own build of
# pyarrow, which needs Linux-manylinux wheels a local dev machine can't
# just pip-install into a zip. us-east-1-specific and versioned by AWS
# (see https://aws-sdk-pandas.readthedocs.io/en/stable/layers.html);
# re-pin if this project ever moves region.
PANDAS_LAYER_ARN = "arn:aws:lambda:us-east-1:336392948345:layer:AWSSDKPandas-Python312:31"


class ApiStack(Stack):
    """Public API layer.

    Owns: API Gateway REST API, Lambda resolvers, usage-plan throttling.
    No Cognito -- every endpoint here only ever reads precomputed or
    curated data (OMNIWeb history, the DONKI event catalog, SEA/forecast-skill
    results), so there's nothing to protect behind auth; the todo's "optional
    for a portfolio demo" case applies (decided 2026-09-16). No WebSocket API
    either -- descoped along with the dashboard's live-readings view, see
    space-weather-platform-todo.md section 7.
    """

    def __init__(self, scope: Construct, construct_id: str, curated_bucket: s3.IBucket, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        code = lambda_.Code.from_asset(
            str(LAMBDAS_ROOT),
            exclude=[".venv", "**/tests", "**/__pycache__", "requirements-dev.txt"],
        )
        common_env = {"CURATED_BUCKET_NAME": curated_bucket.bucket_name}

        pandas_layer = lambda_.LayerVersion.from_layer_version_arn(self, "PandasLayer", PANDAS_LAYER_ARN)

        historical_fn = lambda_.Function(
            self,
            "HistoricalFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="api.historical.handler",
            code=code,
            layers=[pandas_layer],
            # 29s, not just "high enough" -- API Gateway REST APIs have a
            # hard, non-configurable 29s integration timeout, so anything
            # longer here would never actually get used; this just avoids
            # Lambda cutting the invocation short before API Gateway would.
            # In practice a full 63-year range still finishes in ~1-2s
            # thanks to the concurrent per-year S3 fetches (see
            # lambdas/api/historical.py's _fetch_years).
            timeout=Duration.seconds(29),
            memory_size=1024,
            environment=common_env,
        )

        events_fn = lambda_.Function(
            self,
            "EventsFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="api.events.handler",
            code=code,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment=common_env,
        )

        sea_results_fn = lambda_.Function(
            self,
            "SeaResultsFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="api.sea_results.handler",
            code=code,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment=common_env,
        )

        forecast_skill_results_fn = lambda_.Function(
            self,
            "ForecastSkillResultsFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="api.forecast_skill_results.handler",
            code=code,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment=common_env,
        )

        for fn in (historical_fn, events_fn, sea_results_fn, forecast_skill_results_fn):
            curated_bucket.grant_read(fn)

        rest_api = apigateway.RestApi(
            self,
            "RestApi",
            rest_api_name="space-weather-api",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=["GET"],
            ),
            # Basic, no-API-key rate limiting on the whole public API --
            # a usage plan/API key would defeat the point of a public
            # read-only demo API, so this throttles the stage itself instead.
            deploy_options=apigateway.StageOptions(
                stage_name="v1",
                throttling_rate_limit=10,
                throttling_burst_limit=20,
            ),
        )

        historical_resource = rest_api.root.add_resource("historical")
        historical_resource.add_method("GET", apigateway.LambdaIntegration(historical_fn))

        events_resource = rest_api.root.add_resource("events")
        events_resource.add_method("GET", apigateway.LambdaIntegration(events_fn))

        sea_normalization_resource = rest_api.root.add_resource("sea").add_resource("{field}").add_resource(
            "{normalization}"
        )
        sea_normalization_resource.add_method("GET", apigateway.LambdaIntegration(sea_results_fn))

        forecast_skill_resource = (
            rest_api.root.add_resource("forecast-skill").add_resource("{target}").add_resource("{error_type}")
        )
        forecast_skill_resource.add_method("GET", apigateway.LambdaIntegration(forecast_skill_results_fn))

        CfnOutput(self, "ApiUrl", value=rest_api.url)
        self.rest_api = rest_api
