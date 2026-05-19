# Recommended Blueprint

## Goal

Convert this repo into a Recommended blueprint for automating clinical trial matching: a reusable, governed, deterministic-first product that can run standalone or be embedded into host applications such as Leafy.

The current repo already contains the right core ingredients:

- deterministic trial ingestion and compilation
- explicit rule-family governance
- patient evidence and match profile concepts
- patient-to-trial and trial-to-patient matching
- proof, readiness, and benchmark artifacts

The blueprint gap is mostly product boundary hygiene:

- the public product API is mixed with extracted demo routes
- the UI is still a React compatibility layer
- auth, tenancy, and release gating are not yet first-class runtime requirements

## Product Promise

The Recommended blueprint should guarantee five things:

1. Every published trial contract is immutable, explainable, and versioned.
2. Every patient match result is traceable back to evidence, rule families, and runtime versions.
3. Unsupported or ambiguous criteria never disappear into silent automation; they become visible review work.
4. Matching quality is promoted through benchmark and proof gates, not only through demos.
5. The same engine can run as a standalone product or be embedded by a host shell.

## Demo Scope Strategy

For the product demo, the recommended operating model is one shared platform with two lenses:

- `discovery`
  - broad trial catalog exploration across a wider corpus
  - lighter metadata enrichment
- `breast-oncology-ai`
  - deep canonical matching, screened patient evidence, literature links, and benchmark governance for the breast cancer slice

This avoids duplicating raw or canonical trial data while keeping the expensive AI and review-heavy workflows concentrated on the disease area that already has the strongest assets in this repo.

Recommended implementation rule:

- ingest and canonicalize shared trial records once
- segment demo experiences by tags, readiness state, and focus-lens membership
- keep breast-oncology-specific artifacts such as literature links, match assets, and benchmark packs as separate referenced records rather than duplicate trial copies

## Recommended Architecture

The recommended deployment model is a modular monolith first, with service boundaries made explicit in code and API contracts. That keeps implementation practical while preserving a later path to split services if scale or compliance needs justify it.

### 1. Trial Contract Service

Owns:

- remote trial search
- trial source ingestion
- immutable source versioning
- canonical criterion extraction
- rule-family compilation
- review queue and review application
- published trial contracts

Primary current sources:

- `backend/clinical_trials_service_app/app/routes/clinical_trials.py`
- `backend/clinical_trials_service_app/app/services/ingest_service.py`
- `backend/clinical_trials_service_app/app/services/compiler.py`
- `backend/clinical_trials_service_app/app/services/parser.py`
- `backend/src/clinical_trial_matching_agent/standalone/service_materializer.py`

### 2. Patient Evidence Service

Owns:

- patient linkage state
- patient evidence extraction
- patient evidence profiles
- patient match profiles
- cohort gating and readiness
- patient coverage snapshots

Primary current sources:

- patient-profile logic in `backend/src/clinical_trial_matching_agent/leafy_extract/services/patient_curation.py`
- supporting extraction and linkage logic in `backend/src/clinical_trial_matching_agent/leafy_extract/services/pharma_services.py`

### 3. Matching Runtime Service

Owns:

- patient-to-trial screening
- trial-to-patient screening
- runtime-mode enforcement
- run records and explainability traces
- deterministic and review-safe outcome handling

Primary current sources:

- `backend/clinical_trials_service_app/app/services/matcher.py`
- current match routes in `backend/clinical_trials_service_app/app/routes/clinical_trials.py`

### 4. Governance and Proof Service

Owns:

- dataset readiness
- family coverage
- unresolved criteria analytics
- benchmark pack generation
- review-sheet generation
- proof reports and release gates

Primary current sources:

- `backend/clinical_trials_service_app/app/services/proof_service.py`
- `backend/clinical_trials_service_app/app/services/micro_benchmark_service.py`
- benchmark artifacts under `benchmarks/output/clinical_trials_micro_benchmark`

### 5. Experience and Integration Layer

Owns:

- standalone product UI
- embedding adapters
- API client contracts
- host-shell integration boundaries

Primary current sources:

- target shell: `frontend/app`
- temporary compatibility layer: `frontend/leafy-react-compat`

## Canonical Product Artifacts

The Recommended blueprint should make the following artifacts explicit and durable.

### Trial-side artifacts

- `TrialSourceVersion`
  - raw normalized payload from ClinicalTrials.gov or another upstream source
  - immutable once stored
  - supports recompile and provenance

- `TrialContract`
  - the published matching-ready trial object
  - contains status, disease area, runtime readiness, current version pointer, and display metadata

- `TrialContractVersion`
  - versioned compiled contract for a specific trial source state
  - immutable once published

- `CriterionNode`
  - one structured or canonical eligibility node
  - stores normalized text, selected family, parse status, compiled rule, review state, and audit fields

- `RuleFamilyDefinition`
  - governed registry entry for a family such as age minimum, HER2 status, ECOG, or manual review buckets

- `ReviewDecision`
  - explicit reviewer action over a criterion
  - captures reviewer, timestamp, rationale, and resulting family or override

### Patient-side artifacts

- `PatientLinkage`
  - authoritative linkage state between local patient identity and the longitudinal clinical or FHIR identity used for matching

- `PatientEvidenceProfile`
  - evidence-first patient view
  - stores resolved features, evidence paths, conflicts, and completeness state

- `PatientMatchProfile`
  - matcher-ready patient projection
  - stores normalized demographics, disease window, cohort membership, runtime readiness, and screening filters

### Runtime and governance artifacts

- `MatchRun`
  - one patient-to-trial or trial-to-patient execution record
  - stores request parameters, runtime versions, results, explanations, and run status

- `ProofReport`
  - governed readiness snapshot for a corpus, population mode, and target disease window

- `BenchmarkPack`
  - frozen pack of trials, patients, and expected evaluation outcomes for internal and SME review

## Recommended Public API v1

The public product contract should be API-first and narrower than the current extracted surface. The current `/clinical-trials/*` routes are the best base to keep expanding.

### Trial contract APIs

- `GET /api/v1/trials/health`
- `POST /api/v1/trials/search`
- `POST /api/v1/trials/ingest`
- `POST /api/v1/trials/ingest/bulk`
- `GET /api/v1/trials`
- `GET /api/v1/trials/{trialId}`
- `GET /api/v1/trials/{trialId}/criteria`
- `GET /api/v1/trials/{trialId}/canonical-tree`
- `POST /api/v1/trials/{trialId}/recompile`
- `GET /api/v1/rule-families`
- `GET /api/v1/reviews/queue`
- `POST /api/v1/reviews/apply`

### Patient evidence APIs

- `GET /api/v1/patients/{patientRef}/evidence`
- `GET /api/v1/patients/{patientRef}/match-profile`
- `GET /api/v1/patients/{patientRef}/linkage`
- `POST /api/v1/patients/{patientRef}/linkage/resolve`
- `GET /api/v1/patient-coverage`

### Matching APIs

- `POST /api/v1/matches/patient/{patientRef}`
- `POST /api/v1/matches/trial/{trialId}`
- `GET /api/v1/match-runs`
- `GET /api/v1/match-runs/{runId}`

### Governance APIs

- `GET /api/v1/governance/proof/latest`
- `GET /api/v1/governance/benchmarks/micro/latest`
- `GET /api/v1/governance/benchmarks/micro/latest/review-sheet`
- `GET /api/v1/governance/analytics/unresolved-top`
- `GET /api/v1/governance/analytics/family-coverage`

### Admin and terminology APIs

- `GET /api/v1/terminology/status`
- `POST /api/v1/terminology/snomed/resolve`
- `POST /api/v1/terminology/bindings/{bindingId}/validate`

## Current-to-Target API Mapping

### Keep and promote

- current `/clinical-trials/*` routes become the public backbone of the blueprint
- current benchmark and analytics routes remain public, but should move under a governance namespace

### Extract and promote

- current `/pharma/patient_evidence_profile`
- current `/pharma/patient_match_profile`
- current `/pharma/patient_curation/coverage`
- current `/pharma/linkage/pending`
- current `/pharma/linkage/resolve`

These should become first-class product APIs under `/api/v1/patients/*` and `/api/v1/patient-coverage`.

### Internalize or retire

- `/pharma/bootstrap`
- `/pharma/control_plane/*`
- `/pharma/source/status`
- `/pharma/sync/clinicaltrials`
- `/pharma/synthea/*`
- `/pharma/trials/llm_adjudicate*`
- `/pharma/cohort_graph`
- generic demo routes such as `/appointments`

These are useful today for extraction and demo operations, but they should not define the Recommended public contract.

## Recommended Persistence Model

The blueprint should move toward product-neutral collection names even if compatibility aliases remain during migration.

### Recommended logical collections

- `trial_source_versions`
- `trial_contracts`
- `trial_contract_versions`
- `trial_criteria`
- `rule_family_registry`
- `review_decisions`
- `patient_linkages`
- `patient_evidence_profiles`
- `patient_match_profiles`
- `match_runs`
- `proof_reports`
- `benchmark_packs`
- `terminology_bindings`

### Transition rule

Keep current `pharma_*` and `clinical_trial_*` collections readable during migration, but centralize all naming in a single settings module and stop leaking legacy names into public contracts.

## Runtime Rules

The Recommended blueprint should operate under these rules:

- only published trial contracts are matchable
- every criterion must resolve to deterministic, manual review, or not-patient-rule
- unsupported logic is visible and measurable
- every match run records contract versions, family registry version, patient profile version, and runtime mode
- benchmark and proof artifacts are generated from the same runtime used for matching

## Security, Compliance, and Platform Requirements

These requirements should be mandatory for the Recommended blueprint:

- authentication for every non-health endpoint
- role-based authorization for ingestion, review, matching, and admin operations
- tenant or workspace isolation if used across multiple organizations
- PHI-safe logging with redaction-by-default
- immutable review and audit history
- provenance fields on published contracts and match runs
- retention and deletion policy for patient evidence artifacts
- environment-specific toggles for synthetic data versus live clinical data

## Testing and Release Gates

The repo should not be Recommended until it has a visible first-party test and release story.

### Required test layers

- API contract tests for public endpoints
- golden tests for criterion compilation and materialization
- matcher fixture tests for patient-to-trial and trial-to-patient runs
- regression tests for rule-family behavior
- UI smoke tests for ingestion, patient curation, matching, and governance paths

### Required release gates

- benchmark pack generation succeeds
- benchmark directional consistency passes
- unresolved family coverage stays within threshold
- runtime-ready trial minimum stays above threshold
- match-ready patient minimum stays above threshold
- public API schema changes are reviewed explicitly

## Codebase Migration Map

### Keep as the product core

- `backend/clinical_trials_service_app/app/routes/clinical_trials.py`
- `backend/clinical_trials_service_app/app/schemas.py`
- `backend/clinical_trials_service_app/app/config.py`
- `backend/clinical_trials_service_app/app/database.py`
- `backend/clinical_trials_service_app/app/services/rule_families.py`
- `backend/clinical_trials_service_app/app/services/matcher.py`
- `backend/clinical_trials_service_app/app/services/proof_service.py`
- `backend/clinical_trials_service_app/app/services/micro_benchmark_service.py`

### Keep, but rename and reposition

- `backend/src/clinical_trial_matching_agent/standalone/service_materializer.py`

Recommended destination:

- `backend/clinical_trials_service_app/app/services/trial_contract_materializer.py`

Rationale:

- it is a product bridge from extracted pharma trial documents into the cleaner contract model
- it should sit with the core trial service, not in a temporary standalone namespace

### Extract into first-class patient modules

- patient linkage and cohort logic from `backend/src/clinical_trial_matching_agent/leafy_extract/services/patient_curation.py`
- patient evidence and patient match profile logic from `backend/src/clinical_trial_matching_agent/leafy_extract/services/pharma_services.py`

Recommended destination:

- `backend/clinical_trials_service_app/app/services/patient_linkage.py`
- `backend/clinical_trials_service_app/app/services/patient_evidence.py`
- `backend/clinical_trials_service_app/app/services/patient_match_profiles.py`

### Shrink to an internal compatibility adapter

- `backend/src/clinical_trial_matching_agent/leafy_extract/routes/pharma_blueprint.py`
- remaining demo-oriented pieces of `backend/src/clinical_trial_matching_agent/leafy_extract/services/pharma_services.py`

Recommended role:

- temporary adapter for Leafy embedding and data bootstrap
- not part of the default standalone Recommended deployment

### Split the current runtime shell

- `backend/main.py`

Recommended destination:

- `backend/app_factory.py` for the public product app
- `backend/compat_factory.py` for the temporary Flask adapter mount

### Replace the compatibility frontend over time

- keep `frontend/app` as the standalone product shell
- treat `frontend/leafy-react-compat` as transitional
- rebuild the public UI around product routes for `ingestion`, `patient`, `matching`, and `governance`

## Delivery Phases

### Phase 1: Contract Freeze

- declare the public API v1 namespace
- keep `/clinical-trials/*` behavior stable while adding patient and governance namespaces
- mark `/pharma/*` as internal or deprecated
- add contract tests and auth middleware

### Phase 2: Patient Service Extraction

- pull patient linkage and evidence logic out of the extracted pharma layer
- create stable patient APIs
- version patient evidence and patient match profiles explicitly

### Phase 3: Governance as a Release Gate

- move benchmark, proof, and family coverage outputs into CI
- require threshold checks before release
- persist proof reports and benchmark pack metadata in governed collections

### Phase 4: Frontend Productization

- replace compatibility-only API clients with a smaller product client
- build a Next-native workbench around the same four workflow areas already visible in the current navigator
- keep Leafy embedding as a consumer of the public APIs and optional UI package

## Exit Criteria for Recommended Status

This repo should be considered a Recommended blueprint when all of the following are true:

- the public contract is centered on a versioned FastAPI product API
- `/pharma/*` is no longer required for the default standalone product flow
- patient evidence and linkage are first-class product modules
- benchmark and proof gates run in CI
- first-party tests cover the public API and core rule behavior
- auth, audit, and PHI-safe logging are enforced by default
- the standalone UI uses product-native APIs rather than a broad extracted compatibility client

## Practical Next Move

The highest-leverage implementation step is to split the current mixed runtime into two explicit layers:

- a public product app built around the clinical-trials service plus new patient and governance modules
- a temporary compatibility adapter that keeps Leafy and extraction-era workflows functioning during migration

That one move makes the rest of the blueprint tractable, because it creates a stable product boundary without forcing an immediate rewrite of every extracted component.
