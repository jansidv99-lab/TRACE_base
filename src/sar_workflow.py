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

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from src import config
from src.case_formatter import format_case_for_llm
from src.compliance_officer_agent import ComplianceError
from src.foundation_sar import (
    CaseData,
    ComplianceOfficerOutput,
    ExplainabilityLogger,
    HumanDecision,
    RiskAnalystOutput,
    SARDocument,
    SARProvenance,
    SuspiciousActivity,
    TransactionData,
    count_words,
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
    # Phase 4 – Step 4.4: real IDs of the transactions the compliance agent looked up
    looked_up_ids: list[str] = field(default_factory=list)
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
    # Phase 4 – Step 4.4: SARs are written to <output_dir>/sars/ (default: git-ignored outputs/)
    def __init__(
        self,
        risk_agent: Any,
        compliance_agent: Any,
        logger: ExplainabilityLogger,
        output_dir: str | Path | None = None,
    ) -> None:
        self.risk_agent = risk_agent
        self.compliance_agent = compliance_agent
        self.logger = logger
        self.output_dir = Path(output_dir) if output_dir else config.PROJECT_ROOT / "outputs"
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
        record.looked_up_ids = _looked_up_ids(
            record.case, getattr(self.compliance_agent, "last_run", None)
        )
        self._move(record, CaseStatus.COMPLIANCE_REVIEW)
        return record

    # Phase 4 – Step 4.3: human risk gate; RISK_REVIEW → RISK_APPROVED or CLOSED_RISK_REJECTED
    def record_risk_decision(self, case_id: str, decision: HumanDecision) -> CaseRecord:
        record = self.get(case_id)
        self._check_gate(record, decision, "risk", CaseStatus.RISK_REVIEW)
        record.risk_decision = decision
        approved = decision.decision == "approve"
        self._move(
            record, CaseStatus.RISK_APPROVED if approved else CaseStatus.CLOSED_RISK_REJECTED
        )
        risk = record.risk_output
        self._log_decision(
            record,
            decision,
            reviewed={
                "classification": risk.classification,
                "risk_level": risk.risk_level,
                "confidence": risk.confidence_score,
            },
        )
        return record

    # Phase 4 – Step 4.3: human compliance gate; COMPLIANCE_REVIEW → SAR_FILED or
    # CLOSED_COMPLIANCE_REJECTED
    # Phase 4 – Step 4.4: approval builds and writes the SAR; if it can't be built, nothing changes
    def record_compliance_decision(self, case_id: str, decision: HumanDecision) -> CaseRecord:
        record = self.get(case_id)
        self._check_gate(record, decision, "compliance", CaseStatus.COMPLIANCE_REVIEW)
        approved = decision.decision == "approve"
        record.compliance_decision = decision
        sar = None
        if approved:
            try:
                sar = build_sar(record)
            except (ValidationError, ValueError) as exc:
                record.compliance_decision = None
                raise WorkflowError(f"cannot build a SAR for {record.case_id}: {exc}") from exc
        self._move(
            record, CaseStatus.SAR_FILED if approved else CaseStatus.CLOSED_COMPLIANCE_REJECTED
        )
        out = record.compliance_output
        self._log_decision(
            record,
            decision,
            reviewed={
                "narrative_words": count_words(out.narrative),
                "citations": out.regulatory_citations,
            },
        )
        if sar is not None:
            self._write_sar(record, sar)
        return record

    # Phase 4 – Step 4.4: save the SAR as JSON and audit it
    def _write_sar(self, record: CaseRecord, sar: SARDocument) -> None:
        sar_dir = self.output_dir / "sars"
        sar_dir.mkdir(parents=True, exist_ok=True)
        path = sar_dir / f"{sar.sar_id}.json"
        path.write_text(sar.model_dump_json(indent=2), encoding="utf-8")
        record.sar_id, record.sar_path = sar.sar_id, str(path)
        self.logger.log_agent_action(
            agent_type="Workflow",
            action="sar_generated",
            case_id=record.case_id,
            input_data={
                "typology": sar.activity.typology,
                "transactions": len(sar.activity.transaction_ids),
            },
            output_data={"sar_id": sar.sar_id, "path": path},
            reasoning="SAR generated after approval at both human gates.",
            execution_time_ms=0.0,
        )

    # Phase 4 – Step 4.3: run the whole flow for many cases, asking the reviewers at each gate
    def process(
        self, cases: list[CaseData], risk_reviewer: "Reviewer", compliance_reviewer: "Reviewer"
    ) -> list[CaseRecord]:
        """Add each case and push it as far as it can go. Sequential (Stage 04 decision 1)."""
        records = []
        for case in cases:
            record = self.add_case(case)
            self.run_risk_analysis(record.case_id)
            if record.status == CaseStatus.RISK_REVIEW:
                self.record_risk_decision(record.case_id, risk_reviewer.review(record, "risk"))
            if record.status == CaseStatus.RISK_APPROVED:
                self.run_compliance(record.case_id)
            if record.status == CaseStatus.COMPLIANCE_REVIEW:
                self.record_compliance_decision(
                    record.case_id, compliance_reviewer.review(record, "compliance")
                )
            records.append(record)
        return records

    # Phase 4 – Step 4.3: a decision must be for this gate, and the case must be waiting at it
    def _check_gate(
        self, record: CaseRecord, decision: HumanDecision, gate: str, expected: CaseStatus
    ) -> None:
        if decision.gate != gate:
            raise WorkflowError(
                f"a '{decision.gate}' decision cannot be recorded at the {gate} gate"
            )
        self._require(record, expected, f"record a {gate} gate decision")

    # Phase 4 – Step 4.3: one Human audit event per decision — approvals AND rejections
    def _log_decision(
        self, record: CaseRecord, decision: HumanDecision, reviewed: dict[str, Any]
    ) -> None:
        self.logger.log_agent_action(
            agent_type="Human",
            action=f"{decision.gate}_gate_decision",
            case_id=record.case_id,
            input_data=reviewed,
            output_data={"decision": decision.decision, "new_status": str(record.status)},
            reasoning=decision.reason or f"Approved by {decision.reviewer_id}",
            execution_time_ms=0.0,
            reviewer_id=decision.reviewer_id,
            decision=decision.decision,
            decided_at=decision.decided_at.isoformat(),
        )

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


# ---------------------------------------------------------------------------
# Phase 4 – Step 4.3: reviewer policies — objects that produce HumanDecisions
# ---------------------------------------------------------------------------
# Phase 4 – Step 4.3: anything with this method can sit at a gate (scripted, console, Streamlit)
class Reviewer(Protocol):
    def review(self, record: CaseRecord, gate: str) -> HumanDecision: ...


LOW_RISK_REASON = "Low risk: activity is consistent with the customer profile; no SAR warranted."


# Phase 4 – Step 4.3: deterministic reviewer for tests and demos
class ScriptedReviewer:
    """Default policy: reject `Low` at the risk gate (Stage 04 decision 4), approve everything else.

    `answers` overrides the policy per case and gate:
    `{(case_id, "risk"): ("reject", "false positive"), ...}`.
    """

    def __init__(
        self,
        reviewer_id: str = "scripted_reviewer",
        answers: dict[tuple[str, str], tuple[str, str | None]] | None = None,
    ) -> None:
        self.reviewer_id = reviewer_id
        self.answers = answers or {}

    # Phase 4 – Step 4.3: fixed answer if given, else the default policy
    def review(self, record: CaseRecord, gate: str) -> HumanDecision:
        if (record.case_id, gate) in self.answers:
            decision, reason = self.answers[(record.case_id, gate)]
        elif gate == "risk" and record.risk_output.risk_level == "Low":
            decision, reason = "reject", LOW_RISK_REASON
        else:
            decision, reason = "approve", None
        return HumanDecision(
            gate=gate, decision=decision, reviewer_id=self.reviewer_id, reason=reason
        )


# Phase 4 – Step 4.3: interactive reviewer for the notebook; input/print are injectable for tests
class ConsoleReviewer:
    def __init__(
        self,
        reviewer_id: str,
        input_fn: Callable[[str], str] = input,
        print_fn: Callable[..., None] = print,
    ) -> None:
        self.reviewer_id = reviewer_id
        self.input = input_fn
        self.print = print_fn

    # Phase 4 – Step 4.3: show what is being reviewed, then ask until the answer is valid
    def review(self, record: CaseRecord, gate: str) -> HumanDecision:
        self.print(f"=== {gate.upper()} GATE | {record.case_id} ===")
        if gate == "risk":
            r = record.risk_output
            self.print(f"{r.classification} | {r.risk_level} | confidence {r.confidence_score}")
            for indicator in r.key_indicators:
                self.print(f"  - {indicator}")
        else:
            out = record.compliance_output
            self.print(out.narrative)
            self.print(f"({count_words(out.narrative)} words) Citations: "
                       f"{'; '.join(out.regulatory_citations)}")
        while True:
            answer = self.input("Approve or reject? [a/r]: ").strip().lower()
            if answer in ("a", "approve"):
                return HumanDecision(gate=gate, decision="approve", reviewer_id=self.reviewer_id)
            if answer in ("r", "reject"):
                reason = ""
                while not reason:
                    reason = self.input("Reason for rejection: ").strip()
                return HumanDecision(
                    gate=gate, decision="reject", reviewer_id=self.reviewer_id, reason=reason
                )
            self.print("Please type 'a' or 'r'.")


# ---------------------------------------------------------------------------
# Phase 4 – Step 4.4: SAR document generation
# ---------------------------------------------------------------------------
# Phase 4 – Step 4.4: real IDs behind the T### refs the compliance agent looked up
def _looked_up_ids(case: CaseData, run: Any) -> list[str]:
    id_map = format_case_for_llm(case).id_map
    ids: list[str] = []
    for step in getattr(run, "trace", None) or []:
        args = step.get("arguments")
        if step.get("action") != "get_transaction_details" or not isinstance(args, dict):
            continue
        for ref in args.get("refs") or []:
            real = id_map.get(str(ref).strip().upper())
            if real and real not in ids:
                ids.append(real)
    return ids


# Phase 4 – Step 4.4: transactions the SAR is about, in date order
def involved_transactions(record: CaseRecord) -> list[TransactionData]:
    """Transactions named in the approved risk finding or the narrative reasoning, plus those the
    compliance agent looked up. Falls back to all case transactions if none can be identified."""
    risk, out = record.risk_output, record.compliance_output
    texts = [*risk.key_indicators, risk.reasoning, out.narrative_reasoning if out else ""]
    wanted = set(record.looked_up_ids)
    involved = [
        t for t in record.case.transactions
        if t.transaction_id in wanted or any(t.transaction_id in text for text in texts)
    ]
    return involved or list(record.case.transactions)


# Phase 4 – Step 4.4: assemble the SAR from an approved case (validated by SARDocument)
def build_sar(record: CaseRecord, generated_at: datetime | None = None) -> SARDocument:
    generated_at = generated_at or datetime.now(UTC)
    case, risk = record.case, record.risk_output
    txns = involved_transactions(record)
    if not txns:
        raise ValueError("the case has no transactions to report")
    risk_stats = record.risk_stats or StageStats()
    compliance_stats = record.compliance_stats or StageStats()
    return SARDocument(
        sar_id=f"SAR_{case.customer.customer_id}_{generated_at:%Y%m%d%H%M%S}",
        generated_at=generated_at,
        case_id=case.case_id,
        subject=case.customer,
        accounts=case.accounts,
        activity=SuspiciousActivity(
            typology=risk.classification,
            risk_level=risk.risk_level,
            confidence_score=risk.confidence_score,
            start_date=min(t.transaction_date for t in txns),
            end_date=max(t.transaction_date for t in txns),
            total_amount=round(sum(t.amount for t in txns), 2),
            transaction_ids=[t.transaction_id for t in txns],
        ),
        narrative=record.compliance_output,
        risk_review=record.risk_decision,
        compliance_review=record.compliance_decision,
        provenance=SARProvenance(
            risk_model=risk_stats.model,
            risk_prompt_version=risk_stats.prompt_version,
            compliance_model=compliance_stats.model,
            compliance_prompt_version=compliance_stats.prompt_version,
        ),
    )
