# Phase 3 – Step 3.3: deterministic referee for SAR narratives — the model proposes, code decides
"""Narrative checks.

`check_narrative()` returns a NarrativeReport. **Errors** block submission; **warnings** are
recorded for the human reviewer but do not block (Stage 03 decision 3).

| Check        | Rule                                                                  | Severity |
|--------------|-----------------------------------------------------------------------|----------|
| Length       | fewer than 120 words                                                  | error    |
| Who          | the customer's full name appears                                      | error    |
| What         | an instrument / mechanism term (cash, deposit, wire, transfer, ...)   | error    |
| When         | at least one date                                                     | error    |
| Where        | a branch, location, ATM, online channel or account                    | error    |
| Why          | a reason-for-suspicion term (threshold, inconsistent, pattern, ...)   | error    |
| Terminology  | no speculative or accusatory words (guilty, probably, ...)            | error    |
| Internal IDs | no T001 refs, transaction IDs, CUST_ IDs or ACC_ labels               | error    |
| Dates        | every full date is a transaction date or account opening date        | error    |
| Amounts      | every $ amount matches a known figure from the case                   | warning  |
| Citations    | all from the curated table; general + typology-specific included      | error    |

The 5-W checks are keyword heuristics: they catch a missing element, not a weak one. Judging the
*quality* of each element is a Stage 08 (LLM-as-judge) task.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from src.foundation_sar import NARRATIVE_WORD_LIMIT, CaseData, SuspiciousActivityType, count_words
from src.regulations import (
    GENERAL_SAR_CITATIONS,
    TYPOLOGY_CITATIONS,
    is_allowed_citation,
    normalize_citation,
)

# ---------------------------------------------------------------------------
# Phase 3 – Step 3.3: vocabulary for the 5 W's and terminology checks
# ---------------------------------------------------------------------------
_WHAT = re.compile(
    r"\b(cash|deposits?|withdrawals?|wires?|wired|transfers?|transferred|ach|checks?|cheques?|"
    r"debits?|credits?|purchases?|payments?)\b",
    re.IGNORECASE,
)
_WHERE = re.compile(
    r"\b(branch(es)?|locations?|atms?|online|mobile|accounts?|branch_\w+)\b", re.IGNORECASE
)
_WHY = re.compile(
    r"\b(threshold|reporting requirements?|structur\w*|inconsistent|unusual|suspicious|rapid(ly)?|"
    r"patterns?|evad\w*|avoid\w*|unexplained|atypical|layering|disproportionate|"
    r"no apparent (business|lawful) purpose)\b",
    re.IGNORECASE,
)
# Speculation and conclusions of guilt have no place in a factual SAR narrative
BANNED_TERMS = (
    "guilty", "criminal", "criminals", "crime", "crimes", "launderer", "launderers", "fraudster",
    "fraudsters",
    "obviously", "probably", "definitely", "undoubtedly", "certainly", "must have",
)
_BANNED = re.compile(r"\b(" + "|".join(re.escape(t) for t in BANNED_TERMS) + r")\b", re.IGNORECASE)
# Database keys: masked refs (T001), raw transaction IDs, customer IDs, account labels
_INTERNAL_ID = re.compile(r"\b(T\d{3,}|[A-Z]+_[0-9A-F]{8}|CUST_\d{4}\w*|ACC_\d+)\b")

# ---------------------------------------------------------------------------
# Phase 3 – Step 3.3: dates and amounts as they appear in prose
# ---------------------------------------------------------------------------
_MONTHS = {
    m: i
    for i, names in enumerate(
        [("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
         ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
         ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
         ("december", "dec")],
        start=1,
    )
    for m in names
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_MDY_DATE = re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)
_DMY_DATE = re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_RE})\.?,?\s+(\d{{4}})\b", re.IGNORECASE)
_MONTH_DAY = re.compile(rf"\b({_MONTH_RE})\.?\s+\d{{1,2}}\b", re.IGNORECASE)  # "July 22" (no year)
_AMOUNT = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\b")
# Regulatory thresholds a narrative may legitimately mention
THRESHOLD_AMOUNTS = (10_000.00, 5_000.00)


# Phase 3 – Step 3.3: result of checking one narrative
@dataclass
class NarrativeReport:
    word_count: int
    five_ws: dict[str, bool]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors

    # Phase 3 – Step 3.3: JSON-friendly form, used as the tool observation and in the audit log
    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "word_count": self.word_count,
            "word_limit": f"fewer than {NARRATIVE_WORD_LIMIT}",
            "five_ws": self.five_ws,
            "errors": self.errors,
            "warnings": self.warnings,
        }


# Phase 3 – Step 3.3: every full date written in the text, as (original text, date or None)
def extract_dates(text: str) -> list[tuple[str, date | None]]:
    found: list[tuple[str, date | None]] = []

    def add(raw: str, year: str, month: int | str, day: str) -> None:
        try:
            found.append((raw, date(int(year), int(month), int(day))))
        except ValueError:
            found.append((raw, None))  # e.g. February 30

    for m in _ISO_DATE.finditer(text):
        add(m.group(0), m.group(1), m.group(2), m.group(3))
    for m in _US_DATE.finditer(text):
        add(m.group(0), m.group(3), m.group(1), m.group(2))
    for m in _MDY_DATE.finditer(text):
        add(m.group(0), m.group(3), _MONTHS[m.group(1).lower()], m.group(2))
    for m in _DMY_DATE.finditer(text):
        add(m.group(0), m.group(3), _MONTHS[m.group(2).lower()], m.group(1))
    return found


# Phase 3 – Step 3.3: every dollar amount written in the text, as (original text, value)
def extract_amounts(text: str) -> list[tuple[str, float]]:
    return [
        (m.group(0), float(m.group(1).replace(",", "") + "." + (m.group(2) or "0")))
        for m in _AMOUNT.finditer(text)
    ]


# Phase 3 – Step 3.3: figures the narrative may quote without a warning
def _known_amounts(case: CaseData) -> set[float]:
    amounts = {round(t.amount, 2) for t in case.transactions}
    for a in case.accounts:
        amounts |= {round(a.current_balance, 2), round(a.average_monthly_balance, 2)}
    amounts.add(round(case.customer.annual_income, 2))
    amounts |= set(THRESHOLD_AMOUNTS)
    return amounts


# Phase 3 – Step 3.3: run every check on one narrative
def check_narrative(
    narrative: str,
    citations: list[str],
    case: CaseData,
    typology: SuspiciousActivityType,
) -> NarrativeReport:
    text = narrative or ""
    words = count_words(text)
    five_ws = {
        "who": case.customer.name.lower() in text.lower(),
        "what": bool(_WHAT.search(text)),
        "when": bool(extract_dates(text) or _MONTH_DAY.search(text)),
        "where": bool(_WHERE.search(text)),
        "why": bool(_WHY.search(text)),
    }
    report = NarrativeReport(word_count=words, five_ws=five_ws)
    errors, warnings = report.errors, report.warnings

    # Length
    if words == 0:
        errors.append("narrative is empty")
    elif words >= NARRATIVE_WORD_LIMIT:
        errors.append(f"narrative has {words} words; it must be fewer than {NARRATIVE_WORD_LIMIT}")

    # 5 W's
    hints = {
        "who": f"name the subject, '{case.customer.name}'",
        "what": "state the instruments or mechanisms used (e.g. cash deposits, wire transfers)",
        "when": "give the dates or date range of the activity",
        "where": "say where it happened (branch, ATM, online) or which account type",
        "why": "explain why the activity is suspicious",
    }
    for w, ok in five_ws.items():
        if not ok:
            errors.append(f"missing '{w}': {hints[w]}")

    # Terminology and internal identifiers
    banned = sorted({m.group(0).lower() for m in _BANNED.finditer(text)})
    if banned:
        errors.append(f"speculative or accusatory wording not allowed: {banned}")
    internal = sorted({m.group(0) for m in _INTERNAL_ID.finditer(text)})
    if internal:
        errors.append(f"internal identifiers must not appear in the narrative: {internal}")

    # Facts: dates (strict) and amounts (warning)
    allowed_dates = {t.transaction_date for t in case.transactions} | {
        a.opening_date for a in case.accounts
    }
    for raw, parsed in extract_dates(text):
        if parsed is None:
            errors.append(f"invalid date '{raw}'")
        elif parsed not in allowed_dates:
            errors.append(f"date '{raw}' does not match any transaction in the case")
    known = _known_amounts(case)
    for raw, value in extract_amounts(text):
        if round(value, 2) not in known:
            warnings.append(f"unverified amount '{raw}': not a single transaction or known figure")

    # Citations
    if not citations:
        errors.append("at least one regulatory citation is required")
    unknown = [c for c in citations if not is_allowed_citation(c)]
    if unknown:
        errors.append(f"citations not in the approved table: {unknown}")
    given = {normalize_citation(c) for c in citations}
    if not given & {normalize_citation(c.citation) for c in GENERAL_SAR_CITATIONS}:
        errors.append("include a general SAR citation (31 U.S.C. 5318(g) or 31 CFR 1020.320)")
    specific = {normalize_citation(c.citation) for c in TYPOLOGY_CITATIONS.get(typology, ())}
    if specific and not given & specific:
        errors.append(f"include at least one {typology}-specific citation")

    return report
