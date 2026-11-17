# Phase 5 – Step 5.1: Streamlit UI tests — headless AppTest with stub agents (no browser, no API)
"""`AppTest` runs the real app script without a browser. A workflow with stub agents is put into
session state before the first run, so `app.state.get_workflow()` uses it instead of building the
real agents. Step 5.7 extends this file with tests for every screen.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.foundation_sar import ExplainabilityLogger
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


# Phase 5 – Step 5.1: the app with a stub workflow holding the conftest sample case
@pytest.fixture
def app(tmp_path, sample_case) -> AppTest:
    logger = ExplainabilityLogger(tmp_path / "audit.jsonl")
    workflow = SARWorkflow(StubRiskAgent(), StubComplianceAgent(), logger,
                           output_dir=tmp_path / "outputs")
    workflow.add_case(sample_case)
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["workflow"] = workflow
    return at


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
