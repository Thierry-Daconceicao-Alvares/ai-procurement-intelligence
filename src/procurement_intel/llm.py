"""Calls to the Gemini API with a JSON schema, retries and clear errors.

The model only writes text from evidence computed by the code; every number it
writes is checked afterwards (verify.py). Errors are translated into three
kinds that the application can explain: a per-minute rate limit (retried after
the delay the API asks for), a used-up daily quota (not retried) and other
failures.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from typing import TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
MAX_RATE_LIMIT_WAIT_S = 30.0
_sleep = time.sleep  # replaced in the tests


class LLMError(Exception):
    """The model could not produce a usable answer."""


class LLMQuotaExhausted(LLMError):
    """The daily quota of the API key is used up; retrying before the reset is pointless."""

    def __init__(self, message: str, resets_at: datetime | None = None) -> None:
        super().__init__(message)
        self.resets_at = resets_at


class LLMRateLimited(LLMError):
    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    output_tokens: int


def next_daily_reset(now: datetime | None = None) -> datetime | None:
    """Daily quotas of the Gemini API reset at midnight Pacific time."""
    try:
        pacific = ZoneInfo("America/Los_Angeles")
    except ZoneInfoNotFoundError:  # pragma: no cover - depends on the system
        return None
    local = (now or datetime.now(UTC)).astimezone(pacific)
    midnight = datetime.combine(local.date() + timedelta(days=1), clock_time(0), tzinfo=pacific)
    return midnight.astimezone(UTC)


def _quota(details: object) -> tuple[str, float | None]:
    """Violated quota id (daily quotas first) and retry delay of a 429 response."""
    error = details.get("error", details) if isinstance(details, dict) else {}
    quota_ids: list[str] = []
    retry_after = None
    for item in (error.get("details") if isinstance(error, dict) else None) or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("@type", ""))
        if kind.endswith("QuotaFailure"):
            quota_ids += [str(v.get("quotaId", "")) for v in item.get("violations") or []]
        elif kind.endswith("RetryInfo") and str(item.get("retryDelay", "")).endswith("s"):
            try:
                retry_after = float(str(item["retryDelay"])[:-1])
            except ValueError:
                retry_after = None
    quota_ids.sort(key=lambda q: "PerDay" not in q)
    return (quota_ids[0] if quota_ids else ""), retry_after


def _translate(exc: Exception) -> LLMError:
    if isinstance(exc, genai_errors.ClientError) and exc.code == 429:
        quota, retry_after = _quota(exc.details)
        if "PerDay" in quota:
            resets_at = next_daily_reset()
            when = f"at {resets_at:%H:%M} UTC" if resets_at else "at midnight Pacific time"
            return LLMQuotaExhausted(
                f"The daily quota of the Gemini API key is used up. It resets {when}.",
                resets_at,
            )
        return LLMRateLimited("Gemini rate limit reached; retry shortly.", retry_after)
    if isinstance(exc, genai_errors.ClientError):
        return LLMError(f"Gemini rejected the request (HTTP {exc.code}).")
    if isinstance(exc, genai_errors.ServerError):
        return LLMError(f"Gemini server error (HTTP {exc.code}); retry later.")
    return LLMError(f"Gemini request failed ({exc.__class__.__name__}).")


class Gemini:
    def __init__(self, api_key: str, model: str, max_retries: int = 3) -> None:
        self.model = model
        self._max_retries = max_retries
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=60_000,
                retry_options=types.HttpRetryOptions(
                    attempts=max_retries + 1,
                    initial_delay=2.0,
                    max_delay=30.0,
                    http_status_codes=[408, 500, 502, 503, 504],
                ),
            ),
        )

    def _call(self, request: Callable[[], types.GenerateContentResponse]):
        attempt = 0
        while True:
            try:
                return request()
            except Exception as exc:
                error = _translate(exc)
                if not isinstance(error, LLMRateLimited) or attempt >= self._max_retries:
                    raise error from exc
                attempt += 1
                wait = error.retry_after_s if error.retry_after_s is not None else 2.0 * 2**attempt
                _sleep(min(max(wait, 1.0), MAX_RATE_LIMIT_WAIT_S))

    def generate(self, system: str, user: str, schema: type[T]) -> tuple[T, Usage, float]:
        """Structured answer, token usage and latency in seconds."""
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
        )
        started = time.perf_counter()
        response = self._call(
            lambda: self._client.models.generate_content(
                model=self.model, contents=user, config=config
            )
        )
        latency = time.perf_counter() - started
        parsed = response.parsed
        if not isinstance(parsed, schema):
            try:
                parsed = schema.model_validate_json(response.text or "")
            except Exception as exc:
                raise LLMError("The model did not return a usable answer.") from exc
        metadata = response.usage_metadata
        usage = Usage(
            prompt_tokens=(metadata.prompt_token_count or 0) if metadata else 0,
            output_tokens=(metadata.candidates_token_count or 0) if metadata else 0,
        )
        return parsed, usage, latency
