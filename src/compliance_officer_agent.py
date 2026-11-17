# Phase 3 – Step 3.6: Compliance Officer agent — ReACT loop that writes the SAR narrative
"""Compliance Officer agent.

Flow for one approved case:
    CaseData + approved RiskAnalystOutput
      → user message: masked case (Stage 02 formatter) + risk findings re-masked to T### refs
      → loop (≤ max_steps model turns):
            model: "Thought: ..." + exactly one tool call   (tool_choice="required")
            code:  ComplianceToolbox runs the tool → observation sent back as a `tool` message
      → stops when submit_narrative is accepted (all Step 3.3 checks pass)
      → ComplianceOfficerOutput (narrative_reasoning unmasked to real IDs)

A rejected submission comes back as an observation listing the errors, so the model revises:
that is the revise-on-failure loop. Step budget exhausted or a permanent API error →
ComplianceError (Stage 04 routes the case to manual review).
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAIError

from src import config
from src.case_formatter import FormattedCase, format_case_for_llm, remask, unmask
from src.compliance_tools import TOOL_SCHEMAS, ComplianceToolbox
from src.foundation_sar import (
    CaseData,
    ComplianceOfficerOutput,
    ExplainabilityLogger,
    RiskAnalystOutput,
)
from src.llm_utils import DEFAULT_SEED, call_with_retry
from src.prompts import load_prompt

# Phase 3 – Step 3.6: sent if the model answers in plain text instead of calling a tool
_NUDGE = (
    "You must act by calling one of the tools. When the narrative is ready, call "
    "submit_narrative; the task is only complete when a submission is accepted."
)


# Phase 3 – Step 3.6: final failure for one case — the workflow routes it to manual review
class ComplianceError(Exception):
    def __init__(self, case_id: str, reason: str, steps: int) -> None:
        super().__init__(
            f"Narrative generation failed for {case_id} after {steps} step(s): {reason}"
        )
        self.case_id = case_id
        self.reason = reason
        self.steps = steps


# Phase 3 – Step 3.6: facts about the last run — feeds the audit log (Step 3.7) and cost metrics
@dataclass
class NarrativeRun:
    case_id: str
    model: str | None = None
    system_fingerprint: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    attempts: int = 0  # API calls, including transient-error retries
    steps: int = 0  # model turns in the ReACT loop
    trace: list[dict[str, Any]] = field(default_factory=list)  # Thought → Action → Observation
    report: dict[str, Any] | None = None  # last narrative check report
    error: str | None = None


# Phase 3 – Step 3.6: the agent; the LLM client is injected (real OpenAI client or a test fake)
class ComplianceOfficerAgent:
    # Phase 3 – Step 3.6: model settings, loop budget and retry policy; prompt loaded once
    def __init__(
        self,
        client: Any,
        logger: ExplainabilityLogger,
        model: str | None = None,
        prompt_version: str | None = None,
        temperature: float = 0.0,
        seed: int | None = DEFAULT_SEED,
        max_steps: int = 8,
        max_api_attempts: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.logger = logger
        self.model = model or config.MODEL
        self.prompt_version = prompt_version or config.PROMPT_VERSION
        self.temperature = temperature
        self.seed = seed
        self.max_steps = max_steps
        self.max_api_attempts = max_api_attempts
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep
        self.system_prompt = load_prompt("compliance_officer", self.prompt_version)
        self.last_run: NarrativeRun | None = None
        if not self.model:
            raise ValueError("No model configured: set MODEL in .env or pass model=...")

    # Phase 3 – Step 3.6: public entry point
    # Phase 3 – Step 3.7: ALWAYS writes exactly one audit event (success or failure)
    def generate_compliance_narrative(
        self, case: CaseData, risk_output: RiskAnalystOutput
    ) -> ComplianceOfficerOutput:
        """Return a validated ComplianceOfficerOutput, or raise ComplianceError.

        Exactly one audit event is written per call, including the ReACT trace.
        """
        try:
            result = self._generate(case, risk_output)
        except ComplianceError as exc:
            self._log(case, risk_output, result=None, error=exc.reason)
            raise
        self._log(case, risk_output, result=result, error=None)
        return result

    # Phase 3 – Step 3.7: one audit record an examiner can use to replay how the narrative was built
    def _log(
        self,
        case: CaseData,
        risk_output: RiskAnalystOutput,
        result: ComplianceOfficerOutput | None,
        error: str | None,
    ) -> None:
        run = self.last_run or NarrativeRun(case_id=case.case_id)
        txns = case.transactions
        self.logger.log_agent_action(
            agent_type="ComplianceOfficer",
            action="generate_narrative",
            case_id=case.case_id,
            # A summary, not the full prompt: it is reproducible from case + risk output + version
            input_data={
                "customer_id": case.customer.customer_id,
                "accounts": len(case.accounts),
                "transactions": len(txns),
                "period": [str(txns[0].transaction_date), str(txns[-1].transaction_date)]
                if txns
                else None,
                "risk_classification": risk_output.classification,
                "risk_level": risk_output.risk_level,
                "risk_confidence": risk_output.confidence_score,
            },
            output_data=result,
            reasoning=result.narrative_reasoning
            if result
            else f"Narrative generation failed: {error}",
            execution_time_ms=run.latency_ms,
            success=error is None,
            error_message=error,
            prompt_version=self.prompt_version,
            requested_model=self.model,
            model=run.model,
            temperature=self.temperature,
            seed=self.seed,
            system_fingerprint=run.system_fingerprint,
            token_usage=run.token_usage,
            attempts=run.attempts,
            steps=run.steps,
            max_steps=self.max_steps,
            react_trace=[_audit_step(s) for s in run.trace],
            validation_report=run.report,
        )

    # Phase 3 – Step 3.6: the ReACT loop
    def _generate(self, case: CaseData, risk_output: RiskAnalystOutput) -> ComplianceOfficerOutput:
        formatted = format_case_for_llm(case)
        toolbox = ComplianceToolbox(case, formatted, risk_output.classification)
        run = NarrativeRun(case_id=case.case_id)
        self.last_run = run
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": _user_message(formatted, risk_output)},
        ]
        start = time.perf_counter()
        try:
            while run.steps < self.max_steps:
                run.steps += 1
                message = self._next_message(messages, run)
                tool_calls = list(getattr(message, "tool_calls", None) or [])
                messages.append(_assistant_entry(message, tool_calls))

                if not tool_calls:  # plain text despite tool_choice="required": nudge and retry
                    run.trace.append({"step": run.steps, "thought": message.content,
                                      "action": None, "observation": "no tool call"})
                    messages.append({"role": "user", "content": _NUDGE})
                    continue

                for call in tool_calls:
                    observation = toolbox.execute(call.function.name, call.function.arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(observation),
                    })
                    run.trace.append({
                        "step": run.steps,
                        "thought": message.content,
                        "action": call.function.name,
                        "arguments": _safe_json(call.function.arguments),
                        "observation": observation,
                    })
                run.report = toolbox.last_report.to_dict() if toolbox.last_report else None

                if toolbox.accepted is not None:
                    result = toolbox.accepted
                    return result.model_copy(update={
                        "narrative_reasoning": unmask(result.narrative_reasoning,
                                                      formatted.id_map),
                    })

            raise ComplianceError(
                case.case_id,
                f"step budget exhausted ({self.max_steps} steps) without an accepted narrative",
                run.steps,
            )
        except OpenAIError as exc:
            run.error = f"API error: {type(exc).__name__}: {exc}"
            raise ComplianceError(case.case_id, run.error, run.steps) from exc
        except ComplianceError as exc:
            run.error = exc.reason
            raise
        finally:
            run.latency_ms = (time.perf_counter() - start) * 1000

    # Phase 3 – Step 3.6: one model turn — must call exactly one tool
    def _next_message(self, messages: list[dict[str, Any]], run: NarrativeRun) -> Any:
        response = call_with_retry(
            lambda: self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                seed=self.seed,
                tools=TOOL_SCHEMAS,
                tool_choice="required",
                parallel_tool_calls=False,
                messages=messages,
            ),
            run,
            max_attempts=self.max_api_attempts,
            backoff_seconds=self.backoff_seconds,
            sleep=self.sleep,
        )
        return response.choices[0].message


# Phase 3 – Step 3.6: masked case + approved risk findings (real IDs re-masked to T### refs)
def _user_message(formatted: FormattedCase, risk: RiskAnalystOutput) -> str:
    indicators = "\n".join(f"- {remask(k, formatted.id_map)}" for k in risk.key_indicators)
    return (
        f"{formatted.text}\n\n"
        "## 6. Approved risk findings (approved by a human reviewer)\n"
        f"Typology: {risk.classification}\n"
        f"Risk level: {risk.risk_level}\n"
        f"Confidence: {risk.confidence_score}\n"
        f"Key indicators:\n{indicators}\n"
        f"Analyst reasoning: {remask(risk.reasoning, formatted.id_map)}"
    )


# Phase 3 – Step 3.6: the assistant turn as the API expects it back in the next request
def _assistant_entry(message: Any, tool_calls: list[Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {"role": "assistant", "content": message.content}
    if tool_calls:
        entry["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.function.name, "arguments": c.function.arguments},
            }
            for c in tool_calls
        ]
    return entry


# Phase 3 – Step 3.7: one trace step for the audit log — full action + arguments, short observation
def _audit_step(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "step": step.get("step"),
        "thought": step.get("thought"),
        "action": step.get("action"),
        "arguments": step.get("arguments"),
        "observation": _observation_summary(step.get("action"), step.get("observation")),
    }


# Phase 3 – Step 3.7: shorten a tool observation; tools are deterministic, so the full text
# can be rebuilt from the case data + the logged arguments
def _observation_summary(action: str | None, observation: Any) -> Any:
    if not isinstance(observation, dict):
        return observation  # e.g. "no tool call"
    if "error" in observation:
        return {"error": observation["error"]}
    if action == "get_transaction_details":
        summary: dict[str, Any] = {
            "found": [t.get("ref") for t in observation.get("transactions", [])]
        }
        if observation.get("unknown_refs"):
            summary["unknown_refs"] = observation["unknown_refs"]
        return summary
    if action == "lookup_regulations":
        summary = {
            "typology": observation.get("typology"),
            "citations": [c.get("citation") for c in observation.get("citations", [])],
        }
        if "note" in observation:
            summary["note"] = observation["note"]
        return summary
    if action in ("check_narrative", "submit_narrative"):
        keys = ("accepted", "passed", "word_count", "errors", "warnings")
        return {k: observation[k] for k in keys if k in observation}
    return observation


# Phase 3 – Step 3.6: tool arguments as data for the trace (raw text if not valid JSON)
def _safe_json(arguments: str | None) -> Any:
    try:
        return json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return arguments
