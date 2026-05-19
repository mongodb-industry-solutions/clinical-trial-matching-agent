# Comprehensive AI Demo Proposal

## Goal

Reposition this repo from an extracted clinical trial matcher into a polished 2026 MongoDB demo for AI-driven clinical trial intelligence.

The recommendation is not to turn the product into a loose literature search app. The right move is to keep deterministic trial matching as the trust anchor, then build a strong AI evidence and discovery layer around it.

## Executive Direction

The demo should tell one clear story:

> MongoDB is the operational knowledge layer for AI-assisted clinical trial matching. It connects canonical trial contracts, screened patient evidence, literature signals, controlled vocabularies, and agent traces in one platform.

That is stronger than either of these weaker stories:

- "here is a search app over public trial data"
- "here is an LLM that tries to match patients to studies"

The demo should instead show:

1. governed ingestion of trial criteria
2. authoritative patient evidence assembly
3. deterministic eligibility matching
4. AI-assisted retrieval, summarization, and evidence linking
5. operational proof, governance, and auditability

## Recommended Scope Model

The best practical model is a two-lens demo built on one shared data foundation.

### Lens 1: General Discovery

Purpose:

- broad trial discovery
- sponsor, phase, disease, and intervention exploration
- "what is in the corpus?" storytelling
- faceted Atlas Search and aggregation demos

Data posture:

- light and broad
- trial metadata first
- minimal enrichment outside what is already present in ClinicalTrials.gov

### Lens 2: Breast Oncology AI Deep Dive

Purpose:

- authoritative AI agent workflow
- canonical eligibility
- patient evidence assembly
- deterministic match runs
- literature linking
- governance and benchmark proof

Data posture:

- narrow and deep
- breast-cancer-focused trial contracts
- screened breast-oncology patients
- focused PubMed and MeSH evidence layer
- higher-cost AI enrichment only where it creates visible value

This is the right tradeoff. It gives the demo breadth without forcing us to fully curate every disease area.

## Avoiding Data Duplication

We should avoid creating separate copies of the same trial data just because the demo has two modes.

Recommended approach:

- ingest trial source data once
- persist canonical contracts once
- mark trials with reusable tags such as disease area, demo lens, and readiness state
- build focused derivative records only where necessary for the breast-oncology deep dive

In practice:

- `trial_source_versions` stays global
- `trial_contracts` stays global
- `trial_contract_versions` stays global
- a field such as `demoLenses: ["discovery", "breast-oncology-ai"]` decides where the trial appears
- breast-oncology-specific artifacts such as literature links, benchmark packs, and patient match assets are stored separately and reference the shared trial ids

That means:

- no duplicate raw trial documents
- no duplicate canonical trial contracts
- no duplicate general search corpus
- only targeted deep-dive artifacts for the breast cancer subset

## What Should Be Broad vs Deep

### Broad

- general ClinicalTrials.gov catalog
- trial search and faceting
- sponsor and intervention exploration
- high-level trial trend analytics

### Deep

- canonical trial compilation
- patient evidence profiles
- patient match profiles
- deterministic eligibility
- AI match briefs
- PubMed literature links
- benchmark packs and governance reports

This keeps the storage model cleaner and keeps the demo credible.

## What We Have Now

The current repo already has the right core for a serious clinical demo:

- canonical trial ingestion and compilation
- explicit rule families and review workflows
- patient evidence and patient match profile concepts
- patient-to-trial and trial-to-patient matching
- proof, benchmark, and control-plane surfaces
- product patient routes that require authoritative screened Synthea-linked patients

This is a better trust foundation than MongoRx because it already has an operational matching engine instead of only a discovery shell.

The main gap is narrative shape. Right now the product reads as:

- an extraction from Leafy
- a deterministic matching workbench
- a UI that still quarantines legacy embeddings and retrieval assets

For a 2026 MongoDB AI demo, the same engine should instead read as:

- an AI copilot for clinical trial operations
- backed by a governed matching runtime
- with MongoDB as the unified knowledge and retrieval layer

## What MongoRx Gets Right

MongoRx is still useful as a reference point even though its Realm-centered architecture is no longer the right direction.

Useful ideas to borrow:

- multi-dataset storytelling instead of single-dataset storytelling
- Atlas Search and Vector Search framed as user-facing features, not infrastructure details
- faceted exploration and "drill into the evidence" interactions
- "more like this" and related-content journeys
- dashboards that make the data feel alive and operational
- simple narrative flow: search, compare, filter, explain

What not to carry forward:

- Realm / App Services dependence
- GraphQL resolver-centric architecture
- a pure discovery story without a strong system of record
- a feature narrative that makes search feel more important than governed decision support

In short: borrow the product storytelling, not the old stack.

## PubMed Recommendation

PubMed should be included, but in a focused and governed way.

### Recommended

- use PubMed metadata and abstracts for a disease-focused literature layer
- use MeSH as the official terminology and classification backbone
- use PMC Open Access only for a limited full-text subset when full-text RAG is truly needed
- start with one disease area, ideally breast oncology because the current matching assets already fit it

### Not Recommended

- indexing all PubMed
- treating PubMed literature as the source of truth for eligibility
- bulk downloading PMC through unofficial scraping paths
- turning literature search into the main story of the demo

### Why

PubMed is best used here as supporting evidence:

- recent publications linked to a trial or biomarker
- landmark papers behind an eligibility criterion
- evidence used by the AI copilot when preparing a trial brief or match explanation
- literature trends around disease subtype, biomarker, intervention, or endpoint

The deterministic contract should remain the authority for match outcomes.

## 2026 Positioning

This demo should be positioned as an AI-native clinical operations experience for MongoDB in 2026.

That means:

- no Realm dependency in the target architecture
- provider-neutral AI adapters in the code
- Boyash AI as the preferred default AI provider for the demo
- MongoDB Atlas Search, Vector Search, aggregation, and operational collections as the foundation
- Next.js and FastAPI as the product shell

The current code already contains AI-oriented hooks, but they are still framed as optional helpers and some surfaces explicitly quarantine embeddings for audit only. The recommended shift is:

- keep deterministic matching authoritative
- promote AI evidence retrieval and agent workflows to first-class product features
- make provider names implementation details rather than user-facing product concepts

## Recommended Product Narrative

The best narrative is a five-part experience.

### 1. AI Trial Intelligence

Start from a trial.

Show:

- ingest a fresh ClinicalTrials.gov study
- compile eligibility into canonical criteria
- identify unsupported or ambiguous clauses
- generate an AI trial brief with key disease, biomarker, line-of-therapy, and exclusion signals
- link the trial to supporting literature and related studies

MongoDB story:

- raw source version
- canonical contract
- criterion nodes
- linked evidence
- agent trace

### 2. AI Patient Evidence Builder

Start from a screened patient.

Show:

- load only authoritative screened Synthea-linked patients
- assemble patient evidence profile from longitudinal records
- identify missing facts, contradictory facts, and inferred features
- produce an AI-readable match profile and a clinician-readable patient summary

MongoDB story:

- mixed structured and semi-structured medical data
- one patient evidence document with traceable provenance
- temporal data and derived feature snapshots living together

### 3. AI Match Copilot

This is the centerpiece.

Show:

- deterministic patient-to-trial screening
- deterministic trial-to-patient screening
- criterion-by-criterion explanations
- AI-generated "why this patient is a candidate" brief
- AI-generated "what still requires manual review" brief

Important product rule:

- AI can summarize, retrieve, rank for analyst productivity, and draft explanations
- deterministic logic decides official eligibility status
- unsupported or ambiguous criteria surface as manual review, never silent automation

### 4. Literature and Evidence Graph

This is where PubMed belongs.

Show:

- linked PubMed papers for a trial, biomarker, disease subtype, or intervention
- MeSH-driven disease and topic navigation
- semantic search over abstracts
- related-paper retrieval from a criterion or protocol description
- optional PMC Open Access full-text evidence packs for a small curated subset

This module should feel like "evidence surrounding the trial decision," not a separate search product.

### 5. Governance and Proof

Close with trust.

Show:

- dataset readiness
- benchmark status
- family coverage
- unresolved criteria backlog
- review history
- agent trace and evidence provenance

This is where the demo becomes distinctly MongoDB:

- one operational platform for live runtime state, evidence records, retrieval artifacts, and audit history

## Recommended Data Domains

The comprehensive demo should connect six main data domains.

### 1. Trial Contracts

- `trial_source_versions`
- `trial_contracts`
- `trial_contract_versions`
- `trial_criteria`
- `review_decisions`

### 2. Patient Evidence

- `patient_linkages`
- `patient_evidence_profiles`
- `patient_match_profiles`
- `patient_coverage_snapshots`

### 3. Match Runtime

- `match_runs`
- `match_result_rows`
- `runtime_explanations`

### 4. Literature Intelligence

- `literature_articles`
- `mesh_terms`
- `trial_literature_links`
- `criterion_literature_links`

### 5. Drug and Safety Signals

- `drug_label_signals`
- `drug_trial_links`
- `safety_event_summaries`

This should be optional for phase 1. It is a strong extension if we want to borrow more from MongoRx's OpenFDA story.

### 6. Agent and Governance Records

- `agent_runs`
- `agent_steps`
- `agent_observations`
- `benchmark_packs`
- `proof_reports`

## PubMed Data Scope

The right first slice is not all oncology literature. It is a curated disease module.

### Phase 1 Literature Scope

- breast cancer or breast neoplasms
- 5,000 to 50,000 PubMed records
- metadata, abstract, DOI, PMID, journal, publication type, publication date
- MeSH descriptors and qualifiers
- extracted disease subtype, drug, biomarker, endpoint, and trial-signal tags

### Optional Phase 2

- selected PMC Open Access full text
- only where we want paragraph-level RAG or evidence-grounded trial summaries

### Suggested logical collections

#### `literature_articles`

Each document should keep:

- citation metadata
- abstract
- MeSH tags
- extracted biomedical concepts
- embeddings
- source links
- optional trial signal features

#### `mesh_terms`

Each document should keep:

- descriptor id
- preferred name
- tree numbers
- synonyms
- broader and narrower relationships

#### `trial_literature_links`

Each document should keep:

- trial id
- literature id
- link reason
- matched concepts
- semantic score
- provenance for the link generation method

## Recommended AI Architecture

The AI story should be explicit and modular.

### AI service roles

- `AI Extraction`
  - derive disease subtype, biomarker, therapy line, endpoint, and trial-signal tags
- `AI Retrieval`
  - embed and semantically search trials, patients, criteria, and literature
- `AI Reranking`
  - rerank candidate evidence for the current question
- `AI Summarization`
  - generate trial briefs, patient summaries, and literature digests
- `AI Agent`
  - orchestrate tools to prepare a match workup or research brief

### Strong rule

AI must never replace the canonical matcher in the official decision path.

It can:

- prepare
- retrieve
- summarize
- cluster
- rank evidence for review
- explain

It should not directly decide:

- official eligible
- official not eligible
- official inclusion when the deterministic contract disagrees

### Provider strategy

Replace provider-specific product naming with a provider adapter layer:

- `EmbeddingProvider`
- `RerankProvider`
- `GenerationProvider`
- `AgentProvider`

Then set Boyash AI as the preferred configured provider for the demo.

That gives us two benefits:

- the narrative stays current in 2026
- the code stays portable if provider choices evolve

## Recommended MongoDB Story

The MongoDB value proposition should be visible in every workflow.

### Atlas Search

Use for:

- lexical search over trial text and literature abstracts
- faceted exploration over disease, biomarker, sponsor, phase, intervention, and publication type

### Atlas Vector Search

Use for:

- semantic matching between patient evidence and trial criteria
- protocol-to-paper retrieval
- criterion-to-literature retrieval
- similar-trial retrieval

### Aggregation

Use for:

- coverage views
- unresolved criteria analytics
- publication and biomarker trends
- cohort and match funnel summaries

### Flexible document model

Use for:

- storing raw trial text, canonical criteria, and review metadata together
- storing patient facts, derived evidence, and provenance together
- storing literature metadata, MeSH tags, extracted concepts, and embeddings together
- storing agent steps, tool traces, and outputs together

### Operational data platform

This is the most important 2026 message:

MongoDB is not just where vectors live. It is where the application's knowledge, state, evidence, and agent traces live together.

## Demo Script

This demo should be easy to present to customers in seven minutes.

### Step 1

Import one breast oncology trial and show how raw eligibility becomes a canonical contract.

### Step 2

Open one screened patient and show the evidence profile, missing facts, and readiness status.

### Step 3

Run the deterministic match and show criterion-by-criterion explanations.

### Step 4

Ask the AI copilot to generate a patient-trial fit summary and a manual-review checklist.

### Step 5

Open linked literature and show why the papers were attached to the trial or biomarker context.

### Step 6

Show similar trials and adjacent evidence using vector retrieval.

### Step 7

Close on governance: benchmark readiness, unresolved criteria, audit trail, and agent trace.

This sequence gives MongoDB a polished narrative arc:

- ingest
- understand
- match
- explain
- govern

## What To Build Next In This Repo

### Product and API

- add a product `literature` namespace under `/api/v1`
- add product `agent-runs` and `evidence-links` namespaces
- move AI endpoints away from provider-specific or legacy naming
- add provider-neutral AI service contracts in backend runtime code
- add a demo-lens model so the same shared trial corpus powers both broad discovery and deep breast-oncology workflows

### Data ingestion

- add PubMed metadata ingestion for a scoped oncology corpus
- add MeSH ingestion
- optionally add a limited OpenFDA safety module later

### Frontend

- replace extraction-first copy with AI copilot narrative
- add a Literature Intelligence studio
- add an AI Match Brief panel beside deterministic results
- add a compact Evidence Graph view linking patient, trial, biomarker, and papers

### Governance

- persist AI outputs and traces with provenance
- mark AI-generated statements clearly
- keep benchmark and proof surfaces prominent

## Specific Repo-Level Recommendations

### Keep

- the canonical trial matcher
- the patient evidence product routes
- the benchmark and proof system
- the authoritative screened-patient rule

### Refactor

- rename user-facing Voyage-specific surfaces to provider-neutral AI names
- move compatibility-only AI and search routes behind explicit adapters
- treat old "legacy embeddings quarantined" messaging as a migration artifact, not the target product stance

### Add

- `Literature Intelligence` as a first-class module
- `AI Match Brief` and `AI Trial Brief` artifacts
- `Agent Run` persistence for tool traces and summaries
- MeSH-backed terminology browsing

### Avoid

- all-PubMed ingestion
- Realm-based architecture
- AI-only eligibility decisions
- a narrative that makes vectors feel detached from the operational workflow

## Suggested Naming

If we want the demo to feel more complete and less like an extracted internal tool, consider positioning it as:

- `Clinical Trial Intelligence Copilot`
- `AI Trial Matching Workbench`
- `Oncology Trial Intelligence for MongoDB`

The best fit for the current repo is probably:

`Clinical Trial Intelligence Copilot`

It keeps matching central, but makes the AI layer explicit.

## Proposed Phases

### Phase 1: Narrative and Information Architecture

- update product copy and navigation
- define the two-lens model: `discovery` and `breast-oncology-ai`
- define literature collections and APIs
- make AI service naming provider-neutral
- add the Literature Intelligence studio shell

### Phase 2: Focused Literature Layer

- ingest scoped PubMed metadata and abstracts only for the breast-oncology lens
- ingest MeSH
- generate embeddings and concept tags
- link literature to trials and biomarkers

### Phase 3: AI Copilot Layer

- add AI Trial Brief
- add AI Match Brief
- persist agent runs and traces
- add evidence-grounded retrieval over trials, patients, and literature

### Phase 4: Demo Polish

- finalize the seven-minute talk track
- add Charts or embedded analytics where useful
- tighten visual design around the MongoDB story
- prepare seeded scenarios for deterministic and borderline cases

## Source Notes

MongoRx references reviewed locally:

- `/Users/francesc.mateu/Documents/GitHub/MongoRx/README.md`
- `/Users/francesc.mateu/Documents/GitHub/MongoRx/deploy/setup.sh`
- `/Users/francesc.mateu/Documents/GitHub/MongoRx/src/app/app.component.ts`
- `/Users/francesc.mateu/Documents/GitHub/MongoRx/src/app/dashboard/dashboard.component.ts`
- `/Users/francesc.mateu/Documents/GitHub/MongoRx/realm/functions/trialSearchCustomResolver.js`
- `/Users/francesc.mateu/Documents/GitHub/MongoRx/realm/functions/trialsFacetsCustomResolver.js`

Official reference points reviewed:

- PubMed download and API overview: `https://pubmed.ncbi.nlm.nih.gov/download/`
- PMC Open Access Subset: `https://pmc.ncbi.nlm.nih.gov/tools/openftlist`
- PMC copyright and automated retrieval restrictions: `https://pmc.ncbi.nlm.nih.gov/about/copyright/`
- MeSH download formats: `https://www.nlm.nih.gov/databases/download/mesh.html`
- NLM data download and attribution terms: `https://mainweb.awsprod.nlm.nih.gov/databases/download.html`
- E-utilities guidance and rate limits: `https://www.ncbi.nlm.nih.gov/books/NBK25497/`
