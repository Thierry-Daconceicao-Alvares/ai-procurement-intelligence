"""Evaluation of the detectors on errors injected into real contracts.

Published procurement data has no labels: nobody says which amounts or codes
are wrong. To measure what the detectors find, known errors are injected into
a sample of contracts that look right, the detection runs again on the whole
data set, and two rates are compared:
- recall: the share of injected errors that the detector finds;
- flag rate: the share of the published contracts (scored by the model, before
  any injection) that the detector flags. Some of them are real errors, so this
  is an upper bound of the false alarm rate.

Amounts: a typing or unit error multiplies an amount by 10, 100 or 1,000, or
divides it by 100 or 1,000 (amounts entered in thousands of euros, cents read
as euros, extra zeros). The model of expected amounts (amount_model.py) is
compared with a simpler benchmark, the robust z-score of the amount within its
segment (04_amount_quality.sql).
"""

from __future__ import annotations

import logging

import duckdb
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

CORRUPTIONS = {"x10": 10.0, "x100": 100.0, "x1000": 1000.0, "/100": 0.01, "/1000": 0.001}
IMPLAUSIBLE_Z, REVIEW_Z = 5.0, 3.5


def segment_z(frame: pd.DataFrame, reference: pd.Series) -> pd.Series:
    """Robust z-score of log10(amount) within segments of comparable contracts
    (CPV division, type, framework or not, procedure group), or within the
    broader segment (type, framework or not) when the segment has fewer than
    50 contracts. Same method as 04_amount_quality.sql."""
    detailed = ["cpv_division", "contract_type", "is_framework", "procedure_group"]
    broad = ["contract_type", "is_framework"]
    log_amount = frame["log_amount"]
    ref = frame[reference]

    def stats(keys: list[str]) -> pd.DataFrame:
        grouped = ref.groupby(keys, dropna=False)["log_amount"]
        median = grouped.median().rename("median")
        table = pd.concat([median, grouped.size().rename("n")], axis=1)
        deviation = (ref["log_amount"] - ref.join(median, on=keys)["median"]).abs()
        table["mad"] = deviation.groupby([ref[k] for k in keys], dropna=False).median()
        return table

    fine = frame.join(stats(detailed), on=detailed)
    coarse = frame.join(stats(broad), on=broad)
    use_fine = fine["n"] >= 50
    median = fine["median"].where(use_fine, coarse["median"])
    mad = fine["mad"].where(use_fine, coarse["mad"])
    return (log_amount - median) / (1.4826 * mad.replace(0, np.nan))


def _buyer_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Recompute the buyer's usual and large amounts from the (corrupted)
    amounts, as FEATURES_SQL does."""
    kept = frame[frame["trainable"]]
    grouped = kept.groupby("buyer_siret")["log_amount"]
    stats = pd.DataFrame(
        {"median": grouped.median(), "p90": grouped.quantile(0.9), "n": grouped.size()}
    )
    joined = frame[["buyer_siret"]].join(stats, on="buyer_siret")
    enough = joined["n"] >= 5
    frame = frame.copy()
    frame["buyer_median_log"] = joined["median"].where(enough)
    frame["buyer_p90_log"] = joined["p90"].where(enough)
    frame["buyer_contracts"] = joined["n"].fillna(0)
    return frame


def amount_injection(
    con: duckdb.DuckDBPyConnection, sample_size: int = 20_000, seed: int = 7
) -> dict:
    """Inject unit and typing errors into contracts that both methods find
    typical, rerun the model and the benchmark, and measure recall."""
    # Imported here: the application uses this module without LightGBM.
    from .amount_model import FEATURES_SQL, MAX_AMOUNT, MIN_AMOUNT, fit_and_score

    rng = np.random.default_rng(seed)
    frame = con.execute(FEATURES_SQL).fetchdf()
    current = con.execute(
        "SELECT uid, amount_z_model, amount_z_segment FROM int_amount_status"
    ).fetchdf()
    frame = frame.merge(current, on="uid", how="left")
    typical = (
        frame["trainable"]
        & (frame["amount_z_model"].abs() < REVIEW_Z)
        & (frame["amount_z_segment"].abs() < REVIEW_Z)
    )
    candidates = np.flatnonzero(typical.to_numpy())
    chosen = rng.choice(candidates, size=min(sample_size, len(candidates)), replace=False)
    kinds = rng.choice(list(CORRUPTIONS), size=len(chosen))

    corrupted = frame.copy()
    factors = np.array([CORRUPTIONS[k] for k in kinds])
    corrupted.loc[chosen, "log_amount"] += np.log10(factors)
    amounts = np.power(10.0, corrupted["log_amount"])
    out_of_bounds = ((amounts < MIN_AMOUNT) | (amounts >= MAX_AMOUNT)).to_numpy()
    # Out-of-bounds amounts are caught by a rule and never reach the model.
    corrupted["trainable"] = corrupted["trainable"] & ~out_of_bounds
    corrupted = _buyer_features(corrupted)

    model_z = fit_and_score(corrupted).scores["amount_z_model"].abs().to_numpy()
    bench_z = segment_z(corrupted, corrupted["trainable"]).abs().to_numpy()

    injected = np.zeros(len(frame), dtype=bool)
    injected[chosen] = True
    # Alert volume of each method on the published data, before any injection:
    # a method that flags more contracts finds more errors at a higher cost.
    published = {
        "model": frame["amount_z_model"].abs().to_numpy(),
        "segment_benchmark": frame["amount_z_segment"].abs().to_numpy(),
    }
    results: dict = {
        "sample": len(chosen),
        "seed": seed,
        "selection": "contracts that pass the amount rules and that both the model and the "
        "benchmark find typical (|z| < 3.5)",
        "by_error": {},
    }
    for name, z in (("model", model_z), ("segment_benchmark", bench_z)):
        implausible = out_of_bounds | (np.nan_to_num(z) >= IMPLAUSIBLE_Z)
        review = out_of_bounds | (np.nan_to_num(z) >= REVIEW_Z)
        results[name] = {
            "recall_implausible": round(float(implausible[injected].mean()), 4),
            "recall_review": round(float(review[injected].mean()), 4),
            "flag_rate_published_implausible": round(
                float(np.mean(np.nan_to_num(published[name]) >= IMPLAUSIBLE_Z)), 5
            ),
            "flag_rate_published_review": round(
                float(np.mean(np.nan_to_num(published[name]) >= REVIEW_Z)), 5
            ),
        }
        for kind in CORRUPTIONS:
            rows = chosen[kinds == kind]
            entry = results["by_error"].setdefault(kind, {"contracts": len(rows)})
            entry[name] = {
                "recall_implausible": round(float(implausible[rows].mean()), 4),
                "recall_review": round(float(review[rows].mean()), 4),
            }
    logger.info("Amount injection: %s", {k: results[k] for k in ("model", "segment_benchmark")})
    return results


def code_injection(
    model,
    test: pd.DataFrame,
    min_probability: float,
    max_declared_probability: float,
    sample_size: int = 20_000,
    seed: int = 7,
) -> dict:
    """Replace the CPV division of test contracts (never seen in training) by
    another division drawn from the distribution of declared divisions, and
    measure how many of these wrong codes the flag rule finds.

    A contract is flagged when the model gives at least `min_probability` to a
    division other than the declared one, and at most
    `max_declared_probability` to the declared one."""
    rng = np.random.default_rng(seed)
    sample = test.sample(n=min(sample_size, len(test)), random_state=seed)
    classes = list(model.classes_)
    frequencies = test["cpv_division"].value_counts(normalize=True)
    proba = model.predict_proba(sample["text"])
    best = proba.argmax(axis=1)
    best_proba = proba[np.arange(len(sample)), best]

    def flagged(declared: np.ndarray, min_best: float, max_declared: float) -> np.ndarray:
        index = np.array([classes.index(d) for d in declared])
        declared_proba = proba[np.arange(len(sample)), index]
        return (best != index) & (best_proba >= min_best) & (declared_proba <= max_declared)

    original = sample["cpv_division"].to_numpy()
    wrong = original.copy()
    for i, code in enumerate(original):
        choices = frequencies.drop(code)
        wrong[i] = rng.choice(choices.index.to_numpy(), p=(choices / choices.sum()).to_numpy())

    def rates(min_best: float, max_declared: float) -> dict:
        return {
            "min_probability": min_best,
            "max_declared_probability": max_declared,
            "recall": round(float(flagged(wrong, min_best, max_declared).mean()), 4),
            "flag_rate_declared_codes": round(
                float(flagged(original, min_best, max_declared).mean()), 4
            ),
        }

    rule = rates(min_probability, max_declared_probability)
    return {
        "sample": len(sample),
        "seed": seed,
        "wrong_code": "another division, drawn from the distribution of declared divisions",
        "recall": rule["recall"],
        "flag_rate_declared_codes": rule["flag_rate_declared_codes"],
        # Looser rules find more wrong codes and flag more correct ones.
        "trade_off": [
            rates(best_min, declared_max)
            for best_min, declared_max in ((0.9, 0.02), (0.7, 0.02), (0.5, 0.05), (0.0, 0.05))
        ],
    }
