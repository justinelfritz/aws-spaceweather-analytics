// Mirrors the valid-value lists documented in docs/api-reference.md /
// enforced server-side in lambdas/api/*.py -- kept in sync by hand since
// the API doesn't (yet) expose a schema/options endpoint.
export const QUERYABLE_FIELDS = [
  "kp",
  "dst_index",
  "ae_index",
  "ap_index",
  "sunspot_number_r",
  "plasma_speed",
  "proton_density",
  "proton_temperature",
  "field_magnitude_avg",
  "bx_gsm",
  "by_gsm",
  "bz_gsm",
  "bx_gse",
  "by_gse",
  "bz_gse",
  "flow_pressure",
  "electric_field",
  "f107_index",
];

// Human-readable labels + units for each queryable field, per the OMNI2
// format spec (https://spdf.gsfc.nasa.gov/pub/data/omni/low_res_omni/omni2.text,
// scripts/omni2_format.py). Indices (Kp, sunspot number) are dimensionless
// and get no unit suffix. bx_gsm/bx_gse are numerically identical (GSE and
// GSM share the same X-axis by definition) but shown as two fields anyway,
// matching how By/Bz are split -- see lambdas/api/historical.py's
// FIELD_COLUMN comment for the full explanation.
export const FIELD_LABELS = {
  kp: "Kp",
  dst_index: "Dst Index [nT]",
  ae_index: "AE Index [nT]",
  ap_index: "Ap Index [nT]",
  sunspot_number_r: "Sunspot Number",
  plasma_speed: "Plasma Speed [km/s]",
  proton_density: "Proton Density [n/cm³]",
  proton_temperature: "Proton Temperature [K]",
  field_magnitude_avg: "Avg Field Magnitude [nT]",
  bx_gsm: "Bx GSM [nT]",
  by_gsm: "By GSM [nT]",
  bz_gsm: "Bz GSM [nT]",
  bx_gse: "Bx GSE [nT]",
  by_gse: "By GSE [nT]",
  bz_gse: "Bz GSE [nT]",
  flow_pressure: "Flow Pressure [nPa]",
  electric_field: "Electric Field [mV/m]",
  f107_index: "F10.7 Index [sfu]",
};

// Groups QUERYABLE_FIELDS for the historical explorer's field checkboxes,
// one column per group, instead of one long list in a somewhat arbitrary
// order. Every field in QUERYABLE_FIELDS must appear in exactly one group.
export const FIELD_GROUPS = [
  { label: "Indices", fields: ["kp", "dst_index", "ae_index", "ap_index", "sunspot_number_r", "f107_index"] },
  { label: "Plasma", fields: ["plasma_speed", "proton_density", "proton_temperature", "flow_pressure"] },
  {
    label: "Fields",
    fields: ["field_magnitude_avg", "bx_gsm", "by_gsm", "bz_gsm", "bx_gse", "by_gse", "bz_gse", "electric_field"],
  },
];

// Derived from FIELD_GROUPS (not QUERYABLE_FIELDS's own order) so any
// field-picker elsewhere -- e.g. the SEA tab's Field dropdown -- lists
// fields in the same order a user already sees them grouped into columns
// on the historical explorer, instead of two screens disagreeing on order.
export const FIELDS_IN_DISPLAY_ORDER = FIELD_GROUPS.flatMap((group) => group.fields);

export const NORMALIZATIONS = ["raw", "baseline_deviation", "normalized_amplitude"];

// Plain-language labels for the 3 normalization strategies (sea/normalization.py's
// STRATEGIES), described there as: raw values unchanged, deviation from each
// event's own pre-event baseline, and that deviation scaled by the
// baseline's own spread (a z-score).
export const NORMALIZATION_LABELS = {
  raw: "Raw Values",
  baseline_deviation: "Deviation from Baseline",
  normalized_amplitude: "Normalized Amplitude (Z-Score)",
};

export const FORECAST_TARGETS = ["kp", "dst_index"];

export const ERROR_TYPES = ["signed_error", "abs_error"];
