# Project Status – TRACE SAR System

Last updated: 2026-11-17 · Master plan: [`plan/00-master/plan.md`](plan/00-master/plan.md)

Legend: ✅ done · 🔄 in progress · ⏳ not started · 📝 plan only

| Stage | Status | Branch / PR |
|---|---|---|
| 00 – Master plan | ✅ | `docs/master-plan` · PR #1 merged |
| 01 – Foundation & data modeling | ✅ | `feature/foundation-schemas` (PR #2), `feature/foundation-data-exploration` (PR #3) merged |
| 02 – Risk Analyst agent | ✅ | `feature/risk-analyst-agent` · PR #4 merged |
| 03 – Compliance Officer agent | ✅ | `feature/compliance-officer-agent` · PR #5 merged |
| 04 – Workflow integration & human gates | ✅ | `feature/sar-workflow` · PR #6 merged |
| 05 – Streamlit UI | ✅ (5.8 deferred) | `feature/streamlit-review-ui` · PR #7 merged |
| 06 – CI/CD to GCP | 🔄 in progress | `chore/github-actions-ci` (PR #8 merged), `chore/cloudrun-deploy` |
| 07 – Test suite consolidation | ⏳ | – |
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

## Stage 04 – Workflow integration & human gates ✅
Plan: [`plan/04-workflow-integration/plan.md`](plan/04-workflow-integration/plan.md) — approved with all recommendations (sequential batch, PII in SAR file only, approve/reject only, all cases to the gate, tests before notebook)

| Step | Status | Output |
|---|---|---|
| 4.1 Gate and SAR schemas | ✅ | `HumanDecision`, `SARDocument`, `AgentType` += `Workflow` |
| 4.2 Workflow core | ✅ | `src/sar_workflow.py` (state machine, manual-review routing) |
| 4.3 Human decision gates | ✅ | audited gate decisions, scripted + console reviewers |
| 4.4 SAR document generation | ✅ | `outputs/sars/<sar_id>.json` |
| 4.5 Efficiency metrics | ✅ | `src/metrics.py` (cost per stage, cost avoided) |
| 4.6 Integration tests | ✅ | `tests/test_workflow.py` |
| 4.7 Integration notebook | ✅ | `notebooks/03_workflow_integration.ipynb` (8 real cases, 6 SARs, ~$0.035) |

**Known findings (Stage 04):**
- False-positive SAR: clean customer CUST_0003 rated Money_Laundering/Medium; the scripted reviewer
  approves non-Low, so it was filed → exactly what a real human gate (Stage 05 UI) must catch.
- Savings scale with the rejection rate: only 2 of 8 cases stopped at the risk gate → 22% saved
  ($0.0097, 47 s). Better risk calibration (`V2` prompt) or a stricter reviewer policy would raise it.
- Compliance costs ~6.3× risk per case ($0.00485 vs $0.00077; 23.7 s vs 9.1 s).
- FREQ case rated Money_Laundering/Medium here vs Other/Low in Stage 02 → reproducibility topic for Stage 08.
- Notebook projection to 119 active cases uses an approval rate that includes the free
  zero-activity rejection → slightly optimistic; labelled as a rough assumption.

## Stage 05 – Streamlit UI ✅ (5.8 deferred)
Plan: [`plan/05-streamlit-ui/plan.md`](plan/05-streamlit-ui/plan.md) — approved with all recommendations (session-only state, segregation of duties enforced in the workflow, batch N ≤ 10, free-text reviewer ID, added setup/tests/walk-through steps)

| Step | Status | Output |
|---|---|---|
| 5.1 Setup and app shell | ✅ | `app/streamlit_app.py`, `app/state.py`, `.streamlit/config.toml` |
| 5.2 Case queue | ✅ | `app/views/queue.py` |
| 5.3 Risk review screen | ✅ | `app/views/risk_review.py` |
| 5.4 Compliance review screen | ✅ | `app/views/compliance_review.py` |
| 5.5 SAR & audit views | ✅ | `app/views/sars_audit.py`, `read_audit_events` |
| 5.6 Metrics panel | ✅ | `app/views/metrics.py` |
| 5.7 UI tests | ✅ | `tests/test_streamlit_app.py`, `tests/test_ui_helpers.py` (50 tests, see map below) |
| 5.8 Manual walk-through | ⏸ deferred | developer decision 2026-11-17: CI/CD (Stage 06) first; walk-through later on the deployed app |

**UI test coverage map (Step 5.7, 50 tests):** app shell 7 · case queue 6 · risk review 6 ·
compliance review 7 · SARs & audit 4 · metrics 3 · end-to-end journey with real agents on
`FakeLLMClient` 1 · pure helpers (`app/ui_helpers.py`) 16.

**Decisions (Stage 05):**
- Review screens (queue, risk review, compliance review) never show real transaction IDs: the
  journey test fails on any planted prefix (`STRUCT_`, `LARGE_`, …). The **SARs & audit** page keeps
  real IDs on purpose (developer decision, 2026-11-17): it is the post-decision record an examiner
  reads, and the SAR file and audit log must hold the real IDs.

## Stage 06 – CI/CD to GCP 🔄
Was Stage 07 — moved ahead of test consolidation (developer decision 2026-11-17, master plan decision 6).
Plan: [`plan/06-cicd-gcp/plan.md`](plan/06-cicd-gcp/plan.md) — approved with all recommendations (optional passcode gate, $5 budget alert, no `llm` tests in CI, `asia-south1`)
Note (developer, `project_goal.md`): same GCP account, separate resources for this project.

| Step | Status |
|---|---|
| 6.1 CI workflow (GitHub Actions) | ✅ (PR #8, green on GitHub) |
| 6.2 Dockerfile + CD pipeline (`cd.yml`: build & smoke test) | 🔄 |
| 6.3 GCP setup (Artifact Registry, Secret Manager, WIF) | 🔄 runbook `plan/06-cicd-gcp/gcp_setup.md` written; developer runs it |
| 6.4 CD deploy job (Cloud Run) + optional passcode gate | 🔄 |

## Stage 07 – Test suite consolidation ⏳
Was Stage 06.

| Step | Status |
|---|---|
| 7.1 Coverage review | ⏳ |
| 7.2 Synthetic scenarios | ⏳ |
| 7.3 Error-path tests | ⏳ |
| 7.4 Performance benchmarks | ⏳ |
| 7.5 Run log | ⏳ |

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
