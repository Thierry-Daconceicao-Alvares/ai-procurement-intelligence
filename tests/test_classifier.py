"""Spend classification: text normalisation, time split, flags of wrong codes."""

from __future__ import annotations

import numpy as np
import pandas as pd

from procurement_intel.classifier import classify, evaluate, make_model, normalize, time_split
from procurement_intel.evaluation import code_injection

WORDS = {
    "45": ["travaux", "voirie", "chaussee", "renovation", "batiment", "toiture"],
    "15": ["denrees", "alimentaires", "repas", "fruits", "legumes", "viande"],
    "72": ["logiciel", "maintenance", "informatique", "developpement", "applicatif", "hebergement"],
}


def corpus(n: int = 900, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        division = list(WORDS)[i % 3]
        words = rng.choice(WORDS[division], size=4)
        rows.append(
            {
                "uid": f"u{i}",
                "text": normalize(" ".join(words) + f" lot {i}"),
                "cpv_division": division,
                "notification_date": pd.Timestamp("2023-01-01") + pd.Timedelta(days=int(i)),
            }
        )
    return pd.DataFrame(rows)


def test_normalize_keeps_letters_only():
    text = "Fourniture d’ÉLECTRICITÉ – lot n°3 &amp; gaz"
    assert normalize(text) == "fourniture d electricite lot n gaz"


def test_time_split_removes_texts_seen_in_training():
    frame = corpus()
    frame.loc[len(frame) - 1, "text"] = frame.loc[0, "text"]
    split = time_split(frame)
    assert split.train["notification_date"].max() < pd.Timestamp("2025-01-01")
    assert split.test["notification_date"].min() >= pd.Timestamp("2025-01-01")
    assert not set(split.test["text"]) & set(split.train["text"])


def test_model_learns_divisions_and_flags_wrong_codes():
    frame = corpus()
    model = make_model(max_features=1000).fit(frame["text"], frame["cpv_division"])
    metrics = evaluate(model, frame)
    assert metrics["accuracy"] > 0.95
    assert classify(model, "Travaux de renovation de la toiture")[0][0] == "45"
    injected = code_injection(model, frame, 0.5, 0.2, sample_size=300)
    assert injected["recall"] > 0.8
    assert injected["flag_rate_declared_codes"] < 0.05
    assert [r["min_probability"] for r in injected["trade_off"]] == [0.9, 0.7, 0.5, 0.0]
