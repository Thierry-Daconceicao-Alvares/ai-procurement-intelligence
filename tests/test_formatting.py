from __future__ import annotations

import math

import pytest

from procurement_intel.formatting import compact, eur, pct, ratio_phrase


@pytest.mark.parametrize(
    ("amount", "text"),
    [
        (850, "€850"),
        (1_500, "€1.5k"),
        (12_345, "€12k"),
        (1_200_000, "€1.2M"),
        (123_400_000, "€123M"),
        (2_400_000_000, "€2.4bn"),
        (472_300_000_000, "€472bn"),
        (1_164_700_000_000, "€1.16tn"),
        (-5_000_000, "-€5M"),
        (None, "n/a"),
        (math.nan, "n/a"),
    ],
)
def test_eur(amount, text):
    assert eur(amount) == text


@pytest.mark.parametrize(
    ("count", "text"),
    [(850, "850"), (9_999, "9,999"), (29_037, "29k"), (160_677, "161k"), (1_526_229, "1.53M")],
)
def test_compact(count, text):
    assert compact(count) == text


def test_pct_and_ratio_phrase():
    assert pct(0.2049) == "20%"
    assert pct(0.2049, 1) == "20.5%"
    assert pct(None) == "n/a"
    assert ratio_phrase(290.4) == "290 times"
    assert ratio_phrase(2.5) == "2.5 times"
    assert ratio_phrase(0.025) == "1/40 of"
    assert ratio_phrase(0.0001) == "less than 1/1,000 of"
