from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


APP_ROOT = Path(__file__).resolve().parent
SERVICE_ROOT = APP_ROOT.parent
BACKEND_ROOT = SERVICE_ROOT.parent

load_dotenv(BACKEND_ROOT / ".env")
load_dotenv(SERVICE_ROOT / ".env")


def env(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    value = os.getenv(name, default)
    if required and (value is None or str(value).strip() == ""):
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    raw_value = env(name)
    if raw_value is None:
        return default
    return str(raw_value).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    mongodb_connection_string: str
    mongodb_database: str
    trials_collection: str
    trial_versions_collection: str
    trial_criteria_collection: str
    rule_families_collection: str
    ingest_runs_collection: str
    match_runs_collection: str
    patient_curation_database: str
    patient_facts_collection: str
    patient_profiles_collection: str
    parser_version: str
    family_registry_version: str
    clinicaltrials_api_base: str
    clinicaltrials_timeout_sec: int
    clinicaltrials_default_condition: str
    search_page_size: int
    search_max_pages: int
    allowed_origins: list[str]
    terminology_base_url: str
    snomed_navigator_url: str
    terminology_timeout_sec: int
    enable_change_streams: bool
    strict_canonical_mode: bool


def get_settings() -> Settings:
    allowed_origins = [
        item.strip()
        for item in str(
            env(
                "CLINICAL_TRIALS_ALLOWED_ORIGINS",
                "http://localhost:3000,http://localhost:8080,http://127.0.0.1:8080",
            )
        ).split(",")
        if item.strip()
    ]
    return Settings(
        mongodb_connection_string=str(
            env(
                "CLINICAL_TRIALS_MONGODB_URI",
                env("MONGODB_CONNECTION_STRING", env("MONGODB_URI")),
                required=True,
            )
        ),
        mongodb_database=str(
            env(
                "CLINICAL_TRIALS_DATABASE_NAME",
                env("CLINICAL_TRIALS_DB", env("DATABASE_NAME", "demo_hc_virtual_hospital")),
            )
        ),
        trials_collection=str(
            env("CLINICAL_TRIALS_TRIALS_COLLECTION", env("PHARMA_TRIALS_COLLECTION", "pharma_clinical_trials"))
        ),
        trial_versions_collection=str(env("CLINICAL_TRIALS_VERSIONS_COLLECTION", "clinical_trial_versions")),
        trial_criteria_collection=str(env("CLINICAL_TRIALS_CRITERIA_COLLECTION", "clinical_trial_criteria")),
        rule_families_collection=str(env("CLINICAL_TRIALS_RULE_FAMILIES_COLLECTION", "clinical_trial_rule_families")),
        ingest_runs_collection=str(env("CLINICAL_TRIALS_INGEST_RUNS_COLLECTION", "clinical_trial_ingest_runs")),
        match_runs_collection=str(env("CLINICAL_TRIALS_MATCH_RUNS_COLLECTION", "clinical_trial_match_runs")),
        patient_curation_database=str(
            env(
                "CLINICAL_TRIALS_PATIENT_DB",
                env(
                    "CLINICAL_TRIALS_DATABASE_NAME",
                    env("CLINICAL_TRIALS_DB", env("DATABASE_NAME", "demo_hc_virtual_hospital")),
                ),
            )
        ),
        patient_facts_collection=str(
            env("CLINICAL_TRIALS_PATIENT_FACTS_COLLECTION", env("PHARMA_PATIENT_FACTS_COLLECTION", "pharma_patient_facts"))
        ),
        patient_profiles_collection=str(
            env(
                "CLINICAL_TRIALS_PATIENT_PROFILES_COLLECTION",
                env("PHARMA_PATIENT_PROFILES_COLLECTION", "pharma_patient_profiles"),
            )
        ),
        parser_version=str(env("CLINICAL_TRIALS_PARSER_VERSION", "ctgov_det_ingest_v1")),
        family_registry_version=str(env("CLINICAL_TRIALS_FAMILY_REGISTRY_VERSION", "2026-04-03")),
        clinicaltrials_api_base=str(env("CLINICALTRIALS_API_BASE", "https://clinicaltrials.gov/api/v2")),
        clinicaltrials_timeout_sec=int(env("CLINICALTRIALS_TIMEOUT_SEC", "30") or "30"),
        clinicaltrials_default_condition=str(env("CLINICALTRIALS_DEFAULT_CONDITION", "breast cancer")),
        search_page_size=max(1, min(int(env("CLINICAL_TRIALS_SEARCH_PAGE_SIZE", "20") or "20"), 100)),
        search_max_pages=max(1, min(int(env("CLINICAL_TRIALS_SEARCH_MAX_PAGES", "5") or "5"), 20)),
        allowed_origins=allowed_origins,
        terminology_base_url=str(env("CLINICAL_TRIALS_TERMINOLOGY_BASE_URL", "") or "").rstrip("/"),
        snomed_navigator_url=str(env("CLINICAL_TRIALS_SNOMED_NAVIGATOR_URL", "") or "").rstrip("/"),
        terminology_timeout_sec=int(env("CLINICAL_TRIALS_TERMINOLOGY_TIMEOUT_SEC", "20") or "20"),
        enable_change_streams=env_bool("CLINICAL_TRIALS_ENABLE_CHANGE_STREAMS", True),
        strict_canonical_mode=env_bool(
            "CLINICAL_TRIALS_STRICT_CANONICAL_MODE",
            env_bool("PHARMA_STRICT_CANONICAL_MODE", True),
        ),
    )
