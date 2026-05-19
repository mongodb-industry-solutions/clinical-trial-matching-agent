# Extraction Map

This repo was bootstrapped from the following Leafy Hospital source areas:

| This repo | Source in Leafy Hospital |
|---|---|
| `backend/src/clinical_trial_matching_agent/leafy_extract/routes/pharma_blueprint.py` | `externals/webservices/routes/pharma_blueprint.py` |
| `backend/src/clinical_trial_matching_agent/leafy_extract/services/pharma_evidence_tools.py` | `externals/webservices/services/pharma_evidence_tools.py` |
| `backend/src/clinical_trial_matching_agent/leafy_extract/services/pharma_rule_tools.py` | `externals/webservices/services/pharma_rule_tools.py` |
| `backend/src/clinical_trial_matching_agent/leafy_extract/services/pharma_services.py` | `externals/webservices/services/pharma_services.py` |
| `backend/src/clinical_trial_matching_agent/leafy_extract/services/patient_curation.py` | `externals/webservices/services/patient_curation.py` |
| `backend/src/clinical_trial_matching_agent/leafy_extract/services/audit_trial_matching_dataset_readiness.py` | `externals/webservices/audit_trial_matching_dataset_readiness.py` |
| `backend/src/clinical_trial_matching_agent/leafy_extract/curate_demo_cohort.py` | `externals/webservices/curate_demo_cohort.py` |
| `backend/src/clinical_trial_matching_agent/leafy_extract/bootstrap_trial_matching_dataset.py` | `externals/webservices/bootstrap_trial_matching_dataset.py` |
| `backend/src/clinical_trial_matching_agent/leafy_extract/normalize_authoritative_linkage_policy.py` | `externals/webservices/normalize_authoritative_linkage_policy.py` |
| `backend/src/clinical_trial_matching_agent/leafy_extract/agent_tools/clinical_trial_agent.py` | `externals/webservices/agent_tools/clinical_trial_agent.py` |
| `backend/src/clinical_trial_matching_agent/leafy_extract/tests/*` | `externals/webservices/tests/test_patient_curation.py`, `test_trial_matching_foundation.py`, `test_clinical_trial_agent.py`, `test_curate_demo_cohort.py` |
| `backend/clinical_trials_service_app/.env.example` | `externals/clinical_trials_service/.env.example` |
| `backend/clinical_trials_service_app/README.md` | `externals/clinical_trials_service/README.md` |
| `backend/clinical_trials_service_app/requirements.txt` | `externals/clinical_trials_service/requirements.txt` |
| `backend/clinical_trials_service_app/tests/*` | `externals/clinical_trials_service/tests/*` |
| `benchmarks/clinical_trials_*.py` | `externals/benchmarks/clinical_trials_*.py` |
| `frontend/leafy-react-compat/src/pages/PharmaNavigator` | `app/src/pages/PharmaNavigator` |
| `frontend/leafy-react-compat/src/components/*` | `app/src/components/ClinicalTrialCanonicalWorkspace`, `ClinicalTrialsWorkbench`, `PharmaMatching`, `PharmaDesignSystem`, `PharmaStepIndicator`, `PatientContextPicker` |
| `frontend/leafy-react-compat/src/services` | `app/src/services/APIConfig.js`, `app/src/services/APIService.js` |
| `frontend/leafy-react-compat/src/context/AppContext.js` | `app/src/context/AppContext.js` |

The extracted React tree is a compatibility layer, not yet a standalone Next.js-native implementation.
