# Phase 5 – Step 5.1: pure display helpers — no streamlit import, so they are unit-testable
"""Turn workflow objects into rows and values for display.

Pages call these and only lay the results out; formatting logic stays testable without a browser.
Planted transaction-ID prefixes (STRUCT_, LARGE_, ...) are the answer key of the synthetic data and
are never part of any row shown to a reviewer.
"""

from collections.abc import Iterable
from typing import Any

from src.sar_workflow import CaseRecord, CaseStatus

# Phase 5 – Step 5.2: measured Stage 04 average per risk analysis (gpt-4o-mini, assumed prices);
# used until this session has its own measurements
DEFAULT_RISK_COST_USD = 0.0008
MAX_BATCH = 10  # Stage 05 decision 3


# Phase 5 – Step 5.2: one row per case for the queue table
def queue_rows(records: Iterable[CaseRecord]) -> list[dict[str, Any]]:
    rows = []
    for rec in records:
        case, risk = rec.case, rec.risk_output
        rows.append({
            "customer": case.customer.customer_id,
            "name": case.customer.name,
            "profile_risk": case.customer.risk_rating,
            "accounts": len(case.accounts),
            "transactions": len(case.transactions),
            "status": str(rec.status),
            "classification": risk.classification if risk else None,
            "risk_level": risk.risk_level if risk else None,
            "case_id": rec.case_id,
        })
    return rows


# Phase 5 – Step 5.2: label for a case in pickers
def case_label(rec: CaseRecord) -> str:
    c = rec.case
    return f"{c.customer.customer_id} – {c.customer.name} ({len(c.transactions)} transactions)"


# Phase 5 – Step 5.2: the first `n` cases still waiting for Stage 1, in queue order
def next_new_cases(records: Iterable[CaseRecord], n: int) -> list[CaseRecord]:
    return [r for r in records if r.status == CaseStatus.NEW][: max(n, 0)]


# Phase 5 – Step 5.2: estimated cost of analysing these cases; zero-activity cases are free
def estimate_risk_cost(records: Iterable[CaseRecord], avg_cost_usd: float | None) -> float:
    per_case = avg_cost_usd if avg_cost_usd is not None else DEFAULT_RISK_COST_USD
    return sum(per_case for r in records if r.case.transactions)


# Phase 5 – Step 5.2: number of cases per status, in workflow order (zeros included)
def status_counts(records: Iterable[CaseRecord]) -> dict[str, int]:
    counts = {str(s): 0 for s in CaseStatus}
    for rec in records:
        counts[str(rec.status)] += 1
    return counts
