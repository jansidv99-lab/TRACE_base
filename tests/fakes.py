# Phase 2 – Step 2.7: test doubles for the OpenAI client — offline, instant and free
"""A fake that looks like `OpenAI()` to the agents: it has `.chat.completions.create(**kwargs)`.

Give it a *script*: a list where each item is either a reply string (returned as the model's
message) or an exception instance (raised, to simulate API failures). Every call is recorded in
`.calls`, so tests can check exactly what the agent sent.
"""

import json
from types import SimpleNamespace
from typing import Any

import httpx2
import openai

_REQUEST = httpx2.Request("POST", "https://fake.local/v1/chat/completions")


# Phase 2 – Step 2.7: build a response object shaped like the SDK's ChatCompletion
def make_response(
    content: str,
    model: str = "fake-model-2024-01-01",
    prompt_tokens: int = 100,
    completion_tokens: int = 20,
) -> SimpleNamespace:
    return SimpleNamespace(
        model=model,
        system_fingerprint="fp_fake123",
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


# Phase 2 – Step 2.7: scripted stand-in for the OpenAI client
class FakeLLMClient:
    def __init__(self, script: list[str | Exception]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    # Phase 2 – Step 2.7: record the call, then return or raise the next scripted item
    def _create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("FakeLLMClient called more times than scripted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return make_response(item)


# Phase 2 – Step 2.7: a valid RiskAnalystOutput as a JSON string (fields overridable)
def risk_json(**overrides: Any) -> str:
    data = {
        "reasoning": "Step 1: baseline. Step 2: patterns T001. Step 3: flags. Step 4: Other. "
        "Step 5: Low.",
        "key_indicators": ["T001 is consistent with the profile"],
        "classification": "Other",
        "risk_level": "Low",
        "confidence_score": 0.9,
    }
    data.update(overrides)
    return json.dumps(data)


# Phase 2 – Step 2.7: real openai exception objects, as the SDK would raise them
def timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=_REQUEST)


def _status_error(cls: type[openai.APIStatusError], code: int, message: str):
    return cls(message, response=httpx2.Response(code, request=_REQUEST), body=None)


def rate_limit_error() -> openai.RateLimitError:
    return _status_error(openai.RateLimitError, 429, "rate limited")


def server_error() -> openai.InternalServerError:
    return _status_error(openai.InternalServerError, 503, "service unavailable")


def auth_error() -> openai.AuthenticationError:
    return _status_error(openai.AuthenticationError, 401, "invalid api key")


def bad_request_error() -> openai.BadRequestError:
    return _status_error(openai.BadRequestError, 400, "bad request")
