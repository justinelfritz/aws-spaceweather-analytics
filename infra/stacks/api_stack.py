from aws_cdk import Stack
from constructs import Construct


class ApiStack(Stack):
    """Public API layer.

    Owns: API Gateway REST API, Lambda resolvers, Cognito user pool (if used), usage plans.
    No WebSocket API — descoped along with the dashboard's live-readings view,
    see space-weather-platform-todo.md section 7.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
