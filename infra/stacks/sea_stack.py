from aws_cdk import Stack
from constructs import Construct


class SeaStack(Stack):
    """Superposed epoch analysis pipeline.

    Owns: event catalog storage, Fargate/Glue SEA job, Step Functions trigger, results storage.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
