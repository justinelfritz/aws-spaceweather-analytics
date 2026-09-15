import aws_cdk as core
import aws_cdk.assertions as assertions

from stacks.storage_stack import StorageStack


def test_storage_stack_synthesizes():
    app = core.App()
    stack = StorageStack(app, "storage")
    assertions.Template.from_stack(stack)
