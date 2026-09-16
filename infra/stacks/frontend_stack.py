from pathlib import Path

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3_deployment
from constructs import Construct

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class FrontendStack(Stack):
    """Static dashboard hosting.

    Owns: S3 static site bucket, CloudFront distribution (origin access
    control, no public bucket access), cache invalidation on deploy.

    `frontend/dist` must already exist and be built against the deployed
    API's URL before `cdk deploy` — Vite bakes VITE_API_BASE_URL in at
    build time, it isn't something the running site can read at request
    time. See frontend/.env.example:

        cd frontend
        echo "VITE_API_BASE_URL=$(aws cloudformation describe-stacks \\
            --stack-name SpaceWeather-Api-<stage> --profile sw-bootstrap \\
            --query \"Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue\" \\
            --output text)" > .env
        npm run build
        cd ../infra && cdk deploy SpaceWeather-Frontend-<stage>
    """

    def __init__(self, scope: Construct, construct_id: str, stage: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Holds only rebuildable frontend build output (unlike the raw/
        # curated data buckets), so DESTROY is safe here and keeps
        # `cdk destroy` from leaving an orphaned bucket behind.
        site_bucket = s3.Bucket(
            self,
            "SiteBucket",
            bucket_name=f"space-weather-frontend-{stage}-{self.account}-{self.region}",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            # Cheapest edge footprint (US/Canada/Europe) -- a portfolio demo
            # doesn't need global edge coverage, matching the cost-conscious
            # choices made elsewhere (see StorageStack's lifecycle rules,
            # ApiStack's stage throttling instead of a usage plan).
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
        )

        # `distribution`/`distribution_paths` here is what gives us "cache
        # invalidation on frontend deploys" for free -- every `cdk deploy`
        # that changes the built assets invalidates the whole distribution
        # after upload, no separate script needed.
        s3_deployment.BucketDeployment(
            self,
            "DeploySite",
            sources=[s3_deployment.Source.asset(str(FRONTEND_DIST))],
            destination_bucket=site_bucket,
            distribution=distribution,
            distribution_paths=["/*"],
        )

        CfnOutput(self, "SiteUrl", value=f"https://{distribution.distribution_domain_name}")
        self.distribution = distribution
        self.site_bucket = site_bucket
