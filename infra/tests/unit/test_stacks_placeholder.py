import json

import aws_cdk as core
import aws_cdk.assertions as assertions
from aws_cdk.assertions import Match

from stacks.api_stack import ApiStack
from stacks.frontend_stack import FrontendStack
from stacks.ingestion_stack import IngestionStack
from stacks.ml_stack import MlStack
from stacks.sea_stack import SeaStack
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


def test_sea_stack_synthesizes():
    app = core.App()
    storage = StorageStack(app, "storage", stage="dev")
    sea = SeaStack(app, "sea", curated_bucket=storage.curated_bucket)
    template = assertions.Template.from_stack(sea)
    # Still exactly one Glue job *definition* -- the Map state below invokes
    # it 54 times (once per field x normalization combination), it doesn't
    # create 54 separate job resources.
    template.resource_count_is("AWS::Glue::Job", 1)
    template.resource_count_is("AWS::StepFunctions::StateMachine", 1)
    template.resource_count_is("AWS::Events::Rule", 1)
    template.has_resource_properties("AWS::Glue::Job", {"Command": {"Name": "pythonshell"}})
    # Concurrent Glue runs of the same job are capped at 1 by default --
    # must be raised, or the Map state's concurrent GlueStartJobRun calls
    # would mostly fail with ConcurrentRunsExceededException.
    template.has_resource_properties("AWS::Glue::Job", {"ExecutionProperty": {"MaxConcurrentRuns": 8}})

    # The state machine's DefinitionString is an Fn::Join of raw string
    # fragments interleaved with intrinsic-function refs (e.g. {"Ref": ...}
    # for the partition) -- reassemble just the string parts to get the
    # literal ASL JSON text, rather than re-running the whole template
    # through json.dumps (which would double-escape the quotes already
    # inside that embedded JSON string and break substring matching).
    state_machine_resource = next(
        resource
        for resource in template.to_json()["Resources"].values()
        if resource["Type"] == "AWS::StepFunctions::StateMachine"
    )
    join_parts = state_machine_resource["Properties"]["DefinitionString"]["Fn::Join"][1]
    definition = "".join(part for part in join_parts if isinstance(part, str))

    assert '"Type":"Map"' in definition
    assert '"ItemsPath":"$.combinations"' in definition
    assert '"MaxConcurrency":8' in definition
    assert definition.count('"field":"kp"') >= 1
    assert definition.count('"normalization":"normalized_amplitude"') == 18  # once per field
    assert '"--field.$":"$.field"' in definition
    assert '"--normalization.$":"$.normalization"' in definition


def test_ml_stack_synthesizes():
    app = core.App()
    storage = StorageStack(app, "storage", stage="dev")
    ml = MlStack(app, "ml", curated_bucket=storage.curated_bucket)
    template = assertions.Template.from_stack(ml)
    template.resource_count_is("AWS::StepFunctions::StateMachine", 1)
    template.resource_count_is("AWS::Events::Rule", 1)
    # DefinitionString is an Fn::Join of parts, not a plain string, so check
    # the fully-rendered template JSON instead of matching the property directly.
    assert "createTrainingJob.sync" in json.dumps(template.to_json())


def test_api_stack_synthesizes():
    app = core.App()
    storage = StorageStack(app, "storage", stage="dev")
    api = ApiStack(app, "api", curated_bucket=storage.curated_bucket)
    template = assertions.Template.from_stack(api)
    template.resource_count_is("AWS::ApiGateway::RestApi", 1)
    template.resource_count_is("AWS::Lambda::Function", 4)
    # One GET per resolver (/historical, /events, /sea/{field}/{normalization},
    # /forecast-skill/{target}/{error_type}); OPTIONS methods also show up
    # per-resource from default_cors_preflight_options, so filter rather
    # than assert a total count that depends on CDK's CORS-resource wiring.
    get_methods = template.find_resources("AWS::ApiGateway::Method", {"Properties": {"HttpMethod": "GET"}})
    assert len(get_methods) == 4
    template.has_resource_properties(
        "AWS::ApiGateway::Method", {"HttpMethod": "GET", "AuthorizationType": "NONE"}
    )
    template.has_resource_properties(
        "AWS::ApiGateway::Stage",
        {"StageName": "v1", "MethodSettings": [{"ThrottlingRateLimit": 10, "ThrottlingBurstLimit": 20}]},
    )


def test_frontend_stack_synthesizes():
    # Requires frontend/dist to exist (`npm run build` in frontend/) --
    # BucketDeployment's asset is bundled at synth time.
    app = core.App()
    frontend = FrontendStack(app, "frontend", stage="dev")
    template = assertions.Template.from_stack(frontend)
    template.resource_count_is("AWS::S3::Bucket", 1)
    template.resource_count_is("AWS::CloudFront::Distribution", 1)
    template.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": {
                "DefaultRootObject": "index.html",
                "PriceClass": "PriceClass_100",
            }
        },
    )
    # Origin access control, not a public bucket -- confirm the bucket
    # policy grants CloudFront (among the deployment Lambda's own grants),
    # never "*".
    template.has_resource_properties(
        "AWS::S3::BucketPolicy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [Match.object_like({"Principal": {"Service": "cloudfront.amazonaws.com"}})]
                )
            }
        },
    )
