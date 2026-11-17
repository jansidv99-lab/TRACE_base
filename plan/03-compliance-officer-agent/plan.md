# Stage 03 – Compliance Officer Agent (ReACT)

- **Status:** APPROVED – ready to implement (Step 3.1 next)
- **Branch:** `feature/compliance-officer-agent`
- **Master plan:** [`plan/00-master/plan.md`](../00-master/plan.md)
- **Depends on:** Stage 01 (schemas, logger, fixtures) and Stage 02 (formatter + masking, prompt loader,
  retry/backoff logic, `FakeLLMClient`); merged in PR #4
- **Requirements:** `project_goal.md` → "Phase 3 Deliverables: Compliance Officer Agent"

---

## 1. Goal of this stage

Given a `CaseData` and the (human-approved) `RiskAnalystOutput`, the agent writes the **SAR
narrative**: fewer than 120 words, factual, objective, answering *who / what / when / where / why*,
with correct BSA/AML citations. It returns a validated `ComplianceOfficerOutput`.

It works as a **ReACT loop**: the model *reasons*, *acts* by calling tools we provide (look up exact
transaction facts, look up regulations, check a draft), *observes* the results, and repeats until it
submits a narrative that passes every check.

**Done when:** `ComplianceOfficerAgent.generate_compliance_narrative(case, risk_output)` returns a
valid output whose narrative passes all deterministic checks (word count, 5 W's, terminology, facts,
citations). Every step of the loop is audit-logged, and offline tests and a few opt-in `llm` tests pass.

## 2. Environment facts (checked 2026-11-16)

- **Function calling works through the Vocareum proxy** (`gpt-4o-mini`): the model returned a
  `tool_calls` response, accepted the tool result and answered with it (181 tokens).
- Stage 02 lessons applied here: `seed=42` + `temperature=0` for reproducibility; masking stays on;
  facts must be **checked against the case data**, because the risk agent once attached the wrong date
  to an amount.

## 3. Concepts introduced in this stage

- **ReACT (Reason + Act):** instead of answering in one shot, the model alternates
  *Thought → Action → Observation*. Actions are real function calls executed by our code, so
  observations are **facts from the system**, not things the model imagines.
- **Function calling ("tools"):** we describe functions with a JSON schema. The model replies with
  `tool_calls` (function name + JSON arguments), our code runs the function, and the result goes back as
  a `tool` message. The model never executes anything itself.
- **Agent loop & step budget:** a `while` loop around the API call, with a hard limit (e.g. 8 steps)
  so a confused model can't loop forever or run up cost.
- **Grounding:** the narrative may only state facts the tools returned. A deterministic **fact
  checker** verifies every date and dollar amount against the case data.
- **Deterministic validators as the referee:** the model *proposes*; code *decides*. Word count, the
  5 W's, banned speculative language and allowed citations are all checked in Python, not by the model's say-so.
- **Refactoring shared code:** retry/backoff and JSON extraction move from the risk agent into a
  shared module so both agents use one tested implementation.

## 4. Design overview

```
case + approved RiskAnalystOutput
   └─ format (masked, PII-free; risk output re-masked) ─► system prompt + user message
        loop (≤ max_steps):
          model ─► Thought text + tool_calls
          code  ─► run tools ─► observations (tool messages)
          tools: get_transaction_details · lookup_regulations · check_narrative · submit_narrative
        submit_narrative passes all checks ─► ComplianceOfficerOutput (+ ReACT trace for audit)
```

| Tool | What it returns | Why |
|---|---|---|
| `get_transaction_details(refs)` | Exact lines (date, type, amount, method, location) for `T###` refs | Facts come from data, not memory |
| `lookup_regulations(typology)` | Curated citations for the typology (+ the general SAR rule) | No invented or wrong citations |
| `check_narrative(narrative, citations)` | Word count, 5-W coverage, banned terms, unverified dates/amounts, invalid citations | The model can test a draft before submitting |
| `submit_narrative(narrative, citations, reasoning)` | Accepted, or the list of failed checks | Final answer goes through the same checks: revise-on-failure |

## 5. Steps

Each step is implemented one at a time, with your permission before code changes and an optional
commit after each.

---

### Step 3.1 – Shared LLM utilities (refactor)
- **Purpose:** one implementation of retry/backoff, transient-error detection and JSON extraction for both agents.
- **Output:** `src/llm_utils.py` with `extract_json`, `is_transient`, `InvalidLLMOutputError`,
  `add_usage` and a `call_with_retry(create_fn, sleep, max_attempts, backoff)` helper. The risk agent is
  switched to use it with **no behaviour change**, and all Stage 02 tests must still pass unchanged
  (except import paths).
- **Depends on:** Stage 02

---

### Step 3.2 – Regulatory citation table
- **Purpose:** a curated, reviewable source of truth for citations, used by the tool and the validator.
- **Output:** `src/regulations.py`:

| Typology | Citations |
|---|---|
| *(every SAR)* | 31 U.S.C. 5318(g) (SAR authority); 31 CFR 1020.320 (bank SAR filing) |
| Structuring | 31 U.S.C. 5324 (structuring); 31 CFR 1010.311 (CTR over $10,000) |
| Money_Laundering | 18 U.S.C. 1956 (laundering of monetary instruments); 18 U.S.C. 1957 (transactions in criminally derived property) |
| Fraud | 18 U.S.C. 1344 (bank fraud); 18 U.S.C. 1343 (wire fraud) |
| Sanctions | 50 U.S.C. 1701–1706 (IEEPA); 31 CFR Part 501 (OFAC reporting) |
| Other | general SAR citations only |

  Educational reference for this project, not legal advice. Each entry has a short plain-English description.
- **Depends on:** –

---

### Step 3.3 – Narrative validators (deterministic checks)
- **Purpose:** the referee that decides whether a narrative is acceptable.
- **Output:** `src/narrative_checks.py`, with `check_narrative(narrative, citations, case, typology) -> NarrativeReport`
  (lists of `errors` and `warnings`, plus a `passed` flag):
  - **Length:** `count_words < 120` (reuses Stage 01 `count_words`) → error.
  - **5 W's** (heuristics): *who*, the customer's name appears; *what*, an instrument/mechanism term
    (cash, deposit, wire, transfer, ACH, withdrawal, check); *when*, at least one date; *where*, a
    branch/location, account type or "account"; *why*, a suspicion term (threshold, inconsistent,
    structuring, unusual, rapid, pattern …) → error if missing.
  - **Terminology:** banned speculative or accusatory words (e.g. "guilty", "criminal", "obviously",
    "probably", "definitely", "launderer") → error. Internal refs (`T###`, raw transaction IDs) in the
    narrative → error, since SAR narratives describe activity, not database keys.
  - **Facts:** every date must be a transaction date in the case → error otherwise. Every dollar amount
    must equal a transaction amount → otherwise a **warning** ("unverified amount", e.g. a total the
    model computed). See open question 3.
  - **Citations:** every citation must come from the table in 3.2 → error. Must include the general
    SAR citation and at least one typology-specific citation (when the typology has any) → error.
- **Depends on:** 3.2, Stage 01

---

### Step 3.4 – ReACT system prompt (versioned)
- **Purpose:** define the role, the loop, the tools and the narrative style.
- **Output:** `src/prompts/compliance_officer_V1.md`:
  1. **Role:** BSA/AML compliance officer writing a SAR narrative for FinCEN.
  2. **ReACT method:** before every action, write a short *Thought* (what you know, what you need
     next). Then call exactly the tool that gets it. Typical path: review the case and risk findings →
     `get_transaction_details` for the key refs → `lookup_regulations` → draft → `check_narrative` →
     fix → `submit_narrative`.
  3. **Narrative style guide:** fewer than 120 words; third person, past tense; factual and
     objective; answers who/what/when/where/why; dates and amounts copied exactly from tool results;
     no internal references, no speculation, no conclusions of guilt.
  4. **Rules:** only facts from the case or tool observations; citations only from `lookup_regulations`.
- **Depends on:** 3.1–3.3

---

### Step 3.5 – Tools: schemas + executor
- **Purpose:** turn the four tools into function-calling schemas and safe Python implementations.
- **Output:** `src/compliance_tools.py`
  - `TOOL_SCHEMAS`: JSON schemas passed as `tools=[...]`.
  - `ComplianceToolbox(case, formatted_case, typology)`: executes a tool call by name with validated
    arguments and returns a JSON-serialisable observation. Unknown tools or bad arguments return an
    *error observation* (the model can recover) instead of crashing.
  - Works on masked refs only (`T###`), so no real IDs reach the model.
- **Depends on:** 3.2, 3.3, Stage 02 formatter

---

### Step 3.6 – `ComplianceOfficerAgent` + `generate_compliance_narrative` (the ReACT loop)
- **Purpose:** run the loop and return a validated result.
- **Output:** `src/compliance_officer_agent.py`
  - `ComplianceOfficerAgent(client, logger, model, prompt_version, temperature=0.0, seed=42, max_steps=8, ...)`
  - `generate_compliance_narrative(case, risk_output) -> ComplianceOfficerOutput`:
    1. Build the user message: masked case (Stage 02 formatter) + the approved risk findings, with real
       IDs re-masked to `T###`.
    2. Loop: call the API with `tools` → record the model's *Thought* (message text) → execute each
       tool call → append observations → stop when `submit_narrative` passes.
    3. A failed `submit_narrative` returns its errors as the observation, so the model revises.
       This is the **revise-on-failure** loop.
    4. Step budget exhausted, or permanent API failure → `ComplianceError(case_id, reason)` (Stage 04
       routes the case to manual review).
  - `completeness_check` is set by **our validator** (all checks passed), not by the model.
  - API retries/backoff via `src/llm_utils.py` (3.1).
- **Depends on:** 3.1–3.5

---

### Step 3.7 – Audit integration
- **Purpose:** an examiner can replay *how* the narrative was built.
- **Output:** one `ExplainabilityLogger` event per call (success or failure):
  `agent_type="ComplianceOfficer"`, `action="generate_narrative"`, output, `narrative_reasoning`, plus
  the **ReACT trace** (per step: thought text, tool name, arguments, short observation summary),
  `steps`, `model`, `requested_model`, `seed`, `system_fingerprint`, summed `token_usage`, and the final
  validation report (errors and warnings).
- **Depends on:** 3.6

---

### Step 3.8 – Tests (`tests/test_compliance_officer.py`)
- **Purpose:** prove the loop, tools and validators work offline, plus a small real-model check.
- **Output:**
  - `tests/fakes.py` gains tool-call responses (`tool_call_response(name, args)`).
  - **Citations table:** every typology maps to valid entries.
  - **Validators:** each check passes on a good narrative and fails on a crafted bad one (119/120
    words, missing name, missing date, banned word, `T001` in the text, a made-up date, an unknown citation).
  - **Tools:** `get_transaction_details` returns masked exact lines; unknown refs and bad arguments
    give error observations; `lookup_regulations` matches the table.
  - **Loop:** happy path (details → regulations → submit); failed submit → revision → success; step
    budget exhausted → `ComplianceError`; transient API error retried; risk output re-masked (no real IDs sent).
  - **Audit:** trace, steps, tokens and validation report logged.
  - **`@pytest.mark.llm` (opt-in):** the structuring and large-cash customers → narrative passes all
    checks, includes 31 U.S.C. 5324 for structuring, < 120 words. That's 2 cases at about 3–6 calls each,
    still around a cent.
- **Depends on:** 3.1–3.7

---

### Step 3.9 – Demo notebook (learning)
- **Purpose:** watch a ReACT loop step by step and read the resulting SARs.
- **Output:** `notebooks/03_compliance_officer_demo.ipynb`: prints the Thought → Action → Observation
  trace for one case, shows the final narratives with their validation reports for ~4 cases, and summarises tokens, steps and cost.
  (Note: `notebooks/03_workflow_integration.ipynb` stays reserved for Stage 04.)
- **Depends on:** 3.6–3.7

## 6. Files created or changed

```
src/llm_utils.py              (new; risk_analyst_agent.py refactored to use it)
src/regulations.py            src/narrative_checks.py
src/compliance_tools.py       src/compliance_officer_agent.py
src/prompts/compliance_officer_V1.md
tests/fakes.py (extended)     tests/test_compliance_officer.py
notebooks/03_compliance_officer_demo.ipynb
```

## 7. Decisions (resolved in review, 2026-11-16)

1. **Real ReACT loop with function calling.** Observations come from our tools, not the model's imagination.
2. **Step 3.1 refactor approved:** shared helpers move to `src/llm_utils.py`, with no behaviour change;
   Stage 02 tests are the safety net.
3. **Unverified amounts are warnings** (e.g. computed totals); **unverified dates are errors**.
4. **Narrative identifiers:** customer name + account *types* only; no account numbers or transaction
   IDs. Full identifiers belong in the Stage 04 SAR document.
5. **Low-risk inputs are not refused by the agent;** the Stage 04 human gate decides whether a
   narrative is needed.

## 8. Findings during implementation

- **Step 3.6, live run (2026-11-16):** with `tool_choice="required"`, `gpt-4o-mini` returned tool calls
  with **empty message text**, so no `Thought:` was captured despite the prompt. Fix considered: a
  required `thought` argument on every tool. **Deferred by developer decision**; the trace records
  actions + observations only for now. Revisit before Stage 08 or if examiners need the reasoning.
- The model repeated an unchanged `check_narrative` call once (wasted step). Candidate `V2` prompt rule.
- Token use is dominated by re-sent input (~28k in vs ~1k out over 6 steps). Relevant to Stage 04 cost metrics.
