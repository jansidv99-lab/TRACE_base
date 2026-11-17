# Project Status – TRACE SAR System

Last updated: 2026-11-17 · Master plan: [`plan/00-master/plan.md`](plan/00-master/plan.md)

Legend: ✅ done · 🔄 in progress · ⏳ not started · 📝 plan only

| Stage | Status | Branch / PR |
|---|---|---|
| 00 – Master plan | ✅ | `docs/master-plan` · PR #1 merged |
| 01 – Foundation & data modeling | ✅ | `feature/foundation-schemas` (PR #2), `feature/foundation-data-exploration` (PR #3) merged |
| 02 – Risk Analyst agent | ✅ | `feature/risk-analyst-agent` · PR #4 merged |
| 03 – Compliance Officer agent | ✅ | `feature/compliance-officer-agent` · PR #5 merged |
| 04 – Workflow integration & human gates | 🔄 in progress | `feature/sar-workflow` |
| 05 – Streamlit UI | ⏳ | – |
| 06 – Test suite consolidation | ⏳ | – |
| 07 – CI/CD to GCP | ⏳ | – |
| 08 – AI QA framework | ⏳ | – |

---

## Stage 00 – Master plan ✅
| Step | Status | Output |
|---|---|---|
| Master plan + review decisions | ✅ | `plan/00-master/plan.md` |

## Stage 01 – Foundation & data modeling ✅
Plan: [`plan/01-foundation/plan.md`](plan/01-foundation/plan.md)

| Step | Status | Output |
|---|---|---|
| 1.1 Tooling setup | ✅ | `pyproject.toml`, `requirements*.txt`, `.env.example`, `src/config.py` |
| 1.2 Data exploration notebook | ✅ | `notebooks/01_data_exploration.ipynb` |
| 1.3 Entity schemas | ✅ | `CustomerData`, `AccountData`, `TransactionData` |
| 1.4 CaseData schema | ✅ | `CaseData` |
| 1.5 Agent output schemas | ✅ | `RiskAnalystOutput`, `ComplianceOfficerOutput` |
| 1.6 ExplainabilityLogger | ✅ | JSONL audit log with PII redaction |
| 1.7 DataLoader | ✅ | 150 cases, 368 data-quality flags |
| 1.8 Foundation tests | ✅ | `tests/test_foundation.py`, `tests/conftest.py` |

## Stage 02 – Risk Analyst agent ✅
Plan: [`plan/02-risk-analyst-agent/plan.md`](plan/02-risk-analyst-agent/plan.md)

| Step | Status | Output |
|---|---|---|
| 2.1 LLM client setup | ✅ | `src/llm_client.py` (openai 3.51.0, Vocareum endpoint verified) |
| 2.2 Chain-of-Thought prompt | ✅ | `src/prompts/risk_analyst_V1.md`, `load_prompt()` |
| 2.3 Case formatter with masking | ✅ | `src/case_formatter.py` (no ID/PII leaks across 150 cases) |
| 2.4 RiskAnalystAgent + analyze_case | ✅ | `src/risk_analyst_agent.py`, zero-activity short-circuit |
| 2.5 JSON parsing & error handling | ✅ | repair retry, exponential backoff, `RiskAnalysisError` |
| 2.6 Audit integration | ✅ | one audit event per analysis; logger records actual prompt version |
| 2.7 Tests | ✅ | `tests/test_risk_analyst.py`, `tests/fakes.py`; `seed=42` for reproducibility |
| 2.8 Demo notebook | ✅ | `notebooks/02_risk_analyst_demo.ipynb` |

**Known findings carried forward:**
- High-frequency small transfers (`FREQ_`) were rated Other/Low → candidate for a `V2` prompt / Stage 08 eval.
- Structuring cases rated Medium rather than High → calibration question for `V2`.
- One factual slip (wrong date on an amount) seen in Step 2.4 → Stage 08 faithfulness grader.
- ~8.5 s per case → ~17 min for 119 active cases sequentially → relevant to Stage 04.

## Stage 03 – Compliance Officer agent ✅
Plan: [`plan/03-compliance-officer-agent/plan.md`](plan/03-compliance-officer-agent/plan.md) — real ReACT loop with function calling (verified via Vocareum)

| Step | Status | Output |
|---|---|---|
| 3.1 Shared LLM utilities (refactor) | ✅ | `src/llm_utils.py`, `tests/test_llm_utils.py`; risk agent uses it, no behaviour change |
| 3.2 Regulatory citation table | ✅ | `src/regulations.py` (10 citations, format-insensitive matching) |
| 3.3 Narrative validators | ✅ | `src/narrative_checks.py` (length, 5 W's, terminology, internal IDs, dates, amounts, citations) |
| 3.4 ReACT system prompt | ✅ | `src/prompts/compliance_officer_V1.md` (~1,030 tokens) |
| 3.5 Tools: schemas + executor | ✅ | `src/compliance_tools.py` (4 tools, error observations instead of crashes) |
| 3.6 ComplianceOfficerAgent ReACT loop | ✅ | `src/compliance_officer_agent.py` (live: 6 steps, accepted 95-word narrative) |
| 3.7 Audit integration | ✅ | one audit event per call with summarised ReACT trace + validation report |
| 3.8 Tests | ✅ | `tests/test_compliance_officer.py` (39 offline + 2 live, both live passed); `tool_call_response` fake |
| 3.9 Demo notebook | ✅ | `notebooks/03_compliance_officer_demo.ipynb` (4 cases, all accepted, ~$0.02) |

**Known findings (Stage 03):**
- `gpt-4o-mini` emits no `Thought:` text alongside forced tool calls → trace has actions/observations only (`thought` tool argument deferred).
- Redundant `check_narrative` calls are systematic: 2–4 per case in Step 3.9, even after a passing check
  → main reason Stage 2 costs ~5× Stage 1 per case ($0.0042 vs $0.0008); `V2` rule: submit right after a pass.
- Amount check warns on correct computed totals (e.g. the sum of four deposits) → teach it sums, or keep as warning.
- Hedged wording ("suggesting potential structuring") passes the banned-terms list → tone grading in Stage 08.
- Weak Risk Analyst calls flow through faithfully (GEO case classified Structuring on $4.8k/$7.4k deposits)
  → shows why the Stage 04 human gate matters.

## Stage 04 – Workflow integration & human gates 🔄
Plan: [`plan/04-workflow-integration/plan.md`](plan/04-workflow-integration/plan.md) — approved with all recommendations (sequential batch, PII in SAR file only, approve/reject only, all cases to the gate, tests before notebook)

| Step | Status | Output |
|---|---|---|
| 4.1 Gate and SAR schemas | ✅ | `HumanDecision`, `SARDocument`, `AgentType` += `Workflow` |
| 4.2 Workflow core | ⏳ | `src/sar_workflow.py` (state machine, manual-review routing) |
| 4.3 Human decision gates | ⏳ | audited gate decisions, scripted + console reviewers |
| 4.4 SAR document generation | ⏳ | `outputs/sars/<sar_id>.json` |
| 4.5 Efficiency metrics | ⏳ | `src/metrics.py` (cost per stage, cost avoided) |
| 4.6 Integration tests | ⏳ | `tests/test_workflow.py` |
| 4.7 Integration notebook | ⏳ | `notebooks/03_workflow_integration.ipynb` |

## Stage 05 – Streamlit UI ⏳
| Step | Status |
|---|---|
| 5.1 Case queue | ⏳ |
| 5.2 Risk review screen | ⏳ |
| 5.3 Compliance review screen | ⏳ |
| 5.4 SAR & audit views | ⏳ |
| 5.5 Metrics panel | ⏳ |

## Stage 06 – Test suite consolidation ⏳
| Step | Status |
|---|---|
| 6.1 Coverage review | ⏳ |
| 6.2 Synthetic scenarios | ⏳ |
| 6.3 Error-path tests | ⏳ |
| 6.4 Performance benchmarks | ⏳ |
| 6.5 Run log | ⏳ |

## Stage 07 – CI/CD to GCP ⏳
Note (developer, `project_goal.md`): same GCP account, separate resources for this project.

| Step | Status |
|---|---|
| 7.1 CI workflow (GitHub Actions) | ⏳ |
| 7.2 Dockerfile | ⏳ |
| 7.3 GCP setup (Artifact Registry, Secret Manager, WIF) | ⏳ |
| 7.4 CD workflow (Cloud Run) | ⏳ |

## Stage 08 – AI QA framework ⏳
| Step | Status |
|---|---|
| 8.1 Graders (rule-based + LLM-as-judge on `JUDGE_MODEL`) | ⏳ |
| 8.2 Eval harness | ⏳ |
| 8.3 Slices | ⏳ |
| 8.4 Feedback loop | ⏳ |
| 8.5 Run log → Cloud Logging → BigQuery | ⏳ |
| 8.6 Dashboard | ⏳ |
| 8.7 Alerts | ⏳ |
