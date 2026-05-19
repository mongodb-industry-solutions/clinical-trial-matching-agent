# Clinical Trial Matching Book Blueprint

_Updated May 6, 2026_

## Core Claim

A trustworthy clinical trial matching system is:

1. A trial contract compiler
2. A patient evidence graph
3. A deterministic criterion evaluator
4. A constrained retrieval layer
5. A benchmark and reviewer operating system

AI helps reduce ambiguity, retrieve supporting evidence, and summarize what happened. AI does not get to silently own final eligibility on hard criteria.

## What This Repo Should Become

This repo should not feel like four sequential demos. It should feel like one benchmark product with four live engines behind it:

- `Trial Factory`
  - ingest source trials
  - normalize text into canonical contracts
  - surface inclusion and exclusion nodes with provenance
- `Patient Graph`
  - curate one authoritative patient evidence graph
  - show readiness, linkage, gaps, and provenance
- `Match Studio`
  - execute criterion-by-criterion adjudication
  - replay patient-to-trial and trial-to-patient runs
  - keep deterministic outcomes and AI assistance separate
- `Governance Center`
  - freeze benchmark packs
  - track criterion family coverage
  - expose unresolved clusters
  - measure agreement, release posture, and review burden

The benchmark command center should be the product shell that ties those engines together.

## Design Principles

### 1. Deterministic-first matching

Every inclusion criterion and exclusion criterion must resolve to a typed adjudication state, not a generic score.

Recommended criterion states:

- `met`
- `not_met`
- `unknown_missing_data`
- `unknown_ambiguous_trial_text`
- `manual_review_required`
- `blocked_by_identity_or_linkage`
- `not_applicable_context`

### 2. Criterion-level explainability

The unit of truth is not the trial-level rank. The unit of truth is the atomic criterion evaluation with:

- normalized criterion text
- criterion family
- comparator and threshold
- temporal window
- evidence references
- evaluator version
- adjudication state
- reviewer override metadata

### 3. Provenance everywhere

Every patient-side fact and trial-side normalization step must carry provenance:

- source system
- source record ID
- extraction or curation path
- version timestamp
- reviewer or runtime actor

### 4. Benchmark-first productization

The product should always be able to answer:

- Which frozen pack is this demo using?
- Which patient cohort and trial slice are in scope?
- Which criteria families are fully supported?
- Which criteria clusters still require manual work?
- What agreement do we have against reviewed cases?

### 5. Human review is part of the architecture

Manual review is not failure. Manual review is the explicit and safe outcome when:

- source evidence is missing
- trial text is ambiguous
- the criterion family is not yet sufficiently modeled
- evidence conflicts cannot be deterministically resolved

## Target Architecture

## 1. Source and Contract Layer

Inputs:

- ClinicalTrials.gov API v2
- optional sponsor feeds or curated local trial assets

Outputs:

- `TrialSourceVersion`
- `TrialContract`
- `TrialContractVersion`
- `CriterionNode`
- `RuleFamilyDefinition`

Responsibilities:

- preserve raw source payloads immutably
- compile free text into atomic eligibility nodes
- preserve inclusion and exclusion group structure
- encode provenance and reviewer state
- publish only runtime-ready contract versions

## 2. Patient Evidence Layer

Inputs:

- FHIR R4 resources
- mCODE-aligned oncology facts
- screening data
- curated data-entry overlays
- clinical notes and extracted facts

Outputs:

- `PatientLinkage`
- `PatientEvidenceProfile`
- `PatientMatchProfile`
- `PatientEvidenceFact`

Responsibilities:

- normalize evidence into governed, versioned patient facts
- separate raw evidence from match-ready projections
- preserve provenance for every resolved feature
- expose completeness, conflict, and identity state

## 3. Retrieval Layer

The retrieval order should be:

1. Hard eligibility prefilters
2. Constraint-aware candidate retrieval
3. Semantic retrieval and vector search
4. LLM-assisted evidence retrieval or summarization

Vectors are appropriate for:

- broad candidate recall
- terminology expansion
- note and evidence retrieval
- linking similar unresolved clusters

Vectors are not the final authority for:

- hard biomarker requirements
- age bounds
- ECOG thresholds
- explicit prior-therapy exclusions
- lab cutoffs
- temporal washout rules

## 4. Deterministic Matching Runtime

Each run should produce:

- trial-level summary
- criterion-level adjudications
- trace summaries
- gaps
- contract versions
- patient evidence versions
- benchmark pack context

The runtime should support:

- patient-to-trial matching
- trial-to-patient matching
- scoped replay from frozen benchmark packs
- deterministic-only mode
- deterministic plus AI-assist mode

## 5. Governance and Benchmark Layer

Core artifacts:

- `ProofReport`
- `BenchmarkPack`
- `ExternalReviewPack`
- `SMEReviewResult`
- `MatchRun`

Responsibilities:

- freeze benchmark datasets and replay cases
- track agreement at overall and criterion levels
- measure unresolved review burden
- gate promotion through readiness thresholds
- keep reviewer disagreement taxonomies explicit

## Canonical Criterion Contract

Each atomic criterion should carry at least:

- `criterionId`
- `trialId`
- `section`
- `criterionType`
  - inclusion or exclusion
- `normalizedText`
- `familyId`
- `parseStatus`
- `rulePayload`
- `temporalWindow`
- `dataDependencies`
- `determinismClass`
- `reviewStatus`
- `sourceProvenance`
- `audit`

Recommended `determinismClass` values:

- `fully_deterministic`
- `deterministic_with_normalized_lookup`
- `requires_temporal_reasoning`
- `requires_semantic_disambiguation`
- `manual_family`

## Patient Evidence Contract

Each evidence fact should carry:

- `factId`
- `patientRef`
- `featureKey`
- `resolvedValue`
- `valueType`
- `effectiveDate`
- `status`
- `supportingEvidenceRefs`
- `conflictState`
- `provenance`
- `curationState`

This keeps the patient graph reviewable and prevents the match runtime from depending on opaque blobs.

## Safe AI Roles

AI should be allowed to help with:

- criterion normalization suggestions
- family classification suggestions
- evidence retrieval from notes or literature
- semantic expansion for retrieval
- patient-summary generation
- explanation drafting for reviewers

AI should not silently finalize:

- hard eligibility on explicit threshold rules
- exclusions when evidence is absent
- ambiguity resolution without provenance
- irreversible reviewer decisions

## Benchmark Program

The benchmark program should evaluate three levels:

### 1. Retrieval

- recall of relevant trials before adjudication
- stage-window-aware retrieval quality
- effect of constrained retrieval versus pure semantic retrieval

### 2. Criterion Adjudication

- agreement by family ID
- agreement by criterion type
- agreement by temporal reasoning category
- unknown versus manual-review routing quality

### 3. Trial-Level Decisioning

- agreement on candidate versus excluded versus manual review
- directional consistency across patient-to-trial and trial-to-patient
- reviewer burden per supported family and per pack

## Experience Blueprint

The UI should make the system feel industrial, not academic:

- the benchmark command center is the default landing
- frozen showcase cases open the live studios already scoped
- one click should move from benchmark to patient evidence to trial contract to criterion replay
- unresolved logic must be visible on the main path
- the benchmark pack ID and target window must always be visible
- patient, trial, and criterion provenance should be inspectable without losing context

## Roadmap For This Repo

### Phase 0. Recovery

Status:

- recovered original Pharma Navigator workbench surface
- restored the multi-space route
- reintroduced the original visual language

### Phase 1. Benchmark Command Center

Build:

- integrated benchmark landing canvas
- showcase replay links into live studios
- family coverage, unresolved cluster, and recent run visibility

### Phase 2. Contract Formalization

Build next:

- richer atomic criterion IR
- explicit temporal semantics
- stricter criterion family typing
- reviewer override lineage

### Phase 3. Evidence Graph Hardening

Build next:

- mCODE-aligned evidence fact model
- stronger provenance on patient facts
- conflict resolution policies
- identity and linkage adjudication states

### Phase 4. Retrieval Stack Separation

Build next:

- hard prefilter service
- constraint-aware retrieval stage
- vector retrieval as an assistive layer
- evaluator-safe AI interfaces

### Phase 5. Benchmark and Release Gates

Build next:

- criterion-level gold labels
- reviewer workflow improvements
- release gates for benchmark promotion
- scale benchmark cohort expansion

## Research Anchors

These are the best anchors for the blueprint currently in use:

- ClinicalTrials.gov API v2
  - https://clinicaltrials.gov/data-about-studies/learn-about-api
- HL7 mCODE on FHIR R4
  - https://hl7.org/fhir/us/mcode/
- HL7 FHIR Provenance
  - https://hl7.org/fhir/R4/provenance.html
- MatchMiner and CTML
  - https://www.nature.com/articles/s41698-022-00312-5
- Criteria2Query
  - https://academic.oup.com/jamia/article/26/4/294/5308980
- TrialGPT
  - https://www.nature.com/articles/s41467-024-53081-z
  - https://www.ncbi.nlm.nih.gov/research/trialgpt/faq/
- SatIR
  - https://arxiv.org/abs/2604.08849
  - https://satir.genie.stanford.edu/

## Final Position

The existing direction in this repo is good. The important idea was already right:

- criterion-by-criterion matching
- deterministic first
- explicit unknown states
- patient-to-trial and trial-to-patient symmetry
- operational proof and benchmark artifacts

What turns it into a book-grade blueprint is stricter formalism:

- more explicit contracts
- stronger provenance
- clearer AI boundaries
- deeper temporal reasoning
- benchmark gates that feel like release engineering, not just demo prep
