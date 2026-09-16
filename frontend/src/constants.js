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
  "bz_gsm",
  "by_gsm",
  "bz_gse",
  "by_gse",
  "flow_pressure",
  "electric_field",
  "f107_index",
];

export const NORMALIZATIONS = ["raw", "baseline_deviation", "normalized_amplitude"];

export const FORECAST_TARGETS = ["kp", "dst_index"];

export const ERROR_TYPES = ["signed_error", "abs_error"];
