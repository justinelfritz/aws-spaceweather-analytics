import aws_cdk as core
import aws_cdk.assertions as assertions

from stacks.ingestion_stack import IngestionStack
from stacks.storage_stack import StorageStack


def test_storage_stack_synthesizes():
    app = core.App()
    stack = StorageStack(app, "storage", stage="dev")
    template = assertions.Template.from_stack(stack)
    template.resource_count_is("AWS::S3::Bucket", 2)
    template.resource_count_is("AWS::Glue::Database", 1)
    template.resource_count_is("AWS::Glue::Crawler", 1)


def test_ingestion_stack_synthesizes():
    app = core.App()
    storage = StorageStack(app, "storage", stage="dev")
    ingestion = IngestionStack(app, "ingestion", raw_bucket=storage.raw_bucket)
    template = assertions.Template.from_stack(ingestion)
    template.resource_count_is("AWS::Lambda::Function", 1)
    template.resource_count_is("AWS::Events::Rule", 1)
    template.resource_count_is("AWS::SQS::Queue", 1)
    template.resource_count_is("AWS::CloudWatch::Alarm", 1)
