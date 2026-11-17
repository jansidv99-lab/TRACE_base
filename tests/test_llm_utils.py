# Phase 3 – Step 3.1: tests for the shared LLM helpers (moved from test_risk_analyst.py)
"""JSON extraction, transient-error classification and call_with_retry, shared by all agents."""

from types import SimpleNamespace

import pytest

from src.llm_utils import (
    InvalidLLMOutputError,
    add_usage,
    call_with_retry,
    extract_json,
    is_transient,
)
from tests.fakes import (
    auth_error,
    bad_request_error,
    make_response,
    rate_limit_error,
    server_error,
    timeout_error,
)

# ===========================================================================
# JSON extraction and error classification
# ===========================================================================


# Phase 2 – Step 2.7: JSON is found in the shapes models actually return
@pytest.mark.parametrize(
    "reply",
    [
        '{"a": 1}',
        '  {"a": 1}\n',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Here is my analysis:\n{"a": 1}\nThanks!',
        'Sure! ```JSON {"a": 1} ``` done',
    ],
)
def test_extract_json_accepts_common_shapes(reply):
    assert extract_json(reply) == {"a": 1}


# Phase 2 – Step 2.7: anything that is not a JSON object is rejected
@pytest.mark.parametrize("reply", ["", "not json", "[1, 2]", '{"a": ', "null"])
def test_extract_json_rejects_garbage(reply):
    with pytest.raises(InvalidLLMOutputError):
        extract_json(reply)


# Phase 2 – Step 2.7: which API errors are worth retrying
@pytest.mark.parametrize(
    ("error", "transient"),
    [
        (timeout_error(), True),
        (rate_limit_error(), True),
        (server_error(), True),
        (auth_error(), False),
        (bad_request_error(), False),
    ],
)
def test_is_transient(error, transient):
    assert is_transient(error) is transient


# ===========================================================================
# call_with_retry (Phase 3 – Step 3.1)
# ===========================================================================


# Phase 3 – Step 3.1: minimal stats object with the fields call_with_retry records
def _stats():
    return SimpleNamespace(attempts=0, token_usage={}, model=None, system_fingerprint=None,
                           error=None)


# Phase 3 – Step 3.1: helper — a create() that raises/returns scripted items in order
def _scripted(*items):
    queue = list(items)

    def create():
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return create


# Phase 3 – Step 3.1: success returns the full response and records model, tokens, fingerprint
def test_call_with_retry_success_records_stats():
    stats, sleeps = _stats(), []
    response = call_with_retry(_scripted(make_response("hi")), stats, sleep=sleeps.append)
    assert response.choices[0].message.content == "hi"
    assert stats.attempts == 1
    assert stats.model == "fake-model-2024-01-01"
    assert stats.system_fingerprint == "fp_fake123"
    assert stats.token_usage == {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    assert sleeps == []


# Phase 3 – Step 3.1: transient errors are retried with exponential backoff
def test_call_with_retry_backoff():
    stats, sleeps = _stats(), []
    call_with_retry(_scripted(timeout_error(), server_error(), make_response("ok")), stats,
                    sleep=sleeps.append, backoff_seconds=0.5)
    assert stats.attempts == 3
    assert sleeps == [0.5, 1.0]


# Phase 3 – Step 3.1: gives up after max_attempts, recording the error
def test_call_with_retry_gives_up():
    stats, sleeps = _stats(), []
    with pytest.raises(type(rate_limit_error())):
        call_with_retry(_scripted(*[rate_limit_error()] * 2), stats, sleep=sleeps.append,
                        max_attempts=2)
    assert stats.attempts == 2
    assert "RateLimitError" in stats.error


# Phase 3 – Step 3.1: permanent errors are raised immediately
def test_call_with_retry_permanent_error_not_retried():
    stats, sleeps = _stats(), []
    with pytest.raises(type(auth_error())):
        call_with_retry(_scripted(auth_error(), make_response("never")), stats,
                        sleep=sleeps.append)
    assert stats.attempts == 1
    assert sleeps == []


# Phase 3 – Step 3.1: token usage accumulates across responses; missing usage is ignored
def test_add_usage_accumulates():
    totals = {}
    add_usage(totals, make_response("a"))
    add_usage(totals, make_response("b", prompt_tokens=1, completion_tokens=1))
    add_usage(totals, SimpleNamespace(usage=None))
    assert totals == {"prompt_tokens": 101, "completion_tokens": 21, "total_tokens": 122}
