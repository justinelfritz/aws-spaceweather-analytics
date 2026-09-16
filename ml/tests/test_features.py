import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from features import (
    BASE_FEATURE_COLUMNS,
    DERIVED_FEATURE_COLUMNS,
    add_derived_columns,
    build_feature_table,
    feature_column_names,
)

N_ROWS = 40


def make_synthetic_frame(n_rows: int = N_ROWS) -> pd.DataFrame:
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    return pd.DataFrame(
        {
            "timestamp": [start + timedelta(hours=i) for i in range(n_rows)],
            "plasma_speed": [400.0 + i for i in range(n_rows)],
            "proton_density": [5.0 + 0.1 * i for i in range(n_rows)],
            "field_magnitude_avg": [5.0 for _ in range(n_rows)],
            "bz_gsm": [-2.0 - 0.05 * i for i in range(n_rows)],
            "by_gsm": [1.0 for _ in range(n_rows)],
            "kp": [3.0 + 0.05 * i for i in range(n_rows)],
            "dst_index": [-10.0 - i for i in range(n_rows)],
            "ae_index": [100.0 + i for i in range(n_rows)],
        }
    )


def test_build_feature_table_is_deterministic():
    df = make_synthetic_frame()
    result_a = build_feature_table(df.copy())
    result_b = build_feature_table(df.copy())
    pd.testing.assert_frame_equal(result_a, result_b)


def test_lag_features_shift_correctly():
    df = make_synthetic_frame()
    result = build_feature_table(df, lags=(1, 2), rolling_windows=(3,), horizon_hours=3)

    # Pick an arbitrary row and confirm its lag1h/lag2h kp values match the
    # raw frame's kp at (that row's timestamp - 1h) / (- 2h).
    row = result.iloc[5]
    raw_row_index = df.index[df["timestamp"] == row["timestamp"]][0]
    assert row["kp_lag1h"] == pytest.approx(df.loc[raw_row_index - 1, "kp"])
    assert row["kp_lag2h"] == pytest.approx(df.loc[raw_row_index - 2, "kp"])


def test_targets_shift_forward_by_horizon():
    df = make_synthetic_frame()
    horizon = 3
    result = build_feature_table(df, lags=(1,), rolling_windows=(3,), horizon_hours=horizon)

    row = result.iloc[0]
    raw_row_index = df.index[df["timestamp"] == row["timestamp"]][0]
    assert row["kp_target"] == pytest.approx(df.loc[raw_row_index + horizon, "kp"])
    assert row["dst_target"] == pytest.approx(df.loc[raw_row_index + horizon, "dst_index"])


def test_rolling_mean_matches_manual_computation():
    df = make_synthetic_frame()
    result = build_feature_table(df, lags=(1,), rolling_windows=(3,), horizon_hours=1)

    row = result.iloc[2]
    raw_row_index = df.index[df["timestamp"] == row["timestamp"]][0]
    expected_mean = df.loc[raw_row_index - 2 : raw_row_index, "kp"].mean()
    assert row["kp_roll3h_mean"] == pytest.approx(expected_mean)


def test_dropna_trims_start_and_end_correctly():
    df = make_synthetic_frame(n_rows=20)
    max_lookback = 6  # largest of lags/rolling windows used below
    horizon = 2
    result = build_feature_table(df, lags=(1, max_lookback), rolling_windows=(3,), horizon_hours=horizon)

    expected_len = len(df) - max_lookback - horizon
    assert len(result) == expected_len
    # First surviving row must have full lookback history; last must have
    # a real (non-extrapolated) target within the original frame's range.
    assert result.iloc[0]["timestamp"] == df["timestamp"].iloc[max_lookback]
    assert result.iloc[-1]["timestamp"] == df["timestamp"].iloc[len(df) - 1 - horizon]


def test_derived_columns_match_hand_computed_values():
    df = make_synthetic_frame(n_rows=1)
    df.loc[0, ["plasma_speed", "proton_density", "bz_gsm", "by_gsm"]] = [500.0, 10.0, -5.0, 3.0]
    result = add_derived_columns(df)

    expected_pressure = 1.6726e-6 * 10.0 * 500.0**2
    expected_efield = -500.0 * -5.0 * 1e-3
    b_t = math.sqrt(3.0**2 + 5.0**2)
    theta_c = math.atan2(3.0, -5.0)
    expected_coupling = (500.0 ** (4 / 3)) * (b_t ** (2 / 3)) * (abs(math.sin(theta_c / 2)) ** (8 / 3))

    assert result.loc[0, "dynamic_pressure"] == pytest.approx(expected_pressure)
    assert result.loc[0, "motional_efield"] == pytest.approx(expected_efield)
    assert result.loc[0, "newell_coupling"] == pytest.approx(expected_coupling)


def test_feature_column_names_excludes_raw_and_target_columns():
    names = feature_column_names(lags=(1, 2), rolling_windows=(3,))
    # Regression test: a naive `startswith("kp_")` filter would wrongly
    # sweep up unrelated columns like "kp_raw" or "kp_target".
    assert "kp_raw" not in names
    assert "kp_target" not in names
    assert "dst_target" not in names
    assert "kp_lag1h" in names
    assert "dst_index_roll3h_mean" in names
    expected_count = len(BASE_FEATURE_COLUMNS + DERIVED_FEATURE_COLUMNS) * (2 + 2)
    assert len(names) == expected_count


def test_build_feature_table_has_no_nulls_in_required_columns():
    df = make_synthetic_frame()
    result = build_feature_table(df)
    required = feature_column_names() + ["kp_target", "dst_target"]
    assert not result[required].isna().any().any()
