# Phase 4 – Step 4.2: workflow tests — state machine, stage runs, manual-review routing
"""Step 4.2 tests use tiny stub agents, so they test the *workflow* rules only (order, guards,
routing, stats). Step 4.6 adds end-to-end tests with the real agents on FakeLLMClient.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.compliance_officer_agent import ComplianceError, ComplianceOfficerAgent
from src.foundation_sar import (
    AccountData,
    CaseData,
    ComplianceOfficerOutput,
    CustomerData,
    ExplainabilityLogger,
    HumanDecision,
    RiskAnalystOutput,
    SARDocument,
    TransactionData,
)
from src.risk_analyst_agent import RiskAnalysisError, RiskAnalystAgent
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
    build_sar,
    involved_transactions,
)
from tests.fakes import FakeLLMClient, make_response, risk_json, tool_call_response

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

    # Phase 4 – Step 4.4: `trace` mimics NarrativeRun.trace (tool calls with masked refs)
    def __init__(self, result=NARRATIVE, error: str | None = None, trace=None) -> None:
        self.result, self.error, self.calls = result, error, 0
        self.trace = trace or []

    def generate_compliance_narrative(self, case, risk_output):
        self.calls += 1
        self.received_risk = risk_output
        self.last_run = SimpleNamespace(
            model="fake-model", token_usage={"prompt_tokens": 5000, "completion_tokens": 200,
                                             "total_tokens": 5200},
            latency_ms=20000.0, attempts=5, steps=5, trace=self.trace,
        )
        if self.error:
            raise ComplianceError(case.case_id, self.error, 8)
        return self.result


@pytest.fixture
def audit(tmp_path):
    path = tmp_path / "audit.jsonl"
    return ExplainabilityLogger(path), path


# Phase 4 – Step 4.4: SARs go to a temp folder next to the audit log, never the real outputs/
def _workflow(audit, risk=None, compliance=None):
    return SARWorkflow(
        risk or StubRiskAgent(), compliance or StubComplianceAgent(), audit[0],
        output_dir=audit[1].parent / "outputs",
    )


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
# Phase 5 – Step 5.1: default reviewers differ per gate (segregation of duties)
def _decision(gate, decision="approve", reason=None, reviewer=None):
    reviewer = reviewer or ("analyst_1" if gate == "risk" else "officer_1")
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


# Phase 5 – Step 5.1: segregation of duties — the risk reviewer can't also decide compliance
@pytest.mark.parametrize("same_id", ["analyst_1", "ANALYST_1"], ids=["exact", "case-insensitive"])
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_segregation_of_duties(at_risk_gate, audit, same_id, decision):
    wf, _, cid = at_risk_gate
    wf.record_risk_decision(cid, _decision("risk", reviewer="analyst_1"))
    wf.run_compliance(cid)
    events_before = len(_records(audit[1]))
    reason = "unclear" if decision == "reject" else None
    with pytest.raises(WorkflowError, match="segregation of duties"):
        wf.record_compliance_decision(cid, _decision("compliance", decision, reason, same_id))
    rec = wf.get(cid)
    assert rec.status == CaseStatus.COMPLIANCE_REVIEW and rec.compliance_decision is None
    assert len(_records(audit[1])) == events_before  # refused: nothing logged
    # a different reviewer can decide
    wf.record_compliance_decision(cid, _decision("compliance", reviewer="officer_1"))
    assert wf.get(cid).status == CaseStatus.SAR_FILED


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
    event = next(e for e in _records(audit[1]) if e["action"] == "compliance_gate_decision")
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
        ("CUST_0002", "sar_generated"),  # Phase 4 – Step 4.4
        ("CUST_0003", "risk_gate_decision"),
    ]
    # Phase 4 – Step 4.5: metrics straight from the workflow
    m = wf.metrics()
    assert m.status_counts == {"CLOSED_RISK_REJECTED": 1, "SAR_FILED": 1}
    assert (m.risk.llm_runs, m.compliance.llm_runs, m.risk_gate.rejected) == (2, 1, 1)
    assert m.compliance.total_tokens == 5200
    assert m.cost_avoided_usd is None  # "fake-model" has no price: tokens counted, $ not guessed


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


# ===========================================================================
# Phase 4 – Step 4.4: SAR document generation
# ===========================================================================
TXN_IDS = ("STRUCT_7D3B8E94", "STRUCT_1A2B3C4D", "STRUCT_5E6F7A8B")


# Phase 4 – Step 4.4: three cash deposits; T001..T003 map to TXN_IDS in date order
@pytest.fixture
def three_txn_case(sample_case, transaction_row) -> CaseData:
    days = [("2025-03-03", "9800.00"), ("2025-03-05", "9500.00"), ("2025-03-07", "9900.00")]
    txns = [
        TransactionData(**{**transaction_row, "transaction_id": tid, "transaction_date": day,
                           "amount": amount, "transaction_type": "Cash_Deposit",
                           "method": "Cash"})
        for tid, (day, amount) in zip(TXN_IDS, days, strict=True)
    ]
    return sample_case.model_copy(update={"transactions": txns})


# Phase 4 – Step 4.4: drive one case through both approvals
def _file(audit, case, risk=RISK, compliance=None):
    wf = _workflow(audit, risk=StubRiskAgent(result=risk), compliance=compliance)
    wf.add_case(case)
    wf.run_risk_analysis(case.case_id)
    wf.record_risk_decision(case.case_id, _decision("risk"))
    wf.run_compliance(case.case_id)
    return wf, wf.record_compliance_decision(
        case.case_id, _decision("compliance", reviewer="officer_1")
    )


# Phase 4 – Step 4.4: approval writes a valid SAR file and a sar_generated audit event
def test_sar_written_on_approval(audit, sample_case):
    _, rec = _file(audit, sample_case)
    path = Path(rec.sar_path)
    assert path.parent == audit[1].parent / "outputs" / "sars"
    sar = SARDocument.model_validate_json(path.read_text(encoding="utf-8"))
    assert sar.sar_id == rec.sar_id == path.stem
    assert sar.subject.name == "Renee Blair"
    assert sar.activity.transaction_ids == ["TXN_B24455F3"]
    assert sar.activity.typology == "Structuring"
    assert (sar.risk_review.reviewer_id, sar.compliance_review.reviewer_id) == (
        "analyst_1", "officer_1"
    )
    assert sar.provenance.risk_model == "fake-model"
    assert sar.provenance.compliance_prompt_version == "V1"
    event = _records(audit[1])[-1]
    assert event["agent_type"] == "Workflow" and event["action"] == "sar_generated"
    assert event["output"]["sar_id"] == rec.sar_id
    assert event["output"]["path"].endswith(f"{rec.sar_id}.json")


# Phase 4 – Step 4.4: rejection at the compliance gate writes no SAR
def test_no_sar_on_compliance_rejection(audit, sample_case):
    wf = _workflow(audit)
    wf.add_case(sample_case)
    wf.run_risk_analysis(sample_case.case_id)
    wf.record_risk_decision(sample_case.case_id, _decision("risk"))
    wf.run_compliance(sample_case.case_id)
    rec = wf.record_compliance_decision(
        sample_case.case_id, _decision("compliance", "reject", "facts unclear")
    )
    assert rec.sar_path is None
    assert not (audit[1].parent / "outputs").exists()


# Phase 4 – Step 4.4: involved = IDs in the risk finding + refs the agent looked up (unmasked)
def test_involved_transactions_combine_sources(audit, three_txn_case):
    risk = RISK.model_copy(update={"key_indicators": [f"{TXN_IDS[0]} just under $10,000"]})
    trace = [{"step": 1, "action": "get_transaction_details",
              "arguments": {"refs": ["t003", "T999"]}, "observation": {}}]
    _, rec = _file(audit, three_txn_case, risk=risk,
                   compliance=StubComplianceAgent(trace=trace))
    assert rec.looked_up_ids == [TXN_IDS[2]]  # T999 is unknown and ignored
    sar = SARDocument.model_validate_json(Path(rec.sar_path).read_text(encoding="utf-8"))
    assert sar.activity.transaction_ids == [TXN_IDS[0], TXN_IDS[2]]  # not the middle one
    assert (str(sar.activity.start_date), str(sar.activity.end_date)) == (
        "2025-03-03", "2025-03-07"
    )
    assert sar.activity.total_amount == 19700.00


# Phase 4 – Step 4.4: if nothing names a transaction, the SAR covers the whole case
def test_involved_transactions_fallback(audit, three_txn_case):
    risk = RISK.model_copy(update={"key_indicators": ["deposits just under $10,000"],
                                   "reasoning": "no IDs quoted"})
    _, rec = _file(audit, three_txn_case, risk=risk)
    assert [t.transaction_id for t in involved_transactions(rec)] == list(TXN_IDS)


# Phase 4 – Step 4.4: a SAR that can't be built leaves the case at the compliance gate
def test_unbuildable_sar_changes_nothing(audit, sample_case):
    empty = sample_case.model_copy(update={"transactions": []})
    wf = _workflow(audit)
    wf.add_case(empty)
    wf.run_risk_analysis(empty.case_id)
    wf.record_risk_decision(empty.case_id, _decision("risk"))
    wf.run_compliance(empty.case_id)
    events_before = len(_records(audit[1]))
    with pytest.raises(WorkflowError, match="cannot build a SAR"):
        wf.record_compliance_decision(empty.case_id, _decision("compliance"))
    rec = wf.get(empty.case_id)
    assert rec.status == CaseStatus.COMPLIANCE_REVIEW
    assert rec.compliance_decision is None and rec.sar_path is None
    assert len(_records(audit[1])) == events_before


# Phase 4 – Step 4.4: sar_id = customer + UTC generation time
def test_build_sar_id(audit, sample_case):
    wf, rec = _file(audit, sample_case)
    sar = build_sar(rec, generated_at=datetime(2026, 11, 17, 9, 30, 5, tzinfo=UTC))
    assert sar.sar_id == "SAR_CUST_0002_20261117093005"


# ===========================================================================
# Phase 4 – Step 4.6: end-to-end integration — REAL agents on FakeLLMClient
# ===========================================================================
PRICED_MODEL = "gpt-4o-mini-2024-07-18"  # has an entry in metrics.PRICES
E2E_CITATIONS = ["31 U.S.C. 5318(g)", "31 U.S.C. 5324"]
# Passes every Stage 03 check against three_txn_case (same dates, amounts, branch, account)
E2E_NARRATIVE = (
    "Renee Blair, a banker, made three cash deposits into a Money Market account at the "
    "Westside Plaza branch on March 3, 2025, March 5, 2025 and March 7, 2025, totalling "
    "$9,800.00, $9,500.00 and $9,900.00. Each deposit fell just below the $10,000 reporting "
    "threshold, a pattern consistent with structuring to evade Currency Transaction Report "
    "requirements. The activity is inconsistent with the customer's profile."
)


# Phase 4 – Step 4.6: model replies, priced so the metrics can be checked in dollars
def _risk_reply(**overrides):
    fields = {
        "classification": "Structuring",
        "risk_level": "High",
        "confidence_score": 0.9,
        "key_indicators": ["T001 and T002 are cash deposits just under $10,000"],
        "reasoning": "Step 1: profile. Step 2: T001, T002, T003. Step 3: under threshold. "
                     "Step 4: Structuring. Step 5: High.",
    }
    return make_response(risk_json(**{**fields, **overrides}), model=PRICED_MODEL)


def _tool(name, args, call_id):
    response = tool_call_response(name, args, call_id)
    response.model = PRICED_MODEL
    return response


def _react_script(narrative=E2E_NARRATIVE):
    return [
        _tool("get_transaction_details", {"refs": ["T001", "T002", "T003"]}, "c1"),
        _tool("lookup_regulations", {"typology": "Structuring"}, "c2"),
        _tool("submit_narrative", {"narrative": narrative, "citations": E2E_CITATIONS,
                                   "narrative_reasoning": "Facts from T001-T003."}, "c3"),
    ]


# Phase 4 – Step 4.6: both real agents share one audit log, each with its own fake client
def _e2e(audit, risk_script, compliance_script, **compliance_kwargs):
    logger, path = audit
    risk_client, compliance_client = FakeLLMClient(risk_script), FakeLLMClient(compliance_script)
    risk_agent = RiskAnalystAgent(risk_client, logger, model=PRICED_MODEL, sleep=lambda _s: None)
    compliance_agent = ComplianceOfficerAgent(
        compliance_client, logger, model=PRICED_MODEL, sleep=lambda _s: None, **compliance_kwargs
    )
    wf = SARWorkflow(risk_agent, compliance_agent, logger, output_dir=path.parent / "outputs")
    return wf, risk_client, compliance_client


# Phase 4 – Step 4.6: CSV-shaped case → SAR file, with the full audit trail in order
def test_e2e_happy_path(audit, three_txn_case):
    wf, risk_client, compliance_client = _e2e(audit, [_risk_reply()], _react_script())
    [rec] = wf.process([three_txn_case], ScriptedReviewer("analyst_1"),
                       ScriptedReviewer("officer_1"))

    assert rec.status == CaseStatus.SAR_FILED
    assert len(risk_client.calls) == 1 and len(compliance_client.calls) == 3
    sar = SARDocument.model_validate_json(Path(rec.sar_path).read_text(encoding="utf-8"))
    # risk named T001/T002 (unmasked by the risk agent); the ReACT agent looked up T003 too
    assert sar.activity.transaction_ids == list(TXN_IDS)
    assert sar.activity.total_amount == 29200.00
    assert sar.narrative.narrative == E2E_NARRATIVE
    assert sar.provenance.risk_model == PRICED_MODEL
    # the model never saw a real transaction ID in either stage
    sent = json.dumps([c["messages"] for c in risk_client.calls + compliance_client.calls])
    assert not any(tid in sent for tid in TXN_IDS)

    trail = [(e["agent_type"], e["action"]) for e in _records(audit[1])]
    assert trail == [
        ("RiskAnalyst", "analyze_case"),
        ("Human", "risk_gate_decision"),
        ("ComplianceOfficer", "generate_narrative"),
        ("Human", "compliance_gate_decision"),
        ("Workflow", "sar_generated"),
    ]
    assert {e["case_id"] for e in _records(audit[1])} == {three_txn_case.case_id}


# Phase 4 – Step 4.6: the cost optimization, measured — a rejected case makes ZERO Stage 2 calls
def test_e2e_risk_rejection_skips_compliance(audit, three_txn_case):
    low = _risk_reply(classification="Other", risk_level="Low")
    wf, _, compliance_client = _e2e(audit, [low], [])  # empty script: any call would fail
    [rec] = wf.process([three_txn_case], ScriptedReviewer(), ScriptedReviewer())
    assert rec.status == CaseStatus.CLOSED_RISK_REJECTED
    assert compliance_client.calls == []
    assert [e["action"] for e in _records(audit[1])] == ["analyze_case", "risk_gate_decision"]


# Phase 4 – Step 4.6: malformed model output twice → RiskAnalysisError → manual review
def test_e2e_risk_failure_to_manual_review(audit, three_txn_case):
    wf, _, compliance_client = _e2e(audit, ["not json", "still not json"], [])
    [rec] = wf.process([three_txn_case], ScriptedReviewer(), ScriptedReviewer())
    assert rec.status == CaseStatus.MANUAL_REVIEW
    assert compliance_client.calls == []
    assert [(e["agent_type"], e["success"]) for e in _records(audit[1])] == [
        ("RiskAnalyst", False), ("Workflow", False),
    ]


# Phase 4 – Step 4.6: ReACT step budget exhausted → ComplianceError → manual review, no SAR
def test_e2e_compliance_failure_to_manual_review(audit, three_txn_case):
    stuck = [_tool("lookup_regulations", {"typology": "Structuring"}, f"c{i}") for i in range(2)]
    wf, _, _ = _e2e(audit, [_risk_reply()], stuck, max_steps=2)
    [rec] = wf.process([three_txn_case], ScriptedReviewer(), ScriptedReviewer())
    assert rec.status == CaseStatus.MANUAL_REVIEW
    assert "step budget exhausted" in rec.error
    assert rec.sar_path is None
    assert rec.compliance_stats.steps == 2  # spent tokens are still on the record for metrics
    assert [e["action"] for e in _records(audit[1])][-2:] == [
        "generate_narrative", "routed_to_manual_review",
    ]


# Phase 4 – Step 4.6: metrics in dollars from a real (faked) batch — one filed, one rejected
def test_e2e_metrics(audit, three_txn_case, second_case):
    low = _risk_reply(classification="Other", risk_level="Low")
    wf, _, _ = _e2e(audit, [_risk_reply(), low], _react_script())
    wf.process([three_txn_case, second_case], ScriptedReviewer("analyst_1"),
               ScriptedReviewer("officer_1"))
    m = wf.metrics()
    # fake replies: 100 prompt + 20 completion tokens each → $0.000027 per call on gpt-4o-mini
    per_call = 100 / 1e6 * 0.15 + 20 / 1e6 * 0.60
    assert m.risk.cost_usd == pytest.approx(2 * per_call)
    assert m.compliance.cost_usd == pytest.approx(3 * per_call)  # 3 ReACT turns
    assert m.cost_avoided_usd == pytest.approx(3 * per_call)  # the rejected case's narrative
    assert m.savings_rate == pytest.approx(3 / 8)
