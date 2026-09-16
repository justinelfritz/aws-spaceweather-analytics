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
    it on a weekly schedule -- also startable on-demand via
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
                "--field": "dst_index",
                "--normalization": "raw",
                "--hours-before": "24",
                "--hours-after": "72",
            },
            # Python shell jobs: 0.0625 or 1 DPU only, and MaxCapacity can't
            # be combined with GlueVersion 2.0+ -- this workload is tiny, so
            # the smallest size (and no GlueVersion) is the right fit.
            max_capacity=0.0625,
        )
        sea_job.node.add_dependency(lib_deployment)

        run_sea_job_task = sfn_tasks.GlueStartJobRun(
            self,
            "RunSeaJob",
            glue_job_name=SEA_JOB_NAME,
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
        )

        state_machine = sfn.StateMachine(
            self,
            "SeaStateMachine",
            definition_body=sfn.DefinitionBody.from_chainable(run_sea_job_task),
            timeout=Duration.minutes(15),
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
