# Phase 1 – Step 1.3: foundation module — validated data models for the SAR pipeline
"""Foundation layer of the SAR system.

Step 1.3 adds one Pydantic model per CSV row: CustomerData, AccountData, TransactionData.
Later steps in this file: CaseData (1.4), agent output schemas (1.5),
ExplainabilityLogger (1.6) and DataLoader (1.7).

Every rule below traces back to a finding in notebooks/01_data_exploration.ipynb.
"""

import json
import threading
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from src import config

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
# Phase 1 – Step 1.4: e.g. CASE_CUST_0001_20261116143005 (customer + UTC creation time)
CASE_ID_PATTERN = r"^CASE_CUST_\d{4}_\d{14}$"

# Phase 1 – Step 1.5: allowed values for the agents' outputs (project_goal.md, Phase 2)
SuspiciousActivityType = Literal["Structuring", "Sanctions", "Fraud", "Money_Laundering", "Other"]
RiskLevel = Literal["Low", "Medium", "High", "Critical"]
# Phase 1 – Step 1.5: a string that is not empty after trimming spaces
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# Phase 1 – Step 1.5: SAR narrative must be FEWER than this many words (master plan decision 2)
NARRATIVE_WORD_LIMIT = 120


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


# Phase 1 – Step 1.4: one unit of work for the agents — a customer and everything they hold
class CaseData(BaseModel):
    """One customer, all of their accounts, and all transactions on those accounts.

    Built by DataLoader (Step 1.7), which also generates `case_id` and `data_quality_flags`.
    Data-quality quirks (e.g. a transaction dated before its account opened) are *recorded*
    in `data_quality_flags`, never rejected — see Stage 01 review decision 2.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=CASE_ID_PATTERN)
    customer: CustomerData
    accounts: list[AccountData] = Field(min_length=1)
    # May be empty: 37 accounts in the dataset have no transactions
    transactions: list[TransactionData] = Field(default_factory=list)
    case_created_at: datetime
    data_quality_flags: list[str] = Field(default_factory=list)

    # Phase 1 – Step 1.4: timestamps must carry a timezone (audit trails use UTC)
    @field_validator("case_created_at")
    @classmethod
    def _timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("case_created_at must be timezone-aware (use UTC)")
        return value

    # Phase 1 – Step 1.4: case_id must name the case's own customer
    @model_validator(mode="after")
    def _case_id_matches_customer(self) -> "CaseData":
        if not self.case_id.startswith(f"CASE_{self.customer.customer_id}_"):
            raise ValueError(
                f"case_id {self.case_id} does not match customer {self.customer.customer_id}"
            )
        return self

    # Phase 1 – Step 1.4: every account belongs to this customer, with no duplicates
    @model_validator(mode="after")
    def _accounts_belong_to_customer(self) -> "CaseData":
        foreign = [
            a.account_id for a in self.accounts if a.customer_id != self.customer.customer_id
        ]
        if foreign:
            raise ValueError(
                f"accounts {foreign} do not belong to customer {self.customer.customer_id}"
            )
        ids = [a.account_id for a in self.accounts]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate account_id in case")
        return self

    # Phase 1 – Step 1.4: every transaction belongs to one of the case's accounts, no duplicates
    @model_validator(mode="after")
    def _transactions_belong_to_accounts(self) -> "CaseData":
        account_ids = {a.account_id for a in self.accounts}
        foreign = [t.transaction_id for t in self.transactions if t.account_id not in account_ids]
        if foreign:
            raise ValueError(f"transactions {foreign[:5]} are not on any account in this case")
        ids = [t.transaction_id for t in self.transactions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate transaction_id in case")
        return self


# Phase 1 – Step 1.5: count words the same way everywhere (schema, Stage 03 validator, evals)
def count_words(text: str) -> int:
    """Number of whitespace-separated words, e.g. "31 U.S.C. 5324" counts as 3."""
    return len(text.split())


# Phase 1 – Step 1.5: contract the Risk Analyst agent (Stage 02) must fill
class RiskAnalystOutput(BaseModel):
    """Structured result of the Chain-of-Thought risk analysis for one case.

    Unlike the CSV record models, unknown extra keys are *ignored* rather than rejected:
    LLMs sometimes add harmless fields, and that should not fail an otherwise valid answer.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    classification: SuspiciousActivityType
    confidence_score: float = Field(ge=0.0, le=1.0)
    risk_level: RiskLevel
    reasoning: NonEmptyStr
    key_indicators: list[NonEmptyStr] = Field(min_length=1)


# Phase 1 – Step 1.5: contract the Compliance Officer agent (Stage 03) must fill
class ComplianceOfficerOutput(BaseModel):
    """Structured result of the ReACT narrative generation for one case.

    The schema enforces the hard word limit; the behavioural checks (who/what/when/where/why,
    terminology, citation correctness) live in Stage 03 because they need case context.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    narrative: NonEmptyStr
    narrative_reasoning: NonEmptyStr
    regulatory_citations: list[NonEmptyStr] = Field(min_length=1)
    completeness_check: bool

    # Phase 1 – Step 1.5: hard limit — the narrative must be fewer than 120 words
    @field_validator("narrative")
    @classmethod
    def _under_word_limit(cls, value: str) -> str:
        words = count_words(value)
        if words >= NARRATIVE_WORD_LIMIT:
            raise ValueError(
                f"narrative has {words} words; it must be fewer than {NARRATIVE_WORD_LIMIT}"
            )
        return value


# ---------------------------------------------------------------------------
# Phase 1 – Step 1.6: audit logging
# ---------------------------------------------------------------------------
AgentType = Literal["DataLoader", "RiskAnalyst", "ComplianceOfficer", "Human"]

# Phase 1 – Step 1.6: personal data that must never reach the audit log (master plan rule 6)
PII_FIELDS = frozenset({"ssn_last_4", "date_of_birth", "address", "phone"})
REDACTED = "[REDACTED]"

# Phase 1 – Step 1.6: keys every audit record has; **extra may not overwrite them
_CORE_FIELDS = frozenset(
    {
        "timestamp", "event_id", "case_id", "agent_type", "action", "input_summary", "output",
        "reasoning", "execution_time_ms", "success", "error_message", "prompt_version",
    }
)


# Phase 1 – Step 1.6: turn models/dates into plain JSON data and mask PII keys at any depth
def redact_pii(value: Any) -> Any:
    """Return a JSON-friendly copy of `value` with PII_FIELDS replaced by "[REDACTED]"."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            k: (REDACTED if k in PII_FIELDS else redact_pii(v)) for k, v in value.items()
        }
    if isinstance(value, list | tuple | set):
        return [redact_pii(v) for v in value]
    if isinstance(value, date):  # also covers datetime (a subclass of date)
        return value.isoformat()
    if isinstance(value, Path):  # forward slashes on every OS, so logs look the same everywhere
        return value.as_posix()
    return value


# Phase 1 – Step 1.6: append-only JSONL audit trail for regulators
class ExplainabilityLogger:
    """Writes one JSON object per line for every agent action and human decision.

    Append-only by design: existing lines are never rewritten, which is what makes the file
    trustworthy as an audit trail. A lock keeps lines whole if several threads log at once
    (e.g. Streamlit reruns in Stage 05).
    """

    # Phase 1 – Step 1.6: choose the log file (default logs/sar_audit.jsonl) and create its folder
    def __init__(self, log_file: str | Path | None = None) -> None:
        self.log_file = Path(log_file) if log_file else config.LOG_DIR / "sar_audit.jsonl"
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # Phase 1 – Step 1.6: record one event; returns the record that was written
    def log_agent_action(
        self,
        agent_type: AgentType,
        action: str,
        case_id: str | None,
        input_data: Any,
        output_data: Any,
        reasoning: str,
        execution_time_ms: float,
        success: bool = True,
        error_message: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        if agent_type not in get_args(AgentType):
            raise ValueError(f"agent_type must be one of {get_args(AgentType)}, got {agent_type!r}")
        clashes = _CORE_FIELDS & extra.keys()
        if clashes:
            raise ValueError(f"extra fields may not overwrite core fields: {sorted(clashes)}")

        record: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event_id": str(uuid.uuid4()),
            "case_id": case_id,
            "agent_type": agent_type,
            "action": action,
            "input_summary": redact_pii(input_data),
            "output": redact_pii(output_data),
            "reasoning": reasoning,
            "execution_time_ms": round(float(execution_time_ms), 2),
            "success": success,
            "error_message": error_message,
            "prompt_version": config.PROMPT_VERSION,
            # Optional from Stage 02 on: model, token_usage, ...
            **redact_pii(extra),
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock, self.log_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return record
