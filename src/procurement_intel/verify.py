"""Check that every figure written by the language model comes from the evidence.

The model receives the evidence of an alert (or the facts of a view) as JSON.
Its text is scanned for numbers (amounts such as €1.2M, percentages, counts,
years); each must match a value of the evidence, allowing for the rounding of
a written figure. Legal references (R. 2194-8), CPV and NAF codes are not
figures and are ignored.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

_IGNORED = [
    re.compile(r"\b[RL]\.?\s?\d{4}-\d+(?:-\d+)?\b"),  # articles of the Code
    re.compile(r"\b\d{2}\.\d{2}[A-Z]\b"),  # NAF codes
    re.compile(r"\b\d{8}(?:-\d)?\b"),  # CPV codes
    re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b"),  # ISO dates, checked separately
]
_NUMBER = re.compile(
    r"(?P<currency>€\s?)?(?P<value>\d{1,3}(?:[ , ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s?(?P<unit>tn|trillion|bn|billion|M|million|k|thousand|%|percent)?(?![\w])",
)
_SCALE = {
    "tn": 1e12,
    "trillion": 1e12,
    "bn": 1e9,
    "billion": 1e9,
    "M": 1e6,
    "million": 1e6,
    "k": 1e3,
    "thousand": 1e3,
}


def evidence_numbers(evidence: object) -> list[float]:
    """Every number in the evidence, including numeric strings and the years
    and days of dates."""
    numbers: list[float] = []

    def walk(value: object) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, int | float):
            numbers.append(float(value))
        elif isinstance(value, str):
            if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
                numbers.append(float(value))
            for year, month, day in re.findall(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})\b", value):
                numbers.extend([float(year), float(month), float(day)])
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)

    walk(json.loads(evidence) if isinstance(evidence, str) else evidence)
    return numbers


def text_numbers(text: str) -> list[tuple[str, float, str]]:
    """(token, value, kind) for each figure of the text; kind is 'amount',
    'percent' or 'count'."""
    for pattern in _IGNORED:
        text = pattern.sub(" ", text)
    found = []
    for match in _NUMBER.finditer(text):
        raw = match.group("value").replace(",", "").replace(" ", "").replace(" ", "")
        value = float(raw)
        unit = match.group("unit")
        if unit in _SCALE:
            value *= _SCALE[unit]
        kind = (
            "percent"
            if unit in {"%", "percent"}
            else ("amount" if match.group("currency") or unit in _SCALE else "count")
        )
        found.append((match.group(0).strip(), value, kind))
    return found


def _matches(value: float, kind: str, candidates: Iterable[float]) -> bool:
    for candidate in candidates:
        if kind == "percent":
            # 64% matches a ratio of 0.64 (or 0.6437), and 64 itself.
            if abs(value - candidate * 100) <= 1 or abs(value - candidate) <= 1:
                return True
        elif kind == "amount":
            # €1.2M matches 1,234,567: a written amount keeps 2 significant digits.
            if candidate and abs(value - abs(candidate)) <= max(0.05 * abs(candidate), 1):
                return True
        elif abs(value - candidate) <= max(0.01 * abs(candidate), 0.5):
            return True
    return False


def unverified_figures(text: str, evidence: object, allowed: Iterable[float] = ()) -> list[str]:
    """Figures of `text` that match no value of the evidence. Small counts (up
    to 4, as in 'two checks') and the explicitly `allowed` values are accepted."""
    candidates = evidence_numbers(evidence) + [float(v) for v in allowed]
    unverified = []
    for token, value, kind in text_numbers(text):
        if kind == "count" and value <= 4 and value == int(value):
            continue
        if not _matches(value, kind, candidates):
            unverified.append(token)
    return unverified
