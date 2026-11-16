# Stage 01 – Foundation & Data Modeling

- **Status:** APPROVED – implementation in progress (Step 1.1)
- **Branch:** `feature/foundation-schemas`
- **Master plan:** [`plan/00-master/plan.md`](../00-master/plan.md)
- **Depends on:** Stage 00 (master plan, merged in PR #1)
- **Requirements:** `project_goal.md` → "Phase 1: Foundation & Data Modeling"

---

## 1. Goal of this stage

Turn three raw CSV files into **validated Python objects**. Each object is one *case*: a customer with
all their accounts and transactions. We also build an **audit logger** that every later stage writes to.
Nothing in this stage calls an LLM.

**Done when:** `DataLoader` loads all 150 customers into valid `CaseData` objects, any bad row is
reported (not crashed on), and `pytest` + `ruff check .` pass.

## 2. What the data actually looks like (profiled 2026-11-16)

These facts drive the validation rules in §4. Step 1.2 turns them into a notebook so you can
explore them yourself.

| File | Rows | Key facts |
|---|---|---|
| `customers.csv` | 150 | `risk_rating` ∈ {Low 118, Medium 25, High 7}; `annual_income` 30,284–433,150; `phone` comes in ~6 different formats; `ssn_last_4` is 4 digits (must stay a **string**, because a leading zero like `0123` would be lost as an int) |
| `accounts.csv` | 178 | 130 customers have 1 account, 12 have 2, 8 have 3; `account_type` ∈ {Checking, Savings, Money_Market, Business_Checking}; `status` ∈ {Active 133, Closed 45}; balances 1,008–99,932, none negative; 37 accounts have `average_monthly_balance = 0.0` |
| `transactions.csv` | 4,268 | dates 2025-02-03 → 2025-08-06; amounts 11.67–98,090.16, none negative; 12 `transaction_type`s, 7 `method`s; `counterparty` blank in 2,943 rows, `location` blank in 3,396 rows |

**Referential integrity:** every account's customer exists and every transaction's account exists.
37 accounts have **no transactions**.

**Data-quality quirks to tolerate (log them, don't reject them):**
- 368 transactions are dated **before** their account's `opening_date`.
- `transaction_type` and `method` can disagree (e.g. `Online_Transfer` via `ATM`).
- `average_monthly_balance = 0.0` on accounts with large balances (these are likely the 37 accounts without transactions).

### ⚠️ Important finding: the transaction ID prefix reveals the answer

Most IDs start `TXN_`, but 200 rows carry a prefix that names a **planted suspicious pattern**:

| Prefix | Rows / accounts | Pattern |
|---|---|---|
| `STRUCT_` | 18 / 4 | Cash deposits of $9,154–$9,916, just under the $10k reporting threshold → **structuring** |
| `LARGE_` | 7 / 3 | Cash transactions of $15.9k–$48.5k |
| `RAPID_` | 21 / 4 | Large incoming/outgoing wires (up to $98k): rapid movement of funds |
| `GEO_` | 60 / 10 | ATM deposits/withdrawals spread across many branches |
| `FREQ_` | 94 / 4 | High-frequency small electronic transfers |

**Why it matters:** if the Risk Analyst agent sees `STRUCT_7D3B8E94`, it can "detect" structuring by
reading the label instead of reasoning about the data. That's called **data leakage**.
- **Stage 01:** keep `transaction_id` exactly as-is (it's the record's identity and the audit trail needs it).
- **Stage 02 (carried forward):** the prompt formatter must **mask** IDs before they reach the LLM.
- **Stage 08 (bonus):** the prefixes give us free **ground-truth labels** for evals.

## 3. Concepts introduced in this stage

- **Pydantic v2 models:** declare fields with types; Pydantic converts (`"2025-02-18"` → `date`) and
  validates, raising a `ValidationError` that lists *every* problem at once.
- **`Literal[...]`:** restricts a field to a fixed set of values (e.g. risk rating). A typo in the data
  fails loudly instead of silently becoming a new category.
- **`field_validator` vs `model_validator`:** a field validator checks one field (amount > 0); a model
  validator checks relationships between fields (account ID starts with its customer ID).
- **Empty string → `None`:** CSV has no "null", only `""`. We convert blanks to `None` so "unknown" is
  explicit.
- **JSONL audit log:** one JSON object per line, append-only. It's easy to `grep`, stream and load into BigQuery later.
- **pytest markers & config in `pyproject.toml`:** one file configures ruff and pytest.

## 4. Steps

Each step is implemented **one at a time**, with your permission before any code change, and an
optional commit after each step.

---

### Step 1.1 – Tooling setup
- **Purpose:** a reproducible environment where lint and tests work before any real code exists.
- **Output:**
  - `pyproject.toml`: ruff (line length 100, rules `E,F,I,B,UP`, target py311); pytest
    (`testpaths=["tests"]`, `pythonpath=["."]`, `llm` marker registered, `addopts = "-m 'not llm'"`
    so real-LLM tests are skipped by default).
  - `requirements.txt` (runtime, exact pins): `pydantic`, `pandas`, `python-dotenv`.
    (`openai` comes in Stage 02 and `streamlit` in Stage 05, each added only when first needed.)
  - `requirements-dev.txt`: `-r requirements.txt` + `pytest`, `ruff`, `ipykernel` (notebooks).
  - `.env.example`: every key from `.env` with placeholder values, plus `JUDGE_MODEL`.
  - `src/__init__.py`, `src/config.py`: loads `.env` with `python-dotenv` and exposes `DATA_DIR`
    (default `project_data`), `LOG_DIR` (default `logs`), `MODEL`, `JUDGE_MODEL`, `PROMPT_VERSION`.
    A missing API key does **not** fail in Stage 01, because nothing uses it yet.
  - `tests/__init__.py`, `tests/test_smoke.py` (imports `src.config`, proves the setup works).
- **Depends on:** –
- **You run:** `py -3.11 -m venv .venv`, activate, `pip install -r requirements-dev.txt`, `ruff check .`, `pytest`.

---

### Step 1.2 – Data exploration notebook
- **Purpose:** see the data for yourself, confirm the facts in §2 and learn what suspicious activity looks like.
- **Output:** `notebooks/01_data_exploration.ipynb` with sections:
  1. Load and shape of each CSV; dtypes; null/blank counts.
  2. Joins: customer → accounts → transactions; orphan checks; accounts without transactions.
  3. Value sets for every categorical column (these become the `Literal`s in 1.3).
  4. Data-quality quirks (dates before account opening, type/method mismatch).
  5. Suspicious patterns: deposits of $9k–$10k, large cash, wire bursts, branch spread, high frequency.
     Compare against the ID prefixes (§2) and discuss leakage.
- **Depends on:** 1.1

---

### Step 1.3 – Entity schemas (`src/foundation_sar.py`)
- **Purpose:** one validated model per CSV row.
- **Output:**

**`CustomerData`**
| Field | Type | Rule |
|---|---|---|
| `customer_id` | `str` | pattern `^CUST_\d{4}$` |
| `name` | `str` | non-empty, whitespace stripped |
| `date_of_birth` | `date` | not in the future |
| `ssn_last_4` | `str` | pattern `^\d{4}$` (string, not int) |
| `address`, `phone`, `occupation` | `str` | non-empty; phone kept as-is (formats vary) |
| `customer_since` | `date` | not in the future, after `date_of_birth` |
| `risk_rating` | `Literal["Low","Medium","High"]` | |
| `annual_income` | `float` | ≥ 0 |

**`AccountData`**
| Field | Type | Rule |
|---|---|---|
| `account_id` | `str` | pattern `^CUST_\d{4}_ACC_\d+$` |
| `customer_id` | `str` | model validator: `account_id` starts with `customer_id` |
| `account_type` | `Literal["Checking","Savings","Money_Market","Business_Checking"]` | |
| `opening_date` | `date` | not in the future |
| `current_balance` | `float` | ≥ 0 ("balance validation" from the goal) |
| `average_monthly_balance` | `float` | ≥ 0 (0.0 is allowed) |
| `status` | `Literal["Active","Closed"]` | |

**`TransactionData`**
| Field | Type | Rule |
|---|---|---|
| `transaction_id` | `str` | pattern `^[A-Z]+_[0-9A-F]{8}$` |
| `account_id` | `str` | pattern as above |
| `transaction_date` | `date` | not in the future |
| `transaction_type` | `Literal[...]` | the 12 observed values |
| `amount` | `float` | > 0 |
| `description` | `str` | non-empty |
| `counterparty`, `location` | `str \| None` | blank → `None` |
| `method` | `Literal[...]` | the 7 observed values |

- **Depends on:** 1.2

---

### Step 1.4 – `CaseData` schema
- **Purpose:** the single unit of work passed to the agents: one customer and everything they hold.
- **Output:** `CaseData` with
  - `case_id: str` (format `CASE_<customer_id>_<YYYYMMDDHHMMSS>`, generated by the loader)
  - `customer: CustomerData`, `accounts: list[AccountData]` (min 1), `transactions: list[TransactionData]` (may be empty)
  - `case_created_at: datetime` (UTC)
  - `data_quality_flags: list[str]` (e.g. `"TXN_ab12cd34 dated before account opening"`), so quirks are recorded rather than hidden
  - model validators: every account belongs to the customer; every transaction belongs to one of the case's accounts.
- **Depends on:** 1.3

---

### Step 1.5 – Agent output schemas
- **Purpose:** fix the agents' output contracts **before** writing the agents (Stages 02/03 must fill these).
- **Output:**

**`RiskAnalystOutput`**: `classification: Literal["Structuring","Sanctions","Fraud","Money_Laundering","Other"]`,
`confidence_score: float` (0.0–1.0), `risk_level: Literal["Low","Medium","High","Critical"]`,
`reasoning: str` (non-empty), `key_indicators: list[str]` (min 1).

**`ComplianceOfficerOutput`**: `narrative: str` (validator: **word count < 120**, non-empty),
`narrative_reasoning: str`, `regulatory_citations: list[str]` (min 1), `completeness_check: bool`.
The 5-W and terminology checks are behavioural, so they live in Stage 03, not in the schema.

- **Depends on:** 1.3

---

### Step 1.6 – `ExplainabilityLogger`
- **Purpose:** an append-only audit trail that a regulator can read.
- **Output:** `ExplainabilityLogger(log_file="logs/sar_audit.jsonl")` with
  `log_agent_action(agent_type, action, case_id, input_data, output_data, reasoning, execution_time_ms, success=True, error_message=None, **extra)`.
  Each line contains: `timestamp` (UTC ISO-8601), `event_id` (uuid4), `case_id`, `agent_type`
  (`DataLoader`, `RiskAnalyst`, `ComplianceOfficer`, `Human`), `action`, `input_summary`, `output`,
  `reasoning`, `execution_time_ms`, `success`, `error_message`, `prompt_version`, plus optional
  `model` / `token_usage` (filled from Stage 02 on).
  - creates `logs/` if missing; one `json.dumps` line per event; UTF-8.
  - **PII guard:** strips `ssn_last_4`, `date_of_birth`, `address` and `phone` from logged payloads.
- **Depends on:** 1.1

---

### Step 1.7 – `DataLoader`
- **Purpose:** CSV → validated entities → `CaseData`, with bad rows reported instead of crashing.
- **Output:** `DataLoader(logger, data_dir=DATA_DIR)` with
  - `load_customers() / load_accounts() / load_transactions()`: read with pandas (`dtype=str` so
    nothing is coerced by accident, `keep_default_na=False` so blanks stay `""`), validate each row,
    and return `(valid_records, errors)`, where each error has the row number, ID and Pydantic message.
  - `create_case_from_data(customer, accounts, transactions) -> CaseData`: builds `case_id` and `data_quality_flags`.
  - `build_all_cases() -> list[CaseData]`: groups by customer, builds every case, and logs one
    summary event (counts loaded / rejected / flagged).
- **Depends on:** 1.4, 1.6

---

### Step 1.8 – Foundation tests (`tests/test_foundation.py`)
- **Purpose:** prove the foundation works on real data and fails gracefully on bad data.
- **Output (tests):**
  - **Schema validation:** every row of all 3 CSVs validates; one hand-built valid example per model.
  - **Rejection cases:** bad `risk_rating`, negative balance, zero/negative amount, malformed IDs,
    future date, `ssn_last_4="12a4"`, account not matching its customer, narrative of 120+ words,
    confidence 1.5.
  - **Blank handling:** `counterparty=""` → `None`.
  - **DataLoader:** 150 cases built; account/transaction counts match the CSVs; a temp CSV with a bad
    row yields 1 error and no crash; data-quality flags are produced for pre-opening transactions.
  - **Logger:** writes valid JSONL (one object per line), appends rather than overwrites, and excludes PII fields.
  - Tests use pytest's `tmp_path` so they never write into the real `logs/`.
- **Depends on:** 1.3–1.7

## 5. Files created in this stage

```
pyproject.toml  requirements.txt  requirements-dev.txt  .env.example
src/__init__.py  src/config.py  src/foundation_sar.py
notebooks/01_data_exploration.ipynb
tests/__init__.py  tests/test_smoke.py  tests/test_foundation.py
```

## 6. Decisions (resolved in review, 2026-11-16)

1. **Money type:** `float` for amounts and balances.
2. **Data-quality quirks:** recorded as `data_quality_flags` and logged; rows are not rejected.
3. **CSV reading:** pandas with `dtype=str`, `keep_default_na=False`; Pydantic does all validation.
4. **Leakage:** `transaction_id` stays intact in Stage 01; Stage 02's prompt formatter masks it.
