# Data sources: NOAA SWPC, NASA OMNIWeb/SPDF, NASA DONKI

Confirmed by live fetch on 2026-09-15 (endpoints and schemas can drift — re-verify before
building against this if it's been a while). Covers the section 3 to-do item: "Confirm exact
NOAA SWPC / NASA OMNIWeb endpoints and response formats for each feed."

v1 scope (from section 0): Kp index + solar wind plasma/mag. X-ray flux is a later addition.

---

## 1. Live/operational feeds — NOAA SWPC

These are fetched every 2 minutes and landed in the raw S3 zone (see
`lambdas/ingestion/handler.py`). Note: a dedicated dashboard "live readings" view was
considered and cut (2026-09-15) as redundant with existing public tools (e.g. SWPC's own
site) — these feeds are still ingested because they're the source data for the curated
layer, SEA, and eventually the historical explorer's recent end, not to power a live view.

### Planetary Kp index — official, 3-hour cadence

- **URL:** `https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json`
- **Format:** JSON array of objects, one per 3-hour UTC bin
- **Fields:** `time_tag` (ISO 8601, bin start), `Kp` (float, e.g. `4.33`), `a_running` (int, running A-index), `station_count` (int)
- **Use:** authoritative "current Kp" value; treat as ground truth once the bin closes

### Planetary Kp index — 1-minute estimated (provisional)

- **URL:** `https://services.swpc.noaa.gov/json/planetary_k_index_1m.json`
- **Format:** JSON array, ~1-minute cadence
- **Fields:** `time_tag`, `kp_index` (int 0-9), `estimated_kp` (float), `kp` (string like `"2P"` — value + status letter: `P`=provisional/estimated, `Z`=zero, etc.)
- **Use:** near-real-time trend ahead of the official 3-hour value; drives the "live" feel

### Real-time solar wind plasma (RTSW)

- **URL:** `https://services.swpc.noaa.gov/json/rtsw/rtsw_wind_1m.json`
- **Format:** JSON array, ~1-minute cadence
- **Fields:** `time_tag`, `active` (bool), `source` (currently `"IMAP"`; historically `"ACE"`/`"DSCOVR"` — expect this to change again over the project's life), `proton_speed` (km/s), `proton_temperature` (K), `proton_density` (cm⁻³), `proton_v{x,y,z}_gse`/`_gsm` (frequently `null`), plus quality flags (`max_convergence_flag`, `max_data_flag`, `max_error_count_flag`, `max_processing_flag`, `max_range_flag`, `max_sample_count_flag`, `max_telemetry_flag`, `overall_quality`)
- **Note:** the older path `/products/solar-wind/plasma-1-day.json` (commonly referenced in older tutorials/blog posts) now 404s. NOAA moved real-time solar wind under `/json/rtsw/` and switched the primary source spacecraft to IMAP. Don't trust older blog posts' endpoint paths — verify live.

### Real-time solar wind magnetic field (RTSW)

- **URL:** `https://services.swpc.noaa.gov/json/rtsw/rtsw_mag_1m.json`
- **Format:** JSON array, ~1-minute cadence
- **Fields:** `time_tag`, `active`, `source`, `bt` (nT, total field magnitude), `bx_gse`/`by_gse`/`bz_gse`, `theta_gse`/`phi_gse`, `bx_gsm`/`by_gsm`/`bz_gsm`, `theta_gsm`/`phi_gsm`
- **Use:** `bz_gsm` is the key driver field — sustained southward Bz is what couples solar wind energy into the magnetosphere and triggers storms. Worth surfacing prominently on the dashboard.

**Important — multi-source records:** both RTSW feeds return one record per timestamp *per source spacecraft*, not one record per timestamp overall. As of 2026-09-15, `rtsw_wind_1m.json` carries interleaved `ACE`, `IMAP`, and `SOLAR1` readings, and only one source is flagged `"active": true` at any given time (currently `SOLAR1`) — that's NOAA's designated primary feed; the others are backups. Land the full raw response untouched in the raw zone (all sources, for full fidelity/debuggability); filter to `active: true` when building the curated layer or reading for the dashboard. Don't hardcode a source name — which spacecraft is active changes over the mission lifecycle (this project has already observed a hand-off away from the once-standard DSCOVR).

### Not in v1 scope (noted for later)

- `https://services.swpc.noaa.gov/json/rtsw/rtsw_ephemerides_1h.json` — spacecraft position, hourly. Skip until there's a reason to need it.
- X-ray flux (GOES) — deferred per the v1 scope decision; endpoint not yet confirmed.

---

## 2. Historical/bulk data — NASA OMNIWeb / SPDF

For section 6 ML training data and optional pre-launch backfill of the curated S3 layer.

- **Base path:** `https://spdf.gsfc.nasa.gov/pub/data/omni/low_res_omni/`
- **Per-year files:** `omni2_YYYY.dat` (e.g. `omni2_2024.dat`), one row per hour, 1963–present
- **Combined file:** `omni2_all_years.dat` (~175 MB) — single bulk pull if we want everything at once
- **Format:** fixed-width ASCII text, **not JSON** — column layout, units, and fill values (e.g. `999.9` for missing data) are documented in `omni2.text` at the same path. This needs a dedicated fixed-width parser, unlike the SWPC JSON feeds.
- **Relevant columns:** solar wind speed/density/temperature, IMF Bx/By/Bz (GSE & GSM), Kp, Dst, ap index, F10.7 solar flux, sunspot number — the same physical quantities as the SWPC real-time feeds, but quality-controlled and merged across decades from multiple spacecraft.
- **Use:** this is the training-data source for section 6, and a candidate for backfilling history that predates this project's own ingestion.

---

## 3. Event catalog for SEA — reconsider the source

The to-do previously named "NOAA SWPC geomagnetic storm list" as the SEA event catalog. On
inspection, **SWPC doesn't actually publish a discrete storm-event catalog** — what it
publishes is raw index time series (Daily Geomagnetic Data / `DGD.txt`). Turning that into a
list of discrete storms would mean picking our own onset-detection thresholds, which is extra
work and an extra source of disagreement with published storm lists.

**Recommendation: use NASA's DONKI GST endpoint instead.**

- **URL:** `https://kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get/GST?startDate=YYYY-MM-DD&endDate=YYYY-MM-DD`
- **Format:** JSON array, one object per storm
- **Fields:** `gstID`, `startTime`, `allKpIndex[]` (`observedTime`, `kpIndex`, `source` — source is `"NOAA"`, so this is still SWPC-sourced data, just pre-curated into discrete events), `link`, `linkedEvents[]` (cross-references to associated CME activity IDs), `submissionTime`, `versionId`
- This is NASA CCMC's own curated catalog of discrete storm events, built from NOAA data, with onset times already picked for us — exactly the epoch t=0 markers SEA needs, with no threshold-picking on our end.
- **This changes the todo note, not the intent** — still ultimately NOAA-sourced Kp data, just accessed as a pre-built event list rather than scraped/derived from raw index files. Flagging for confirmation before updating section 0/5 notes.

---

## Ingestion cadence implications (feeds into section 3's EventBridge step)

| Feed | Suggested schedule | Notes |
|---|---|---|
| Kp 1-minute | `rate(1 minute)` or `rate(2 minutes)` | matches the to-do's original suggestion |
| RTSW plasma 1-minute | `rate(1 minute)` or `rate(2 minutes)` | same cadence as Kp 1m, could share a Lambda invocation |
| RTSW mag 1-minute | `rate(1 minute)` or `rate(2 minutes)` | same as above |
| Kp official 3-hour | `rate(3 hours)`, or just derive from the 1m feed at ingestion time | avoids a second ingestion path for what's arguably the same signal at lower cadence |
| OMNI2 historical | one-time backfill + rare re-pull | batch job, not a high-frequency EventBridge rule — likely a separate one-off script or Step Functions run, not part of the recurring section 3 loop |
| DONKI GST catalog | daily poll for new storms + one-time historical backfill | low frequency; new storms are rare events |
