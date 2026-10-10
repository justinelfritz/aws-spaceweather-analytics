import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from load_data import load_omniweb_series  # noqa: E402

BUCKET = "curated-test"


@mock_aws
def test_bx_gsm_and_bx_gse_both_read_the_single_bx_column():
    # GSE and GSM share the same X-axis by definition, so OMNI2 only has one
    # physical Bx column (bx_gse_gsm) -- both queryable fields should read
    # it and return identical values.
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)

    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    table = pa.table({"timestamp": [base, base + timedelta(hours=1)], "bx_gse_gsm": [1.5, -2.5]})
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    s3.put_object(Bucket=BUCKET, Key="curated/omniweb_omni2_hourly/year=2024/data.parquet", Body=buffer.getvalue())

    gsm_series = load_omniweb_series(s3, BUCKET, field="bx_gsm", years=[2024])
    gse_series = load_omniweb_series(s3, BUCKET, field="bx_gse", years=[2024])

    assert gsm_series == gse_series == {base: 1.5, base + timedelta(hours=1): -2.5}
