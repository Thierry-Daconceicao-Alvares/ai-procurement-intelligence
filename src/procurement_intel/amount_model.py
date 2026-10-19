"""Expected contract amount, learned from comparable contracts.

A gradient-boosting model (LightGBM) predicts the logarithm of a contract's
amount from what is known about it: category (CPV), type, procedure,
framework agreement or not, duration, buyer category, region and usual
amount of the buyer's contracts. It minimises the absolute error, so it
estimates a typical (median) amount and is little influenced by the errors it
has to find.

The predictions are cross-fitted: each contract is scored by a model trained
on the other folds, so that an error cannot be learned and then hidden. The
residual (reported minus expected, in orders of magnitude) is divided by its
robust spread (1.4826 x MAD) to give a z-score comparable across segments. The
spread is estimated per buyer, shrunk toward the spread of its segment: a
central purchasing body awards contracts of very different sizes, so a
framework agreement of one billion euros is less surprising there than at a
municipality.

Amounts that failed the rule-based checks of 04_amount_quality.sql
(placeholders, default ceilings) are scored but neither used for training nor
for the buyer's usual amounts: a buyer that enters 999,999,999 on half of its
contracts must not make that amount look normal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.model_selection import KFold

logger = logging.getLogger(__name__)

CATEGORICAL = [
    "cpv_division",
    "cpv_group",
    "contract_type",
    "contract_nature",
    "procedure_type",
    "price_form",
    "price_variation",
    "buyer_category",
    "buyer_region_code",
    "lead_holder_size",
]
NUMERIC = [
    "is_framework",
    "notification_year",
    "duration_months",
    "offers_received",
    "holder_count",
    "buyer_median_log",
    "buyer_p90_log",
    "buyer_contracts",
    "description_words",
]
FEATURES = CATEGORICAL + NUMERIC

PARAMS = {
    "objective": "l1",
    "n_estimators": 400,
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_child_samples": 50,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "random_state": 42,
    # Same results on every run and machine (at a small cost in speed).
    "deterministic": True,
    "force_row_wise": True,
    "verbose": -1,
}

# A buyer's own spread counts from this number of contracts of a segment, with
# the weight of its number of contracts against SPREAD_SHRINKAGE contracts for
# the spread of the segment. Below, a few errors would make their own spread.
SPREAD_MIN_CONTRACTS = 30
SPREAD_SHRINKAGE = 100

# Contracts with amounts outside these bounds are never plausible: they are
# not scored (04_amount_quality.sql flags them as out_of_bounds).
MIN_AMOUNT, MAX_AMOUNT = 100, 1e10

FEATURES_SQL = f"""
WITH canonical AS (
    SELECT c.*, q.amount_issue
    FROM int_contracts AS c
    JOIN int_amount_quality AS q USING (uid)
),
buyer_scale AS (
    SELECT
        buyer_siret,
        median(log10(initial_amount)) AS buyer_median_log,
        quantile_cont(log10(initial_amount), 0.9) AS buyer_p90_log,
        count(*) AS n
    FROM canonical
    WHERE initial_amount IS NOT NULL AND amount_issue IS NULL
    GROUP BY buyer_siret
)
SELECT
    c.uid,
    c.buyer_siret,
    c.amount_issue IS NULL AS trainable,
    log10(c.initial_amount) AS log_amount,
    coalesce(left(c.cpv_code, 2), '??') AS cpv_division,
    coalesce(left(c.cpv_code, 3), '???') AS cpv_group,
    c.contract_type,
    c.contract_nature,
    coalesce(c.is_framework, false)::INT AS is_framework,
    c.procedure_type,
    c.procedure_group,
    c.price_form,
    c.price_variation,
    c.buyer_category,
    c.buyer_region_code,
    h.holder_size AS lead_holder_size,
    year(c.notification_date) AS notification_year,
    c.initial_duration_months AS duration_months,
    c.offers_received,
    c.holder_count,
    -- The buyer's usual and large amounts, when it has enough contracts to
    -- estimate them.
    CASE WHEN b.n >= 5 THEN b.buyer_median_log END AS buyer_median_log,
    CASE WHEN b.n >= 5 THEN b.buyer_p90_log END AS buyer_p90_log,
    coalesce(b.n, 0) AS buyer_contracts,
    len(string_split(coalesce(c.description, ''), ' ')) AS description_words
FROM canonical AS c
LEFT JOIN buyer_scale AS b USING (buyer_siret)
LEFT JOIN int_contract_holders AS h ON h.uid = c.uid AND h.holder_key = c.lead_holder_key
WHERE c.initial_amount >= {MIN_AMOUNT} AND c.initial_amount < {MAX_AMOUNT}
-- A stable order: the folds, and so the scores, are the same on every run.
ORDER BY c.uid
"""


@dataclass
class AmountModelResult:
    scores: pd.DataFrame  # uid, expected_amount, amount_residual, amount_z_model
    metrics: dict[str, float]


def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    features = frame[FEATURES].copy()
    for name in CATEGORICAL:
        features[name] = features[name].astype("category")
    for name in NUMERIC:
        features[name] = pd.to_numeric(features[name], errors="coerce").astype("float64")
    return features


def robust_z(
    residuals: pd.Series,
    segments: pd.Series,
    buyers: pd.Series | None = None,
    reference: pd.Series | None = None,
    shrinkage: float = SPREAD_SHRINKAGE,
    min_contracts: int = SPREAD_MIN_CONTRACTS,
) -> pd.Series:
    """Robust z-score of residuals.

    Residuals are centred on the median of their segment and divided by
    1.4826 x a median absolute deviation (MAD): the segment's, or, when buyers
    are given, a mix of the buyer's own MAD in the segment and the segment's,
    weighted by the buyer's number of contracts n in the segment:
    sqrt((n * MAD_buyer^2 + shrinkage * MAD_segment^2) / (n + shrinkage)),
    with n set to 0 below `min_contracts`.
    Medians and MADs are computed on the reference rows (all rows by default).
    """
    if reference is None:
        reference = pd.Series(True, index=residuals.index)
    ref_segments = segments[reference]
    centre = segments.map(residuals[reference].groupby(ref_segments).median())
    deviation = (residuals - centre).abs()
    spread = segments.map(deviation[reference].groupby(ref_segments).median())
    if buyers is not None:
        stats = (
            deviation[reference].groupby([buyers[reference], ref_segments]).agg(["size", "median"])
        )
        keys = pd.MultiIndex.from_arrays([buyers, segments])
        n = stats["size"].reindex(keys).fillna(0).to_numpy()
        n = np.where(n >= min_contracts, n, 0)
        buyer_mad = stats["median"].reindex(keys).fillna(0).to_numpy()
        mixed = (n * buyer_mad**2 + shrinkage * spread.to_numpy() ** 2) / (n + shrinkage)
        spread = pd.Series(np.sqrt(mixed), index=residuals.index)
    return (residuals - centre) / (1.4826 * spread.replace(0, np.nan))


def fit_and_score(
    frame: pd.DataFrame, folds: int = 3, seed: int = 42, features: list[str] | None = None
) -> AmountModelResult:
    """Cross-fitted predictions of log10(amount) for every row of `frame`.

    Rows whose `trainable` column is false are predicted but never used to
    train a model, nor to compute the spread of the residuals; metrics are
    computed on the trainable rows."""
    columns = features or FEATURES
    data = prepare(frame)[columns]
    target = frame["log_amount"].to_numpy()
    trainable = (
        frame["trainable"].astype(bool).to_numpy()
        if "trainable" in frame
        else np.ones(len(frame), dtype=bool)
    )
    predicted = np.empty(len(frame))
    fold_mae = []
    for train_idx, test_idx in KFold(folds, shuffle=True, random_state=seed).split(data):
        train_idx = train_idx[trainable[train_idx]]
        model = lgb.LGBMRegressor(**PARAMS)
        model.fit(data.iloc[train_idx], target[train_idx])
        predicted[test_idx] = model.predict(data.iloc[test_idx])
        scored = test_idx[trainable[test_idx]]
        fold_mae.append(float(np.mean(np.abs(predicted[scored] - target[scored]))))
    residual = pd.Series(target - predicted, index=frame.index)
    segment = frame["contract_type"].astype(str) + "/" + frame["is_framework"].astype(str)
    reference = pd.Series(trainable, index=frame.index)
    buyers = frame.get("buyer_siret")
    z = robust_z(residual, segment, buyers, reference)
    scores = pd.DataFrame(
        {
            "uid": frame["uid"].to_numpy(),
            "expected_amount": np.power(10.0, predicted),
            "amount_residual": residual.round(4).to_numpy(),
            "amount_z_model": z.round(2).to_numpy(),
        }
    )
    kept, kept_residual = target[trainable], residual.to_numpy()[trainable]
    total = np.sum((kept - kept.mean()) ** 2)
    metrics = {
        "contracts_scored": len(frame),
        "contracts_trained": int(trainable.sum()),
        "features": columns,
        "mae_log10": float(np.mean(np.abs(kept_residual))),
        "median_abs_error_log10": float(np.median(np.abs(kept_residual))),
        "mae_log10_median_baseline": float(np.mean(np.abs(kept - np.median(kept)))),
        "r2_log10": float(1 - np.sum(kept_residual**2) / total),
        "fold_mae_log10": fold_mae,
        "spread_min_contracts": SPREAD_MIN_CONTRACTS,
        "spread_shrinkage_contracts": SPREAD_SHRINKAGE,
        "implausible_high": int((z >= 5).sum()),
        "implausible_low": int((z <= -5).sum()),
        "atypical": int(((z.abs() >= 3.5) & (z.abs() < 5)).sum()),
    }
    return AmountModelResult(scores, metrics)


def build_amount_model(con: duckdb.DuckDBPyConnection) -> dict[str, float]:
    """Score every canonical contract and store int_amount_model."""
    frame = con.execute(FEATURES_SQL).fetchdf()
    result = fit_and_score(frame)
    con.register("scores", result.scores)
    con.execute("CREATE OR REPLACE TABLE int_amount_model AS SELECT * FROM scores")
    con.unregister("scores")
    logger.info("Amount model: %s", result.metrics)
    return result.metrics
