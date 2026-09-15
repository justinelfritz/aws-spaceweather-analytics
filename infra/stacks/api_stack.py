from aws_cdk import Stack
from constructs import Construct


class ApiStack(Stack):
    """Public API and live push layer.

    Owns: API Gateway REST + WebSocket APIs, Lambda resolvers, Cognito user pool (if used), usage plans.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
