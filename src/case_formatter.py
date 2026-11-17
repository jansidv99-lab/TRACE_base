# Phase 2 – Step 2.3: turn a CaseData into a compact, PII-free, leak-free prompt for the LLM
"""Case → user-message formatter.

Three jobs:
1. **Masking:** real transaction IDs (e.g. STRUCT_7D3B8E94) reveal the planted pattern, so the
   model only ever sees neutral references T001, T002, … ; `id_map` lets us translate back.
2. **Data minimisation:** the model gets what an analyst needs (name, age, occupation, income,
   risk rating, tenure) and never SSN, address, phone or date of birth.
3. **No ready-made red flags:** the activity summary is neutral counts and totals only
   (Stage 02 decision 1); spotting patterns is the model's job.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from src.foundation_sar import CaseData, TransactionData

# Phase 2 – Step 2.3: matches masked references such as T001 or T0123 in model output
_REF_PATTERN = re.compile(r"\bT\d{3,}\b")


# Phase 2 – Step 2.3: the prompt text plus the mapping needed to undo the masking
@dataclass(frozen=True)
class FormattedCase:
    text: str
    id_map: dict[str, str] = field(default_factory=dict)  # {"T001": "STRUCT_7D3B8E94", ...}


# Phase 2 – Step 2.3: whole years between two dates (birthday-aware)
def _years_between(start: date, end: date) -> int:
    return end.year - start.year - ((end.month, end.day) < (start.month, start.day))


# Phase 2 – Step 2.3: "CUST_0002_ACC_1" → "ACC_1" (shorter, and the customer is already known)
def _short_account(account_id: str) -> str:
    return account_id.split("_", 2)[-1]


# Phase 2 – Step 2.3: US-style money formatting, e.g. 9168.5 → "9,168.50"
def _money(amount: float) -> str:
    return f"{amount:,.2f}"


# Phase 2 – Step 2.3: neutral activity totals — counts and sums only, no thresholds or flags
def _activity_summary(transactions: list[TransactionData]) -> list[str]:
    if not transactions:
        return ["No transactions in the review period."]
    by_type: dict[str, list[float]] = defaultdict(list)
    for t in transactions:
        by_type[t.transaction_type].append(t.amount)
    first, last = transactions[0].transaction_date, transactions[-1].transaction_date
    locations = {t.location for t in transactions if t.location}
    counterparties = {t.counterparty for t in transactions if t.counterparty}
    lines = [
        f"Period: {first} to {last} ({(last - first).days + 1} days), "
        f"{len(transactions)} transactions, total {_money(sum(t.amount for t in transactions))}",
        f"Distinct locations: {len(locations)} | Distinct counterparties: {len(counterparties)}",
        "By type (count | total USD):",
    ]
    for tx_type in sorted(by_type):
        amounts = by_type[tx_type]
        lines.append(f"- {tx_type}: {len(amounts)} | {_money(sum(amounts))}")
    return lines


# Phase 2 – Step 2.3: build the masked, PII-free user message for one case
def format_case_for_llm(case: CaseData) -> FormattedCase:
    """Render `case` as the user message described in the risk analyst prompt's "Input" section."""
    as_of = case.case_created_at.date()
    customer = case.customer

    # Transactions are already in date order (DataLoader sorts them); number them T001, T002, ...
    width = max(3, len(str(len(case.transactions))))
    id_map = {f"T{i:0{width}d}": t.transaction_id for i, t in enumerate(case.transactions, 1)}
    ref_of = {real: ref for ref, real in id_map.items()}

    lines = [
        "## 1. Customer profile",
        f"Name: {customer.name}",
        f"Age: {_years_between(customer.date_of_birth, as_of)}",
        f"Occupation: {customer.occupation}",
        f"Annual income: {_money(customer.annual_income)} USD",
        f"Bank risk rating: {customer.risk_rating}",
        f"Customer since: {customer.customer_since} "
        f"({_years_between(customer.customer_since, as_of)} years)",
        "",
        "## 2. Accounts (label | type | opened | status | balance USD | avg monthly balance USD)",
    ]
    lines += [
        f"{_short_account(a.account_id)} | {a.account_type} | {a.opening_date} | {a.status} | "
        f"{_money(a.current_balance)} | {_money(a.average_monthly_balance)}"
        for a in case.accounts
    ]

    lines += ["", "## 3. Activity summary", *_activity_summary(case.transactions), ""]

    lines.append(
        "## 4. Transactions (ref | date | account | type | amount USD | method | location | "
        "counterparty | description)"
    )
    if case.transactions:
        lines += [
            f"{ref_of[t.transaction_id]} | {t.transaction_date} | {_short_account(t.account_id)} | "
            f"{t.transaction_type} | {_money(t.amount)} | {t.method} | {t.location or '-'} | "
            f"{t.counterparty or '-'} | {t.description}"
            for t in case.transactions
        ]
    else:
        lines.append("None.")

    lines += ["", "## 5. Data-quality notes"]
    if case.data_quality_flags:
        lines += [f"- {_mask(flag, ref_of, case)}" for flag in case.data_quality_flags]
    else:
        lines.append("None.")

    return FormattedCase(text="\n".join(lines), id_map=id_map)


# Phase 2 – Step 2.3: replace real transaction/account IDs inside a data-quality flag
def _mask(text: str, ref_of: dict[str, str], case: CaseData) -> str:
    for real, ref in ref_of.items():
        text = text.replace(real, ref)
    for account in case.accounts:
        text = text.replace(account.account_id, _short_account(account.account_id))
    return text


# Phase 3 – Step 3.6: the reverse of unmask — hide real IDs again (e.g. in approved risk findings)
def remask(text: str, id_map: dict[str, str]) -> str:
    """Replace real transaction IDs with their T### references."""
    for ref, real in id_map.items():
        text = text.replace(real, ref)
    return text


# Phase 2 – Step 2.3: put real transaction IDs back into model output (reasoning, indicators)
def unmask(text: str, id_map: dict[str, str]) -> str:
    """Replace T### references with the real transaction IDs; unknown refs are left as-is."""
    return _REF_PATTERN.sub(lambda m: id_map.get(m.group(0), m.group(0)), text)
