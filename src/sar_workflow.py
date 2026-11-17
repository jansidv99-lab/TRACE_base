# Phase 4 – Step 4.2: two-stage SAR workflow — orchestrates both agents and enforces the gates
"""SAR workflow.

One `CaseRecord` per case moves through a small state machine:

    NEW
     ├─ run_risk_analysis ──► RISK_REVIEW            (or MANUAL_REVIEW if the agent fails)
    RISK_REVIEW
     ├─ risk gate: approve ─► RISK_APPROVED
     └─ risk gate: reject ──► CLOSED_RISK_REJECTED
    RISK_APPROVED
     ├─ run_compliance ─────► COMPLIANCE_REVIEW      (or MANUAL_REVIEW if the agent fails)
    COMPLIANCE_REVIEW
     ├─ compliance gate: approve ─► SAR_FILED
     └─ compliance gate: reject ──► CLOSED_COMPLIANCE_REJECTED

The agents know nothing about gates: this module calls them, stores their results and decides what
may happen next. An illegal move (e.g. narrating a case nobody approved) raises `WorkflowError` and
changes nothing, which is what makes the human gate enforceable rather than a convention.

Shared by the notebook (Step 4.7), the Streamlit UI (Stage 05) and the tests.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from src.compliance_officer_agent import ComplianceError
from src.foundation_sar import (
    CaseData,
    ComplianceOfficerOutput,
    ExplainabilityLogger,
    HumanDecision,
    RiskAnalystOutput,
)
from src.risk_analyst_agent import RiskAnalysisError


# Phase 4 – Step 4.2: where a case is in the workflow
class CaseStatus(StrEnum):
    NEW = "NEW"
    RISK_REVIEW = "RISK_REVIEW"  # risk analysis done, waiting for the human risk gate
    RISK_APPROVED = "RISK_APPROVED"
    CLOSED_RISK_REJECTED = "CLOSED_RISK_REJECTED"
    COMPLIANCE_REVIEW = "COMPLIANCE_REVIEW"  # narrative done, waiting for the compliance gate
    SAR_FILED = "SAR_FILED"
    CLOSED_COMPLIANCE_REJECTED = "CLOSED_COMPLIANCE_REJECTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"  # an agent failed; a person must take over


# Phase 4 – Step 4.2: the only allowed moves; anything else is a WorkflowError
TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    CaseStatus.NEW: frozenset({CaseStatus.RISK_REVIEW, CaseStatus.MANUAL_REVIEW}),
    CaseStatus.RISK_REVIEW: frozenset(
        {CaseStatus.RISK_APPROVED, CaseStatus.CLOSED_RISK_REJECTED}
    ),
    CaseStatus.RISK_APPROVED: frozenset({CaseStatus.COMPLIANCE_REVIEW, CaseStatus.MANUAL_REVIEW}),
    CaseStatus.COMPLIANCE_REVIEW: frozenset(
        {CaseStatus.SAR_FILED, CaseStatus.CLOSED_COMPLIANCE_REJECTED}
    ),
}
# Phase 4 – Step 4.2: no move leads out of these (final in Stage 04)
FINAL_STATUSES = frozenset(set(CaseStatus) - set(TRANSITIONS))


# Phase 4 – Step 4.2: raised for illegal moves, unknown cases or duplicates; state is unchanged
class WorkflowError(Exception):
    pass


# Phase 4 – Step 4.2: what one agent run cost — feeds the metrics (Step 4.5)
@dataclass
class StageStats:
    model: str | None = None
    prompt_version: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    attempts: int = 0
    steps: int | None = None  # ReACT turns (compliance only)
    method: str = "llm"  # "rule_based" for zero-activity risk results (no tokens spent)

    # Phase 4 – Step 4.2: copy the facts from an agent's `last_run` (AnalysisRun or NarrativeRun)
    @classmethod
    def from_run(cls, run: Any, prompt_version: str | None) -> "StageStats":
        if run is None:
            return cls(prompt_version=prompt_version)
        return cls(
            model=getattr(run, "model", None),
            prompt_version=prompt_version,
            token_usage=dict(getattr(run, "token_usage", {}) or {}),
            latency_ms=getattr(run, "latency_ms", 0.0) or 0.0,
            attempts=getattr(run, "attempts", 0) or 0,
            steps=getattr(run, "steps", None),
            method=getattr(run, "method", "llm"),
        )


# Phase 4 – Step 4.2: everything the workflow knows about one case
@dataclass
class CaseRecord:
    case: CaseData
    status: CaseStatus = CaseStatus.NEW
    risk_output: RiskAnalystOutput | None = None
    risk_stats: StageStats | None = None
    risk_decision: HumanDecision | None = None
    compliance_output: ComplianceOfficerOutput | None = None
    compliance_stats: StageStats | None = None
    compliance_decision: HumanDecision | None = None
    sar_id: str | None = None
    sar_path: str | None = None
    error: str | None = None
    # (status, UTC time) for every move, starting with NEW
    history: list[tuple[CaseStatus, datetime]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.history:
            self.history.append((self.status, datetime.now(UTC)))

    @property
    def case_id(self) -> str:
        return self.case.case_id

    @property
    def is_final(self) -> bool:
        return self.status in FINAL_STATUSES


# Phase 4 – Step 4.2: the orchestrator; agents and logger are injected (real or fakes)
class SARWorkflow:
    # Phase 4 – Step 4.2: both agents should share `logger` so the audit trail is one file
    def __init__(
        self, risk_agent: Any, compliance_agent: Any, logger: ExplainabilityLogger
    ) -> None:
        self.risk_agent = risk_agent
        self.compliance_agent = compliance_agent
        self.logger = logger
        self.records: dict[str, CaseRecord] = {}

    # Phase 4 – Step 4.2: register a case; each case_id may be added only once
    def add_case(self, case: CaseData) -> CaseRecord:
        if case.case_id in self.records:
            raise WorkflowError(f"case {case.case_id} is already in the workflow")
        record = CaseRecord(case=case)
        self.records[case.case_id] = record
        return record

    # Phase 4 – Step 4.2: look up a case or fail clearly
    def get(self, case_id: str) -> CaseRecord:
        try:
            return self.records[case_id]
        except KeyError:
            raise WorkflowError(f"unknown case {case_id}") from None

    # Phase 4 – Step 4.2: Stage 1 — Risk Analyst; NEW → RISK_REVIEW (or MANUAL_REVIEW on failure)
    def run_risk_analysis(self, case_id: str) -> CaseRecord:
        record = self.get(case_id)
        self._require(record, CaseStatus.NEW, "run risk analysis")
        try:
            output = self.risk_agent.analyze_case(record.case)
        except RiskAnalysisError as exc:
            record.risk_stats = self._stats(self.risk_agent)
            self._route_to_manual_review(record, "risk_analysis", exc.reason)
            return record
        record.risk_stats = self._stats(self.risk_agent)
        record.risk_output = output
        self._move(record, CaseStatus.RISK_REVIEW)
        return record

    # Phase 4 – Step 4.2: Stage 2 — Compliance Officer, ONLY after human approval at the risk gate
    def run_compliance(self, case_id: str) -> CaseRecord:
        record = self.get(case_id)
        self._require(record, CaseStatus.RISK_APPROVED, "generate a narrative")
        try:
            output = self.compliance_agent.generate_compliance_narrative(
                record.case, record.risk_output
            )
        except ComplianceError as exc:
            record.compliance_stats = self._stats(self.compliance_agent)
            self._route_to_manual_review(record, "compliance_narrative", exc.reason)
            return record
        record.compliance_stats = self._stats(self.compliance_agent)
        record.compliance_output = output
        self._move(record, CaseStatus.COMPLIANCE_REVIEW)
        return record

    # Phase 4 – Step 4.2: guard with a readable message before attempting an action
    @staticmethod
    def _require(record: CaseRecord, expected: CaseStatus, action: str) -> None:
        if record.status != expected:
            hint = (
                " (it needs human approval at the risk gate first)"
                if expected == CaseStatus.RISK_APPROVED
                else ""
            )
            raise WorkflowError(
                f"cannot {action} for {record.case_id}: status is {record.status}, "
                f"expected {expected}{hint}"
            )

    # Phase 4 – Step 4.2: the single place where status changes; checks the transition table
    @staticmethod
    def _move(record: CaseRecord, new_status: CaseStatus) -> None:
        if new_status not in TRANSITIONS.get(record.status, frozenset()):
            raise WorkflowError(
                f"illegal move for {record.case_id}: {record.status} → {new_status}"
            )
        record.status = new_status
        record.history.append((new_status, datetime.now(UTC)))

    # Phase 4 – Step 4.2: agent failure → a person takes over; the routing itself is audited
    def _route_to_manual_review(self, record: CaseRecord, stage: str, reason: str) -> None:
        previous = record.status
        record.error = reason
        self._move(record, CaseStatus.MANUAL_REVIEW)
        self.logger.log_agent_action(
            agent_type="Workflow",
            action="routed_to_manual_review",
            case_id=record.case_id,
            input_data={"stage": stage, "from_status": str(previous)},
            output_data={"status": str(record.status)},
            reasoning=f"{stage} failed; case routed to manual review: {reason}",
            execution_time_ms=0.0,
            success=False,
            error_message=reason,
        )

    # Phase 4 – Step 4.2: run stats from an agent's last run (agents expose `last_run`)
    @staticmethod
    def _stats(agent: Any) -> StageStats:
        return StageStats.from_run(
            getattr(agent, "last_run", None), getattr(agent, "prompt_version", None)
        )
