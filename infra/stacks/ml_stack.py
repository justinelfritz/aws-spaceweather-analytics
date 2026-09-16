from pathlib import Path

from aws_cdk import Duration, Size, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as sfn_tasks
from constructs import Construct

ML_ROOT = Path(__file__).resolve().parents[2] / "ml"
TRAINING_OUTPUT_PREFIX = "code/ml-training-output"


class MlStack(Stack):
    """Kp(t+3h)/Dst(t+3h) forecasting: a SageMaker Training Job (running the
    same ml/train.py already validated locally, see ml/experiments/log.jsonl
    for the local run) + Step Functions trigger -- mirrors the SEA stack's
    pattern (local script -> containerized AWS managed-service job).

    Uses a Training Job rather than a Processing Job: this CDK version's
    Step Functions integration only has a native, completion-aware task
    for Training Jobs (SageMakerCreateTrainingJob supports
    IntegrationPattern.RUN_JOB / ".sync" natively); Processing Jobs would
    need a hand-rolled poll loop (DescribeProcessingJob + Wait + Choice)
    since there's no dedicated construct for them here.

    The container is a plain "bring your own container": SageMaker runs
    `docker run <image> train`, and ml/sagemaker_entrypoint.sh ignores that
    argument and reads its config from environment variables set below,
    then runs ml/train.py exactly as it runs locally. Model artifacts land
    in /opt/ml/model, which SageMaker automatically tars and uploads to
    OutputDataConfig's S3 location -- no explicit upload code needed.
    """

    def __init__(self, scope: Construct, construct_id: str, curated_bucket: s3.IBucket, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        image_asset = ecr_assets.DockerImageAsset(self, "MlTrainingImage", directory=str(ML_ROOT))

        train_task = sfn_tasks.SageMakerCreateTrainingJob(
            self,
            "TrainModel",
            training_job_name=sfn.JsonPath.format("space-weather-ml-{}", sfn.JsonPath.string_at("$$.Execution.Name")),
            algorithm_specification=sfn_tasks.AlgorithmSpecification(
                training_image=sfn_tasks.DockerImage.from_ecr_repository(image_asset.repository, image_asset.image_tag),
            ),
            output_data_config=sfn_tasks.OutputDataConfig(
                s3_output_location=sfn_tasks.S3Location.from_bucket(curated_bucket, TRAINING_OUTPUT_PREFIX),
            ),
            resource_config=sfn_tasks.ResourceConfig(
                instance_count=1,
                # Smallest general-purpose SageMaker training instance --
                # this dataset/model trains in well under a minute locally,
                # so there's no reason to pay for more.
                instance_type=ec2.InstanceType.of(ec2.InstanceClass.M5, ec2.InstanceSize.LARGE),
                volume_size=Size.gibibytes(5),
            ),
            stopping_condition=sfn_tasks.StoppingCondition(max_runtime=Duration.minutes(30)),
            environment={"CURATED_BUCKET": curated_bucket.bucket_name},
            # Default integration pattern is REQUEST_RESPONSE (fire the
            # CreateTrainingJob API call and immediately report success) --
            # RUN_JOB switches to the ".sync" resource ARN so the state
            # machine actually waits for training to finish and fails the
            # execution if the job fails. Same reasoning as GlueStartJobRun
            # in sea_stack.py.
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
        )

        curated_bucket.grant_read_write(train_task.role)
        image_asset.repository.grant_pull(train_task.role)
        # SageMaker Training Jobs write their own CloudWatch logs using the
        # job's execution role (unlike Glue, which can rely on its managed
        # service role policy for this) -- without this, the job runs but
        # its logs never show up.
        train_task.role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/sagemaker/*"],
            )
        )

        state_machine = sfn.StateMachine(
            self,
            "MlStateMachine",
            definition_body=sfn.DefinitionBody.from_chainable(train_task),
            timeout=Duration.minutes(45),
        )

        # Weekly retraining, same cadence/rationale as the SEA job: model
        # quality doesn't need to be fresher than that.
        schedule_rule = events.Rule(self, "MlWeeklySchedule", schedule=events.Schedule.rate(Duration.days(7)))
        schedule_rule.add_target(targets.SfnStateMachine(state_machine))

        self.state_machine = state_machine
