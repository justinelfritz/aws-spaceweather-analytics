import json
import sys
import urllib.error
from pathlib import Path

import boto3
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backfill_supermag_substorms import (  # noqa: E402
    build_catalog,
    fetch_substorm_year_text,
    main,
    parse_substorm_text,
)

# Shape matches SuperMAG's real self-documented ASCII response (fields confirmed
# 2026-10-15 against GFZ's swvo reader source, not a live call).
SAMPLE_RESPONSE = """some header line SuperMAG prepends
<year> <month> <day> <hour> <min> <mlt> <mlat> <glon> <glat>
2019 11 15 10 42 21.69 73.52 25.01 76.51
2019 12 03 02 05 9.39 62.36 209.80 62.29
"""

EMPTY_EVENTS_RESPONSE = """<year> <month> <day> <hour> <min> <mlt> <mlat> <glon> <glat>
"""


def test_parse_substorm_text_extracts_events_after_header():
    events = parse_substorm_text(SAMPLE_RESPONSE, "newell")
    assert len(events) == 2
    assert events[0] == {
        "onset_time": "2019-11-15T10:42:00+00:00",
        "mlt": 21.69,
        "mlat": 73.52,
        "glon": 25.01,
        "glat": 76.51,
    }


def test_parse_substorm_text_handles_no_events():
    assert parse_substorm_text(EMPTY_EVENTS_RESPONSE, "newell") == []


def test_parse_substorm_text_converts_liou_mlt_from_degrees_to_hours():
    events = parse_substorm_text(SAMPLE_RESPONSE, "liou")
    assert events[0]["mlt"] == 21.69 / 15


def test_parse_substorm_text_raises_without_header():
    try:
        parse_substorm_text("no header here\n2019 11 15 10 42 21.69 73.52 25.01 76.51\n", "newell")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "expected table header" in str(exc)


def test_parse_substorm_text_raises_on_malformed_record():
    bad_response = "<year> <month> <day> <hour> <min> <mlt> <mlat> <glon> <glat>\n2019 11 15 10 42 21.69\n"
    try:
        parse_substorm_text(bad_response, "newell")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "malformed" in str(exc)


def test_build_catalog_sorts_and_tags_catalog_and_id():
    events = parse_substorm_text(SAMPLE_RESPONSE, "newell")
    catalog = build_catalog(events, "newell")
    assert [entry["onset_time"] for entry in catalog] == [events[0]["onset_time"], events[1]["onset_time"]]
    assert catalog[0]["catalog"] == "newell"
    assert catalog[0]["substorm_id"] == f"SUPERMAG-NEWELL-{events[0]['onset_time']}"


def test_fetch_substorm_year_text_raises_immediately_on_error_response(monkeypatch):
    import backfill_supermag_substorms

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"ERROR:Invalid logon"

    call_count = {"n": 0}

    def fake_urlopen(url, timeout=30):
        call_count["n"] += 1
        return FakeResponse()

    monkeypatch.setattr(backfill_supermag_substorms.urllib.request, "urlopen", fake_urlopen)

    try:
        fetch_substorm_year_text("newell", 2019, "testuser", attempts=4)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "Invalid logon" in str(exc)
    assert call_count["n"] == 1  # not retried -- a real API error, not transient


def test_fetch_substorm_year_text_retries_transient_network_errors(monkeypatch):
    import backfill_supermag_substorms

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return SAMPLE_RESPONSE.encode("utf-8")

    attempts_made = {"n": 0}

    def fake_urlopen(url, timeout=30):
        attempts_made["n"] += 1
        if attempts_made["n"] < 3:
            raise urllib.error.URLError("temporary failure")
        return FakeResponse()

    monkeypatch.setattr(backfill_supermag_substorms.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(backfill_supermag_substorms.time, "sleep", lambda *_: None)

    text = fetch_substorm_year_text("newell", 2019, "testuser", attempts=4)
    assert text == SAMPLE_RESPONSE
    assert attempts_made["n"] == 3


def test_fetch_substorm_year_text_never_includes_user_in_error_message(monkeypatch):
    import backfill_supermag_substorms

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"ERROR:Invalid logon"

    monkeypatch.setattr(
        backfill_supermag_substorms.urllib.request, "urlopen", lambda url, timeout=30: FakeResponse()
    )

    try:
        fetch_substorm_year_text("newell", 2019, "super-secret-username", attempts=1)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "super-secret-username" not in str(exc)


@mock_aws
def test_main_requires_user(monkeypatch, capsys):
    monkeypatch.delenv("SUPERMAG_USERNAME", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["backfill_supermag_substorms.py", "--raw-bucket", "raw-test", "--curated-bucket", "curated-test"],
    )

    try:
        main()
        assert False, "expected SystemExit"
    except SystemExit as exc:
        assert "SUPERMAG_USERNAME" in str(exc)


@mock_aws
def test_main_writes_raw_and_curated_catalog(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="raw-test")
    s3.create_bucket(Bucket="curated-test")

    import backfill_supermag_substorms

    monkeypatch.setattr(backfill_supermag_substorms, "fetch_substorm_year_text", lambda *a, **k: SAMPLE_RESPONSE)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backfill_supermag_substorms.py",
            "--raw-bucket",
            "raw-test",
            "--curated-bucket",
            "curated-test",
            "--user",
            "testuser",
            "--start-year",
            "2019",
            "--end-year",
            "2019",
        ],
    )

    main()

    raw_listing = s3.list_objects_v2(Bucket="raw-test", Prefix="raw/supermag_substorms/newell/")
    assert len(raw_listing["Contents"]) == 1

    curated_obj = s3.get_object(Bucket="curated-test", Key="curated/event_catalog/substorms.json")
    catalog = json.loads(curated_obj["Body"].read())
    assert len(catalog) == 2
    assert catalog[0]["catalog"] == "newell"


@mock_aws
def test_main_dry_run_writes_nothing(monkeypatch):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="raw-test")
    s3.create_bucket(Bucket="curated-test")

    import backfill_supermag_substorms

    monkeypatch.setattr(backfill_supermag_substorms, "fetch_substorm_year_text", lambda *a, **k: SAMPLE_RESPONSE)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backfill_supermag_substorms.py",
            "--raw-bucket",
            "raw-test",
            "--curated-bucket",
            "curated-test",
            "--user",
            "testuser",
            "--start-year",
            "2019",
            "--end-year",
            "2019",
            "--dry-run",
        ],
    )

    main()

    assert "Contents" not in s3.list_objects_v2(Bucket="raw-test", Prefix="raw/supermag_substorms/")
    assert "Contents" not in s3.list_objects_v2(Bucket="curated-test", Prefix="curated/")
