# Phase 5 – Step 5.3: risk review — the human risk gate, with the evidence to judge the finding
import pandas as pd
import streamlit as st

from app.state import get_workflow, record_decision, show_flash
from app.ui_helpers import (
    account_rows,
    case_label,
    cited_refs,
    customer_profile,
    masked,
    reasoning_steps,
    transaction_rows,
)
from src.sar_workflow import CaseStatus

st.title("Risk review")
st.caption(
    "Approve to send the case to the Compliance Officer, or reject with a reason to close it. "
    "Transactions appear as T001, T002, … exactly as the model saw them."
)
show_flash()

workflow = get_workflow()
waiting = [r for r in workflow.records.values() if r.status == CaseStatus.RISK_REVIEW]
if not waiting:
    st.info("No cases are waiting for risk review. Analyze cases in the Case queue first.")
    st.stop()

case_id = st.selectbox(
    f"Case ({len(waiting)} waiting)", [r.case_id for r in waiting], key="risk_case",
    format_func=lambda cid: case_label(workflow.get(cid)),
)
rec = workflow.get(case_id)
case, risk = rec.case, rec.risk_output

# Phase 5 – Step 5.3: the finding
st.subheader("Risk Analyst finding")
tiles = st.columns(3)
tiles[0].metric("Classification", risk.classification)
tiles[1].metric("Risk level", risk.risk_level)
tiles[2].metric("Confidence", f"{risk.confidence_score:.0%}")
st.markdown("**Key indicators**")
for indicator in risk.key_indicators:
    st.markdown(f"- {masked(indicator, case)}")
with st.expander("Chain-of-Thought reasoning", expanded=True):
    for step in reasoning_steps(masked(risk.reasoning, case)):
        st.markdown(step)

# Phase 5 – Step 5.3: the evidence — who the customer is and what happened
profile_col, accounts_col = st.columns([1, 2])
with profile_col:
    st.subheader("Customer")
    profile = customer_profile(case)
    st.table(pd.DataFrame({"value": [str(v) for v in profile.values()]}, index=list(profile)))
with accounts_col:
    st.subheader("Accounts")
    st.dataframe(pd.DataFrame(account_rows(case)), hide_index=True, width="stretch")

st.subheader("Transactions")
cited = cited_refs(rec)
rows = transaction_rows(case, cited)
only_cited = st.checkbox(f"Show only the {len(cited)} cited transaction(s)", key="only_cited",
                         disabled=not cited)
table = pd.DataFrame([r for r in rows if r["cited"] or not only_cited])
if not table.empty:
    table = table.style.apply(
        lambda row: ["background-color: rgba(255, 196, 0, 0.30)" if row["cited"] else ""]
        * len(row),
        axis=1,
    )
st.dataframe(table, hide_index=True, width="stretch")
st.caption(f"{len(rows)} transactions · highlighted rows are quoted in the finding")

# Phase 5 – Step 5.3: the gate decision
st.subheader("Decision")
with st.form("risk_decision_form"):
    choice = st.radio("Decision", ["approve", "reject"], horizontal=True, key="risk_choice")
    reason = st.text_area("Reason (required, at least 10 characters)", key="risk_reason")
    submitted = st.form_submit_button("Submit risk decision", type="primary", key="risk_submit")
if submitted and record_decision("risk", case_id, choice, reason):
    st.rerun()
