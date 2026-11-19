---
name: ci-docs-skip
description: Make GitHub Actions CI skip lint/test when a change only touches docs or project-management files (plans, status docs, README-style markdown, images, notebooks). Use when CI "runs on every change", when asked to skip CI for docs, add path filters to a workflow, or restrict CI to relevant files. Safe with required status checks.
---

# CI skips docs-only changes

Goal: CI still starts on every PR/push, but the expensive job (lint, tests, build) is **skipped**
when every changed file is docs-only. Deterministic, free, no secrets — never use an LLM in CI
to decide relevance.

Scope: **CI only** (lint/test). For deploy workflows (build/push/deploy only when image inputs
changed) use the separate **`cd-deploy-skip`** skill — different question, different list.

## 1. Decide the repo's docs-only list (repo-specific — never guess)

Delegate the analysis to the **`ci-scope-auditor`** subagent ("propose the docs-only list for this
repo"). Review its evidence table before using the regex. Rules it must respect:

- Only paths that **no linter, test, build or runtime step reads** are docs-only.
- Markdown used at runtime (prompt files, templates, `src/**/*.md`) is **code**. Match root-level
  markdown only: `^[^/]+\.md$`, never `\.md$`.
- Same for images: `^[^/]+\.png$`, not `\.png$` — `app/static/*.png` is a runtime asset.
- Escape the dot (`\.md`): an unescaped `.` matches any character. Watch for shell/sed edits
  that silently drop backslashes; the cases file has literal-dot cases (`cmd`, `buildmd`) to catch it.
- Excluded from the linter **and** never read by tests (e.g. `notebooks/`) → docs-only.
- When in doubt, leave it out of the list (CI runs — the safe failure).

Record the final list in the project's `CLAUDE.md` (one line in its CI section), so the next
session doesn't re-derive it.

## 2. Use a `changes` job — not workflow-level `paths-ignore`

If the CI job is a **required status check**, a workflow skipped by `paths`/`paths-ignore` never
reports, and the PR waits forever on "Expected — waiting for status". A **job skipped by `if:`**
reports as passed. So:

- Add the `changes` job from `templates/changes-job.yml`.
- On the existing job: `needs: changes` and `if: needs.changes.outputs.code == 'true'`.
- Keep the existing job's `name:` **unchanged** — required checks match on it.

Plain `paths-ignore:` is acceptable only when the user confirms there is no branch protection.

## 3. Diff rules (already in the template)

- `pull_request`: `git diff --name-only <base.sha>...<head.sha>` (three dots = the PR's own
  changes), checkout with `fetch-depth: 0`.
- `push`: `<before>..<sha>`.
- **Fail open** → `code=true` when: `before` is all zeros (new branch), the diff command fails,
  the diff is empty, or the event is anything else (`workflow_dispatch`, `schedule`).

## 4. Downstream workflows

Workflows triggered by `workflow_run: [CI]` (e.g. CD) still start, because CI ends `success`
with the job skipped. They cannot be path-filtered, so they need their **own** `changes`-style
check (e.g. "did the image inputs change?"). Keep both lists in sync and say so in a comment.

## 5. Verify

1. Locally (Git Bash): `DOCS_ONLY_REGEX='<regex>' bash scripts/test_docs_only.sh <cases-file>`
   (format in `examples/python-repo-cases.txt`). Include a runtime `.md` case that must give `true`.
2. Lint the YAML if `actionlint` is available.
3. On GitHub: a code PR → job runs; a docs-only PR → job shows **Skipped** and the PR is
   mergeable; the `changes` job's summary says "docs-only".

## Files

- `templates/changes-job.yml` — the job + wiring, with a `DOCS_ONLY_REGEX` placeholder.
- `scripts/docs_only.sh` — same filter logic for local testing (stdin: file names → `code=true|false`).
  **Keep its logic identical to the template's `run:` block.**
- `scripts/test_docs_only.sh` + `examples/python-repo-cases.txt` — table-driven tests.
