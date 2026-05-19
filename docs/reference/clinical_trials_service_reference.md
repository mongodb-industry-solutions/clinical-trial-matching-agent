# Clinical Trials Service

Dedicated FastAPI service for deterministic ClinicalTrials.gov ingestion into the `hc-clinical-trials` MongoDB database.

## Endpoints

- `GET /health`
- `POST /clinical-trials/search`
- `POST /clinical-trials/ingest`
- `GET /clinical-trials/runs`
- `GET /clinical-trials/runs/{run_id}`
- `GET /clinical-trials/runs/{run_id}/stream`
- `GET /clinical-trials/trials`
- `GET /clinical-trials/trials/{trial_id}`
- `GET /clinical-trials/trials/{trial_id}/criteria`
- `GET /clinical-trials/rule-families`
- `GET /api/v1/patients/screened/random`
- `GET /api/v1/patients/{patient_ref}/evidence`
- `GET /api/v1/patients/{patient_ref}/match-profile`
- `GET /api/v1/patients/{patient_ref}/linkage`
- `GET /api/v1/patient-coverage`
- `GET /clinical-trials/terminology/status`
- `POST /clinical-trials/terminology/snomed/resolve`
- `POST /clinical-trials/terminology/bindings/{binding_id}/validate`

## Collections

- `trials`
- `trial_versions`
- `trial_criteria`
- `rule_families`
- `ingest_runs`

## Current scope

- Phase 3 breast cancer ingest flow from ClinicalTrials.gov API v2
- Immutable source versioning
- Deterministic structured-rule compilation for age, sex, healthy volunteers
- Deterministic text-rule compilation for ECOG, sample willingness, and active CNS metastases
- Review-safe fallback for unresolved composite criteria
- Product patient evidence resolves only from authoritative screened Synthea-linked patients
- Terminology/SNOMED proxy endpoints prepared for later upstream wiring
