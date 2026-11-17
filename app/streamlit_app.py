# Phase 5 – Step 5.1: Streamlit entry point — page config, sidebar and navigation
"""TRACE SAR review app.

Run from the repo root:  streamlit run app/streamlit_app.py

Every page is a thin layer over `src/sar_workflow.py`: buttons call workflow methods, so the
Stage 04 state machine, gate rules, audit events and SAR validation apply unchanged.
"""

import hmac
import sys
from pathlib import Path

# Phase 5 – Step 5.1: `streamlit run` puts app/ on sys.path, not the repo root; add the root so
# `src` and `app` import the same way as in tests and notebooks
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app.state import REVIEWER_KEY, get_workflow, reviewer_id  # noqa: E402
from src import config  # noqa: E402

st.set_page_config(page_title="TRACE – SAR Review", layout="wide")

PASSCODE_OK_KEY = "passcode_ok"


# Phase 6 – Step 6.4: optional passcode gate for the public URL (Stage 06 decision Q1).
# Runs before anything else, so no cases, agents or API calls are touched until it passes.
def passcode_gate() -> None:
    expected = config.APP_PASSCODE
    if not expected or st.session_state.get(PASSCODE_OK_KEY):
        return
    st.title("TRACE – SAR Review")
    with st.form("passcode_form"):
        entered = st.text_input("Passcode", type="password", key="passcode_input")
        submitted = st.form_submit_button("Enter", type="primary", key="passcode_submit")
    if submitted:
        # constant-time comparison: the response time does not reveal how much was right
        if hmac.compare_digest(entered.encode(), expected.encode()):
            st.session_state[PASSCODE_OK_KEY] = True
            st.rerun()
        st.error("Wrong passcode.")
    st.stop()

# Phase 5 – Step 5.1: one page per screen (Stage 05 plan, Steps 5.2–5.6)
PAGES = [
    st.Page("views/queue.py", title="Case queue", default=True),
    st.Page("views/risk_review.py", title="Risk review"),
    st.Page("views/compliance_review.py", title="Compliance review"),
    st.Page("views/sars_audit.py", title="SARs & audit"),
    st.Page("views/metrics.py", title="Metrics"),
]


# Phase 5 – Step 5.1: sidebar on every page — who is reviewing, which models, what it has cost
def render_sidebar() -> None:
    workflow = get_workflow()
    with st.sidebar:
        st.header("TRACE")
        st.text_input(
            "Reviewer ID",
            key=REVIEWER_KEY,
            placeholder="e.g. analyst_jane",
            help="Recorded with every gate decision. The compliance reviewer must differ from "
            "the risk reviewer (segregation of duties).",
        )
        if reviewer_id() is None:
            st.warning("Enter a reviewer ID to record decisions.")
        risk, compliance = workflow.risk_agent, workflow.compliance_agent
        st.caption(
            f"Risk: {getattr(risk, 'model', '?')} · prompt {getattr(risk, 'prompt_version', '?')}"
        )
        st.caption(
            f"Compliance: {getattr(compliance, 'model', '?')} · "
            f"prompt {getattr(compliance, 'prompt_version', '?')}"
        )
        st.metric("Session cost (est.)", f"${workflow.metrics().total_cost_usd:.4f}")


passcode_gate()
render_sidebar()
st.navigation(PAGES).run()
