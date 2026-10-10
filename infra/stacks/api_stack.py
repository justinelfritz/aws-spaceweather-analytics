import shutil
from pathlib import Path

import jsii
from aws_cdk import BundlingOptions, CfnOutput, Duration, ILocalBundling, Stack
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from constructs import Construct

LAMBDAS_ROOT = Path(__file__).resolve().parents[2] / "lambdas"
SEA_ROOT = Path(__file__).resolve().parents[2] / "sea"

# AWS-maintained "AWS SDK for pandas" layer (pandas/numpy/pyarrow prebuilt
# for the Lambda runtime) -- used instead of bundling our own build of
# pyarrow, which needs Linux-manylinux wheels a local dev machine can't
# just pip-install into a zip. us-east-1-specific and versioned by AWS
# (see https://aws-sdk-pandas.readthedocs.io/en/stable/layers.html);
# re-pin if this project ever moves region.
PANDAS_LAYER_ARN = "arn:aws:lambda:us-east-1:336392948345:layer:AWSSDKPandas-Python312:31"

# The on-demand SEA path (lambdas/api/sea_results.py's POST handler) needs
# the same alignment/normalization/aggregation/catalog-loading modules the
# batch Glue job uses (sea/run_sea_job.py), imported the same *flat* way
# (`from alignment import ...`, not a package) that sea/pyproject.toml's
# wheel already installs them for Glue -- so a plain Lambda Layer with these
# files under python/ mirrors that exact layout. All four are pure stdlib
# (dataclasses/datetime/statistics/math, plus pyarrow in load_data.py's
# OMNIWeb reader, already covered by the pandas layer below), so this is
# just a file copy, not a real packaging problem -- no pip/Docker build
# needed, unlike the wheel path Glue requires (Python shell jobs only
# accept .egg/.whl for --extra-py-files, but Lambda layers accept loose
# files just fine).
SEA_PIPELINE_MODULES = ["alignment.py", "normalization.py", "aggregation.py", "load_data.py"]


@jsii.implements(ILocalBundling)
class _CopySeaPipelineModules:
    """CDK local-bundling provider: copies SEA_PIPELINE_MODULES into
    <output_dir>/python (the layout a Lambda layer expects) instead of
    running a Docker-based bundling command -- there's nothing to build,
    just files to place."""

    def try_bundle(self, output_dir: str, *args, **kwargs) -> bool:
        target = Path(output_dir) / "python"
        target.mkdir(parents=True, exist_ok=True)
        for filename in SEA_PIPELINE_MODULES:
            shutil.copy2(SEA_ROOT / filename, target / filename)
        return True


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

        sea_pipeline_layer = lambda_.LayerVersion(
            self,
            "SeaPipelineLayer",
            code=lambda_.Code.from_asset(
                str(SEA_ROOT),
                # CDK hashes this whole source tree to name the asset, even
                # though _CopySeaPipelineModules only ever reads
                # SEA_PIPELINE_MODULES -- without excluding the rest,
                # anything unrelated changing under sea/ (rebuilding the
                # Glue job's wheel in dist/, running pytest, .venv churn)
                # spuriously changes this hash and forces a pointless Lambda
                # layer replacement. Confirmed the hard way: rebuilding
                # sea/dist/*.whl for infra/stacks/sea_stack.py alone showed
                # up as a diff here despite the four .py files being
                # byte-identical.
                exclude=[".venv", "dist", "build", "*.egg-info", "**/tests", "**/__pycache__", ".pytest_cache"],
                bundling=BundlingOptions(
                    # Never actually invoked -- _CopySeaPipelineModules.try_bundle
                    # always returns True, so CDK never falls back to running
                    # this image. Still required as a valid BundlingOptions value.
                    image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                    local=_CopySeaPipelineModules(),
                ),
            ),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
        )

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
            layers=[pandas_layer, sea_pipeline_layer],
            # Matches historical_fn's reasoning: 29s is the ceiling API
            # Gateway's own integration timeout allows anyway, and an
            # on-demand POST now does the same concurrent-per-year OMNIWeb
            # reads historical.py does (see sea_results.py's _compute_result),
            # not just a single instant S3 GET like the old GET-only version.
            timeout=Duration.seconds(29),
            memory_size=1024,
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
                # POST added for the on-demand SEA endpoint (docs/sea-on-demand-design.md's
                # Decision 2) -- every other endpoint stays GET-only.
                allow_methods=["GET", "POST"],
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
        # POST carries an optional {"event_ids": [...]} body for on-demand,
        # caller-chosen-subset SEA (docs/sea-on-demand-design.md) -- the one
        # non-GET endpoint in this API, because a meaningful event-ID
        # selection doesn't fit safely in a URL (Decision 2). Still
        # effectively read-only: no state is mutated either way.
        sea_normalization_resource.add_method("POST", apigateway.LambdaIntegration(sea_results_fn))

        forecast_skill_resource = (
            rest_api.root.add_resource("forecast-skill").add_resource("{target}").add_resource("{error_type}")
        )
        forecast_skill_resource.add_method("GET", apigateway.LambdaIntegration(forecast_skill_results_fn))

        CfnOutput(self, "ApiUrl", value=rest_api.url)
        self.rest_api = rest_api
