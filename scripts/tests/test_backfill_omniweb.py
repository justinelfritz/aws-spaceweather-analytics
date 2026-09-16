import io
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3
import pyarrow.parquet as pq
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backfill_omniweb import backfill_year, parse_line, parse_year_text, records_to_table, table_to_parquet_bytes  # noqa: E402

# Real line from omni2_2024.dat (verified field-by-field against the format
# spec before this parser was written) and a real all-fill line from
# omni2_2026.dat (a not-yet-occurred date the source pre-pads through Dec 31).
REAL_LINE = (
    "2024   1  0 2596 51 52  60  36   5.3   5.1  -7.3 322.9   4.0  -3.0  -0.6  -2.8  -1.3   0.1   1.5   0.3   0.4"
    "   1.5   31114.   7.4  306.  -2.3   2.8 0.042  1.35    4691.   0.4    2.   0.3   1.3 0.007   0.40   1.75   7.9"
    "  7  55     0   20 999999.99 99999.99 99999.99 99999.99 99999.99 99999.99  0   3 131.2   0.3    -9    11  5.0"
)
ALL_FILL_LINE = (
    "2026 365 23 9999 99 99 999 999 999.9 999.9 999.9 999.9 999.9 999.9 999.9 999.9 999.9 999.9 999.9 999.9 999.9"
    " 999.9 9999999. 999.9 9999. 999.9 999.9 9.999 99.99 9999999. 999.9 9999. 999.9 999.9 9.999 999.99 999.99 999.9"
    " 99 999 99999 9999 999999.99 99999.99 99999.99 99999.99 99999.99 99999.99  0 999 999.9 999.9 99999 99999 99.9"
)


def test_parse_line_decodes_real_data_row_with_no_nulls():
    record = parse_line(REAL_LINE)
    assert record["year"] == 2024
    assert record["day"] == 1
    assert record["hour"] == 0
    assert record["bz_gsm"] == -1.3
    assert record["proton_density"] == 7.4
    assert record["plasma_speed"] == 306.0
    assert record["dst_index"] == 0
    assert record["kp_raw"] == 7
    assert record["kp"] == 0.7
    assert record["timestamp"] == datetime(2024, 1, 1, 0, tzinfo=timezone.utc)


def test_parse_line_nulls_out_fill_sentinels_but_keeps_meaningful_zero_flag():
    record = parse_line(ALL_FILL_LINE)
    # Real time words are never fill values, even on an all-fill row.
    assert record["year"] == 2026
    assert record["day"] == 365
    assert record["hour"] == 23
    assert record["timestamp"] == datetime(2026, 12, 31, 23, tzinfo=timezone.utc)
    # Every physical measurement should be nulled out.
    assert record["bz_gsm"] is None
    assert record["proton_density"] is None
    assert record["plasma_speed"] is None
    assert record["dst_index"] is None
    assert record["kp_raw"] is None
    assert record["kp"] is None
    # flux_flag's fill-looking "0" is a real, meaningful flag value, not a
    # missing-data sentinel — must NOT be nulled.
    assert record["flux_flag"] == 0


def test_parse_year_text_skips_blank_lines():
    text = f"{REAL_LINE}\n\n{ALL_FILL_LINE}\n"
    records = parse_year_text(text)
    assert len(records) == 2


def test_records_to_table_and_parquet_round_trip():
    records = [parse_line(REAL_LINE), parse_line(ALL_FILL_LINE)]
    table = records_to_table(records)
    parquet_bytes = table_to_parquet_bytes(table)

    read_back = pq.read_table(io.BytesIO(parquet_bytes))
    assert read_back.num_rows == 2
    assert read_back.column("dst_index").to_pylist() == [0, None]
    assert read_back.column("kp").to_pylist() == [0.7, None]


@mock_aws
def test_backfill_year_writes_raw_and_curated_objects():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="raw-test")
    s3.create_bucket(Bucket="curated-test")

    import backfill_omniweb

    original_fetch = backfill_omniweb.fetch_year_text
    backfill_omniweb.fetch_year_text = lambda year, **kwargs: f"{REAL_LINE}\n{ALL_FILL_LINE}\n"
    try:
        backfill_year(s3, "raw-test", "curated-test", 2024)
    finally:
        backfill_omniweb.fetch_year_text = original_fetch

    raw_obj = s3.get_object(Bucket="raw-test", Key="raw/omniweb_omni2_hourly/2024.dat")
    assert REAL_LINE in raw_obj["Body"].read().decode("ascii")

    curated_obj = s3.get_object(Bucket="curated-test", Key="curated/omniweb_omni2_hourly/year=2024/data.parquet")
    table = pq.read_table(io.BytesIO(curated_obj["Body"].read()))
    assert table.num_rows == 2
