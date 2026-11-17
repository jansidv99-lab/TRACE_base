# Phase 5 – Step 5.1: shared app state — cached resources and the per-session workflow
"""Where things live across Streamlit reruns.

Streamlit reruns the whole script on every click, so:
- **shared and expensive** objects (the 150 loaded cases, the LLM client) are created once per
  server process with `st.cache_resource`;
- **per-user** state (the `SARWorkflow` and its case records) lives in `st.session_state`
  (Stage 05 decision 1: session only; the audit log and SAR files persist on disk).

Tests inject a workflow with fake agents by putting it in `st.session_state["workflow"]` before the
app runs; `get_workflow()` then uses it as is.
"""

from typing import Any

import streamlit as st
from pydantic import ValidationError

from src import config
from src.foundation_sar import CaseData, DataLoader, ExplainabilityLogger, HumanDecision
from src.sar_workflow import SARWorkflow, WorkflowError

WORKFLOW_KEY = "workflow"
REVIEWER_KEY = "reviewer_id"
AUDIT_FILE = config.LOG_DIR / "sar_audit.jsonl"
OUTPUT_DIR = config.PROJECT_ROOT / "outputs"


# Phase 5 – Step 5.1: one audit logger per server — the agents, gates and workflow all write to it
@st.cache_resource
def get_logger() -> ExplainabilityLogger:
    return ExplainabilityLogger(AUDIT_FILE)


# Phase 5 – Step 5.1: the 150 cases, loaded and validated once per server
@st.cache_resource
def load_cases() -> dict[str, CaseData]:
    return {c.case_id: c for c in DataLoader(get_logger()).build_all_cases()}


# Phase 5 – Step 5.1: one OpenAI-compatible client per server (OPENAI_BASE_URL from .env)
@st.cache_resource
def get_client() -> Any:
    from src.llm_client import get_llm_client

    return get_llm_client()


# Phase 5 – Step 5.1: this session's workflow — injected (tests) or built with the real agents
def get_workflow() -> SARWorkflow:
    if WORKFLOW_KEY not in st.session_state:
        from src.compliance_officer_agent import ComplianceOfficerAgent
        from src.risk_analyst_agent import RiskAnalystAgent

        logger, client = get_logger(), get_client()
        workflow = SARWorkflow(
            RiskAnalystAgent(client, logger),
            ComplianceOfficerAgent(client, logger),
            logger,
            output_dir=OUTPUT_DIR,
        )
        for case in load_cases().values():
            workflow.add_case(case)
        st.session_state[WORKFLOW_KEY] = workflow
    return st.session_state[WORKFLOW_KEY]


# Phase 5 – Step 5.2: one-shot messages that survive the st.rerun() after an action
FLASH_KEY = "flash_messages"


def flash(kind: str, message: str) -> None:
    """Queue a message (`kind` ∈ success / info / warning / error) for the next run."""
    st.session_state.setdefault(FLASH_KEY, []).append((kind, message))


def show_flash() -> None:
    for kind, message in st.session_state.pop(FLASH_KEY, []):
        getattr(st, kind)(message)


# Phase 5 – Step 5.3: turn a submitted decision form into a HumanDecision and record it.
# Shared by both gates; every refusal becomes a readable message instead of a traceback.
def record_decision(gate: str, case_id: str, choice: str, reason: str) -> bool:
    reviewer = reviewer_id()
    if reviewer is None:
        st.error("Enter your reviewer ID in the sidebar before recording a decision.")
        return False
    try:
        decision = HumanDecision(
            gate=gate, decision=choice, reviewer_id=reviewer, reason=reason or None
        )
    except ValidationError as exc:
        st.error("; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors()))
        return False
    workflow = get_workflow()
    record = workflow.get(case_id)
    try:
        if gate == "risk":
            workflow.record_risk_decision(case_id, decision)
        else:
            workflow.record_compliance_decision(case_id, decision)
    except WorkflowError as exc:
        st.error(str(exc))
        return False
    flash("success", f"{case_id}: {choice} recorded by {reviewer} → {record.status}")
    return True


# Phase 5 – Step 5.1: the reviewer ID typed in the sidebar; None means decisions are blocked
def reviewer_id() -> str | None:
    value = (st.session_state.get(REVIEWER_KEY) or "").strip()
    return value or None
