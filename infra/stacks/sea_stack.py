from pathlib import Path

from aws_cdk import Duration, Stack
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_glue as glue
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_assets as s3_assets
from aws_cdk import aws_s3_deployment as s3_deployment
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as sfn_tasks
from constructs import Construct

SEA_ROOT = Path(__file__).resolve().parents[2] / "sea"
SEA_JOB_NAME = "space-weather-sea"

# Mirrors lambdas/api/historical.py's QUERYABLE_FIELDS -- kept in sync by
# hand, same tradeoff as frontend/src/constants.js's copy of the same list:
# infra/ and lambdas/ are separate installs with no shared package to import
# this from.
SEA_FIELDS = [
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
]
# Mirrors sea/normalization.py's STRATEGIES keys.
SEA_NORMALIZATIONS = ["raw", "baseline_deviation", "normalized_amplitude"]
# The full field x normalization matrix (18 x 3 = 54) -- every combination
# the API/dashboard can request (see docs/api-reference.md's /sea/{field}/
# {normalization}), computed by the same one Glue job run once per
# combination (see the Map state below) instead of hardcoding just one.
SEA_COMBINATIONS = [
    {"field": field, "normalization": normalization}
    for field in SEA_FIELDS
    for normalization in SEA_NORMALIZATIONS
]
# How many Glue job runs the Map state lets run at once. Must match the
# Glue job's own ExecutionProperty.max_concurrent_runs below -- Glue caps
# concurrent runs of the *same* job at 1 by default, so without raising
# that too, most of the Map's concurrent GlueStartJobRun calls would just
# fail with ConcurrentRunsExceededException.
SEA_MAP_CONCURRENCY = 8
# Must match the version pinned in sea/pyproject.toml. Python shell jobs only
# support .egg/.whl for --extra-py-files (not loose .py files or a plain zip
# of modules -- confirmed the hard way: an earlier zip-of-a-directory attempt
# deployed fine but failed at run time with ModuleNotFoundError, since Glue
# doesn't unpack/import from an arbitrary zip the way Spark ETL jobs do).
# Rebuild after changing any shared module: cd sea && pip wheel --no-deps -w dist/ .
SEA_LIB_WHEEL_FILENAME = "sea_lib-0.1.0-py3-none-any.whl"
SEA_LIB_WHEEL = SEA_ROOT / "dist" / SEA_LIB_WHEEL_FILENAME


class SeaStack(Stack):
    """Superposed epoch analysis: Glue Python shell job + Step Functions trigger.

    Owns: the SEA Glue job (alignment -> normalization -> aggregation, see
    sea/run_sea_job.py), its execution role, and a state machine that runs
    it -- via a Map state -- once per {field, normalization} combination in
    SEA_COMBINATIONS (all 18 queryable fields x all 3 normalizations), on a
    weekly schedule. Also startable on-demand via
    `aws stepfunctions start-execution` today; a proper HTTP trigger is
    section 7's job once API Gateway exists.
    """

    def __init__(self, scope: Construct, construct_id: str, curated_bucket: s3.IBucket, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if not SEA_LIB_WHEEL.exists():
            raise FileNotFoundError(
                f"{SEA_LIB_WHEEL} not found -- build it first: "
                f"cd sea && pip wheel --no-deps -w dist/ ."
            )

        script_asset = s3_assets.Asset(self, "SeaJobScript", path=str(SEA_ROOT / "run_sea_job.py"))

        # Plain Asset uploads always get a content-hash filename, which
        # breaks pip's strict PEP 427 wheel-filename parsing ("<name> is not
        # a valid wheel filename") -- confirmed the hard way, this failed at
        # job run time even though the deploy itself succeeded. BucketDeployment
        # preserves the original filename, which pip needs to install it.
        lib_deployment_prefix = "code/sea-lib"
        lib_deployment = s3_deployment.BucketDeployment(
            self,
            "DeploySeaLib",
            sources=[s3_deployment.Source.asset(str(SEA_ROOT / "dist"))],
            destination_bucket=curated_bucket,
            destination_key_prefix=lib_deployment_prefix,
            prune=False,
        )
        lib_wheel_s3_url = f"s3://{curated_bucket.bucket_name}/{lib_deployment_prefix}/{SEA_LIB_WHEEL_FILENAME}"

        job_role = iam.Role(
            self,
            "SeaJobRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSGlueServiceRole")],
        )
        curated_bucket.grant_read_write(job_role)
        script_asset.grant_read(job_role)

        sea_job = glue.CfnJob(
            self,
            "SeaJob",
            name=SEA_JOB_NAME,
            role=job_role.role_arn,
            command=glue.CfnJob.JobCommandProperty(
                name="pythonshell",
                python_version="3.9",
                script_location=script_asset.s3_object_url,
            ),
            default_arguments={
                "--extra-py-files": lib_wheel_s3_url,
                "--curated-bucket": curated_bucket.bucket_name,
                # Fallback only, for a manual/console run with no explicit
                # arguments -- every real run through the state machine below
                # overrides --field/--normalization per Map iteration.
                "--field": "dst_index",
                "--normalization": "raw",
                "--hours-before": "24",
                "--hours-after": "72",
            },
            # Python shell jobs: 0.0625 or 1 DPU only, and MaxCapacity can't
            # be combined with GlueVersion 2.0+ -- this workload is tiny, so
            # the smallest size (and no GlueVersion) is the right fit.
            max_capacity=0.0625,
            # Glue caps concurrent runs of one job at 1 by default -- raise
            # it to match the state machine's Map concurrency, or most of
            # the concurrent GlueStartJobRun calls below would fail with
            # ConcurrentRunsExceededException.
            execution_property=glue.CfnJob.ExecutionPropertyProperty(max_concurrent_runs=SEA_MAP_CONCURRENCY),
        )
        sea_job.node.add_dependency(lib_deployment)

        run_sea_job_task = sfn_tasks.GlueStartJobRun(
            self,
            "RunSeaJob",
            glue_job_name=SEA_JOB_NAME,
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
            # Each Map iteration's input is one {"field", "normalization"}
            # item from SEA_COMBINATIONS (see below) -- overrides the job's
            # default_arguments for just that run.
            arguments=sfn.TaskInput.from_object(
                {
                    "--field": sfn.JsonPath.string_at("$.field"),
                    "--normalization": sfn.JsonPath.string_at("$.normalization"),
                    "--hours-before": "24",
                    "--hours-after": "72",
                }
            ),
        )
        # Even with MaxConcurrentRuns raised to match the Map's concurrency,
        # firing several StartJobRun calls in a tight window races Glue's
        # own admission check -- confirmed the hard way (a real backfill run:
        # 8 concurrent starts, one job finished almost instantly, and the
        # very next start into that freed slot got rejected with this error
        # before Glue's count had caught up). Retrying with backoff, not
        # raising the concurrency further, is the standard fix for this
        # specific race.
        run_sea_job_task.add_retry(
            errors=["Glue.ConcurrentRunsExceededException"],
            interval=Duration.seconds(10),
            max_attempts=6,
            backoff_rate=1.5,
        )

        # Seeds the fixed combinations list into the execution's state --
        # Map's newer `items` prop (a literal array, no input plumbing
        # needed) only works under the JSONata query language; this state
        # machine uses the classic JSONPath language (matching the rest of
        # this project's state machines), where Map can only iterate over
        # an array already present in the input, via `items_path`.
        seed_combinations = sfn.Pass(
            self,
            "SeedCombinations",
            result=sfn.Result.from_array(SEA_COMBINATIONS),
            result_path="$.combinations",
        )

        # Runs the Glue job once per {field, normalization} combination
        # instead of just the one hardcoded pair this originally shipped
        # with -- the job itself already supported any combination via its
        # --field/--normalization args (sea/run_sea_job.py), this was the
        # only piece missing to actually compute the other 47.
        for_each_combination = sfn.Map(
            self,
            "ForEachFieldNormalization",
            items_path="$.combinations",
            max_concurrency=SEA_MAP_CONCURRENCY,
        )
        for_each_combination.item_processor(run_sea_job_task)

        state_machine = sfn.StateMachine(
            self,
            "SeaStateMachine",
            definition_body=sfn.DefinitionBody.from_chainable(seed_combinations.next(for_each_combination)),
            # Bumped from 15 minutes (one job run) to comfortably cover all
            # 54 combinations at SEA_MAP_CONCURRENCY-way concurrency.
            timeout=Duration.minutes(30),
        )

        # Weekly re-run to pick up newly cataloged storms -- SEA results
        # don't need to be fresher than that.
        schedule_rule = events.Rule(
            self,
            "SeaWeeklySchedule",
            schedule=events.Schedule.rate(Duration.days(7)),
        )
        schedule_rule.add_target(targets.SfnStateMachine(state_machine))

        self.state_machine = state_machine
