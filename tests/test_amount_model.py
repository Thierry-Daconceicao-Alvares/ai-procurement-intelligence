"""Model of expected amounts: robust z-scores and cross-fitted predictions."""

from __future__ import annotations

import numpy as np
import pandas as pd

from procurement_intel.amount_model import CATEGORICAL, NUMERIC, fit_and_score, robust_z


def test_robust_z_by_segment():
    residuals = pd.Series([-0.2, -0.1, 0.0, 0.1, 0.2, 0.0, 3.0])
    segments = pd.Series(["a"] * 7)
    z = robust_z(residuals, segments)
    assert abs(z.iloc[2]) < 1e-9  # the median
    assert abs(z.iloc[6] - 3.0 / (1.4826 * 0.1)) < 1e-6  # MAD of 0.1


def test_reference_rows_set_the_scale():
    residuals = pd.Series([0.0, 0.1, -0.1, 5.0, 5.0, 5.0])
    segments = pd.Series(["a"] * 6)
    reference = pd.Series([True, True, True, False, False, False])
    # The three large residuals are excluded from the median and the MAD.
    assert (robust_z(residuals, segments, reference=reference).iloc[3:] > 30).all()


def test_buyer_spread_is_shrunk_toward_the_segment():
    rng = np.random.default_rng(0)
    narrow = rng.normal(0, 0.2, 400)
    wide = rng.normal(0, 1.0, 400)
    residuals = pd.Series(np.concatenate([narrow, wide, [2.0], [2.0]]))
    segments = pd.Series(["s"] * len(residuals))
    buyers = pd.Series(["small"] * 400 + ["central"] * 400 + ["small", "central"])
    z_segment = robust_z(residuals, segments)
    z_buyer = robust_z(residuals, segments, buyers)
    # The same residual is less surprising for the buyer whose amounts vary a lot.
    assert z_buyer.iloc[-1] < z_segment.iloc[-1] < z_buyer.iloc[-2]


def synthetic_contracts(n: int = 4000, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    divisions = rng.choice(["45", "72", "15", "90"], n)
    level = {"45": 5.5, "72": 5.0, "15": 4.5, "90": 4.8}
    frame = pd.DataFrame({name: "x" for name in CATEGORICAL}, index=range(n))
    frame["cpv_division"] = divisions
    frame["cpv_group"] = divisions
    frame["contract_type"] = np.where(divisions == "45", "Works", "Services")
    for name in NUMERIC:
        frame[name] = 1.0
    frame["duration_months"] = rng.integers(1, 48, n)
    frame["is_framework"] = rng.integers(0, 2, n)
    frame["log_amount"] = (
        frame["cpv_division"].map(level)
        + 0.01 * frame["duration_months"]
        + 0.5 * frame["is_framework"]
        + rng.normal(0, 0.3, n)
    )
    frame["uid"] = [f"u{i}" for i in range(n)]
    frame["buyer_siret"] = rng.choice([f"b{i}" for i in range(40)], n)
    frame["trainable"] = True
    return frame


def test_cross_fitted_model_finds_unit_errors():
    frame = synthetic_contracts()
    errors = frame.index[:40]
    frame.loc[errors, "log_amount"] += 3  # amounts entered in euros instead of thousands
    result = fit_and_score(frame)
    z = result.scores["amount_z_model"]
    assert (z.loc[errors] >= 5).mean() > 0.9
    assert (z.drop(errors).abs() >= 5).mean() < 0.01
    assert result.metrics["contracts_trained"] == len(frame)
    assert result.metrics["r2_log10"] > 0.3


def test_untrainable_rows_are_scored_but_not_learned():
    frame = synthetic_contracts()
    frame.loc[:99, "log_amount"] = 9.0  # placeholders
    frame.loc[:99, "trainable"] = False
    result = fit_and_score(frame)
    assert result.metrics["contracts_trained"] == len(frame) - 100
    assert len(result.scores) == len(frame)
    # Not learned: their expected amounts stay at the level of their category.
    assert (np.log10(result.scores["expected_amount"].iloc[:100]) < 7).all()
