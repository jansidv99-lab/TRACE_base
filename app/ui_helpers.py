# Phase 5 – Step 5.1: pure display helpers — no streamlit import, so they are unit-testable
"""Turn workflow objects into rows and values for display.

Pages call these and only lay the results out; formatting logic stays testable without a browser.
Planted transaction-ID prefixes (STRUCT_, LARGE_, ...) are the answer key of the synthetic data and
are never part of any row shown to a reviewer.
"""

import json
import re
from collections.abc import Iterable
from datetime import date
from typing import Any

from src.case_formatter import format_case_for_llm, remask
from src.foundation_sar import CaseData
from src.metrics import GateSummary, WorkflowMetrics
from src.narrative_checks import NarrativeReport, check_narrative
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


# ---------------------------------------------------------------------------
# Phase 5 – Step 5.3: risk review — evidence shown with the model's neutral refs (T001, ...)
# ---------------------------------------------------------------------------
# Real transaction IDs start with the planted answer key (STRUCT_, LARGE_, ...). On screen they are
# re-masked to the same T### refs the model saw, so a reviewer is not biased by the prefix and can
# still match an indicator to its row. SAR files and the audit log keep the real IDs.
_STEP = re.compile(r"(?=\bStep\s+\d+\s*[:.)-])")


# Phase 5 – Step 5.3: real ID → display ref for one case (same numbering as format_case_for_llm)
def display_refs(case: CaseData) -> dict[str, str]:
    return {real: ref for ref, real in format_case_for_llm(case).id_map.items()}


# Phase 5 – Step 5.3: replace real transaction IDs in text with their display refs
def masked(text: str, case: CaseData) -> str:
    return remask(text or "", format_case_for_llm(case).id_map)


# Phase 5 – Step 5.3: refs of the transactions the risk finding quotes (indicators or reasoning)
def cited_refs(rec: CaseRecord) -> set[str]:
    risk = rec.risk_output
    if risk is None:
        return set()
    text = " ".join([*risk.key_indicators, risk.reasoning])
    return {ref for real, ref in display_refs(rec.case).items() if real in text}


# Phase 5 – Step 5.3: profile for the reviewer — no DOB, SSN, address or phone on screen
def customer_profile(case: CaseData, as_of: date | None = None) -> dict[str, Any]:
    c, as_of = case.customer, as_of or case.case_created_at.date()
    years = as_of.year - c.date_of_birth.year - (
        (as_of.month, as_of.day) < (c.date_of_birth.month, c.date_of_birth.day)
    )
    return {
        "Name": c.name,
        "Occupation": c.occupation,
        "Age": years,
        "Customer since": c.customer_since.isoformat(),
        "Profile risk rating": c.risk_rating,
        "Annual income": f"${c.annual_income:,.0f}",
    }


# Phase 5 – Step 5.3: accounts with short labels (ACC_1), as the model saw them
def account_rows(case: CaseData) -> list[dict[str, Any]]:
    return [{
        "account": a.account_id.split("_", 2)[-1],
        "type": a.account_type.replace("_", " "),
        "status": a.status,
        "opened": a.opening_date.isoformat(),
        "balance": round(a.current_balance, 2),
        "avg_monthly_balance": round(a.average_monthly_balance, 2),
    } for a in case.accounts]


# Phase 5 – Step 5.3: transactions with display refs; `cited` marks the rows the finding quotes
def transaction_rows(case: CaseData, cited: set[str]) -> list[dict[str, Any]]:
    refs = display_refs(case)
    rows = []
    for t in case.transactions:
        ref = refs[t.transaction_id]
        rows.append({
            "cited": ref in cited,
            "ref": ref,
            "date": t.transaction_date.isoformat(),
            "account": t.account_id.split("_", 2)[-1],
            "type": t.transaction_type.replace("_", " "),
            "amount": round(t.amount, 2),
            "method": t.method,
            "location": t.location or "",
            "counterparty": t.counterparty or "",
            "description": t.description,
        })
    return rows


# Phase 5 – Step 5.3: "Step 1: ... Step 2: ..." → one string per step (whole text if no steps)
def reasoning_steps(text: str) -> list[str]:
    parts = [p.strip() for p in _STEP.split(text or "") if p.strip()]
    return parts or [text]


# ---------------------------------------------------------------------------
# Phase 5 – Step 5.4: compliance review
# ---------------------------------------------------------------------------
FIVE_WS = ("who", "what", "when", "where", "why")


# Phase 5 – Step 5.4: re-run the Stage 03 checks on the narrative awaiting review
def narrative_report(rec: CaseRecord) -> NarrativeReport | None:
    out, risk = rec.compliance_output, rec.risk_output
    if out is None or risk is None:
        return None
    return check_narrative(out.narrative, out.regulatory_citations, rec.case, risk.classification)


# Phase 5 – Step 5.4: "✅ who  ❌ when ..." from a report's five_ws dict
def five_ws_line(report: NarrativeReport) -> str:
    return "   ".join(f"{'✅' if report.five_ws.get(w) else '❌'} {w}" for w in FIVE_WS)


# Phase 5 – Step 5.4: short, readable text for one tool call's arguments or result
def _short(value: Any, limit: int = 90) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _observation_text(action: str | None, obs: Any) -> str:
    if not isinstance(obs, dict):
        return _short(obs)
    if "error" in obs:
        return f"error: {_short(obs['error'])}"
    if action == "get_transaction_details":
        found = [t.get("ref") for t in obs.get("transactions", [])]
        unknown = obs.get("unknown_refs")
        return f"{len(found)} found" + (f", unknown {unknown}" if unknown else "")
    if action == "lookup_regulations":
        return ", ".join(c.get("citation", "") for c in obs.get("citations", []))
    if action in ("check_narrative", "submit_narrative"):
        status = "accepted" if obs.get("accepted") else (
            "passed" if obs.get("passed") else "rejected"
        )
        errors = obs.get("errors") or []
        words = f" · {obs['word_count']} words" if "word_count" in obs else ""
        return status + words + (f" · {len(errors)} error(s)" if errors else "")
    return _short(obs)


# Phase 5 – Step 5.4: one row per ReACT step (Thought → Action → Observation)
def trace_rows(trace: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for step in trace:
        args = step.get("arguments")
        if isinstance(args, dict) and "narrative" in args:
            args = {**args, "narrative": f"<draft, {len(str(args['narrative']).split())} words>"}
        rows.append({
            "step": step.get("step"),
            "thought": step.get("thought") or "",
            "action": step.get("action") or "(text only)",
            "arguments": _short(args) if args is not None else "",
            "observation": _observation_text(step.get("action"), step.get("observation")),
        })
    return rows


# ---------------------------------------------------------------------------
# Phase 5 – Step 5.5: SAR and audit views
# ---------------------------------------------------------------------------
# Phase 5 – Step 5.5: the SAR's sections for display — the screen shows name and occupation only;
# the downloadable JSON is the full filing (Stage 04 decision 2)
def sar_sections(sar: dict[str, Any]) -> dict[str, Any]:
    subject, activity, prov = sar["subject"], sar["activity"], sar.get("provenance") or {}
    return {
        "subject": f"{subject['name']} · {subject['occupation']} · {subject['customer_id']}",
        "accounts": [
            f"{a['account_type'].replace('_', ' ')} ({a['status']})" for a in sar["accounts"]
        ],
        "activity": (
            f"{activity['typology']} · {activity['risk_level']} · "
            f"confidence {activity['confidence_score']:.0%} · "
            f"{activity['start_date']} to {activity['end_date']} · "
            f"${activity['total_amount']:,.2f} over {len(activity['transaction_ids'])} "
            "transaction(s)"
        ),
        "narrative": sar["narrative"]["narrative"],
        "citations": sar["narrative"]["regulatory_citations"],
        "reviews": [
            f"{label}: {r['decision']} by {r['reviewer_id']} at {r['decided_at'][:19]}"
            for label, r in (("Risk gate", sar["risk_review"]),
                             ("Compliance gate", sar["compliance_review"]))
        ],
        "provenance": (
            f"risk {prov.get('risk_model')} / {prov.get('risk_prompt_version')} · "
            f"compliance {prov.get('compliance_model')} / {prov.get('compliance_prompt_version')}"
        ),
    }


# Phase 5 – Step 5.5: one row per audit event — who did what, when, at what cost, and why
def audit_rows(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "time": (e.get("timestamp") or "")[:19].replace("T", " "),
        "actor": e.get("agent_type"),
        "action": e.get("action"),
        "success": e.get("success"),
        "reviewer": e.get("reviewer_id") or "",
        "tokens": (e.get("token_usage") or {}).get("total_tokens"),
        "prompt": e.get("prompt_version"),
        "reasoning": _short(e.get("reasoning") or e.get("error_message") or "", 120),
    } for e in events]


# ---------------------------------------------------------------------------
# Phase 5 – Step 5.6: metrics panel
# ---------------------------------------------------------------------------
NOT_MEASURABLE = "not measurable yet"


def _rate(gate: GateSummary) -> str:
    if gate.approval_rate is None:
        return "—"
    return f"{gate.approval_rate:.0%} ({gate.approved}/{gate.decided})"


# Phase 5 – Step 5.6: headline tiles as display strings; no savings claim without a measurement
def metric_tiles(m: WorkflowMetrics) -> dict[str, str]:
    processed = m.cases - m.status_counts.get("NEW", 0)
    avoided, rate = m.cost_avoided_usd, m.savings_rate
    return {
        "Cases processed": f"{processed} / {m.cases}",
        "SARs filed": str(m.status_counts.get("SAR_FILED", 0)),
        "Total cost (est.)": f"${m.total_cost_usd:.4f}",
        "Cost avoided": NOT_MEASURABLE if avoided is None else f"${avoided:.4f}",
        "Savings rate": "—" if rate is None else f"{rate:.0%}",
        "Time avoided": "—" if m.time_avoided_s is None else f"{m.time_avoided_s:.0f} s",
        "Risk gate approval": _rate(m.risk_gate),
        "Compliance gate approval": _rate(m.compliance_gate),
    }


# Phase 5 – Step 5.6: runs whose model has no price entry (tokens counted, dollars not)
def unpriced_runs(m: WorkflowMetrics) -> int:
    return m.risk.unpriced_runs + m.compliance.unpriced_runs
