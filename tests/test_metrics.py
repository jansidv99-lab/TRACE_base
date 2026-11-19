# Phase 4 – Step 4.5: efficiency metrics tests — pricing, stage totals, gates, cost avoided
"""Hand-built records with round numbers, so every expected value can be checked by hand."""

import pytest

from src.foundation_sar import HumanDecision
from src.metrics import PRICES, StageSummary, price_for, run_cost, summarize
from src.sar_workflow import CaseRecord, CaseStatus, StageStats


# Phase 4 – Step 4.5: 1M prompt + 1M completion tokens on gpt-4o-mini = $0.15 + $0.60
def _stats(prompt=1_000_000, completion=1_000_000, model="gpt-4o-mini-2024-07-18",
           latency_ms=10_000.0, method="llm") -> StageStats:
    return StageStats(
        model=model, prompt_version="V1", latency_ms=latency_ms, method=method,
        token_usage={"prompt_tokens": prompt, "completion_tokens": completion,
                     "total_tokens": prompt + completion},
    )


def _decision(gate, decision):
    # Phase 9 – Step 9.2: every decision carries a reason of at least 10 characters
    return HumanDecision(gate=gate, decision=decision, reviewer_id="r1",
                         reason=f"{decision}: reason recorded")


def _record(case, status, risk=None, risk_decision=None, compliance=None,
            compliance_decision=None) -> CaseRecord:
    rec = CaseRecord(case=case, status=status)
    rec.risk_stats, rec.compliance_stats = risk, compliance
    rec.risk_decision = _decision("risk", risk_decision) if risk_decision else None
    rec.compliance_decision = (
        _decision("compliance", compliance_decision) if compliance_decision else None
    )
    return rec


# Phase 4 – Step 4.5: prices match dated model names by prefix; unknown models are not guessed
def test_price_lookup():
    assert price_for("gpt-4o-mini-2024-07-18") == PRICES["gpt-4o-mini"]
    assert price_for("some-other-model") is None
    assert price_for(None) is None


def test_run_cost():
    assert run_cost(_stats()) == pytest.approx(0.75)
    assert run_cost(_stats(prompt=0, completion=0)) == 0.0  # nothing spent
    assert run_cost(_stats(model="unknown-model")) is None


# Phase 4 – Step 4.5: rule-based runs are counted but cost nothing and don't skew averages
def test_stage_summary_rule_based_and_unpriced():
    s = StageSummary()
    s.add(_stats())
    s.add(_stats(prompt=0, completion=0, latency_ms=0.0, method="rule_based"))
    s.add(_stats(model="unknown-model"))
    s.add(None)  # stage never ran
    assert (s.runs, s.llm_runs, s.rule_based_runs, s.unpriced_runs) == (3, 2, 1, 1)
    assert s.total_tokens == 4_000_000  # unpriced tokens still counted
    assert s.cost_usd == pytest.approx(0.75)
    assert s.avg_cost_usd == pytest.approx(0.75)  # averaged over priced model runs only
    assert s.avg_latency_s == pytest.approx(10.0)


# Phase 4 – Step 4.5: a batch of 4 cases — 2 rejected at the risk gate, 1 filed, 1 rejected later
def test_summarize_batch(sample_case):
    risk = _stats(prompt=100_000, completion=10_000, latency_ms=8_000)  # $0.021
    comp = _stats(prompt=1_000_000, completion=50_000, latency_ms=20_000)  # $0.18
    records = [
        _record(sample_case, CaseStatus.CLOSED_RISK_REJECTED, risk, "reject"),
        _record(sample_case, CaseStatus.CLOSED_RISK_REJECTED, risk, "reject"),
        _record(sample_case, CaseStatus.SAR_FILED, risk, "approve", comp, "approve"),
        _record(sample_case, CaseStatus.CLOSED_COMPLIANCE_REJECTED, risk, "approve", comp,
                "reject"),
    ]
    m = summarize(records)

    assert m.cases == 4
    assert m.status_counts == {"CLOSED_COMPLIANCE_REJECTED": 1, "CLOSED_RISK_REJECTED": 2,
                               "SAR_FILED": 1}
    assert m.risk.cost_usd == pytest.approx(4 * 0.021)
    assert m.compliance.cost_usd == pytest.approx(2 * 0.18)
    assert m.risk_gate.approval_rate == 0.5
    assert m.compliance_gate.approval_rate == 0.5
    # 2 rejected at gate 1 × $0.18 average narrative = $0.36 not spent, 40 s not waited
    assert m.cost_avoided_usd == pytest.approx(0.36)
    assert m.time_avoided_s == pytest.approx(40.0)
    assert m.single_stage_cost_usd == pytest.approx(0.084 + 0.36 + 0.36)
    assert m.savings_rate == pytest.approx(0.36 / 0.804)

    rows = m.stage_rows()
    assert [r["stage"] for r in rows] == ["Risk Analyst", "Compliance Officer"]
    assert rows[1]["avg_cost_usd"] == pytest.approx(0.18)
    headline = m.summary()
    assert headline["cost_avoided_usd"] == pytest.approx(0.36)
    assert headline["savings_rate"] == pytest.approx(0.448, abs=1e-3)


# Phase 4 – Step 4.5: without any narrated case there is no average, so no savings claim
def test_no_compliance_runs_means_no_savings_claim(sample_case):
    m = summarize([_record(sample_case, CaseStatus.CLOSED_RISK_REJECTED, _stats(), "reject")])
    assert m.cost_avoided_usd is None
    assert m.savings_rate is None
    assert m.summary()["cost_avoided_usd"] is None


def test_empty_batch():
    m = summarize([])
    assert m.cases == 0 and m.total_cost_usd == 0.0
    assert m.risk_gate.approval_rate is None
