# Phase 5 – Step 5.2: unit tests for app/ui_helpers.py — plain functions, no Streamlit needed
import pytest

from app.ui_helpers import (
    DEFAULT_RISK_COST_USD,
    account_rows,
    case_label,
    cited_refs,
    customer_profile,
    display_refs,
    estimate_risk_cost,
    masked,
    next_new_cases,
    queue_rows,
    reasoning_steps,
    status_counts,
    transaction_rows,
)
from src.foundation_sar import RiskAnalystOutput
from src.sar_workflow import CaseRecord, CaseStatus

RISK = RiskAnalystOutput(classification="Fraud", confidence_score=0.7, risk_level="Medium",
                         reasoning="Step 1 ... Step 5", key_indicators=["x"])


@pytest.fixture
def records(sample_case):
    empty = sample_case.model_copy(update={"transactions": []})
    analyzed = CaseRecord(case=sample_case, status=CaseStatus.RISK_REVIEW)
    analyzed.risk_output = RISK
    return [
        CaseRecord(case=sample_case),            # NEW, 1 transaction
        analyzed,                                # RISK_REVIEW
        CaseRecord(case=empty),                  # NEW, zero activity
        CaseRecord(case=sample_case),            # NEW
    ]


# Phase 5 – Step 5.2: rows carry what a reviewer needs, findings only once known
def test_queue_rows(records):
    rows = queue_rows(records)
    assert rows[0] == {
        "customer": "CUST_0002", "name": "Renee Blair", "profile_risk": "Low", "accounts": 1,
        "transactions": 1, "status": "NEW", "classification": None, "risk_level": None,
        "case_id": "CASE_CUST_0002_20261116120000",
    }
    assert (rows[1]["classification"], rows[1]["risk_level"]) == ("Fraud", "Medium")
    assert rows[2]["transactions"] == 0


def test_case_label(records):
    assert case_label(records[0]) == "CUST_0002 – Renee Blair (1 transactions)"


# Phase 5 – Step 5.2: only NEW cases, in order, capped at n
def test_next_new_cases(records):
    assert next_new_cases(records, 2) == [records[0], records[2]]
    assert next_new_cases(records, 10) == [records[0], records[2], records[3]]
    assert next_new_cases(records, 0) == []


# Phase 5 – Step 5.2: zero-activity cases are free; measured average wins over the default
def test_estimate_risk_cost(records):
    todo = next_new_cases(records, 10)  # 2 active + 1 zero-activity
    assert estimate_risk_cost(todo, None) == pytest.approx(2 * DEFAULT_RISK_COST_USD)
    assert estimate_risk_cost(todo, 0.001) == pytest.approx(0.002)
    assert estimate_risk_cost([], 0.001) == 0


def test_status_counts(records):
    counts = status_counts(records)
    assert counts["NEW"] == 3 and counts["RISK_REVIEW"] == 1
    assert list(counts) == [str(s) for s in CaseStatus]  # every status, workflow order
    assert sum(counts.values()) == 4


# ===========================================================================
# Phase 5 – Step 5.3: risk review helpers
# ===========================================================================
REAL_ID = "TXN_B24455F3"  # the conftest transaction → display ref T001


@pytest.fixture
def reviewed(sample_case):
    rec = CaseRecord(case=sample_case, status=CaseStatus.RISK_REVIEW)
    rec.risk_output = RISK.model_copy(update={
        "key_indicators": [f"{REAL_ID} is a large online transfer"],
        "reasoning": f"Step 1: profile. Step 2: {REAL_ID} stands out. Step 3) flags.",
    })
    return rec


# Phase 5 – Step 5.3: real IDs never reach the screen; the model's refs do
def test_masked_and_cited(reviewed):
    assert masked(reviewed.risk_output.key_indicators[0], reviewed.case) == (
        "T001 is a large online transfer"
    )
    assert display_refs(reviewed.case) == {REAL_ID: "T001"}
    assert cited_refs(reviewed) == {"T001"}
    assert cited_refs(CaseRecord(case=reviewed.case)) == set()  # not analysed yet


# Phase 5 – Step 5.3: profile shows age, never DOB / SSN / address / phone
def test_customer_profile(sample_case):
    profile = customer_profile(sample_case)
    assert profile["Age"] == 48  # born 1978-05-14, case created 2026-11-16
    assert profile["Annual income"] == "$72,005"
    shown = " ".join(map(str, profile.values()))
    assert "1978-05-14" not in shown and "7201" not in shown and "Janet Cape" not in shown


def test_account_and_transaction_rows(reviewed):
    [acc] = account_rows(reviewed.case)
    assert (acc["account"], acc["type"], acc["status"]) == ("ACC_1", "Money Market", "Active")
    [row] = transaction_rows(reviewed.case, {"T001"})
    assert row["cited"] is True and row["ref"] == "T001"
    assert REAL_ID not in str(row)
    assert transaction_rows(reviewed.case, set())[0]["cited"] is False


@pytest.mark.parametrize(
    ("text", "steps"),
    [
        ("Step 1: a. Step 2: b. Step 3) c.", ["Step 1: a.", "Step 2: b.", "Step 3) c."]),
        ("no numbered steps here", ["no numbered steps here"]),
    ],
)
def test_reasoning_steps(text, steps):
    assert reasoning_steps(text) == steps
