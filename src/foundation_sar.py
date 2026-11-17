# Phase 1 – Step 1.3: foundation module — validated data models for the SAR pipeline
"""Foundation layer of the SAR system.

Step 1.3 adds one Pydantic model per CSV row: CustomerData, AccountData, TransactionData.
Later steps in this file: CaseData (1.4), agent output schemas (1.5),
ExplainabilityLogger (1.6) and DataLoader (1.7).

Every rule below traces back to a finding in notebooks/01_data_exploration.ipynb.
"""

import json
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, get_args

import pandas as pd
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
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
# Phase 4 – Step 4.1: human gate decisions and the final SAR document
# ---------------------------------------------------------------------------
GateName = Literal["risk", "compliance"]
GateDecision = Literal["approve", "reject"]
# Phase 4 – Step 4.1: e.g. SAR_CUST_0137_20261117120000 (customer + UTC generation time)
SAR_ID_PATTERN = r"^SAR_CUST_\d{4}_\d{14}$"


# Phase 4 – Step 4.1: shared rule — audit and filing timestamps must carry a timezone (UTC)
def _require_timezone(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware (use UTC)")
    return value


# Phase 4 – Step 4.1: one reviewer's decision at one gate (approvals AND rejections are audited)
class HumanDecision(BaseModel):
    """What a human reviewer decided at the risk gate or the compliance gate.

    A gate is data, not a prompt: the workflow receives this object from a notebook, the Streamlit
    UI or a test, so the same logic works everywhere.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    gate: GateName
    decision: GateDecision
    reviewer_id: NonEmptyStr
    reason: str | None = None
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Phase 4 – Step 4.1: timestamps in the audit trail are always timezone-aware
    @field_validator("decided_at")
    @classmethod
    def _timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone(value, "decided_at")

    # Phase 4 – Step 4.1: a rejection must say why (examiners will ask); blank reasons become None
    @model_validator(mode="after")
    def _reject_needs_reason(self) -> "HumanDecision":
        if self.reason is not None and not self.reason:
            self.reason = None
        if self.decision == "reject" and self.reason is None:
            raise ValueError("a rejection must include a reason")
        return self


# Phase 4 – Step 4.1: the suspicious-activity section of the SAR
class SuspiciousActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    typology: SuspiciousActivityType
    risk_level: RiskLevel
    confidence_score: float = Field(ge=0.0, le=1.0)
    start_date: date
    end_date: date
    total_amount: float = Field(ge=0)
    # Real IDs only: a masked ref such as T001 must never reach a filing
    transaction_ids: list[Annotated[str, Field(pattern=TRANSACTION_ID_PATTERN)]] = Field(
        min_length=1
    )

    # Phase 4 – Step 4.1: the activity period cannot end before it starts
    @model_validator(mode="after")
    def _dates_in_order(self) -> "SuspiciousActivity":
        if self.end_date < self.start_date:
            raise ValueError(f"end_date {self.end_date} is before start_date {self.start_date}")
        return self


# Phase 4 – Step 4.1: which models and prompt versions produced the SAR (reproducibility)
class SARProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_model: str | None = None
    risk_prompt_version: str | None = None
    compliance_model: str | None = None
    compliance_prompt_version: str | None = None


# Phase 4 – Step 4.1: the regulatory deliverable — written as JSON to outputs/sars/
class SARDocument(BaseModel):
    """A complete Suspicious Activity Report for one case, approved at both human gates.

    The subject carries full customer details (name, DOB, address, phone, ssn_last_4) because a SAR
    filing needs them (Stage 04 decision 2). The file lives under git-ignored `outputs/`; the audit
    log stays redacted. Audit events link to this SAR through `case_id`.
    """

    model_config = ConfigDict(extra="forbid")

    sar_id: str = Field(pattern=SAR_ID_PATTERN)
    generated_at: datetime
    case_id: str = Field(pattern=CASE_ID_PATTERN)
    subject: CustomerData
    accounts: list[AccountData] = Field(min_length=1)
    activity: SuspiciousActivity
    narrative: ComplianceOfficerOutput
    risk_review: HumanDecision
    compliance_review: HumanDecision
    provenance: SARProvenance = Field(default_factory=SARProvenance)

    # Phase 4 – Step 4.1: filing time must be timezone-aware
    @field_validator("generated_at")
    @classmethod
    def _timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone(value, "generated_at")

    # Phase 4 – Step 4.1: case, SAR and subject must all name the same customer
    @model_validator(mode="after")
    def _ids_consistent(self) -> "SARDocument":
        customer_id = self.subject.customer_id
        if not self.case_id.startswith(f"CASE_{customer_id}_"):
            raise ValueError(f"case_id {self.case_id} does not match subject {customer_id}")
        if not self.sar_id.startswith(f"SAR_{customer_id}_"):
            raise ValueError(f"sar_id {self.sar_id} does not match subject {customer_id}")
        return self

    # Phase 4 – Step 4.1: a SAR exists only if BOTH gates approved, each on its own gate
    @model_validator(mode="after")
    def _both_gates_approved(self) -> "SARDocument":
        for field_name, gate in (("risk_review", "risk"), ("compliance_review", "compliance")):
            review: HumanDecision = getattr(self, field_name)
            if review.gate != gate:
                raise ValueError(
                    f"{field_name} must be a '{gate}' gate decision, got '{review.gate}'"
                )
            if review.decision != "approve":
                raise ValueError(
                    f"{field_name} is not an approval; a SAR needs both gates approved"
                )
        return self


# ---------------------------------------------------------------------------
# Phase 1 – Step 1.6: audit logging
# ---------------------------------------------------------------------------
# Phase 4 – Step 4.1: "Workflow" added for events that are neither an agent nor a human
# (e.g. sar_generated, routed_to_manual_review)
AgentType = Literal["DataLoader", "RiskAnalyst", "ComplianceOfficer", "Human", "Workflow"]

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
        # Phase 2 – Step 2.6: the prompt version actually used (agents may override the default)
        prompt_version: str | None = None,
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
            "prompt_version": prompt_version or config.PROMPT_VERSION,
            # Optional from Stage 02 on: model, token_usage, ...
            **redact_pii(extra),
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock, self.log_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return record


# Phase 5 – Step 5.5: read the audit trail back (UI audit view, later Stage 08 run log)
def read_audit_events(path: str | Path, case_id: str | None = None) -> list[dict[str, Any]]:
    """Return the audit events in file order, optionally only those for `case_id`.

    A missing file means "no events yet". Lines that are not valid JSON objects (e.g. a line cut off
    by a crash mid-write) are skipped rather than failing the whole read.
    """
    path = Path(path)
    if not path.exists():
        return []
    events = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and (case_id is None or event.get("case_id") == case_id):
                events.append(event)
    return events


# ---------------------------------------------------------------------------
# Phase 1 – Step 1.7: loading CSVs into validated cases
# ---------------------------------------------------------------------------


# Phase 1 – Step 1.7: one problem found while loading, precise enough to fix the source data
@dataclass(frozen=True)
class LoadError:
    file: str  # e.g. "accounts.csv"
    row_number: int | None  # line number in the CSV (header = line 1); None for grouping errors
    record_id: str | None  # e.g. the account_id of the bad row
    message: str


# Phase 1 – Step 1.7: CSV → validated records → one CaseData per customer
class DataLoader:
    """Reads customers/accounts/transactions CSVs and builds validated CaseData objects.

    Bad rows never crash the load: they are collected in `self.errors` as LoadError entries
    and summarised in the audit log. Data-quality quirks become `data_quality_flags`.
    """

    # Phase 1 – Step 1.7: where the CSVs live and where audit events go
    def __init__(self, logger: ExplainabilityLogger, data_dir: str | Path | None = None) -> None:
        self.logger = logger
        self.data_dir = Path(data_dir) if data_dir else config.DATA_DIR
        self.errors: list[LoadError] = []

    # Phase 1 – Step 1.7: read a CSV as plain strings so Pydantic does all the typing
    def _read_csv(self, file_name: str) -> list[dict[str, str]]:
        df = pd.read_csv(self.data_dir / file_name, dtype=str, keep_default_na=False)
        return df.to_dict(orient="records")

    # Phase 1 – Step 1.7: validate every row; keep good ones, record bad ones with line numbers
    def _load(self, file_name: str, model: type[BaseModel], id_field: str) -> tuple[list, list]:
        valid, errors = [], []
        for index, row in enumerate(self._read_csv(file_name)):
            try:
                valid.append(model(**row))
            except ValidationError as exc:
                problems = "; ".join(
                    f"{'.'.join(str(p) for p in e['loc']) or 'row'}: {e['msg']}"
                    for e in exc.errors()
                )
                errors.append(LoadError(file_name, index + 2, row.get(id_field) or None, problems))
        self.errors.extend(errors)
        return valid, errors

    # Phase 1 – Step 1.7: customers.csv → CustomerData
    def load_customers(self) -> tuple[list[CustomerData], list[LoadError]]:
        return self._load("customers.csv", CustomerData, "customer_id")

    # Phase 1 – Step 1.7: accounts.csv → AccountData
    def load_accounts(self) -> tuple[list[AccountData], list[LoadError]]:
        return self._load("accounts.csv", AccountData, "account_id")

    # Phase 1 – Step 1.7: transactions.csv → TransactionData
    def load_transactions(self) -> tuple[list[TransactionData], list[LoadError]]:
        return self._load("transactions.csv", TransactionData, "transaction_id")

    # Phase 1 – Step 1.7: quirks we record but do not reject (Stage 01 review decision 2)
    @staticmethod
    def find_data_quality_flags(
        accounts: list[AccountData], transactions: list[TransactionData]
    ) -> list[str]:
        """Flags transactions dated before their account opened.

        transaction_type/method disagreements are NOT flagged: in this dataset the method is
        spread almost uniformly across types (synthetic noise), so the flag would fire on
        thousands of rows and drown out the useful ones.
        """
        opened = {a.account_id: a.opening_date for a in accounts}
        return [
            f"{t.transaction_id} dated {t.transaction_date} before account {t.account_id} "
            f"opened {opened[t.account_id]}"
            for t in transactions
            if t.account_id in opened and t.transaction_date < opened[t.account_id]
        ]

    # Phase 1 – Step 1.7: assemble one validated case (case_id = customer + UTC time)
    def create_case_from_data(
        self,
        customer: CustomerData,
        accounts: list[AccountData],
        transactions: list[TransactionData],
        created_at: datetime | None = None,
    ) -> CaseData:
        created_at = created_at or datetime.now(UTC)
        return CaseData(
            case_id=f"CASE_{customer.customer_id}_{created_at:%Y%m%d%H%M%S}",
            customer=customer,
            accounts=accounts,
            transactions=sorted(transactions, key=lambda t: (t.transaction_date, t.transaction_id)),
            case_created_at=created_at,
            data_quality_flags=self.find_data_quality_flags(accounts, transactions),
        )

    # Phase 1 – Step 1.7: load all three files and build one case per customer
    def build_all_cases(self) -> list[CaseData]:
        start = time.perf_counter()
        self.errors = []
        customers, _ = self.load_customers()
        accounts, _ = self.load_accounts()
        transactions, _ = self.load_transactions()

        # Group accounts under valid customers; anything left over is an orphan
        customer_ids = {c.customer_id for c in customers}
        accounts_by_customer: dict[str, list[AccountData]] = defaultdict(list)
        for a in accounts:
            if a.customer_id in customer_ids:
                accounts_by_customer[a.customer_id].append(a)
            else:
                self.errors.append(
                    LoadError("accounts.csv", None, a.account_id,
                              f"customer {a.customer_id} not found or invalid")
                )

        # Group transactions under the customer who owns the account
        owner_of = {a.account_id: a.customer_id for a in accounts if a.customer_id in customer_ids}
        txns_by_customer: dict[str, list[TransactionData]] = defaultdict(list)
        for t in transactions:
            owner = owner_of.get(t.account_id)
            if owner:
                txns_by_customer[owner].append(t)
            else:
                self.errors.append(
                    LoadError("transactions.csv", None, t.transaction_id,
                              f"account {t.account_id} not found or invalid")
                )

        created_at = datetime.now(UTC)  # one timestamp for the whole batch
        cases: list[CaseData] = []
        for c in customers:
            if not accounts_by_customer[c.customer_id]:
                self.errors.append(
                    LoadError("customers.csv", None, c.customer_id,
                              "customer has no valid accounts; case not built")
                )
                continue
            try:
                cases.append(
                    self.create_case_from_data(
                        c, accounts_by_customer[c.customer_id], txns_by_customer[c.customer_id],
                        created_at,
                    )
                )
            except ValidationError as exc:
                self.errors.append(LoadError("customers.csv", None, c.customer_id, str(exc)))

        summary = {
            "customers_valid": len(customers),
            "accounts_valid": len(accounts),
            "transactions_valid": len(transactions),
            "cases_built": len(cases),
            "errors": len(self.errors),
            "cases_with_quality_flags": sum(1 for k in cases if k.data_quality_flags),
            "quality_flags_total": sum(len(k.data_quality_flags) for k in cases),
        }
        self.logger.log_agent_action(
            agent_type="DataLoader",
            action="build_all_cases",
            case_id=None,
            input_data={"data_dir": self.data_dir},
            output_data={**summary, "first_errors": [e.__dict__ for e in self.errors[:10]]},
            reasoning="Validated CSV rows with Pydantic; grouped them into one case per customer.",
            execution_time_ms=(time.perf_counter() - start) * 1000,
            success=not self.errors,
            error_message=f"{len(self.errors)} load errors" if self.errors else None,
        )
        return cases
