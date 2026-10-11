import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from load_data import load_event_catalog, load_omniweb_series  # noqa: E402

BUCKET = "curated-test"


def _write_catalog(s3, entries):
    s3.put_object(
        Bucket=BUCKET,
        Key="curated/event_catalog/geomagnetic_storms.json",
        Body=json.dumps(entries).encode("utf-8"),
    )


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


@mock_aws
def test_load_event_catalog_filters_by_storm_class():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    _write_catalog(
        s3,
        [
            {"gst_id": "evt-g1", "start_time": base.isoformat(), "storm_class": "G1"},
            {"gst_id": "evt-g2", "start_time": (base + timedelta(days=1)).isoformat(), "storm_class": "G2"},
            {"gst_id": "evt-g4", "start_time": (base + timedelta(days=2)).isoformat(), "storm_class": "G4"},
        ],
    )

    minor = load_event_catalog(s3, BUCKET, storm_classes={"G1", "G2"})

    assert {event.event_id for event in minor} == {"evt-g1", "evt-g2"}


@mock_aws
def test_load_event_catalog_combines_storm_class_and_time_bounds():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    _write_catalog(
        s3,
        [
            {"gst_id": "evt-early-g1", "start_time": base.isoformat(), "storm_class": "G1"},
            {"gst_id": "evt-late-g1", "start_time": (base + timedelta(days=10)).isoformat(), "storm_class": "G1"},
            {"gst_id": "evt-late-g4", "start_time": (base + timedelta(days=10)).isoformat(), "storm_class": "G4"},
        ],
    )

    events = load_event_catalog(
        s3, BUCKET, min_start_time=base + timedelta(days=5), storm_classes={"G1"}
    )

    assert {event.event_id for event in events} == {"evt-late-g1"}


@mock_aws
def test_load_event_catalog_storm_classes_none_is_unfiltered():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    _write_catalog(
        s3,
        [
            {"gst_id": "evt-g1", "start_time": base.isoformat(), "storm_class": "G1"},
            {"gst_id": "evt-g5", "start_time": (base + timedelta(days=1)).isoformat(), "storm_class": "G5"},
        ],
    )

    events = load_event_catalog(s3, BUCKET)

    assert {event.event_id for event in events} == {"evt-g1", "evt-g5"}
