# Phase 5 – Step 5.5: SARs & audit — the regulatory deliverables: filed SARs and the audit trail
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from app.state import get_workflow, show_flash
from app.ui_helpers import audit_rows, case_label, sar_sections
from src.foundation_sar import read_audit_events
from src.sar_workflow import CaseStatus

st.title("SARs & audit")
st.caption(
    "Filed SAR documents (JSON in outputs/sars/) and the audit trail an examiner would read."
)
show_flash()

workflow = get_workflow()
records = list(workflow.records.values())

# ---------------------------------------------------------------------------
# Phase 5 – Step 5.5: filed SARs
# ---------------------------------------------------------------------------
filed = [r for r in records if r.status == CaseStatus.SAR_FILED and r.sar_path]
st.subheader(f"Filed SARs ({len(filed)})")
if filed:
    sar_case = st.selectbox("SAR", [r.case_id for r in filed], key="sar_case",
                            format_func=lambda cid: f"{workflow.get(cid).sar_id} – "
                                                    f"{workflow.get(cid).case.customer.name}")
    rec = workflow.get(sar_case)
    raw = Path(rec.sar_path).read_text(encoding="utf-8")
    sections = sar_sections(json.loads(raw))
    with st.container(border=True):
        st.markdown(f"**Subject:** {sections['subject']}")
        st.markdown(f"**Accounts:** {', '.join(sections['accounts'])}")
        st.markdown(f"**Activity:** {sections['activity']}")
        st.markdown("**Narrative**")
        st.markdown(sections["narrative"])
        st.markdown(f"**Citations:** {'; '.join(sections['citations'])}")
        for review in sections["reviews"]:
            st.markdown(f"- {review}")
        st.caption(f"Provenance: {sections['provenance']}")
    st.download_button(
        "Download SAR (JSON)", data=raw, file_name=f"{rec.sar_id}.json",
        mime="application/json", key="sar_download", type="primary",
    )
    st.caption("The downloaded file is the full filing, including the subject's personal details.")
    with st.expander("Raw JSON"):
        st.code(raw, language="json")
else:
    st.caption("No SARs filed in this session yet.")

# ---------------------------------------------------------------------------
# Phase 5 – Step 5.5: audit trail for one case
# ---------------------------------------------------------------------------
st.subheader("Audit trail")
touched = [r for r in records if r.status != CaseStatus.NEW]
if touched:
    audit_case = st.selectbox("Case", [r.case_id for r in touched], key="audit_case",
                              format_func=lambda cid: f"{case_label(workflow.get(cid))} · "
                                                      f"{workflow.get(cid).status}")
    events = read_audit_events(workflow.logger.log_file, audit_case)
    st.dataframe(pd.DataFrame(audit_rows(events)), hide_index=True, width="stretch")
    st.caption(f"{len(events)} event(s) from {workflow.logger.log_file.name} · agents, human "
               "decisions (approvals and rejections) and workflow events, oldest first")
else:
    st.caption("No case has been processed yet.")
