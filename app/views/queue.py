# Phase 5 – Step 5.2: case queue — every case with its status, and Stage 1 (risk analysis) actions
import pandas as pd
import streamlit as st

from app.state import flash, get_workflow, show_flash
from app.ui_helpers import (
    MAX_BATCH,
    case_label,
    estimate_risk_cost,
    next_new_cases,
    queue_rows,
    status_counts,
)
from src.sar_workflow import CaseStatus

st.title("Case queue")
st.caption("Every case in this session. Run the Risk Analyst here; results go to Risk review.")
show_flash()

workflow = get_workflow()
records = list(workflow.records.values())

# Phase 5 – Step 5.2: where the cases are right now
counts = status_counts(records)
tiles = st.columns(5)
tiles[0].metric("New", counts["NEW"])
tiles[1].metric("Awaiting risk review", counts["RISK_REVIEW"])
tiles[2].metric("Awaiting compliance", counts["RISK_APPROVED"] + counts["COMPLIANCE_REVIEW"])
tiles[3].metric("SARs filed", counts["SAR_FILED"])
tiles[4].metric("Closed / manual", counts["CLOSED_RISK_REJECTED"]
                + counts["CLOSED_COMPLIANCE_REJECTED"] + counts["MANUAL_REVIEW"])


# Phase 5 – Step 5.2: Stage 1 for one case; the outcome is flashed after the rerun
def _analyze(case_id: str) -> None:
    rec = workflow.run_risk_analysis(case_id)
    if rec.status == CaseStatus.MANUAL_REVIEW:
        flash("error", f"{case_id}: risk analysis failed → manual review ({rec.error})")
    else:
        r = rec.risk_output
        flash("success", f"{case_id}: {r.classification} / {r.risk_level} → awaiting risk review")


st.subheader("Run risk analysis")
new_cases = next_new_cases(records, len(records))
avg = workflow.metrics().risk.avg_cost_usd
one, batch = st.columns(2)

with one:
    if new_cases:
        choice = st.selectbox(
            "Case", [r.case_id for r in new_cases], key="analyze_case",
            format_func=lambda cid: case_label(workflow.get(cid)),
        )
        if st.button("Analyze selected case", key="analyze_one", type="primary"):
            with st.spinner("Risk Analyst is reasoning (≈ 9 s)…"):
                _analyze(choice)
            st.rerun()
    else:
        st.info("No new cases left to analyze.")

with batch:
    n = st.number_input("Next N new cases", min_value=1, max_value=MAX_BATCH, value=5,
                        key="batch_n")
    todo = next_new_cases(records, int(n))
    st.caption(
        f"{len(todo)} case(s) · estimated cost ≈ ${estimate_risk_cost(todo, avg):.4f} "
        f"({'measured average' if avg is not None else 'Stage 04 average'}; "
        "zero-activity cases are free)"
    )
    if st.button(f"Analyze next {len(todo)}", key="analyze_batch", disabled=not todo):
        progress = st.progress(0.0, text="Starting…")
        for i, rec in enumerate(todo, start=1):
            progress.progress(
                (i - 1) / len(todo), text=f"Analyzing {rec.case_id} ({i}/{len(todo)})"
            )
            _analyze(rec.case_id)
        progress.progress(1.0, text="Done")
        st.rerun()

# Phase 5 – Step 5.2: the table, filterable by status (empty filter = all cases)
st.subheader("All cases")
selected = st.multiselect("Filter by status", [str(s) for s in CaseStatus], key="status_filter")
shown = [r for r in records if not selected or str(r.status) in selected]
st.dataframe(pd.DataFrame(queue_rows(shown)), hide_index=True, width="stretch")
st.caption(f"{len(shown)} of {len(records)} cases")
