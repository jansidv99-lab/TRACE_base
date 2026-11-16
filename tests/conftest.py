# Phase 1 – Step 1.8: shared pytest fixtures — valid sample records reused by every test module
"""Fixtures are pytest's way of sharing setup. A test asks for one by naming it as a parameter.

Each record fixture returns a *fresh* dict of raw CSV-style strings, so a test can change a field
(e.g. `{**customer_row, "risk_rating": "Hgih"}`) without affecting other tests.
"""

from datetime import UTC, datetime

import pytest

from src.foundation_sar import AccountData, CaseData, CustomerData, TransactionData


# Phase 1 – Step 1.8: one valid customers.csv row (real row CUST_0002, PII values shortened)
@pytest.fixture
def customer_row() -> dict[str, str]:
    return {
        "customer_id": "CUST_0002",
        "name": "Renee Blair",
        "date_of_birth": "1978-05-14",
        "ssn_last_4": "7201",
        "address": "959 Janet Cape Apt. 413, South Joshuastad, GA 49021",
        "phone": "(534)719-2832x764",
        "customer_since": "2023-03-03",
        "risk_rating": "Low",
        "occupation": "Banker",
        "annual_income": "72005",
    }


# Phase 1 – Step 1.8: one valid accounts.csv row belonging to customer_row
@pytest.fixture
def account_row() -> dict[str, str]:
    return {
        "account_id": "CUST_0002_ACC_1",
        "customer_id": "CUST_0002",
        "account_type": "Money_Market",
        "opening_date": "2023-12-03",
        "current_balance": "92121.66",
        "average_monthly_balance": "172242.96",
        "status": "Active",
    }


# Phase 1 – Step 1.8: one valid transactions.csv row on account_row (blank optional fields)
@pytest.fixture
def transaction_row() -> dict[str, str]:
    return {
        "transaction_id": "TXN_B24455F3",
        "account_id": "CUST_0002_ACC_1",
        "transaction_date": "2025-02-18",
        "transaction_type": "Online_Transfer",
        "amount": "1615.06",
        "description": "Credit card payment",
        "counterparty": "",
        "location": "Branch_Westside_Plaza",
        "method": "ATM",
    }


# Phase 1 – Step 1.8: a fixed, timezone-aware creation time so case_ids are predictable
@pytest.fixture
def created_at() -> datetime:
    return datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)


# Phase 1 – Step 1.8: a complete valid CaseData built from the rows above
@pytest.fixture
def sample_case(customer_row, account_row, transaction_row, created_at) -> CaseData:
    return CaseData(
        case_id="CASE_CUST_0002_20261116120000",
        customer=CustomerData(**customer_row),
        accounts=[AccountData(**account_row)],
        transactions=[TransactionData(**transaction_row)],
        case_created_at=created_at,
    )
