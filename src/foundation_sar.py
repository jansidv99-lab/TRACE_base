# Phase 1 – Step 1.3: foundation module — validated data models for the SAR pipeline
"""Foundation layer of the SAR system.

Step 1.3 adds one Pydantic model per CSV row: CustomerData, AccountData, TransactionData.
Later steps in this file: CaseData (1.4), agent output schemas (1.5),
ExplainabilityLogger (1.6) and DataLoader (1.7).

Every rule below traces back to a finding in notebooks/01_data_exploration.ipynb.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Phase 1 – Step 1.3: allowed values (Literal types) observed in the data
# ---------------------------------------------------------------------------
RiskRating = Literal["Low", "Medium", "High"]
AccountType = Literal["Checking", "Savings", "Money_Market", "Business_Checking"]
AccountStatus = Literal["Active", "Closed"]
TransactionType = Literal[
    "ACH_Credit",
    "ACH_Debit",
    "ATM_Withdrawal",
    "Cash_Deposit",
    "Cash_Withdrawal",
    "Check_Deposit",
    "Debit_Purchase",
    "Direct_Deposit",
    "Online_Transfer",
    "Wire_Transfer",
    "Wire_Transfer_Credit",
    "Wire_Transfer_Debit",
]
TransactionMethod = Literal["ATM", "Branch", "Cash", "Electronic", "Mobile", "Online", "Wire"]

# Phase 1 – Step 1.3: ID formats, e.g. CUST_0001, CUST_0001_ACC_1, STRUCT_7D3B8E94
CUSTOMER_ID_PATTERN = r"^CUST_\d{4}$"
ACCOUNT_ID_PATTERN = r"^CUST_\d{4}_ACC_\d+$"
TRANSACTION_ID_PATTERN = r"^[A-Z]+_[0-9A-F]{8}$"


# Phase 1 – Step 1.3: shared validation rule — dates in the data can never be in the future
def _not_in_future(value: date, field_name: str) -> date:
    if value > date.today():
        raise ValueError(f"{field_name} {value} is in the future")
    return value


# Phase 1 – Step 1.3: shared settings for every record model
class _RecordModel(BaseModel):
    # str_strip_whitespace: " Low " → "Low"
    # extra="forbid": an unexpected CSV column is an error, so schema drift is caught early
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


# Phase 1 – Step 1.3: one row of customers.csv
class CustomerData(_RecordModel):
    customer_id: str = Field(pattern=CUSTOMER_ID_PATTERN)
    name: str = Field(min_length=1)
    date_of_birth: date
    # Text, not int: "0123" must keep its leading zero
    ssn_last_4: str = Field(pattern=r"^\d{4}$")
    address: str = Field(min_length=1)
    # Kept as free text: the data uses ~6 different phone formats
    phone: str = Field(min_length=1)
    customer_since: date
    risk_rating: RiskRating
    occupation: str = Field(min_length=1)
    annual_income: float = Field(ge=0)

    # Phase 1 – Step 1.3: birth date and relationship start cannot be in the future
    @field_validator("date_of_birth", "customer_since")
    @classmethod
    def _dates_not_in_future(cls, value: date, info) -> date:
        return _not_in_future(value, info.field_name)

    # Phase 1 – Step 1.3: cross-field rule — a customer cannot join before being born
    @model_validator(mode="after")
    def _customer_since_after_birth(self) -> "CustomerData":
        if self.customer_since <= self.date_of_birth:
            raise ValueError(
                f"customer_since {self.customer_since} must be after "
                f"date_of_birth {self.date_of_birth}"
            )
        return self


# Phase 1 – Step 1.3: one row of accounts.csv
class AccountData(_RecordModel):
    account_id: str = Field(pattern=ACCOUNT_ID_PATTERN)
    customer_id: str = Field(pattern=CUSTOMER_ID_PATTERN)
    account_type: AccountType
    opening_date: date
    # "Balance validation" from project_goal.md: no negative balances in this dataset
    current_balance: float = Field(ge=0)
    # 0.0 is allowed: it marks the 37 accounts with no transactions
    average_monthly_balance: float = Field(ge=0)
    status: AccountStatus

    # Phase 1 – Step 1.3: account cannot be opened in the future
    @field_validator("opening_date")
    @classmethod
    def _opening_not_in_future(cls, value: date) -> date:
        return _not_in_future(value, "opening_date")

    # Phase 1 – Step 1.3: cross-field rule — CUST_0001_ACC_1 must belong to CUST_0001
    @model_validator(mode="after")
    def _account_matches_customer(self) -> "AccountData":
        if not self.account_id.startswith(f"{self.customer_id}_"):
            raise ValueError(
                f"account_id {self.account_id} does not belong to customer_id {self.customer_id}"
            )
        return self


# Phase 1 – Step 1.3: one row of transactions.csv
class TransactionData(_RecordModel):
    # Kept exactly as-is (incl. revealing prefixes); masked later in Stage 02 prompts
    transaction_id: str = Field(pattern=TRANSACTION_ID_PATTERN)
    account_id: str = Field(pattern=ACCOUNT_ID_PATTERN)
    transaction_date: date
    transaction_type: TransactionType
    amount: float = Field(gt=0)
    description: str = Field(min_length=1)
    counterparty: str | None = None
    location: str | None = None
    method: TransactionMethod

    # Phase 1 – Step 1.3: CSV has no null — an empty string means "unknown", so store None
    @field_validator("counterparty", "location", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # Phase 1 – Step 1.3: a transaction cannot be dated in the future
    @field_validator("transaction_date")
    @classmethod
    def _date_not_in_future(cls, value: date) -> date:
        return _not_in_future(value, "transaction_date")
