#!/usr/bin/env python3
"""One-time (or rare, e.g. annual) backfill of the OMNI2 historical hourly
dataset from NASA SPDF into S3.

For each requested year: fetches https://spdf.gsfc.nasa.gov/pub/data/omni/low_res_omni/omni2_<year>.dat,
lands the raw text untouched in the raw bucket, parses it against the
verified field layout in omni2_format.py (nulling out fill-value sentinels),
and writes a Hive-partitioned Parquet file to the curated bucket. Both
writes are keyed by year, so re-running is idempotent — it just overwrites
that year's objects.

Usage:
    python backfill_omniweb.py --raw-bucket ... --curated-bucket ... --profile sw-bootstrap
    python backfill_omniweb.py --raw-bucket ... --curated-bucket ... --start-year 2020 --dry-run
"""

import argparse
import io
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

from omni2_format import FIELDS, FILL_VALUES

OMNI2_BASE_URL = "https://spdf.gsfc.nasa.gov/pub/data/omni/low_res_omni"
FIRST_YEAR = 1963


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-bucket", required=True)
    parser.add_argument("--curated-bucket", required=True)
    parser.add_argument("--start-year", type=int, default=FIRST_YEAR)
    parser.add_argument("--end-year", type=int, default=datetime.now(timezone.utc).year)
    parser.add_argument("--profile", default=None, help="AWS profile to use (default: default credential chain)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report without writing to S3")
    return parser.parse_args()


def fetch_year_text(year: int, attempts: int = 3, backoff_seconds: float = 2.0) -> str:
    url = f"{OMNI2_BASE_URL}/omni2_{year}.dat"
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return response.read().decode("ascii")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(backoff_seconds * attempt)
    raise RuntimeError(f"failed to fetch {url} after {attempts} attempts: {last_error}")


def parse_line(line: str) -> dict:
    tokens = line.split()
    if len(tokens) != len(FIELDS):
        raise ValueError(f"expected {len(FIELDS)} fields, got {len(tokens)}: {line!r}")

    record = {}
    for field, token in zip(FIELDS, tokens):
        value = int(token) if field.kind == "int" else float(token)
        fill = FILL_VALUES.get(field.name)
        record[field.name] = None if fill is not None and value == fill else value

    record["timestamp"] = datetime(record["year"], 1, 1, tzinfo=timezone.utc) + timedelta(
        days=record["day"] - 1, hours=record["hour"]
    )
    record["kp"] = record["kp_raw"] / 10.0 if record["kp_raw"] is not None else None
    return record


def parse_year_text(text: str) -> list[dict]:
    return [parse_line(line) for line in text.splitlines() if line.strip()]


def records_to_table(records: list[dict]) -> pa.Table:
    column_names = [field.name for field in FIELDS] + ["timestamp", "kp"]
    columns = {name: [record[name] for record in records] for name in column_names}
    return pa.table(columns)


def table_to_parquet_bytes(table: pa.Table) -> bytes:
    buffer = io.BytesIO()
    # snappy, not zstd: the API's /historical Lambda resolver reads this
    # data using AWS's managed "AWSSDKPandas" layer to avoid bundling our
    # own pyarrow build, and that layer's pyarrow doesn't have the zstd
    # codec built in -- confirmed the hard way (ArrowNotImplementedError:
    # Support for codec 'zstd' not built, from a real deployed request).
    # snappy is universally supported and still shrinks this data plenty.
    pq.write_table(table, buffer, compression="snappy")
    return buffer.getvalue()


def backfill_year(s3, raw_bucket: str, curated_bucket: str, year: int, dry_run: bool = False) -> None:
    text = fetch_year_text(year)
    records = parse_year_text(text)

    raw_key = f"raw/omniweb_omni2_hourly/{year}.dat"
    curated_key = f"curated/omniweb_omni2_hourly/year={year}/data.parquet"

    if dry_run:
        print(f"[dry-run] {year}: {len(records)} records -> {raw_key}, {curated_key}")
        return

    s3.put_object(Bucket=raw_bucket, Key=raw_key, Body=text.encode("ascii"), ContentType="text/plain")

    parquet_bytes = table_to_parquet_bytes(records_to_table(records))
    s3.put_object(Bucket=curated_bucket, Key=curated_key, Body=parquet_bytes, ContentType="application/octet-stream")

    print(
        f"{year}: {len(records)} records -> "
        f"s3://{raw_bucket}/{raw_key} and s3://{curated_bucket}/{curated_key} "
        f"({len(parquet_bytes)} bytes parquet)"
    )


def main():
    args = parse_args()
    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    s3 = session.client("s3")

    for year in range(args.start_year, args.end_year + 1):
        backfill_year(s3, args.raw_bucket, args.curated_bucket, year, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
