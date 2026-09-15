#!/usr/bin/env python3
import os

import aws_cdk as cdk
from aws_cdk import Tags

from stacks.ingestion_stack import IngestionStack
from stacks.storage_stack import StorageStack
from stacks.sea_stack import SeaStack
from stacks.ml_stack import MlStack
from stacks.api_stack import ApiStack
from stacks.frontend_stack import FrontendStack

app = cdk.App()

# Selected at synth/deploy time: `cdk deploy -c stage=prod` (defaults to "dev").
stage = app.node.try_get_context("stage") or "dev"

env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    region=os.getenv("CDK_DEFAULT_REGION"),
)


def stack_name(name: str) -> str:
    return f"SpaceWeather-{name}-{stage}"


storage = StorageStack(app, stack_name("Storage"), env=env)
ingestion = IngestionStack(app, stack_name("Ingestion"), env=env)
sea = SeaStack(app, stack_name("Sea"), env=env)
ml = MlStack(app, stack_name("Ml"), env=env)
api = ApiStack(app, stack_name("Api"), env=env)
frontend = FrontendStack(app, stack_name("Frontend"), env=env)

for stack in (storage, ingestion, sea, ml, api, frontend):
    Tags.of(stack).add("Project", "space-weather")
    Tags.of(stack).add("Stage", stage)

app.synth()
