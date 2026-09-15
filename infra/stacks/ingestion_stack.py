from aws_cdk import Stack
from constructs import Construct


class IngestionStack(Stack):
    """Fetches, parses, and normalizes data from upstream feeds (NOAA SWPC, NASA OMNIWeb).

    Owns: ingestion Lambdas, EventBridge schedules, SQS dead-letter queues.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
