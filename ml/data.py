"""Shared S3 access for the curated OMNIWeb OMNI2 hourly dataset."""

import io

import boto3
import pandas as pd
import pyarrow.parquet as pq


def load_omniweb(
    s3,
    bucket: str,
    prefix: str = "curated/omniweb_omni2_hourly/",
    start_year: int | None = None,
    end_year: int | None = None,
) -> pd.DataFrame:
    """Loads curated OMNIWeb Parquet files from S3 into one DataFrame,
    optionally restricted to a year range (each year is a separate
    Hive-partitioned object, so this only fetches what's needed)."""
    paginator = s3.get_paginator("list_objects_v2")
    keys = [
        obj["Key"]
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix)
        for obj in page.get("Contents", [])
        if obj["Key"].endswith(".parquet")
    ]
    if start_year is not None or end_year is not None:
        lo = start_year if start_year is not None else 0
        hi = end_year if end_year is not None else 9999
        keys = [key for key in keys if lo <= int(key.split("year=")[1].split("/")[0]) <= hi]

    frames = []
    for key in sorted(keys):
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        frames.append(pq.read_table(io.BytesIO(body)).to_pandas())
    df = pd.concat(frames, ignore_index=True)
    return df.sort_values("timestamp").reset_index(drop=True)


def make_s3_client(profile: str | None):
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    return session.client("s3")
