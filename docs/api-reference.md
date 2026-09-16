# API reference

Public, read-only REST API over the curated data lake. No auth (see
`infra/stacks/api_stack.py` for why: every endpoint here only ever reads
curated/precomputed data, there's nothing to protect). Base URL is the
`ApiUrl` CloudFormation output of the `SpaceWeather-Api-<stage>` stack,
e.g. `https://<id>.execute-api.us-east-1.amazonaws.com/v1`.

All responses are `application/json` with CORS enabled for all origins
(`GET` only). Errors are `{"error": "<message>"}` with a 4xx status.

---

## `GET /historical`

Range query over the curated OMNIWeb OMNI2 hourly dataset
(`scripts/backfill_omniweb.py`). Powers the dashboard's historical explorer.

**Query parameters**

| param | required | notes |
|---|---|---|
| `fields` | yes | comma-separated, e.g. `kp,dst_index`. See valid list below. |
| `start` | yes | ISO 8601 date or datetime, inclusive |
| `end` | yes | ISO 8601 date or datetime, inclusive |

Max range per request: 366 days. Missing year-partitions inside the range
(e.g. a request reaching into the future) are silently skipped, not an error.

**Valid fields**: `kp`, `dst_index`, `ae_index`, `ap_index`,
`sunspot_number_r`, `plasma_speed`, `proton_density`, `proton_temperature`,
`field_magnitude_avg`, `bz_gsm`, `by_gsm`, `bz_gse`, `by_gse`,
`flow_pressure`, `electric_field`, `f107_index` — the physically meaningful
curated columns; internal provenance columns (spacecraft IDs, per-hour point
counts, sigma/uncertainty fields) aren't exposed.

**Example**

```
GET /historical?fields=kp,dst_index&start=2024-05-10&end=2024-05-12
```

```json
{
  "fields": ["kp", "dst_index"],
  "start": "2024-05-10T00:00:00+00:00",
  "end": "2024-05-12T00:00:00+00:00",
  "count": 49,
  "data": [
    {"timestamp": "2024-05-10T00:00:00+00:00", "kp": 3.3, "dst_index": -8},
    ...
  ]
}
```

---

## `GET /events`

The DONKI geomagnetic storm catalog (`scripts/backfill_donki_gst.py`).
Powers storm markers on the historical explorer and the event picker for
the SEA / forecast-skill views.

**Query parameters** (both optional — omit both for the full catalog)

| param | notes |
|---|---|
| `start` | ISO 8601 date/datetime, inclusive lower bound on `start_time` |
| `end` | ISO 8601 date/datetime, inclusive upper bound on `start_time` |

**Example**

```
GET /events?start=2024-01-01&end=2024-12-31
```

```json
{
  "count": 12,
  "events": [
    {
      "gst_id": "2024-05-10T16:00:00-GST-001",
      "start_time": "2024-05-10T16:00:00+00:00",
      "max_kp": 9.0,
      "max_kp_time": "2024-05-11T00:00:00+00:00",
      "storm_class": "G5",
      "kp_readings": [...],
      "linked_event_ids": [...],
      "linked_cme_ids": [...],
      "source_link": "...",
      "submission_time": "...",
      "version_id": 1
    },
    ...
  ]
}
```

---

## `GET /sea/{field}/{normalization}`

Precomputed superposed epoch analysis result (`sea/run_sea_job.py`),
passed through from S3 as-is. `404` means that combination hasn't been
computed yet, not that it's invalid — the weekly SEA job currently only
runs `dst_index`/`raw` (see `infra/stacks/sea_stack.py`).

**Path parameters**

| param | valid values |
|---|---|
| `field` | same field list as `/historical` |
| `normalization` | `raw`, `baseline_deviation`, `normalized_amplitude` |

**Example**

```
GET /sea/dst_index/raw
```

```json
{
  "field": "dst_index",
  "normalization": "raw",
  "hours_before": 24,
  "hours_after": 72,
  "baseline_start_offset": -48,
  "baseline_end_offset": -6,
  "event_count": 200,
  "generated_at": "2026-09-15T21:34:08+00:00",
  "offsets": [
    {"offset": -24, "n": 200, "mean": -9.1, "median": -7.0, "percentiles": {"25": -14.0, "75": -3.0}},
    ...
  ]
}
```

---

## `GET /forecast-skill/{target}/{error_type}`

Precomputed forecast-skill backtest result (`ml/forecast_skill.py`): the
Kp(t+3h)/Dst(t+3h) model's prediction error, aligned on the same storm
catalog SEA uses, restricted to the held-out test period. See
`ml/forecast_skill/findings.md` for the headline result this powers.

**Path parameters**

| param | valid values |
|---|---|
| `target` | `kp`, `dst_index` |
| `error_type` | `signed_error`, `abs_error` |

**Example**

```
GET /forecast-skill/dst_index/abs_error
```

```json
{
  "target": "dst_index",
  "error_type": "abs_error",
  "horizon_hours": 3,
  "hours_before": 24,
  "hours_after": 72,
  "event_count": 75,
  "generated_at": "2026-09-16T09:04:20+00:00",
  "model_version": {"git_commit": "a1b2c3d", "train_years": "1995-2020"},
  "offsets": [
    {"offset": 0, "n": 69, "mean": 20.933, "median": 18.5, "percentiles": {"25": 10.0, "75": 27.0}},
    ...
  ]
}
```

---

## Rate limiting

The API Gateway stage throttles at 10 requests/second (burst 20) across
all endpoints, unauthenticated — no API keys or usage plans, since this is
a public read-only demo and an API key would just add friction without
protecting anything meaningful. See `infra/stacks/api_stack.py`.
