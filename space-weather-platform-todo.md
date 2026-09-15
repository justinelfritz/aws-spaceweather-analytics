# Space Weather Platform — Project To-Do

A living checklist for building the real-time dashboard, ML forecasting pipeline, and superposed epoch analysis (SEA) tooling on AWS. Check items off as you go; add sub-items freely as decisions get made.

---

## 0. Project setup & planning

- [x] Define scope for v1 — decide which data feeds are in scope for launch (recommend starting with just Kp index + solar wind plasma/mag, add X-ray flux later)
- [x] Pick the storm/CME event catalog you'll use for SEA — pivoted from "NOAA SWPC geomagnetic storm list" to the NASA DONKI GST API (`kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get/GST`): SWPC doesn't actually publish a discrete storm catalog, only raw index time series, so DONKI's pre-curated NOAA-sourced event list is used instead — see `docs/data-sources.md`
- [x] Set up GitHub repo with a clear structure: `/infra` (CDK), `/lambdas`, `/ml`, `/sea`, `/frontend`, `/docs`
- [ ] Write a short README with the architecture diagram and elevator pitch (useful for portfolio reviewers from day one) — elevator pitch done, architecture diagram still needed
- [x] Set up a personal AWS account (or dedicated sub-account) separate from any work/production accounts
- [x] Enable AWS Budgets with an alert at a chosen threshold (e.g., $30/month) — do this before writing any infra code
- [x] Set up billing alarms in CloudWatch as a second layer of protection

## 1. AWS account & IAM

- [x] Create an IAM user/role for CDK deployments (avoid using root credentials) — `cdk-bootstrap-admin` (bootstrap only) + `cdk-deployer` (day-to-day, assume-role-only) set up and verified end-to-end with `cdk diff --all -c stage=dev --profile sw-deploy`
- [ ] Define least-privilege IAM policies per Lambda function (don't reuse one broad role across all functions) — revisit once section 3 writes real Lambdas
- [ ] Set up a `dev` and `prod` stage/account split if you want to show environment separation (optional but a good practice signal) — same-account `-c stage=` split wired in `infra/app.py`, decide if separate accounts are worth it later
- [x] Enable CloudTrail for audit logging — `space-weather-trail` (multi-region), see `docs/aws-account-setup.md`
- [x] Decide on tagging convention (e.g., `Project=space-weather`, `Stage=dev`) and apply consistently via CDK — done in `infra/app.py`

## 2. Infrastructure as code (CDK)

- [x] Initialize CDK app (TypeScript or Python — pick one and stay consistent) — Python
- [x] Define stack boundaries (e.g., `IngestionStack`, `StorageStack`, `MlStack`, `ApiStack`, `FrontendStack`) rather than one monolithic stack — stubs in `infra/stacks/`, empty pending sections 3-8
- [ ] Set up CI (GitHub Actions) to run `cdk synth` / `cdk diff` on PRs
- [ ] Add a `cdk deploy` pipeline step gated behind manual approval for prod
- [ ] Document how to deploy from scratch in the README

## 3. Data ingestion

- [x] Confirm exact NOAA SWPC / NASA OMNIWeb endpoints and response formats for each feed — see `docs/data-sources.md` (live-verified endpoints, schemas, and per-feed cadence)
- [x] Write ingestion Lambda(s): fetch, parse, validate, normalize units/timestamps — single Lambda covering all 4 live SWPC feeds, `lambdas/ingestion/handler.py`
- [x] Add retry logic with exponential backoff for upstream API calls — `fetch_json()`, 3 attempts
- [x] Define idempotent write keys (timestamp + source) to avoid duplicate records on re-processing — `raw_key()`, keyed by feed + ingestion minute
- [x] Create EventBridge rules per feed with appropriate schedule (e.g., `rate(2 minutes)` for Kp index) — one shared `rate(2 minutes)` rule (see cadence table in `docs/data-sources.md` for why one schedule covers all 4 live feeds)
- [x] Set up an SQS dead-letter queue for failed ingestion records — wired to the schedule's Lambda target, `infra/stacks/ingestion_stack.py`
- [x] Add a CloudWatch alarm on DLQ depth — notifies via SNS email subscription
- [x] Write unit tests for the parsing/validation logic using saved sample API responses — `lambdas/ingestion/tests/`, fixtures captured live from each endpoint
- [x] Handle upstream schema drift gracefully (log + alert rather than crash) — per-feed `validate()` checks required fields; one feed failing doesn't block the others, but still trips the DLQ so it's visible

## 4. Storage

- [x] Create S3 buckets: raw landing zone, curated Parquet layer — raw bucket from section 3; `CuratedBucket` added in `infra/stacks/storage_stack.py` (empty container — the raw→curated transform job itself isn't built yet)
- [x] Define S3 partitioning scheme (e.g., `source/year/month/day/`) — `<feed>/<yyyy>/<mm>/<dd>/<HHMM>.json`, already in use since the ingestion Lambda (`lambdas/ingestion/handler.py`'s `raw_key()`)
- [x] Set up S3 lifecycle policies (transition raw data to Infrequent Access/Glacier after 30–90 days) — Standard → IA at 30 days → Glacier at 90 days, on the raw bucket
- [ ] ~~Create Timestream database + table(s) for the hot, query-facing time series~~ — **Descoped 2026-09-15**: Timestream's only real use case was backing a live-readings dashboard view, which was cut (see section 8) as redundant with existing public space-weather sites. Not free-tier eligible either, so cutting it also removes an ongoing cost with no remaining use. S3 raw → curated (Parquet) is the only store the platform needs.
- [ ] ~~Configure Timestream memory-store retention (short — hours to days) and magnetic-store retention (longer)~~ — descoped along with the item above
- [x] Set up Glue Data Catalog + crawler if using Athena for ad hoc querying of the S3 layer — `space_weather_raw_<stage>` database + on-demand crawler over the raw bucket (note: raw JSON is nested per-feed, not flat — clean Athena querying really wants the curated layer; this crawler is a stopgap for ad hoc raw debugging)
- [x] Write a small script/notebook to sanity-check data completeness (are there gaps in the time series?) — `scripts/check_data_completeness.py`; run against the live raw bucket, it confirmed the Kp feeds are 100% complete and surfaced real ~2-3 min upstream telemetry dropouts already present in the RTSW plasma/mag source data (not an ingestion issue — DLQ is empty)

## 5. Superposed epoch analysis (SEA)

- [ ] Assemble the event catalog (storm onset times, CME arrival times) as a static dataset in S3 — source is the DONKI GST API, see `docs/data-sources.md`
- [ ] Write the core SEA function: given an event list and a window size, extract and align time-series segments on epoch time
- [ ] Decide on normalization approach (raw values vs. deviation from baseline vs. normalized amplitude)
- [ ] Implement aggregation (mean, median, percentile bands) across aligned events
- [ ] Package as a Fargate task or Glue Python shell job (pandas/numpy/scipy)
- [ ] Add a Step Functions state to trigger SEA runs on a schedule or on-demand via API
- [ ] Write unit tests using a synthetic dataset with known events (verify alignment logic is correct)
- [ ] Validate SEA output against a published space-physics result as a sanity check (e.g., known Dst index storm signature)
- [ ] Store SEA results in S3 (and/or a small DynamoDB table) for the dashboard to read

## 6. ML forecasting pipeline

- [ ] Pull historical OMNIWeb data for training (solar wind, IMF, Kp/Dst index)
- [ ] Do exploratory data analysis — check for gaps, outliers, seasonal/solar-cycle effects
- [ ] Define the prediction target precisely (e.g., Kp index 1–3 hours ahead, or binary storm/no-storm classification)
- [ ] Feature engineering: lagged values, rolling statistics, solar wind derived parameters
- [ ] Establish a baseline model (persistence model or simple linear regression) before anything fancier
- [ ] Train a first real model (gradient boosting or LSTM) in a SageMaker notebook or processing job
- [ ] Track experiments (SageMaker Experiments, or even a simple spreadsheet/MLflow if you want to keep it light)
- [ ] Evaluate against the baseline — don't ship a model that doesn't beat persistence
- [ ] Decide on serving pattern: SageMaker Serverless Inference vs. scheduled batch transform (avoid a 24/7 real-time endpoint)
- [ ] Set up a retraining schedule (e.g., weekly, via Step Functions) if you want the model to stay current
- [ ] Write tests for the feature engineering pipeline (deterministic outputs given fixed input)
- [ ] Log model version + training data window alongside each prediction for traceability

## 7. API & serving layer

- [ ] Design the API contract: endpoints for historical range queries, forecast results, SEA results
- [ ] Set up API Gateway REST API + Lambda resolvers
- [ ] ~~Set up API Gateway WebSocket API for live push of new readings~~ — **Descoped 2026-09-15**: no live-readings view to push updates to, see section 8
- [ ] Add Cognito user pool if you want authenticated access (optional for a portfolio demo — could also leave public/read-only)
- [ ] Add basic rate limiting / usage plans on API Gateway
- [ ] Write integration tests hitting a deployed dev-stage API
- [ ] Document the API (OpenAPI spec or a simple markdown reference)

## 8. Dashboard / frontend

- [ ] Choose stack (React + D3/Plotly — pick one path and don't split effort)
- [ ] ~~Build the live readings view (current Kp, solar wind, alerts)~~ — **Descoped 2026-09-15**: redundant with existing public tools (e.g. SWPC's own site); this dashboard's value is in SEA + forecast, not re-displaying live readings that already exist elsewhere
- [ ] Build the historical explorer (date range picker + chart)
- [ ] Build the SEA visualization (aligned event overlays with mean/median band)
- [ ] Build the forecast view (predicted Kp/storm probability with confidence indication)
- [ ] ~~Wire up WebSocket client for live updates~~ — descoped along with the live readings view above
- [ ] Handle loading/error/empty states gracefully
- [ ] Deploy to S3 + CloudFront
- [ ] Set up cache invalidation on frontend deploys
- [ ] Basic responsive/mobile check

## 9. Testing

- [ ] Unit tests: ingestion parsing, SEA alignment logic, feature engineering
- [ ] Integration tests: end-to-end ingestion → storage → API read-back on a dev stage
- [ ] Load test the API at a modest concurrency to confirm Lambda concurrency limits are sane
- [ ] Chaos-test the DLQ path (deliberately feed malformed data and confirm it routes correctly)
- [ ] Validate SEA output against known physical events (sanity check, not just unit correctness)
- [ ] Model evaluation tests (confirm the deployed model beats baseline on held-out data)
- [ ] Frontend smoke tests (does the dashboard load and render with real API data)

## 10. Monitoring, cost, and operations

- [ ] CloudWatch dashboards for Lambda errors/duration, API latency, DLQ depth
- [ ] Alarms wired to an SNS topic (email/Slack) for critical failures
- [ ] Confirm AWS Budgets alert is still active and threshold still makes sense as usage grows
- [ ] ~~Periodic review of Timestream retention settings vs. actual query patterns~~ — descoped along with Timestream itself, see section 4
- [ ] Review Cost Explorer monthly, compare against the estimate table in the cost writeup

## 11. Documentation & portfolio polish

- [ ] Finalize architecture diagram (matches what's actually deployed, not just the plan)
- [ ] Write up the cost breakdown and trade-off decisions (serverless vs. EC2, batch vs. real-time inference)
- [ ] Write up the SEA methodology and any interesting findings from the analysis
- [ ] Write up the ML model choice, evaluation, and what didn't work (this is often the most compelling part of a portfolio writeup)
- [ ] Record a short demo video or GIF of the dashboard in action
- [ ] Clean up the README for a first-time reader (someone who has 60 seconds to evaluate the project)

---

**Suggested build order:** 0 → 1 → 2 → 3 → 4, then 5 and 6 can proceed in parallel, then 7 → 8, with 9 and 10 threaded throughout rather than left for the end. Section 11 gets revisited continuously, not just at the finish.
