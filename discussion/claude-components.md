# Discussion – More Claude Code components for TRACE's development cycle

Created: 2026-11-19 by Claude (developer request) · Status: **OPEN – reviewing one by one**

How to use this file: go through S1–S16 and tick one decision on each. Every **accepted** item
then gets its own step in a new stage plan, `plan/10-claude-dev-tooling/plan.md` (proposed branch
`chore/claude-dev-tooling`, started after Step 9.2 is committed), following CLAUDE.md rules 1–5.
Nothing in this file has been implemented yet.

---

## Where we are today

| Component | In the repo now |
|---|---|
| Skills | `ci-docs-skip`, `cd-deploy-skip` (`.claude/skills/`) |
| Subagents | `ci-scope-auditor` (`cd-scope-auditor` planned) (`.claude/agents/`) |
| Project settings (`.claude/settings.json`) | **none**, so there are no permission rules and no hooks |
| MCP servers (`.mcp.json`) | none |
| User-level | status line script, `engineering` plugin |

**The gap:** most of the workflow rules in `CLAUDE.md` exist only as prose. Nothing enforces
plan-first, ask before commits, never push, never read `.env`, never edit an old prompt version,
phase comments, or updating `project_status.md`. They hold only as long as Claude remembers them.

### Concept: which component is for what?

| Component | Who runs it | Good for |
|---|---|---|
| **Permission rules** | the harness | hard *allow / ask / deny* on tools and commands |
| **Hooks** | the harness, on events (before/after a tool, session start, stop) | automatic checks that must happen every time; does not rely on Claude remembering |
| **Skills** | Claude, when the task matches (or you type `/name`) | a repeatable *procedure*: steps, templates, scripts |
| **Subagents** | Claude delegates; the subagent runs in its own context | investigation or review that would flood the main conversation; can be restricted to read-only tools |
| **Output styles** | changes how Claude writes | e.g. teaching mode |
| **MCP servers** | external tools wired into Claude | GitHub, browsers, docs, cloud |

---

## Tier 1: settings that enforce CLAUDE.md (high value, low risk)

### S1 – Permission rules in a project `.claude/settings.json`
- **Why:** turns CLAUDE.md rules 3, 5, 10 and the `.env` secret into hard tool limits.
- **Draft:**
  - `deny`: `Read(./.env)`, `Edit(./.env)`, `Bash(git push:*)`, `Bash(gh pr create:*)`,
    `Bash(gh pr merge:*)`, `Bash(git merge:*)`
  - `ask`: `Bash(git commit:*)`, `Bash(git switch -c:*)`, `Bash(git branch:*)`,
    `Bash(pytest -m llm:*)` (live LLM calls cost money), `Bash(gcloud run deploy:*)`
  - `allow`: `Bash(ruff check:*)`, `Bash(pytest:*)`, `Bash(git status)`, `Bash(git diff:*)`,
    `Bash(git log:*)`, `Bash(gcloud run services describe:*)` (read-only)
  - Then run the built-in `/fewer-permission-prompts` to allow the read-only commands that come up often.
- **Verify:** ask Claude to `git push` or read `.env` → denied. `git commit` → prompts you.
- **Decision:** ☑ accept ☐ reject ☐ later · Notes: accepted 2026-11-19 → planned as Step 10.1 in
  [`plan/10-claude-dev-tooling/plan.md`](../plan/10-claude-dev-tooling/plan.md)

### S2 – Hook `lint-on-edit` (PostToolUse, `Edit|Write` on `*.py`)
- **Why:** runs `ruff check <file>` right after each edit. Errors go straight back to Claude, so they
  are fixed before CI ever sees them.
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

### S3 – Hook `prompt-immutable` (PreToolUse, `Edit|Write` on `src/prompts/*_V*.md`)
- **Why:** enforces the rule "a behaviour change means a new `V<n>`, not an edit". It blocks editing
  a prompt file already tracked by git and replies "create `V<n+1>` instead". Relevant now for 9.3/9.4.
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

### S4 – Hook `phase-header` (PostToolUse, `Write` of a new `*.py` in `src/`, `app/`, `tests/`)
- **Why:** rule 7. Warns when a new file has no `# Phase N – Step M:` line.
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

### S5 – Hook `session-sync` (SessionStart)
- **Why:** rule 9. Each session starts with a printout of the branch, ahead/behind `origin/main`,
  uncommitted files, and the current 🔄 step from `project_status.md`.
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

### S6 – Hook `status-reminder` (Stop)
- **Why:** if `src/` or `app/` changed but `project_status.md` did not, Claude is reminded to update
  it before finishing.
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

> S2–S6 implementation note: scripts go in `.claude/hooks/*.sh` (Git Bash) with small tests, like
> `test_docs_only.sh`, and are configured with the `update-config` skill.

### S7 – Output style "Explanatory" or "Learning" (built-in)
- **Why:** rule 8 (learning-oriented).
  - **Explanatory** adds short "Insight" notes about the concepts and choices behind the code.
  - **Learning** also leaves small `TODO(human)` pieces for you to write yourself.
- **Where:** `.claude/settings.local.json` (a personal choice, not committed). Switch any time with
  `/output-style`.
- **Decision:** ☐ Explanatory ☐ Learning ☐ reject ☐ later · Notes:

---

## Tier 2: procedures for the next stages (skills + subagents)

These follow the pattern already proven by `ci-docs-skip` + `ci-scope-auditor`: the skill holds
the HOW, and the subagent does the read-only investigation.

### S8 – Skill `new-stage-plan` (user-invoked only: `disable-model-invocation: true`)
- **Why:** rule 1 is repeated for every stage. The skill scaffolds `plan/NN-<topic>/plan.md` with the
  required header (Status, branch, master link, Depends on) and steps with
  purpose / output / depends_on. It also adds the stage row to `project_status.md`.
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

### S9 – Skill `prompt-version-bump` (needed for 9.3 / 9.4)
- **Why:** a safe procedure for a new prompt version:
  1. copy `<agent>_V<n>.md` → `V<n+1>.md`; never edit the old file
  2. extend the exact-case `PROMPT_VERSION` guard test
  3. write a short changelog: what changed and why
  4. run the V1-vs-V2 comparison (S11)
- Pairs with the S3 hook.
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

### S10 – Skill `close-step` (user-invoked)
- **Why:** one checklist at the end of every step:
  1. `ruff` + `pytest` green
  2. `/code-review` and `/security-review` on the diff
  3. update `project_status.md`
  4. propose a commit message in the repo format (`type: … (Phase N – Step M)`) and **wait for approval**
  5. **print** the push/PR commands for you to run (rule 10)
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

### S11 – Subagent `prompt-eval-runner` (9.4; seed of Stage 08)
- **Why:** runs V1 and V2 on a fixed case list (planted patterns + the repeat false positive
  CUST_0003) with `seed=42`. Returns a table per version: classification, risk level, confidence,
  tokens, cost, plus the diffs.
- Asks before any live LLM run. The table is stored with the Stage 09 plan as evidence before merging.
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

### S12 – Subagent `sar-fact-checker` (read-only)
- **Why:** automates the manual check from Step 5.8. It verifies every amount, date, count and
  counterparty in a SAR narrative against `project_data/*.csv`, and flags a "why" the data does
  not support.
- Reuses `src/narrative_checks.py` and `DataLoader`. Prototype of the Stage 08 faithfulness grader.
- **Verify:** on `SAR_CUST_0003_20261117131657` it should reproduce the 5.8 findings
  (9 wires / $17,414.86; SAR section total ≠ narrative total).
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

### S13 – Subagent `masking-auditor` (read-only)
- **Why:** masking is a regulatory property and is easy to break by accident. Before a commit, this
  agent checks the diff for any path where raw transaction IDs (`STRUCT_`, `LARGE_`, …) or PII could
  reach an LLM call or a review screen. It checks against `src/case_formatter.py`
  (`remask` / `unmask`) and the UI journey test.
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

---

## Tier 3: optional, when the matching stage arrives

### S14 – Claude in Chrome + `/run` skill for UI walk-throughs (9.5, any UI change)
- Drives the Streamlit app (locally or on Cloud Run) through analysis → both gates → SAR, records a
  GIF and reads the browser console. Replaces the manual walk-through. You type the passcode yourself.
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

### S15 – Git worktrees for experiments
- A separate checkout (`EnterWorktree`, or a subagent with `isolation: "worktree"`), so prompt V2 or
  CD experiments don't touch a branch with uncommitted work, such as `fix/walkthrough-findings` now.
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

### S16 – Read-only CI/CD watching (`gh run watch` / GitHub MCP + Monitor)
- After **you** push, Claude watches the CI/CD run (e.g. the pending `deb5488` check) and reports
  pass/fail with the failing log lines. Pushing, PRs and merges stay with you (rule 10). This needs
  your OK because it is a GitHub *read* activity.
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

### S17 – Scheduled routines (`/schedule`) for Stage 08
- A nightly eval run plus drift report, feeding Stage 08's run log → BigQuery → dashboard → alerts layer.
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

### S18 – Project info in the status line
- Add the current stage/step and ahead/behind `origin/main` to the existing
  `~/.claude/statusline-command.sh`.
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

### S19 – `claude-code-action` (Claude PR review in GitHub Actions)
- Automatic review comments on every PR. **Not recommended now:** it needs an Anthropic API key as a
  repo secret and adds cost. Local `/code-review` (inside S10) covers most of it.
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

---

## Housekeeping

### S20 – Add `discussion/` to the CI docs-only pattern
- `discussion/` is not in the `changes` job pattern
  (`^plan/|^notebooks/|^\.claude/|^[^/]+\.md$|^[^/]+\.png$`), so committing this file would still
  run lint/test. Use the `ci-docs-skip` skill to add `^discussion/` and extend `trace-cases.txt`.
- **Decision:** ☐ accept ☐ reject ☐ later · Notes:

---

## Recommended order (Claude)
1. **S1 + S2 + S3 + S5 + S7**: one small PR. Cheap, enforces the rules, and S3 protects 9.3/9.4.
2. **S9 + S11**: just before Steps 9.3 / 9.4.
3. **S10, S12, S13, S20**: then S4, S6, S8.
4. Tier 3 when the matching stage arrives.
