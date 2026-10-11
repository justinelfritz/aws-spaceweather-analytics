# ML forecasting expansion: design decisions

**Status: decided 2026-10-10 — this is now the implementation spec.** The
three directions below were already chosen by the user after reviewing the
current ML pipeline and the project's documented scope boundaries; what
follows records *how* each is implemented, not whether to build it.

## Motivation

The dashboard's ML forecasting was, until now, deliberately narrow:
two targets (Kp, Dst), one horizon (t+3h), one aggregate error-vs-onset
view (`ml/forecast_skill.py`) that lumps all 200 DONKI storms together.
Three gaps were worth closing:

1. **Severity-conditioned forecast skill** — is the model specifically
   worse for extreme storms, or uniformly off? Today's backtest can't say.
2. **Storm/no-storm classification** — `space-weather-platform-todo.md`
   section 6 already flagged this as "available later for free by
   thresholding either prediction... rather than training a separate
   classifier." This implements exactly that, nothing more.
3. **AE index as a third forecast target** — captures substorm/auroral
   activity, distinct from Dst (ring current) and Kp (planetary). AE was
   already an input feature, just never a target.

## Current architecture (for context)

- `ml/features.py`'s `add_targets()` hardcoded exactly two target columns
  (`kp_target`, `dst_target`); `ml/train.py`'s `train_and_evaluate()` and
  `persistence_predictions()` likewise hardcoded a 2-entry target list.
- `ml/forecast_skill.py`'s `TARGETS` list was already iterated generically
  by both `build_test_predictions()` and `run_backtest()` — adding a third
  target there was mechanical.
- `sea/load_data.py`'s `load_event_catalog()` filtered the raw DONKI
  catalog by time bounds only. `alignment.Event` is deliberately
  source-agnostic (`{event_id, time}` only, per its own docstring) — storm
  class was never threaded through it.
- `lambdas/api/forecast_skill_results.py` is a pure S3 passthrough;
  `VALID_TARGETS`/`VALID_ERROR_TYPES` are plain Python sets with no API
  Gateway-level enum enforcement — `{error_type}` is a generic greedy path
  parameter, and Lambda proxy integration already forwards query strings
  (used today by `/historical`'s `fields=` and `/events`'s `start`/`end`).
- Real data check against the live S3 catalog
  (`curated/event_catalog/geomagnetic_storms.json`): in the 2021-2025 test
  period, storms by class are G1:1, G2:42, G3:20, G4:9, G5:3.

---

## Decision 1: severity bucketing granularity

**Option A — one bucket per G-class (G1..G5).** Most granular, but G1
(n=1) and G5 (n=3) in the test period are far too small to backtest
meaningfully on their own.

**Option B — 3-band grouping** (minor = G1-G2, strong = G3, severe =
G4-G5), matching NOAA's own G-scale groupings.

**Option C — 2-band grouping** (minor vs. major). Simpler, but collapses
the real difference between "strong" and "severe" storms, which is
exactly the kind of distinction this feature exists to surface.

**Chosen: B.** minor (n=43), strong (n=20), severe (n=12) in the test
period — the coarsest split that still separates three practically
distinct severity tiers while keeping every bucket's `n` in double digits.

## Decision 2: severity API surface

**Option A — query param on the existing `GET /forecast-skill/{target}/{error_type}` endpoint.**

**Option B — new path segment**
(`/forecast-skill/{target}/{error_type}/{severity}`). Requires a new CDK
resource.

**Option C — separate endpoint.** Duplicates the passthrough logic for
what is just one more optional filter dimension.

**Chosen: A — `?severity=minor|strong|severe`, omitted = unfiltered.**
Zero CDK changes (query strings already forwarded by the existing proxy
integration), matches this API's established convention for optional GET
filters, and is fully backward compatible — omitting the param reproduces
today's exact response byte-for-byte.

## Decision 3: severity S3 key layout

**Option A — nest under the existing key**
(`curated/ml_forecast_skill/{target}/{error_type}/{severity}.json`),
leaving the unfiltered key (`.../​{error_type}.json`) untouched.

**Option B — restructure the existing key** to make room for a uniform
scheme (e.g. always include a severity segment, `all` for unfiltered).

**Chosen: A.** Purely additive — no migration of the already-deployed,
already-consumed unfiltered key. S3 has no real directories, so a file at
`.../abs_error.json` and more at `.../abs_error/minor.json` coexist with
no actual collision, only a cosmetic nesting in the S3 console's folder
view.

## Decision 4: classification implementation

**Option A — train a dedicated classifier.**

**Option B — threshold the existing regression predictions.**

**Chosen: B**, per the project's own prior decision
(`space-weather-platform-todo.md` section 6). No new model, no new
training code path.

## Decision 5: classification thresholds

**Chosen:** `kp >= 5.0` (NOAA's own G1/"minor storm" definition — not a
judgment call) and `dst_index <= -50.0` nT (the commonly cited
literature moderate-storm cutoff, Gonzalez et al. 1994 — Dst has no
official NOAA G-scale threshold, so this is the most defensible
standard value available). Both are named constants
(`DEFAULT_KP_STORM_THRESHOLD`, `DEFAULT_DST_STORM_THRESHOLD` in
`ml/classification_skill.py`) and CLI-overridable
(`--kp-storm-threshold`, `--dst-storm-threshold`), not hardcoded only
inline.

## Decision 6: classification API surface

**Option A — new endpoint** (e.g. `/forecast-skill/{target}/classification` as a distinct resource).

**Option B — a third `error_type` value on the existing endpoint.**

**Chosen: B — `error_type=classification`.** Zero CDK changes (same
reasoning as Decision 2's chosen option): that path segment was already
an unvalidated string at the API Gateway level, only validated in Python.

## Decision 7: classification scope — pointwise vs. epoch-aligned

**Chosen: pointwise, over the full held-out test period** (~35,000 hourly
rows, 2021-2025), **not** epoch-aligned on storm onset like the error-band
analysis. Precision/recall/F1/false-alarm-rate need a large, representative
contingency table — including quiet-time true negatives — to mean
anything. Epoch-aligning into offset bins as small as the `severe` severity
band's 12 events would collapse each offset's confusion matrix to single
digits, far too thin for these metrics to be meaningful at any individual
offset. This is also why classification deliberately does **not** compose
with severity bands (Decision 1-3) or storm-onset offsets — it's one
whole-test-period summary per target, matching the "basic" scope requested.

## Decision 8: AE target rollout mechanics

**Option A — a generic N-target config** (e.g. a list of target names fed
through every function via config).

**Option B — hardcode a third entry** in each existing list, mirroring how
the 2-target version was already written.

**Chosen: B**, consistent with this project's established, repeated
preference for explicit, lightweight code over generic abstraction (e.g.
a plain JSONL experiment log instead of MLflow). `ae_index` is excluded
from classification (Decision 4-7): there's no established "storm"
threshold for AE the way there is for Kp (NOAA G1) or Dst (literature
convention).

---

## Explicitly out of scope for this round

- Any new trained classification model (Decision 4 forecloses this).
- Severity-conditioning for the classification skill (Decision 7
  forecloses this — pointwise metrics need the full test period's sample
  size to mean anything).
- A storm threshold for AE (no established NOAA/literature cutoff;
  `CLASSIFIABLE_TARGETS` stays `kp`/`dst_index` only).
- Any live/real-time inference — everything here stays retrospective/
  batch, following the existing precomputed-JSON-in-S3 + Lambda-passthrough
  pattern every other endpoint in this API uses.
- Making severity bands or classification thresholds user-adjustable from
  the frontend — both are fixed per-run config, same as `hours_before`/
  `hours_after` are today.
- A full ROC curve / threshold sweep for classification. This is
  deliberately a "basic" check at one fixed, named threshold per target,
  not a threshold-optimization exercise.

---

## Implementation plan (locked)

1. **`sea/load_data.py`**: add `storm_classes: set | None = None` to
   `load_event_catalog`, filtering the raw catalog dict's `storm_class`
   before constructing `Event` objects. Default `None` keeps every
   existing caller (`sea/run_sea_job.py`, `lambdas/api/sea_results.py`'s
   on-demand POST path) unaffected.
2. **`ml/features.py`**: `add_targets` gains `ae_target`;
   `build_feature_table`'s `required` dropna list gains it too.
3. **`ml/train.py`**: `persistence_predictions` and the hardcoded target
   loop in `train_and_evaluate` both gain an `ae_index`/`ae_target` entry.
4. **`ml/forecast_skill.py`**: `TARGETS` gains `("ae_index", "ae_target")`;
   add `SEVERITY_BANDS`; `result_key` gains an optional `severity` arg;
   `run_backtest` gains an outer loop over `{None: None, **SEVERITY_BANDS}`,
   result dict keys become `(target, error_type, severity)` 3-tuples, each
   result JSON gains a `"severity"` field.
5. **New file `ml/classification_skill.py`**: reuses
   `build_test_predictions`/`load_model_version`/`DEFAULT_MODEL_DIR` from
   `forecast_skill.py`. Computes a confusion matrix + precision/recall/F1/
   false-alarm-rate via `sklearn.metrics` for `kp`/`dst_index` only, writes
   `curated/ml_classification_skill/{target}.json`.
6. **`lambdas/api/forecast_skill_results.py`**: `VALID_TARGETS` gains
   `ae_index`; `VALID_ERROR_TYPES` gains `classification`; new
   `VALID_SEVERITIES`; reads `severity` from `queryStringParameters`, 400s
   on an unknown value or on `severity` combined with
   `error_type=classification`; `result_key` branches to the
   classification S3 key when `error_type == "classification"`.
7. **Frontend**: `constants.js` gains `ae_index` in `FORECAST_TARGETS`,
   `classification` in `ERROR_TYPES`/`ERROR_TYPE_LABELS`,
   `CLASSIFICATION_TARGETS`, `SEVERITY_BANDS`/`SEVERITY_BAND_LABELS`.
   `api.js`'s `fetchForecastSkill` gains an optional `severity` arg.
   `ForecastSkillView.jsx` gains a severity dropdown (hidden when
   `error_type=classification`), restricts the target dropdown to
   `CLASSIFICATION_TARGETS` when classification is selected (resetting off
   `ae_index` if needed), and renders a confusion-matrix/metric-tiles
   summary instead of the Plotly band chart when the response has no
   `offsets`.
8. **Docs**: `docs/api-reference.md`'s forecast-skill section documents
   `ae_index`, the `severity` query param, and `error_type=classification`
   with a worked example. `space-weather-platform-todo.md` section 6
   marked done with a dated note once shipped.

See the top-level implementation plan (`/home/justing/.claude/plans/cryptic-frolicking-crescent.md`)
for the full rollout/verification sequence and test plan.
