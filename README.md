# Clinical Trial Matching Agent

Clinical Trial Matching Agent is the solution library for automatic patient-to-trial and trial-to-patient matching. It is being extracted from Leafy Hospital's Pharma Navigator so it can evolve independently as a reusable MongoDB healthcare solution while still being embedded back into Leafy as one composed demo.

This repo is intended to become the source of truth for:

- deterministic clinical-trial matching logic
- trial eligibility and evidence tooling
- trial-ingestion and review workflows
- matching-focused React surfaces and design system assets

## Scope

Included here today:

- extracted Leafy backend services and route layer for trial matching
- extracted React compatibility layer for Pharma Navigator and matching workspaces
- metadata-oriented FastAPI shell
- solution manifest and boundary docs

Kept outside this repo on purpose:

- the general-purpose reasoning control plane
- Foundry-specific extension logic
- Leafy Hospital shell and non-trial workflows

## Repository Layout

```text
backend/
  main.py
  src/clinical_trial_matching_agent/
    leafy_extract/
      routes/
      services/

frontend/
  app/                     # lightweight standalone shell
  leafy-react-compat/      # extracted React matching surfaces

docs/
  BOUNDARIES.md
  COMPREHENSIVE_AI_DEMO.md
  EXTRACTION_MAP.md
  LEAFY_COMPOSITION.md
  RECOVERY_BLUEPRINT_2026-05-05.md
  RECOMMENDED_BLUEPRINT.md

solution-library.manifest.json
```

## Recommended Blueprint

The target product architecture and migration plan now live in:

- `docs/RECOMMENDED_BLUEPRINT.md`
- `docs/COMPREHENSIVE_AI_DEMO.md`
- `docs/RECOVERY_BLUEPRINT_2026-05-05.md`
- `docs/BOOK_BLUEPRINT_2026-05-06.md`

## Standalone Quick Start

1. Copy the local environment files:
   - `cp backend/.env.example backend/.env`
   - `cp frontend/.env.local.example frontend/.env.local`

2. Install backend dependencies:
   - `cd backend && uv sync`

   Alternative:
   - `cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`

3. Install frontend dependencies:
   - `cd frontend && npm install`

4. Run the standalone backend:
   - `cd backend && uv run uvicorn main:app --reload --port 8014`

5. Run the standalone frontend in another terminal:
   - `cd frontend && npm run dev`

6. Open `http://localhost:3014`

The standalone repo now exposes two UI surfaces:

- Product shell
  - `/discovery` for canonical trial discovery, remote search, and ingestion
  - `/patients` for patient evidence, cohort coverage, and demo enrichment
  - `/match` for deterministic patient-to-trial and trial-to-patient screening
  - `/governance` for benchmarks, family coverage, review queue, and unresolved criteria
- Recovered Pharma Navigator workbench
  - `/pharmaNavigator` for the continuous Navigator overview focused on ingestion, patient data, and bidirectional matching
  - `/pharmaNavigator?space=ingestion` for Trial Ingestion Studio
  - `/pharmaNavigator?space=patient` for Patient Curation Studio
  - `/pharmaNavigator?space=matching&direction=patient_to_trials` for Find Trials for One Patient
  - `/pharmaNavigator?space=matching&direction=trial_to_patients` for Find Patients for One Trial
  - `/pharmaNavigator?space=control` for Control Plane & Proof

If port `3014` is already in use, run the frontend on another port, for example:

- `cd frontend && npm run dev -- --port 3015`

Then open `http://localhost:3015`.

## Validation

Run the backend suites from one command:

- `make backend-test`

Build the frontend production bundle:

- `make frontend-build`

Run both before shipping changes:

- `make verify`

## API

The standalone backend exposes introspection endpoints for setup and extracted assets:

- `GET /`
- `GET /api/v1/agents/clinical-trial-matching/health`
- `POST /api/v1/agents/clinical-trial-matching/invoke`
- `GET /api/v1/manifest`
- `GET /api/v1/extract/summary`
- `GET /api/v1/setup/env-spec`
- `GET /api/v1/setup/status`
- `GET /api/v1/assets/compat-summary`

## Environment Model

The repo now owns its own local setup contract:

- trial and patient collections
- ClinicalTrials.gov ingestion defaults
- Synthea/FHIR linkage defaults
- deterministic matching mode flags
- optional Voyage reranking

This makes the package runnable outside Leafy while preserving the compatibility extracts that Leafy still embeds during the migration.
