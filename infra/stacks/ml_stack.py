from aws_cdk import Stack
from constructs import Construct


class MlStack(Stack):
    """ML forecasting pipeline.

    Owns: SageMaker training/processing jobs, model registry, batch transform or serverless inference, retraining schedule.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
