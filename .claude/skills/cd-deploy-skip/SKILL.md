---
name: cd-deploy-skip
description: Make a GitHub Actions CD/deploy workflow build, push and deploy only when files that go into the container image changed, comparing the new commit with the version that is actually live (e.g. Cloud Run image tag = commit SHA). Use when CD "redeploys on every merge", docs-only merges restart the service, a failed deploy must be retried, or to replace duplicated `paths:` lists in a deploy workflow.
---

# CD deploys only when image inputs changed

Goal: the CD workflow still starts on every trigger, but **build → push → deploy** run only when
something that goes **into the image** changed since the version that is **live**. Deterministic,
free, no LLM in the pipeline.

Scope: **CD only**. For lint/test skipping use the separate **`ci-docs-skip`** skill. The two
answer different questions:

| | CI (`ci-docs-skip`) | CD (this skill) |
|---|---|---|
| Question | is anything *not* docs? | did anything that goes *into the image* change? |
| List | ignore list (docs-only) | **allow list** (image inputs) |
| Diff base after merge | push `before..sha` | **live deployed SHA..new SHA** |
| Fail open means | run lint/test | **deploy** |

A `tests/` change runs CI but must not redeploy, which is why the lists differ.

## 1. Get the image-input allow list (repo-specific — never guess)

Delegate to the **`cd-scope-auditor`** subagent ("propose IMAGE_INPUTS_REGEX for this repo").
Review its evidence table. Rules:

- Sources of every Dockerfile `COPY`/`ADD`, the `Dockerfile`, `.dockerignore`, dependency files
  installed in the image (`requirements.txt`, lockfiles), and the CD workflow file itself.
- Anchor everything: folders `^src/`, files `^Dockerfile$`, `^requirements\.txt$`. Escape dots.
- Dev-only files (`requirements-dev.txt`, `tests/`, docs) are **not** image inputs.
- The list must not overlap the CI docs-only list. If a path is in both, one of them is wrong.

Record the list in the project's `CLAUDE.md` (CD bullet).

## 2. One `changes` job for every trigger — no workflow-level `paths:`

- Remove `paths:`/`paths-ignore:` from the CD triggers. The list then exists **once**, in the
  job, and a skipped build reports as passed if it ever becomes a required check.
- Add the job from `templates/deploy-changes-job.yml`. Wire the build job with
  `needs: changes` and `if: needs.changes.outputs.image == 'true'` (plus any existing conditions).
- `workflow_run: [CI]` triggers cannot be path-filtered at all, so this job is the only gate.

## 3. Diff base by trigger

| Trigger | Range | Why |
|---|---|---|
| `pull_request` | `base.sha...head.sha` | the PR's own changes (build + smoke test only) |
| after CI on main (`workflow_run`) | **`<live SHA>..<new SHA>`** | compares what runs with what is new |
| `workflow_dispatch` | — always deploy | covers config-only fixes |

Diffing against the **live** version (not `HEAD^1`) means:
- a failed deploy is retried by the next merge, even a docs-only one;
- merge, squash and rebase merges are all covered;
- re-running CD for an already-live commit does nothing (`live == new`).

Cloud Run recipe (in the template): `status.latestReadyRevisionName` → that revision's
`spec.containers[0].image` → the tag after the last `:` must be a 40-hex commit SHA. This only
works when deploys tag images with the commit SHA. Other platforms: read the deployed version from
wherever the platform records it (a label, an env var, a release record).

## 4. Fail open = deploy

Deploy when: login/describe fails, there is no ready revision, the tag is not a 40-hex SHA (e.g. a
digest or `latest`), the live commit isn't in the git history, the diff fails or is empty, or the
event is unknown. Skip only when the diff is known **and** no image input changed, or `live == new`.
Login steps use `continue-on-error: true` so a failure becomes "deploy", not a red run.

## 5. Permissions and concurrency

- The `changes` job needs `id-token: write` for keyless login (WIF). The deploy identity can usually
  read services already (Cloud Run `run.admin`/`run.viewer`), so no new roles are needed.
- Log in only on the after-CI path; PR runs need no cloud access.
- Keep deploys serialized (`concurrency` without cancel for non-PR events). The next run then reads
  the live version only after the previous deploy has finished.

## 6. Verify

1. `IMAGE_INPUTS_REGEX='<regex>' bash scripts/test_inputs_changed.sh <cases-file>`
   (format: `examples/python-docker-cases.txt`; `true` = deploy). Include `tests/x.py` → `false`
   and a runtime `src/**/*.md` → `true`.
2. Extract the `run:` block (PyYAML) and run it on real commits with `LIVE_SHA` set by hand. Cover
   deploy, skip, live == new, a non-SHA tag, PR docs-only, PR `tests/`-only and manual runs.
3. On GitHub: after the merge, the summary shows "Live `<sha>` → new `<sha>`" and the job deploys.
   A later docs-only merge says "nothing to deploy".

## Files

- `templates/deploy-changes-job.yml` — the job (Cloud Run + WIF recipe) with an
  `IMAGE_INPUTS_REGEX` placeholder.
- `scripts/inputs_changed.sh` — the allow-list filter for local testing (stdin: file names →
  `image=true|false`). **Keep it identical to the template's filter line.**
- `scripts/test_inputs_changed.sh` + `examples/*.txt` — table-driven tests.
