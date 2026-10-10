#!/usr/bin/env python3
"""Runs the SEA pipeline (alignment -> normalization -> aggregation) for one
OMNIWeb field against the DONKI event catalog, and writes the aggregated
result to S3 for the dashboard to read.

Packaged as an AWS Glue Python shell job (see infra/stacks/sea_stack.py) --
runs identically here, locally, or inside Glue, since it's a plain argparse
script with no Glue-specific libraries required (Glue Python shell jobs pass
`--key value` job parameters through to sys.argv either way).

Usage:
    python run_sea_job.py --curated-bucket ... --field dst_index \
        --normalization raw --hours-before 24 --hours-after 72 \
        --profile sw-bootstrap
"""

import argparse
import json
from datetime import datetime, timezone

import boto3

from aggregation import aggregate_events
from alignment import align_events
from load_data import load_event_catalog, load_omniweb_series
from normalization import STRATEGIES


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--curated-bucket", required=True)
    parser.add_argument("--field", required=True, help="OMNIWeb curated column to run SEA on, e.g. dst_index")
    parser.add_argument("--normalization", choices=list(STRATEGIES), default="baseline_deviation")
    parser.add_argument("--hours-before", type=int, default=24)
    parser.add_argument("--hours-after", type=int, default=72)
    parser.add_argument("--baseline-start-offset", type=int, default=-48)
    parser.add_argument("--baseline-end-offset", type=int, default=-6)
    parser.add_argument("--percentiles", type=int, nargs="+", default=[10, 25, 75, 90])
    parser.add_argument("--profile", default=None, help="AWS profile to use (default: default credential chain)")
    # Glue Python shell jobs pass every job parameter (including its own
    # framework args like --extra-py-files, --scriptLocation, --python-version)
    # through to this script's argv -- parse_known_args() ignores whatever it
    # doesn't recognize instead of erroring, which parse_args() would do
    # (confirmed the hard way: a real job run failed with "unrecognized
    # arguments: --extra-py-files ... --scriptLocation ... --python-version ...").
    args, _unknown = parser.parse_known_args()
    return args


def run(s3, args) -> dict:
    events = load_event_catalog(s3, args.curated_bucket)

    event_years = {event.time.year for event in events}
    years = sorted(event_years | {y - 1 for y in event_years} | {y + 1 for y in event_years})
    series = load_omniweb_series(s3, args.curated_bucket, field=args.field, years=years)

    offsets, aligned = align_events(series, events, hours_before=args.hours_before, hours_after=args.hours_after)

    normalize = STRATEGIES[args.normalization]
    normalized = [
        normalize(
            offsets,
            event,
            baseline_start_offset=args.baseline_start_offset,
            baseline_end_offset=args.baseline_end_offset,
        )
        for event in aligned
    ]

    aggregated = aggregate_events(offsets, normalized, percentiles=tuple(args.percentiles))

    return {
        "field": args.field,
        "normalization": args.normalization,
        "hours_before": args.hours_before,
        "hours_after": args.hours_after,
        "baseline_start_offset": args.baseline_start_offset,
        "baseline_end_offset": args.baseline_end_offset,
        "event_count": len(events),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "offsets": [
            {
                "offset": row.offset,
                "n": row.n,
                "mean": row.mean,
                "median": row.median,
                "stderr": row.stderr,
                "ci95_lower": row.ci95_lower,
                "ci95_upper": row.ci95_upper,
                "percentiles": {str(pct): value for pct, value in row.percentiles.items()},
            }
            for row in aggregated
        ],
    }


def result_key(field: str, normalization: str) -> str:
    return f"curated/sea_results/{field}/{normalization}.json"


def main():
    args = parse_args()
    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    s3 = session.client("s3")

    result = run(s3, args)

    key = result_key(args.field, args.normalization)
    s3.put_object(
        Bucket=args.curated_bucket,
        Key=key,
        Body=json.dumps(result, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    print(
        f"SEA result for field={args.field} normalization={args.normalization} "
        f"({result['event_count']} events) -> s3://{args.curated_bucket}/{key}"
    )


if __name__ == "__main__":
    main()
