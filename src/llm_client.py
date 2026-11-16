# Phase 2 – Step 2.1: single place that builds the OpenAI client from settings
"""LLM client factory.

Agents never create their own client: they *receive* one (dependency injection). Production code
passes `get_llm_client()`, and tests pass a fake with the same `chat.completions.create` shape.
"""

from openai import OpenAI

from src import config

# Phase 2 – Step 2.1: give up on a single request after this many seconds
DEFAULT_TIMEOUT_SECONDS = 30.0


# Phase 2 – Step 2.1: build the client for the configured OpenAI-compatible endpoint
def get_llm_client(timeout: float = DEFAULT_TIMEOUT_SECONDS) -> OpenAI:
    """Return an OpenAI client using OPENAI_API_KEY and OPENAI_BASE_URL from .env.

    `max_retries=0` turns off the SDK's silent built-in retries: the agents retry themselves
    (Step 2.5), so every attempt is visible in the audit log.
    """
    if not config.OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and fill in your key."
        )
    return OpenAI(
        api_key=config.OPENAI_API_KEY,
        base_url=config.OPENAI_BASE_URL or None,  # None → SDK default (api.openai.com)
        timeout=timeout,
        max_retries=0,
    )
