# Phase 3 – Step 3.8: Compliance Officer tests — citations, checks, tools, ReACT loop, audit
"""Covers the "Compliance Officer Tests" category in project_goal.md: ReACT framework, narrative
quality, citation accuracy and integration with Risk Analyst outputs.

Everything runs offline: FakeLLMClient plays the model one turn at a time, while the REAL
ComplianceToolbox and narrative checks run on our side. Tests marked `llm` call the real model and
only run with `pytest -m llm`.
"""

import json
import re
from datetime import UTC, datetime

import pytest

from src import config
from src.case_formatter import format_case_for_llm
from src.compliance_officer_agent import ComplianceError, ComplianceOfficerAgent
from src.compliance_tools import TOOL_NAMES, ComplianceToolbox
from src.foundation_sar import (
    AccountData,
    CaseData,
    CustomerData,
    DataLoader,
    ExplainabilityLogger,
    RiskAnalystOutput,
    TransactionData,
    count_words,
)
from src.narrative_checks import check_narrative
from src.regulations import (
    GENERAL_SAR_CITATIONS,
    TYPOLOGY_CITATIONS,
    citations_for,
    is_allowed_citation,
)
from tests.fakes import (
    FakeLLMClient,
    auth_error,
    make_response,
    server_error,
    tool_call_response,
)

STRUCT_IDS = ("STRUCT_7D3B8E94", "STRUCT_1A2B3C4D", "STRUCT_5E6F7A8B")
GOOD_CITATIONS = ["31 U.S.C. 5318(g)", "31 U.S.C. 5324"]
GOOD_NARRATIVE = (
    "Renee Blair, a banker, made three cash deposits into a Money Market account at the "
    "Westside Plaza branch on March 3, 2025, March 5, 2025 and March 7, 2025, totalling "
    "$9,800.00, $9,500.00 and $9,900.00. Each deposit fell just below the $10,000 reporting "
    "threshold, a pattern consistent with structuring to evade Currency Transaction Report "
    "requirements. The activity is inconsistent with the customer's profile."
)


# ---------------------------------------------------------------------------
# Phase 3 – Step 3.8: fixtures
# ---------------------------------------------------------------------------

# Phase 3 – Step 3.8: three cash deposits just under $10k (built from the conftest rows)
@pytest.fixture
def structuring_case(customer_row, account_row, transaction_row) -> CaseData:
    deposits = [("2025-03-03", "9800.00"), ("2025-03-05", "9500.00"), ("2025-03-07", "9900.00")]
    transactions = [
        TransactionData(**{
            **transaction_row,
            "transaction_id": tid,
            "transaction_date": day,
            "transaction_type": "Cash_Deposit",
            "amount": amount,
            "description": "Cash deposit",
            "method": "Cash",
        })
        for tid, (day, amount) in zip(STRUCT_IDS, deposits, strict=True)
    ]
    return CaseData(
        case_id="CASE_CUST_0002_20261116120000",
        customer=CustomerData(**customer_row),
        accounts=[AccountData(**account_row)],
        transactions=transactions,
        case_created_at=datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC),
    )


# Phase 3 – Step 3.8: approved risk findings that quote REAL IDs (the agent must re-mask them)
@pytest.fixture
def approved_risk() -> RiskAnalystOutput:
    return RiskAnalystOutput(
        classification="Structuring",
        confidence_score=0.9,
        risk_level="High",
        reasoning=f"Deposits {', '.join(STRUCT_IDS)} each fall just under $10,000.",
        key_indicators=[f"{STRUCT_IDS[0]} is $200 below the CTR threshold"],
    )


# Phase 3 – Step 3.8: a fresh audit logger per test
@pytest.fixture
def audit(tmp_path):
    path = tmp_path / "audit.jsonl"
    return ExplainabilityLogger(path), path


# Phase 3 – Step 3.8: toolbox for the structuring case
@pytest.fixture
def toolbox(structuring_case) -> ComplianceToolbox:
    return ComplianceToolbox(structuring_case, format_case_for_llm(structuring_case), "Structuring")


def _agent(script, logger, **kwargs) -> tuple[ComplianceOfficerAgent, FakeLLMClient]:
    client = FakeLLMClient(script)
    agent = ComplianceOfficerAgent(
        client, logger, model="fake-model", sleep=lambda _s: None, **kwargs
    )
    return agent, client


def _submit(narrative: str = GOOD_NARRATIVE, citations=None, call_id: str = "call_s"):
    return tool_call_response(
        "submit_narrative",
        {
            "narrative": narrative,
            "citations": GOOD_CITATIONS if citations is None else citations,
            "narrative_reasoning": "Used T001-T003 amounts and dates; cited the structuring law.",
        },
        call_id=call_id,
    )


def _padded(words: int) -> str:
    """GOOD_NARRATIVE padded with neutral filler to exactly `words` words."""
    text = GOOD_NARRATIVE + " Review noted." + " noted" * (words - count_words(GOOD_NARRATIVE) - 2)
    assert count_words(text) == words
    return text


def _records(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------------------
# Phase 3 – Step 3.8: citation table (Step 3.2)
# ---------------------------------------------------------------------------

# Phase 3 – Step 3.8: every typology gets the general citations, all of them allowed
@pytest.mark.parametrize("typology", list(TYPOLOGY_CITATIONS))
def test_every_typology_maps_to_allowed_citations(typology):
    cites = citations_for(typology)
    assert cites[: len(GENERAL_SAR_CITATIONS)] == list(GENERAL_SAR_CITATIONS)
    assert all(is_allowed_citation(c.citation) for c in cites)
    if typology != "Other":
        assert len(cites) > len(GENERAL_SAR_CITATIONS)


# Phase 3 – Step 3.8: matching ignores formatting but not substance
@pytest.mark.parametrize("text", ["31 USC 5324", "31 u.s.c. § 5324", "31  U. S. C. 5324"])
def test_citation_matching_ignores_formatting(text):
    assert is_allowed_citation(text)


def test_unknown_citation_rejected_and_bad_typology_raises():
    assert not is_allowed_citation("31 U.S.C. 9999")
    with pytest.raises(ValueError):
        citations_for("Tax_Evasion")


# ---------------------------------------------------------------------------
# Phase 3 – Step 3.8: narrative checks (Step 3.3)
# ---------------------------------------------------------------------------

# Phase 3 – Step 3.8: the reference narrative passes every check with no warnings
def test_good_narrative_passes(structuring_case):
    report = check_narrative(GOOD_NARRATIVE, GOOD_CITATIONS, structuring_case, "Structuring")
    assert report.passed, report.errors
    assert report.warnings == []
    assert all(report.five_ws.values())


# Phase 3 – Step 3.8: word limit boundary — 119 passes, 120 fails
def test_word_limit_boundary(structuring_case):
    ok = check_narrative(_padded(119), GOOD_CITATIONS, structuring_case, "Structuring")
    too_long = check_narrative(_padded(120), GOOD_CITATIONS, structuring_case, "Structuring")
    assert ok.passed, ok.errors
    assert any("120 words" in e for e in too_long.errors)


# Phase 3 – Step 3.8: each crafted defect is caught with a targeted error message
@pytest.mark.parametrize(
    ("narrative", "expected"),
    [
        (GOOD_NARRATIVE.replace("Renee Blair, a banker,", "The customer"), "missing 'who'"),
        (
            GOOD_NARRATIVE.replace(
                "on March 3, 2025, March 5, 2025 and March 7, 2025, ", ""
            ),
            "missing 'when'",
        ),
        (GOOD_NARRATIVE.replace("consistent with", "obviously"), "speculative or accusatory"),
        (GOOD_NARRATIVE.replace("three cash", "T001 cash"), "internal identifiers"),
        (GOOD_NARRATIVE.replace("March 5, 2025", "March 6, 2025"), "does not match any"),
        (GOOD_NARRATIVE.replace("March 5, 2025", "February 30, 2025"), "invalid date"),
    ],
    ids=["no-name", "no-date", "banned-term", "internal-id", "made-up-date", "invalid-date"],
)
def test_defects_are_errors(structuring_case, narrative, expected):
    report = check_narrative(narrative, GOOD_CITATIONS, structuring_case, "Structuring")
    assert not report.passed
    assert any(expected in e for e in report.errors), report.errors


# Phase 3 – Step 3.8: citation rules — allowlist, general SAR basis, typology-specific
@pytest.mark.parametrize(
    ("citations", "expected"),
    [
        ([], "at least one regulatory citation"),
        ([*GOOD_CITATIONS, "31 U.S.C. 9999"], "not in the approved table"),
        (["31 U.S.C. 5324"], "general SAR citation"),
        (["31 U.S.C. 5318(g)"], "Structuring-specific"),
    ],
    ids=["none", "unknown", "no-general", "no-specific"],
)
def test_citation_errors(structuring_case, citations, expected):
    report = check_narrative(GOOD_NARRATIVE, citations, structuring_case, "Structuring")
    assert any(expected in e for e in report.errors), report.errors


# Phase 3 – Step 3.8: an unverified amount is only a warning (Stage 03 decision 3)
def test_unverified_amount_is_warning(structuring_case):
    text = GOOD_NARRATIVE.replace("totalling", "together $29,200.00, namely")
    report = check_narrative(text, GOOD_CITATIONS, structuring_case, "Structuring")
    assert report.passed, report.errors
    assert any("$29,200.00" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# Phase 3 – Step 3.8: tools (Step 3.5)
# ---------------------------------------------------------------------------

# Phase 3 – Step 3.8: exact, narrative-ready facts for masked refs; never the real ID
def test_get_transaction_details_returns_masked_facts(toolbox):
    obs = toolbox.execute("get_transaction_details", {"refs": ["t001", "T003"]})
    first = obs["transactions"][0]
    assert first["ref"] == "T001"
    assert first["amount"] == "$9,800.00"
    assert first["date_text"] == "March 3, 2025"
    assert first["location"] == "Westside Plaza branch"
    assert "STRUCT_" not in json.dumps(obs)


@pytest.mark.parametrize(
    ("name", "args", "expected"),
    [
        ("get_transaction_details", {"refs": []}, "error"),
        ("get_transaction_details", "{not json", "error"),
        ("lookup_regulations", {"typology": "Tax_Evasion"}, "error"),
        ("delete_case", {}, "error"),
    ],
    ids=["empty-refs", "bad-json", "bad-typology", "unknown-tool"],
)
def test_bad_tool_input_gives_error_observation(toolbox, name, args, expected):
    obs = toolbox.execute(name, args)  # must not raise
    assert expected in obs


def test_unknown_refs_are_reported(toolbox):
    obs = toolbox.execute("get_transaction_details", {"refs": ["T001", "T999"]})
    assert [t["ref"] for t in obs["transactions"]] == ["T001"]
    assert obs["unknown_refs"] == ["T999"]


# Phase 3 – Step 3.8: lookup_regulations returns exactly the curated table
def test_lookup_regulations_matches_table(toolbox):
    obs = toolbox.execute("lookup_regulations", {"typology": "Structuring"})
    assert [c["citation"] for c in obs["citations"]] == [
        c.citation for c in citations_for("Structuring")
    ]
    other = toolbox.execute("lookup_regulations", {"typology": "Fraud"})
    assert "Structuring" in other["note"]


# Phase 3 – Step 3.8: only a passing submission is accepted
def test_submit_rejects_then_accepts(toolbox):
    rejected = toolbox.execute(
        "submit_narrative",
        {"narrative": "Too short.", "citations": [], "narrative_reasoning": "x"},
    )
    assert rejected["accepted"] is False and toolbox.accepted is None
    accepted = toolbox.execute(
        "submit_narrative",
        {"narrative": GOOD_NARRATIVE, "citations": GOOD_CITATIONS, "narrative_reasoning": "x"},
    )
    assert accepted["accepted"] is True
    assert toolbox.accepted.completeness_check is True
    assert set(TOOL_NAMES) == {
        "get_transaction_details", "lookup_regulations", "check_narrative", "submit_narrative"
    }


# ---------------------------------------------------------------------------
# Phase 3 – Step 3.8: ReACT loop (Step 3.6)
# ---------------------------------------------------------------------------

# Phase 3 – Step 3.8: details → regulations → submit, accepted on the first try
def test_happy_path(structuring_case, approved_risk, audit):
    logger, _ = audit
    agent, client = _agent(
        [
            tool_call_response("get_transaction_details", {"refs": ["T001", "T002", "T003"]}),
            tool_call_response("lookup_regulations", {"typology": "Structuring"}, "call_2"),
            _submit(),
        ],
        logger,
    )
    result = agent.generate_compliance_narrative(structuring_case, approved_risk)

    assert result.narrative == GOOD_NARRATIVE
    assert result.completeness_check is True
    assert agent.last_run.steps == 3
    # narrative_reasoning is unmasked back to real IDs for humans
    assert STRUCT_IDS[0] in result.narrative_reasoning
    # every request forces exactly one tool call, deterministically
    call = client.calls[0]
    assert call["tool_choice"] == "required"
    assert call["parallel_tool_calls"] is False
    assert call["temperature"] == 0.0 and call["seed"] == 42
    # observations are fed back as `tool` messages tied to the call id
    tool_msg = client.calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_1"


# Phase 3 – Step 3.8: the model never sees a real transaction ID (risk findings re-masked)
def test_no_real_ids_sent_to_model(structuring_case, approved_risk, audit):
    agent, client = _agent([_submit()], audit[0])
    agent.generate_compliance_narrative(structuring_case, approved_risk)
    sent = json.dumps(client.calls[0]["messages"])
    assert not re.search(r"STRUCT_[0-9A-F]{8}", sent)
    assert "T001" in sent


# Phase 3 – Step 3.8: a rejected submission is revised and then accepted
def test_failed_submit_then_revision(structuring_case, approved_risk, audit):
    agent, client = _agent(
        [_submit(narrative="Renee Blair deposited cash.", call_id="c1"), _submit(call_id="c2")],
        audit[0],
    )
    result = agent.generate_compliance_narrative(structuring_case, approved_risk)
    assert result.narrative == GOOD_NARRATIVE
    first_obs = json.loads(client.calls[1]["messages"][-1]["content"])
    assert first_obs["accepted"] is False and first_obs["errors"]


# Phase 3 – Step 3.8: plain text instead of a tool call → nudge, then continue
def test_plain_text_turn_is_nudged(structuring_case, approved_risk, audit):
    agent, client = _agent([make_response("I think it is structuring."), _submit()], audit[0])
    agent.generate_compliance_narrative(structuring_case, approved_risk)
    nudge = client.calls[1]["messages"][-1]
    assert nudge["role"] == "user" and "submit_narrative" in nudge["content"]
    assert agent.last_run.trace[0]["observation"] == "no tool call"


# Phase 3 – Step 3.8: step budget exhausted → ComplianceError
def test_step_budget_exhausted(structuring_case, approved_risk, audit):
    script = [
        tool_call_response("lookup_regulations", {"typology": "Structuring"}, f"c{i}")
        for i in range(3)
    ]
    agent, _ = _agent(script, audit[0], max_steps=3)
    with pytest.raises(ComplianceError, match="step budget exhausted") as exc_info:
        agent.generate_compliance_narrative(structuring_case, approved_risk)
    assert exc_info.value.steps == 3


# Phase 3 – Step 3.8: transient API error is retried; permanent one becomes ComplianceError
def test_transient_error_retried(structuring_case, approved_risk, audit):
    agent, _ = _agent([server_error(), _submit()], audit[0])
    agent.generate_compliance_narrative(structuring_case, approved_risk)
    assert agent.last_run.attempts == 2


def test_permanent_error_raises(structuring_case, approved_risk, audit):
    agent, client = _agent([auth_error()], audit[0])
    with pytest.raises(ComplianceError, match="API error"):
        agent.generate_compliance_narrative(structuring_case, approved_risk)
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Phase 3 – Step 3.8: audit integration (Step 3.7)
# ---------------------------------------------------------------------------

# Phase 3 – Step 3.8: one success event with trace, tokens and validation report
def test_audit_event_on_success(structuring_case, approved_risk, audit):
    logger, path = audit
    agent, _ = _agent(
        [
            tool_call_response("get_transaction_details", {"refs": ["T001", "T999"]}),
            _submit(),
        ],
        logger,
    )
    agent.generate_compliance_narrative(structuring_case, approved_risk)

    [rec] = _records(path)
    assert rec["agent_type"] == "ComplianceOfficer"
    assert rec["action"] == "generate_narrative"
    assert rec["success"] is True
    assert rec["steps"] == 2
    assert rec["token_usage"]["total_tokens"] == 240
    assert rec["prompt_version"] == agent.prompt_version
    assert rec["input_summary"]["risk_classification"] == "Structuring"
    assert rec["validation_report"]["passed"] is True
    details, submit = rec["react_trace"]
    # observations are summarised: refs only, not the full transaction lines
    assert details["observation"] == {"found": ["T001"], "unknown_refs": ["T999"]}
    assert submit["observation"]["accepted"] is True
    assert submit["arguments"]["narrative"] == GOOD_NARRATIVE


# Phase 3 – Step 3.8: one failure event, then the error is re-raised
def test_audit_event_on_failure(structuring_case, approved_risk, audit):
    logger, path = audit
    agent, _ = _agent([_submit(narrative="Too short.")], logger, max_steps=1)
    with pytest.raises(ComplianceError):
        agent.generate_compliance_narrative(structuring_case, approved_risk)

    [rec] = _records(path)
    assert rec["success"] is False
    assert "step budget exhausted" in rec["error_message"]
    assert rec["reasoning"].startswith("Narrative generation failed")
    assert rec["validation_report"]["passed"] is False
    assert rec["react_trace"][0]["observation"]["accepted"] is False


# ---------------------------------------------------------------------------
# Phase 3 – Step 3.8: real model (opt-in, about a cent)
# ---------------------------------------------------------------------------
needs_api = pytest.mark.skipif(not config.OPENAI_API_KEY, reason="OPENAI_API_KEY not set")


# Phase 3 – Step 3.8: all 150 real cases, loaded once for the live tests
@pytest.fixture(scope="module")
def real_cases(tmp_path_factory) -> dict[str, CaseData]:
    logger = ExplainabilityLogger(tmp_path_factory.mktemp("logs") / "load.jsonl")
    return {c.customer.customer_id: c for c in DataLoader(logger).build_all_cases()}


# Phase 3 – Step 3.8: one real agent shared by the live tests
@pytest.fixture(scope="module")
def live_agent(tmp_path_factory):
    from src.llm_client import get_llm_client

    logger = ExplainabilityLogger(tmp_path_factory.mktemp("live") / "audit.jsonl")
    return ComplianceOfficerAgent(get_llm_client(), logger)


def _live_risk(classification: str, indicator: str) -> RiskAnalystOutput:
    return RiskAnalystOutput(
        classification=classification, confidence_score=0.85, risk_level="High",
        reasoning=indicator, key_indicators=[indicator],
    )


# Phase 3 – Step 3.8: planted structuring customer → accepted narrative citing 31 U.S.C. 5324
@pytest.mark.llm
@needs_api
def test_live_structuring_narrative(live_agent, real_cases):
    case = real_cases["CUST_0137"]
    result = live_agent.generate_compliance_narrative(
        case, _live_risk("Structuring", "Repeated cash deposits just under $10,000")
    )
    report = check_narrative(result.narrative, result.regulatory_citations, case, "Structuring")
    assert report.passed, report.errors
    assert count_words(result.narrative) < 120
    assert any("5324" in c for c in result.regulatory_citations)


# Phase 3 – Step 3.8: large cash customer → accepted Money_Laundering narrative
@pytest.mark.llm
@needs_api
def test_live_large_cash_narrative(live_agent, real_cases):
    case = next(
        c for c in real_cases.values()
        if any(t.transaction_id.startswith("LARGE_") for t in c.transactions)
    )
    result = live_agent.generate_compliance_narrative(
        case, _live_risk("Money_Laundering", "Large cash transactions inconsistent with profile")
    )
    report = check_narrative(
        result.narrative, result.regulatory_citations, case, "Money_Laundering"
    )
    assert report.passed, report.errors
    assert count_words(result.narrative) < 120
