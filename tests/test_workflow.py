# Phase 4 – Step 4.2: workflow tests — state machine, stage runs, manual-review routing
"""Step 4.2 tests use tiny stub agents, so they test the *workflow* rules only (order, guards,
routing, stats). Step 4.6 adds end-to-end tests with the real agents on FakeLLMClient.
"""

import json
from types import SimpleNamespace

import pytest

from src.compliance_officer_agent import ComplianceError
from src.foundation_sar import (
    AccountData,
    CaseData,
    ComplianceOfficerOutput,
    CustomerData,
    ExplainabilityLogger,
    HumanDecision,
    RiskAnalystOutput,
    TransactionData,
)
from src.risk_analyst_agent import RiskAnalysisError
from src.sar_workflow import (
    FINAL_STATUSES,
    LOW_RISK_REASON,
    TRANSITIONS,
    CaseStatus,
    ConsoleReviewer,
    SARWorkflow,
    ScriptedReviewer,
    StageStats,
    WorkflowError,
)

RISK = RiskAnalystOutput(
    classification="Structuring", confidence_score=0.9, risk_level="High",
    reasoning="Step 1 ... Step 5", key_indicators=["TXN_B24455F3 just under threshold"],
)
NARRATIVE = ComplianceOfficerOutput(
    narrative="Renee Blair made cash deposits.", narrative_reasoning="who/what/when/where/why",
    regulatory_citations=["31 U.S.C. 5318(g)"], completeness_check=True,
)


# Phase 4 – Step 4.2: stand-in agents — return a result or raise, and expose `last_run`
class StubRiskAgent:
    prompt_version = "V1"

    def __init__(self, result=RISK, error: str | None = None) -> None:
        self.result, self.error, self.calls = result, error, 0

    def analyze_case(self, case):
        self.calls += 1
        self.last_run = SimpleNamespace(
            model="fake-model", token_usage={"prompt_tokens": 900, "completion_tokens": 100,
                                             "total_tokens": 1000},
            latency_ms=8000.0, attempts=1, method="llm",
        )
        if self.error:
            raise RiskAnalysisError(case.case_id, self.error, 3)
        # Phase 4 – Step 4.3: a dict gives a different result per case
        return self.result[case.case_id] if isinstance(self.result, dict) else self.result


class StubComplianceAgent:
    prompt_version = "V1"

    def __init__(self, result=NARRATIVE, error: str | None = None) -> None:
        self.result, self.error, self.calls = result, error, 0

    def generate_compliance_narrative(self, case, risk_output):
        self.calls += 1
        self.received_risk = risk_output
        self.last_run = SimpleNamespace(
            model="fake-model", token_usage={"prompt_tokens": 5000, "completion_tokens": 200,
                                             "total_tokens": 5200},
            latency_ms=20000.0, attempts=5, steps=5,
        )
        if self.error:
            raise ComplianceError(case.case_id, self.error, 8)
        return self.result


@pytest.fixture
def audit(tmp_path):
    path = tmp_path / "audit.jsonl"
    return ExplainabilityLogger(path), path


def _workflow(audit, risk=None, compliance=None):
    return SARWorkflow(risk or StubRiskAgent(), compliance or StubComplianceAgent(), audit[0])


def _records(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# Phase 4 – Step 4.2: the transition table is consistent
def test_transition_table_shape():
    assert FINAL_STATUSES == {
        CaseStatus.SAR_FILED, CaseStatus.CLOSED_RISK_REJECTED,
        CaseStatus.CLOSED_COMPLIANCE_REJECTED, CaseStatus.MANUAL_REVIEW,
    }
    reachable = {s for targets in TRANSITIONS.values() for s in targets}
    assert reachable | {CaseStatus.NEW} == set(CaseStatus)


# Phase 4 – Step 4.2: add / get / duplicates / unknown
def test_add_and_get(audit, sample_case):
    wf = _workflow(audit)
    rec = wf.add_case(sample_case)
    assert rec.status == CaseStatus.NEW and not rec.is_final
    assert wf.get(sample_case.case_id) is rec
    assert rec.history[0][0] == CaseStatus.NEW
    with pytest.raises(WorkflowError, match="already"):
        wf.add_case(sample_case)
    with pytest.raises(WorkflowError, match="unknown case"):
        wf.get("CASE_CUST_9999_20261116120000")


# Phase 4 – Step 4.2: Stage 1 stores the result and stats, then waits at the risk gate
def test_risk_analysis_moves_to_review(audit, sample_case):
    wf = _workflow(audit)
    wf.add_case(sample_case)
    rec = wf.run_risk_analysis(sample_case.case_id)
    assert rec.status == CaseStatus.RISK_REVIEW
    assert rec.risk_output == RISK
    assert rec.risk_stats == StageStats(
        model="fake-model", prompt_version="V1",
        token_usage={"prompt_tokens": 900, "completion_tokens": 100, "total_tokens": 1000},
        latency_ms=8000.0, attempts=1, steps=None, method="llm",
    )
    assert [s for s, _ in rec.history] == [CaseStatus.NEW, CaseStatus.RISK_REVIEW]


# Phase 4 – Step 4.2: THE gate rule — no narrative without risk approval, and no agent call
@pytest.mark.parametrize("analyzed", [False, True], ids=["new", "awaiting-review"])
def test_compliance_blocked_without_approval(audit, sample_case, analyzed):
    compliance = StubComplianceAgent()
    wf = _workflow(audit, compliance=compliance)
    wf.add_case(sample_case)
    if analyzed:
        wf.run_risk_analysis(sample_case.case_id)
    status_before = wf.get(sample_case.case_id).status
    with pytest.raises(WorkflowError, match="needs human approval at the risk gate"):
        wf.run_compliance(sample_case.case_id)
    assert compliance.calls == 0
    assert wf.get(sample_case.case_id).status == status_before  # unchanged


# Phase 4 – Step 4.2: once approved (gate methods arrive in 4.3), Stage 2 runs
def test_compliance_after_approval(audit, sample_case):
    compliance = StubComplianceAgent()
    wf = _workflow(audit, compliance=compliance)
    wf.add_case(sample_case)
    rec = wf.run_risk_analysis(sample_case.case_id)
    rec.status = CaseStatus.RISK_APPROVED  # stand-in for the Step 4.3 risk gate
    rec = wf.run_compliance(sample_case.case_id)
    assert rec.status == CaseStatus.COMPLIANCE_REVIEW
    assert compliance.received_risk is RISK  # the approved finding is handed to Stage 2
    assert rec.compliance_output == NARRATIVE
    assert rec.compliance_stats.steps == 5
    assert rec.compliance_stats.token_usage["total_tokens"] == 5200


# Phase 4 – Step 4.2: running a stage twice is illegal
def test_risk_analysis_twice_rejected(audit, sample_case):
    risk = StubRiskAgent()
    wf = _workflow(audit, risk=risk)
    wf.add_case(sample_case)
    wf.run_risk_analysis(sample_case.case_id)
    with pytest.raises(WorkflowError, match="status is RISK_REVIEW"):
        wf.run_risk_analysis(sample_case.case_id)
    assert risk.calls == 1


# Phase 4 – Step 4.2: agent failure at either stage → MANUAL_REVIEW + one Workflow audit event
def test_risk_failure_routes_to_manual_review(audit, sample_case):
    _, path = audit
    wf = _workflow(audit, risk=StubRiskAgent(error="invalid JSON after repair"))
    wf.add_case(sample_case)
    rec = wf.run_risk_analysis(sample_case.case_id)
    assert rec.status == CaseStatus.MANUAL_REVIEW and rec.is_final
    assert rec.error == "invalid JSON after repair"
    assert rec.risk_stats.token_usage["total_tokens"] == 1000  # tokens spent are still counted
    [event] = _records(path)
    assert event["agent_type"] == "Workflow"
    assert event["action"] == "routed_to_manual_review"
    assert event["success"] is False
    assert event["input_summary"] == {"stage": "risk_analysis", "from_status": "NEW"}


def test_compliance_failure_routes_to_manual_review(audit, sample_case):
    _, path = audit
    wf = _workflow(audit, compliance=StubComplianceAgent(error="step budget exhausted"))
    wf.add_case(sample_case)
    wf.run_risk_analysis(sample_case.case_id).status = CaseStatus.RISK_APPROVED
    rec = wf.run_compliance(sample_case.case_id)
    assert rec.status == CaseStatus.MANUAL_REVIEW
    assert rec.compliance_stats.steps == 5
    [event] = _records(path)
    assert event["input_summary"]["from_status"] == "RISK_APPROVED"
    assert "step budget exhausted" in event["error_message"]


# Phase 4 – Step 4.2: a final case cannot move again
def test_final_case_cannot_move(audit, sample_case):
    wf = _workflow(audit, risk=StubRiskAgent(error="boom"))
    wf.add_case(sample_case)
    wf.run_risk_analysis(sample_case.case_id)
    with pytest.raises(WorkflowError):
        wf.run_compliance(sample_case.case_id)
    with pytest.raises(WorkflowError, match="illegal move"):
        wf._move(wf.get(sample_case.case_id), CaseStatus.RISK_REVIEW)


# Phase 4 – Step 4.2: stats tolerate a missing run (agent crashed before creating one)
def test_stage_stats_from_missing_run():
    assert StageStats.from_run(None, "V1") == StageStats(prompt_version="V1")


# ===========================================================================
# Phase 4 – Step 4.3: human decision gates and reviewer policies
# ===========================================================================
def _decision(gate, decision="approve", reason=None, reviewer="analyst_1"):
    return HumanDecision(gate=gate, decision=decision, reviewer_id=reviewer, reason=reason)


# Phase 4 – Step 4.3: a case waiting at the risk gate
@pytest.fixture
def at_risk_gate(audit, sample_case):
    compliance = StubComplianceAgent()
    wf = _workflow(audit, compliance=compliance)
    wf.add_case(sample_case)
    wf.run_risk_analysis(sample_case.case_id)
    return wf, compliance, sample_case.case_id


# Phase 4 – Step 4.3: approval moves on and writes one Human audit event
def test_risk_gate_approve(at_risk_gate, audit):
    wf, _, cid = at_risk_gate
    rec = wf.record_risk_decision(cid, _decision("risk"))
    assert rec.status == CaseStatus.RISK_APPROVED
    assert rec.risk_decision.reviewer_id == "analyst_1"
    [event] = _records(audit[1])
    assert event["agent_type"] == "Human"
    assert event["action"] == "risk_gate_decision"
    assert event["decision"] == "approve" and event["reviewer_id"] == "analyst_1"
    assert event["input_summary"] == {
        "classification": "Structuring", "risk_level": "High", "confidence": 0.9
    }
    assert event["output"] == {"decision": "approve", "new_status": "RISK_APPROVED"}


# Phase 4 – Step 4.3: rejection closes the case, is audited with its reason, blocks Stage 2
def test_risk_gate_reject(at_risk_gate, audit):
    wf, compliance, cid = at_risk_gate
    rec = wf.record_risk_decision(cid, _decision("risk", "reject", "false positive: payroll"))
    assert rec.status == CaseStatus.CLOSED_RISK_REJECTED and rec.is_final
    [event] = _records(audit[1])
    assert event["reasoning"] == "false positive: payroll"
    with pytest.raises(WorkflowError):
        wf.run_compliance(cid)
    assert compliance.calls == 0


# Phase 4 – Step 4.3: wrong gate, wrong time, or deciding twice → error, nothing changes
def test_gate_guards(at_risk_gate, audit):
    wf, _, cid = at_risk_gate
    with pytest.raises(WorkflowError, match="cannot be recorded at the risk gate"):
        wf.record_risk_decision(cid, _decision("compliance"))
    with pytest.raises(WorkflowError, match="expected COMPLIANCE_REVIEW"):
        wf.record_compliance_decision(cid, _decision("compliance"))
    assert wf.get(cid).status == CaseStatus.RISK_REVIEW
    assert _records(audit[1]) == []  # no event for refused decisions
    wf.record_risk_decision(cid, _decision("risk"))
    with pytest.raises(WorkflowError, match="status is RISK_APPROVED"):
        wf.record_risk_decision(cid, _decision("risk"))


def test_decision_before_analysis_rejected(audit, sample_case):
    wf = _workflow(audit)
    wf.add_case(sample_case)
    with pytest.raises(WorkflowError, match="status is NEW"):
        wf.record_risk_decision(sample_case.case_id, _decision("risk"))


# Phase 4 – Step 4.3: compliance gate outcomes
@pytest.mark.parametrize(
    ("decision", "reason", "status"),
    [
        ("approve", None, CaseStatus.SAR_FILED),
        ("reject", "narrative omits the branch", CaseStatus.CLOSED_COMPLIANCE_REJECTED),
    ],
)
def test_compliance_gate(at_risk_gate, audit, decision, reason, status):
    wf, _, cid = at_risk_gate
    wf.record_risk_decision(cid, _decision("risk"))
    wf.run_compliance(cid)
    rec = wf.record_compliance_decision(
        cid, _decision("compliance", decision, reason, "officer_1")
    )
    assert rec.status == status and rec.is_final
    event = _records(audit[1])[-1]
    assert event["action"] == "compliance_gate_decision"
    assert event["reviewer_id"] == "officer_1"
    assert event["input_summary"]["citations"] == ["31 U.S.C. 5318(g)"]


# Phase 4 – Step 4.3: a second customer, so process() can handle a batch
@pytest.fixture
def second_case(customer_row, account_row, transaction_row, created_at) -> CaseData:
    return CaseData(
        case_id="CASE_CUST_0003_20261116120000",
        customer=CustomerData(**{**customer_row, "customer_id": "CUST_0003", "name": "Al Ng"}),
        accounts=[AccountData(**{**account_row, "customer_id": "CUST_0003",
                                 "account_id": "CUST_0003_ACC_1"})],
        transactions=[TransactionData(**{**transaction_row, "account_id": "CUST_0003_ACC_1"})],
        case_created_at=created_at,
    )


LOW = RISK.model_copy(update={"risk_level": "Low", "classification": "Other"})


# Phase 4 – Step 4.3: Stage 2 only runs for the case the scripted reviewer approved
def test_process_runs_only_approved_cases(audit, sample_case, second_case):
    risk = StubRiskAgent(result={sample_case.case_id: RISK, second_case.case_id: LOW})
    compliance = StubComplianceAgent()
    wf = _workflow(audit, risk=risk, compliance=compliance)
    high, low = wf.process(
        [sample_case, second_case], ScriptedReviewer("analyst_1"), ScriptedReviewer("officer_1")
    )
    assert high.status == CaseStatus.SAR_FILED
    assert low.status == CaseStatus.CLOSED_RISK_REJECTED
    assert low.risk_decision.reason == LOW_RISK_REASON
    assert risk.calls == 2 and compliance.calls == 1  # the cost optimization, in one line
    actions = [(e["case_id"][5:14], e["action"]) for e in _records(audit[1])]
    assert actions == [
        ("CUST_0002", "risk_gate_decision"),
        ("CUST_0002", "compliance_gate_decision"),
        ("CUST_0003", "risk_gate_decision"),
    ]


# Phase 4 – Step 4.3: failed cases skip the gates entirely
def test_process_failure_skips_reviewers(audit, sample_case):
    class NeverAsked:
        def review(self, record, gate):
            raise AssertionError("reviewer must not be asked about a failed case")

    wf = _workflow(audit, risk=StubRiskAgent(error="timeout"))
    [rec] = wf.process([sample_case], NeverAsked(), NeverAsked())
    assert rec.status == CaseStatus.MANUAL_REVIEW


# Phase 4 – Step 4.3: fixed answers override the default policy
def test_scripted_reviewer_answers(at_risk_gate):
    wf, _, cid = at_risk_gate
    reviewer = ScriptedReviewer(answers={(cid, "risk"): ("reject", "known customer")})
    d = reviewer.review(wf.get(cid), "risk")
    assert (d.decision, d.reason, d.reviewer_id) == (
        "reject", "known customer", "scripted_reviewer"
    )
    assert ScriptedReviewer().review(wf.get(cid), "risk").decision == "approve"  # High


# Phase 4 – Step 4.3: the console reviewer re-asks until answers are valid
def test_console_reviewer(at_risk_gate):
    wf, _, cid = at_risk_gate
    answers, printed = iter(["maybe", "r", "  ", "insufficient evidence"]), []
    reviewer = ConsoleReviewer(
        "me",
        input_fn=lambda _prompt: next(answers),
        print_fn=lambda *a: printed.append(" ".join(map(str, a))),
    )
    d = reviewer.review(wf.get(cid), "risk")
    assert (d.decision, d.reason, d.reviewer_id) == ("reject", "insufficient evidence", "me")
    assert any("RISK GATE" in p for p in printed)
    assert any("Please type" in p for p in printed)
    approve = ConsoleReviewer("me", input_fn=lambda _prompt: "a", print_fn=lambda *a: None)
    assert approve.review(wf.get(cid), "risk").decision == "approve"
