# Stage 02 – Risk Analyst Agent (Chain-of-Thought)

- **Status:** APPROVED – ready to implement (Step 2.1 next)
- **Branch:** `feature/risk-analyst-agent`
- **Master plan:** [`plan/00-master/plan.md`](../00-master/plan.md)
- **Depends on:** Stage 01 (foundation: `CaseData`, `RiskAnalystOutput`, `ExplainabilityLogger`, test fixtures; merged in PR #3)
- **Requirements:** `project_goal.md` → "Phase 2: Risk Analyst Agent"

---

## 1. Goal of this stage

Given one `CaseData`, the agent decides **whether and how** the activity is suspicious. It returns a
validated `RiskAnalystOutput`: one of 5 typologies, a confidence score, a risk level, key indicators,
and the step-by-step reasoning behind them. Every call is audit-logged with model, prompt version,
tokens and latency.

**Done when:** `RiskAnalystAgent.analyze_case(case)` returns a valid `RiskAnalystOutput` for real
cases, recovers from malformed JSON and transient API errors, never shows the model the
revealing transaction-ID prefixes, and is covered by offline tests plus a few opt-in `llm` tests.

## 2. Environment facts (checked 2026-11-16)

| Setting | Value | Consequence |
|---|---|---|
| `MODEL` | `gpt-4o-mini` | Supports `temperature` and JSON mode (`response_format={"type": "json_object"}`) |
| `OPENAI_BASE_URL` | `openai.vocareum.com` (Udacity proxy) | Must be passed to the client; OpenAI-compatible Chat Completions API |
| `PROMPT_VERSION` | `V1` | Selects the prompt file and is stamped on every audit record |
| Case size | 0–121 transactions (median 27); **31 cases have none** | Full transaction list fits easily in context (~3k tokens worst case) |

## 3. Concepts introduced in this stage

- **Chain-of-Thought (CoT):** we ask the model to work through fixed, numbered reasoning steps before
  concluding. This improves accuracy on multi-factor judgements and leaves a reasoning trail an
  examiner can read.
- **System vs user message:** the *system* prompt defines the role, method and output format, and
  stays the same for every case. The *user* message carries one case's data.
- **JSON mode:** `response_format={"type": "json_object"}` makes the API return syntactically valid
  JSON. It does *not* guarantee our fields, so Pydantic still validates the content.
- **Temperature:** controls randomness. We use `0` because the same case should get the same
  classification, which matters for audits.
- **Data leakage & masking:** replace the real transaction IDs (`STRUCT_7D3B8E94`) with neutral ones
  (`T001`) before prompting, then map them back in the output.
- **Retries with exponential backoff:** on temporary failures (timeouts, rate limits, 5xx), wait
  1s → 2s → 4s and try again. Don't retry permanent errors such as a bad API key.
- **Fake client (dependency injection):** tests pass in an object that imitates
  `client.chat.completions.create`, so they run offline, instantly and for free.

## 4. Steps

Each step is implemented one at a time, with your permission before code changes and an optional
commit after each.

---

### Step 2.1 – LLM client setup
- **Purpose:** one place that creates the OpenAI client from settings, so agents receive it instead of building it.
- **Output:**
  - `requirements.txt`: add `openai` (exact pin).
  - `src/llm_client.py`: `get_llm_client() -> OpenAI` using `OPENAI_API_KEY` and `OPENAI_BASE_URL`
    with a request timeout (e.g. 30s) and SDK-level retries **off** (`max_retries=0`), because we do our
    own retries in 2.5 so every attempt is visible and logged. A missing key raises a clear `RuntimeError`.
  - `.env.example`: already has the keys; no change expected.
- **Depends on:** Stage 01 (`src/config.py`)

---

### Step 2.2 – Chain-of-Thought system prompt (versioned file)
- **Purpose:** define *how* the analyst thinks and *exactly* what it returns.
- **Output:** `src/prompts/risk_analyst_V1.md`, loaded by `PROMPT_VERSION`. Sections:
  1. **Role:** senior AML risk analyst at a US bank; factual, evidence-based, no speculation.
  2. **5-step reasoning framework:**
     1. *Customer & account baseline:* occupation, income, risk rating, tenure, account types and balances. What activity is expected?
     2. *Transaction pattern analysis:* volumes, cash vs electronic, amounts near thresholds, velocity, wires, locations, counterparties, timing.
     3. *Red-flag identification:* list concrete indicators, each citing transaction refs (`T007`), amounts and dates.
     4. *Typology classification:* choose one of the 5 types using the definitions below and explain why the others fit worse.
     5. *Risk level & confidence:* assign Low/Medium/High/Critical and a calibrated 0.0–1.0 confidence; conclude.
  3. **Typology definitions:**
     - *Structuring:* splitting cash to stay under the $10,000 CTR threshold (31 U.S.C. 5324).
     - *Sanctions:* links to sanctioned parties or jurisdictions.
     - *Fraud:* deception, account takeover, mule or testing patterns.
     - *Money_Laundering:* placement, layering or integration, e.g. large cash or rapid wire in/out.
     - *Other:* suspicious but none of the above, **or** no meaningful suspicion (paired with Low risk).
  4. **Calibration rules:** e.g. if there are no red flags → `Other` + `Low` + say so explicitly; confidence reflects strength of evidence, not certainty of guilt.
  5. **Output contract:** a JSON object with exactly the `RiskAnalystOutput` fields. `reasoning` contains the 5 steps labelled `Step 1:` … `Step 5:`.
- **Depends on:** Stage 01 (`RiskAnalystOutput`)

---

### Step 2.3 – Case → prompt formatter (with masking)
- **Purpose:** turn a `CaseData` into a compact, PII-free, leak-free user message.
- **Output:** `src/case_formatter.py`
  - `format_case_for_llm(case) -> FormattedCase` with fields `text` (the user message) and
    `id_map` (`{"T001": "STRUCT_7D3B8E94", …}`).
  - **Masking:** transactions are numbered `T001…` in date order; real IDs never appear in `text`.
  - **PII:** sends `name`, occupation, income, risk rating, customer since and **age** (not DOB).
    Never sends SSN, address or phone.
  - **Layout:** customer profile → accounts table → neutral activity summary (counts and totals per
    transaction type, date span, number of distinct locations) → full transaction list as compact
    pipe-separated lines (`T001 | 2025-02-18 | Cash_Deposit | 9,168.52 | Cash | Branch_Southpark_Ave | - | Cash deposit - business receipts`).
  - Includes `data_quality_flags` (with masked IDs), so the model doesn't mistake data errors for suspicion.
  - `unmask(text, id_map)` puts the real IDs back into the model's reasoning and indicators.
- **Depends on:** Stage 01 (`CaseData`)

---

### Step 2.4 – `RiskAnalystAgent` + `analyze_case`
- **Purpose:** the agent itself: prompt in, validated result out.
- **Output:** `src/risk_analyst_agent.py`
  - `RiskAnalystAgent(client, logger, model=config.MODEL, prompt_version=config.PROMPT_VERSION, temperature=0.0)`
  - `analyze_case(case) -> RiskAnalystOutput`: format → call Chat Completions (system + user,
    JSON mode) → parse (2.5) → unmask IDs → return. Records tokens and latency.
  - **Zero-transaction short-circuit:** a case with no transactions gets a deterministic
    `Other / Low / confidence 1.0` result ("no account activity in the review period") with **no API
    call**, audited with `method="rule_based"` (decision 2).
- **Depends on:** 2.1, 2.2, 2.3

---

### Step 2.5 – JSON parsing & error handling
- **Purpose:** never trust raw LLM text; recover where possible and fail clearly where not.
- **Output (in `src/risk_analyst_agent.py`):**
  - `extract_json(text) -> dict`: handles plain JSON, ```` ```json ```` fences and leading/trailing prose
    (finds the outermost `{…}`).
  - **Invalid output** (not JSON, or fails `RiskAnalystOutput` validation): one **repair retry** that
    sends the validation errors back to the model ("your previous answer failed: …; return corrected JSON").
  - **Transient API errors** (`APITimeoutError`, `APIConnectionError`, `RateLimitError`, 5xx): up to 3
    attempts with exponential backoff (1s, 2s, 4s; the sleep function is injectable so tests don't wait).
  - **Permanent errors** (`AuthenticationError`, `BadRequestError`, other 4xx): no retry.
  - **Final failure:** raise `RiskAnalysisError(case_id, reason)` after logging a `success=False` event.
    Stage 04 decides what the workflow does with failed cases (e.g. route to manual review).
- **Depends on:** 2.4

---

### Step 2.6 – Audit integration
- **Purpose:** every analysis is explainable after the fact.
- **Output:** one `ExplainabilityLogger` event per `analyze_case` call (success or failure):
  `agent_type="RiskAnalyst"`, `action="analyze_case"`, `case_id`, input summary (counts, date span;
  **not** the full prompt), output (the validated result), `reasoning`, `execution_time_ms`, plus
  `model`, `token_usage` (prompt/completion/total), `attempts`, `temperature`.
- **Depends on:** 2.4, 2.5, Stage 01 (`ExplainabilityLogger`)

---

### Step 2.7 – Tests (`tests/test_risk_analyst.py`)
- **Purpose:** prove the agent's logic without spending money, plus a small real-model check.
- **Output:**
  - `tests/fakes.py`: `FakeLLMClient` with scripted responses (strings or exceptions) that records the messages it was sent.
  - **Formatter:** no real transaction ID, SSN, address, phone or DOB in the prompt; `T###` numbering in date order; `unmask` round-trips.
  - **Prompt:** the file loads for `PROMPT_VERSION`; it contains all 5 steps, all 5 typologies and all 4 risk levels.
  - **Parsing:** plain / fenced / prose-wrapped JSON parse; garbage raises.
  - **Agent:** zero-transaction case → rule-based result and the fake client is never called; happy path returns a valid output with unmasked IDs; invalid JSON → repair retry → success; still invalid → `RiskAnalysisError`; timeout then success; auth error is not retried; correct audit event (tokens, attempts, `success`).
  - **`@pytest.mark.llm` (opt-in, real API):** a `STRUCT_` customer → `Structuring` with risk ≥ High; a customer with no planted pattern → not High/Critical; and a mixed case. That's 3 calls, costing a fraction of a cent on `gpt-4o-mini`.
- **Depends on:** 2.1–2.6, Stage 01 fixtures

---

### Step 2.8 – Demo notebook (learning)
- **Purpose:** see the agent work on real cases and read its reasoning.
- **Output:** `notebooks/02_risk_analyst_demo.ipynb`. It shows the masked prompt for one case, runs
  the agent on ~6 cases (one per planted pattern plus a clean one), shows a table of classification /
  risk / confidence next to the hidden ID-prefix label, and a token/latency summary.
- **Depends on:** 2.4–2.6

## 5. Files created or changed

```
requirements.txt                    (+ openai)
src/llm_client.py                   src/prompts/risk_analyst_V1.md
src/case_formatter.py               src/risk_analyst_agent.py
tests/fakes.py                      tests/test_risk_analyst.py
notebooks/02_risk_analyst_demo.ipynb
```

## 6. Decisions (resolved in review, 2026-11-16)

1. **No ready-made red flags in the prompt.** Only neutral aggregates (counts and totals per
   transaction type, date span, distinct locations) plus the full transaction list. Detecting patterns
   such as deposits just under $10k is the model's job in CoT Step 2–3.
2. **Zero-transaction cases (31)** short-circuit to a rule-based `Other / Low` result with no API call,
   audited as `method="rule_based"`.
3. **Final failure** raises `RiskAnalysisError`; Stage 04 routes such cases to manual review.
4. **Transaction descriptions** are kept as realistic bank text; only the ID prefixes are masked.
5. **Prompt** lives in the versioned file `src/prompts/risk_analyst_V1.md`, selected by `PROMPT_VERSION`.
6. **Reproducibility (added during Step 2.7, 2026-11-16):** at `temperature=0` the same case
   (`CUST_0137`) was classified Structuring in 4 of 5 runs and Money_Laundering in 1. The agent now
   sends a fixed `seed` (`DEFAULT_SEED = 42`) and logs `seed` + the response's `system_fingerprint`.
   Afterwards it gave 5 of 5 identical results on fingerprint `fp_b411a220b7`. This is best effort, not
   a guarantee; Stage 08 measures consistency across many runs. The live structuring test stays
   strict on classification. If it still flakes, fall back to a majority-of-3 vote in that test.
7. **Live-test thresholds vs plan:** the model rates the planted structuring case **Medium** (not
   High), so the live test asserts `risk_level != "Low"`. Whether 3 consecutive near-$10k
   deposits should be High is a prompt-calibration question for a later `V2`, measured with Stage 08 evals.
