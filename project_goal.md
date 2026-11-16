# Goal 
  To architect and build an intelligent, two-pronged AI system that can not only think like a seasoned Risk Analyst to detect suspicious activities but also write like a Compliance Officer to articulate these findings in perfectly crafted Suspicious Activity Reports. 
  This system will be the critical tool that empowers the compliance team to move faster, act with greater precision, and ultimately protect the integrity of the financial system, all while under the watchful eye of regulatory examiners who will scrutinize your work for its accuracy, efficiency, and transparency.

## Product
   Build an AI-powered Suspicious Activity Report (SAR) processing system that automates financial crime detection using a multi-agent architecture with Chain-of-Thought and ReACT prompting strategies. 
   This complete SAR processing system will:
   1.Detect suspicious financial activity using AI-powered pattern recognition.
   2.Generate regulatory-compliant reports for FinCEN submission.
   3.Implement human-in-the-loop decision gates for compliance oversight.
   4.Create comprehensive audit trails for regulatory examination.

## Project instructions 
   - if any new tasks are added here , clearly specity how has added it developer or claude for future reference .
   - @project_system_arch.png doc for the overall flow.

## Crafting the SAR Narrative
  A critical part of the SAR is the written narrative, which explains the suspicious activity. A well-written narrative is factual, objective, and concise (typically 100-200 words). It must answer the five essential questions:
  1.Who is conducting the activity?
  2.What instruments or mechanisms are being used?
  3.When did the activity occur?
  4.Where did the activity take place?
  5.Why is the activity considered suspicious?

## System Architecture Overview
  📊 CSV Data → 🔍 Risk Analyst Agent(Chain-of-Thought) → 👤 Human Review → ✅ Compliance Officer Agent(ReACT Framework) → 📄 SAR Filing  
  
## Development Path

  ### Phase 1: Foundation & Data Modeling
  🎯 **Goal:** Understand financial data and build foundation components

  **Tasks:**
  1. Explore customer, account, and transaction data
  2. Identify suspicious activity patterns
  3. Implement Pydantic schemas
  4. Create data loading and audit logging systems

  **Deliverable:** Working foundation module with validated schemas

  **Core Implementation (`src/foundation_sar.py`):**
    - [ ] **CustomerData Schema**: Pydantic model matching `data/customers.csv` structure
    - [ ] **AccountData Schema**: Pydantic model for account information with balance validation
    - [ ] **TransactionData Schema**: Transaction records with amount and date validation
    - [ ] **CaseData Schema**: Unified schema combining customer, accounts, and transactions
    - [ ] **RiskAnalystOutput Schema**: Structured output for risk analysis results
    - [ ] **ComplianceOfficerOutput Schema**: Structured output for compliance narratives
    - [ ] **ExplainabilityLogger Class**: Audit logging system with JSONL output
    - [ ] **DataLoader Class**: CSV data processing and case object creation

  **Validation Requirements:**
    - All schemas must validate successfully with provided CSV data
    - Implement field validators for critical data (dates, amounts, risk ratings)
    - Handle optional fields and missing data gracefully
    - Include comprehensive error messages for validation failures

  ### Phase 2: Risk Analyst Agent
  🎯 **Goal:** Build Chain-of-Thought reasoning for suspicious activity classification

  **Tasks:**
  1. Study Chain-of-Thought prompting methodology
  2. Implement risk analysis agent in `risk_analyst_agent.py`
  3. Test with different suspicious activity patterns
  4. Validate JSON output parsing and error handling

  **Deliverable:** Risk Analyst Agent that classifies 5 crime types with confidence scores

  **Core Implementation (`src/risk_analyst_agent.py`):**
  - [ ] **Chain-of-Thought System Prompt**: 5-step reasoning framework for financial crime analysis
  - [ ] **RiskAnalystAgent Class**: Main agent class with OpenAI integration
  - [ ] **analyze_case Method**: Process CaseData and return RiskAnalystOutput
  - [ ] **JSON Parsing Logic**: Robust extraction and validation of LLM responses
  - [ ] **Error Handling**: Comprehensive error recovery for API failures and malformed outputs

  **Classification Requirements:**
  - Support 5 suspicious activity types: Structuring, Sanctions, Fraud, Money_Laundering, Other
  - Generate confidence scores (0.0-1.0) for all classifications
  - Provide clear reasoning chains following Chain-of-Thought methodology
  - Include key suspicious indicators in structured format
  - Assign risk levels: Low, Medium, High, Critical

  ### Phase 3: Compliance Officer Agent
  🎯 **Goal:** Build ReACT framework for regulatory narrative generation

  **Tasks:**
  1. Learn ReACT (Reasoning + Action) prompting
  2. Implement compliance agent in `compliance_officer_agent.py`
  3. Generate SAR narratives ≤120 words with regulatory citations
  4. Validate narrative completeness and terminology

  **Deliverable:** Compliance Officer Agent that generates regulatory-compliant narratives
    ### Phase 3 Deliverables: Compliance Officer Agent

    **Core Implementation (`src/compliance_officer_agent.py`):**
    - [ ] **ReACT System Prompt**: Reasoning + Action framework for narrative generation
    - [ ] **ComplianceOfficerAgent Class**: Main agent class with regulatory focus
    - [ ] **generate_compliance_narrative Method**: Create SAR narratives from risk analysis
    - [ ] **Narrative Validation**: Word count limits (≤120 words) and terminology checking
    - [ ] **Regulatory Citations**: Include relevant BSA/AML regulation references

    **Compliance Requirements:**
    - Generate narratives suitable for FinCEN submission
    - Include all required elements: who, what, when, where, why
    - Use appropriate regulatory terminology and professional tone
    - Validate narrative completeness and regulatory compliance
    - Provide reasoning for narrative construction decisions

  ### Phase 4: System Integration
  🎯 **Goal:** Build complete two-stage workflow with human oversight

  **Tasks:**
  1. Integrate both agents into unified workflow
  2. Implement human-in-the-loop decision gates
  3. Generate complete SAR documents
  4. Create efficiency metrics and cost optimization

  **Deliverable:** End-to-end SAR processing system with audit trails  
  **Workflow Implementation :**
  - [ ] **Two-Stage Processing**: Risk analysis → Human review → Compliance generation
  - [ ] **Human Decision Gates**: Interactive approval/rejection points
  - [ ] **SAR Document Generation**: Complete regulatory forms with all required fields
  - [ ] **Audit Trail Creation**: Comprehensive logging of all decisions and reasoning
  - [ ] **Efficiency Metrics**: Cost tracking and performance optimization demonstrations

  **Integration Requirements:**
  - Process real cases from provided CSV data
  - Demonstrate human-in-the-loop decision making
  - Generate complete SAR documents in JSON format
  - Create audit logs suitable for regulatory examination
  - Show cost optimization benefits of two-stage approach


  ### Phase 5: UI design
    - use Streamlit for the UI design

  ### Phase 6:Test Categories Breakdown
  
    **Foundation Tests (`test_foundation.py`):**
      - **Schema Validation**: Verify Pydantic models work with real CSV data
      - **Data Loading**: Test DataLoader creates unified case objects correctly
      - **Audit Logging**: Validate ExplainabilityLogger captures all operations
      - **Error Handling**: Ensure graceful handling of malformed data

    **Risk Analyst Tests (`test_risk_analyst.py`):**
    - **Prompt Engineering**: Test Chain-of-Thought reasoning framework
    - **Classification Accuracy**: Verify correct suspicious activity categorization
    - **JSON Parsing**: Test structured output handling and error recovery
    - **Edge Cases**: Validate behavior with unusual or minimal data

    **Compliance Officer Tests (`test_compliance_officer.py`):**
    - **ReACT Framework**: Test Reasoning + Action prompt structure
    - **Narrative Quality**: Verify regulatory compliance and word limits
    - **Citation Accuracy**: Test proper regulatory references
    - **Integration**: Validate works with Risk Analyst outputs

    **Integration Tests:**
    - **End-to-End Workflow**: Complete processing from CSV to SAR document
    - **Human Gates**: Test decision points and user interaction
    - **Performance**: Validate system efficiency and cost optimization

  ### Test Data and Scenarios

    The testing framework includes:
    - **Synthetic Test Cases**: Pre-built scenarios for each suspicious activity type
    - **Real Data Validation**: Tests using the provided CSV datasets
    - **Edge Case Coverage**: Malformed inputs, missing data, API failures
    - **Performance Benchmarks**: Response time and accuracy metrics

  ### Phase 7 : CI/CD pipelines to GCP
    - github actions for the CI pipeliens
    - cloudrun for the deployment
    - use the same GCP account , but create seperate resources for this project


  ### Phase 8 : AI QA Framework 
  Where each AI QA layer lives
    | Layer | Where |
    | A – Deterministic testing (minimal) | Tests in **every** phase, run by **CI**. Harness code gets unit tests with fakes. |
    | B – Probabilistic evals (**main**) | Graders → harness  → slices→ feedback loop 
    | C – System metrics & drift (**main**) | Run log → logging/BigQuery →  dashboard → alerts |