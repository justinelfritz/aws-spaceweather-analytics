import json
import sys
from pathlib import Path

import boto3
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backfill_donki_gst import build_catalog, main, normalize_event, storm_class  # noqa: E402

# Real events from the DONKI GST endpoint (fetched and verified 2026-09-15).
EVENT_NO_LINKED_CME = {
    "gstID": "2010-04-05T12:00:00-GST-001",
    "startTime": "2010-04-05T12:00Z",
    "allKpIndex": [{"observedTime": "2010-04-05T12:00Z", "kpIndex": 7.0, "source": "NOAA"}],
    "link": "https://kauai.ccmc.gsfc.nasa.gov/DONKI/view/GST/582/-1",
    "linkedEvents": None,
    "submissionTime": "2013-07-17T19:27Z",
    "versionId": 1,
}
EVENT_WITH_LINKED_CME = {
    "gstID": "2024-05-10T15:00:00-GST-001",
    "startTime": "2024-05-10T15:00Z",
    "allKpIndex": [
        {"observedTime": "2024-05-10T18:00Z", "kpIndex": 7.67, "source": "NOAA"},
        {"observedTime": "2024-05-10T21:00Z", "kpIndex": 8.67, "source": "NOAA"},
    ],
    "link": "https://kauai.ccmc.gsfc.nasa.gov/DONKI/view/GST/30693/-1",
    "linkedEvents": [{"activityID": "2024-05-08T05:36:00-CME-001"}],
    "submissionTime": "2024-05-10T18:44Z",
    "versionId": 1,
}


def test_storm_class_maps_kp_to_noaa_g_scale():
    assert storm_class(5.0) == "G1"
    assert storm_class(6.0) == "G2"
    assert storm_class(7.67) == "G4"
    assert storm_class(8.67) == "G5"
    assert storm_class(9.0) == "G5"


def test_storm_class_clamps_out_of_range_values():
    assert storm_class(0.0) == "G1"
    assert storm_class(20.0) == "G5"


def test_normalize_event_handles_null_linked_events():
    normalized = normalize_event(EVENT_NO_LINKED_CME)
    assert normalized["gst_id"] == "2010-04-05T12:00:00-GST-001"
    assert normalized["start_time"] == "2010-04-05T12:00:00+00:00"
    assert normalized["max_kp"] == 7.0
    assert normalized["storm_class"] == "G3"
    assert normalized["linked_event_ids"] == []
    assert normalized["linked_cme_ids"] == []


def test_normalize_event_extracts_linked_cme_and_peak_kp():
    normalized = normalize_event(EVENT_WITH_LINKED_CME)
    assert normalized["max_kp"] == 8.67
    assert normalized["max_kp_time"] == "2024-05-10T21:00:00+00:00"
    assert normalized["storm_class"] == "G5"
    assert normalized["linked_cme_ids"] == ["2024-05-08T05:36:00-CME-001"]
    assert len(normalized["kp_readings"]) == 2


def test_build_catalog_sorts_by_start_time():
    catalog = build_catalog([EVENT_WITH_LINKED_CME, EVENT_NO_LINKED_CME])
    assert [event["gst_id"] for event in catalog] == [
        EVENT_NO_LINKED_CME["gstID"],
        EVENT_WITH_LINKED_CME["gstID"],
    ]


@mock_aws
def test_main_writes_raw_and_curated_catalog(monkeypatch, capsys):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="raw-test")
    s3.create_bucket(Bucket="curated-test")

    import backfill_donki_gst

    monkeypatch.setattr(backfill_donki_gst, "fetch_gst_events", lambda *a, **k: [EVENT_NO_LINKED_CME, EVENT_WITH_LINKED_CME])
    monkeypatch.setattr(
        sys,
        "argv",
        ["backfill_donki_gst.py", "--raw-bucket", "raw-test", "--curated-bucket", "curated-test"],
    )

    main()

    listing = s3.list_objects_v2(Bucket="raw-test", Prefix="raw/donki_gst/")
    assert len(listing["Contents"]) == 1

    curated_obj = s3.get_object(Bucket="curated-test", Key="curated/event_catalog/geomagnetic_storms.json")
    catalog = json.loads(curated_obj["Body"].read())
    assert len(catalog) == 2
    assert catalog[0]["gst_id"] == EVENT_NO_LINKED_CME["gstID"]
