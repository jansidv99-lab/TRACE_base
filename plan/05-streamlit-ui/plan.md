# Stage 05 – Streamlit Review UI

- **Status:** APPROVED – ready to implement (Step 5.1 next)
- **Branch:** `feature/streamlit-review-ui`
- **Master plan:** [`plan/00-master/plan.md`](../00-master/plan.md)
- **Depends on:** Stage 04 (`SARWorkflow`, gates, SAR generation, metrics; PR #6), and through it
  Stages 01–03
- **Requirements:** `project_goal.md` → "Phase 5: UI design – use Streamlit"; Phase 4 "Human Decision
  Gates: interactive approval/rejection points"

---

## 1. Goal of this stage

A browser app where a compliance team works the real two-stage process:

```
Case queue ─► run risk analysis ─► Risk review (approve / reject + reason)
          ─► generate narrative ─► Compliance review (approve / reject + reason)
          ─► SAR viewer + download ─► audit trail ─► metrics
```

The app is a **thin layer over `src/sar_workflow.py`**: every button calls an existing workflow method,
so the state machine, gate rules, audit events and SAR validation from Stage 04 apply unchanged. The
UI adds no business logic of its own.

**Done when:** `streamlit run app/streamlit_app.py` lets one person take a real case from the queue
to a downloaded SAR (or a logged rejection), every decision appears in the audit trail, the metrics
panel shows cost per stage and cost avoided, and headless UI tests pass with fake agents.

## 2. Facts carried in from Stage 04

- Latency: risk ~9 s per case, compliance ~24 s → every model call needs a visible spinner, and batch
  runs need a progress bar and a cost estimate first.
- The Stage 04 demo filed a SAR for a clean customer (CUST_0003) because a *scripted* reviewer
  approved every non-`Low` finding. The review screens must give a person enough evidence (the
  transactions behind each indicator, the narrative checks) to reject that kind of false positive.
- `ScriptedReviewer` and `ConsoleReviewer` stay for notebooks and tests; the UI produces
  `HumanDecision`s directly from form submissions.

## 3. Concepts introduced in this stage

- **Streamlit's rerun model:** every click reruns the whole script from the top. Anything that must
  survive a click (the workflow and its case records) lives in `st.session_state`.
- **Caching:** `st.cache_resource` keeps one shared object across reruns and sessions (the LLM
  client, the loaded cases); session state keeps per-user objects (the workflow).
- **Forms:** `st.form` groups inputs so a decision is submitted once, on purpose, not on every
  keystroke.
- **Multipage apps:** `st.navigation` + `st.Page`, one file per screen.
- **Headless UI testing:** `streamlit.testing.v1.AppTest` runs the app without a browser; tests click
  buttons, fill forms and read what is rendered. Fake agents are injected through session state.
- **Thin UI / fat core:** formatting helpers are plain functions (unit-testable without Streamlit);
  pages only lay things out and call the workflow.

## 4. Design overview

```
app/
├── streamlit_app.py      # entry point: page config, sidebar, navigation, workflow bootstrap
├── state.py              # get_workflow(), cached client/cases, reviewer id, session helpers
├── ui_helpers.py         # pure functions → DataFrames / dicts for display (no streamlit import)
└── views/
    ├── queue.py          # 5.2 case queue + run risk analysis
    ├── risk_review.py    # 5.3 risk gate
    ├── compliance_review.py  # 5.4 narrative + compliance gate + manual-review list
    ├── sars_audit.py     # 5.5 SAR viewer/download + audit trail
    └── metrics.py        # 5.6 metrics panel
.streamlit/config.toml    # headless, usage statistics off
```

**Bootstrap:** on first run, `get_workflow()` builds the real agents (shared `ExplainabilityLogger` at
`logs/sar_audit.jsonl`, SARs to `outputs/`) and stores the `SARWorkflow` in `st.session_state`. If
the session already holds a workflow (as tests arrange), it is used as is, which is how fake agents get in.

**Sidebar (every page):** reviewer ID (required before any decision), model and prompt versions, and
this session's running cost.

## 5. Steps

### Step 5.1 – Setup and app shell
- **Purpose:** a running app skeleton with the workflow wired in.
- **Output:** `streamlit` pinned in `requirements.txt` (runtime: Stage 06 deploys this app);
  `.streamlit/config.toml`; `app/streamlit_app.py` with `st.navigation` and the sidebar;
  `app/state.py` (`get_workflow`, cached client and cases, reviewer-ID helper); one AppTest smoke test.
- **Depends on:** Stage 04

### Step 5.2 – Case queue
- **Purpose:** see every case and start Stage 1.
- **Output:** a table of all 150 cases: customer, risk rating, accounts, transactions, status,
  classification and risk level once known. Filter by status. Actions: **analyze one selected case**,
  or **analyze the next N new cases** (N ≤ 10, with estimated cost shown first and a progress bar).
  Planted transaction-ID prefixes are **never shown** (they are the answer key).
- **Depends on:** 5.1

### Step 5.3 – Risk review screen
- **Purpose:** the human risk gate, with the evidence needed to reject a false positive.
- **Output:** a picker over `RISK_REVIEW` cases. For the chosen case: customer profile (no DOB/SSN/
  address on screen), accounts, transactions table with the **transactions cited in the key indicators
  highlighted**, classification / risk level / confidence, the Chain-of-Thought reasoning by step and the
  key indicators. A form: approve or reject, reason (required to reject). Validation errors from
  `HumanDecision` / `WorkflowError` are shown as messages, not tracebacks.
- **Depends on:** 5.2

### Step 5.4 – Compliance review screen
- **Purpose:** generate narratives for approved cases and run the compliance gate.
- **Output:** three sections:
  - `RISK_APPROVED` cases with a **Generate narrative** button (spinner; ~24 s).
  - `COMPLIANCE_REVIEW` cases: narrative, word count, **5-W checklist and warnings** (re-running the
    Stage 03 checks), citations, the ReACT trace in an expander, and an approve / reject form.
  - `MANUAL_REVIEW` cases with the agent error, so nothing disappears silently.
- **Depends on:** 5.3

### Step 5.5 – SAR and audit views
- **Purpose:** the regulatory deliverables.
- **Output:** a list of filed SARs, each rendered by section (subject, activity, narrative, reviews,
  provenance) with the raw JSON and a **download button**; an **audit trail** table for any case, read
  from the JSONL file (new small helper `read_audit_events(path, case_id)` in `src/foundation_sar.py`).
- **Depends on:** 5.4

### Step 5.6 – Metrics panel
- **Purpose:** show the cost optimization live.
- **Output:** `st.metric` tiles (cases, SARs filed, total cost, cost avoided, savings rate, approval
  rates) and the per-stage table from `WorkflowMetrics.stage_rows()`; prices labelled as assumptions.
- **Depends on:** 5.2

### Step 5.7 – UI tests
- **Purpose:** prove the screens drive the workflow correctly, offline.
- **Output:** `tests/test_streamlit_app.py` using `AppTest` with stub agents in session state:
  app starts on every page; analyzing a case moves it to risk review; approve and reject (with and
  without reason) at both gates; generate narrative; SAR appears with a download button; audit table
  shows the case's events; metrics render. Plus unit tests for `app/ui_helpers.py`.
- **Depends on:** 5.1–5.6

### Step 5.8 – Manual walk-through
- **Purpose:** check the real experience with real model calls.
- **Output:** a short checklist in this plan (run the app, analyze 3 cases including CUST_0003, reject
  one with a reason, file one SAR, download it, check the audit trail and metrics). I run it with you
  and record findings in `project_status.md`. Cost: a few cents.
- **Depends on:** 5.7

## 6. Files created or changed

```
requirements.txt               (+ streamlit, exact pin)
.streamlit/config.toml         (new)
app/streamlit_app.py  app/state.py  app/ui_helpers.py  app/views/*.py   (new)
src/foundation_sar.py          (+ read_audit_events)
src/sar_workflow.py            (only if Q2 is accepted: separate reviewers)
tests/test_streamlit_app.py  tests/test_ui_helpers.py                  (new)
CLAUDE.md                      (replace "entry point TBD" with the real command)
```

## 7. Decisions (resolved in review, 2026-11-17 — all recommendations accepted)

1. **Workflow state:** browser session only (`st.session_state`); audit log and SAR files persist on
   disk. Durable shared state is a later topic (Stage 06 deploy / Stage 08).
2. **Segregation of duties:** enforced in `SARWorkflow.record_compliance_decision` — the compliance
   reviewer ID must differ from the risk reviewer ID (applies to UI, notebooks and tests).
3. **Batch analysis:** "analyze next N" with N ≤ 10 and the estimated cost shown first.
4. **Reviewer identity:** free-text reviewer ID in the sidebar, no login until Stage 06 (CI/CD) or later.
5. **Steps:** setup (5.1), UI tests (5.7) and a manual walk-through (5.8) added to the five screens.
