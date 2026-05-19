from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient


LOGGER = logging.getLogger("clinical_trial_matching_agent")
logging.basicConfig(level=logging.INFO)

BACKEND_ROOT = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_ROOT.parent
SRC_ROOT = BACKEND_ROOT / "src"
CT_SERVICE_ROOT = BACKEND_ROOT / "clinical_trials_service_app"
MANIFEST_PATH = REPO_ROOT / "solution-library.manifest.json"
EXTRACT_ROOT = SRC_ROOT / "clinical_trial_matching_agent" / "leafy_extract"
FRONTEND_COMPAT_ROOT = REPO_ROOT / "frontend" / "leafy-react-compat" / "src"

load_dotenv(BACKEND_ROOT / ".env")
load_dotenv(CT_SERVICE_ROOT / ".env")

for path in [str(CT_SERVICE_ROOT), str(SRC_ROOT)]:
    if path not in sys.path:
        sys.path.insert(0, path)


def _alias_env(target: str, *source_names: str, default: str | None = None) -> None:
    current_value = str(os.getenv(target) or "").strip()
    if current_value:
        return
    for source_name in source_names:
        source_value = str(os.getenv(source_name) or "").strip()
        if source_value:
            os.environ[target] = source_value
            return
    if default is not None:
        os.environ[target] = default


_alias_env("CLINICAL_TRIALS_MONGODB_URI", "MONGODB_CONNECTION_STRING", "MONGODB_URI")
_alias_env(
    "CLINICAL_TRIALS_DATABASE_NAME",
    "CLINICAL_TRIALS_DB",
    "DATABASE_NAME",
    default="demo_hc_virtual_hospital",
)
_alias_env(
    "LEAFY_HOSPITAL_MONGODB_URI",
    "CLINICAL_TRIALS_MONGODB_URI",
    "MONGODB_CONNECTION_STRING",
    "MONGODB_URI",
)
_alias_env(
    "LEAFY_HOSPITAL_DATABASE_NAME",
    "DATABASE_NAME",
    "CLINICAL_TRIALS_DATABASE_NAME",
    default="demo_hc_virtual_hospital",
)
_alias_env("MONGODB_CONNECTION_STRING", "CLINICAL_TRIALS_MONGODB_URI", "MONGODB_URI")
_alias_env("MONGODB_URI", "CLINICAL_TRIALS_MONGODB_URI")
_alias_env(
    "CLINICAL_TRIALS_DB",
    "CLINICAL_TRIALS_DATABASE_NAME",
    "DATABASE_NAME",
    default="demo_hc_virtual_hospital",
)
_alias_env("DATABASE_NAME", "CLINICAL_TRIALS_DATABASE_NAME", default="demo_hc_virtual_hospital")
_alias_env("CLINICAL_TRIALS_ALLOWED_ORIGINS", "ORIGINS", default="http://localhost:3000")
_alias_env("CLINICAL_TRIALS_TRIALS_COLLECTION", default="clinical_trial_trials")
_alias_env(
    "CLINICAL_TRIALS_PATIENT_DB",
    "CLINICAL_TRIALS_DATABASE_NAME",
    "CLINICAL_TRIALS_DB",
    "DATABASE_NAME",
    default="demo_hc_virtual_hospital",
)
_alias_env(
    "CLINICAL_TRIALS_PATIENT_FACTS_COLLECTION",
    "PHARMA_PATIENT_FACTS_COLLECTION",
    default="pharma_patient_facts",
)
_alias_env(
    "CLINICAL_TRIALS_PATIENT_PROFILES_COLLECTION",
    "PHARMA_PATIENT_PROFILES_COLLECTION",
    default="pharma_patient_profiles",
)
_alias_env("CLINICAL_TRIALS_STRICT_CANONICAL_MODE", "PHARMA_STRICT_CANONICAL_MODE", default="true")

from app.database import collections as clinical_trials_service_collections  # noqa: E402
from app.database import ensure_indexes_and_seed_rule_families  # noqa: E402
from app.routes.clinical_trials import router as clinical_trials_router  # noqa: E402
from clinical_trial_matching_agent.leafy_extract.services.pharma_services import (  # noqa: E402
    create_pharma_services,
)
from clinical_trial_matching_agent.standalone.service_materializer import (  # noqa: E402
    materialize_service_collections_from_pharma,
)


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default)).strip()


def parse_bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def parse_int_value(value: Any, default: int = 0, minimum: int | None = None) -> int:
    try:
        parsed = int(float(value))
    except Exception:
        parsed = int(default)
    if minimum is not None:
        return max(int(minimum), parsed)
    return parsed


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def serialize_doc(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {key: serialize_doc(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize_doc(item) for item in value]
    return value


ENV_SPEC = [
    {"name": "APP_NAME", "default": "clinical-trial-matching-agent"},
    {"name": "ORIGINS", "default": "http://localhost:3000"},
    {"name": "CLINICAL_TRIALS_MONGODB_URI", "default": "", "requiredForStartup": True},
    {"name": "CLINICAL_TRIALS_DATABASE_NAME", "default": "demo_hc_virtual_hospital", "requiredForStartup": True},
    {
        "name": "LEAFY_HOSPITAL_MONGODB_URI",
        "default": "(falls back to CLINICAL_TRIALS_MONGODB_URI)",
    },
    {
        "name": "LEAFY_HOSPITAL_DATABASE_NAME",
        "default": "(falls back to CLINICAL_TRIALS_DATABASE_NAME)",
    },
    {"name": "ENRICHED_APPOINTMENTS_COLLECTION", "default": "enriched_appointments"},
    {"name": "CLINICAL_REPORTS_COLLECTION", "default": "clinicalreports"},
    {"name": "PHARMA_TRIALS_COLLECTION", "default": "pharma_clinical_trials"},
    {"name": "CLINICAL_TRIALS_TRIALS_COLLECTION", "default": "clinical_trial_trials"},
    {"name": "CLINICAL_TRIALS_VERSIONS_COLLECTION", "default": "clinical_trial_versions"},
    {"name": "CLINICAL_TRIALS_CRITERIA_COLLECTION", "default": "clinical_trial_criteria"},
    {"name": "PHARMA_PATIENT_PROFILES_COLLECTION", "default": "pharma_patient_profiles"},
    {"name": "PHARMA_PATIENT_FACTS_COLLECTION", "default": "pharma_patient_facts"},
    {"name": "PHARMA_EVALUATION_RUNS_COLLECTION", "default": "pharma_evaluation_runs"},
    {"name": "PHARMA_IMPROVEMENT_RUNS_COLLECTION", "default": "pharma_improvement_runs"},
    {"name": "PHARMA_PATIENT_CURATION_OVERLAYS_COLLECTION", "default": ""},
    {"name": "PHARMA_ONCOLOGY_COHORT_COLLECTION", "default": ""},
    {"name": "PHARMA_FEATURE_EDGES_COLLECTION", "default": ""},
    {"name": "LEAFY_SYNTHEA_LINKS_COLLECTION", "default": "leafy_synthea_links"},
    {"name": "SYNTHEA_FHIR_DB", "default": "hc_fhir_synthea_r4_sep2019_canonical"},
    {"name": "SYNTHEA_RESOURCES_COLLECTION", "default": "fhir_resources"},
    {"name": "VOYAGE_API_KEY", "default": ""},
    {"name": "CLINICALTRIALS_API_BASE", "default": "https://clinicaltrials.gov/api/v2"},
    {"name": "PHARMA_STRICT_CANONICAL_MODE", "default": "true"},
    {"name": "CLINICAL_TRIALS_SERVICE_SYNC_ON_STARTUP", "default": "false"},
    {"name": "CLINICAL_TRIALS_SERVICE_BOOTSTRAP_LIMIT", "default": "80"},
]

CLINICAL_TRIALS_MONGO_URI = _env("CLINICAL_TRIALS_MONGODB_URI", _env("MONGODB_URI"))
CLINICAL_TRIALS_DATABASE_NAME = _env(
    "CLINICAL_TRIALS_DATABASE_NAME",
    _env("DATABASE_NAME", "demo_hc_virtual_hospital"),
)
LEAFY_HOSPITAL_MONGO_URI = _env("LEAFY_HOSPITAL_MONGODB_URI", CLINICAL_TRIALS_MONGO_URI)
LEAFY_HOSPITAL_DATABASE_NAME = _env(
    "LEAFY_HOSPITAL_DATABASE_NAME",
    CLINICAL_TRIALS_DATABASE_NAME,
)
MONGO_URI = CLINICAL_TRIALS_MONGO_URI
DATABASE_NAME = CLINICAL_TRIALS_DATABASE_NAME
ENRICHED_APPOINTMENTS_COLLECTION = _env("ENRICHED_APPOINTMENTS_COLLECTION", "enriched_appointments")
CLINICAL_REPORTS_COLLECTION = _env("CLINICAL_REPORTS_COLLECTION", "clinicalreports")
PHARMA_TRIALS_COLLECTION = _env("PHARMA_TRIALS_COLLECTION", "pharma_clinical_trials")
PHARMA_PATIENT_PROFILES_COLLECTION = _env("PHARMA_PATIENT_PROFILES_COLLECTION", "pharma_patient_profiles")
PHARMA_PATIENT_FACTS_COLLECTION = _env("PHARMA_PATIENT_FACTS_COLLECTION", "pharma_patient_facts")
PHARMA_PATIENT_CURATION_OVERLAYS_COLLECTION = _env("PHARMA_PATIENT_CURATION_OVERLAYS_COLLECTION", "")
PHARMA_EVALUATION_RUNS_COLLECTION = _env("PHARMA_EVALUATION_RUNS_COLLECTION", "pharma_evaluation_runs")
PHARMA_IMPROVEMENT_RUNS_COLLECTION = _env("PHARMA_IMPROVEMENT_RUNS_COLLECTION", "pharma_improvement_runs")
PHARMA_ONCOLOGY_COHORT_COLLECTION = _env("PHARMA_ONCOLOGY_COHORT_COLLECTION", "")
PHARMA_FEATURE_EDGES_COLLECTION = _env("PHARMA_FEATURE_EDGES_COLLECTION", "")
LEAFY_SYNTHEA_LINKS_COLLECTION = _env("LEAFY_SYNTHEA_LINKS_COLLECTION", "leafy_synthea_links")
SYNTHEA_FHIR_DB = _env("SYNTHEA_FHIR_DB", "hc_fhir_synthea_r4_sep2019_canonical")
SYNTHEA_RESOURCES_COLLECTION = _env("SYNTHEA_RESOURCES_COLLECTION", "fhir_resources")
SYNTHEA_RESOURCES_NDJSON_PATH = _env("SYNTHEA_RESOURCES_NDJSON_PATH", "")
CLINICALTRIALS_API_BASE = _env("CLINICALTRIALS_API_BASE", "https://clinicaltrials.gov/api/v2")
CLINICALTRIALS_TIMEOUT_SEC = parse_int_value(_env("CLINICALTRIALS_TIMEOUT_SEC", "30"), 30, 1)
CLINICALTRIALS_DEFAULT_CONDITION = _env("CLINICALTRIALS_DEFAULT_CONDITION", "breast cancer")
CLINICALTRIALS_SYNC_DAYS_BACK = parse_int_value(_env("CLINICALTRIALS_SYNC_DAYS_BACK", "35"), 35, 1)
VOYAGE_API_KEY = _env("VOYAGE_API_KEY")
VOYAGE_EMBEDDING_MODEL = _env("VOYAGE_EMBEDDING_MODEL", "voyage-3.5")
VOYAGE_RERANK_MODEL = _env("VOYAGE_RERANK_MODEL", "rerank-2")
VOYAGE_PHARMA_VECTOR_INDEX_NAME = _env("VOYAGE_PHARMA_VECTOR_INDEX_NAME", "vectorSearch-pharma-trials")
VOYAGE_PHARMA_PATIENT_VECTOR_INDEX_NAME = _env(
    "VOYAGE_PHARMA_PATIENT_VECTOR_INDEX_NAME",
    "vectorSearch-pharma-patients",
)
BEDROCK_DEFAULT_MODEL = _env("AGENTIC_BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-20250514-v1:0")

ORIGINS = [
    item.strip()
    for item in _env("ORIGINS", "http://localhost:3000").split(",")
    if item.strip()
]
CLINICAL_TRIALS_SERVICE_SYNC_ON_STARTUP = parse_bool_value(
    _env("CLINICAL_TRIALS_SERVICE_SYNC_ON_STARTUP", "false"),
    False,
)
CLINICAL_TRIALS_SERVICE_BOOTSTRAP_LIMIT = parse_int_value(
    _env("CLINICAL_TRIALS_SERVICE_BOOTSTRAP_LIMIT", "80"),
    80,
    1,
)

clinical_trials_mongo_client = MongoClient(CLINICAL_TRIALS_MONGO_URI) if CLINICAL_TRIALS_MONGO_URI else None
leafy_hospital_mongo_client = (
    clinical_trials_mongo_client
    if clinical_trials_mongo_client is not None and LEAFY_HOSPITAL_MONGO_URI == CLINICAL_TRIALS_MONGO_URI
    else (MongoClient(LEAFY_HOSPITAL_MONGO_URI) if LEAFY_HOSPITAL_MONGO_URI else None)
)
clinical_trials_db = (
    clinical_trials_mongo_client[CLINICAL_TRIALS_DATABASE_NAME]
    if clinical_trials_mongo_client is not None
    else None
)
leafy_hospital_db = (
    leafy_hospital_mongo_client[LEAFY_HOSPITAL_DATABASE_NAME]
    if leafy_hospital_mongo_client is not None
    else None
)
mongo_client = clinical_trials_mongo_client
mongo_db = clinical_trials_db
synthea_db = leafy_hospital_mongo_client[SYNTHEA_FHIR_DB] if leafy_hospital_mongo_client is not None else None

appointments_collection = (
    leafy_hospital_db[ENRICHED_APPOINTMENTS_COLLECTION] if leafy_hospital_db is not None else None
)
clinical_reports_collection = (
    leafy_hospital_db[CLINICAL_REPORTS_COLLECTION] if leafy_hospital_db is not None else None
)
pharma_trials_collection = (
    clinical_trials_db[PHARMA_TRIALS_COLLECTION] if clinical_trials_db is not None else None
)
pharma_profiles_collection = (
    clinical_trials_db[PHARMA_PATIENT_PROFILES_COLLECTION] if clinical_trials_db is not None else None
)
pharma_patient_facts_collection = (
    clinical_trials_db[PHARMA_PATIENT_FACTS_COLLECTION] if clinical_trials_db is not None else None
)
pharma_patient_curation_overlays_collection = (
    clinical_trials_db[PHARMA_PATIENT_CURATION_OVERLAYS_COLLECTION]
    if clinical_trials_db is not None and PHARMA_PATIENT_CURATION_OVERLAYS_COLLECTION
    else None
)
pharma_evaluation_runs_collection = (
    clinical_trials_db[PHARMA_EVALUATION_RUNS_COLLECTION] if clinical_trials_db is not None else None
)
pharma_improvement_runs_collection = (
    clinical_trials_db[PHARMA_IMPROVEMENT_RUNS_COLLECTION] if clinical_trials_db is not None else None
)
pharma_oncology_cohort_collection = (
    clinical_trials_db[PHARMA_ONCOLOGY_COHORT_COLLECTION]
    if clinical_trials_db is not None and PHARMA_ONCOLOGY_COHORT_COLLECTION
    else None
)
pharma_feature_edges_collection = (
    clinical_trials_db[PHARMA_FEATURE_EDGES_COLLECTION]
    if clinical_trials_db is not None and PHARMA_FEATURE_EDGES_COLLECTION
    else None
)
leafy_synthea_links_collection = (
    leafy_hospital_db[LEAFY_SYNTHEA_LINKS_COLLECTION]
    if leafy_hospital_db is not None and LEAFY_SYNTHEA_LINKS_COLLECTION
    else None
)
synthea_resources_collection = synthea_db[SYNTHEA_RESOURCES_COLLECTION] if synthea_db is not None else None

PHARMA_RESOURCE_TYPES = {
    "Patient",
    "Condition",
    "Observation",
    "DiagnosticReport",
    "Procedure",
    "MedicationRequest",
    "DocumentReference",
    "Encounter",
}
PHARMA_TRAIL_KEYWORDS = [
    "breast",
    "cancer",
    "calcification",
    "mass",
    "spiculated",
    "asymmetry",
    "biopsy",
    "malignant",
    "neoplasm",
    "oncology",
    "metastatic",
    "hormone",
    "her2",
    "recurrence",
]
ACTIVE_TRIAL_STATUSES = [
    "RECRUITING",
    "ACTIVE_NOT_RECRUITING",
    "NOT_YET_RECRUITING",
    "ENROLLING_BY_INVITATION",
]
ACTIVE_TRIAL_STATUSES_ALL = list(
    {
        *ACTIVE_TRIAL_STATUSES,
        "Recruiting",
        "Active, not recruiting",
        "Not yet recruiting",
        "Enrolling by invitation",
    }
)

_voyage_client = None


def _get_voyage_client():
    global _voyage_client
    if _voyage_client is not None:
        return _voyage_client
    if not VOYAGE_API_KEY:
        return None
    try:
        import voyageai  # type: ignore
    except Exception as exc:
        LOGGER.warning("Voyage client unavailable: %s", exc)
        return None
    try:
        _voyage_client = voyageai.Client(api_key=VOYAGE_API_KEY)
    except Exception as exc:
        LOGGER.warning("Failed to initialize Voyage client: %s", exc)
        return None
    return _voyage_client


def voyage_embed_texts(
    texts: list[str],
    embedding_model: str | None = None,
    input_type: str = "document",
) -> list[list[float]]:
    client = _get_voyage_client()
    if client is None:
        return []
    cleaned_texts = [str(text or "").strip() for text in texts if str(text or "").strip()]
    if not cleaned_texts:
        return []
    try:
        response = client.embed(
            cleaned_texts,
            model=embedding_model or VOYAGE_EMBEDDING_MODEL,
            input_type=input_type,
        )
        return list(getattr(response, "embeddings", []) or [])
    except Exception as exc:
        LOGGER.warning("Voyage embed_texts failed: %s", exc)
        return []


def voyage_embed_text(
    text: str,
    embedding_model: str | None = None,
    input_type: str = "document",
) -> list[float] | None:
    embeddings = voyage_embed_texts([text], embedding_model=embedding_model, input_type=input_type)
    return embeddings[0] if embeddings else None


pharma_services = create_pharma_services(
    deps={
        "clinicaltrials_api_base": CLINICALTRIALS_API_BASE,
        "clinicaltrials_timeout_sec": CLINICALTRIALS_TIMEOUT_SEC,
        "pharma_resource_types": PHARMA_RESOURCE_TYPES,
        "pharma_trail_keywords": PHARMA_TRAIL_KEYWORDS,
        "synthea_patient_ref_cache": {},
        "collection": appointments_collection,
        "db_clinical_reports_collection": clinical_reports_collection,
        "db_leafy_synthea_links_collection": leafy_synthea_links_collection,
        "db_pharma_evaluation_runs_collection": pharma_evaluation_runs_collection,
        "db_pharma_feature_edges_collection": pharma_feature_edges_collection,
        "db_pharma_improvement_runs_collection": pharma_improvement_runs_collection,
        "db_pharma_patient_facts_collection": pharma_patient_facts_collection,
        "db_pharma_patient_curation_overlays_collection": pharma_patient_curation_overlays_collection,
        "db_pharma_oncology_cohort_collection": pharma_oncology_cohort_collection,
        "db_pharma_profiles_collection": pharma_profiles_collection,
        "db_pharma_trials_collection": pharma_trials_collection,
        "db_synthea_resources_collection": synthea_resources_collection,
        "synthea_fhir_db_name": SYNTHEA_FHIR_DB,
        "synthea_resources_collection_name": SYNTHEA_RESOURCES_COLLECTION,
        "parse_int_value_fn": parse_int_value,
        "safe_float_fn": safe_float,
        "serialize_doc_fn": serialize_doc,
        "voyage_api_key": VOYAGE_API_KEY,
        "voyage_embedding_model": VOYAGE_EMBEDDING_MODEL,
        "voyage_embed_text_fn": voyage_embed_text,
        "voyage_embed_texts_fn": voyage_embed_texts,
    }
)

ensure_pharma_indexes = pharma_services["ensure_pharma_indexes"]
normalize_pharma_text = pharma_services["normalize_pharma_text"]
merge_trial_version_history = pharma_services["merge_trial_version_history"]
resolve_trial_definition_canonical = pharma_services["resolve_trial_definition_canonical"]
resolve_trial_prefilter_contract = pharma_services["resolve_trial_prefilter_contract"]
build_trial_query_for_patient_context = pharma_services["build_trial_query_for_patient_context"]
evaluate_patient_context_against_trial_prefilter = pharma_services[
    "evaluate_patient_context_against_trial_prefilter"
]
evaluate_profile_against_trial_prefilter = pharma_services["evaluate_profile_against_trial_prefilter"]
build_leafy_patient_context = pharma_services["build_leafy_patient_context"]
build_patient_evidence_profile_v2 = pharma_services["build_patient_evidence_profile_v2"]
build_patient_match_profile_v2 = pharma_services["build_patient_match_profile_v2"]
build_oncology_cohort_assessment_input = pharma_services["build_oncology_cohort_assessment_input"]
build_breast_oncology_cohort_document = pharma_services["build_breast_oncology_cohort_document"]
infer_synthea_analog_patients = pharma_services["infer_synthea_analog_patients"]
resolve_leafy_synthea_link = pharma_services["resolve_leafy_synthea_link"]
normalize_authoritative_linkage_policy = pharma_services["normalize_authoritative_linkage_policy"]
list_pending_leafy_synthea_linkages = pharma_services["list_pending_leafy_synthea_linkages"]
promote_demo_screening_links = pharma_services["promote_demo_screening_links"]
rebuild_demo_screened_patient_cohort = pharma_services["rebuild_demo_screened_patient_cohort"]
build_trial_matching_control_plane_summary = pharma_services["build_trial_matching_control_plane_summary"]
list_trial_matching_evaluation_runs = pharma_services["list_trial_matching_evaluation_runs"]
list_trial_matching_improvement_runs = pharma_services["list_trial_matching_improvement_runs"]
run_trial_matching_control_plane_evaluation = pharma_services[
    "run_trial_matching_control_plane_evaluation"
]
build_patient_evidence_pack = pharma_services["build_patient_evidence_pack"]
evaluate_trial_structured_match = pharma_services["evaluate_trial_structured_match"]
build_trial_match_text = pharma_services["build_trial_match_text"]
build_profile_match_text = pharma_services["build_profile_match_text"]
estimate_trial_coverage_with_synthea = pharma_services["estimate_trial_coverage_with_synthea"]
build_profile_query_for_trial = pharma_services["build_profile_query_for_trial"]
fetch_clinicaltrials_studies = pharma_services["fetch_clinicaltrials_studies"]
normalize_ctgov_study = pharma_services["normalize_ctgov_study"]
build_trial_prefilter_contract = pharma_services["build_trial_prefilter_contract"]
audit_trial_matching_dataset_readiness = pharma_services["audit_trial_matching_dataset_readiness"]
audit_breast_oncology_cohort_quality = pharma_services["audit_breast_oncology_cohort_quality"]
build_patient_curation_coverage_summary_document = pharma_services[
    "build_patient_curation_coverage_summary_document"
]
rebuild_pharma_patient_profiles = pharma_services["rebuild_pharma_patient_profiles"]
rebuild_pharma_patient_facts = pharma_services["rebuild_pharma_patient_facts"]
rebuild_breast_oncology_cohort = pharma_services["rebuild_breast_oncology_cohort"]
rebuild_pharma_feature_edges = pharma_services["rebuild_pharma_feature_edges"]
load_synthea_resources_if_missing = pharma_services["load_synthea_resources_if_missing"]
batch_embed_trial_documents = pharma_services["batch_embed_trial_documents"]
batch_embed_profile_documents = pharma_services["batch_embed_profile_documents"]
resolve_linked_synthea_patient_ref = pharma_services["_resolve_linked_synthea_patient_ref"]
