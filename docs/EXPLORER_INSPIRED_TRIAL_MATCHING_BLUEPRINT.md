# Explorer-Inspired Trial Matching Blueprint

This project should keep the legacy Pharma Navigator user interface as the master experience and improve the matching architecture underneath it.

The right inspiration from `Leafy Hospital Explorer` is not the full chatbot product surface.

The right inspiration is the discipline around:

- governed contracts
- deterministic bounded plans
- explicit resolver logic
- evidence-first grounding
- evaluation and release gates

## 1. What Explorer Actually Gives Us

In `Leafy-Hospital-Demo`, the strongest reusable pattern is:

1. define a governed semantic surface
2. resolve the request into that surface
3. compile a bounded execution plan
4. execute deterministic retrieval and tools
5. compose the final answer on top of grounded evidence

Key references:

- Explorer architecture: [docs/explorer-blueprint.md](</Users/francesc.mateu/Documents/GitHub/Leafy-Hospital-Demo/docs/explorer-blueprint.md>)
- Conformance model: [docs/semantic-product-conformance-pack.md](</Users/francesc.mateu/Documents/GitHub/Leafy-Hospital-Demo/docs/semantic-product-conformance-pack.md>)
- Canonical convergence: [docs/semantic-product-canonical-convergence.md](</Users/francesc.mateu/Documents/GitHub/Leafy-Hospital-Demo/docs/semantic-product-canonical-convergence.md>)
- Canonical product compiler: [canonical_semantic_product.py](</Users/francesc.mateu/Documents/GitHub/Leafy-Hospital-Demo/externals/webservices/explorer_components/canonical_semantic_product.py>)
- Resolver: [resolver.py](</Users/francesc.mateu/Documents/GitHub/Leafy-Hospital-Demo/externals/webservices/explorer_components/resolver.py>)
- Planner: [semantic_planner.py](</Users/francesc.mateu/Documents/GitHub/Leafy-Hospital-Demo/externals/webservices/explorer_components/semantic_planner.py>)
- Grounding bundle: [grounding.py](</Users/francesc.mateu/Documents/GitHub/Leafy-Hospital-Demo/externals/webservices/explorer_components/grounding.py>)

## 2. What We Should Reuse

### A. Stable product contract

Explorer defines a stable contract before execution.

For clinical trial matching, the equivalent is not a generic semantic-product registry with dozens of products.
It is a small governed family of matching surfaces:

- `patient_to_trials`
- `trial_to_patients`
- `trial_ingest_compile`
- `criterion_review`

Each should have:

- required anchors
- allowed scope
- deterministic retrieval policy
- execution policy
- grounding policy
- output contract

### B. Resolver logic separate from product definition

Explorer keeps custom matching logic in the resolver, not hidden inside each product definition.

For trials, this means:

- keep `trial` contracts stable
- keep `patient` evidence contracts stable
- put request-routing logic in a thin resolver layer

Examples:

- if the user selected a trial, route to `trial_to_patients`
- if the user selected a patient, route to `patient_to_trials`
- if anchors are missing, emit clarification instead of guessing
- if a trial is not runtime-ready, block or downgrade cleanly

### C. Deterministic bounded plan before execution

Explorer compiles a canonical query plan before running tools.

For trials, the equivalent should be:

- trial `match_contract`
- trial `search_contract`
- patient runtime-readiness and match-filter profile
- declared shortlist strategy
- declared final decision strategy

This is already partially present here:

- contract materialization: [compiler.py](../backend/clinical_trials_service_app/app/services/compiler.py)
- trial projection: [ingest_service.py](../backend/clinical_trials_service_app/app/services/ingest_service.py)

### D. Evidence-first grounding bundle

Explorer explicitly builds grounded claims and support refs before answer composition.

For trial matching, the final decision should always be reducible to:

- prefilter evaluations
- criterion evaluations
- evidence refs and evidence paths
- missing evidence gaps
- manual-review reasons

This is already close to our canonical matcher shape:

- criterion execution: [matcher.py](../backend/clinical_trials_service_app/app/services/matcher.py)
- legacy compat translation: [pharma_canonical_compat.py](../backend/src/clinical_trial_matching_agent/leafy_extract/services/pharma_canonical_compat.py)

### E. Conformance and release audit

Explorer has a strong contract-audit mindset.

For trials, we should adopt:

- contract completeness checks
- criterion-family coverage checks
- search-contract coverage checks
- benchmark pack checks
- UI trace completeness checks

## 3. What We Should Not Copy

We should not import the full Explorer architecture into trial matching.

That would be too much.

Specifically, we should avoid:

- a large multi-product semantic registry for the demo
- generic chat-first resolver flows for core trial matching
- dynamic multi-tool orchestration at answer time for basic eligibility
- a heavy authoring studio before the deterministic matcher is mature
- overusing LLM routing where deterministic route selection is enough

Clinical trial matching is narrower and more structured than Explorer.
That is a strength.

## 4. Recommended Clinical Trial Architecture

## 4.1 Match Request Envelope

Create a small first-class request contract for matching.

Suggested shape:

- `direction`: `patient_to_trials` or `trial_to_patients`
- `patientRef`
- `trialId`
- `populationMode`
- `stageTargetWindow`
- `limit`
- `requestedEvidenceViews`
- `uiContext`

This is the trial equivalent of Explorer's intent envelope, but much lighter.

## 4.2 Match Resolver

Create a thin deterministic resolver layer that:

- validates anchors
- selects direction
- applies runtime gates
- chooses whether clarification is required
- selects the shortlist plan

This should not do answer composition.
It should only decide which governed plan runs.

## 4.3 Match Plan

Materialize a canonical match plan with explicit stages:

1. `load_trial_contract`
2. `load_patient_scope`
3. `shortlist_candidates`
4. `apply_hard_prefilter`
5. `evaluate_inclusion_criteria`
6. `evaluate_exclusion_criteria`
7. `summarize_gaps_and_manual_review`
8. `rank_results`

This should be persisted in the run document or reconstructable from it.

## 4.4 Retrieval Contract

Use retrieval only for shortlist support.

The shortlist contract should declare:

- safe structured filters
- optional vector filter
- keyword terms
- candidate signals
- ranking blend policy
- shortlist size

This is now partially implemented as:

- `match_contract.search_contract` in [compiler.py](../backend/clinical_trials_service_app/app/services/compiler.py)
- compat route consumption in [pharma_blueprint.py](../backend/src/clinical_trial_matching_agent/leafy_extract/routes/pharma_blueprint.py)

## 4.5 Final Eligibility Contract

Final eligibility must remain purely criterion-based.

The contract should make explicit that:

- every inclusion criterion is checked independently
- every exclusion criterion is checked independently
- shortlist retrieval cannot flip an unmet criterion into eligible
- vectors cannot override deterministic evidence
- unknowns and manual-review cases remain explicit

The current canonical evaluator is the right core:

- [evaluate_trial_match in matcher.py](../backend/clinical_trials_service_app/app/services/matcher.py)

## 4.6 Grounding Contract

Add a dedicated matching-grounding bundle per result.

Suggested structure:

- `claimSet`
- `criterionSupport`
- `prefilterSupport`
- `missingEvidence`
- `manualReviewQueue`
- `supportRefs`
- `verifierStatus`

This should be the trial equivalent of Explorer grounding, but grounded in criterion execution rather than free-text answer claims.

## 4.7 UI Contract

Do not replace the legacy UI.

Instead, enrich the existing Navigator surfaces with better structured trace data:

- show the resolved shortlist contract
- show which shortlist filters were applied
- show inclusion results one by one
- show exclusion results one by one
- show why a row is `candidate`, `excluded`, or `needs more data`
- show the exact missing evidence and fillability

The current UI already has the right surfaces:

- main Navigator flow: [PharmaNavigator.js](../frontend/leafy-react-compat/src/pages/PharmaNavigator/PharmaNavigator.js)
- candidate pipeline summary: [CandidatePipelineSummary.js](../frontend/leafy-react-compat/src/components/PharmaMatching/CandidatePipelineSummary.js)
- matching flow explainer: [MatchingPipelineExplainer.js](../frontend/leafy-react-compat/src/components/PharmaMatching/MatchingPipelineExplainer.js)

## 5. Concrete Mapping From Explorer To Trial Matching

### Explorer `semantic product`

Trial equivalent:

- governed matching surface such as `trial_to_patients`

### Explorer `resolver`

Trial equivalent:

- matching direction and anchor resolver

### Explorer `canonical query plan`

Trial equivalent:

- materialized match plan plus `match_contract.search_contract`

### Explorer `grounding policy`

Trial equivalent:

- evidence-path and criterion-support policy for every eligibility decision

### Explorer `tool graph`

Trial equivalent:

- shortlist retrieval plus deterministic criterion evaluator

### Explorer `evaluation library`

Trial equivalent:

- benchmark packs, gold pairs, SME review sets, and contract audits

## 6. Proposed Implementation Roadmap

### Phase 1. Stabilize the request and plan contracts

- add a lightweight match request envelope
- add a persisted match plan shape
- expose it through `pipelineVisibility`

### Phase 2. Separate inclusion and exclusion execution more explicitly

- keep one result row per criterion
- add section-aware summaries
- ensure UI can render inclusion and exclusion lanes independently

### Phase 3. Add a grounding bundle for each result

- attach support refs, evidence paths, gaps, and verifier status
- make the final score auditable from deterministic inputs

### Phase 4. Add conformance checks

- every runtime-ready trial must have:
  - compiled criteria
  - match contract
  - search contract
  - deterministic criterion coverage summary
- every match run must expose:
  - shortlist trace
  - prefilter trace
  - criterion trace
  - gap trace

### Phase 5. Improve retrieval without changing the UI

- keep vectors optional
- improve structured shortlist ranking
- add more criterion-derived search terms
- add patient-profile canonical filters where safe

## 7. Recommended Bottom Line

The right clinical-trial architecture is:

- not full Explorer
- not generic RAG
- not legacy opaque heuristics

It should be:

`a contract-governed deterministic matching system with retrieval only for shortlist support and criterion-by-criterion evidence as the source of truth`

That is the cleanest way to:

- keep the UI you want
- deepen the canonical engine
- preserve explainability
- avoid over-engineering
- make the system benchmarkable and reviewable
