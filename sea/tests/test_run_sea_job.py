import json
import sys
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run_sea_job import result_key, run  # noqa: E402

BUCKET = "curated-test"


def test_result_key_is_namespaced_by_field_and_normalization():
    assert result_key("dst_index", "raw") == "curated/sea_results/dst_index/raw.json"
    assert result_key("plasma_speed", "baseline_deviation") == "curated/sea_results/plasma_speed/baseline_deviation.json"


def _write_catalog(s3, events):
    catalog = [
        {"gst_id": event_id, "start_time": start_time.isoformat()}
        for event_id, start_time in events
    ]
    s3.put_object(
        Bucket=BUCKET,
        Key="curated/event_catalog/geomagnetic_storms.json",
        Body=json.dumps(catalog).encode("utf-8"),
    )


def _write_omniweb_year(s3, year: int, dst_by_hour_of_year: dict):
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq

    timestamps, values = [], []
    for hour_of_year, value in sorted(dst_by_hour_of_year.items()):
        timestamps.append(datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(hours=hour_of_year))
        values.append(value)

    table = pa.table({"timestamp": timestamps, "dst_index": values})
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    s3.put_object(Bucket=BUCKET, Key=f"curated/omniweb_omni2_hourly/year={year}/data.parquet", Body=buffer.getvalue())


@mock_aws
def test_run_produces_expected_aggregate_shape_and_values():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)

    event_time = datetime(2024, 6, 1, 12, tzinfo=timezone.utc)
    _write_catalog(s3, [("evt-1", event_time), ("evt-2", event_time + timedelta(days=10))])

    # A simple synthetic signature: value = 100 * offset from onset, for a
    # window wide enough to cover both events' full requested window.
    def hour_of_year(dt):
        return int((dt - datetime(dt.year, 1, 1, tzinfo=timezone.utc)).total_seconds() // 3600)

    dst_by_hour = {}
    for anchor in (event_time, event_time + timedelta(days=10)):
        base = hour_of_year(anchor)
        dst_by_hour.update({base + offset: 100 * offset for offset in range(-2, 3)})
    _write_omniweb_year(s3, 2024, dst_by_hour)

    args = Namespace(
        curated_bucket=BUCKET,
        field="dst_index",
        normalization="raw",
        hours_before=2,
        hours_after=2,
        baseline_start_offset=-2,
        baseline_end_offset=-1,
        percentiles=[50],
    )
    result = run(s3, args)

    assert result["field"] == "dst_index"
    assert result["normalization"] == "raw"
    assert result["event_count"] == 2
    assert [row["offset"] for row in result["offsets"]] == [-2, -1, 0, 1, 2]

    # Both events share the identical relative signature, so raw-mode mean
    # at each offset should exactly equal that offset's signature value.
    for row in result["offsets"]:
        assert row["n"] == 2
        assert row["mean"] == 100 * row["offset"]
        assert row["median"] == 100 * row["offset"]


@mock_aws
def test_main_writes_result_to_s3(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)

    event_time = datetime(2024, 6, 1, 12, tzinfo=timezone.utc)
    _write_catalog(s3, [("evt-1", event_time)])
    hour_of_year_base = int((event_time - datetime(2024, 1, 1, tzinfo=timezone.utc)).total_seconds() // 3600)
    _write_omniweb_year(s3, 2024, {hour_of_year_base + offset: offset for offset in range(-1, 2)})

    import run_sea_job

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_sea_job.py",
            "--curated-bucket", BUCKET,
            "--field", "dst_index",
            "--normalization", "raw",
            "--hours-before", "1",
            "--hours-after", "1",
        ],
    )

    run_sea_job.main()

    obj = s3.get_object(Bucket=BUCKET, Key="curated/sea_results/dst_index/raw.json")
    result = json.loads(obj["Body"].read())
    assert result["event_count"] == 1
