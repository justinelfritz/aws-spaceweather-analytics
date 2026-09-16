"""Feature engineering for the Kp(t+3h) / Dst(t+3h) forecasting models.

Turns the raw hourly OMNIWeb frame into a supervised-learning table: lagged
and rolling-window features of solar wind/IMF/geomagnetic fields, a few
physically-derived coupling parameters, and the two forecast targets (Kp
and Dst shifted `horizon_hours` into the future). Pure pandas, no I/O --
deterministic given a fixed input frame and config, so it's testable with
small synthetic frames (see ml/tests/test_features.py).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Base columns used as feature inputs: the primary solar wind/IMF drivers
# of magnetospheric coupling, plus the geomagnetic indices themselves
# (autoregressive signal -- a persistence baseline is just kp_lag{horizon}h).
# All are >=99.5% complete for kp/dst/ae per the EDA (ml/eda/findings.md);
# the solar wind/IMF fields have real gaps concentrated pre-~1973-1995,
# which is why callers should restrict the training window rather than
# imputing across those gaps.
BASE_FEATURE_COLUMNS = [
    "plasma_speed",
    "proton_density",
    "field_magnitude_avg",
    "bz_gsm",
    "by_gsm",
    "kp",
    "dst_index",
    "ae_index",
]

DERIVED_FEATURE_COLUMNS = ["dynamic_pressure", "motional_efield", "newell_coupling"]

DEFAULT_LAG_HOURS = (1, 2, 3, 6, 12, 24)
DEFAULT_ROLLING_WINDOWS = (3, 6, 24)
DEFAULT_HORIZON_HOURS = 3


def add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Adds physically-derived solar wind coupling parameters."""
    df = df.copy()
    # Solar wind dynamic pressure (nPa) -- standard proton-dominated-wind approximation.
    df["dynamic_pressure"] = 1.6726e-6 * df["proton_density"] * df["plasma_speed"] ** 2
    # Dawn-dusk motional electric field (mV/m); southward Bz (negative) drives
    # dayside reconnection, the main energy input channel for storms.
    df["motional_efield"] = -df["plasma_speed"] * df["bz_gsm"] * 1e-3
    # Newell et al. (2007) universal coupling function -- empirically
    # correlates with geomagnetic activity better than Bz or V alone.
    b_t = np.sqrt(df["by_gsm"] ** 2 + df["bz_gsm"] ** 2)
    theta_c = np.arctan2(df["by_gsm"], df["bz_gsm"])
    df["newell_coupling"] = (
        (df["plasma_speed"] ** (4 / 3)) * (b_t ** (2 / 3)) * (np.sin(theta_c / 2).abs() ** (8 / 3))
    )
    return df


def lag_feature_names(columns: list[str], lags: tuple[int, ...]) -> list[str]:
    return [f"{column}_lag{lag}h" for column in columns for lag in lags]


def rolling_feature_names(columns: list[str], windows: tuple[int, ...]) -> list[str]:
    return [f"{column}_roll{window}h_mean" for column in columns for window in windows] + [
        f"{column}_roll{window}h_std" for column in columns for window in windows
    ]


def feature_column_names(
    lags: tuple[int, ...] = DEFAULT_LAG_HOURS,
    rolling_windows: tuple[int, ...] = DEFAULT_ROLLING_WINDOWS,
) -> list[str]:
    """The exact model input column names for a given config -- a pure
    function of the config, independent of any actual DataFrame, so
    training/inference can agree on column order without introspecting
    a table (and without accidentally matching unrelated columns, e.g. a
    naive `startswith("kp_")` check would wrongly sweep up `kp_raw`)."""
    feature_columns = BASE_FEATURE_COLUMNS + DERIVED_FEATURE_COLUMNS
    return lag_feature_names(feature_columns, lags) + rolling_feature_names(feature_columns, rolling_windows)


def add_lag_features(df: pd.DataFrame, columns: list[str], lags: tuple[int, ...]) -> pd.DataFrame:
    df = df.copy()
    for column in columns:
        for lag in lags:
            df[f"{column}_lag{lag}h"] = df[column].shift(lag)
    return df


def add_rolling_features(df: pd.DataFrame, columns: list[str], windows: tuple[int, ...]) -> pd.DataFrame:
    df = df.copy()
    for column in columns:
        for window in windows:
            df[f"{column}_roll{window}h_mean"] = df[column].rolling(window).mean()
            df[f"{column}_roll{window}h_std"] = df[column].rolling(window).std()
    return df


def add_targets(df: pd.DataFrame, horizon_hours: int) -> pd.DataFrame:
    df = df.copy()
    df["kp_target"] = df["kp"].shift(-horizon_hours)
    df["dst_target"] = df["dst_index"].shift(-horizon_hours)
    return df


def build_feature_table(
    df: pd.DataFrame,
    lags: tuple[int, ...] = DEFAULT_LAG_HOURS,
    rolling_windows: tuple[int, ...] = DEFAULT_ROLLING_WINDOWS,
    horizon_hours: int = DEFAULT_HORIZON_HOURS,
) -> pd.DataFrame:
    """End-to-end: raw hourly OMNIWeb rows -> supervised feature table.

    Requires `df` sorted ascending by timestamp on an unbroken hourly grid
    (true of the curated dataset -- the EDA found zero timestamp gaps).
    Every lag/rolling feature only ever looks at data at or before the
    current row, so there is no leakage from the shift(-horizon_hours)
    targets. Rows near the start (insufficient lag/rolling history) or the
    end (target horizon runs past the available data) are dropped.
    """
    df = add_derived_columns(df)
    feature_columns = BASE_FEATURE_COLUMNS + DERIVED_FEATURE_COLUMNS
    df = add_lag_features(df, feature_columns, lags)
    df = add_rolling_features(df, feature_columns, rolling_windows)
    df = add_targets(df, horizon_hours)

    required = feature_column_names(lags, rolling_windows) + ["kp_target", "dst_target"]
    return df.dropna(subset=required).reset_index(drop=True)
