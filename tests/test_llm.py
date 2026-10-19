"""Gemini errors are translated into what the application can explain."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from google.genai import errors as genai_errors

from procurement_intel import llm
from procurement_intel.llm import (
    Gemini,
    LLMError,
    LLMQuotaExhausted,
    LLMRateLimited,
    _quota,
    _translate,
    next_daily_reset,
)


def quota_error(quota_id: str, retry: str | None = "17s") -> genai_errors.ClientError:
    details = [
        {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{"quotaId": quota_id}],
        }
    ]
    if retry:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry})
    body = {
        "error": {
            "code": 429,
            "message": "quota",
            "status": "RESOURCE_EXHAUSTED",
            "details": details,
        }
    }
    return genai_errors.ClientError(429, body)


def test_quota_parsing_prefers_daily_quotas():
    error = quota_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier")
    error.details["error"]["details"][0]["violations"].append(
        {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}
    )
    quota, retry = _quota(error.details)
    assert "PerDay" in quota
    assert retry == 17.0


def test_daily_quota_is_not_retried():
    translated = _translate(quota_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier"))
    assert isinstance(translated, LLMQuotaExhausted)
    assert "resets" in str(translated)


def test_minute_quota_is_a_rate_limit():
    translated = _translate(quota_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"))
    assert isinstance(translated, LLMRateLimited)
    assert translated.retry_after_s == 17.0


def test_other_errors():
    assert "HTTP 400" in str(_translate(genai_errors.ClientError(400, {"error": {}})))
    assert "server error" in str(_translate(genai_errors.ServerError(503, {"error": {}})))
    assert "TimeoutError" in str(_translate(TimeoutError()))


def test_daily_reset_is_midnight_pacific():
    reset = next_daily_reset(datetime(2026, 10, 19, 12, 0, tzinfo=UTC))
    assert reset == datetime(2026, 10, 20, 7, 0, tzinfo=UTC)  # PDT is UTC-7


def test_rate_limits_are_retried_then_raised(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(llm, "_sleep", waits.append)
    client = Gemini.__new__(Gemini)
    client._max_retries = 2
    calls = {"n": 0}

    def request():
        calls["n"] += 1
        raise quota_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "3s")

    with pytest.raises(LLMRateLimited):
        client._call(request)
    assert calls["n"] == 3
    assert waits == [3.0, 3.0]


def test_daily_quota_stops_at_once(monkeypatch):
    monkeypatch.setattr(llm, "_sleep", lambda s: pytest.fail("must not wait"))
    client = Gemini.__new__(Gemini)
    client._max_retries = 3

    def request():
        raise quota_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier")

    with pytest.raises(LLMQuotaExhausted):
        client._call(request)
    assert issubclass(LLMQuotaExhausted, LLMError)
