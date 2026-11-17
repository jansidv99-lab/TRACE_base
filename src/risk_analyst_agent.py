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

Audit (Step 2.6): every analyze_case call writes exactly one ExplainabilityLogger event.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from openai import OpenAIError
from pydantic import ValidationError

from src import config
from src.case_formatter import format_case_for_llm, unmask
from src.foundation_sar import CaseData, ExplainabilityLogger, RiskAnalystOutput
from src.llm_utils import (  # Phase 3 – Step 3.1: shared helpers
    DEFAULT_SEED,
    InvalidLLMOutputError,
    call_with_retry,
    describe_error,
    extract_json,
)
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
    # Phase 2 – Step 2.7: backend config that served the request; with `seed`, same fingerprint
    # + same input should give the same answer (best effort, not guaranteed)
    system_fingerprint: str | None = None
    error: str | None = None  # Phase 2 – Step 2.5: set when the analysis failed


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
        seed: int | None = DEFAULT_SEED,
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
        self.seed = seed  # Phase 2 – Step 2.7
        self.max_api_attempts = max_api_attempts
        self.max_repairs = max_repairs
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep
        self.system_prompt = load_prompt("risk_analyst", self.prompt_version)
        self.last_run: AnalysisRun | None = None
        if not self.model:
            raise ValueError("No model configured: set MODEL in .env or pass model=...")

    # Phase 2 – Step 2.6: public entry point — analyse one case and ALWAYS write an audit event
    def analyze_case(self, case: CaseData) -> RiskAnalystOutput:
        """Return a validated RiskAnalystOutput, or raise RiskAnalysisError.

        Exactly one audit event is written per call: success, rule-based or failure.
        """
        try:
            result = self._analyze(case)
        except RiskAnalysisError as exc:
            self._log(case, result=None, error=exc.reason)
            raise
        self._log(case, result=result, error=None)
        return result

    # Phase 2 – Step 2.6: one audit record with everything an examiner needs to replay the decision
    def _log(self, case: CaseData, result: RiskAnalystOutput | None, error: str | None) -> None:
        run = self.last_run or AnalysisRun(case_id=case.case_id, method="llm")
        txns = case.transactions
        self.logger.log_agent_action(
            agent_type="RiskAnalyst",
            action="analyze_case",
            case_id=case.case_id,
            # A summary, not the full prompt: the prompt is reproducible from case + prompt_version
            input_data={
                "customer_id": case.customer.customer_id,
                "accounts": len(case.accounts),
                "transactions": len(txns),
                "period": [str(txns[0].transaction_date), str(txns[-1].transaction_date)]
                if txns
                else None,
                "data_quality_flags": len(case.data_quality_flags),
            },
            output_data=result,
            reasoning=result.reasoning if result else f"Analysis failed: {error}",
            execution_time_ms=run.latency_ms,
            success=error is None,
            error_message=error,
            prompt_version=self.prompt_version,
            method=run.method,
            requested_model=self.model if run.method == "llm" else None,
            model=run.model,
            temperature=self.temperature if run.method == "llm" else None,
            seed=self.seed if run.method == "llm" else None,
            system_fingerprint=run.system_fingerprint,
            token_usage=run.token_usage,
            attempts=run.attempts,
            repairs=run.repairs,
        )

    # Phase 2 – Step 2.4: analyse one case and return a validated, unmasked result
    # Phase 2 – Step 2.5: with repair retries; raises RiskAnalysisError on final failure
    def _analyze(self, case: CaseData) -> RiskAnalystOutput:
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
                            case.case_id, f"invalid output: {describe_error(exc)}", run.attempts
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
    # Phase 3 – Step 3.1: retry/backoff/accounting now delegated to llm_utils.call_with_retry
    def _complete(self, messages: list[dict[str, str]], run: AnalysisRun) -> str:
        response = call_with_retry(
            lambda: self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                seed=self.seed,
                response_format={"type": "json_object"},
                messages=messages,
            ),
            run,
            max_attempts=self.max_api_attempts,
            backoff_seconds=self.backoff_seconds,
            sleep=self.sleep,
        )
        choices = getattr(response, "choices", None) or []
        return (choices[0].message.content or "") if choices else ""


# Phase 2 – Step 2.5: follow-up message asking the model to fix its previous answer
def _repair_prompt(error: Exception) -> str:
    return (
        f"Your previous answer could not be accepted: {describe_error(error)}. "
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
