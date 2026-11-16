# Master Plan – TRACE: AI-Powered SAR Processing System

- **Status:** DRAFT – review feedback applied, awaiting approval
- **Branch:** `docs/master-plan`
- **Master plan:** this document
- **Depends on:** nothing (first stage)
- **Source of requirements:** [`project_goal.md`](../../project_goal.md), [`project_system_arch.png`](../../project_system_arch.png)

---

## 1. What we are building (in one paragraph)

A system that takes customer, account and transaction data from CSV files, groups it into one
**case** per customer, and runs it through two AI agents. The **Risk Analyst agent** decides whether the
activity looks suspicious and why, reasoning step by step (Chain-of-Thought). A **human reviewer**
approves or rejects that finding. The **Compliance Officer agent** then writes a short SAR
narrative (under 120 words) that FinCEN would accept, using a Reason → Act → Observe loop (ReACT). A second human
review approves the narrative before a **SAR document** (JSON) is produced. Every step, decision and
rejection is written to a JSONL **audit log** that a regulator can inspect.

```
project_data/*.csv → DataLoader → CaseData
  → RiskAnalystAgent (CoT) → RiskAnalystOutput → [Human: Risk Review Gate]
  → ComplianceOfficerAgent (ReACT) → ComplianceOfficerOutput → [Human: Compliance Review Gate]
  → SAR document (JSON)                       (every arrow also writes to ExplainabilityLogger)
```

## 2. Key concepts (learning notes)

| Concept | What it means here | Where we meet it |
|---|---|---|
| **Pydantic schema** | A Python class that declares the shape and rules of data (types, ranges, allowed values) and rejects bad input with a clear error. | Phase 1 |
| **JSONL** | "JSON Lines": one JSON object per line. Writes only ever add to the end of the file, and it's easy to stream into BigQuery. Good for audit logs. | Phase 1 |
| **Chain-of-Thought (CoT)** | Prompting the model to reason in explicit numbered steps before answering, which makes the answer more accurate *and* explainable. | Phase 2 |
| **ReACT** | Reason → Act → Observe loop. The model thinks, takes an action (e.g. "check word count", "look up regulation"), sees the result, and repeats until done. | Phase 3 |
| **Human-in-the-loop (HITL)** | A person must approve before the system moves on. Here it provides both regulatory oversight and cost control. | Phase 4 |
| **Two-stage cost optimization** | The expensive narrative step only runs on cases a human confirmed, so we don't pay to write SARs for false positives. | Phase 4 |
| **Fakes / dependency injection** | The LLM client is passed in rather than created inside the agent, so tests can pass a fake that returns canned answers without spending money on API calls. | Phases 2–3, 6 |
| **Evals** | Tests that score the AI's *quality* statistically (e.g. "92% of narratives answer all 5 W's"), not just pass/fail. | Phase 8 |

## 3. Target repository layout

```
TRACE_base/
├── src/
│   ├── __init__.py
│   ├── config.py                    # Phase 1: reads .env (models, base URL, prompt version)
│   ├── foundation_sar.py            # Phase 1: schemas, DataLoader, ExplainabilityLogger
│   ├── risk_analyst_agent.py        # Phase 2
│   ├── compliance_officer_agent.py  # Phase 3
│   └── sar_workflow.py              # Phase 4: pipeline + gate logic (reused by notebook & UI)
├── notebooks/
│   ├── 01_data_exploration.ipynb    # Phase 1
│   └── 03_workflow_integration.ipynb# Phase 4
├── app/streamlit_app.py             # Phase 5
├── tests/                           # grows every phase; consolidated in Phase 6
├── evals/                           # Phase 8
├── project_data/                    # provided CSVs (read-only)
├── logs/  outputs/                  # generated, git-ignored
├── plan/                            # one numbered folder per stage
├── pyproject.toml                   # ruff + pytest config
├── requirements.txt / requirements-dev.txt
├── .env.example
├── Dockerfile                       # Phase 7
└── .github/workflows/               # Phase 7
```

## 4. Cross-cutting conventions (apply in every phase)

1. **Phase comments** at the top of every new file and on every function/class (`# Phase N – Step M: …`).
2. **No real LLM calls in default tests.** Tests that hit the API are marked `@pytest.mark.llm`
   and skipped unless run with `pytest -m llm`.
3. **LLM client is injectable**: `OpenAI(base_url=OPENAI_BASE_URL)` is built once in `config.py`
   and passed into each agent. Agents use `MODEL`; the Phase 8 LLM-as-judge uses a **different**
   model, `JUDGE_MODEL`, so a model never grades its own output.
4. **Prompt versioning**: every prompt carries `PROMPT_VERSION`, and every audit record stores it, so we
   can tell which prompt produced which SAR.
5. **Audit everything**: each agent call and gate decision logs `case_id`, timestamp, actor
   (agent/human), input summary, output, reasoning, model, prompt version, token usage, latency.
6. **Never log the raw SSN or full PII** in audit/telemetry; use `customer_id` and `ssn_last_4` at most.
7. **One stage = one branch = one PR**, after the plan for that stage is reviewed.

## 5. Stages

Each stage gets its own folder `plan/NN-<topic>/plan.md`, written and reviewed **before** any code.
The steps below are the high-level outline; each stage's plan expands them.

---

### Stage 01 – Foundation & Data Modeling  (`plan/01-foundation/`, branch `feature/foundation-schemas`)
**Goal:** trustworthy, validated data objects and an audit logger that everything else builds on.

| Step | Purpose | Output | Depends on |
|---|---|---|---|
| 1.1 Tooling setup | Reproducible environment, lint and test from day one | `pyproject.toml`, `requirements*.txt`, `.env.example` (incl. `JUDGE_MODEL`), `src/config.py`, empty `tests/` | – |
| 1.2 Data exploration | Understand columns, value ranges, joins, messy values, suspicious patterns in the data (e.g. cash deposits just under $10k) | `notebooks/01_data_exploration.ipynb` + findings in stage plan | 1.1 |
| 1.3 Entity schemas | Validate one row each of customers / accounts / transactions | `CustomerData`, `AccountData`, `TransactionData` with field validators | 1.2 |
| 1.4 CaseData schema | One customer + their accounts + those accounts' transactions | `CaseData` | 1.3 |
| 1.5 Agent output schemas | Define the contracts the agents must produce *before* writing the agents | `RiskAnalystOutput`, `ComplianceOfficerOutput` | 1.3 |
| 1.6 ExplainabilityLogger | Append-only JSONL audit log | `ExplainabilityLogger` class | 1.1 |
| 1.7 DataLoader | Read the 3 CSVs, validate, build `CaseData` objects, report bad rows clearly | `DataLoader` class | 1.4, 1.6 |
| 1.8 Foundation tests | Prove every CSV row validates and bad data fails gracefully | `tests/test_foundation.py` | 1.3–1.7 |

**Deliverable:** `src/foundation_sar.py` that loads all 150 customers into valid `CaseData` objects.

---

### Stage 02 – Risk Analyst Agent  (`plan/02-risk-analyst-agent/`, branch `feature/risk-analyst-agent`)
**Goal:** classify a case into one of 5 crime types with confidence, risk level and an explicit reasoning chain.

| Step | Purpose | Output | Depends on |
|---|---|---|---|
| 2.1 CoT prompt design | 5-step reasoning framework (e.g. profile → patterns → indicators → typology → conclusion) | System prompt + versioned prompt file | Stage 01 |
| 2.2 Case → prompt formatter | Turn `CaseData` into a compact, token-efficient summary | formatter function | 1.4 |
| 2.3 RiskAnalystAgent + `analyze_case` | Call the LLM and return `RiskAnalystOutput` | agent class | 2.1, 2.2, 1.5 |
| 2.4 JSON parsing & validation | Extract JSON robustly (code fences, extra text), validate with Pydantic | parser + retry-on-invalid | 2.3 |
| 2.5 Error handling | Timeouts, rate limits, malformed output → retry, then a clear failure result that gets logged | error paths | 2.3 |
| 2.6 Audit integration | Log every analysis (input summary, reasoning, tokens, latency) | logger calls | 1.6 |
| 2.7 Tests | Fake-client unit tests + a few opt-in `llm` tests on real cases | `tests/test_risk_analyst.py` | 2.3–2.6 |

**Contract:** classification ∈ {Structuring, Sanctions, Fraud, Money_Laundering, Other};
confidence 0.0–1.0; risk level ∈ {Low, Medium, High, Critical}; key indicators list; reasoning steps.

---

### Stage 03 – Compliance Officer Agent  (`plan/03-compliance-officer-agent/`, branch `feature/compliance-officer-agent`)
**Goal:** an under-120-word, FinCEN-ready narrative that answers who / what / when / where / why, with citations.

| Step | Purpose | Output | Depends on |
|---|---|---|---|
| 3.1 ReACT prompt design | Thought → Action → Observation structure for narrative drafting | System prompt (versioned) | Stage 02 |
| 3.2 ComplianceOfficerAgent + `generate_compliance_narrative` | Turn `CaseData` + `RiskAnalystOutput` into `ComplianceOfficerOutput` | agent class | 3.1, 1.5 |
| 3.3 Narrative validation | Word count < 120 (hard limit), presence of the 5 W's, required terminology, no speculative language | validator functions | 3.2 |
| 3.4 Regulatory citations | Map typologies to BSA/AML references (e.g. 31 CFR 1020.320, 31 U.S.C. 5324 for structuring) | citation table + checks | 3.2 |
| 3.5 Revise-on-failure loop | If validation fails, feed the errors back for one more revision attempt | bounded retry loop | 3.3 |
| 3.6 Audit + tests | Log the reasoning behind how the narrative was built; fake-client tests | `tests/test_compliance_officer.py` | 3.2–3.5 |

---

### Stage 04 – System Integration & Human Gates  (`plan/04-workflow-integration/`, branch `feature/sar-workflow`)
**Goal:** working end-to-end pipeline with two human decision points and a complete audit trail.

| Step | Purpose | Output | Depends on |
|---|---|---|---|
| 4.1 Workflow module | Orchestrate load → analyze → gate → narrate → gate → SAR, as a library that the notebook and the UI both reuse | `src/sar_workflow.py` | Stages 01–03 |
| 4.2 Human decision gates | Approve / reject (with reason) at both gates; rejections end the case and are logged | gate functions + `HumanDecision` schema | 4.1 |
| 4.3 SAR document generation | Assemble full SAR JSON (subject, accounts, activity dates/amounts, typology, narrative, citations, reviewers) | SAR schema + writer to `outputs/` | 4.2 |
| 4.4 Efficiency metrics | Tokens, cost and latency per stage; show savings from not narrating rejected cases | metrics summary | 4.1 |
| 4.5 Integration notebook | Demonstrate the full flow interactively on real cases | `notebooks/03_workflow_integration.ipynb` | 4.1–4.4 |
| 4.6 Integration tests | End-to-end with fake LLM, both gate outcomes | `tests/test_workflow.py` | 4.1–4.3 |

---

### Stage 05 – Streamlit UI  (`plan/05-streamlit-ui/`, branch `feature/streamlit-review-ui`)
**Goal:** a reviewer-friendly interface for the same workflow.

| Step | Purpose | Output | Depends on |
|---|---|---|---|
| 5.1 Case queue | Browse/select cases, see customer summary and transactions | page/section | Stage 04 |
| 5.2 Risk review screen | Show classification, confidence, reasoning; approve/reject with reason | gate UI | 4.2 |
| 5.3 Compliance review screen | Show narrative, word count, 5-W checklist, citations; approve/reject | gate UI | 4.2 |
| 5.4 SAR & audit views | View/download SAR JSON; browse audit trail per case | views | 4.3 |
| 5.5 Metrics panel | Cost/latency/approval-rate summary | panel | 4.4 |

---

### Stage 06 – Test Suite Consolidation  (`plan/06-testing/`, branch `test/consolidated-suite`)
**Goal:** fill the gaps in the per-phase tests so the categories in `project_goal.md` are fully covered.

| Step | Purpose | Output | Depends on |
|---|---|---|---|
| 6.1 Coverage review | Find gaps against the test categories in the goal | gap list | Stages 01–05 |
| 6.2 Synthetic scenarios | One handcrafted case per crime type + edge cases (empty txns, malformed rows) | `tests/fixtures/` | 6.1 |
| 6.3 Error-path tests | API failures, malformed JSON, validation failures | tests | 6.2 |
| 6.4 Performance benchmarks | Response time / token budgets (opt-in `llm` marker) | benchmark tests | 6.2 |
| 6.5 Run log | Structured per-run record (case, model, prompt version, scores, cost) feeding Phase 8 | run-log writer | 4.4 |

---

### Stage 07 – CI/CD to GCP  (`plan/07-cicd-gcp/`, branches `chore/github-actions-ci`, `chore/cloudrun-deploy`)
**Goal:** every PR is linted and tested automatically; `main` deploys the Streamlit app to Cloud Run.

| Step | Purpose | Output | Depends on |
|---|---|---|---|
| 7.1 CI workflow | `ruff check` + `pytest` on every PR | `.github/workflows/ci.yml` | Stage 06 |
| 7.2 Dockerfile | Container for the Streamlit app | `Dockerfile`, `.dockerignore` | Stage 05 |
| 7.3 GCP setup | Artifact Registry, Secret Manager (`OPENAI_API_KEY`), service account, Workload Identity Federation (keyless auth from GitHub) | documented `gcloud` commands (you run them) | – |
| 7.4 CD workflow | Build → push → `gcloud run deploy` on merge to `main` | `.github/workflows/deploy.yml` | 7.1–7.3 |

---

### Stage 08 – AI QA Framework  (`plan/08-ai-qa/`, branch `feature/ai-qa-evals`)
**Goal:** measure and monitor AI quality, not just whether the code runs.

| Layer | Steps | Output | Depends on |
|---|---|---|---|
| **A – Deterministic tests** (minimal) | Already built in every phase; CI runs them | – | Stage 07 |
| **B – Probabilistic evals** (main) | 8.1 Graders (rule-based: word count, 5 W's, citations; LLM-as-judge on `JUDGE_MODEL`: factual faithfulness to case data) → 8.2 eval harness over a labeled case set → 8.3 slice results (by crime type, risk level) → 8.4 feedback loop into prompt versions | `evals/` + reports | Stages 04, 06 |
| **C – System metrics & drift** (main) | 8.5 Ship run log to Cloud Logging → BigQuery sink → 8.6 dashboard (Looker Studio) → 8.7 alerts on cost, latency, approval-rate or score drift | dashboards + alert policies | 6.5, Stage 07 |

---

## 6. Dependency overview

```
01 Foundation ─→ 02 Risk Analyst ─→ 03 Compliance ─→ 04 Integration ─→ 05 UI
                                                            │              │
                                                            └──→ 06 Tests ←┘
                                                                     │
                                                              07 CI/CD (GCP)
                                                                     │
                                                              08 AI QA Framework
```

## 7. Decisions (resolved in review, 2026-11-16)

1. **Workflow location:** the logic lives in `src/sar_workflow.py`; `notebooks/03_workflow_integration.ipynb`
   only demonstrates it. Streamlit and the tests reuse the same module. (Developer updated
   `project_goal.md` to drop the notebook-only wording.)
2. **Narrative length:** hard limit of **fewer than 120 words** (`word_count < 120`), enforced in 3.3
   and graded in 8.1.
3. **Phase 8 inputs:** developer removed the "from Phase 4 / from Phase 6" wording in `project_goal.md`.
   The plan keeps its dependencies: graders (8.1) reuse the validators from 3.3 and metrics from 4.4,
   and telemetry (8.5) ships the run log from 6.5.
4. **Judge model:** LLM-as-judge uses a different model from the agents, configured as `JUDGE_MODEL`
   in `.env` (value chosen when Stage 08 is planned).
5. **Git config:** `pull.ff=only` and `fetch.prune=true` are now set in the local repo config.

## 8. Next step

After this plan is approved → write `plan/01-foundation/plan.md` (detailed Stage 01 plan) for review.
