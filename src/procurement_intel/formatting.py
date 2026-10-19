"""Number formatting shared by alert titles, the application and the LLM checks."""

from __future__ import annotations

import math


def _missing(value: float | None) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _three_digits(value: float) -> str:
    """At most three significant digits, without trailing zeros: 1.16, 14.0 -> 14, 472."""
    if value >= 100:
        text = f"{value:.0f}"
    elif value >= 10:
        text = f"{value:.1f}"
    else:
        text = f"{value:.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def eur(amount: float | None) -> str:
    """Euros, rounded to a readable precision: €850, €12k, €1.2M, €472bn, €1.16tn."""
    if _missing(amount):
        return "n/a"
    sign = "-" if amount < 0 else ""
    value = abs(float(amount))
    if value >= 1e12:
        text = f"{_three_digits(value / 1e12)}tn"
    elif value >= 1e9:
        text = f"{value / 1e9:.1f}bn" if value < 1e11 else f"{value / 1e9:.0f}bn"
    elif value >= 1e6:
        text = f"{value / 1e6:.1f}M" if value < 1e8 else f"{value / 1e6:.0f}M"
    elif value >= 1e4:
        text = f"{value / 1e3:.0f}k"
    elif value >= 1e3:
        text = f"{value / 1e3:.1f}k"
    else:
        text = f"{value:.0f}"
    return f"{sign}€{text.replace('.0bn', 'bn').replace('.0M', 'M').replace('.0k', 'k')}"


def compact(count: float | None) -> str:
    """Counts for stat tiles: 850, 29k, 161k, 1.53M."""
    if _missing(count):
        return "n/a"
    value = float(count)
    if abs(value) >= 1e6:
        return f"{_three_digits(value / 1e6)}M"
    if abs(value) >= 1e4:
        return f"{_three_digits(value / 1e3)}k"
    return f"{value:,.0f}"


def pct(ratio: float | None, digits: int = 0) -> str:
    if _missing(ratio):
        return "n/a"
    return f"{ratio * 100:.{digits}f}%"


def ratio_phrase(ratio: float) -> str:
    """'12 times', '2.5 times' or '1/40 of', to compare an amount with the
    expected one."""
    if ratio >= 1:
        return f"{ratio:,.0f} times" if ratio >= 10 else f"{ratio:.1f} times"
    if ratio < 0.001:
        return "less than 1/1,000 of"
    return f"1/{1 / ratio:,.0f} of"
