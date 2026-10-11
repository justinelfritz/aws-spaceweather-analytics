# API reference

Public, read-only REST API over the curated data lake. No auth (see
`infra/stacks/api_stack.py` for why: every endpoint here only ever reads
curated/precomputed data, there's nothing to protect). Base URL is the
`ApiUrl` CloudFormation output of the `SpaceWeather-Api-<stage>` stack,
e.g. `https://<id>.execute-api.us-east-1.amazonaws.com/v1`.

All responses are `application/json` with CORS enabled for all origins.
Every endpoint is `GET` except `/sea/{field}/{normalization}`, which also
accepts `POST` for on-demand, caller-chosen-subset analysis (see below) --
still effectively read-only, no state is mutated either way; `POST` is
used there only because a meaningful event-selection payload doesn't fit
safely in a URL. Errors are `{"error": "<message>"}` with a 4xx status.

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
`field_magnitude_avg`, `bx_gsm`, `by_gsm`, `bz_gsm`, `bx_gse`, `by_gse`,
`bz_gse`, `flow_pressure`, `electric_field`, `f107_index` — the physically
meaningful curated columns; internal provenance columns (spacecraft IDs,
per-hour point counts, sigma/uncertainty fields) aren't exposed.
`bx_gsm` and `bx_gse` always return numerically identical values: GSE and
GSM share the same X-axis by definition (both point from Earth to the Sun;
only Y and Z differ, rotated about that shared axis), so OMNI2 only
publishes one physical Bx column. Exposed as two fields anyway for
symmetry with By/Bz, which really do differ between the two frames.

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
the SEA / forecast-skill views. `min_dst` isn't part of DONKI's own data —
it's computed from the curated OMNIWeb Dst series in a window around each
storm's onset (-24h to +72h, matching SEA's default window) and can be
`null` if that OMNIWeb data isn't backfilled yet for the relevant years.

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
      "min_dst": -412,
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

Precomputed superposed epoch analysis result over the *full* DONKI catalog
(`sea/run_sea_job.py`), passed through from S3 as-is. `404` means that
combination hasn't been computed yet, not that it's invalid — the weekly
SEA job runs the full 18-field x 3-normalization matrix (see
`infra/stacks/sea_stack.py`).

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
    {
      "offset": -24,
      "n": 200,
      "mean": -9.1,
      "median": -7.0,
      "stderr": 0.8,
      "ci95_lower": -10.7,
      "ci95_upper": -7.5,
      "percentiles": {"10": -22.0, "25": -14.0, "75": -3.0, "90": 1.5}
    },
    ...
  ]
}
```

`stderr`/`ci95_lower`/`ci95_upper` are the standard error of the mean and its
95% confidence interval (`mean ± 1.96 * stderr`) — they describe how
precisely the *mean* at that offset is known, which is a different question
from the percentile band's "how spread out are the storms themselves".
Both are `null` when `n < 2` (a single-event selection has no spread to
estimate an error from). `percentiles` defaults to the 10th/25th/75th/90th
(`sea/aggregation.py`'s `DEFAULT_PERCENTILES`) — the batch job can override
this via `run_sea_job.py --percentiles`, so don't assume exactly these four
keys are always present.

---

## `POST /sea/{field}/{normalization}`

On-demand superposed epoch analysis over a **caller-chosen subset** of the
DONKI catalog (see `docs/sea-on-demand-design.md` for the full design
rationale). Same path parameters and response shape as the `GET` above.

**Body** (optional)

```json
{"event_ids": ["2024-05-10T15:00:00-GST-001", "2010-04-05T12:00:00-GST-001"]}
```

| case | behavior |
|---|---|
| body omitted, or `event_ids` omitted/`null` | identical to `GET` — the full-catalog precomputed result, one S3 lookup, no on-demand compute |
| `event_ids` names every event in the current catalog | same fast path as above |
| `event_ids` names a true subset | computed synchronously: filters the catalog, loads only the OMNIWeb years that subset touches, and re-runs the same align → normalize → aggregate pipeline the batch job uses. Typically finishes in a few seconds. |
| `event_ids` is `[]` | `400` — omit the field entirely to request the full catalog instead |
| `event_ids` contains an unknown `gst_id` | `400`, naming the unknown ID(s) |

No minimum selection size is enforced server-side — even a single event
returns a valid (if statistically trivial) result. The dashboard's SEA tab
shows a "small sample" caveat below 10 selected events; that's a frontend
convention, not an API rule.

**Example**

```
POST /sea/dst_index/raw
Content-Type: application/json

{"event_ids": ["2024-05-10T15:00:00-GST-001"]}
```

Response shape is identical to the `GET` example above, except
`event_count` reflects the requested subset (`1`, here) instead of the
full catalog.

---

## `GET /forecast-skill/{target}/{error_type}`

Precomputed forecast-skill backtest result (`ml/forecast_skill.py`,
`ml/classification_skill.py`): the Kp(t+3h)/Dst(t+3h)/AE(t+3h) model's
prediction error, aligned on the same storm catalog SEA uses, restricted
to the held-out test period. See `ml/forecast_skill/findings.md` for the
headline result this powers.

**Path parameters**

| param | valid values |
|---|---|
| `target` | `kp`, `dst_index`, `ae_index` |
| `error_type` | `signed_error`, `abs_error`, `classification` |

**Query parameters**

| param | valid values | default |
|---|---|---|
| `severity` | `minor` (G1–G2), `strong` (G3), `severe` (G4–G5) | omitted = unfiltered, every storm in the test period |

Restricts the `signed_error`/`abs_error` backtest to storms of the given
class band instead of the full catalog — e.g. "is the model specifically
worse right around severe storms?" Grouped into 3 bands rather than one
per G-class: individual classes run as few as 1–3 storms in the 2021-2025
test period, too small to backtest meaningfully on their own. **Not valid
with `error_type=classification`** (400) — that result is a single
whole-test-period summary, not aligned on storm onset, so a severity band
doesn't apply to it (see below).

**Example**

```
GET /forecast-skill/dst_index/abs_error?severity=severe
```

```json
{
  "target": "dst_index",
  "error_type": "abs_error",
  "severity": "severe",
  "horizon_hours": 3,
  "hours_before": 24,
  "hours_after": 72,
  "event_count": 12,
  "generated_at": "2026-09-16T09:04:20+00:00",
  "model_version": {"git_commit": "a1b2c3d", "train_years": "1995-2020"},
  "offsets": [
    {"offset": 0, "n": 11, "mean": 20.933, "median": 18.5, "percentiles": {"25": 10.0, "75": 27.0}},
    ...
  ]
}
```

`severity` is `null` and `event_count` reflects the full test-period
catalog when the query parameter is omitted — identical to this
endpoint's behavior before `severity` existed.

### `error_type=classification`

Thresholds the model's existing regression predictions into a storm/
no-storm call and scores it as a confusion matrix — not a separately
trained classifier (`space-weather-platform-todo.md` section 6 flagged
this as "available for free" this way). Only valid for `target=kp` or
`target=dst_index` (`ae_index` has no established storm threshold — 400
otherwise). Computed pointwise over every hourly prediction in the held-out
test period, not epoch-aligned, so the response has no `offsets` array.

```
GET /forecast-skill/dst_index/classification
```

```json
{
  "target": "dst_index",
  "threshold": -50.0,
  "storm_definition": "dst_index <= -50.0",
  "horizon_hours": 3,
  "row_count": 35064,
  "positive_rate_actual": 0.071,
  "positive_rate_predicted": 0.065,
  "generated_at": "2026-10-10T12:00:00+00:00",
  "model_version": {"git_commit": "a1b2c3d", "train_years": [1995, 2020]},
  "confusion_matrix": {"tp": 2050, "fp": 180, "tn": 32400, "fn": 434},
  "precision": 0.919,
  "recall": 0.825,
  "f1": 0.870,
  "false_alarm_rate": 0.0055
}
```

`kp`'s storm definition is `kp >= 5.0` (NOAA's own G1 threshold);
`dst_index`'s is `dst_index <= -50.0` (a standard literature moderate-storm
cutoff, Gonzalez et al. 1994) — both are CLI-overridable in
`ml/classification_skill.py`, not fixed at the API layer.

---

## Rate limiting

The API Gateway stage throttles at 10 requests/second (burst 20) across
all endpoints, unauthenticated — no API keys or usage plans, since this is
a public read-only demo and an API key would just add friction without
protecting anything meaningful. See `infra/stacks/api_stack.py`.
