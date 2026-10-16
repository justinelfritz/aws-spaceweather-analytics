#!/usr/bin/env python3
"""Pull SuperMAG's substorm onset catalog into S3 -- a second event catalog
SEA can align time-series segments against, alongside the existing DONKI
geomagnetic storm catalog (see backfill_donki_gst.py).

SuperMAG distributes five onset catalogues (different detection techniques):
newell (2011, the default here -- most-cited/field-standard), forsyth (2015,
"SOPHIE"), ohtani (2020), frey, and liou. Only one is fetched per run. Each
onset record is just a timestamp (minute resolution, no seconds) plus
location (magnetic local time/latitude, geographic lon/lat) -- there is no
storm-intensity/magnitude field in this catalog, unlike DONKI's `max_kp`.
`liou` is the one catalogue whose `mlt` field SuperMAG serves in degrees
rather than hours; this script converts it (divides by 15) so all catalogues
expose `mlt` in the same units.

Endpoint confirmed 2026-10-15 by reading GFZ's `swvo` library source
(swvo/io/substorms/supermag.py, Apache-2.0) -- JHU/APL's own official
`supermag-api.py` client doesn't cover this endpoint at all, only station
inventory/raw magnetometer data/SME-SML-SMU indices.

Requires a SuperMAG registered username (free registration at
supermag.jhuapl.edu), passed as the `user` query parameter -- treat it as a
credential: set it via the SUPERMAG_USERNAME env var, never pass it as a
literal on a shared command line or log it (this script never prints it).

Real coverage for the `newell` catalogue, probed against the live API
2026-10-15 (SuperMAG's own documented "1969 onward" claim does not hold up):
zero events for every year 1960-1975, then a full year of data starting
exactly 1976-01-01. The *end* is also well short of "now" -- the response
itself carries a "Substorm database generated on ... 2023-10-31" notice,
and the last real event actually present was 2023-07-29 (2024+ returns a
well-formed response with the expected header and zero data rows, not an
error -- this is a real processing/revision lag on SuperMAG's end, same
kind of publication lag already documented for OMNIWeb's own data in this
project, not a bug in this script). `--start-year` defaults to 1976
accordingly; `--end-year` still defaults to the current year since newer
data could appear on SuperMAG's side without any change needed here --
getting 0 events for recent years is expected, not a sign of a broken
run.

Lands each year's raw ASCII response in S3 (keyed by catalog + year, for
provenance) and writes a normalized, flattened catalog to the curated
bucket at a fixed key. Re-running overwrites both with the latest data --
safe to re-run periodically, but note that re-running with a *different*
--catalog overwrites the curated file with that catalog's events instead of
merging, since only one catalogue is tracked at a time.

Usage:
    python backfill_supermag_substorms.py --raw-bucket ... --curated-bucket ... --profile sw-bootstrap
    python backfill_supermag_substorms.py --raw-bucket ... --curated-bucket ... --start-year 2020 --dry-run
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import boto3

SUPERMAG_URL = "https://supermag.jhuapl.edu/lib/services/"
FIRST_YEAR = 1976  # real coverage start for `newell`, confirmed by probing the live API 2026-10-15 (see module docstring)
VALID_CATALOGS = {"newell", "forsyth", "ohtani", "frey", "liou"}
DEFAULT_CATALOG = "newell"

# SuperMAG's self-documented ASCII table header for the substorm_list response.
_HEADER_TOKENS = ("<year>", "<month>", "<day>", "<hour>", "<min>", "<mlt>", "<mlat>", "<glon>", "<glat>")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-bucket", required=True)
    parser.add_argument("--curated-bucket", required=True)
    parser.add_argument("--catalog", default=DEFAULT_CATALOG, choices=sorted(VALID_CATALOGS))
    parser.add_argument("--start-year", type=int, default=FIRST_YEAR)
    parser.add_argument("--end-year", type=int, default=datetime.now(timezone.utc).year)
    parser.add_argument(
        "--user",
        default=os.environ.get("SUPERMAG_USERNAME"),
        help="SuperMAG registered username (default: SUPERMAG_USERNAME env var)",
    )
    parser.add_argument("--profile", default=None, help="AWS profile to use (default: default credential chain)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and report without writing to S3")
    return parser.parse_args()


def fetch_substorm_year_text(catalog: str, year: int, user: str, attempts: int = 4, backoff_seconds: float = 1.0) -> str:
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    # SuperMAG includes events exactly at both request boundaries -- end at
    # the last second of the year so a New Year's event isn't double-counted
    # across two adjacent per-year requests.
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
    params = {
        "service": "substorms",
        "downloadtype": "substorm_list",
        "user": user,
        "fmt": "ascii",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "list": catalog,
    }
    url = f"{SUPERMAG_URL}?{urlencode(params)}"

    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                text = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            text = None

        if text is not None:
            stripped = text.strip()
            if stripped.startswith("ERROR"):
                # A real API error (e.g. bad username) -- not transient, don't retry.
                raise RuntimeError(f"SuperMAG API error fetching {catalog} {year}: {stripped.splitlines()[0]}")
            if stripped:
                return text
            last_error = RuntimeError(f"SuperMAG returned an empty response for {catalog} {year}")

        if attempt < attempts:
            time.sleep(backoff_seconds * 2 ** (attempt - 1))

    raise RuntimeError(f"failed to fetch SuperMAG {catalog} {year} after {attempts} attempts: {last_error}")


def parse_substorm_text(text: str, catalog: str) -> list[dict]:
    lines = text.strip().splitlines()
    header_index = next(
        (index for index, line in enumerate(lines) if tuple(line.strip().lower().split()) == _HEADER_TOKENS),
        None,
    )
    if header_index is None:
        raise ValueError(f"SuperMAG {catalog} substorm response does not contain the expected table header")

    events = []
    for line_number, line in enumerate(lines[header_index + 1 :], start=header_index + 2):
        stripped = line.strip()
        if not stripped:
            continue
        fields = stripped.split()
        if len(fields) != len(_HEADER_TOKENS):
            raise ValueError(
                f"malformed SuperMAG {catalog} substorm record on line {line_number}: "
                f"expected {len(_HEADER_TOKENS)} fields, found {len(fields)}: {line!r}"
            )

        year, month, day, hour, minute, mlt, mlat, glon, glat = fields
        onset = datetime(int(year), int(month), int(day), int(hour), int(minute), tzinfo=timezone.utc)
        mlt_value = float(mlt)
        if catalog == "liou":
            # SuperMAG labels this field MLT but serves Liou's original values in
            # magnetic-longitude degrees, not hours -- normalize by 15 deg/hour.
            mlt_value /= 15

        events.append(
            {
                "onset_time": onset.isoformat(),
                "mlt": mlt_value,
                "mlat": float(mlat),
                "glon": float(glon),
                "glat": float(glat),
            }
        )
    return events


def build_catalog(events: list[dict], catalog: str) -> list[dict]:
    entries = []
    for event in events:
        entry = dict(event)
        entry["catalog"] = catalog
        entry["substorm_id"] = f"SUPERMAG-{catalog.upper()}-{event['onset_time']}"
        entries.append(entry)
    entries.sort(key=lambda e: e["onset_time"])
    return entries


def main():
    args = parse_args()
    if not args.user:
        raise SystemExit("SuperMAG username required: pass --user or set the SUPERMAG_USERNAME env var")

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    s3 = session.client("s3")

    all_events = []
    for year in range(args.start_year, args.end_year + 1):
        text = fetch_substorm_year_text(args.catalog, year, args.user)
        events = parse_substorm_text(text, args.catalog)
        all_events.extend(events)

        raw_key = f"raw/supermag_substorms/{args.catalog}/{year}.txt"
        if args.dry_run:
            print(f"[dry-run] {args.catalog} {year}: {len(events)} events -> {raw_key}")
        else:
            s3.put_object(Bucket=args.raw_bucket, Key=raw_key, Body=text.encode("utf-8"), ContentType="text/plain")
            print(f"{args.catalog} {year}: {len(events)} events -> s3://{args.raw_bucket}/{raw_key}")

    catalog = build_catalog(all_events, args.catalog)
    curated_key = "curated/event_catalog/substorms.json"

    if args.dry_run:
        print(f"[dry-run] {len(catalog)} normalized events -> {curated_key}")
        return

    s3.put_object(
        Bucket=args.curated_bucket,
        Key=curated_key,
        Body=json.dumps(catalog, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"{len(catalog)} normalized events -> s3://{args.curated_bucket}/{curated_key}")


if __name__ == "__main__":
    main()
