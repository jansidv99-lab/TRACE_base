# Phase 5 – Step 5.2: unit tests for app/ui_helpers.py — plain functions, no Streamlit needed
import pytest

from app.ui_helpers import (
    DEFAULT_RISK_COST_USD,
    case_label,
    estimate_risk_cost,
    next_new_cases,
    queue_rows,
    status_counts,
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
