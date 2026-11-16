# Phase 2 – Step 2.4: Risk Analyst agent — Chain-of-Thought classification of one case
"""Risk Analyst agent.

Flow for one case:
    CaseData → format_case_for_llm (masked, PII-free) → Chat Completions (system + user, JSON mode)
             → parse + validate as RiskAnalystOutput → unmask T### refs → result

Cases without transactions skip the LLM entirely (Stage 02 decision 2).

Error handling (Step 2.5):
- transient API errors (timeout, connection, rate limit, 5xx) → retry with exponential backoff
- permanent API errors (bad key, bad request, other 4xx)      → fail immediately
- output that is not valid JSON / fails validation            → one "repair" retry with the errors
- anything still failing                                      → RiskAnalysisError
Step 2.6 adds audit logging.
"""

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from openai import APIConnectionError, APIStatusError, OpenAIError, RateLimitError
from pydantic import ValidationError

from src import config
from src.case_formatter import format_case_for_llm, unmask
from src.foundation_sar import CaseData, ExplainabilityLogger, RiskAnalystOutput
from src.prompts import load_prompt

# Phase 2 – Step 2.4: the fixed answer for cases with no activity (no API call needed)
NO_ACTIVITY_RESULT = RiskAnalystOutput(
    classification="Other",
    confidence_score=1.0,
    risk_level="Low",
    reasoning=(
        "Step 1: Customer has accounts but no transactions in the review period. "
        "Step 2: No activity to analyse. Step 3: No red flags. "
        "Step 4: Other - no suspicious activity. Step 5: Low risk; rule-based result, no LLM call."
    ),
    key_indicators=["No account activity in the review period"],
)

# Phase 2 – Step 2.5: ```json ... ``` (or plain ```) fences some models wrap around JSON
_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


# Phase 2 – Step 2.5: the model's reply could not be turned into a valid result
class InvalidLLMOutputError(ValueError):
    pass


# Phase 2 – Step 2.5: final failure for one case — the workflow routes it to manual review
class RiskAnalysisError(Exception):
    def __init__(self, case_id: str, reason: str, attempts: int) -> None:
        super().__init__(
            f"Risk analysis failed for {case_id} after {attempts} attempt(s): {reason}"
        )
        self.case_id = case_id
        self.reason = reason
        self.attempts = attempts


# Phase 2 – Step 2.4: facts about the last analysis (feeds the audit log and cost metrics)
@dataclass
class AnalysisRun:
    case_id: str
    method: Literal["llm", "rule_based"]
    model: str | None = None  # model name returned by the API, e.g. gpt-4o-mini-2024-07-18
    token_usage: dict[str, int] = field(default_factory=dict)  # summed over all attempts
    latency_ms: float = 0.0
    attempts: int = 0  # API calls made, including retries and repairs
    repairs: int = 0  # Phase 2 – Step 2.5: how many repair prompts were needed
    error: str | None = None  # Phase 2 – Step 2.5: set when the analysis failed


# Phase 2 – Step 2.5: pull a JSON object out of a model reply (plain, fenced, or wrapped in prose)
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


# Phase 2 – Step 2.5: errors worth retrying — they usually succeed a moment later
def is_transient(error: Exception) -> bool:
    if isinstance(error, APIConnectionError | RateLimitError):  # includes APITimeoutError
        return True
    return isinstance(error, APIStatusError) and error.status_code >= 500


# Phase 2 – Step 2.5: short, model-readable summary of why an answer was rejected
def _describe(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'object'}: {e['msg']}" for e in error.errors()
        )
    return str(error)


# Phase 2 – Step 2.4: the agent; the LLM client is injected (real OpenAI client or a test fake)
class RiskAnalystAgent:
    # Phase 2 – Step 2.4: configure model, prompt version and temperature; load the prompt once
    # Phase 2 – Step 2.5: plus retry limits and an injectable sleep (tests pass a no-op)
    def __init__(
        self,
        client: Any,
        logger: ExplainabilityLogger,
        model: str | None = None,
        prompt_version: str | None = None,
        temperature: float = 0.0,
        max_api_attempts: int = 3,
        max_repairs: int = 1,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.logger = logger
        self.model = model or config.MODEL
        self.prompt_version = prompt_version or config.PROMPT_VERSION
        self.temperature = temperature
        self.max_api_attempts = max_api_attempts
        self.max_repairs = max_repairs
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep
        self.system_prompt = load_prompt("risk_analyst", self.prompt_version)
        self.last_run: AnalysisRun | None = None
        if not self.model:
            raise ValueError("No model configured: set MODEL in .env or pass model=...")

    # Phase 2 – Step 2.4: analyse one case and return a validated, unmasked result
    # Phase 2 – Step 2.5: with repair retries; raises RiskAnalysisError on final failure
    def analyze_case(self, case: CaseData) -> RiskAnalystOutput:
        if not case.transactions:
            self.last_run = AnalysisRun(case_id=case.case_id, method="rule_based")
            return NO_ACTIVITY_RESULT

        formatted = format_case_for_llm(case)
        run = AnalysisRun(case_id=case.case_id, method="llm")
        self.last_run = run
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": formatted.text},
        ]
        start = time.perf_counter()
        try:
            for repair in range(self.max_repairs + 1):
                raw = self._complete(messages, run)
                try:
                    result = RiskAnalystOutput.model_validate(extract_json(raw))
                    return _unmask_output(result, formatted.id_map)
                except (InvalidLLMOutputError, ValidationError) as exc:
                    if repair == self.max_repairs:
                        raise RiskAnalysisError(
                            case.case_id, f"invalid output: {_describe(exc)}", run.attempts
                        ) from exc
                    run.repairs += 1
                    messages += [
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": _repair_prompt(exc)},
                    ]
        except OpenAIError as exc:
            raise RiskAnalysisError(
                case.case_id, f"API error: {type(exc).__name__}: {exc}", run.attempts
            ) from exc
        except RiskAnalysisError as exc:
            run.error = exc.reason
            raise
        finally:
            run.latency_ms = (time.perf_counter() - start) * 1000
        raise AssertionError("unreachable")  # pragma: no cover

    # Phase 2 – Step 2.5: one completion, retrying transient API errors with exponential backoff
    def _complete(self, messages: list[dict[str, str]], run: AnalysisRun) -> str:
        for attempt in range(self.max_api_attempts):
            run.attempts += 1
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=self.temperature,
                    response_format={"type": "json_object"},
                    messages=messages,
                )
            except OpenAIError as exc:
                if not is_transient(exc) or attempt == self.max_api_attempts - 1:
                    run.error = f"{type(exc).__name__}: {exc}"
                    raise
                self.sleep(self.backoff_seconds * 2**attempt)  # 1s, 2s, 4s, ...
                continue
            run.model = getattr(response, "model", None) or run.model
            _add_usage(run.token_usage, response)
            choices = getattr(response, "choices", None) or []
            return (choices[0].message.content or "") if choices else ""
        raise AssertionError("unreachable")  # pragma: no cover


# Phase 2 – Step 2.5: follow-up message asking the model to fix its previous answer
def _repair_prompt(error: Exception) -> str:
    return (
        f"Your previous answer could not be accepted: {_describe(error)}. "
        "Reply with ONLY a corrected JSON object with exactly these keys: reasoning, "
        "key_indicators, classification, risk_level, confidence_score."
    )


# Phase 2 – Step 2.4: put real transaction IDs back into the free-text fields
def _unmask_output(result: RiskAnalystOutput, id_map: dict[str, str]) -> RiskAnalystOutput:
    return result.model_copy(
        update={
            "reasoning": unmask(result.reasoning, id_map),
            "key_indicators": [unmask(k, id_map) for k in result.key_indicators],
        }
    )


# Phase 2 – Step 2.4: add one response's token counts to the running total
def _add_usage(totals: dict[str, int], response: Any) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        totals[key] = totals.get(key, 0) + (getattr(usage, key, 0) or 0)
