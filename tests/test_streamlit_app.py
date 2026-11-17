# Phase 5 – Step 5.1: Streamlit UI tests — headless AppTest with stub agents (no browser, no API)
"""`AppTest` runs the real app script without a browser. A workflow with stub agents is put into
session state before the first run, so `app.state.get_workflow()` uses it instead of building the
real agents. Step 5.7 extends this file with tests for every screen.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.foundation_sar import (
    ComplianceOfficerOutput,
    ExplainabilityLogger,
    HumanDecision,
    count_words,
)
from src.sar_workflow import SARWorkflow
from tests.test_workflow import StubComplianceAgent, StubRiskAgent

# AppTest resolves relative paths against this test file, so use an absolute path
APP = str(Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py")
PAGES = [
    "views/queue.py",
    "views/risk_review.py",
    "views/compliance_review.py",
    "views/sars_audit.py",
    "views/metrics.py",
]


# Phase 5 – Step 5.2: three customers (CUST_0002..0004) built from the conftest sample case
@pytest.fixture
def cases(sample_case):
    out = []
    for n in (2, 3, 4):
        cid = f"CUST_{n:04d}"
        customer = sample_case.customer.model_copy(update={"customer_id": cid})
        account = sample_case.accounts[0].model_copy(
            update={"customer_id": cid, "account_id": f"{cid}_ACC_1"}
        )
        txns = [t.model_copy(update={"account_id": f"{cid}_ACC_1"})
                for t in sample_case.transactions]
        out.append(sample_case.model_copy(update={
            "case_id": f"CASE_{cid}_20261116120000", "customer": customer,
            "accounts": [account], "transactions": txns,
        }))
    return out


# Phase 5 – Step 5.1: build an AppTest around a stub workflow (no browser, no API)
# Phase 5 – Step 5.4: `compliance` lets a test choose the narrative stub
def _app(tmp_path, cases, risk=None, compliance=None) -> tuple[AppTest, SARWorkflow]:
    logger = ExplainabilityLogger(tmp_path / "audit.jsonl")
    workflow = SARWorkflow(risk or StubRiskAgent(), compliance or StubComplianceAgent(), logger,
                           output_dir=tmp_path / "outputs")
    for case in cases:
        workflow.add_case(case)
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["workflow"] = workflow
    return at, workflow


@pytest.fixture
def app(tmp_path, cases) -> AppTest:
    return _app(tmp_path, cases)[0]


# Phase 5 – Step 5.1: the shell renders: sidebar, reviewer warning, models, session cost
def test_app_starts(app):
    app.run()
    assert not app.exception
    assert app.sidebar.text_input[0].label == "Reviewer ID"
    assert any("Enter a reviewer ID" in w.value for w in app.sidebar.warning)
    assert app.sidebar.metric[0].value == "$0.0000"
    assert app.title[0].value == "Case queue"  # default page


# Phase 5 – Step 5.1: a reviewer ID clears the warning
def test_reviewer_id_entered(app):
    app.run()
    app.sidebar.text_input[0].input("analyst_jane").run()
    assert not app.sidebar.warning


# Phase 5 – Step 5.1: every page opens without an exception
@pytest.mark.parametrize("page", PAGES)
def test_every_page_opens(app, page):
    app.run()
    app.switch_page(page).run()
    assert not app.exception, app.exception


# ===========================================================================
# Phase 5 – Step 5.2: case queue
# ===========================================================================
def _table(at):
    return at.dataframe[0].value


# Phase 5 – Step 5.2: all cases listed as NEW, with tiles and no answer-key column
def test_queue_lists_cases(app):
    app.run()
    df = _table(app)
    assert list(df["customer"]) == ["CUST_0002", "CUST_0003", "CUST_0004"]
    assert set(df["status"]) == {"NEW"}
    assert "transaction_id" not in df.columns
    assert app.metric[0].label == "New" and app.metric[0].value == "3"


# Phase 5 – Step 5.2: analyze one selected case → RISK_REVIEW, success message after rerun
def test_analyze_selected_case(tmp_path, cases):
    at, wf = _app(tmp_path, cases)
    at.run()
    at.selectbox(key="analyze_case").select(cases[1].case_id)
    at.button(key="analyze_one").click().run()
    assert not at.exception
    assert wf.get(cases[1].case_id).status == "RISK_REVIEW"
    assert wf.get(cases[0].case_id).status == "NEW"
    assert any("Structuring / High" in s.value for s in at.success)
    assert list(_table(at)["status"]) == ["NEW", "RISK_REVIEW", "NEW"]


# Phase 5 – Step 5.2: analyze next N → the first N new cases, in order
def test_analyze_next_n(tmp_path, cases):
    at, wf = _app(tmp_path, cases)
    at.run()
    at.number_input(key="batch_n").set_value(2).run()
    # stub model is unpriced → Stage 04 default of $0.0008 per active case
    assert any("2 case(s) · estimated cost ≈ $0.0016" in c.value for c in at.caption)
    at.button(key="analyze_batch").click().run()
    assert [wf.get(c.case_id).status for c in cases] == ["RISK_REVIEW", "RISK_REVIEW", "NEW"]
    assert len(at.success) == 2


# Phase 5 – Step 5.2: a failing agent is reported as manual review, not a crash
def test_analyze_failure_shows_error(tmp_path, cases):
    at, wf = _app(tmp_path, cases, risk=StubRiskAgent(error="model timeout"))
    at.run()
    at.button(key="analyze_one").click().run()
    assert not at.exception
    assert wf.get(cases[0].case_id).status == "MANUAL_REVIEW"
    assert any("manual review" in e.value for e in at.error)


# Phase 5 – Step 5.2: the status filter narrows the table
def test_status_filter(tmp_path, cases):
    at, wf = _app(tmp_path, cases)
    wf.run_risk_analysis(cases[0].case_id)
    at.run()
    at.multiselect(key="status_filter").select("RISK_REVIEW").run()
    assert list(_table(at)["customer"]) == ["CUST_0002"]


# Phase 5 – Step 5.2: nothing left to analyze → info message, batch button disabled
def test_no_new_cases(tmp_path, cases):
    at, wf = _app(tmp_path, cases[:1])
    wf.run_risk_analysis(cases[0].case_id)
    at.run()
    assert any("No new cases" in i.value for i in at.info)
    assert at.button(key="analyze_batch").disabled


# ===========================================================================
# Phase 5 – Step 5.3: risk review screen
# ===========================================================================
# Phase 5 – Step 5.3: open the risk review page with the first case analysed
def _risk_page(tmp_path, cases, reviewer="analyst_1"):
    at, wf = _app(tmp_path, cases)
    wf.run_risk_analysis(cases[0].case_id)
    if reviewer:
        at.session_state["reviewer_id"] = reviewer
    at.run()
    at.switch_page("views/risk_review.py").run()
    return at, wf


def _submit_risk(at, choice, reason=""):
    at.radio(key="risk_choice").set_value(choice)
    at.text_area(key="risk_reason").input(reason)
    at.button(key="risk_submit").click().run()


def test_risk_review_empty(app):
    app.run()
    app.switch_page("views/risk_review.py").run()
    assert any("No cases are waiting" in i.value for i in app.info)


# Phase 5 – Step 5.3: the finding and evidence render with masked refs and cited rows
def test_risk_review_shows_evidence(tmp_path, cases):
    at, _ = _risk_page(tmp_path, cases)
    assert not at.exception
    assert [m.value for m in at.main.metric] == ["Structuring", "High", "90%"]  # main area only
    text = " ".join(m.value for m in at.markdown)
    assert "T001 just under threshold" in text
    assert "TXN_B24455F3" not in text  # the real ID never reaches the screen
    tx = at.dataframe[-1].value
    assert list(tx["ref"]) == ["T001"] and list(tx["cited"]) == [True]


# Phase 5 – Step 5.3: approve → RISK_APPROVED, success after rerun, case leaves the queue
def test_risk_approve(tmp_path, cases):
    at, wf = _risk_page(tmp_path, cases)
    _submit_risk(at, "approve")
    assert wf.get(cases[0].case_id).status == "RISK_APPROVED"
    assert wf.get(cases[0].case_id).risk_decision.reviewer_id == "analyst_1"
    assert any("approve recorded by analyst_1" in s.value for s in at.success)
    assert any("No cases are waiting" in i.value for i in at.info)


# Phase 5 – Step 5.3: reject with a reason → closed
def test_risk_reject_with_reason(tmp_path, cases):
    at, wf = _risk_page(tmp_path, cases)
    _submit_risk(at, "reject", "payroll transfer, known employer")
    rec = wf.get(cases[0].case_id)
    assert rec.status == "CLOSED_RISK_REJECTED"
    assert rec.risk_decision.reason == "payroll transfer, known employer"


# Phase 5 – Step 5.3: refusals are messages, and nothing changes
@pytest.mark.parametrize(
    ("reviewer", "choice", "message"),
    [
        (None, "approve", "Enter your reviewer ID"),
        ("analyst_1", "reject", "a rejection must include a reason"),
    ],
    ids=["no-reviewer", "reject-without-reason"],
)
def test_risk_decision_refused(tmp_path, cases, reviewer, choice, message):
    at, wf = _risk_page(tmp_path, cases, reviewer=reviewer)
    _submit_risk(at, choice)
    assert not at.exception
    assert any(message in e.value for e in at.error)
    assert wf.get(cases[0].case_id).status == "RISK_REVIEW"


# ===========================================================================
# Phase 5 – Step 5.4: compliance review screen
# ===========================================================================
# Phase 5 – Step 5.4: passes every Stage 03 check for the conftest transaction (Feb 18, 2025)
GOOD_NARRATIVE = ComplianceOfficerOutput(
    narrative=(
        "Renee Blair made an online transfer of $1,615.06 from a Money Market account at the "
        "Westside Plaza branch on February 18, 2025. The transfer is inconsistent with the "
        "customer's profile and forms part of an unusual pattern."
    ),
    narrative_reasoning="Built from TXN_B24455F3: who, what, when, where and why.",
    regulatory_citations=["31 U.S.C. 5318(g)", "31 U.S.C. 5324"],
    completeness_check=True,
)
TRACE = [
    {"step": 1, "thought": None, "action": "get_transaction_details",
     "arguments": {"refs": ["T001"]}, "observation": {"transactions": [{"ref": "T001"}]}},
    {"step": 2, "thought": None, "action": "submit_narrative",
     "arguments": {"narrative": GOOD_NARRATIVE.narrative, "citations": ["31 U.S.C. 5324"]},
     "observation": {"accepted": True, "warnings": []}},
]


# Phase 5 – Step 5.4: first case approved at the risk gate by analyst_1; optionally narrated
def _compliance_page(tmp_path, cases, narrate=True, reviewer="officer_1", compliance=None):
    at, wf = _app(tmp_path, cases,
                  compliance=compliance or StubComplianceAgent(result=GOOD_NARRATIVE, trace=TRACE))
    cid = cases[0].case_id
    wf.run_risk_analysis(cid)
    wf.record_risk_decision(cid, HumanDecision(gate="risk", decision="approve",
                                               reviewer_id="analyst_1"))
    if narrate:
        wf.run_compliance(cid)
    at.session_state["reviewer_id"] = reviewer
    at.run()
    at.switch_page("views/compliance_review.py").run()
    return at, wf


def _submit_compliance(at, choice, reason=""):
    at.radio(key="compliance_choice").set_value(choice)
    at.text_area(key="compliance_reason").input(reason)
    at.button(key="compliance_submit").click().run()


def test_compliance_page_empty(app):
    app.run()
    app.switch_page("views/compliance_review.py").run()
    assert not app.exception
    captions = " ".join(c.value for c in app.caption)
    assert "No cases approved" in captions and "No narratives waiting" in captions
    assert "No agent failures" in captions


# Phase 5 – Step 5.4: generate a narrative for a risk-approved case
def test_generate_narrative(tmp_path, cases):
    at, wf = _compliance_page(tmp_path, cases, narrate=False)
    at.button(key="narrate").click().run()
    assert not at.exception
    assert wf.get(cases[0].case_id).status == "COMPLIANCE_REVIEW"
    assert any("narrative ready after 5 ReACT step(s)" in s.value for s in at.success)


# Phase 5 – Step 5.4: the narrative, checks, citations and trace are shown for review
def test_compliance_review_shows_checks(tmp_path, cases):
    at, wf = _compliance_page(tmp_path, cases)
    assert not at.exception
    words = count_words(GOOD_NARRATIVE.narrative)
    assert [m.value for m in at.main.metric] == [f"{words} / <120", "passed", "0"]
    text = " ".join(m.value for m in at.markdown)
    assert "Renee Blair made an online transfer" in text
    assert "✅ who" in text and "❌" not in text
    assert "T001" in text and "TXN_B24455F3" not in text  # reasoning shown with masked refs
    assert any("approved by **analyst_1**" in c.value for c in at.caption)
    trace = at.dataframe[0].value
    assert list(trace["action"]) == ["get_transaction_details", "submit_narrative"]
    assert list(trace["observation"]) == ["1 found", "accepted"]


# Phase 5 – Step 5.4: a different reviewer approves → SAR filed
def test_compliance_approve_files_sar(tmp_path, cases):
    at, wf = _compliance_page(tmp_path, cases)
    _submit_compliance(at, "approve")
    rec = wf.get(cases[0].case_id)
    assert rec.status == "SAR_FILED" and Path(rec.sar_path).exists()
    assert any("approve recorded by officer_1" in s.value for s in at.success)


# Phase 5 – Step 5.4: the risk reviewer cannot also decide here (segregation of duties)
def test_compliance_same_reviewer_refused(tmp_path, cases):
    at, wf = _compliance_page(tmp_path, cases, reviewer="analyst_1")
    _submit_compliance(at, "approve")
    assert not at.exception
    assert any("segregation of duties" in e.value for e in at.error)
    assert wf.get(cases[0].case_id).status == "COMPLIANCE_REVIEW"


def test_compliance_reject_with_reason(tmp_path, cases):
    at, wf = _compliance_page(tmp_path, cases)
    _submit_compliance(at, "reject", "narrative overstates the pattern")
    rec = wf.get(cases[0].case_id)
    assert rec.status == "CLOSED_COMPLIANCE_REJECTED" and rec.sar_path is None


# Phase 5 – Step 5.4: an agent failure is listed under manual review
def test_manual_review_listed(tmp_path, cases):
    at, wf = _compliance_page(tmp_path, cases, narrate=False,
                              compliance=StubComplianceAgent(error="step budget exhausted"))
    at.button(key="narrate").click().run()
    assert wf.get(cases[0].case_id).status == "MANUAL_REVIEW"
    assert any("manual review" in e.value for e in at.error)
    manual = at.dataframe[-1].value
    assert list(manual["failed_stage"]) == ["compliance"]
    assert "step budget exhausted" in manual["error"][0]


# ===========================================================================
# Phase 5 – Step 5.5: SARs & audit views
# ===========================================================================
def _sars_page(tmp_path, cases, file_sar=True):
    at, wf = _app(tmp_path, cases,
                  compliance=StubComplianceAgent(result=GOOD_NARRATIVE, trace=TRACE))
    cid = cases[0].case_id
    wf.run_risk_analysis(cid)
    wf.record_risk_decision(cid, HumanDecision(gate="risk", decision="approve",
                                               reviewer_id="analyst_1"))
    wf.run_compliance(cid)
    if file_sar:
        wf.record_compliance_decision(cid, HumanDecision(gate="compliance", decision="approve",
                                                         reviewer_id="officer_1"))
    at.run()
    at.switch_page("views/sars_audit.py").run()
    return at, wf


def test_sars_page_empty(app):
    app.run()
    app.switch_page("views/sars_audit.py").run()
    assert not app.exception
    captions = " ".join(c.value for c in app.caption)
    assert "No SARs filed" in captions and "No case has been processed" in captions


# Phase 5 – Step 5.5: the filed SAR is rendered by section with a JSON download
def test_sar_rendered_with_download(tmp_path, cases):
    at, wf = _sars_page(tmp_path, cases)
    assert not at.exception
    rec = wf.get(cases[0].case_id)
    text = " ".join(m.value for m in at.markdown)
    assert "**Subject:** Renee Blair · Banker · CUST_0002" in text
    assert "Risk gate: approve by analyst_1" in text
    assert "Compliance gate: approve by officer_1" in text
    assert "1978-05-14" not in text  # DOB stays in the file, off the screen
    download = at.get("download_button")[0]
    assert download.proto.label == "Download SAR (JSON)"
    assert rec.sar_id in at.code[0].value  # raw JSON expander


# Phase 5 – Step 5.5: the audit trail lists every event for the case, in order
def test_audit_trail(tmp_path, cases):
    at, _ = _sars_page(tmp_path, cases)
    trail = at.dataframe[-1].value
    assert list(trail["action"]) == [
        "risk_gate_decision", "compliance_gate_decision", "sar_generated",
    ]  # stub agents write no events of their own; real agents add theirs (Step 4.6)
    assert list(trail["reviewer"]) == ["analyst_1", "officer_1", ""]


# Phase 5 – Step 5.5: a case awaiting review has an audit trail but no SAR yet
def test_audit_without_sar(tmp_path, cases):
    at, _ = _sars_page(tmp_path, cases, file_sar=False)
    assert any("No SARs filed" in c.value for c in at.caption)
    assert list(at.dataframe[-1].value["action"]) == ["risk_gate_decision"]
