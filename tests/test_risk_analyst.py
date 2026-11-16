# Phase 2 – Step 2.7: Risk Analyst tests — formatter, prompt, parsing, agent behaviour, audit
"""Covers the "Risk Analyst Tests" category in project_goal.md: prompt engineering, JSON parsing,
error recovery and edge cases. Everything runs offline with FakeLLMClient, except the tests
marked `llm`, which call the real model and only run with `pytest -m llm`.
"""

import json
import re

import pytest

from src import config
from src.case_formatter import format_case_for_llm, unmask
from src.foundation_sar import CaseData, DataLoader, ExplainabilityLogger, TransactionData
from src.prompts import load_prompt
from src.risk_analyst_agent import (
    DEFAULT_SEED,
    NO_ACTIVITY_RESULT,
    InvalidLLMOutputError,
    RiskAnalysisError,
    RiskAnalystAgent,
    extract_json,
    is_transient,
)
from tests.fakes import (
    FakeLLMClient,
    auth_error,
    bad_request_error,
    rate_limit_error,
    risk_json,
    server_error,
    timeout_error,
)

PLANTED_PREFIX = re.compile(r"\b(STRUCT|LARGE|RAPID|GEO|FREQ|TXN)_[0-9A-F]{8}\b")


# Phase 2 – Step 2.7: all 150 real cases, loaded once for this module
@pytest.fixture(scope="module")
def real_cases(tmp_path_factory) -> dict[str, CaseData]:
    logger = ExplainabilityLogger(tmp_path_factory.mktemp("logs") / "load.jsonl")
    return {c.customer.customer_id: c for c in DataLoader(logger).build_all_cases()}


# Phase 2 – Step 2.7: a fresh audit logger per test, writing into tmp_path
@pytest.fixture
def audit(tmp_path):
    path = tmp_path / "audit.jsonl"
    return ExplainabilityLogger(path), path


# Phase 2 – Step 2.7: read audit events back
def _events(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# Phase 2 – Step 2.7: an agent wired to a scripted fake; sleeps are recorded, not slept
def _agent(script, logger, **kwargs):
    client, sleeps = FakeLLMClient(script), []
    agent = RiskAnalystAgent(client, logger, model="gpt-4o-mini", sleep=sleeps.append, **kwargs)
    return agent, client, sleeps


# ===========================================================================
# Case formatter: masking, PII, no ready-made red flags
# ===========================================================================


# Phase 2 – Step 2.7: no real transaction ID or planted prefix reaches the model, in any case
def test_formatter_never_leaks_transaction_ids(real_cases):
    for case in real_cases.values():
        text = format_case_for_llm(case).text
        assert not PLANTED_PREFIX.search(text), case.case_id


# Phase 2 – Step 2.7: SSN, address, phone and DOB are never sent; name and age are
def test_formatter_excludes_pii(real_cases):
    for case in real_cases.values():
        c, text = case.customer, format_case_for_llm(case).text
        for secret in (c.ssn_last_4, c.address, c.phone, str(c.date_of_birth)):
            assert secret not in text, (case.case_id, secret)
        assert f"Name: {c.name}" in text
        assert re.search(r"^Age: \d+$", text, re.MULTILINE)


# Phase 2 – Step 2.7: refs are T001.. in the same (date) order as the case's transactions
def test_formatter_numbers_refs_in_date_order(real_cases):
    case = real_cases["CUST_0137"]
    formatted = format_case_for_llm(case)
    assert list(formatted.id_map.values()) == [t.transaction_id for t in case.transactions]
    assert list(formatted.id_map)[:3] == ["T001", "T002", "T003"]
    ref_dates = re.findall(r"^T\d{3} \| (\d{4}-\d{2}-\d{2})", formatted.text, re.MULTILINE)
    assert ref_dates == sorted(ref_dates)


# Phase 2 – Step 2.7: unmask restores real IDs and leaves unknown refs alone
def test_unmask_round_trip(real_cases):
    formatted = format_case_for_llm(real_cases["CUST_0137"])
    first_real = formatted.id_map["T001"]
    assert unmask("See T001 and T999.", formatted.id_map) == f"See {first_real} and T999."


# Phase 2 – Step 2.7: data-quality notes are shown, with masked IDs
def test_formatter_masks_data_quality_notes(real_cases):
    case = next(c for c in real_cases.values() if c.data_quality_flags)
    notes = format_case_for_llm(case).text.split("## 5. Data-quality notes")[1]
    assert "before account" in notes
    assert not PLANTED_PREFIX.search(notes)
    assert case.customer.customer_id not in notes


# Phase 2 – Step 2.7: the summary is neutral — counts and totals, no pre-computed red flags
def test_formatter_has_no_ready_made_red_flags(real_cases):
    text = format_case_for_llm(real_cases["CUST_0137"]).text
    summary = text.split("## 3. Activity summary")[1].split("## 4. Transactions")[0]
    for word in ("suspicious", "threshold", "structuring", "red flag", "10,000", "near"):
        assert word not in summary.lower()


# Phase 2 – Step 2.7: a case without transactions still formats cleanly
def test_formatter_handles_no_transactions(sample_case):
    empty = sample_case.model_copy(update={"transactions": []})
    formatted = format_case_for_llm(empty)
    assert "No transactions in the review period." in formatted.text
    assert formatted.id_map == {}


# ===========================================================================
# Prompt
# ===========================================================================


# Phase 2 – Step 2.7: the configured prompt version loads and contains the full method
def test_prompt_contains_method_typologies_and_levels():
    prompt = load_prompt("risk_analyst", config.PROMPT_VERSION)
    for i in range(1, 6):
        assert f"Step {i}" in prompt
    for typology in ("Structuring", "Sanctions", "Fraud", "Money_Laundering", "Other"):
        assert typology in prompt
    for level in ("Low", "Medium", "High", "Critical"):
        assert level in prompt
    assert "<!--" not in prompt  # developer comments are stripped


# Phase 2 – Step 2.7: an unknown version fails with a helpful message
def test_prompt_missing_version_lists_available():
    with pytest.raises(FileNotFoundError, match="Available"):
        load_prompt("risk_analyst", "V999")


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
# Agent behaviour
# ===========================================================================


# Phase 2 – Step 2.7: no transactions → rule-based result, the LLM is never called, audited
def test_zero_transactions_short_circuit(sample_case, audit):
    logger, path = audit
    empty = sample_case.model_copy(update={"transactions": []})
    agent, client, _ = _agent([], logger)
    assert agent.analyze_case(empty) is NO_ACTIVITY_RESULT
    assert client.calls == []
    event = _events(path)[0]
    assert (event["method"], event["success"], event["attempts"]) == ("rule_based", True, 0)


# Phase 2 – Step 2.7: happy path — correct request, validated result, real IDs restored
def test_happy_path(sample_case, audit):
    logger, _ = audit
    agent, client, _ = _agent([risk_json()], logger)
    result = agent.analyze_case(sample_case)

    real_id = sample_case.transactions[0].transaction_id
    assert result.classification == "Other"
    assert result.key_indicators == [f"{real_id} is consistent with the profile"]
    assert real_id in result.reasoning

    request = client.calls[0]
    assert request["model"] == "gpt-4o-mini"
    assert request["temperature"] == 0.0
    assert request["seed"] == DEFAULT_SEED
    assert request["response_format"] == {"type": "json_object"}
    system, user = request["messages"]
    assert system["role"] == "system" and "Step 5" in system["content"]
    assert user["role"] == "user" and "T001" in user["content"]
    assert real_id not in user["content"]


# Phase 2 – Step 2.7: invalid JSON → one repair round (with the error) → success
def test_repair_after_invalid_json(sample_case, audit):
    logger, _ = audit
    agent, client, _ = _agent(["this is not json", risk_json()], logger)
    agent.analyze_case(sample_case)
    assert len(client.calls) == 2
    repair_messages = client.calls[1]["messages"]
    assert repair_messages[-2] == {"role": "assistant", "content": "this is not json"}
    assert "could not be accepted" in repair_messages[-1]["content"]
    assert (agent.last_run.attempts, agent.last_run.repairs) == (2, 1)


# Phase 2 – Step 2.7: an out-of-contract value is repaired the same way
def test_repair_after_validation_error(sample_case, audit):
    logger, _ = audit
    agent, client, _ = _agent([risk_json(risk_level="Severe"), risk_json()], logger)
    assert agent.analyze_case(sample_case).risk_level == "Low"
    assert "risk_level" in client.calls[1]["messages"][-1]["content"]


# Phase 2 – Step 2.7: still invalid after the repair → RiskAnalysisError, audited as failure
def test_invalid_twice_raises(sample_case, audit):
    logger, path = audit
    agent, _, _ = _agent([risk_json(confidence_score=7), "{}"], logger)
    with pytest.raises(RiskAnalysisError, match="invalid output") as exc:
        agent.analyze_case(sample_case)
    assert exc.value.case_id == sample_case.case_id
    event = _events(path)[0]
    assert event["success"] is False
    assert event["output"] is None
    assert "invalid output" in event["error_message"]


# Phase 2 – Step 2.7: transient errors are retried with exponential backoff
def test_transient_errors_retried_with_backoff(sample_case, audit):
    logger, _ = audit
    agent, client, sleeps = _agent([rate_limit_error(), server_error(), risk_json()], logger)
    agent.analyze_case(sample_case)
    assert len(client.calls) == 3
    assert sleeps == [1.0, 2.0]


# Phase 2 – Step 2.7: transient errors that never clear → RiskAnalysisError after max attempts
def test_transient_errors_give_up(sample_case, audit):
    logger, path = audit
    agent, client, sleeps = _agent([timeout_error()] * 3, logger)
    with pytest.raises(RiskAnalysisError, match="APITimeoutError") as exc:
        agent.analyze_case(sample_case)
    assert exc.value.attempts == 3
    assert sleeps == [1.0, 2.0]
    assert _events(path)[0]["attempts"] == 3


# Phase 2 – Step 2.7: permanent errors are never retried
@pytest.mark.parametrize("error_factory", [auth_error, bad_request_error])
def test_permanent_errors_not_retried(sample_case, audit, error_factory):
    logger, _ = audit
    agent, client, sleeps = _agent([error_factory(), risk_json()], logger)
    with pytest.raises(RiskAnalysisError):
        agent.analyze_case(sample_case)
    assert len(client.calls) == 1
    assert sleeps == []


# Phase 2 – Step 2.7: the audit event carries model, tokens, attempts and prompt version
def test_audit_event_contents(sample_case, audit):
    logger, path = audit
    agent, _, _ = _agent(["oops", risk_json()], logger)
    agent.analyze_case(sample_case)
    event = _events(path)[0]
    assert event["agent_type"] == "RiskAnalyst"
    assert event["action"] == "analyze_case"
    assert event["case_id"] == sample_case.case_id
    assert event["success"] is True
    assert event["method"] == "llm"
    assert event["requested_model"] == "gpt-4o-mini"
    assert event["model"] == "fake-model-2024-01-01"
    assert event["token_usage"] == {"prompt_tokens": 200, "completion_tokens": 40,
                                    "total_tokens": 240}
    assert (event["attempts"], event["repairs"]) == (2, 1)
    assert event["prompt_version"] == agent.prompt_version
    assert (event["seed"], event["system_fingerprint"]) == (DEFAULT_SEED, "fp_fake123")
    assert event["input_summary"]["transactions"] == len(sample_case.transactions)
    assert event["output"]["classification"] == "Other"


# Phase 2 – Step 2.7: exactly one audit event per analyze_case call
def test_one_event_per_call(sample_case, audit):
    logger, path = audit
    agent, _, _ = _agent([risk_json(), risk_json()], logger)
    agent.analyze_case(sample_case)
    agent.analyze_case(sample_case)
    assert len(_events(path)) == 2


# Phase 2 – Step 2.7: a transaction ID in a case is never passed through to the request
def test_planted_prefix_hidden_from_model(sample_case, transaction_row, audit):
    logger, _ = audit
    planted = TransactionData(**{**transaction_row, "transaction_id": "STRUCT_7D3B8E94"})
    case = sample_case.model_copy(update={"transactions": [planted]})
    agent, client, _ = _agent([risk_json()], logger)
    result = agent.analyze_case(case)
    assert "STRUCT_" not in json.dumps(client.calls[0]["messages"])
    assert "STRUCT_7D3B8E94" in result.reasoning  # restored for the human reviewer


# ===========================================================================
# Real model (opt-in): pytest -m llm
# ===========================================================================

needs_api = pytest.mark.skipif(not config.OPENAI_API_KEY, reason="OPENAI_API_KEY not set")


# Phase 2 – Step 2.7: one real agent shared by the llm tests
@pytest.fixture(scope="module")
def live_agent(tmp_path_factory):
    from src.llm_client import get_llm_client

    logger = ExplainabilityLogger(tmp_path_factory.mktemp("live") / "audit.jsonl")
    return RiskAnalystAgent(get_llm_client(), logger)


# Phase 2 – Step 2.7: planted structuring (STRUCT_ deposits just under $10k) is recognised
@pytest.mark.llm
@needs_api
def test_live_structuring_detected(live_agent, real_cases):
    result = live_agent.analyze_case(real_cases["CUST_0137"])
    assert result.classification == "Structuring"
    assert result.risk_level != "Low"


# Phase 2 – Step 2.7: a customer with no planted pattern is not escalated
@pytest.mark.llm
@needs_api
def test_live_clean_customer_not_escalated(live_agent, real_cases):
    result = live_agent.analyze_case(real_cases["CUST_0002"])
    assert result.risk_level in ("Low", "Medium")


# Phase 2 – Step 2.7: large cash activity (LARGE_ $15.9k–$48.5k) is at least noticed
@pytest.mark.llm
@needs_api
def test_live_large_cash_noticed(live_agent, real_cases):
    case_id = next(
        cid for cid, c in real_cases.items()
        if any(t.transaction_id.startswith("LARGE_") for t in c.transactions)
    )
    result = live_agent.analyze_case(real_cases[case_id])
    assert result.risk_level != "Low"
    assert all(f"Step {i}" in result.reasoning for i in range(1, 6))
