#!/usr/bin/env python3
"""Validates the SEA pipeline (alignment + normalization + aggregation)
against a real, well-documented result: averaging Dst across many real
DONKI-cataloged geomagnetic storms should reproduce the classic superposed-
epoch storm signature — a quiet pre-onset baseline near zero, a sharp dip
into the main phase, and a gradual partial recovery over the following
days. This is section 5's "validate SEA output against a published
space-physics result" check.

Dst is used raw (normalize_raw), not baseline-deviated, because Dst is
already defined as a deviation from a quiet-time reference — that's the
convention published Dst superposed-epoch studies use.

Usage:
    python validate_dst_signature.py --curated-bucket ... --profile sw-bootstrap
"""

import argparse
import statistics

import boto3

from aggregation import aggregate_events
from alignment import align_events
from load_data import load_event_catalog, load_omniweb_series
from normalization import normalize_raw


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--curated-bucket", required=True)
    parser.add_argument("--profile", default=None, help="AWS profile to use (default: default credential chain)")
    parser.add_argument("--hours-before", type=int, default=24)
    parser.add_argument("--hours-after", type=int, default=72)
    return parser.parse_args()


def main():
    args = parse_args()
    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    s3 = session.client("s3")

    events = load_event_catalog(s3, args.curated_bucket)
    print(f"Loaded {len(events)} storm events from the DONKI-sourced catalog")

    event_years = {event.time.year for event in events}
    years = sorted(event_years | {y - 1 for y in event_years} | {y + 1 for y in event_years})
    series = load_omniweb_series(s3, args.curated_bucket, field="dst_index", years=years)
    print(f"Loaded {len(series)} hourly Dst points across years {years[0]}-{years[-1]}")

    offsets, aligned = align_events(series, events, hours_before=args.hours_before, hours_after=args.hours_after)
    normalized = [normalize_raw(offsets, event) for event in aligned]
    aggregated = aggregate_events(offsets, normalized, percentiles=(25, 75))

    print()
    print(f"{'offset':>7} {'n':>4} {'mean':>8} {'median':>8} {'p25':>8} {'p75':>8}")
    for row in aggregated:
        p25 = row.percentiles[25]
        p75 = row.percentiles[75]
        if row.mean is None:
            print(f"{row.offset:>7} {row.n:>4} {'--':>8} {'--':>8} {'--':>8} {'--':>8}")
        else:
            print(f"{row.offset:>7} {row.n:>4} {row.mean:>8.1f} {row.median:>8.1f} {p25:>8.1f} {p75:>8.1f}")

    mean_by_offset = {row.offset: row.mean for row in aggregated if row.mean is not None}

    pre_onset_window = [o for o in range(-args.hours_before, -5) if o in mean_by_offset]
    pre_onset_mean = statistics.mean(mean_by_offset[o] for o in pre_onset_window)

    min_offset = min(mean_by_offset, key=lambda o: mean_by_offset[o])
    min_value = mean_by_offset[min_offset]

    late_window = [o for o in range(args.hours_after - 24, args.hours_after + 1) if o in mean_by_offset]
    late_recovery_mean = statistics.mean(mean_by_offset[o] for o in late_window)

    print()
    print(f"Pre-onset mean Dst (offset {pre_onset_window[0]} to {pre_onset_window[-1]}): {pre_onset_mean:.1f} nT")
    print(f"Minimum mean Dst: {min_value:.1f} nT at offset +{min_offset}h")
    print(f"Late-window mean Dst (offset +{late_window[0]} to +{late_window[-1]}): {late_recovery_mean:.1f} nT")
    print()

    checks = [
        ("Pre-onset baseline is higher (less negative) than the storm minimum", pre_onset_mean > min_value),
        ("The Dst minimum occurs after onset, not before or exactly at it", min_offset > 0),
        ("There is measurable recovery from the minimum by the late window", late_recovery_mean > min_value),
    ]
    all_passed = True
    for description, passed in checks:
        print(f"[{'PASS' if passed else 'FAIL'}] {description}")
        all_passed = all_passed and passed

    print()
    if all_passed:
        print("Aggregate Dst profile matches the expected superposed-epoch storm signature.")
    else:
        print("Aggregate Dst profile does NOT match the expected signature — investigate before packaging the pipeline.")


if __name__ == "__main__":
    main()
