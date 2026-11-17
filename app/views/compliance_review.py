# Phase 5 – Step 5.4: compliance review — narratives for approved cases and the compliance gate
import pandas as pd
import streamlit as st

from app.state import flash, get_workflow, record_decision, show_flash
from app.ui_helpers import case_label, five_ws_line, masked, narrative_report, trace_rows
from src.sar_workflow import CaseStatus

st.title("Compliance review")
st.caption(
    "Generate SAR narratives for cases approved at the risk gate, then approve (files the SAR) or "
    "reject them. The compliance reviewer must differ from the risk reviewer."
)
show_flash()

workflow = get_workflow()
records = list(workflow.records.values())
ready = [r for r in records if r.status == CaseStatus.RISK_APPROVED]
waiting = [r for r in records if r.status == CaseStatus.COMPLIANCE_REVIEW]
manual = [r for r in records if r.status == CaseStatus.MANUAL_REVIEW]


def _label(cid: str) -> str:
    return case_label(workflow.get(cid))


# ---------------------------------------------------------------------------
# Phase 5 – Step 5.4: Stage 2 — the ReACT agent writes the narrative
# ---------------------------------------------------------------------------
st.subheader(f"Ready for narrative ({len(ready)})")
if ready:
    ready_id = st.selectbox("Case", [r.case_id for r in ready], key="narrate_case",
                            format_func=_label)
    if st.button("Generate narrative", key="narrate", type="primary"):
        with st.spinner("Compliance Officer is drafting and checking the narrative (≈ 24 s)…"):
            rec = workflow.run_compliance(ready_id)
        if rec.status == CaseStatus.MANUAL_REVIEW:
            flash("error", f"{ready_id}: narrative generation failed → manual review ({rec.error})")
        else:
            flash("success", f"{ready_id}: narrative ready after "
                             f"{rec.compliance_stats.steps} ReACT step(s) → awaiting review")
        st.rerun()
else:
    st.caption("No cases approved at the risk gate yet.")

# ---------------------------------------------------------------------------
# Phase 5 – Step 5.4: the compliance gate
# ---------------------------------------------------------------------------
st.subheader(f"Awaiting compliance review ({len(waiting)})")
if waiting:
    case_id = st.selectbox("Case", [r.case_id for r in waiting], key="compliance_case",
                           format_func=_label)
    rec = workflow.get(case_id)
    out, risk, report = rec.compliance_output, rec.risk_output, narrative_report(rec)

    st.caption(
        f"{risk.classification} / {risk.risk_level} · risk gate approved by "
        f"**{rec.risk_decision.reviewer_id}** (use a different reviewer ID here)"
    )
    with st.container(border=True):
        st.markdown(out.narrative)
    tiles = st.columns(3)
    tiles[0].metric("Words", f"{report.word_count} / <120")
    tiles[1].metric("Checks", "passed" if report.passed else "failed")
    tiles[2].metric("Warnings", len(report.warnings))
    st.markdown(f"**5 W's:** {five_ws_line(report)}")
    for warning in report.warnings:
        st.warning(warning)
    for error in report.errors:
        st.error(error)
    st.markdown("**Citations:** " + "; ".join(out.regulatory_citations))
    with st.expander("Why the narrative was built this way"):
        st.markdown(masked(out.narrative_reasoning, rec.case))
    with st.expander(f"ReACT trace ({len(rec.compliance_trace)} steps)"):
        st.dataframe(pd.DataFrame(trace_rows(rec.compliance_trace)), hide_index=True,
                     width="stretch")

    with st.form("compliance_decision_form"):
        choice = st.radio("Decision", ["approve", "reject"], horizontal=True,
                          key="compliance_choice")
        reason = st.text_area("Reason (required to reject)", key="compliance_reason")
        submitted = st.form_submit_button("Submit compliance decision", type="primary",
                                          key="compliance_submit")
    if submitted and record_decision("compliance", case_id, choice, reason):
        st.rerun()
else:
    st.caption("No narratives waiting for review.")

# ---------------------------------------------------------------------------
# Phase 5 – Step 5.4: failures stay visible — a person must take these over
# ---------------------------------------------------------------------------
st.subheader(f"Manual review ({len(manual)})")
if manual:
    st.dataframe(pd.DataFrame([{
        "customer": r.case.customer.customer_id,
        "failed_stage": "compliance" if r.risk_decision else "risk analysis",
        "error": r.error,
        "case_id": r.case_id,
    } for r in manual]), hide_index=True, width="stretch")
else:
    st.caption("No agent failures.")
