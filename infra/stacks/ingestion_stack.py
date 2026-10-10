from pathlib import Path

from aws_cdk import Duration, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cloudwatch_actions
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as sns_subscriptions
from aws_cdk import aws_sqs as sqs
from constructs import Construct

LAMBDAS_ROOT = Path(__file__).resolve().parents[2] / "lambdas"


class IngestionStack(Stack):
    """Fetches, parses, and normalizes data from upstream feeds (NOAA SWPC, NASA OMNIWeb).

    Owns: ingestion Lambdas, EventBridge schedules, SQS dead-letter queues.
    """

    def __init__(self, scope: Construct, construct_id: str, raw_bucket: s3.IBucket, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        alerts_topic = sns.Topic(self, "IngestionAlertsTopic")
        alerts_topic.add_subscription(
            sns_subscriptions.EmailSubscription("justin.elfritz@protonmail.com")
        )

        dlq = sqs.Queue(
            self,
            "SwpcLiveFeedsDlq",
            retention_period=Duration.days(14),
        )

        swpc_live_feeds_fn = lambda_.Function(
            self,
            "SwpcLiveFeedsFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="ingestion.handler.handler",
            code=lambda_.Code.from_asset(
                str(LAMBDAS_ROOT),
                # Bundles the whole lambdas/ root (not just ingestion/) so the
                # zip keeps the "ingestion" package prefix the handler path
                # (ingestion.handler.handler) needs -- but this function never
                # imports anything from api/, so excluding it keeps that
                # unrelated code (and any changes to it) out of both this
                # function's deployed package and its asset hash. Confirmed
                # the hard way: editing lambdas/api/sea_results.py alone
                # showed up as a diff here despite ingestion/handler.py being
                # untouched -- the same class of bug api_stack.py's
                # SeaPipelineLayer asset had for the same reason.
                exclude=[".venv", "**/tests", "**/__pycache__", "requirements-dev.txt", "api"],
            ),
            timeout=Duration.seconds(30),
            memory_size=256,
            environment={"RAW_BUCKET_NAME": raw_bucket.bucket_name},
        )
        raw_bucket.grant_write(swpc_live_feeds_fn)

        # All four live SWPC feeds (Kp 3-hr, Kp 1-min, RTSW plasma, RTSW mag)
        # are cheap HTTP GETs on the same cadence, so one schedule drives one
        # Lambda that fetches all of them per invocation — see the "single
        # Lambda" decision recorded in docs/data-sources.md's cadence table.
        schedule_rule = events.Rule(
            self,
            "SwpcLiveFeedsSchedule",
            schedule=events.Schedule.rate(Duration.minutes(2)),
        )
        schedule_rule.add_target(
            targets.LambdaFunction(
                swpc_live_feeds_fn,
                dead_letter_queue=dlq,
                retry_attempts=2,
            )
        )

        dlq_depth_alarm = cloudwatch.Alarm(
            self,
            "SwpcLiveFeedsDlqDepthAlarm",
            metric=dlq.metric_approximate_number_of_messages_visible(),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            alarm_description="One or more SWPC live feed ingestion invocations failed after retries and landed in the DLQ.",
        )
        dlq_depth_alarm.add_alarm_action(cloudwatch_actions.SnsAction(alerts_topic))

        self.alerts_topic = alerts_topic
