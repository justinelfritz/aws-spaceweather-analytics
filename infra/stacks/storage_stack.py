from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_glue as glue
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct


class StorageStack(Stack):
    """Durable storage for space weather time series.

    Owns: S3 raw/curated buckets, Glue catalog + crawler. No hot/live-query
    store (Timestream) — descoped along with the dashboard's live-readings
    view, see space-weather-platform-todo.md section 4.
    """

    def __init__(self, scope: Construct, construct_id: str, stage: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Raw landing zone — untouched ingestion output, one object per feed
        # per ingestion tick (see lambdas/ingestion/handler.py for the key
        # scheme: raw/<feed>/<yyyy>/<mm>/<dd>/<HHMM>.json). Standard for 30
        # days, then Infrequent Access, then Glacier — raw JSON is cheap to
        # re-derive curated data from but rarely re-read once it ages out of
        # the "did something break recently" window.
        self.raw_bucket = s3.Bucket(
            self,
            "RawLandingBucket",
            bucket_name=f"space-weather-raw-{stage}-{self.account}-{self.region}",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="raw-transition-to-cheaper-storage",
                    enabled=True,
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(30),
                        ),
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER,
                            transition_after=Duration.days(90),
                        ),
                    ],
                )
            ],
        )

        # Curated layer: empty for now. Populating it (raw JSON -> normalized
        # Parquet, deduplicated to each RTSW feed's `active` source, one
        # schema per feed) is a separate transform job, not yet built.
        self.curated_bucket = s3.Bucket(
            self,
            "CuratedBucket",
            bucket_name=f"space-weather-curated-{stage}-{self.account}-{self.region}",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # Glue Data Catalog + crawler over the raw bucket, for ad hoc Athena
        # querying. On-demand only (no schedule) — this is a debugging/
        # exploration tool, not a recurring job, so it only costs anything
        # when actually invoked.
        raw_database_name = f"space_weather_raw_{stage}"
        glue_database = glue.CfnDatabase(
            self,
            "RawDataCatalog",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=raw_database_name,
                description="Ad hoc Athena access to the raw SWPC ingestion landing zone.",
            ),
        )

        crawler_role = iam.Role(
            self,
            "RawDataCrawlerRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSGlueServiceRole"),
            ],
        )
        self.raw_bucket.grant_read(crawler_role)

        self.raw_data_crawler = glue.CfnCrawler(
            self,
            "RawDataCrawler",
            name=f"space-weather-raw-{stage}",
            role=crawler_role.role_arn,
            database_name=raw_database_name,
            targets=glue.CfnCrawler.TargetsProperty(
                s3_targets=[glue.CfnCrawler.S3TargetProperty(path=f"s3://{self.raw_bucket.bucket_name}/raw/")]
            ),
        )
        self.raw_data_crawler.add_resource_dependency(glue_database)
