# Stage 04 – System Integration & Human Gates

- **Status:** APPROVED – ready to implement (Step 4.1 next)
- **Branch:** `feature/sar-workflow`
- **Master plan:** [`plan/00-master/plan.md`](../00-master/plan.md)
- **Depends on:** Stage 01 (schemas, `DataLoader`, `ExplainabilityLogger`), Stage 02
  (`RiskAnalystAgent`, PR #4), Stage 03 (`ComplianceOfficerAgent`, narrative checks, PR #5)
- **Requirements:** `project_goal.md` → "Phase 4: System Integration"

---

## 1. Goal of this stage

Join both agents into one **two-stage, human-gated workflow** that runs on real CSV cases:

```
CaseData ─► RiskAnalystAgent ─► [Risk gate: human approve / reject]
                                      │ reject ─► case closed (logged)
                                      ▼ approve
            ComplianceOfficerAgent ─► [Compliance gate: human approve / reject]
                                      │ reject ─► case closed (logged)
                                      ▼ approve
                                 SAR document (JSON in outputs/sars/)
```

**Done when:**
- `src/sar_workflow.py` can take any case from loaded to SAR (or to a logged rejection).
- Both gates record a `HumanDecision` and write an audit event (approvals **and** rejections).
- Approved cases produce a complete SAR JSON document.
- Metrics show tokens, cost and latency per stage, plus the cost **avoided** by not running the
  Compliance Officer on cases rejected at the risk gate.
- Offline integration tests pass, and a notebook demonstrates the flow on real cases.

## 2. Facts carried in from earlier stages

- 150 cases, **119 with activity**. Planted patterns: GEO 10, STRUCT 4, RAPID 4, FREQ 4, LARGE 3.
  The other customers have ordinary activity (`TXN_` prefix) only.
- Cost per case (Step 3.9, `gpt-4o-mini`, assumed prices): Risk ≈ **$0.0008**, Compliance ≈
  **$0.0042** (~5×). Latency: Risk ~8 s, Compliance 17–23 s.
- So every case rejected at the risk gate saves about $0.004 and 20 s. That is the "cost
  optimization" this stage must *measure*, not just claim.
- Agents already raise `RiskAnalysisError` / `ComplianceError` on final failure, and Stage 03
  decision: those cases go to **manual review**, not silently dropped.
- Zero-activity cases are answered by a rule (`NO_ACTIVITY_RESULT`) with no LLM call.

## 3. Concepts introduced in this stage

- **Orchestration:** one module owns the order of steps. The agents stay simple and know nothing
  about gates; the workflow calls them, stores their results and decides what can happen next.
- **State machine:** every case has a `status`, and only certain moves are allowed (e.g. you cannot
  write a narrative for a case nobody approved). Illegal moves raise an error instead of quietly
  doing the wrong thing. This is what makes the human gate *enforceable*, not just a convention.
- **Non-blocking gates:** a gate is **data** (a `HumanDecision` passed in), not a function that waits
  for keyboard input. The same workflow then works in a notebook, in Streamlit (Stage 05, where the
  app reruns on every click) and in tests (where a scripted reviewer decides).
- **Reviewer policy:** a small object that produces decisions. Tests and demos use a scripted
  policy; the notebook can also use an interactive one that asks you.
- **Cost avoided vs cost spent:** comparing what we paid with what "narrate everything" would
  have paid, using measured per-case averages.

## 4. Design overview

```
src/foundation_sar.py   + HumanDecision, SARDocument (data contracts, Pydantic)
                        + "Workflow" added to AgentType (for SAR-generated / manual-review events)
src/sar_workflow.py     CaseStatus, CaseRecord, SARWorkflow, reviewer policies
src/metrics.py          price table, per-stage usage summary, cost avoided
tests/test_workflow.py  end-to-end with FakeLLMClient
notebooks/03_workflow_integration.ipynb
```

### Case statuses (the state machine)

```
NEW ──analyze──► RISK_REVIEW ──approve──► RISK_APPROVED ──narrate──► COMPLIANCE_REVIEW
 │                   │                                     │              │        │
 │                   └─reject─► CLOSED_RISK_REJECTED       │           approve   reject
 │                                                         │              ▼        ▼
 └─RiskAnalysisError─► MANUAL_REVIEW ◄──ComplianceError────┘          SAR_FILED  CLOSED_COMPLIANCE_REJECTED
```

- Closed statuses (`SAR_FILED`, `CLOSED_*`, `MANUAL_REVIEW`) are final in this stage.
- Every transition is checked; an illegal one raises `WorkflowError` and changes nothing.

### Public API (sketch)

```python
wf = SARWorkflow(risk_agent, compliance_agent, logger, output_dir="outputs")
rec = wf.add_case(case)                         # NEW
wf.run_risk_analysis(case_id)                   # → RISK_REVIEW or MANUAL_REVIEW
wf.record_risk_decision(case_id, decision)      # → RISK_APPROVED or CLOSED_RISK_REJECTED
wf.run_compliance(case_id)                      # → COMPLIANCE_REVIEW or MANUAL_REVIEW
wf.record_compliance_decision(case_id, decision)# → SAR_FILED (writes SAR) or CLOSED_COMPLIANCE_REJECTED
wf.process(cases, risk_reviewer, compliance_reviewer)  # convenience: the whole flow in a loop
wf.metrics()                                    # → efficiency summary (Step 4.5)
```

## 5. Steps

### Step 4.1 – Gate and SAR schemas (`src/foundation_sar.py`)
- **Purpose:** define the contracts for human decisions and the final SAR before writing the logic.
- **Output:**
  - `HumanDecision`: `gate` ∈ {`risk`, `compliance`}, `decision` ∈ {`approve`, `reject`},
    `reviewer_id` (non-empty), `reason` (**required when rejecting**), `decided_at` (UTC).
  - `SARDocument`: `sar_id`, `generated_at`, `case_id`; **subject** (customer_id, name, occupation,
    customer_since, risk_rating; see decision Q2 on PII); **accounts** (id, type, status);
    **suspicious activity** (typology, risk level, confidence, date range, total amount, list of
    involved transaction IDs); **narrative** + citations; **review** (both `HumanDecision`s);
    **provenance** (models, prompt versions, audit `event_id`s of the agent runs).
  - `AgentType` gains `"Workflow"` for events that are neither an agent nor a human
    (`sar_generated`, `routed_to_manual_review`).
- **Depends on:** Stage 01

### Step 4.2 – Workflow core (`src/sar_workflow.py`)
- **Purpose:** run the two agents in order, store results per case and enforce the state machine.
- **Output:** `CaseStatus` enum, `CaseRecord` (case, status, risk output, narrative output, both
  decisions, per-stage run stats, SAR path, error), `SARWorkflow` with `add_case`,
  `run_risk_analysis`, `run_compliance`, `WorkflowError`. Agent failures → `MANUAL_REVIEW` with a
  `Workflow` audit event. The workflow keeps each agent's `last_run` (tokens, latency) on the record.
- **Depends on:** 4.1

### Step 4.3 – Human decision gates
- **Purpose:** the two approval points, recorded and audited.
- **Output:** `record_risk_decision` / `record_compliance_decision`: validate the gate and status,
  store the decision, move the case, and write one audit event with `agent_type="Human"`,
  `action="risk_gate_decision"` / `"compliance_gate_decision"`, the reviewer, decision and reason.
  Reviewer policies: `ScriptedReviewer` (rule or fixed answers, for tests and demos) and
  `ConsoleReviewer` (asks with `input()`, for the notebook). `process(...)` uses them to run the
  whole flow.
- **Depends on:** 4.2

### Step 4.4 – SAR document generation
- **Purpose:** turn an approved case into the regulatory JSON deliverable.
- **Output:** `build_sar(record) -> SARDocument` and writing to `outputs/sars/<sar_id>.json`
  (already git-ignored). **Involved transactions** = real IDs from the risk analyst's key
  indicators/reasoning plus the refs the compliance agent looked up (unmasked), sorted. A `Workflow`
  audit event `sar_generated` records the file path and `sar_id`.
- **Depends on:** 4.3

### Step 4.5 – Efficiency metrics (`src/metrics.py`)
- **Purpose:** prove the two-stage design saves money, with numbers.
- **Output:** a `PRICES` table (USD per 1M tokens, clearly marked as assumptions, per model name)
  and `summarize(records)` returning: cases per status; tokens, cost and latency per stage (total and
  per case); approval rate at each gate; **cost avoided** = (cases rejected at the risk gate) × (average
  compliance cost per narrated case), and the same for time. Shown as a small table.
- **Depends on:** 4.2

### Step 4.6 – Integration tests (`tests/test_workflow.py`)
- **Purpose:** prove the flow and the gates work offline.
- **Output:** with `FakeLLMClient` and the Stage 03 tool-call fakes:
  - happy path → `SAR_FILED`, SAR file written and valid, involved IDs are real (no `T001`), both
    reviewers recorded;
  - rejected at the risk gate → **the compliance agent is never called** (fake records no calls), case closed;
  - rejected at the compliance gate → no SAR file;
  - agent failure at either stage → `MANUAL_REVIEW` + audit event;
  - illegal transitions (narrate before approval, decide twice, wrong gate) → `WorkflowError`;
  - reject without a reason → validation error;
  - audit: one `Human` event per decision, `Workflow` events for SAR and manual review;
  - metrics arithmetic on hand-built records (cost avoided, approval rates).
- **Depends on:** 4.1–4.5

### Step 4.7 – Integration notebook (`notebooks/03_workflow_integration.ipynb`)
- **Purpose:** demonstrate the full flow on real cases, including human decisions.
- **Output:** about 8 real cases (the planted patterns plus clean and zero-activity customers);
  `ScriptedReviewer` by default, with a switch to `ConsoleReviewer` to decide yourself. Shows the
  status of each case, one complete SAR document, the audit trail for one case end to end, and the
  metrics table with cost avoided. Roughly 8 risk calls + 3–4 narratives ≈ **$0.03**.
- **Depends on:** 4.1–4.5

## 6. Files created or changed

```
src/foundation_sar.py      (HumanDecision, SARDocument, AgentType += "Workflow")
src/sar_workflow.py        (new)
src/metrics.py             (new)
tests/test_workflow.py     (new; tests/test_foundation.py gains schema tests for 4.1)
notebooks/03_workflow_integration.ipynb (new)
project_status.md          (also: mark Stage 02/03 as merged, PR #4 / PR #5)
```

## 7. Decisions (resolved in review, 2026-11-17 — all recommendations accepted)

1. **Batch speed:** sequential in Stage 04; a `max_workers` option only if Stage 05 needs it.
2. **PII in the SAR document:** name, DOB and address go into the SAR JSON (git-ignored `outputs/`);
   only `ssn_last_4` exists; the audit log stays redacted.
3. **Compliance reviewer:** approve or reject (with reason) only; editing is a possible Stage 05 feature.
4. **Low-risk cases:** every analysed case goes to the human gate; the demo `ScriptedReviewer`
   rejects `Low` and approves the rest.
5. **Step order:** tests (4.6) before the notebook (4.7).
