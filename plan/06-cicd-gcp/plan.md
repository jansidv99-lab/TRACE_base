# Stage 06 – CI/CD to GCP

- **Status:** APPROVED – ready to implement (Step 6.1 next)
- **Branches:** `chore/github-actions-ci` (this plan + Step 6.1), then `chore/cloudrun-deploy`
  (Steps 6.2–6.5)
- **Master plan:** [`plan/00-master/plan.md`](../00-master/plan.md) (decision 6: this stage was
  Stage 07 and now comes before test consolidation)
- **Depends on:** Stages 01–05 (all merged; PR #7 brought in the Streamlit app)
- **Requirements:** `project_goal.md` → "Phase 6: CI/CD pipelines to GCP": GitHub Actions for CI,
  Cloud Run for deployment, same GCP account but **separate resources for this project**

---

## 1. Goal of this stage

```
pull request ──► CI (ruff + pytest + docker build) ──► review ──► merge to main
                                                                     │
main ──► CI passes ──► CD: build image → Artifact Registry → Cloud Run (public URL)
```

**Done when:**
- Every PR shows a green or red CI check.
- Every merge to `main` deploys the Streamlit app to Cloud Run automatically.
- The API key lives only in Secret Manager.
- GitHub reaches GCP without any stored key file.

## 2. Facts and decisions carried in (developer, 2026-11-17)

- **Order:** CI/CD now, test consolidation (Stage 07) afterwards. The Stage 05 walk-through (5.8)
  happens later on the deployed app.
- **Access:** a **public URL** (`--allow-unauthenticated`). Every click spends real API credit, so
  the guardrails in section 6 matter.
- **Persistence:** temporary. Cloud Run's disk is wiped on restart or scale-to-zero, so audit logs
  and SAR files are lost then. Durable logging comes in Stage 08 (Cloud Logging → BigQuery).
- **Trigger:** deploy automatically when CI passes on `main`.
- **Environment:**
  - project `GCP_PROJECT_ID` from `.env`, region `asia-south1`
  - repo `jansidv99-lab/TRACE_base`
  - Docker 29.5 and gcloud SDK 568 installed locally
- **The suite needs no secrets:** real-LLM tests are skipped by default, and `src/config.py` lets
  environment variables override `.env`.

## 3. Concepts introduced in this stage

- **CI (continuous integration):** a fresh machine checks every change: install, lint, test, build.
  "It works on my machine" stops counting.
- **CD (continuous deployment):** a passing `main` is automatically built into an image and
  released. No manual deploy steps.
- **Container image:** the app plus its exact runtime, built once from a `Dockerfile` and run the
  same everywhere: on your laptop, in CI, on Cloud Run.
- **Cloud Run:** runs a container on demand behind an HTTPS URL, and scales to zero when idle,
  so it costs nothing while unused.
- **Secret Manager:** stores the API key outside code and images. Cloud Run injects it as an
  environment variable at start-up.
- **Workload Identity Federation (WIF):** GitHub proves "I am a workflow in
  jansidv99-lab/TRACE_base" with a short-lived token, and GCP trusts that instead of a
  downloadable service-account key. No long-lived secret ever exists.
- **Least privilege:** two service accounts, each with only what it needs. One *runs* the app; the
  other *deploys* it.

## 4. Steps

### Step 6.1 – CI workflow (`.github/workflows/ci.yml`) — branch `chore/github-actions-ci`
- **Purpose:** lint and test every PR and every push to `main`.
- **Output:**
  - Triggers: `pull_request` and `push` on `main`.
  - Job `test` on `ubuntu-latest`:
    - `actions/checkout@v4`
    - `actions/setup-python@v5` (Python 3.11, pip cache from `requirements-dev.txt`)
    - `pip install -r requirements-dev.txt`, `ruff check .`, `pytest`
  - `permissions: contents: read`, and a `concurrency` group that cancels outdated runs on the
    same branch.
  - Local check first: `OPENAI_API_KEY= MODEL= OPENAI_BASE_URL= pytest` (empty values override
    `.env`) must give the same result CI will.
- **Depends on:** Stages 01–05

### Step 6.2 – Container (`Dockerfile`, `.dockerignore`) — branch `chore/cloudrun-deploy`
- **Purpose:** one reproducible image of the Streamlit app.
- **Output:**
  - `Dockerfile`:
    - `python:3.11-slim`, with `PYTHONUNBUFFERED=1` and `PYTHONDONTWRITEBYTECODE=1`
    - `pip install --no-cache-dir -r requirements.txt` (runtime dependencies only)
    - copy `src/`, `app/`, `project_data/` and `.streamlit/`
    - run as a non-root user that owns `/app`, so it can write `logs/` and `outputs/` (temporary)
    - `CMD streamlit run app/streamlit_app.py --server.port=${PORT:-8080}
      --server.address=0.0.0.0`
  - `.dockerignore` excludes `.env*`, `.venv`, `.git`, `logs`, `outputs`, `notebooks`, `tests`,
    `plan` and caches. **The API key can never be baked into the image.**
  - A **separate CD pipeline**, `.github/workflows/cd.yml` (developer decision 2026-11-17: CI and
    CD as two workflows, for clarity). Its job "Build and smoke-test container" builds the image,
    checks it contains no `.env`, starts it, and waits for `/_stcore/health` to return `ok`. It runs
    on PRs that touch the image's inputs, and after CI succeeds on `main`. `ci.yml` stays
    lint-and-test only.
- **Depends on:** 6.1

### Step 6.3 – GCP setup (`plan/06-cicd-gcp/gcp_setup.md`) — you run the commands
- **Purpose:** create this project's own resources, all named `trace-sar-*`.
- **Output:** a runbook of `gcloud` commands, with a check command after each block:
  1. Enable APIs: `run`, `artifactregistry`, `secretmanager`, `iam`, `iamcredentials`, `sts`.
  2. Create the Artifact Registry Docker repo `trace-sar` in `asia-south1`.
  3. Create secret `trace-sar-openai-api-key`. You type or paste the key into stdin, so it never
     goes on the command line or into shell history.
  4. Create runtime service account `trace-sar-run`. Its only role is `secretAccessor` on that
     one secret.
  5. Create deployer service account `trace-sar-deployer`, with three roles: `run.admin`,
     `artifactregistry.writer` on the `trace-sar` repo, and `iam.serviceAccountUser` on
     `trace-sar-run`.
  6. Set up WIF: pool `trace-sar-github` and OIDC provider `github`
     (`token.actions.githubusercontent.com`), with the attribute condition
     `assertion.repository == 'jansidv99-lab/TRACE_base'`. Only this repo can use it. Then bind
     `workloadIdentityUser` on the deployer service account.
  7. Recommended: a billing **budget alert** (see Q2).
  8. GitHub **repository variables** (Settings → Secrets and variables → Actions → Variables),
     which you set: `GCP_PROJECT_ID`, `GCP_REGION`, `WIF_PROVIDER`, `DEPLOYER_SA`, `RUNTIME_SA`,
     `OPENAI_BASE_URL`, `MODEL`, `PROMPT_VERSION`. None of these is secret; the key stays in
     Secret Manager.
- **Depends on:** – (can be done in parallel with 6.2)

### Step 6.4 – Deploy job in the CD workflow (`.github/workflows/cd.yml`)
- **Purpose:** deploy every green `main` automatically.
- **Output:**
  - A `deploy` job in `cd.yml` that `needs: build`. It runs only for `workflow_run` (CI completed
    successfully on `main`) or a manual dispatch from `main`, and checks out that run's `head_sha`.
    PR runs stop after the build job.
  - `permissions: contents: read, id-token: write` (the token is used for WIF).
  - Steps:
    1. `google-github-actions/auth@v2` (WIF, no key)
    2. `setup-gcloud@v2`
    3. `gcloud auth configure-docker asia-south1-docker.pkg.dev`
    4. `docker build` and `push`, tagged with the commit SHA (the build job proves it starts)
    5. `google-github-actions/deploy-cloudrun@v2`
  - Deploy flags:
    - `--allow-unauthenticated` (public, decision 2)
    - `--service-account trace-sar-run`
    - `--set-secrets OPENAI_API_KEY=trace-sar-openai-api-key:latest`
    - `--set-env-vars OPENAI_BASE_URL=…,MODEL=…,PROMPT_VERSION=…`
    - `--port 8080 --cpu 1 --memory 1Gi`
    - `--min-instances 0 --max-instances 1`
    - `--session-affinity --timeout 3600` (Streamlit keeps a websocket open per browser tab)
  - Smoke test: `curl <service-url>/_stcore/health` must return `ok`, otherwise the job fails.
    The URL is printed in the job summary.
- **Depends on:** 6.1, 6.2, 6.3

### Step 6.5 – Docs and first deploy
- **Purpose:** make it repeatable and record what happened.
- **Output:**
  - `CLAUDE.md` gains local `docker build` / `docker run` commands and a CI/CD note.
  - `project_status.md` records the service URL, the first deploy's result and any findings.
- **Depends on:** 6.4

## 5. Files created or changed

```
.github/workflows/ci.yml        (6.1; lint + test only)
Dockerfile  .dockerignore       (6.2)
.github/workflows/cd.yml        (6.2 build + smoke job; 6.4 adds the deploy job)
plan/06-cicd-gcp/gcp_setup.md   (6.3)
CLAUDE.md  project_status.md    (6.5)
app/streamlit_app.py            (only if Q1 is accepted)
```

## 6. Decisions (resolved in review, 2026-11-17 — all recommendations accepted)

1. **Passcode gate:** yes, optional — `APP_PASSCODE` (from Secret Manager) gates the public app when
   set; unset means no change (local runs, tests). Implemented with Step 6.4.
2. **Budget alert:** $5/month on this project, email alerts at 50 %, 90 % and 100 % (runbook 6.3).
3. **`llm` tests in CI:** no — they stay local (`pytest -m llm`); CI needs no secrets.
4. **Region:** `asia-south1`.
