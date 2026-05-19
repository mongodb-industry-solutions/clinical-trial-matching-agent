# Recovery Blueprint

## Why this doc exists

This repo already contains most of the important ingredients from Leafy Hospital's Pharma Navigator, but the experience is split across two product layers:

- a newer standalone Next.js shell under `frontend/app/*`
- an extracted Leafy compatibility workbench under `frontend/leafy-react-compat/*`

The result is that important functionality still exists, but it is not consistently exposed, benchmark-ready cohort depth is thinner than the original demo, and some public product endpoints are stricter than the compatibility paths they replaced.

This document records:

- what was working in Leafy staging
- what currently works in this standalone repo
- what is regressed or incomplete
- what the target architecture should be for high-accuracy clinical trial matching

## Recovered from Leafy staging

From the staging branch of `Leafy-Hospital-Demo`, the original Pharma Navigator experience centered on five capabilities:

1. Trial ingestion from ClinicalTrials.gov into a governed MongoDB corpus.
2. Patient curation rooted in a real screening episode plus linked longitudinal evidence.
3. One-patient-to-many-trials deterministic screening.
4. One-trial-to-many-patients deterministic cohort identification.
5. A control plane showing readiness, linkage posture, unresolved criteria, and matching governance.

The original UI also emphasized:

- a readable inclusion/exclusion breakdown
- criterion-by-criterion adjudication
- explicit outcomes of `met`, `not met`, or `unknown`
- visible manual-review backlog instead of silent automation
- side-by-side operational proof and matching detail

## Current standalone findings

These findings were verified against the current repo and local data on May 5, 2026.

### Working now

- `GET /clinical-trials/trials` returns canonical breast-cancer trial contracts.
- `GET /clinical-trials/trials/{trialId}/canonical-tree` returns structured criterion trees.
- `POST /clinical-trials/match/patient/{patientRef}` works for deterministic screening.
- `POST /clinical-trials/match/trial/{trialId}` works for deterministic reverse matching.
- `GET /pharma/patient_context/random` still returns a rich evidence pack and linked patient context.
- `GET /pharma/control_plane` works, but it is expensive.

### Regressed or uneven

- The newer standalone shell did not expose the recovered Pharma Navigator route even though the code was present.
- `GET /api/v1/patients/screened/random` is stricter than the compat flow and can return `404` in environments where `/pharma/patient_context/random` still succeeds.
- In authoritative mode, the public patient catalog is much thinner than the original demo posture.
  - In the current environment, `28` breast-cancer patient rows are visible.
  - Only `5` are runnable.
  - Only `1` is both `authoritativeBreastOncology` and `benchmarkRunnable`.
- The control-plane summary is computed on demand and took about `16.5s` in local verification.
- The README described an older query-param launcher flow that no longer matched the actual Next.js app.

## High-accuracy matching blueprint

### Core principle

Use a deterministic-first engine for eligibility decisions and reserve vectors or LLMs for retrieval, ambiguity handling, and evidence summarization, never as the primary source of truth for final hard eligibility decisions.

### Matching stack

1. Trial normalization
   - Ingest from ClinicalTrials.gov API v2 and persist immutable source snapshots.
   - Compile each version into a canonical trial contract and immutable criterion nodes.
   - Separate inclusion, exclusion, workflow-only notes, and unresolved criteria.

2. Criterion decomposition
   - Convert each criterion into a normalized atomic node with:
     - `criterionId`
     - section
     - normalized text
     - family
     - computability
     - required value
     - provenance
     - review status
   - Store trees and flattened leaves together.

3. Rule-family governance
   - Maintain a governed registry of criterion families:
     - age minimum / maximum
     - sex
     - stage group
     - metastatic extent
     - ECOG / performance status
     - receptor status
     - prior therapy
     - washout windows
     - organ function labs
     - contraindication and safety-history buckets
   - Every criterion must end in one of:
     - deterministic family
     - manual-review family
     - unsupported family explicitly tracked in backlog

4. Patient evidence projection
   - Build a `PatientEvidenceProfile` from screening data, FHIR resources, facts documents, and curated overlays.
   - Build a `PatientMatchProfile` from the evidence profile.
   - Keep provenance pointers for every resolved feature so UI and reviewers can trace each decision to source data.

5. Deterministic prefilter
   - Apply hard filters first:
     - recruiting status
     - age
     - sex
     - disease area
     - broad stage window
     - obvious cohort exclusions
   - This stage should maximize recall without admitting obviously impossible trials.

6. Criterion-level adjudication
   - Evaluate criterion nodes one by one.
   - Each node returns:
     - `met`
     - `not_met`
     - `unknown`
     - `manual_review_required`
     - `not_patient_rule`
   - Trial-level outcome is a composition of criterion-level states, not a free-text model judgment.

7. Retrieval and vectors
   - Use vector search only where semantic retrieval is appropriate:
     - trial discovery
     - candidate retrieval before expensive deterministic evaluation
     - evidence snippet recall
     - literature linking
   - Do not let similarity override a deterministic exclusion.
   - Prefer hybrid retrieval:
     - structured filters first
     - vector recall second
     - deterministic adjudication last

8. Controlled ambiguity handling
   - Use LLMs only to:
     - structure unresolved free text into explicit candidate constraints
     - summarize evidence packs
     - propose reviewer actions
     - adjudicate only after deterministic methods fail, and only with citation-bearing outputs
   - LLM outputs must be cached, versioned, reviewable, and never silently converted into final eligibility.

9. Human review loop
   - Unknown or unsupported nodes become durable review tasks.
   - Review results should either:
     - resolve a patient-specific ambiguity
     - create or improve a reusable rule family
     - refine canonical trial structure

10. Governance and release gates
   - No release without benchmark packs, corpus proof, family coverage, and unresolved-criteria analytics.
   - Benchmark promotion should be mandatory for runtime changes.

## Recommended data contracts

### Trial-side artifacts

- `TrialSourceVersion`
- `TrialContract`
- `TrialContractVersion`
- `CriterionNode`
- `RuleFamilyDefinition`
- `ReviewDecision`

### Patient-side artifacts

- `PatientLinkage`
- `PatientEvidenceProfile`
- `PatientMatchProfile`
- `CoverageSnapshot`

### Runtime artifacts

- `MatchRun`
- `CriterionEvaluation`
- `EvidencePointer`
- `ProofReport`
- `BenchmarkPack`

## Recommended UI experience

### 1. Trial Factory

- Search live trials from ClinicalTrials.gov.
- Materialize into canonical contracts.
- Show compile posture:
  - ready
  - needs review
  - blocked

### 2. Patient Graph

- Start from a real screening episode when available.
- Merge with linked FHIR evidence.
- Show coverage, gaps, linkage confidence, stage, receptor status, ECOG, therapy history, and provenance.

### 3. Match Studio

- Show inclusion and exclusion criteria separately.
- For every criterion show:
  - normalized rule
  - patient value
  - evidence pointers
  - deterministic result
  - reason for unknown or manual review
- Trial-level output should summarize:
  - candidate
  - excluded
  - needs more data
  - manual review required

### 4. Governance Center

- Show:
  - trial readiness
  - patient readiness
  - family coverage
  - unresolved criteria hotspots
  - linkage posture
  - benchmark status

## Immediate execution plan

### Phase 1: restore the demo surface

- Keep the new standalone shell.
- Re-expose the original Pharma Navigator route from the extracted compatibility layer.
- Make docs and navigation reflect the real app structure.

### Phase 2: recover benchmark-ready cohort depth

- Rebuild or enrich the demo-screened cohort so authoritative mode has enough patients for compelling reverse matching.
- Promote more linked patients into `authoritativeBreastOncology` only when evidence coverage actually supports it.
- Separate benchmark-ready from prescreen-only cohorts in the UI and API.

### Phase 3: strengthen deterministic coverage

- Expand rule-family coverage for breast oncology high-frequency gaps:
  - receptor status
  - stage/extent context
  - prior therapy lines and washouts
  - safety-history exclusions
  - organ-function labs
- Turn repeated manual-review buckets into governed deterministic families.

### Phase 4: add retrieval safely

- Use vectors for candidate recall and evidence finding, not final eligibility.
- Keep deterministic exclusion authoritative.
- Add per-stage observability so we can inspect structured filter loss, vector recall, and adjudication outcomes separately.

### Phase 5: harden governance

- Cache expensive control-plane summaries.
- Publish proof and benchmark APIs as first-class product routes.
- Gate releases on benchmark deltas and unresolved backlog thresholds.

## Standards and research anchors

- ClinicalTrials.gov API v2 is the right source-of-truth interface for live trial ingestion.
  - https://clinicaltrials.gov/data-about-studies/learn-about-api
- mCODE provides a practical oncology interoperability baseline on FHIR R4 for structured cancer data exchange.
  - https://www.hl7.org/fhir/us/mcode/index.html
- MatchMiner demonstrates the value of explicitly structured trial eligibility and CTML-style contracts for reproducible matching.
  - https://www.nature.com/articles/s41698-022-00312-5
- Criteria2Query supports the design principle that eligibility text should be converted into structured, reviewable intermediate representations rather than treated as opaque free text.
  - https://academic.oup.com/jamia/article/26/4/294/5308980
- TrialGPT is useful evidence that LLMs can help with retrieval, criterion-level assessment, and ranking, but it should remain a layer on top of deterministic governance rather than a substitute for it.
  - https://www.ncbi.nlm.nih.gov/research/trialgpt/about/
- SatIR is especially aligned with the deterministic-first direction because it frames retrieval as interpretable constraint satisfaction and reports stronger recall and usefulness than embedding-heavy baselines.
  - https://arxiv.org/abs/2604.08849

## What changed in this repo now

- The original Pharma Navigator is available again at `/pharmaNavigator`.
- The standalone docs can now describe both the product shell and the recovered workbench.
- This document becomes the recovery reference for closing the remaining gaps.
