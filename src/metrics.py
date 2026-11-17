# Phase 4 – Step 4.5: efficiency metrics — what each stage cost, and what the risk gate saved
"""Efficiency metrics for the two-stage SAR workflow.

The claim to prove: because the Compliance Officer only runs on cases a human approved at the risk
gate, rejected cases cost one cheap risk analysis instead of a risk analysis *plus* an expensive
ReACT narrative loop.

    cost avoided = (cases rejected at the risk gate) × (average compliance cost per narrated case)

Token counts are exact (from the API); **prices are assumptions** — check the provider's current
price list before quoting any dollar figure. Unknown models are reported as unpriced, not guessed.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # type hints only — avoids a circular import with sar_workflow
    from src.sar_workflow import CaseRecord, StageStats

# Phase 4 – Step 4.5: USD per 1M tokens — ASSUMED prices, verify before relying on them.
# Keys are model-name prefixes: the API reports dated names such as gpt-4o-mini-2024-07-18.
PRICES: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"prompt": 0.15, "completion": 0.60},
}


# Phase 4 – Step 4.5: price entry for a model name (longest matching prefix), or None if unknown
def price_for(model: str | None) -> dict[str, float] | None:
    if not model:
        return None
    matches = [prefix for prefix in PRICES if model.startswith(prefix)]
    return PRICES[max(matches, key=len)] if matches else None


# Phase 4 – Step 4.5: dollar cost of one agent run; None when the model has no price entry
def run_cost(stats: "StageStats") -> float | None:
    usage = stats.token_usage or {}
    if not usage.get("total_tokens"):
        return 0.0  # rule-based results and runs that failed before any API call cost nothing
    price = price_for(stats.model)
    if price is None:
        return None
    return (
        usage.get("prompt_tokens", 0) / 1e6 * price["prompt"]
        + usage.get("completion_tokens", 0) / 1e6 * price["completion"]
    )


# Phase 4 – Step 4.5: totals for one stage (risk or compliance)
@dataclass
class StageSummary:
    runs: int = 0  # every agent run, including failures
    llm_runs: int = 0  # runs that called the model
    rule_based_runs: int = 0  # zero-activity cases answered without the model
    unpriced_runs: int = 0  # model missing from PRICES: tokens counted, cost not
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    # Phase 4 – Step 4.5: averages over model runs only (rule-based runs are free and instant)
    @property
    def avg_cost_usd(self) -> float | None:
        priced = self.llm_runs - self.unpriced_runs
        return self.cost_usd / priced if priced > 0 else None

    @property
    def avg_latency_s(self) -> float | None:
        return self.latency_s / self.llm_runs if self.llm_runs else None

    # Phase 4 – Step 4.5: add one run's stats
    def add(self, stats: "StageStats | None") -> None:
        if stats is None:
            return
        self.runs += 1
        if stats.method == "rule_based":
            self.rule_based_runs += 1
            return
        self.llm_runs += 1
        usage = stats.token_usage or {}
        self.prompt_tokens += usage.get("prompt_tokens", 0)
        self.completion_tokens += usage.get("completion_tokens", 0)
        self.latency_s += (stats.latency_ms or 0.0) / 1000
        cost = run_cost(stats)
        if cost is None:
            self.unpriced_runs += 1
        else:
            self.cost_usd += cost


# Phase 4 – Step 4.5: approvals and rejections at one human gate
@dataclass
class GateSummary:
    approved: int = 0
    rejected: int = 0

    @property
    def decided(self) -> int:
        return self.approved + self.rejected

    @property
    def approval_rate(self) -> float | None:
        return self.approved / self.decided if self.decided else None


# Phase 4 – Step 4.5: the whole picture for a batch of cases
@dataclass
class WorkflowMetrics:
    cases: int = 0
    status_counts: dict[str, int] = field(default_factory=dict)
    risk: StageSummary = field(default_factory=StageSummary)
    compliance: StageSummary = field(default_factory=StageSummary)
    risk_gate: GateSummary = field(default_factory=GateSummary)
    compliance_gate: GateSummary = field(default_factory=GateSummary)

    @property
    def total_cost_usd(self) -> float:
        return self.risk.cost_usd + self.compliance.cost_usd

    # Phase 4 – Step 4.5: compliance runs we did NOT pay for because a human said no at gate 1
    @property
    def cost_avoided_usd(self) -> float | None:
        avg = self.compliance.avg_cost_usd
        return self.risk_gate.rejected * avg if avg is not None else None

    @property
    def time_avoided_s(self) -> float | None:
        avg = self.compliance.avg_latency_s
        return self.risk_gate.rejected * avg if avg is not None else None

    # Phase 4 – Step 4.5: what "narrate every analysed case" would have cost
    @property
    def single_stage_cost_usd(self) -> float | None:
        avoided = self.cost_avoided_usd
        return self.total_cost_usd + avoided if avoided is not None else None

    # Phase 4 – Step 4.5: share of the single-stage cost that the risk gate saved
    @property
    def savings_rate(self) -> float | None:
        baseline = self.single_stage_cost_usd
        return self.cost_avoided_usd / baseline if baseline else None

    # Phase 4 – Step 4.5: one row per stage — ready for pandas.DataFrame(...) or printing
    def stage_rows(self) -> list[dict[str, Any]]:
        rows = []
        for name, s in (("Risk Analyst", self.risk), ("Compliance Officer", self.compliance)):
            rows.append({
                "stage": name,
                "runs": s.runs,
                "llm_runs": s.llm_runs,
                "rule_based": s.rule_based_runs,
                "prompt_tokens": s.prompt_tokens,
                "completion_tokens": s.completion_tokens,
                "cost_usd": round(s.cost_usd, 5),
                "avg_cost_usd": _round(s.avg_cost_usd, 5),
                "avg_latency_s": _round(s.avg_latency_s, 1),
            })
        return rows

    # Phase 4 – Step 4.5: the headline numbers
    def summary(self) -> dict[str, Any]:
        return {
            "cases": self.cases,
            "status_counts": self.status_counts,
            "risk_gate_approval_rate": _round(self.risk_gate.approval_rate, 3),
            "compliance_gate_approval_rate": _round(self.compliance_gate.approval_rate, 3),
            "total_cost_usd": round(self.total_cost_usd, 5),
            "cost_avoided_usd": _round(self.cost_avoided_usd, 5),
            "single_stage_cost_usd": _round(self.single_stage_cost_usd, 5),
            "savings_rate": _round(self.savings_rate, 3),
            "time_avoided_s": _round(self.time_avoided_s, 1),
        }


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


# Phase 4 – Step 4.5: build the metrics from workflow records
def summarize(records: Iterable["CaseRecord"]) -> WorkflowMetrics:
    metrics = WorkflowMetrics()
    statuses: Counter[str] = Counter()
    for record in records:
        metrics.cases += 1
        statuses[str(record.status)] += 1
        metrics.risk.add(record.risk_stats)
        metrics.compliance.add(record.compliance_stats)
        for decision, gate in (
            (record.risk_decision, metrics.risk_gate),
            (record.compliance_decision, metrics.compliance_gate),
        ):
            if decision is None:
                continue
            if decision.decision == "approve":
                gate.approved += 1
            else:
                gate.rejected += 1
    metrics.status_counts = dict(sorted(statuses.items()))
    return metrics
