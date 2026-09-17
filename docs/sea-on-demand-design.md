# On-demand SEA: design decisions

**Status: draft — decisions not yet made.** This document exists so every
design decision for this feature gets made once, deliberately, before any
code changes. Each decision below has options, trade-offs, and a
recommendation; fill in "**Chosen:**" once you've decided. Once all
decisions are marked, this doc becomes the implementation spec.

## Motivation

Today the SEA tab can only show one thing per field/normalization: the
result of aligning *all 200* DONKI-cataloged storms. A user can't ask "what
does this look like for only G4+ storms," "only storms since 2020," or
"just these 5 storms I picked" — the analysis is baked in ahead of time,
not computed against a chosen subset.

## Current architecture (for context)

- `sea/run_sea_job.py` runs as an AWS Glue Python shell job. Given
  `--field` and `--normalization`, it loads the *full* event catalog
  (`sea/load_data.py`'s `load_event_catalog`, no filtering), loads the
  needed OMNIWeb years for that field, and runs
  align → normalize → aggregate (`sea/alignment.py`, `normalization.py`,
  `aggregation.py`).
- `infra/stacks/sea_stack.py`'s `SeaStateMachine` runs that job once per
  {field, normalization} combination (18 fields × 3 normalizations = 54)
  via a Step Functions `Map` state, on a weekly schedule (+ on-demand via
  `aws stepfunctions start-execution`).
- Each run writes one static JSON file to
  `curated/sea_results/{field}/{normalization}.json`.
- `lambdas/api/sea_results.py` (`GET /sea/{field}/{normalization}`) is a
  pure passthrough: look up that S3 key, return its contents, 404 if it
  doesn't exist. There is no "which storms" parameter anywhere in this
  path — it isn't a concept the API or the job knows about.

**The core retooling this feature requires**: moving from "always the full
catalog, computed offline ahead of time" to "a caller-chosen subset,
computed synchronously per request." That's an architecture change, not a
config tweak.

**Why the compute itself isn't the risk**: `alignment.py`, `normalization.py`,
and `aggregation.py` are pure stdlib (`dataclasses`, `datetime`, `math`,
`statistics` — confirmed by reading their imports directly, no
pandas/numpy/pyarrow). Only reading the OMNIWeb Parquet needs pyarrow, and
`/historical`'s Lambda already solved exactly that problem (the
AWS-managed AWSSDKPandas layer, concurrent per-year S3 reads — see
`lambdas/api/historical.py`). `aggregation.py`'s percentile/mean/median
logic already degenerates gracefully at small `n` (a single selected storm
still returns a valid, if statistically trivial, result — no crash, no
special-casing needed). So this is an integration and design problem, not
a performance-research one.

---

## Decision 1: how do users choose storms?

**Option A — Explicit multi-select.** Reuse the events table already built
for the historical explorer (onset, class, max Kp, DONKI link) and make
rows selectable. Most flexible (any exact subset, including oddly specific
ones), most UI work (selection state, a "select all/none" convenience,
carrying the selection into the SEA tab or duplicating the table there).

**Option B — Simple filters.** Date range (mirrors `/events`'s existing
`start`/`end`), minimum storm class (G1–G5), minimum Kp. Covers the
obvious real use cases ("only major storms," "storms since the last solar
maximum") with much less UI — a few form controls, no selection-state
management. Can't cherry-pick individual storms.

**Option C — Both.** Filters narrow the set, explicit selection refines it
further. Most capable, most work — effectively A and B combined.

**Recommendation**: B first. It's a small fraction of A's UI work and
covers the motivating use cases described above. A can be added later
without reworking the API (see Decision 2) if it turns out people want to
cherry-pick.

**Chosen:** _______________

---

## Decision 2: API contract shape

The rest of this API is GET-only, consistent with "public read-only demo."
Two real options:

**Option A — GET with query params.** e.g.
`GET /sea/dst_index/raw?start=2020-01-01&min_class=G3`. Fits the filter
approach (Decision 1B) cleanly and keeps the whole API GET-only.
If Decision 1 later grows to include explicit event-ID lists, those can
still ride along as a comma-separated query param (`event_ids=...,...`,
matching `/historical`'s `fields=a,b,c` convention) *as long as the
realistic selection size stays well under a few dozen* — the full
200-event catalog's IDs as one query string would be several KB long,
past what's comfortable for URLs/CDNs/logs.

**Option B — POST with a JSON body.** No URL-length ceiling, so "select
any of the 200 individually" is safe at any scale. Breaks from this API's
GET-only convention, and turns a "read" into something that looks more
like an action, which is a real (if mostly aesthetic) inconsistency in an
otherwise uniform API.

**Recommendation**: A, given Decision 1 = B (filters, not raw ID lists) —
no URL-length concern, keeps the API's GET-only shape intact. If Decision
1 = A or C (explicit selection), revisit this — POST becomes the safer
default.

**Chosen:** _______________

---

## Decision 3: what happens to the existing batch pipeline?

**Option A — Retire it.** Delete the Glue job, Step Functions state
machine, and weekly schedule (`infra/stacks/sea_stack.py`). Everything
becomes on-demand. Simplest architecture, but every request — including
the common "just show me the default, all-storms view" — now pays a
synchronous compute cost instead of an instant S3 GET, and the recurring
batch-pipeline piece of the project (Glue + Step Functions orchestration)
goes away.

**Option B — Keep both, unconnected.** Batch pipeline keeps producing the
full-catalog result as today; a separate on-demand path handles anything
custom. Two independent code paths to maintain, but each is simple in
isolation.

**Option C — Hybrid: on-demand with a cache-hit fast path.** The on-demand
Lambda always handles the request, but first checks whether it exactly
matches a pre-computed combination (the full, unfiltered catalog for a
given field/normalization) — if so, serve the existing static S3 JSON
instead of recomputing; otherwise compute fresh. One request path, one
Lambda, but the common case stays instantly fast and the batch
pipeline keeps earning its keep.

**Recommendation**: C. It resolves the "instant default load" vs.
"genuinely on-demand" tension without an all-or-nothing call, at the cost
of one extra branch (a cache-key check) in the Lambda.

**Chosen:** _______________

---

## Decision 4: minimum selection size / validation

`aggregation.py` already handles `n=1` without crashing (percentile band
just collapses to a single value), but a mean/median/percentile band from
1–2 storms isn't statistically meaningful. Worth deciding now rather than
discovering complaints later:

- Reject (400) below some minimum (e.g. 5 events)?
- Allow anything ≥1, and let the frontend show a caveat ("small sample,
  interpret with caution") below some threshold?
- No minimum, no caveat — trust the user?

**Recommendation**: allow anything ≥1 (no hard reject — a deliberate
single-storm case-study view is legitimate), but show a frontend caveat
below a small threshold (e.g. <10 events).

**Chosen:** _______________

---

## Explicitly out of scope for this round

To keep this decision set bounded, the following are **not** being
decided now — flag if any should actually be in scope:

- Making the alignment window (`hours_before`/`hours_after`) or baseline
  offsets user-adjustable. Today they're fixed per the job's defaults.
- Caching *custom* (filtered) on-demand results. Given the compute is
  expected to be cheap (seconds), recomputing fresh each time is simplest
  and avoids cache-invalidation complexity. Only the Decision 3C fast path
  caches anything, and only for the one well-known "full catalog" case.
- New rate limiting. The API Gateway stage's existing throttle (10 req/s,
  burst 20 — see `infra/stacks/api_stack.py`) already bounds abuse of a
  more expensive endpoint; no evidence it needs to change.
- Supporting fewer than all 18 fields for on-demand queries. No reason to
  restrict — the mechanism is identical per field.

---

## Once decisions are made: implementation plan

(To be finalized after the above are answered — sketch below assuming the
recommended options.)

1. **New/extended Lambda** (`lambdas/api/`) implementing: parse + validate
   filter params → load + filter the event catalog
   (`load_event_catalog`-equivalent) → determine touched OMNIWeb years for
   the *filtered* set → concurrent S3 reads for the one requested field
   (same pattern as `historical.py`'s `_fetch_years`) → align/normalize/
   aggregate (reusing `sea/alignment.py`, `normalization.py`,
   `aggregation.py` directly — these need to ship inside the Lambda's
   bundle the same way `lambdas/` already bundles its own modules, or via
   the existing `sea_lib` wheel as a layer; packaging approach TBD but not
   expected to be hard, both are established patterns in this repo).
2. **Cache-hit fast path** (if Decision 3 = C): a cheap check — "is this
   exactly the unfiltered, full-catalog request?" — before falling through
   to computing fresh.
3. **API contract update**: new query params on `GET /sea/{field}/{normalization}`
   (if Decision 2 = A), validation errors for bad filters, `docs/api-reference.md`
   update.
4. **Frontend**: filter controls (if Decision 1 = B) in `SeaVisualization.jsx`,
   a loading state sized for synchronous compute latency (likely a few
   seconds, to be measured against a real deployed Lambda before assuming),
   and the small-sample caveat (Decision 4).
5. **Tests**: filter/catalog-loading logic, alignment against a dynamic
   subset (including edge cases: 0 matches, 1 match), the cache-hit fast
   path, API validation errors.
6. **Docs**: update `docs/api-reference.md` and
   `space-weather-platform-todo.md` section 5 with the new capability and
   the decisions made here.

Rough sizing once decisions are locked: comparable to the original section 7
API-layer build-out plus a meaningful slice of frontend work — not a
one-sitting change, but well-bounded once the above is settled.
