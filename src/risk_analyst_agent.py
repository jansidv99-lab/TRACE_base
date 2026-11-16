# Phase 2 – Step 2.4: Risk Analyst agent — Chain-of-Thought classification of one case
"""Risk Analyst agent.

Flow for one case:
    CaseData → format_case_for_llm (masked, PII-free) → Chat Completions (system + user, JSON mode)
             → parse + validate as RiskAnalystOutput → unmask T### refs → result

Cases without transactions skip the LLM entirely (Stage 02 decision 2).
Step 2.5 adds robust parsing and retries; Step 2.6 adds audit logging.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

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


# Phase 2 – Step 2.4: facts about the last analysis (feeds the audit log and cost metrics)
@dataclass
class AnalysisRun:
    case_id: str
    method: Literal["llm", "rule_based"]
    model: str | None = None  # model name returned by the API, e.g. gpt-4o-mini-2024-07-18
    token_usage: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    attempts: int = 0


# Phase 2 – Step 2.4: the agent; the LLM client is injected (real OpenAI client or a test fake)
class RiskAnalystAgent:
    # Phase 2 – Step 2.4: configure model, prompt version and temperature; load the prompt once
    def __init__(
        self,
        client: Any,
        logger: ExplainabilityLogger,
        model: str | None = None,
        prompt_version: str | None = None,
        temperature: float = 0.0,
    ) -> None:
        self.client = client
        self.logger = logger
        self.model = model or config.MODEL
        self.prompt_version = prompt_version or config.PROMPT_VERSION
        self.temperature = temperature
        self.system_prompt = load_prompt("risk_analyst", self.prompt_version)
        self.last_run: AnalysisRun | None = None
        if not self.model:
            raise ValueError("No model configured: set MODEL in .env or pass model=...")

    # Phase 2 – Step 2.4: analyse one case and return a validated, unmasked result
    def analyze_case(self, case: CaseData) -> RiskAnalystOutput:
        if not case.transactions:
            self.last_run = AnalysisRun(case_id=case.case_id, method="rule_based")
            return NO_ACTIVITY_RESULT

        formatted = format_case_for_llm(case)
        start = time.perf_counter()
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": formatted.text},
            ],
        )
        latency_ms = (time.perf_counter() - start) * 1000

        raw = response.choices[0].message.content or ""
        result = RiskAnalystOutput.model_validate(json.loads(raw))

        self.last_run = AnalysisRun(
            case_id=case.case_id,
            method="llm",
            model=getattr(response, "model", None),
            token_usage=_usage_dict(response),
            latency_ms=latency_ms,
            attempts=1,
        )
        return _unmask_output(result, formatted.id_map)


# Phase 2 – Step 2.4: put real transaction IDs back into the free-text fields
def _unmask_output(result: RiskAnalystOutput, id_map: dict[str, str]) -> RiskAnalystOutput:
    return result.model_copy(
        update={
            "reasoning": unmask(result.reasoning, id_map),
            "key_indicators": [unmask(k, id_map) for k in result.key_indicators],
        }
    )


# Phase 2 – Step 2.4: token counts from the API response (empty if the response has none)
def _usage_dict(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }
