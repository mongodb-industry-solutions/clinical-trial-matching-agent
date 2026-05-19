# Canonical Matching

This demo should keep the legacy Navigator UI while enforcing a cleaner backend contract for trial matching.

## Matching Model

The canonical path is:

1. Parse every trial eligibility statement into first-class criterion rows.
2. Preserve the section of each row as either `inclusion` or `exclusion`.
3. Compile deterministic criteria into executable family rules.
4. Evaluate every compiled criterion against patient evidence one by one.
5. Use retrieval only to build a shortlist, never to decide eligibility.

## Search Model

Shortlisting and final eligibility are separate concerns.

- `match_contract.hard_prefilter` is for safe deterministic gating such as age and sex.
- `match_contract.search_contract` is for shortlist retrieval and ranking support.
- `criteria[]` evaluation is the only source of truth for the final inclusion/exclusion decision.

Vectors remain optional shortlist support. They can improve recall and ordering, but they do not determine whether a patient satisfies a criterion.

## Data Model

Canonical trial materialization now stores:

- `match_contract.hard_prefilter`
- `match_contract.deterministic_criterion_ids`
- `match_contract.manual_criterion_ids`
- `match_contract.search_contract`

The search contract contains:

- `mongoProfileQuery`: recall-safe candidate filters for patient profiles
- `mongoVectorFilter`: the vector-stage-safe subset of those filters
- `signals.keywordTerms`: shortlist keywords derived from the trial and compiled criteria
- `signals.candidateSignalsAny`: patient candidate signals that should boost ranking
- `signals.matchFilters`: structured expectations such as primary cancer and biomarker posture
- `semantic.queryText`: the text used for optional semantic retrieval

## Standards Basis

- ClinicalTrials.gov treats eligibility as structured protocol data with age, sex, healthy-volunteer flags, and an inclusion/exclusion criteria field.
- HL7 FHIR `ResearchStudy.recruitment.eligibility` points to `EvidenceVariable` or `Group`, which is a good canonical representation for first-class eligibility characteristics.
- HL7 FHIR `EvidenceVariable.characteristic` includes an `exclude` flag, which cleanly maps to inclusion versus exclusion criteria.
- MongoDB Vector Search supports semantic retrieval plus metadata filters, which fits shortlist retrieval but not final eligibility decisions.

Primary references:

- https://clinicaltrials.gov/policy/protocol-definitions
- https://clinicaltrials.gov/data-api/about-api/study-data-structure
- https://hl7.org/fhir/researchstudy.html
- https://hl7.org/fhir/R5/evidencevariable.html
- https://www.mongodb.com/docs/vector-search/
