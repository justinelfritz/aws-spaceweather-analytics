"""OMNI2_YYYY.DAT field layout, verified against the format spec at
https://spdf.gsfc.nasa.gov/pub/data/omni/low_res_omni/omni2.text and
cross-checked position-by-position against real rows from omni2_2024.dat.

Each line is 55 whitespace-separated fields (not byte-fixed columns, despite
the FORTRAN-style format codes in the spec — every field is padded with
enough leading spaces that splitting on whitespace recovers the exact
55 tokens). Missing data is filled with a sentinel literal per field (e.g.
"999.9"), documented in the FILL column below and confirmed against a real
all-fill row (a not-yet-occurred date in the still-in-progress current year's
file, which NOAA pre-pads through Dec 31 rather than truncating).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # "int" or "float"
    fill: str | None  # exact literal fill text, or None if this field is never missing


FIELDS = [
    Field("year", "int", None),
    Field("day", "int", None),
    Field("hour", "int", None),
    Field("bartels_rotation", "int", "9999"),
    Field("imf_spacecraft_id", "int", "99"),
    Field("plasma_spacecraft_id", "int", "99"),
    Field("imf_points", "int", "999"),
    Field("plasma_points", "int", "999"),
    Field("field_magnitude_avg", "float", "999.9"),
    Field("magnitude_avg_field_vector", "float", "999.9"),
    Field("lat_angle_avg_field_vector", "float", "999.9"),
    Field("long_angle_avg_field_vector", "float", "999.9"),
    Field("bx_gse_gsm", "float", "999.9"),
    Field("by_gse", "float", "999.9"),
    Field("bz_gse", "float", "999.9"),
    Field("by_gsm", "float", "999.9"),
    Field("bz_gsm", "float", "999.9"),
    Field("sigma_b_mag", "float", "999.9"),
    Field("sigma_b_vec", "float", "999.9"),
    Field("sigma_bx", "float", "999.9"),
    Field("sigma_by", "float", "999.9"),
    Field("sigma_bz", "float", "999.9"),
    Field("proton_temperature", "float", "9999999."),
    Field("proton_density", "float", "999.9"),
    Field("plasma_speed", "float", "9999."),
    Field("flow_long_angle", "float", "999.9"),
    Field("flow_lat_angle", "float", "999.9"),
    Field("na_np_ratio", "float", "9.999"),
    Field("flow_pressure", "float", "99.99"),
    Field("sigma_t", "float", "9999999."),
    Field("sigma_n", "float", "999.9"),
    Field("sigma_v", "float", "9999."),
    Field("sigma_phi_v", "float", "999.9"),
    Field("sigma_theta_v", "float", "999.9"),
    Field("sigma_na_np", "float", "9.999"),
    Field("electric_field", "float", "999.99"),
    Field("plasma_beta", "float", "999.99"),
    Field("alfven_mach_number", "float", "999.9"),
    Field("kp_raw", "int", "99"),
    Field("sunspot_number_r", "int", "999"),
    Field("dst_index", "int", "99999"),
    Field("ae_index", "int", "9999"),
    Field("proton_flux_gt1mev", "float", "999999.99"),
    Field("proton_flux_gt2mev", "float", "99999.99"),
    Field("proton_flux_gt4mev", "float", "99999.99"),
    Field("proton_flux_gt10mev", "float", "99999.99"),
    Field("proton_flux_gt30mev", "float", "99999.99"),
    Field("proton_flux_gt60mev", "float", "99999.99"),
    # 0 means "no proton flux data" per the spec's own comments — a real,
    # meaningful flag value, not a "field is missing" sentinel. No fill.
    Field("flux_flag", "int", None),
    Field("ap_index", "int", "999"),
    Field("f107_index", "float", "999.9"),
    Field("pcn_index", "float", "999.9"),
    Field("al_index", "int", "99999"),
    Field("au_index", "int", "99999"),
    Field("magnetosonic_mach_number", "float", "99.9"),
]

FILL_VALUES = {
    field.name: (float(field.fill) if field.kind == "float" else int(field.fill))
    for field in FIELDS
    if field.fill is not None
}
