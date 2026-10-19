"""Spend classification: predict the CPV division from the contract description.

Buyers choose the CPV code of their contracts themselves, and some choose a
wrong one, which puts spend in the wrong category. A text classifier learns
the link between descriptions and CPV divisions from the 1.5 million
contracts, then:
- measures how well a description predicts the division on contracts it has
  never seen (trained on 2019-2024, tested on 2025-2026);
- flags contracts whose declared division is very unlikely given their
  description, for a person to confirm or reject;
- classifies new descriptions in the application.

Model: TF-IDF of words and word pairs, then a linear classifier trained with
stochastic gradient descent on the logistic loss (one model per division).
A complement naive Bayes model is the baseline.
"""

from __future__ import annotations

import gc
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import duckdb
import joblib
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import accuracy_score, f1_score
from sklearn.naive_bayes import ComplementNB
from sklearn.pipeline import Pipeline

from .evaluation import code_injection

logger = logging.getLogger(__name__)

SPLIT_DATE = pd.Timestamp("2025-01-01")
# Flag a declared division only when the model is confident and the declared
# division is almost excluded.
FLAG_MIN_PROBA = 0.9
FLAG_MAX_DECLARED_PROBA = 0.02


def normalize(text: str) -> str:
    """Lower case, no accents, letters only (numbers and lot references carry
    no category information)."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"&#?\w+;", " ", text)
    text = re.sub(r"[^a-z ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def make_model(max_features: int = 200_000) -> Pipeline:
    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    ngram_range=(1, 2),
                    min_df=3,
                    max_features=max_features,
                    sublinear_tf=True,
                    dtype=np.float32,
                ),
            ),
            (
                "classifier",
                SGDClassifier(
                    loss="log_loss",
                    alpha=2e-6,
                    max_iter=12,
                    tol=None,
                    random_state=42,
                    n_jobs=-1,
                ),
            ),
        ]
    )


def make_baseline() -> Pipeline:
    return Pipeline(
        [
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=3, max_features=200_000)),
            ("classifier", ComplementNB(alpha=0.3)),
        ]
    )


def load_dataset(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    frame = con.execute(
        """
        SELECT uid, description, cpv_division, notification_date
        FROM fct_contracts
        WHERE description IS NOT NULL AND cpv_division IS NOT NULL
            AND length(description) >= 10
        ORDER BY uid
        """
    ).fetchdf()
    frame["text"] = frame["description"].map(normalize)
    frame["notification_date"] = pd.to_datetime(frame["notification_date"])
    return frame[frame["text"].str.len() > 0]


@dataclass
class Split:
    train: pd.DataFrame
    test: pd.DataFrame


def time_split(frame: pd.DataFrame) -> Split:
    """Train on 2019-2024, test on 2025-2026, without any test description
    that also appears in the training years (the same text is often reused)."""
    unique = frame.drop_duplicates(["text", "cpv_division"])
    train = unique[unique["notification_date"] < SPLIT_DATE]
    test = unique[unique["notification_date"] >= SPLIT_DATE]
    test = test[~test["text"].isin(set(train["text"]))]
    return Split(train, test)


def evaluate(model: Pipeline, test: pd.DataFrame) -> dict:
    labels = test["cpv_division"].to_numpy()
    predicted = model.predict(test["text"])
    metrics = {
        "test_contracts": len(test),
        "accuracy": round(float(accuracy_score(labels, predicted)), 4),
        "macro_f1": round(float(f1_score(labels, predicted, average="macro")), 4),
        "weighted_f1": round(float(f1_score(labels, predicted, average="weighted")), 4),
    }
    if hasattr(model, "predict_proba"):
        proba = model.predict_proba(test["text"])
        classes = model.classes_
        top3 = classes[np.argsort(proba, axis=1)[:, -3:]]
        metrics["top3_accuracy"] = round(
            float(np.mean([y in row for y, row in zip(labels, top3, strict=True)])), 4
        )
        best = proba.max(axis=1)
        metrics["by_confidence"] = [
            {
                "min_probability": threshold,
                "coverage": round(float(np.mean(best >= threshold)), 4),
                "accuracy": round(
                    float(np.mean(predicted[best >= threshold] == labels[best >= threshold])), 4
                ),
            }
            for threshold in (0.5, 0.7, 0.9)
        ]
    per_division = f1_score(labels, predicted, average=None, labels=np.unique(labels))
    metrics["f1_by_division"] = {
        division: round(float(score), 4)
        for division, score in zip(np.unique(labels), per_division, strict=True)
    }
    confusion = pd.crosstab(
        pd.Series(labels, name="declared"), pd.Series(predicted, name="predicted")
    )
    pairs = confusion.stack().rename("n").reset_index()
    pairs = pairs[pairs["declared"] != pairs["predicted"]].nlargest(10, "n")
    metrics["top_confusions"] = pairs.to_dict("records")
    return metrics


def predict_frame(model: Pipeline, frame: pd.DataFrame, batch: int = 200_000) -> pd.DataFrame:
    """Predicted division, its probability and the probability of the declared
    division, for each row of `frame` (in batches, to bound memory)."""
    if len(frame) > batch:
        return pd.concat(
            [
                predict_frame(model, frame.iloc[i : i + batch], batch)
                for i in range(0, len(frame), batch)
            ],
            ignore_index=True,
        )
    proba = model.predict_proba(frame["text"])
    classes = list(model.classes_)
    best = proba.argmax(axis=1)
    declared_index = frame["cpv_division"].map({c: i for i, c in enumerate(classes)})
    declared = np.array(
        [
            proba[row, int(col)] if pd.notna(col) else np.nan
            for row, col in enumerate(declared_index)
        ]
    )
    top3 = np.argsort(proba, axis=1)[:, ::-1][:, :3]
    return pd.DataFrame(
        {
            "uid": frame["uid"].to_numpy(),
            "declared_division": frame["cpv_division"].to_numpy(),
            "predicted_division": np.array(classes)[best],
            "predicted_probability": proba[np.arange(len(frame)), best].round(4),
            "declared_probability": declared.round(4),
            "top3_divisions": [",".join(np.array(classes)[row]) for row in top3],
        }
    )


def build_classifier(con: duckdb.DuckDBPyConnection, model_dir: Path) -> dict:
    """Evaluate the classifier, score every contract out of sample and save a
    compact model for the application. Creates int_cpv_predictions."""
    frame = load_dataset(con)
    split = time_split(frame)
    results: dict = {"train_contracts": len(split.train)}

    started = time.perf_counter()
    baseline = make_baseline().fit(split.train["text"], split.train["cpv_division"])
    results["baseline"] = evaluate(baseline, split.test)
    results["baseline"]["fit_seconds"] = round(time.perf_counter() - started)
    del baseline

    started = time.perf_counter()
    model = make_model().fit(split.train["text"], split.train["cpv_division"])
    results["model"] = evaluate(model, split.test)
    results["model"]["fit_seconds"] = round(time.perf_counter() - started)
    results["model"]["wrong_codes_injected"] = code_injection(
        model, split.test, FLAG_MIN_PROBA, FLAG_MAX_DECLARED_PROBA
    )
    logger.info(
        "Classifier: %s", {k: v for k, v in results["model"].items() if k != "f1_by_division"}
    )

    # Out-of-sample predictions for every contract: the 2025-2026 contracts are
    # scored by the model trained on 2019-2024, and the earlier contracts by a
    # model trained on 2025-2026.
    recent = frame[frame["notification_date"] >= SPLIT_DATE]
    earlier = frame[frame["notification_date"] < SPLIT_DATE]
    recent_predictions = predict_frame(model, recent)
    del model, split
    gc.collect()
    reverse = make_model().fit(
        recent.drop_duplicates(["text", "cpv_division"])["text"],
        recent.drop_duplicates(["text", "cpv_division"])["cpv_division"],
    )
    predictions = pd.concat([recent_predictions, predict_frame(reverse, earlier)])
    del reverse
    gc.collect()
    con.register("predictions", predictions)
    con.execute("CREATE OR REPLACE TABLE int_cpv_predictions AS SELECT * FROM predictions")
    con.unregister("predictions")
    flagged = predictions[
        (predictions["predicted_division"] != predictions["declared_division"])
        & (predictions["predicted_probability"] >= FLAG_MIN_PROBA)
        & (predictions["declared_probability"] <= FLAG_MAX_DECLARED_PROBA)
    ]
    results["flagged_contracts"] = len(flagged)

    # Compact model for the application, trained on every contract.
    model_dir.mkdir(parents=True, exist_ok=True)
    unique = frame.drop_duplicates(["text", "cpv_division"])
    compact = make_model(max_features=60_000).fit(unique["text"], unique["cpv_division"])
    classifier = compact.named_steps["classifier"]
    classifier.coef_ = classifier.coef_.astype(np.float32)
    joblib.dump(compact, model_dir / "cpv_classifier.joblib", compress=3)
    return results


def load_app_model(model_dir: Path) -> Pipeline | None:
    path = model_dir / "cpv_classifier.joblib"
    return joblib.load(path) if path.exists() else None


def classify(model: Pipeline, description: str, top: int = 3) -> list[tuple[str, float]]:
    """The `top` most likely CPV divisions of a description, with probabilities."""
    proba = model.predict_proba([normalize(description)])[0]
    order = np.argsort(proba)[::-1][:top]
    return [(model.classes_[i], float(proba[i])) for i in order]
