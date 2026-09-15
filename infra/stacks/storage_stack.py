from aws_cdk import Stack
from constructs import Construct


class StorageStack(Stack):
    """Durable and hot-path storage for space weather time series.

    Owns: S3 raw/curated buckets, Timestream database/tables, Glue catalog + crawler.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
