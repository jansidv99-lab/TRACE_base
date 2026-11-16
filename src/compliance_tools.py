# Phase 3 – Step 3.5: the Compliance Officer's tools — JSON schemas for the model + safe executor
"""Function-calling tools for the ReACT loop.

The model never runs code. It asks for a tool by name with JSON arguments; `ComplianceToolbox`
validates the arguments, runs the tool against the case, and returns a JSON-serialisable
*observation*. Bad requests (unknown tool, malformed JSON, wrong argument types) come back as
error observations the model can recover from — they never crash the loop.

All tools speak in masked references (T001, ...). No real transaction ID ever reaches the model.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from src.case_formatter import FormattedCase
from src.foundation_sar import (
    CaseData,
    ComplianceOfficerOutput,
    SuspiciousActivityType,
    TransactionData,
)
from src.llm_utils import describe_error
from src.narrative_checks import NarrativeReport, check_narrative
from src.regulations import TYPOLOGY_CITATIONS, citations_for

# Phase 3 – Step 3.5: cap on refs per lookup, so one call can't dump the whole case
MAX_REFS_PER_CALL = 30

# ---------------------------------------------------------------------------
# Phase 3 – Step 3.5: tool schemas sent to the API as `tools=TOOL_SCHEMAS`
# ---------------------------------------------------------------------------
_STRING_LIST = {"type": "array", "items": {"type": "string"}}
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_transaction_details",
            "description": (
                "Return the exact facts (date, account type, transaction type, amount, method, "
                "location, counterparty, description) for transaction references such as T044. "
                f"At most {MAX_REFS_PER_CALL} references per call."
            ),
            "parameters": {
                "type": "object",
                "properties": {"refs": {**_STRING_LIST, "description": 'e.g. ["T044", "T046"]'}},
                "required": ["refs"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_regulations",
            "description": "Return the approved regulatory citations for a typology.",
            "parameters": {
                "type": "object",
                "properties": {
                    "typology": {"type": "string", "enum": list(TYPOLOGY_CITATIONS)},
                },
                "required": ["typology"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_narrative",
            "description": (
                "Run all compliance checks on a draft narrative and its citations. Returns "
                "passed, word count, five-W coverage, errors and warnings."
            ),
            "parameters": {
                "type": "object",
                "properties": {"narrative": {"type": "string"}, "citations": _STRING_LIST},
                "required": ["narrative", "citations"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_narrative",
            "description": (
                "Submit the final SAR narrative. Accepted only if every check passes; otherwise "
                "the errors are returned so you can fix them and submit again."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "narrative": {"type": "string"},
                    "citations": _STRING_LIST,
                    "narrative_reasoning": {
                        "type": "string",
                        "description": "How the narrative answers who/what/when/where/why.",
                    },
                },
                "required": ["narrative", "citations", "narrative_reasoning"],
            },
        },
    },
]
TOOL_NAMES = tuple(t["function"]["name"] for t in TOOL_SCHEMAS)


# Phase 3 – Step 3.5: argument problems the model should be told about (not a crash)
class ToolArgumentError(ValueError):
    pass


# Phase 3 – Step 3.5: "July 24, 2025" — the date style the prompt asks for
def _date_text(d) -> str:
    return f"{d:%B} {d.day}, {d.year}"


# Phase 3 – Step 3.5: "Business_Checking" → "Business Checking" (narrative-ready wording)
def _readable(label: str) -> str:
    return label.replace("_", " ")


# Phase 3 – Step 3.5: "Branch_Southpark_Ave" → "Southpark Ave branch"; None → "unknown"
def _readable_location(location: str | None) -> str:
    if not location:
        return "unknown"
    if location.startswith("Branch_"):
        return f"{_readable(location.removeprefix('Branch_'))} branch"
    return _readable(location)


# Phase 3 – Step 3.5: runs tool calls for ONE case; remembers the accepted submission
@dataclass
class ComplianceToolbox:
    case: CaseData
    formatted: FormattedCase  # masked case text + id_map from the Stage 02 formatter
    typology: SuspiciousActivityType  # the human-approved classification
    accepted: ComplianceOfficerOutput | None = None
    last_report: NarrativeReport | None = None
    _txn_by_id: dict[str, TransactionData] = field(init=False, repr=False)
    _account_type: dict[str, str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._txn_by_id = {t.transaction_id: t for t in self.case.transactions}
        self._account_type = {a.account_id: a.account_type for a in self.case.accounts}

    # Phase 3 – Step 3.5: entry point — never raises for bad model input
    def execute(self, name: str, arguments: str | dict[str, Any] | None) -> dict[str, Any]:
        """Run tool `name` with `arguments` (JSON string or dict) and return an observation."""
        try:
            args = _parse_arguments(arguments)
            handler = {
                "get_transaction_details": self._get_transaction_details,
                "lookup_regulations": self._lookup_regulations,
                "check_narrative": self._check_narrative,
                "submit_narrative": self._submit_narrative,
            }.get(name)
            if handler is None:
                raise ToolArgumentError(f"unknown tool {name!r}; available: {list(TOOL_NAMES)}")
            return handler(args)
        except ToolArgumentError as exc:
            return {"error": str(exc)}

    # Phase 3 – Step 3.5: exact facts for masked refs
    def _get_transaction_details(self, args: dict[str, Any]) -> dict[str, Any]:
        refs = _string_list(args, "refs")
        if not refs:
            raise ToolArgumentError("'refs' must contain at least one reference such as 'T001'")
        if len(refs) > MAX_REFS_PER_CALL:
            raise ToolArgumentError(f"at most {MAX_REFS_PER_CALL} refs per call; got {len(refs)}")
        found, unknown = [], []
        for ref in refs:
            real_id = self.formatted.id_map.get(ref.strip().upper())
            if real_id is None:
                unknown.append(ref)
                continue
            t = self._txn_by_id[real_id]
            found.append({
                "ref": ref.strip().upper(),
                "date": t.transaction_date.isoformat(),
                "date_text": _date_text(t.transaction_date),
                "account_type": _readable(self._account_type[t.account_id]),
                "transaction_type": _readable(t.transaction_type),
                "amount": f"${t.amount:,.2f}",
                "method": t.method,
                "location": _readable_location(t.location),
                "counterparty": t.counterparty or "unknown",
                "description": t.description,
            })
        result: dict[str, Any] = {"transactions": found}
        if unknown:
            result["unknown_refs"] = unknown
        return result

    # Phase 3 – Step 3.5: curated citations (Step 3.2) for a typology
    def _lookup_regulations(self, args: dict[str, Any]) -> dict[str, Any]:
        typology = args.get("typology")
        if typology not in TYPOLOGY_CITATIONS:
            raise ToolArgumentError(
                f"'typology' must be one of {list(TYPOLOGY_CITATIONS)}; got {typology!r}"
            )
        result: dict[str, Any] = {
            "typology": typology,
            "citations": [
                {"citation": c.citation, "title": c.title, "description": c.description}
                for c in citations_for(typology)
            ],
        }
        if typology != self.typology:
            result["note"] = f"The approved typology for this case is {self.typology}."
        return result

    # Phase 3 – Step 3.5: run the Step 3.3 checks on a draft
    def _check_narrative(self, args: dict[str, Any]) -> dict[str, Any]:
        narrative = _string(args, "narrative")
        citations = _string_list(args, "citations")
        self.last_report = check_narrative(narrative, citations, self.case, self.typology)
        return self.last_report.to_dict()

    # Phase 3 – Step 3.5: final answer — accepted only if every check passes
    def _submit_narrative(self, args: dict[str, Any]) -> dict[str, Any]:
        narrative = _string(args, "narrative")
        citations = _string_list(args, "citations")
        reasoning = _string(args, "narrative_reasoning")
        report = check_narrative(narrative, citations, self.case, self.typology)
        self.last_report = report
        if not report.passed:
            return {"accepted": False, **report.to_dict()}
        try:
            self.accepted = ComplianceOfficerOutput(
                narrative=narrative,
                narrative_reasoning=reasoning,
                regulatory_citations=citations,
                completeness_check=True,  # set by our checks, never by the model
            )
        except ValidationError as exc:
            return {"accepted": False, "errors": [describe_error(exc)]}
        return {"accepted": True, "warnings": report.warnings}


# Phase 3 – Step 3.5: tool arguments arrive as a JSON string from the API
def _parse_arguments(arguments: str | dict[str, Any] | None) -> dict[str, Any]:
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise ToolArgumentError(f"arguments are not valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise ToolArgumentError("arguments must be a JSON object")
    return parsed


# Phase 3 – Step 3.5: required string argument
def _string(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str):
        raise ToolArgumentError(f"'{key}' is required and must be a string")
    return value


# Phase 3 – Step 3.5: required list-of-strings argument
def _string_list(args: dict[str, Any], key: str) -> list[str]:
    value = args.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ToolArgumentError(f"'{key}' is required and must be a list of strings")
    return value
