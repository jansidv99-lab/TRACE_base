# Phase 3 – Step 3.2: curated BSA/AML citation table — the only citations a SAR narrative may use
"""Regulatory citations for SAR narratives.

The Compliance Officer agent gets citations ONLY through the `lookup_regulations` tool, which reads
this table, and the narrative validator rejects anything not listed here. That keeps the model from
inventing or misquoting law.

Educational reference for this project, not legal advice.
"""

import re
from dataclasses import dataclass

from src.foundation_sar import SuspiciousActivityType


# Phase 3 – Step 3.2: one citation with a plain-English explanation
@dataclass(frozen=True)
class Citation:
    citation: str  # exactly as it should appear in a narrative, e.g. "31 U.S.C. 5324"
    title: str
    description: str


# Phase 3 – Step 3.2: apply to every SAR — the legal basis for filing at all
GENERAL_SAR_CITATIONS: tuple[Citation, ...] = (
    Citation(
        "31 U.S.C. 5318(g)",
        "Reporting of suspicious transactions",
        "Bank Secrecy Act authority requiring financial institutions to report suspicious "
        "transactions to FinCEN.",
    ),
    Citation(
        "31 CFR 1020.320",
        "Reports by banks of suspicious transactions",
        "FinCEN rule requiring banks to file a SAR for suspicious transactions of $5,000 or more, "
        "including those designed to evade BSA reporting requirements.",
    ),
)

# Phase 3 – Step 3.2: typology-specific citations (Other has none beyond the general ones)
TYPOLOGY_CITATIONS: dict[str, tuple[Citation, ...]] = {
    "Structuring": (
        Citation(
            "31 U.S.C. 5324",
            "Structuring transactions to evade reporting requirements",
            "Prohibits breaking up cash transactions to avoid the $10,000 Currency Transaction "
            "Report threshold.",
        ),
        Citation(
            "31 CFR 1010.311",
            "Reports of transactions in currency",
            "Requires a Currency Transaction Report (CTR) for cash transactions over $10,000.",
        ),
    ),
    "Money_Laundering": (
        Citation(
            "18 U.S.C. 1956",
            "Laundering of monetary instruments",
            "Criminalises conducting transactions with proceeds of unlawful activity to conceal "
            "their source or promote the activity.",
        ),
        Citation(
            "18 U.S.C. 1957",
            "Monetary transactions in criminally derived property",
            "Criminalises monetary transactions over $10,000 in property derived from specified "
            "unlawful activity.",
        ),
    ),
    "Fraud": (
        Citation(
            "18 U.S.C. 1344",
            "Bank fraud",
            "Criminalises schemes to defraud a financial institution or obtain its funds by false "
            "pretences.",
        ),
        Citation(
            "18 U.S.C. 1343",
            "Fraud by wire",
            "Criminalises schemes to defraud that use interstate wire communications, including "
            "electronic transfers.",
        ),
    ),
    "Sanctions": (
        Citation(
            "50 U.S.C. 1701-1706",
            "International Emergency Economic Powers Act (IEEPA)",
            "Legal basis for U.S. economic sanctions programmes administered by OFAC.",
        ),
        Citation(
            "31 CFR Part 501",
            "OFAC reporting, procedures and penalties",
            "Requires reporting of blocked and rejected transactions involving sanctioned parties.",
        ),
    ),
    "Other": (),
}


# Phase 3 – Step 3.2: general + typology-specific citations, in that order
def citations_for(typology: SuspiciousActivityType) -> list[Citation]:
    if typology not in TYPOLOGY_CITATIONS:
        raise ValueError(
            f"unknown typology {typology!r}; expected one of {list(TYPOLOGY_CITATIONS)}"
        )
    return [*GENERAL_SAR_CITATIONS, *TYPOLOGY_CITATIONS[typology]]


# Phase 3 – Step 3.2: "U.S.C." / "U S C" / "usc" → "USC"; same for C.F.R. (section dots are kept)
_USC = re.compile(r"\bU\.?\s*S\.?\s*C\b\.?", re.IGNORECASE)
_CFR = re.compile(r"\bC\.?\s*F\.?\s*R\b\.?", re.IGNORECASE)


# Phase 3 – Step 3.2: compare citations ignoring formatting ("31 U.S.C. § 5324" == "31 usc 5324")
def normalize_citation(text: str) -> str:
    text = text.replace("§", " ").replace("–", "-").replace("—", "-")
    text = _CFR.sub("CFR", _USC.sub("USC", text))
    return re.sub(r"\s+", " ", text).strip().upper()


# Phase 3 – Step 3.2: every citation in the table, normalised, mapped to its entry
ALLOWED_CITATIONS: dict[str, Citation] = {
    normalize_citation(c.citation): c
    for c in (*GENERAL_SAR_CITATIONS, *(c for cs in TYPOLOGY_CITATIONS.values() for c in cs))
}


# Phase 3 – Step 3.2: is this citation in the curated table?
def is_allowed_citation(text: str) -> bool:
    return normalize_citation(text) in ALLOWED_CITATIONS
