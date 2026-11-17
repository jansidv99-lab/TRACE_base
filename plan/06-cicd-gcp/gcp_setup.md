# Stage 06 – Step 6.3: GCP setup runbook

- **Who runs it:** the developer, once, in **Git Bash** with the Google Cloud SDK, logged in as a
  project owner. Claude does not run these commands (workflow rule 10).
- **What it creates:** this project's own resources, all named `trace-sar-*`, in the existing GCP
  account (`project_goal.md`, Phase 6). Nothing shared with other projects is changed.
- **Plan:** [`plan.md`](plan.md), Step 6.3 · **Depends on:** nothing (can be done in parallel with
  6.2)

Run the blocks in order. Each block ends with a **check** command and its expected result. If a
check fails, stop there and paste the output into the chat.

Every block starts with a **guard line** (`: "${VAR:?…}"`). In a new Git Bash window without the
block 0 variables it stops immediately with *run block 0 first*, instead of running commands with
empty names. (Added after the first deploy: block 4 had silently granted nothing, so Cloud Run got
*Permission denied on secret*.)

```
GitHub Actions (CD) ──OIDC token──► WIF pool trace-sar-github ──► impersonates trace-sar-deployer
        │                                                              │ pushes image / deploys
        ▼                                                              ▼
Artifact Registry  trace-sar  ◄────────────────  Cloud Run service trace-sar (public URL)
                                                          │ runs as trace-sar-run
                                                          ▼
                                       Secret Manager: trace-sar-openai-api-key (+ optional passcode)
```

---

## 0. Variables (set them in every new Git Bash window)

```bash
export PROJECT_ID="<GCP_PROJECT_ID from your .env>"
export REGION="asia-south1"
export GITHUB_REPO="jansidv99-lab/TRACE_base"
export AR_REPO="trace-sar"                       # Artifact Registry repository
export SERVICE="trace-sar"                       # Cloud Run service name
export RUN_SA="trace-sar-run"                    # runtime identity of the app
export DEPLOY_SA="trace-sar-deployer"            # identity GitHub Actions uses to deploy
export POOL="trace-sar-github"                   # Workload Identity pool
export PROVIDER="github"                         # OIDC provider inside the pool
export KEY_SECRET="trace-sar-openai-api-key"
export PASSCODE_SECRET="trace-sar-app-passcode"  # optional passcode gate (decision Q1)

gcloud auth login
gcloud config set project "$PROJECT_ID"
export PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
export RUN_SA_EMAIL="$RUN_SA@$PROJECT_ID.iam.gserviceaccount.com"
export DEPLOY_SA_EMAIL="$DEPLOY_SA@$PROJECT_ID.iam.gserviceaccount.com"
```

**Check:** `echo "$PROJECT_ID $PROJECT_NUMBER"` prints your project ID and a 12-digit number. The
number should equal `GCP_PROJECT_NUMBER` in `.env`.

---

## 1. Enable the APIs

```bash
: "${PROJECT_ID:?run block 0 first}" "${PROJECT_NUMBER:?run block 0 first}"
gcloud services enable \
  run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  cloudresourcemanager.googleapis.com
```

**Check:** `gcloud services list --enabled --format='value(config.name)' | grep -E
'run|artifactregistry|secretmanager|iamcredentials|sts'` lists 5 lines.

---

## 2. Artifact Registry repository (where CD pushes images)

```bash
: "${AR_REPO:?run block 0 first}" "${REGION:?run block 0 first}"
gcloud artifacts repositories create "$AR_REPO" \
  --repository-format=docker --location="$REGION" \
  --description="TRACE SAR review app images"
```

**Check:** `gcloud artifacts repositories describe "$AR_REPO" --location="$REGION"
--format='value(format)'` prints `DOCKER`.

---

## 3. Secrets (the API key never touches a file, the command line or shell history)

```bash
: "${KEY_SECRET:?run block 0 first}"
gcloud secrets create "$KEY_SECRET" --replication-policy=automatic

# Paste the key when prompted (nothing is shown), then press Enter
read -rsp "OpenAI/Vocareum API key: " KEY && echo
printf '%s' "$KEY" | gcloud secrets versions add "$KEY_SECRET" --data-file=-
unset KEY
```

Optional passcode gate (decision Q1). If you skip this, the public app opens without a passcode:

```bash
: "${PASSCODE_SECRET:?run block 0 first}"
gcloud secrets create "$PASSCODE_SECRET" --replication-policy=automatic
read -rsp "App passcode to share with reviewers: " PASSCODE && echo
printf '%s' "$PASSCODE" | gcloud secrets versions add "$PASSCODE_SECRET" --data-file=-
unset PASSCODE
```

**Check:** `gcloud secrets versions list "$KEY_SECRET" --format='value(name,state)'` prints `1
enabled`. Don't use `versions access`: it would print the key.

---

## 4. Runtime service account (what the app runs as)

It can read **only** this project's secrets, and nothing else.

```bash
: "${RUN_SA:?run block 0 first}" "${RUN_SA_EMAIL:?run block 0 first}" "${KEY_SECRET:?run block 0 first}"
gcloud iam service-accounts create "$RUN_SA" --display-name="TRACE SAR Cloud Run runtime"

gcloud secrets add-iam-policy-binding "$KEY_SECRET" \
  --member="serviceAccount:$RUN_SA_EMAIL" --role=roles/secretmanager.secretAccessor
# only if you created the passcode secret:
gcloud secrets add-iam-policy-binding "$PASSCODE_SECRET" \
  --member="serviceAccount:$RUN_SA_EMAIL" --role=roles/secretmanager.secretAccessor
```

**Check:** `gcloud secrets get-iam-policy "$KEY_SECRET" --format='value(bindings.members)'`
contains `serviceAccount:trace-sar-run@…`.

---

## 5. Deployer service account (what GitHub Actions acts as)

```bash
: "${DEPLOY_SA_EMAIL:?run block 0 first}" "${RUN_SA_EMAIL:?run block 0 first}" "${AR_REPO:?run block 0 first}"
gcloud iam service-accounts create "$DEPLOY_SA" --display-name="TRACE SAR GitHub deployer"

# deploy and update Cloud Run services in this project
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$DEPLOY_SA_EMAIL" --role=roles/run.admin --condition=None

# push images, but only to the trace-sar repository
gcloud artifacts repositories add-iam-policy-binding "$AR_REPO" --location="$REGION" \
  --member="serviceAccount:$DEPLOY_SA_EMAIL" --role=roles/artifactregistry.writer

# allowed to start the service *as* trace-sar-run (and no other account)
gcloud iam service-accounts add-iam-policy-binding "$RUN_SA_EMAIL" \
  --member="serviceAccount:$DEPLOY_SA_EMAIL" --role=roles/iam.serviceAccountUser
```

**Check:** `gcloud projects get-iam-policy "$PROJECT_ID" --flatten='bindings[].members'
--filter="bindings.members:$DEPLOY_SA_EMAIL" --format='value(bindings.role)'` prints
`roles/run.admin`.

---

## 6. Workload Identity Federation (keyless login from GitHub)

GitHub signs a short-lived token saying *"I am a workflow in `jansidv99-lab/TRACE_base`, running on
`refs/heads/main`"*. The provider below accepts **only** tokens matching both conditions, so a
fork, another repository or a feature branch cannot deploy.

```bash
: "${POOL:?run block 0 first}" "${PROVIDER:?run block 0 first}" "${GITHUB_REPO:?run block 0 first}" "${PROJECT_NUMBER:?run block 0 first}" "${DEPLOY_SA_EMAIL:?run block 0 first}"
gcloud iam workload-identity-pools create "$POOL" \
  --location=global --display-name="TRACE SAR GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
  --location=global --workload-identity-pool="$POOL" \
  --display-name="GitHub OIDC" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='$GITHUB_REPO' && assertion.ref=='refs/heads/main'"

gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA_EMAIL" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL/attribute.repository/$GITHUB_REPO"

export WIF_PROVIDER="$(gcloud iam workload-identity-pools providers describe "$PROVIDER" \
  --location=global --workload-identity-pool="$POOL" --format='value(name)')"
echo "$WIF_PROVIDER"
```

**Check:** the last line prints
`projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/trace-sar-github/providers/github`.
Keep it for step 8.

---

## 7. Budget alert (decision Q2: $5/month, emails at 50 %, 90 %, 100 %)

```bash
: "${PROJECT_ID:?run block 0 first}"
gcloud services enable billingbudgets.googleapis.com
export BILLING_ACCOUNT="$(gcloud billing projects describe "$PROJECT_ID" \
  --format='value(billingAccountName)' | sed 's#billingAccounts/##')"

gcloud billing budgets create --billing-account="$BILLING_ACCOUNT" \
  --display-name="trace-sar monthly" --budget-amount=5USD \
  --filter-projects="projects/$PROJECT_ID" \
  --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0
```

- If your billing account uses another currency, the command says so. Use the same amount in that
  currency, e.g. `--budget-amount=450INR`.
- A budget **alerts**; it does not stop spending.
- **The LLM API key is billed by OpenAI or Vocareum, not by GCP.** This budget covers Cloud Run,
  Artifact Registry and Secret Manager only. Watch the key's own usage page too.

**Check:** `gcloud billing budgets list --billing-account="$BILLING_ACCOUNT"
--format='value(displayName)'` contains `trace-sar monthly`.

---

## 8. GitHub repository variables (read by the CD workflow)

None of these is secret: the key stays in Secret Manager, and WIF replaces stored credentials.
Set them in **GitHub → Settings → Secrets and variables → Actions → Variables**, or with the
`gh` CLI:

The three app settings are read from your `.env` (run this from the repo root), with the quotes
removed, so no placeholder text can slip through (Stage 09 fix #2; the first live run failed on a
copied placeholder). Look at the three printed values before setting anything:

```bash
: "${WIF_PROVIDER:?run block 6 first}" "${DEPLOY_SA_EMAIL:?run block 0 first}" "${RUN_SA_EMAIL:?run block 0 first}"
env_value() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | tr -d '"'"'"'\r'; }
export APP_BASE_URL="$(env_value OPENAI_BASE_URL)" APP_MODEL="$(env_value MODEL)" APP_PROMPT="$(env_value PROMPT_VERSION)"
printf 'OPENAI_BASE_URL=%s\nMODEL=%s\nPROMPT_VERSION=%s\n' "$APP_BASE_URL" "$APP_MODEL" "$APP_PROMPT"
: "${APP_BASE_URL:?OPENAI_BASE_URL missing in .env}" "${APP_MODEL:?MODEL missing in .env}" "${APP_PROMPT:?PROMPT_VERSION missing in .env}"
```

Expected: the URL starts with `https://`, the model is e.g. `gpt-4o-mini`, the version is `V1`, `V2`,
… — no quotes, no `<…>`. Then set all variables:

```bash
gh variable set GCP_PROJECT_ID  --repo "$GITHUB_REPO" --body "$PROJECT_ID"
gh variable set GCP_REGION      --repo "$GITHUB_REPO" --body "$REGION"
gh variable set AR_REPO         --repo "$GITHUB_REPO" --body "$AR_REPO"
gh variable set CLOUD_RUN_SERVICE --repo "$GITHUB_REPO" --body "$SERVICE"
gh variable set WIF_PROVIDER    --repo "$GITHUB_REPO" --body "$WIF_PROVIDER"
gh variable set DEPLOYER_SA     --repo "$GITHUB_REPO" --body "$DEPLOY_SA_EMAIL"
gh variable set RUNTIME_SA      --repo "$GITHUB_REPO" --body "$RUN_SA_EMAIL"
gh variable set KEY_SECRET      --repo "$GITHUB_REPO" --body "$KEY_SECRET"
gh variable set PASSCODE_SECRET --repo "$GITHUB_REPO" --body "$PASSCODE_SECRET"   # or "" if skipped
gh variable set OPENAI_BASE_URL --repo "$GITHUB_REPO" --body "$APP_BASE_URL"
gh variable set MODEL           --repo "$GITHUB_REPO" --body "$APP_MODEL"
gh variable set PROMPT_VERSION  --repo "$GITHUB_REPO" --body "$APP_PROMPT"
```

**Check:** `gh variable list --repo "$GITHUB_REPO"` shows 12 variables. The CD deploy job also
validates them before every deploy (empty, placeholder or quote characters, a non-`https://` URL,
a version not shaped like `V1`) and stops with a clear message if one is wrong.

---

## 9. Final check

```bash
gcloud artifacts repositories list --location="$REGION" --format='value(name)' | grep trace-sar
gcloud iam service-accounts list --format='value(email)' | grep trace-sar
gcloud secrets list --format='value(name)' | grep trace-sar
gcloud iam workload-identity-pools providers list --location=global \
  --workload-identity-pool="$POOL" --format='value(name)'
```

Expected: 1 repository, 2 service accounts, 1–2 secrets and 1 provider. Tell Claude when it's done;
Step 6.4 (the deploy job) uses exactly these names.

---

## Clean-up (only if you ever want to remove everything)

```bash
gcloud run services delete "$SERVICE" --region="$REGION" --quiet
gcloud artifacts repositories delete "$AR_REPO" --location="$REGION" --quiet
gcloud secrets delete "$KEY_SECRET" --quiet; gcloud secrets delete "$PASSCODE_SECRET" --quiet
gcloud iam workload-identity-pools delete "$POOL" --location=global --quiet
gcloud iam service-accounts delete "$DEPLOY_SA_EMAIL" --quiet
gcloud iam service-accounts delete "$RUN_SA_EMAIL" --quiet
```
