# Stage 09 – Walk-through fixes (before Stage 07)

- **Status:** APPROVED – ready to implement (Step 9.1 next)
- **Branch:** `fix/walkthrough-findings`, from `main` after the Step 5.8 PR (`docs/stage-05-walkthrough`)
  is merged
- **Master plan:** [`plan/00-master/plan.md`](../00-master/plan.md)
- **Depends on:** Stage 05 (UI, walk-through 5.8), Stage 06 (CI/CD live on Cloud Run)
- **Source:** the 6-item fix list in `project_status.md` → Stage 05 → "Fix list (proposed)",
  developer decision 2026-11-17: **do all six before Stage 07**
- **Numbering:** `09` is the next free folder number. The stages are numbered in the order they
  were *planned*; this one is *done* before Stage 07.

---

## 1. Goal of this stage

Fix what the live walk-through exposed. Grouped by how much they touch:

| # | Finding | Fix | Size |
|---|---|---|---|
| 1 | A placeholder in a GitHub variable deployed fine, then every LLM call failed | CD validates its variables before deploying | small |
| 2 | Runbook block 8 invited copying placeholder text by hand | block 8 reads the values from `.env` itself | small |
| 3 | Git Bash crash dumps appeared in the repo root | `.gitignore` gets `*.stackdump` | small |
| 4 | The SAR's activity section (14 txns / $32,719.92) didn't match the narrative (9 wires / $17,414.86) | the narrative *declares* the transactions it relies on; the SAR lists exactly those | medium |
| 5 | Approvals were audited with no reason ("approved") | a reason is required at **both** gates, for approve and reject | medium |
| 6 | CUST_0003 (clean) rated Money_Laundering / Medium twice → false-positive SAR | `V2` risk prompt, calibrated, checked by a small before/after evaluation | medium + live check |
| 7 | Every merge to `main` redeployed, even docs-only (the `workflow_run` trigger has no path filter) → ended review sessions and wiped temporary audit/SAR files | CD builds and deploys only when the image inputs changed (developer decision 2026-11-17: this item only, no GitHub "environment") | small |
| 8 | CI ran lint + tests on docs-only changes (`plan/`, `project_status.md`, …) → wasted minutes, slower docs PRs (added by Claude per developer request, 2026-11-19) | CI skips lint/test when every changed file is docs-only (`changes` job, reusable `ci-docs-skip` skill) | small |
| 9 | CD's decision had gaps: a failed deploy followed by a docs-only merge was never deployed (`HEAD^1..HEAD`), a rebase-merge could hide changes, and the image-input list existed twice (`paths:` + `IMAGE_INPUTS`) (added by Claude per developer request, 2026-11-19) | one `changes` job for every CD trigger that diffs against the **live Cloud Run version**; separate `cd-deploy-skip` skill + `cd-scope-auditor` subagent | small |

**Done when:**
- All nine are fixed, with tests, and CI is green.
- The `V2` evaluation shows clean customers no longer escalated, while the planted patterns still are.
- The new version is deployed through CD and spot-checked on the live app.

## 2. Concepts introduced

- **Validate configuration at the boundary:** check settings where they *enter* the system (the
  deploy step) rather than discovering them at the first user click.
- **Explicit grounding (claims → evidence):** the compliance agent must name the transactions
  (`T###`) its narrative is built on. Code checks they exist, and the SAR lists exactly those. This
  is the same pattern as "the model proposes, code decides" from Stage 03.
- **Prompt versioning in practice:** a behaviour change means new `V2` files, not edits to `V1`. Old
  audit records still say which prompt produced them, and `V1` stays available for comparison.
- **Before/after evaluation:** run `V1` and `V2` on the same labelled cases and compare. This is a
  first, small taste of Stage 08.

## 3. Steps

### Step 9.1 – Small fixes (#1, #2, #3, #7)
- **Purpose:** close the config-error class of bug that cost us the first live attempt, and stop
  redeploying (ending sessions, wiping temporary files) for merges that don't change the image.
- **Output:**
  - `cd.yml`: a new first step in the deploy job, **"Validate deployment variables"**. It fails
    with a clear message when:
    - any of the 11 required variables is empty or contains `<`, `>` or quote characters
    - `OPENAI_BASE_URL` doesn't start with `https://`
    - `PROMPT_VERSION` doesn't match `V<number>`
  - `gcp_setup.md` block 8: `OPENAI_BASE_URL`, `MODEL` and `PROMPT_VERSION` are read from `.env`
    with quotes stripped, and printed for a visual check before `gh variable set`.
  - `.gitignore`: `*.stackdump`.
  - `cd.yml` (#7): a new first job, **`changes`** ("Did the image inputs change?"). After CI on
    `main` it runs `git diff --name-only HEAD^1 HEAD` over the image inputs (`Dockerfile`,
    `.dockerignore`, `requirements.txt`, `app/`, `src/`, `project_data/`, `.streamlit/`, `cd.yml`).
    If nothing changed, `build` and therefore `deploy` are skipped, and the job summary says
    "Nothing to deploy". PR runs (already path-filtered) and manual "Run workflow" runs always
    continue; the manual run covers config-only fixes. Two comments in `cd.yml` record that the
    path list must match the PR filter, and that the check assumes merge-commit merges
    (`HEAD^1..HEAD` covers the whole PR).
- **Depends on:** –

### Step 9.1b – CI skips docs-only changes (#8)
- **Purpose:** don't spend CI minutes (and reviewers' waiting time) on changes that cannot affect
  lint or tests: the project-management docs.
- **Concepts:**
  - **Required checks vs path filters:** a workflow skipped by `paths-ignore` never reports, so if
    "Lint and test" is a required check, a docs-only PR waits forever. A *job* skipped by `if:`
    reports as passed. So the workflow always starts (seconds), but only the expensive job is skipped.
  - **Fail open:** whenever the diff can't be trusted (new branch, failed diff, empty diff,
    other events), CI runs.
  - **Reusable knowledge in Claude Code:** the pattern lives in a user-level **skill**
    (`~/.claude/skills/ci-docs-skip/`: playbook, YAML template, filter script + tests) and a
    read-only **subagent** (`~/.claude/agents/ci-scope-auditor.md`) that proposes a repo's
    docs-only list with evidence. Both work in any project; only the list is repo-specific.
    Identical copies are committed in the repo's `.claude/` (`deb5488`). For **skills the
    `~/.claude` copy wins**, for **subagents the repo copy wins**, so edit both together.
    A `/ci-docs-skip` re-run (2026-11-19) had the subagent re-audit TRACE: pattern confirmed.
- **Output:**
  - `ci.yml`: a new first job **`changes`** ("Code or docs-only change?") from the skill template.
    It lists changed files (PR: `base.sha...head.sha`; push: `before..sha`) and drops docs-only paths:
    `^plan/` · `^notebooks/` (ruff excludes them, no test reads them) · `^\.claude/` (Claude Code skills/agents: no `.py`, nothing reads them) · root-level `*.md` only
    (`^[^/]+\.md$` → `src/prompts/*.md` still triggers CI: prompts are code) · root-level `*.png` only (`^[^/]+\.png$`; a PNG under `app/` would be a runtime asset).
    The job summary says "Docs-only change – lint/test skipped".
  - `test` job: `needs: changes`, `if: needs.changes.outputs.code == 'true'`; job **name
    unchanged**, so any required-check setting keeps matching.
  - `cd.yml`: no change. On a docs-only merge CI still ends `success`, CD starts, and its own
    `changes` job (#7) stops at "nothing to deploy".
  - `CLAUDE.md` CI bullet: the docs-only list + "pattern: `ci-docs-skip` skill".
- **Verification:** the skill's `test_docs_only.sh` with TRACE's regex (11 cases incl.
  `src/prompts/*.md` → run, `planner/` → run); then on GitHub this branch's PR runs "Lint and
  test", and a docs-only PR shows it **Skipped** and is mergeable.
- **Depends on:** 9.1 (same CI/CD area; the CD `changes` job is the pattern being reused)

### Step 9.1c – CD deploys only when image inputs changed vs the live version (#9)
- **Purpose:** give CD the same "skip when irrelevant" behaviour as CI, without the gaps of the
  fix #7 version, and package the know-how separately from CI.
- **Concepts:**
  - **Allow list vs ignore list:** CI asks "is anything *not* docs?" (ignore list). CD asks
    "did anything that goes *into the image* change?" (allow list from the Dockerfile `COPY`
    lines). A `tests/` change runs CI but must not redeploy.
  - **Diff against what is running:** the Cloud Run image tag is the commit SHA, so CD compares
    the *live* commit with the *new* one. A failed deploy is retried by the next merge, even a
    docs-only one, and any merge style (merge, squash, rebase) is covered.
  - **Fail open = deploy:** tag unreadable or not a SHA, unknown commit, diff error, empty diff →
    deploy. Live == new → nothing to deploy (re-runs are free).
  - **One list, one gate:** no workflow-level `paths:` copy; the `changes` job decides for PRs,
    after-CI runs and manual runs (manual = always deploy).
  - **Separate Claude Code pieces:** skill `cd-deploy-skip` (playbook, template, filter script +
    tests) and read-only subagent `cd-scope-auditor` (derives the image-input list with
    evidence). They mirror the CI pair but don't share files, so neither one confuses the other.
- **Output:**
  - `.claude/skills/cd-deploy-skip/`, `.claude/agents/cd-scope-auditor.md` (+ identical
    `~/.claude` copies); one cross-reference line in `ci-docs-skip/SKILL.md`.
  - `cd.yml`: `pull_request.paths:` removed; the `changes` job replaced by the skill template.
    It uses WIF auth (existing `DEPLOYER_SA`, which has `run.admin`, so no GCP changes) only on the
    after-CI path, reads the live revision's image tag, diffs `live..new` over
    `IMAGE_INPUTS_REGEX`, and writes "Live … → new …" to the job summary. Output name `image`
    unchanged, so `build`/`deploy` stay as they are.
  - `CLAUDE.md` CD bullet updated.
- **Verification:** the skill's `test_inputs_changed.sh` with TRACE's cases; the `run:` block
  simulated on real commits with a stubbed `gcloud` (deploy / skip / live == new / unreadable tag /
  PR docs-only / PR `tests/`-only / manual); then on GitHub, after the merge CD logs "Live … →
  new …" and deploys, and a later docs-only merge logs "nothing to deploy".
- **Depends on:** 9.1 (CD `changes` job), 9.1b (CI pattern, skill/subagent structure)

### Step 9.2 – Reason required at both gates (#5)
- **Purpose:** the audit trail must explain *why* a case went on, not only why one was dropped.
- **Output:**
  - `HumanDecision`: `reason` is required (at least 10 characters after trimming) for approvals
    as well as rejections. The error message names the gate.
  - The UI forms say "Reason (required)".
  - `ScriptedReviewer` writes a policy reason, e.g. *"Scripted policy: risk level High – escalate
    for narrative."* `ConsoleReviewer` asks for a reason on approval too.
  - Tests updated: about 10 places that approve without a reason, plus new tests for a missing or
    too-short reason at each gate.
- **Depends on:** –

### Step 9.3 – Compliance prompt V2 + declared supporting transactions (#4)
- **Purpose:** the SAR's activity section and the narrative tell the same story.
- **Output:**
  - `submit_narrative` gains a **required** argument `transaction_refs` (`T###` list, 1–30).
    Unknown refs are rejected as an observation, so the model can fix them, just like other check
    errors.
  - `ComplianceOfficerOutput` gains `supporting_transaction_ids`: the declared refs converted back
    to real IDs.
  - `involved_transactions()` returns exactly those transactions. It falls back to the old logic
    only for outputs without them, such as V1 audit records.
  - **`src/prompts/compliance_officer_V2.md`:**
    - explains `transaction_refs`: list the transactions the narrative's facts come from
    - "the totals you quote must be computable from those transactions"
    - the Stage 03 finding: **"after a passing `check_narrative`, call `submit_narrative`
      immediately"**, which cuts redundant steps and cost
  - Tests: required argument, unknown refs, unmasking, the SAR listing exactly the declared
    transactions, and the activity total matching them.
- **Depends on:** 9.2 (both change audited contracts; one migration of the tests)

### Step 9.4 – Risk prompt V2 (calibration) + before/after evaluation (#6)
- **Purpose:** stop escalating ordinary activity.
- **Output:**
  - **`src/prompts/risk_analyst_V2.md`**, the V1 text plus calibration rules:
    - compare activity against **balances and average monthly balance** as well as income
    - ordinary descriptions (utility bills, payroll, investment transfers, card purchases) are
      **not** red flags on their own
    - **Medium or higher needs at least one concrete, named red-flag pattern**, citing its `T###`
      refs (structuring near $10k, rapid in-and-out, unusual geography, sudden large cash, high
      frequency of small transfers, …)
    - otherwise `Other` / `Low`
  - `PROMPT_VERSION` default → `V2` in `src/config.py`, `.env.example` and your `.env`. Both
    agents load the same version, which is why 9.3 and 9.4 ship together as **V2**.
  - **Evaluation script** `scripts/compare_prompt_versions.py` (opt-in; costs money):
    - runs the Risk Analyst with V1 and V2 on the 5 planted-pattern customers (STRUCT, LARGE,
      RAPID, GEO, FREQ) and 5 clean customers, including CUST_0003 and CUST_0002
    - prints a table: case, hidden label, V1 class/level, V2 class/level, tokens
    - about 20 calls, roughly **$0.02**; you run it, or I run it with your OK
  - **Acceptance:** all 5 clean customers `Low` under V2; at least 4 of 5 planted patterns still
    `Medium` or higher. Results are recorded in `project_status.md`.
- **Depends on:** 9.3

### Step 9.5 – Ship and verify
- **Purpose:** get V2 live.
- **Output:**
  - The GitHub variable `PROMPT_VERSION` set to `V2` (you; block 8 now does it from `.env`).
  - The PR merged, which deploys through CI → CD.
  - A short live check by you: CUST_0003 should now come back Low; a planted case still escalates
    and gets a narrative whose SAR activity matches it.
  - `project_status.md` updated; fix-list items marked done.
- **Depends on:** 9.1–9.4

## 4. Files created or changed

```
.github/workflows/cd.yml  plan/06-cicd-gcp/gcp_setup.md  .gitignore                (9.1)
.github/workflows/ci.yml  CLAUDE.md  .claude/skills/ci-docs-skip  .claude/agents/ci-scope-auditor.md (9.1b)
.github/workflows/cd.yml  CLAUDE.md  .claude/skills/cd-deploy-skip  .claude/agents/cd-scope-auditor.md (9.1c)
src/foundation_sar.py  src/sar_workflow.py  app/views/*_review.py  app/state.py    (9.2)
src/compliance_tools.py  src/compliance_officer_agent.py  src/prompts/compliance_officer_V2.md (9.3)
src/prompts/risk_analyst_V2.md  src/config.py  .env.example  scripts/compare_prompt_versions.py (9.4)
tests/*  (updated + new)  project_status.md  CLAUDE.md
```

## 5. Decisions (resolved in review, 2026-11-17 — all recommendations accepted)

1. **Reason on approval:** required in the schema (≥ 10 characters) at both gates.
2. **Supporting transactions:** required `transaction_refs` argument on `submit_narrative`; code
   verifies the refs and the SAR lists exactly those transactions.
3. **Versions:** one switch to `V2` for both agents (default `V2`); `V1` files kept.
4. **Evaluation:** the ~$0.02 V1-vs-V2 comparison runs before merging.
5. **Checks:** GitHub Actions remains the code check; the comparison script runs locally (CI has no
   API key).
