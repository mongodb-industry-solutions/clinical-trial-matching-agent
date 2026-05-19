from __future__ import annotations

from dataclasses import dataclass

from pymongo import ASCENDING, DESCENDING, MongoClient

from app.config import get_settings
from app.services.rule_families import build_seed_rule_families


settings = get_settings()
client = MongoClient(settings.mongodb_connection_string)
db = client[settings.mongodb_database]


@dataclass(frozen=True)
class CollectionSet:
    trials: any
    trial_versions: any
    trial_criteria: any
    rule_families: any
    ingest_runs: any
    match_runs: any


@dataclass(frozen=True)
class PatientCollectionSet:
    facts: any
    profiles: any


collections = CollectionSet(
    trials=db[settings.trials_collection],
    trial_versions=db[settings.trial_versions_collection],
    trial_criteria=db[settings.trial_criteria_collection],
    rule_families=db[settings.rule_families_collection],
    ingest_runs=db[settings.ingest_runs_collection],
    match_runs=db[settings.match_runs_collection],
)
patient_db = client[settings.patient_curation_database]
patient_collections = PatientCollectionSet(
    facts=patient_db[settings.patient_facts_collection],
    profiles=patient_db[settings.patient_profiles_collection],
)


def ensure_indexes_and_seed_rule_families() -> None:
    collections.trials.create_index(
        [("phase", ASCENDING), ("disease_area", ASCENDING), ("status", ASCENDING)]
    )
    collections.trials.create_index(
        [("compile.status", ASCENDING), ("compile.review_pending_count", ASCENDING)]
    )
    collections.trials.create_index([("current_version_id", ASCENDING)])
    collections.trials.create_index([("corpus_membership.query_key", ASCENDING)])
    collections.trials.create_index([("runtimeReadiness.runnable", ASCENDING), ("display.last_ingested_at", DESCENDING)])

    collections.trial_versions.create_index([("nct_id", ASCENDING), ("fetched_at", DESCENDING)])
    collections.trial_versions.create_index([("nct_id", ASCENDING), ("source_hash", ASCENDING)], unique=True)

    collections.trial_criteria.create_index(
        [("trial_id", ASCENDING), ("version_id", ASCENDING), ("section", ASCENDING), ("ordinal", ASCENDING)]
    )
    collections.trial_criteria.create_index(
        [("compiler.parse_status", ASCENDING), ("review.status", ASCENDING)]
    )
    collections.trial_criteria.create_index([("compiler.selected_family_id", ASCENDING)])
    collections.trial_criteria.create_index([("normalized.criterion_fingerprint", ASCENDING)])
    collections.trial_criteria.create_index([("review.final_family_id", ASCENDING)])
    collections.trial_criteria.create_index([("analytics.review_cluster_key", ASCENDING)])
    collections.trial_criteria.create_index([("analytics.patient_relevance_weight", DESCENDING)])

    collections.rule_families.create_index([("family_id", ASCENDING), ("version", DESCENDING)], unique=True)
    collections.rule_families.create_index([("status", ASCENDING)])
    collections.rule_families.create_index([("patientSupport.supported", ASCENDING)])
    collections.rule_families.create_index([("patientSupport.evaluator", ASCENDING)])

    collections.ingest_runs.create_index([("status", ASCENDING), ("started_at", DESCENDING)])
    collections.ingest_runs.create_index([("scope.nct_id", ASCENDING), ("started_at", DESCENDING)])
    collections.ingest_runs.create_index([("run_type", ASCENDING), ("started_at", DESCENDING)])
    collections.ingest_runs.create_index([("scope.family_id", ASCENDING), ("started_at", DESCENDING)])

    collections.match_runs.create_index([("direction", ASCENDING), ("startedAt", DESCENDING)])
    collections.match_runs.create_index([("patientRef", ASCENDING), ("startedAt", DESCENDING)])
    collections.match_runs.create_index([("trialId", ASCENDING), ("startedAt", DESCENDING)])
    collections.match_runs.create_index([("runStatus", ASCENDING), ("startedAt", DESCENDING)])
    collections.match_runs.create_index([("runtimeMode", ASCENDING), ("startedAt", DESCENDING)])

    patient_collections.facts.create_index([("patientRef", ASCENDING)], unique=True)
    patient_collections.facts.create_index([("patientEvidenceProfile.identity.linkageStatus", ASCENDING)])
    patient_collections.facts.create_index([("patientEvidenceProfile.resolvedFeatures.primaryCancer.status", ASCENDING)])
    patient_collections.profiles.create_index([("patientRef", ASCENDING)], unique=True)
    patient_collections.profiles.create_index([("patientMatchProfile.identity.linkageStatus", ASCENDING)])
    patient_collections.profiles.create_index([("patientMatchProfile.demographics.sex", ASCENDING)])
    patient_collections.profiles.create_index([("patientMatchProfile.demographics.ageYears", ASCENDING)])
    patient_collections.profiles.create_index([("patientMatchProfile.cohortMembership.authoritativeBreastOncology", ASCENDING)])
    patient_collections.profiles.create_index([("patientMatchProfile.runtimeReadiness.mode", ASCENDING)])
    patient_collections.profiles.create_index([("patientMatchProfile.runtimeReadiness.runnable", ASCENDING)])

    for family_document in build_seed_rule_families(settings.family_registry_version):
        collections.rule_families.update_one(
            {"_id": family_document["_id"]},
            {
                "$set": {
                    "family_id": family_document["family_id"],
                    "version": family_document["version"],
                    "status": family_document["status"],
                    "registry_version": family_document["registry_version"],
                    "description": family_document["description"],
                    "allowed_sections": family_document["allowed_sections"],
                    "triggers": family_document["triggers"],
                    "slot_schema": family_document["slot_schema"],
                    "extractor": family_document["extractor"],
                    "validator": family_document["validator"],
                    "compiler_contract": family_document["compiler_contract"],
                    "patientSupport": family_document["patientSupport"],
                    "examples": family_document["examples"],
                },
                "$setOnInsert": {
                    "_id": family_document["_id"],
                    "usage_stats": family_document["usage_stats"],
                },
            },
            upsert=True,
        )


def list_active_rule_families() -> list[dict]:
    return list(collections.rule_families.find({"status": "active"}).sort([("family_id", ASCENDING), ("version", DESCENDING)]))
