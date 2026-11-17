# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project goal

@project_goal.md

## Workflow rules

1. **Plan first:** before every step, write a detailed plan doc under `plan/` that explains each topic clearly. Keep plans organized by stage in numbered folders that follow the phases in `project_goal.md`, e.g. `plan/00-master/`, `plan/01-foundation/`, `plan/02-risk-analyst-agent/`. Plans have steps with a clear purpose, output and depends_on.
   Add new stage folders whenever needed, keeping the numeric prefix and never mixing topics.
   Each stage's `plan.md` opens with a header: `Status:` (e.g. `DRAFT – awaiting review`), the proposed branch, a link to the master plan, and `Depends on:` earlier phases.
2. **Review:** ask the user to review each plan doc before implementing it.
3. **Permission:** always ask for permission before making any change to code.
4. **Phased implementation:** start with the master plan, then plan the code phase by phase and step by step, stating each step's purpose and dependencies.
5. **Commits, new branch creation PRs, merges:** always ask for permission before every commit, new branch creation, pull request, and merge.
6. **Branch naming:** use professional, descriptive names prefixed by type, e.g. `feature/foundation-schemas`, `feature/risk-analyst-agent`, `fix/llm-json-parsing`, `chore/cloudrun-dockerfile`, `docs/master-plan`.
7. **Phase comments in code:** mark which phase/step produced the code, at the top of each new file and on each new function/class, for future reference:
   ```python
   # Phase 2 – Step 3: <purpose of this code>
   ```
8. **Learning-oriented:** this project is a hands-on learning exercise. Go step by step, explain new concepts as they come up, and never build everything in one go.
9. **Sync before new work:** `git switch main && git pull` (Git Bash; in PowerShell 5.1, which has no `&&`: `git switch main; if ($?) { git pull }`) (the repo is configured with `pull.ff=only` and `fetch.prune=true`), confirm `git status` says "up to date with 'origin/main'", then create the new branch from `main`. After a PR is merged: `git switch main && git pull && git branch -d <merged-branch>`.
10. **GitHub activities:** do not perform any GitHub-related activities (push, opening/merging PRs, remote branch operations); the developer performs them manually. Always provide the exact commands to execute. Local git (switch, branch, pull, commit with permission per rule 5) is fine.

## Current state

Per-step progress lives in `project_status.md` (imported at the end of this file); update it, not
this section. Master plan: `plan/00-master/plan.md`.
`.env` holds a real API key and is git-ignored (commit `.env.example` instead). Audit logs
(`*.jsonl`, `logs/`) and `outputs/` are also ignored. Reuse `tests/conftest.py` (sample-record
fixtures) and `tests/fakes.py` (`FakeLLMClient`, openai error builders) in every stage.

## Commands

Python 3.11 in a local `.venv`, run from the repo root. Ruff and pytest are configured in
`pyproject.toml` (pytest sets `pythonpath=["."]`, so tests import `src.<module>`); settings are read
from `.env` via `src/config.py`. Shell lines are for
Git Bash; in PowerShell 5.1 replace `&&` with `;` and activate with `.venv\Scripts\Activate.ps1`.

```bash
py -3.11 -m venv .venv && .venv\Scripts\activate
pip install -r requirements-dev.txt          # runtime deps + pytest + ruff
copy .env.example .env                       # then fill in values

ruff check .                                 # lint: line length 100, rules E,F,I,B,UP
pytest                                       # unit tests; `llm`-marked tests are skipped by default
pytest tests/test_foundation.py::test_name   # single test
pytest -m llm                                # opt in to tests that call the real LLM
streamlit run app/streamlit_app.py           # Phase 5 review UI (http://localhost:8501)
```

`.env` keys: `OPENAI_API_KEY`, `OPENAI_BASE_URL` (OpenAI-compatible endpoint — pass it to the client,
don't assume api.openai.com), `MODEL` (agents), `JUDGE_MODEL` (Phase 8 LLM-as-judge; must differ
from `MODEL`; not in `.env` yet), `PROMPT_VERSION` (record it in audit logs), and
`GCP_PROJECT_ID`, `GCP_PROJECT_NUMBER`, `REGION`, `GIT_REPO` for Phase 7 Cloud Run deployment.

## Architecture

Two-stage, human-gated pipeline (see `project_system_arch.png`):

```
project_data/*.csv → DataLoader → CaseData
  → RiskAnalystAgent (Chain-of-Thought) → RiskAnalystOutput
  → Risk Review Gate (human)  ── rejected ──→ ExplainabilityLogger
  → ComplianceOfficerAgent (ReACT) → ComplianceOfficerOutput
  → Compliance Review Gate (human) ── rejected ──→ ExplainabilityLogger
  → SAR document (JSON)
```

- The compliance agent only runs on cases a human approved at the risk gate — this is the
  "cost optimization" the project must demonstrate, so keep the stages separable and track per-stage
  token/cost usage.
- `ExplainabilityLogger` writes JSONL and receives events from both agents **and** both gate
  decisions (approvals and rejections) — the audit trail is a regulatory deliverable.
- Workflow logic lives in `src/sar_workflow.py` (shared by the notebook, Streamlit UI and tests);
  `notebooks/03_workflow_integration.ipynb` only demonstrates it.
- `src/foundation_sar.py` holds all Pydantic schemas + `DataLoader` + `ExplainabilityLogger`.
  Not yet created: `src/sar_workflow.py`, `notebooks/03_*.ipynb`, `tests/test_compliance_officer.py`.
- Output contracts: risk classification ∈ {Structuring, Sanctions, Fraud, Money_Laundering, Other},
  confidence 0.0–1.0, risk level ∈ {Low, Medium, High, Critical}. SAR narrative must be **fewer than
  120 words** (`word_count < 120`; overrides the generic 100–200 guidance), must answer
  who/what/when/where/why, and cite BSA/AML regulations.

### Shared building blocks (used by both agents)

- `src/llm_client.get_llm_client()` builds the OpenAI client from `OPENAI_BASE_URL`. `src/llm_utils`
  holds `call_with_retry` (exponential backoff on transient errors), `extract_json`, `add_usage`
  (token totals for cost tracking) and `DEFAULT_SEED=42`. New agents should use these rather than
  writing their own retry or parse logic.
- `src/case_formatter` masks real transaction IDs to `T001…` (real prefixes such as `STRUCT_` leak
  the answer) and drops PII before anything reaches the LLM. `remask`/`unmask` convert between real
  IDs and refs: risk findings are re-masked going into the compliance agent, and its reasoning is
  unmasked coming out. Never send raw IDs to a model.
- Prompts are versioned files `src/prompts/<agent>_V<n>.md`, loaded with `load_prompt()`. A
  behaviour change means a new `V<n>` file, not an edit to the old one.

### Risk Analyst (`src/risk_analyst_agent.py`)

Chain-of-Thought, single call: `temperature=0`, `seed=42`, JSON mode, one repair retry on malformed
output, short-circuit for zero-activity cases; raises `RiskAnalysisError` on final failure.

### Compliance Officer ReACT loop (`src/compliance_officer_agent.py`)

A real function-calling loop, not text parsing. Each turn uses `tool_choice="required"`, and
`ComplianceToolbox` (`src/compliance_tools.py`) runs one of 4 tools. Bad arguments come back to the
model as error observations instead of exceptions. `submit_narrative` is accepted only when every
deterministic check in `src/narrative_checks.py` passes (word count < 120, the 5 W's, banned terms,
no internal IDs, dates and amounts that match the case, citations from the `src/regulations.py`
allowlist). A rejection is sent back to the model so it can revise. If the step budget runs out,
the agent raises `ComplianceError`, and Stage 04 sends that case to manual review.

### Data (`project_data/`, not `data/` as `project_goal.md` says)

- `customers.csv` (150 rows) ← `accounts.csv.customer_id` (178 rows) ← `transactions.csv.account_id`
  (4,268 rows). Account IDs are `CUST_####_ACC_n`.
- A `CaseData` is one customer + their accounts + those accounts' transactions.
- Expect messy values the schemas must tolerate: most `counterparty` and many `location` values are
  blank, `transaction_type` and `method` can disagree (e.g. `Online_Transfer` via `ATM`), and
  `average_monthly_balance` can be `0.0` on accounts with large balances.

## Known issues in `project_goal.md`

The Phase 3 section first repeats the Phase 2 (`risk_analyst_agent.py`) checklist under the wrong
heading; the real Phase 3 deliverables are under "Phase 3 Deliverables: Compliance Officer Agent".

## Project status
@project_status.md
