# Phase 3 – Step 3.1: shared LLM helpers used by every agent (moved out of risk_analyst_agent.py)
"""One tested implementation of the plumbing every agent needs:

- `call_with_retry`: one API call, retrying transient errors with exponential backoff and
  recording attempts, tokens, model and system fingerprint on a stats object
- `extract_json`: pull a JSON object out of a model reply (plain, fenced, or wrapped in prose)
- `is_transient`: which API errors are worth retrying
- `describe_error`: short, model-readable explanation of why an answer was rejected

Originally written in Phase 2 – Step 2.5/2.7; behaviour is unchanged by the move.
"""

import json
import re
from collections.abc import Callable
from typing import Any, Protocol

from openai import APIConnectionError, APIStatusError, OpenAIError, RateLimitError
from pydantic import ValidationError

# Phase 2 – Step 2.7 (moved in 3.1): fixed sampling seed for reproducible runs (best effort)
DEFAULT_SEED = 42

# Phase 2 – Step 2.5 (moved in 3.1): ```json ... ``` (or plain ```) fences around JSON
_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


# Phase 2 – Step 2.5 (moved in 3.1): the model's reply could not be turned into a valid result
class InvalidLLMOutputError(ValueError):
    pass


# Phase 3 – Step 3.1: what call_with_retry needs to record (AnalysisRun and later runs match it)
class CallStats(Protocol):
    attempts: int
    token_usage: dict[str, int]
    model: str | None
    system_fingerprint: str | None
    error: str | None


# Phase 2 – Step 2.5 (moved in 3.1): pull a JSON object out of a model reply
def extract_json(text: str) -> dict[str, Any]:
    candidates = [text.strip()]
    fenced = _FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise InvalidLLMOutputError(f"no JSON object found in model reply: {text[:200]!r}")


# Phase 2 – Step 2.5 (moved in 3.1): errors worth retrying — they usually succeed a moment later
def is_transient(error: Exception) -> bool:
    if isinstance(error, APIConnectionError | RateLimitError):  # includes APITimeoutError
        return True
    return isinstance(error, APIStatusError) and error.status_code >= 500


# Phase 2 – Step 2.5 (moved in 3.1): short, model-readable summary of why an answer was rejected
def describe_error(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'object'}: {e['msg']}" for e in error.errors()
        )
    return str(error)


# Phase 2 – Step 2.4 (moved in 3.1): add one response's token counts to the running total
def add_usage(totals: dict[str, int], response: Any) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        totals[key] = totals.get(key, 0) + (getattr(usage, key, 0) or 0)


# Phase 3 – Step 3.1: one API call with exponential backoff; returns the full response object
def call_with_retry(
    create: Callable[[], Any],
    stats: CallStats,
    *,
    max_attempts: int = 3,
    backoff_seconds: float = 1.0,
    sleep: Callable[[float], None],
) -> Any:
    """Call `create()` until it succeeds, retrying transient errors (waits 1s, 2s, 4s, ...).

    Permanent errors, or transient ones that outlast `max_attempts`, are re-raised after
    recording `stats.error`. Every attempt increments `stats.attempts`; a success adds its
    token usage and records the model name and system fingerprint the API reported.
    """
    for attempt in range(max_attempts):
        stats.attempts += 1
        try:
            response = create()
        except OpenAIError as exc:
            if not is_transient(exc) or attempt == max_attempts - 1:
                stats.error = f"{type(exc).__name__}: {exc}"
                raise
            sleep(backoff_seconds * 2**attempt)
            continue
        stats.model = getattr(response, "model", None) or stats.model
        stats.system_fingerprint = (
            getattr(response, "system_fingerprint", None) or stats.system_fingerprint
        )
        add_usage(stats.token_usage, response)
        return response
    raise AssertionError("unreachable")  # pragma: no cover
