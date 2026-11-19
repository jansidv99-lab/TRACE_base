# Stage 10 – Claude Code dev tooling

- **Status:** APPROVED – Step 10.1 ready to implement once the open Stage 09 work is committed
  (developer decisions 2026-11-19: keep `ask git branch *`; no `ask` on code edits; finish Stage 09
  first, then branch from `main`). Later steps are added as items in
  `discussion/claude-components.md` are accepted.
- **Branch:** `chore/claude-dev-tooling`, from `main` (see §4: the current branch has uncommitted
  Stage 09 work)
- **Master plan:** [`plan/00-master/plan.md`](../00-master/plan.md)
- **Depends on:** none of the product stages. It changes only Claude Code configuration, not the
  app, CI or CD.
- **Source:** `discussion/claude-components.md`, item **S1** accepted by the developer on 2026-11-19.

---

## 1. Goal of this stage

Make the workflow rules in `CLAUDE.md` **enforced by Claude Code itself**, not only remembered by
the model, and add tooling that speeds up the remaining stages. Each accepted discussion item
becomes one step here.

| Step | Discussion item | Status |
|---|---|---|
| 10.1 Project permission rules (`.claude/settings.json`) | S1 | 📝 this document |
| 10.x … | S2–S20 | pending discussion |

---

## 2. New concept: permission rules

Claude Code checks every tool call against three lists before it runs:

| List | Effect |
|---|---|
| `deny` | the call is refused; Claude sees an error and must find another way, or ask you |
| `ask` | you get a prompt every time, **even in auto mode** |
| `allow` | runs without a prompt |

Key facts (from the official docs, code.claude.com/docs/en/permissions):

- **Order is deny → ask → allow, and the first match wins.** Specificity does not matter: an
  `allow` can never punch a hole in a `deny` or an `ask`.
- **Rules are enforced by the harness, not the model.** CLAUDE.md shapes what Claude *tries* to
  do; these rules decide what is *allowed*. That is the gap S1 closes.
- **Shape:** `Tool(specifier)`. For shell commands, `Bash(git push *)` matches the command text,
  and `*` matches any text. A trailing ` *` also matches the bare command (`git push`).
  This machine has **two** shell tools, `Bash` (Git Bash) and `PowerShell`, so every shell rule is
  written twice.
- **Compound commands are split:** `cd x && git push` is still caught, because each part
  (`&&`, `;`, `|`, …) is checked on its own.
- **File rules use gitignore syntax:** `Read(.env)` matches any `.env` under the repo. A `!` entry
  carves an exception out of the rules listed before it. A `Read` deny also blocks Edit/Write on
  that path, and Claude Code applies it to recognised shell readers such as `cat`, `head`, `sed`.
- **Scope:** `.claude/settings.json` is committed and shared (the project's rules).
  `.claude/settings.local.json` is personal and not committed. A deny at any level beats an allow
  at any other level.
- **Trust:** project `allow` rules only apply after you accept the folder's trust dialog.
  `deny`/`ask` always apply, because they only restrict.

### Limits: what a rule does *not* stop (be honest about it)
A shell rule matches the command *as written*. It is a guard rail, not a security boundary:
- `Bash(git push *)` does not catch `git -C . push` or `git -c x=y push`.
- `Read(.env)` does not stop a Python script that opens `.env` itself. That is intended:
  `src/config.py` must load it for the app and tests to work.
- `pytest *-m llm*` does not catch a notebook or a `python -c` that calls the LLM.

S2–S6 (hooks) can add command-text checks on top of these rules. The real hard boundary would be
the OS sandbox, which is out of scope here.

---

## 3. Step 10.1 – Project permission rules

- **Purpose:** turn the following CLAUDE.md rules into enforced rules:
  - rule 10 (no GitHub activity: push, PRs, remote operations)
  - rule 5 (ask before every commit, new branch and merge)
  - the `.env` secret
  - "ask before spending money" (live LLM tests, GCP changes)
- **Output:** new file `.claude/settings.json` (draft below), plus a short "Permissions" note in
  `CLAUDE.md` and a Stage 10 row in `project_status.md`.
- **Depends on:** none.

### 3.1 Draft `.claude/settings.json`

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "deny": [
      "Read(.env)", "Read(.env.*)", "Read(!.env.example)",
      "Edit(.env)", "Edit(.env.*)", "Edit(!.env.example)",

      "Bash(git push *)",       "PowerShell(git push *)",
      "Bash(gh *)",             "PowerShell(gh *)",
      "Bash(gcloud secrets versions access *)", "PowerShell(gcloud secrets versions access *)"
    ],
    "ask": [
      "Bash(git commit *)",     "PowerShell(git commit *)",
      "Bash(git switch -c *)",  "PowerShell(git switch -c *)",
      "Bash(git checkout -b *)","PowerShell(git checkout -b *)",
      "Bash(git branch *)",     "PowerShell(git branch *)",
      "Bash(git merge *)",      "PowerShell(git merge *)",
      "Bash(git rebase *)",     "PowerShell(git rebase *)",
      "Bash(git reset *)",      "PowerShell(git reset *)",
      "Bash(git tag *)",        "PowerShell(git tag *)",
      "Bash(git pull *)",       "PowerShell(git pull *)",

      "Bash(pytest *-m llm*)",           "PowerShell(pytest *-m llm*)",
      "Bash(python -m pytest *-m llm*)", "PowerShell(python -m pytest *-m llm*)",

      "Bash(gcloud *)",         "PowerShell(gcloud *)",
      "Bash(docker push *)",    "PowerShell(docker push *)"
    ],
    "allow": [
      "Bash(ruff check *)",       "PowerShell(ruff check *)",
      "Bash(pytest *)",           "PowerShell(pytest *)",
      "Bash(python -m pytest *)", "PowerShell(python -m pytest *)"
    ],
    "disableBypassPermissionsMode": "disable"
  }
}
```

### 3.2 Why each rule

| Rule | Why |
|---|---|
| `Read`/`Edit` `.env`, `.env.*`, except `.env.example` | same shape as `.gitignore`; Claude never needs the real key, and the template stays readable |
| deny `git push` | rule 10: the developer pushes |
| deny `gh *` (all of it) | rule 10: no GitHub activity at all. Simpler than listing every write sub-command. Revisit if S16 (read-only CI watching) is accepted |
| deny `gcloud secrets versions access` | would print the API key from Secret Manager |
| ask `git commit / switch -c / checkout -b / branch / merge` | rule 5: permission before each commit, branch and merge |
| ask `git rebase / reset / tag` | history-rewriting or hard-to-undo local operations |
| ask `git pull` | rule 9 sync is fine, but you see the moment the local tree changes |
| ask `pytest … -m llm` | live LLM calls cost money. The `allow pytest *` below does **not** override this, because ask beats allow |
| ask `gcloud *` | GCP is outward-facing (Cloud Run, IAM, budgets); read-only `describe` calls also prompt, which is rare and acceptable |
| ask `docker push` | images reach Artifact Registry only through CD |
| allow `ruff check`, `pytest` | the inner loop runs without prompts (`git status/diff/log` are already built-in read-only) |
| `disableBypassPermissionsMode` | nobody can start a session that skips all these rules |

### 3.3 Known trade-offs (your call during review)
1. **`ask git branch *` also prompts on a plain `git branch` listing.** A prefix rule can't tell
   "list" from "create". Claude can use `git status` / `git rev-parse --abbrev-ref HEAD` for reads.
   Alternative: drop it and rely on `switch -c` / `checkout -b`.
2. **No `ask` on code edits.** Rule 3 ("ask before changing code") could be `ask: Edit(/src/**)`,
   `Edit(/app/**)`, `Edit(/tests/**)`, but then *every* edit prompts, even after you approve a
   plan. Recommendation: leave rule 3 to the plan-review flow, and decide in auto/manual mode.
3. **`allow` lines depend on how commands are typed.** If Claude runs `.venv/Scripts/python -m pytest`,
   the `pytest *` allow doesn't match (the ask/deny rules still work). After a few sessions,
   `/fewer-permission-prompts` (built-in) proposes the exact read-only forms that came up.
4. **Committed vs personal:** deny/ask are project policy → `settings.json` (committed).
   Personal conveniences go in `settings.local.json` (git-ignored), not here.

---

## 4. Branch and order

The current branch `fix/walkthrough-findings` has uncommitted Stage 09 work (9.1c, 9.2), and Stage
10 is a separate topic (CLAUDE.md rule 1: never mix topics). Options:

- **A (recommended, chosen 2026-11-19):** first commit or finish the open Stage 09 work on its branch. Then
  `git switch main && git pull`, and create `chore/claude-dev-tooling`. This file and
  `discussion/claude-components.md` move with it (they are untracked).
- **B:** create the Stage 10 branch in a separate git worktree off `main` now, and leave the
  Stage 09 tree untouched.

`.claude/` and root `*.md` are already in the CI docs-only pattern, so this PR skips lint/test.
`plan/10-…/` is under `plan/`, also docs-only. `discussion/` is not (S20).

---

## 5. Verification (after the file is created)

Run in a **new** session so the rules load cleanly, and check `/permissions` lists them with no
startup warnings. Then ask Claude to try each case:

| Action | Expected |
|---|---|
| Read tool on `.env` | denied |
| Read tool on `.env.example` | allowed |
| `cat .env` (Bash) | denied |
| `Get-Content .env` (PowerShell) | **to check**: the docs name only Bash readers. If it isn't caught, add `PowerShell(Get-Content *.env)` or cover it with a hook (S2–S6) |
| `git push --dry-run` / `cd . && git push` | denied |
| `gh --version` | denied (expected side effect of `gh *`) |
| `git commit --dry-run -m test` | prompts |
| `pytest -m llm --collect-only` | prompts |
| `pytest -q` / `ruff check .` | run without a prompt |
| `git switch -c tmp-test` | prompts (answer **No**) |

Record the results in `project_status.md` under Stage 10.
