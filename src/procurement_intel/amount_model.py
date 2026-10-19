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
robust spread (1.4826 x MAD) to give a z-score comparable across segments.
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
    "verbose": -1,
}

# Contracts with amounts outside these bounds are never plausible and are not
# used to train the model.
MIN_AMOUNT, MAX_AMOUNT = 100, 1e10

FEATURES_SQL = f"""
WITH canonical AS (
    SELECT c.*
    FROM int_contracts AS c
    LEFT JOIN int_duplicates AS d USING (uid)
    WHERE coalesce(d.is_canonical, true)
),
buyer_scale AS (
    SELECT buyer_siret, median(log10(initial_amount)) AS buyer_median_log, count(*) AS n
    FROM canonical
    WHERE initial_amount BETWEEN {MIN_AMOUNT} AND {MAX_AMOUNT}
    GROUP BY buyer_siret
)
SELECT
    c.uid,
    log10(c.initial_amount) AS log_amount,
    coalesce(left(c.cpv_code, 2), '??') AS cpv_division,
    coalesce(left(c.cpv_code, 3), '???') AS cpv_group,
    c.contract_type,
    c.contract_nature,
    coalesce(c.is_framework, false)::INT AS is_framework,
    c.procedure_type,
    c.price_form,
    c.price_variation,
    c.buyer_category,
    c.buyer_region_code,
    h.holder_size AS lead_holder_size,
    year(c.notification_date) AS notification_year,
    c.initial_duration_months AS duration_months,
    c.offers_received,
    c.holder_count,
    -- The buyer's usual amount, when it has enough contracts to estimate it.
    CASE WHEN b.n >= 5 THEN b.buyer_median_log END AS buyer_median_log,
    coalesce(b.n, 0) AS buyer_contracts,
    len(string_split(coalesce(c.description, ''), ' ')) AS description_words
FROM canonical AS c
LEFT JOIN buyer_scale AS b USING (buyer_siret)
LEFT JOIN int_contract_holders AS h ON h.uid = c.uid AND h.holder_key = c.lead_holder_key
WHERE c.initial_amount BETWEEN {MIN_AMOUNT} AND {MAX_AMOUNT}
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


def robust_z(residuals: pd.Series, groups: pd.Series) -> pd.Series:
    """Residual divided by 1.4826 x MAD of the residuals of its group."""
    centred = residuals - residuals.groupby(groups).transform("median")
    mad = centred.abs().groupby(groups).transform("median")
    return centred / (1.4826 * mad.replace(0, np.nan))


def fit_and_score(frame: pd.DataFrame, folds: int = 3, seed: int = 42) -> AmountModelResult:
    """Cross-fitted predictions of log10(amount) for every row of `frame`."""
    features = prepare(frame)
    target = frame["log_amount"].to_numpy()
    predicted = np.empty(len(frame))
    fold_mae = []
    for train_idx, test_idx in KFold(folds, shuffle=True, random_state=seed).split(features):
        model = lgb.LGBMRegressor(**PARAMS)
        model.fit(features.iloc[train_idx], target[train_idx])
        predicted[test_idx] = model.predict(features.iloc[test_idx])
        fold_mae.append(float(np.mean(np.abs(predicted[test_idx] - target[test_idx]))))
    residual = pd.Series(target - predicted, index=frame.index)
    segment = frame["contract_type"].astype(str) + "/" + frame["is_framework"].astype(str)
    scores = pd.DataFrame(
        {
            "uid": frame["uid"].to_numpy(),
            "expected_amount": np.power(10.0, predicted),
            "amount_residual": residual.round(4).to_numpy(),
            "amount_z_model": robust_z(residual, segment).round(2).to_numpy(),
        }
    )
    total = np.sum((target - target.mean()) ** 2)
    median_baseline = np.mean(np.abs(target - np.median(target)))
    metrics = {
        "contracts": float(len(frame)),
        "mae_log10": float(np.mean(np.abs(residual))),
        "median_abs_error_log10": float(np.median(np.abs(residual))),
        "mae_log10_median_baseline": float(median_baseline),
        "r2_log10": float(1 - np.sum(residual.to_numpy() ** 2) / total),
        "fold_mae_log10": fold_mae,
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
