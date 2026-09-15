from aws_cdk import Stack
from constructs import Construct


class FrontendStack(Stack):
    """Static dashboard hosting.

    Owns: S3 static site bucket, CloudFront distribution, cache invalidation on deploy.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
