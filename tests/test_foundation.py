# Phase 1 – Step 1.8: foundation tests — schemas, CaseData, audit logger and DataLoader
"""Covers the "Foundation Tests" category in project_goal.md (Phase 6):
schema validation, data loading, audit logging and graceful error handling.

All tests run offline and write only into pytest's temporary `tmp_path` folders.
"""

import json
import shutil
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest
from pydantic import ValidationError

from src.config import DATA_DIR
from src.foundation_sar import (
    REDACTED,
    AccountData,
    CaseData,
    ComplianceOfficerOutput,
    CustomerData,
    DataLoader,
    ExplainabilityLogger,
    HumanDecision,
    RiskAnalystOutput,
    SARDocument,
    TransactionData,
    count_words,
)

TOMORROW = (date.today() + timedelta(days=1)).isoformat()


# Phase 1 – Step 1.8: read a real CSV exactly the way DataLoader does
def _csv_rows(name: str) -> list[dict[str, str]]:
    df = pd.read_csv(DATA_DIR / name, dtype=str, keep_default_na=False)
    return df.to_dict(orient="records")


# ===========================================================================
# Schema validation against the real CSV data
# ===========================================================================


# Phase 1 – Step 1.8: every row of every provided CSV must validate
@pytest.mark.parametrize(
    ("file_name", "model"),
    [
        ("customers.csv", CustomerData),
        ("accounts.csv", AccountData),
        ("transactions.csv", TransactionData),
    ],
)
def test_every_csv_row_validates(file_name, model):
    failures = []
    for line, row in enumerate(_csv_rows(file_name), start=2):
        try:
            model(**row)
        except ValidationError as exc:
            failures.append((line, str(exc)))
    assert failures == [], f"{len(failures)} invalid rows in {file_name}: {failures[:3]}"


# Phase 1 – Step 1.8: strings from the CSV are converted to proper Python types
def test_valid_rows_are_typed(customer_row, account_row, transaction_row):
    customer = CustomerData(**customer_row)
    account = AccountData(**account_row)
    txn = TransactionData(**transaction_row)
    assert customer.date_of_birth == date(1978, 5, 14)
    assert customer.annual_income == 72005.0
    assert customer.ssn_last_4 == "7201"  # stays text
    assert account.current_balance == pytest.approx(92121.66)
    assert txn.amount == pytest.approx(1615.06)
    assert txn.transaction_date == date(2025, 2, 18)


# Phase 1 – Step 1.8: ssn_last_4 keeps a leading zero
def test_ssn_leading_zero_preserved(customer_row):
    assert CustomerData(**{**customer_row, "ssn_last_4": "0123"}).ssn_last_4 == "0123"


# Phase 1 – Step 1.8: blank optional fields become None
@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_optional_fields_become_none(transaction_row, blank):
    txn = TransactionData(**{**transaction_row, "counterparty": blank, "location": blank})
    assert txn.counterparty is None
    assert txn.location is None


# ===========================================================================
# Rejection cases — bad data must fail loudly with a clear message
# ===========================================================================


# Phase 1 – Step 1.8: invalid customer values
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("risk_rating", "Hgih"),
        ("customer_id", "CUST_12"),
        ("ssn_last_4", "12a4"),
        ("ssn_last_4", "12345"),
        ("date_of_birth", TOMORROW),
        ("annual_income", "-1"),
        ("name", "   "),
    ],
)
def test_customer_rejects_bad_values(customer_row, field, value):
    with pytest.raises(ValidationError) as exc:
        CustomerData(**{**customer_row, field: value})
    assert field in str(exc.value)


# Phase 1 – Step 1.8: customer cannot join the bank before being born
def test_customer_since_must_follow_birth(customer_row):
    with pytest.raises(ValidationError, match="must be after date_of_birth"):
        CustomerData(**{**customer_row, "customer_since": "1970-01-01"})


# Phase 1 – Step 1.8: invalid account values
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("current_balance", "-0.01"),
        ("average_monthly_balance", "-5"),
        ("account_type", "Chequing"),
        ("status", "Frozen"),
        ("account_id", "ACC_1"),
        ("opening_date", TOMORROW),
    ],
)
def test_account_rejects_bad_values(account_row, field, value):
    with pytest.raises(ValidationError) as exc:
        AccountData(**{**account_row, field: value})
    assert field in str(exc.value)


# Phase 1 – Step 1.8: account_id must belong to its customer_id
def test_account_must_match_customer(account_row):
    with pytest.raises(ValidationError, match="does not belong to customer_id"):
        AccountData(**{**account_row, "customer_id": "CUST_0001"})


# Phase 1 – Step 1.8: invalid transaction values
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount", "0"),
        ("amount", "-100"),
        ("amount", "abc"),
        ("transaction_id", "txn_b24455f3"),
        ("transaction_type", "Crypto"),
        ("method", "Pigeon"),
        ("transaction_date", TOMORROW),
        ("transaction_date", "18/02/2025"),
    ],
)
def test_transaction_rejects_bad_values(transaction_row, field, value):
    with pytest.raises(ValidationError) as exc:
        TransactionData(**{**transaction_row, field: value})
    assert field in str(exc.value)


# Phase 1 – Step 1.8: an unexpected CSV column is schema drift and must fail
def test_unexpected_column_rejected(customer_row):
    with pytest.raises(ValidationError, match="extra"):
        CustomerData(**{**customer_row, "email": "x@example.com"})


# Phase 1 – Step 1.8: every problem is reported at once, not one at a time
def test_all_errors_reported_together(account_row):
    bad = {**account_row, "account_type": "Chequing", "current_balance": "-5", "status": "Frozen"}
    with pytest.raises(ValidationError) as exc:
        AccountData(**bad)
    assert exc.value.error_count() == 3


# ===========================================================================
# CaseData
# ===========================================================================


# Phase 1 – Step 1.8: the shared sample case is valid
def test_sample_case_is_valid(sample_case):
    assert sample_case.customer.customer_id == "CUST_0002"
    assert len(sample_case.accounts) == 1
    assert sample_case.data_quality_flags == []


# Phase 1 – Step 1.8: CaseData relationship rules
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"case_id": "CASE_CUST_0001_20261116120000"}, "does not match customer"),
        ({"case_id": "CASE-0002"}, "should match pattern"),
        ({"accounts": []}, "at least 1 item"),
        ({"case_created_at": datetime(2026, 11, 16, 12, 0)}, "timezone-aware"),
    ],
)
def test_case_rejects_inconsistent_data(sample_case, change, message):
    data = {**sample_case.model_dump(), **change}
    with pytest.raises(ValidationError, match=message):
        CaseData(**data)


# Phase 1 – Step 1.8: a transaction on an account outside the case is rejected
def test_case_rejects_foreign_transaction(sample_case, transaction_row):
    foreign = TransactionData(**{**transaction_row, "account_id": "CUST_0009_ACC_1"})
    data = {**sample_case.model_dump(), "transactions": [foreign]}
    with pytest.raises(ValidationError, match="not on any account in this case"):
        CaseData(**data)


# Phase 1 – Step 1.8: an account belonging to another customer is rejected
def test_case_rejects_foreign_account(sample_case, account_row):
    other = AccountData(
        **{**account_row, "account_id": "CUST_0009_ACC_1", "customer_id": "CUST_0009"}
    )
    data = {**sample_case.model_dump(), "accounts": [*sample_case.accounts, other]}
    with pytest.raises(ValidationError, match="do not belong to customer"):
        CaseData(**data)


# Phase 1 – Step 1.8: duplicates are rejected
def test_case_rejects_duplicate_transactions(sample_case):
    data = {**sample_case.model_dump(), "transactions": sample_case.transactions * 2}
    with pytest.raises(ValidationError, match="duplicate transaction_id"):
        CaseData(**data)


# Phase 1 – Step 1.8: a case with no transactions is allowed (37 such accounts exist)
def test_case_allows_no_transactions(sample_case):
    data = {**sample_case.model_dump(), "transactions": []}
    assert CaseData(**data).transactions == []


# ===========================================================================
# Agent output schemas
# ===========================================================================

VALID_RISK = {
    "classification": "Structuring",
    "confidence_score": 0.87,
    "risk_level": "High",
    "reasoning": "Step 1 ... Step 5",
    "key_indicators": ["7 cash deposits between $9,154 and $9,916"],
}
VALID_COMPLIANCE = {
    "narrative": "Customer made seven cash deposits just under $10,000.",
    "narrative_reasoning": "Answered who, what, when, where, why.",
    "regulatory_citations": ["31 CFR 1020.320", "31 U.S.C. 5324"],
    "completeness_check": True,
}


# Phase 1 – Step 1.8: valid outputs are accepted; unknown LLM keys are ignored
def test_agent_outputs_accept_valid_data():
    risk = RiskAnalystOutput(**VALID_RISK, extra_llm_key="ignored")
    assert risk.classification == "Structuring"
    assert not hasattr(risk, "extra_llm_key")
    assert ComplianceOfficerOutput(**VALID_COMPLIANCE).completeness_check is True


# Phase 1 – Step 1.8: invalid risk analyst outputs
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("classification", "Terrorism"),
        ("confidence_score", 1.5),
        ("confidence_score", -0.1),
        ("risk_level", "Severe"),
        ("reasoning", "   "),
        ("key_indicators", []),
        ("key_indicators", [""]),
    ],
)
def test_risk_output_rejects_bad_values(field, value):
    with pytest.raises(ValidationError):
        RiskAnalystOutput(**{**VALID_RISK, field: value})


# Phase 1 – Step 1.8: narrative word limit is a hard "fewer than 120"
@pytest.mark.parametrize(("words", "ok"), [(1, True), (119, True), (120, False), (200, False)])
def test_narrative_word_limit(words, ok):
    data = {**VALID_COMPLIANCE, "narrative": " ".join(["word"] * words)}
    if ok:
        assert count_words(ComplianceOfficerOutput(**data).narrative) == words
    else:
        with pytest.raises(ValidationError, match="fewer than 120"):
            ComplianceOfficerOutput(**data)


# Phase 1 – Step 1.8: compliance output needs citations and a real boolean
@pytest.mark.parametrize(
    ("field", "value"),
    [("regulatory_citations", []), ("completeness_check", "maybe"), ("narrative", "")],
)
def test_compliance_output_rejects_bad_values(field, value):
    with pytest.raises(ValidationError):
        ComplianceOfficerOutput(**{**VALID_COMPLIANCE, field: value})


# ===========================================================================
# Phase 4 – Step 4.1: human gate decisions and the SAR document
# ===========================================================================


# Phase 4 – Step 4.1: an approval needs no reason; blank reasons are stored as None
def test_human_decision_approve():
    d = HumanDecision(gate="risk", decision="approve", reviewer_id=" analyst_1 ", reason="  ")
    assert d.reviewer_id == "analyst_1"
    assert d.reason is None
    assert d.decided_at.tzinfo is not None


# Phase 4 – Step 4.1: a rejection must say why
@pytest.mark.parametrize("reason", [None, "", "   "])
def test_human_decision_reject_needs_reason(reason):
    with pytest.raises(ValidationError, match="must include a reason"):
        HumanDecision(gate="risk", decision="reject", reviewer_id="a1", reason=reason)


@pytest.mark.parametrize(
    ("field", "value"),
    [("gate", "final"), ("decision", "maybe"), ("reviewer_id", " "),
     ("decided_at", datetime(2026, 11, 17, 12, 0))],  # naive datetime
)
def test_human_decision_rejects_bad_values(field, value):
    data = {"gate": "risk", "decision": "approve", "reviewer_id": "a1", field: value}
    with pytest.raises(ValidationError):
        HumanDecision(**data)


# Phase 4 – Step 4.1: a complete, valid SAR as a dict, built from the conftest sample case
@pytest.fixture
def sar_data(sample_case) -> dict:
    return {
        "sar_id": "SAR_CUST_0002_20261117120000",
        "generated_at": datetime(2026, 11, 17, 12, 0, tzinfo=UTC),
        "case_id": sample_case.case_id,
        "subject": sample_case.customer,
        "accounts": sample_case.accounts,
        "activity": {
            "typology": "Structuring",
            "risk_level": "High",
            "confidence_score": 0.87,
            "start_date": "2025-02-18",
            "end_date": "2025-02-18",
            "total_amount": 1615.06,
            "transaction_ids": ["TXN_B24455F3"],
        },
        "narrative": VALID_COMPLIANCE,
        "risk_review": {"gate": "risk", "decision": "approve", "reviewer_id": "analyst_1"},
        "compliance_review": {"gate": "compliance", "decision": "approve",
                              "reviewer_id": "officer_1"},
        "provenance": {"risk_model": "gpt-4o-mini", "risk_prompt_version": "V1"},
    }


def test_sar_document_valid(sar_data):
    sar = SARDocument(**sar_data)
    dumped = json.loads(sar.model_dump_json())
    assert dumped["subject"]["name"] == "Renee Blair"     # full subject details in the filing
    assert dumped["activity"]["transaction_ids"] == ["TXN_B24455F3"]
    assert dumped["compliance_review"]["gate"] == "compliance"


# Phase 4 – Step 4.1: every rule that keeps an invalid SAR from being written
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"risk_review": {"gate": "risk", "decision": "reject", "reviewer_id": "a1",
                          "reason": "false positive"}}, "not an approval"),
        ({"compliance_review": {"gate": "risk", "decision": "approve", "reviewer_id": "o1"}},
         "must be a 'compliance' gate decision"),
        ({"activity_dates": ("2025-03-01", "2025-02-01")}, "before start_date"),
        ({"activity_ids": ["T001"]}, "transaction_ids"),
        ({"activity_ids": []}, "transaction_ids"),
        ({"sar_id": "SAR-0002"}, "sar_id"),
        ({"sar_id": "SAR_CUST_0999_20261117120000"}, "does not match subject"),
        ({"generated_at": datetime(2026, 11, 17, 12, 0)}, "timezone-aware"),
    ],
    ids=["rejected-review", "wrong-gate", "dates-reversed", "masked-id", "no-ids", "bad-sar-id",
         "other-customer", "naive-time"],
)
def test_sar_document_rejects_invalid(sar_data, change, message):
    data = {**sar_data, "activity": dict(sar_data["activity"])}
    if "activity_dates" in change:
        data["activity"]["start_date"], data["activity"]["end_date"] = change.pop("activity_dates")
    if "activity_ids" in change:
        data["activity"]["transaction_ids"] = change.pop("activity_ids")
    data.update(change)
    with pytest.raises(ValidationError, match=message):
        SARDocument(**data)


# Phase 4 – Step 4.1: the audit logger accepts the new "Workflow" actor
def test_logger_accepts_workflow_actor(tmp_path):
    rec = ExplainabilityLogger(tmp_path / "a.jsonl").log_agent_action(
        "Workflow", "sar_generated", None, None, None, "r", 0
    )
    assert rec["agent_type"] == "Workflow"


# ===========================================================================
# ExplainabilityLogger
# ===========================================================================


# Phase 1 – Step 1.8: helper that reads a JSONL file back into a list of dicts
def _read_jsonl(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# Phase 1 – Step 1.8: each event is one valid JSON line with all core fields
def test_logger_writes_one_json_object_per_line(tmp_path):
    log_file = tmp_path / "audit.jsonl"
    logger = ExplainabilityLogger(log_file)
    logger.log_agent_action("Human", "risk_gate", "CASE_CUST_0002_20261116120000",
                            None, {"decision": "approved"}, "looks right", 12.3456)
    records = _read_jsonl(log_file)
    assert len(records) == 1
    record = records[0]
    for key in ("timestamp", "event_id", "case_id", "agent_type", "action", "input_summary",
                "output", "reasoning", "execution_time_ms", "success", "error_message",
                "prompt_version"):
        assert key in record
    assert record["execution_time_ms"] == 12.35
    assert record["timestamp"].endswith("+00:00")  # UTC


# Phase 1 – Step 1.8: logging appends; earlier lines are never overwritten
def test_logger_appends(tmp_path):
    log_file = tmp_path / "audit.jsonl"
    ExplainabilityLogger(log_file).log_agent_action("Human", "a", None, None, None, "r", 0)
    ExplainabilityLogger(log_file).log_agent_action("Human", "b", None, None, None, "r", 0)
    assert [r["action"] for r in _read_jsonl(log_file)] == ["a", "b"]


# Phase 1 – Step 1.8: logger creates missing folders
def test_logger_creates_parent_folder(tmp_path):
    log_file = tmp_path / "nested" / "dir" / "audit.jsonl"
    ExplainabilityLogger(log_file).log_agent_action("Human", "a", None, None, None, "r", 0)
    assert log_file.exists()


# Phase 1 – Step 1.8: PII never reaches the log, even nested inside a whole case
def test_logger_redacts_pii(tmp_path, sample_case, customer_row):
    log_file = tmp_path / "audit.jsonl"
    ExplainabilityLogger(log_file).log_agent_action(
        "DataLoader", "build", sample_case.case_id, {"case": sample_case}, None, "r", 0
    )
    raw = log_file.read_text(encoding="utf-8")
    for pii_field in ("ssn_last_4", "date_of_birth", "address", "phone"):
        assert customer_row[pii_field] not in raw
    logged_customer = _read_jsonl(log_file)[0]["input_summary"]["case"]["customer"]
    assert logged_customer["ssn_last_4"] == REDACTED
    assert logged_customer["name"] == "Renee Blair"  # needed for the SAR narrative


# Phase 1 – Step 1.8: optional extras are stored, but cannot overwrite core fields
def test_logger_extras_and_guards(tmp_path):
    logger = ExplainabilityLogger(tmp_path / "audit.jsonl")
    record = logger.log_agent_action("RiskAnalyst", "analyze", None, None, None, "r", 0,
                                     model="m", token_usage={"total": 10})
    assert record["model"] == "m"
    assert record["token_usage"] == {"total": 10}
    with pytest.raises(ValueError, match="core fields"):
        logger.log_agent_action("Human", "x", None, None, None, "r", 0, timestamp="forged")
    with pytest.raises(ValueError, match="agent_type"):
        logger.log_agent_action("Robot", "x", None, None, None, "r", 0)


# ===========================================================================
# DataLoader
# ===========================================================================


# Phase 1 – Step 1.8: build all cases from the real data once and share across tests
@pytest.fixture(scope="module")
def real_load(tmp_path_factory):
    log_file = tmp_path_factory.mktemp("logs") / "audit.jsonl"
    loader = DataLoader(ExplainabilityLogger(log_file))
    return loader, loader.build_all_cases(), log_file


# Phase 1 – Step 1.8: one case per customer, nothing lost, no errors
def test_loader_builds_all_cases(real_load):
    loader, cases, _ = real_load
    assert loader.errors == []
    assert len(cases) == 150
    assert sum(len(c.accounts) for c in cases) == 178
    assert sum(len(c.transactions) for c in cases) == 4268
    assert len({c.case_id for c in cases}) == 150


# Phase 1 – Step 1.8: the 368 pre-opening transactions are flagged, not rejected
def test_loader_flags_pre_opening_transactions(real_load):
    _, cases, _ = real_load
    flags = [f for c in cases for f in c.data_quality_flags]
    assert len(flags) == 368
    assert all("before account" in f for f in flags)


# Phase 1 – Step 1.8: transactions inside each case are in date order
def test_loader_sorts_transactions(real_load):
    _, cases, _ = real_load
    for case in cases:
        dates = [t.transaction_date for t in case.transactions]
        assert dates == sorted(dates)


# Phase 1 – Step 1.8: the load writes a summary audit event
def test_loader_writes_summary_event(real_load):
    _, _, log_file = real_load
    event = _read_jsonl(log_file)[-1]
    assert event["agent_type"] == "DataLoader"
    assert event["action"] == "build_all_cases"
    assert event["success"] is True
    assert event["output"]["cases_built"] == 150


# Phase 1 – Step 1.8: helper — copy the real CSVs to a temp folder and edit one cell
def _broken_copy(tmp_path, file_name, row_index, column, value):
    data_dir = tmp_path / "data"
    shutil.copytree(DATA_DIR, data_dir)
    df = pd.read_csv(data_dir / file_name, dtype=str, keep_default_na=False)
    df.loc[row_index, column] = value
    df.to_csv(data_dir / file_name, index=False)
    return data_dir


# Phase 1 – Step 1.8: one bad transaction → exactly one error, no crash, other data kept
def test_loader_reports_bad_row_without_crashing(tmp_path):
    data_dir = _broken_copy(tmp_path, "transactions.csv", 0, "amount", "-5")
    loader = DataLoader(ExplainabilityLogger(tmp_path / "audit.jsonl"), data_dir)
    cases = loader.build_all_cases()
    assert len(cases) == 150
    assert len(loader.errors) == 1
    error = loader.errors[0]
    assert (error.file, error.row_number) == ("transactions.csv", 2)
    assert "amount" in error.message
    assert _read_jsonl(tmp_path / "audit.jsonl")[-1]["success"] is False


# Phase 1 – Step 1.8: a transaction pointing at a missing account is reported as an orphan
def test_loader_reports_orphan_transaction(tmp_path):
    data_dir = _broken_copy(tmp_path, "transactions.csv", 0, "account_id", "CUST_0999_ACC_1")
    loader = DataLoader(ExplainabilityLogger(tmp_path / "audit.jsonl"), data_dir)
    loader.build_all_cases()
    assert len(loader.errors) == 1
    assert "not found" in loader.errors[0].message


# Phase 1 – Step 1.8: a customer whose only account is invalid gets no case, with a reason
def test_loader_skips_customer_without_valid_accounts(tmp_path):
    data_dir = _broken_copy(tmp_path, "accounts.csv", 0, "account_type", "Chequing")
    loader = DataLoader(ExplainabilityLogger(tmp_path / "audit.jsonl"), data_dir)
    cases = loader.build_all_cases()
    assert len(cases) == 149
    messages = [e.message for e in loader.errors]
    assert any("account_type" in m for m in messages)
    assert any("no valid accounts" in m for m in messages)
