from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any

from dateutil import parser as date_parser
from pymongo import DESCENDING, MongoClient

from app.database import collections, list_active_rule_families
from app.config import get_settings
from app.services.compiler import compile_criterion_node, materialize_match_contract, summarize_criteria
from app.services.helpers import stable_json_hash, utcnow
from app.services.ingest_service import (
    _build_projection_snapshots,
    _build_runtime_readiness,
    _build_stage_relevance,
    _infer_disease_area,
)
from app.services.normalizer import normalize_criterion_text


LOGGER = logging.getLogger(__name__)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if value in {None, ""}:
        return None
    try:
        parsed = date_parser.parse(str(value))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value not in {None, ""}:
            return value
    return None


def _resolve_pharma_source_collection():
    settings = get_settings()
    source_collection_name = "pharma_clinical_trials"
    try:
        import os

        source_collection_name = str(os.getenv("PHARMA_TRIALS_COLLECTION", source_collection_name) or source_collection_name).strip()
    except Exception:
        pass
    client = MongoClient(settings.mongodb_connection_string)
    db = client[settings.mongodb_database]
    return db[source_collection_name]


def _normalize_sponsor(value: Any) -> dict:
    if isinstance(value, dict):
        return {
            "lead": value.get("lead") or value.get("name") or value.get("leadSponsor") or value.get("display"),
            "class": value.get("class") or value.get("leadClass"),
        }
    return {"lead": str(value or "").strip() or None, "class": None}


def _normalize_sex_values(values: list[str]) -> str:
    normalized = {str(item or "").strip().lower() for item in values if str(item or "").strip()}
    if normalized == {"female"}:
        return "FEMALE"
    if normalized == {"male"}:
        return "MALE"
    if normalized:
        return "ALL"
    return "UNKNOWN"


def _build_structured_eligibility(trial_doc: dict) -> dict:
    existing = copy.deepcopy(trial_doc.get("structuredEligibility") or {})
    prefilter = copy.deepcopy(((trial_doc.get("trialPrefilterContract") or {}).get("hardFilters")) or {})
    age_filter = prefilter.get("age") or {}
    sex_filter = prefilter.get("sex") or {}
    return {
        "sex": existing.get("sex") or _normalize_sex_values(list(sex_filter.get("anyOf") or [])),
        "minimum_age": existing.get("minimumAge") or age_filter.get("min"),
        "maximum_age": existing.get("maximumAge") or age_filter.get("max"),
        "healthy_volunteers": existing.get("healthyVolunteers"),
    }


def _trial_version_id(trial_doc: dict, trial_id: str) -> str:
    version = trial_doc.get("trialVersion") or {}
    version_id = str(version.get("versionId") or "").strip()
    if version_id:
        return version_id
    source_hash = str(version.get("sourceHash") or "").strip()
    if source_hash:
        return f"{trial_id}::{source_hash[:16]}"
    return f"{trial_id}::{stable_json_hash({'trialId': trial_id, 'updatedAt': str(trial_doc.get('updatedAt') or '')})[:16]}"


def _criterion_text(canonical_criterion: dict) -> str:
    return str(
        canonical_criterion.get("criterionText")
        or ((canonical_criterion.get("matcherBinding") or {}).get("text"))
        or ""
    ).strip()


def _build_structured_nodes(trial_id: str, version_id: str, trial_doc: dict) -> list[dict]:
    structured_nodes: list[dict] = []
    prefilter = copy.deepcopy(((trial_doc.get("trialPrefilterContract") or {}).get("hardFilters")) or {})
    age_filter = prefilter.get("age") or {}
    sex_filter = prefilter.get("sex") or {}
    structured_eligibility = _build_structured_eligibility(trial_doc)

    ordinal = 0

    def add_node(node_id: str, token: str, text: str) -> None:
        nonlocal ordinal
        ordinal += 1
        structured_nodes.append(
            {
                "_id": node_id,
                "trial_id": trial_id,
                "version_id": version_id,
                "section": "inclusion",
                "node_type": "synthetic_structured",
                "source_kind": "structured_field",
                "ordinal": ordinal,
                "depth": 1,
                "path": ["STRUCT", token],
                "parent_node_id": None,
                "ancestor_headers": [],
                "raw": {
                    "text": text,
                    "source_path": f"structuredEligibility.{token.lower()}",
                    "lineage_version": 1,
                },
                "normalized": normalize_criterion_text(text),
                "compiler": {},
                "review": {},
                "audit": {},
            }
        )

    if age_filter.get("min") is not None:
        add_node(f"{trial_id}::{version_id}::AGE_MIN", "AGE_MIN", f"Minimum age {age_filter.get('min')} years")
    if age_filter.get("max") is not None:
        add_node(f"{trial_id}::{version_id}::AGE_MAX", "AGE_MAX", f"Maximum age {age_filter.get('max')} years")
    sex_values = [str(item or "").strip().lower() for item in (sex_filter.get("anyOf") or []) if str(item or "").strip()]
    if sex_values:
        if set(sex_values) == {"female", "male"}:
            sex_label = "All"
        elif len(sex_values) == 1:
            sex_label = sex_values[0].capitalize()
        else:
            sex_label = ", ".join(item.capitalize() for item in sex_values)
        add_node(f"{trial_id}::{version_id}::SEX", "SEX", f"Sex: {sex_label}")
    healthy_volunteers = structured_eligibility.get("healthy_volunteers")
    if healthy_volunteers is not None:
        add_node(
            f"{trial_id}::{version_id}::HEALTHY_VOLUNTEERS",
            "HEALTHY_VOLUNTEERS",
            f"Healthy volunteers: {str(bool(healthy_volunteers)).lower()}",
        )

    return structured_nodes


def _criterion_is_handled_by_structured_nodes(canonical_criterion: dict) -> bool:
    category = str(canonical_criterion.get("criterionCategory") or "").strip().lower()
    subcategory = str(canonical_criterion.get("criterionSubcategory") or "").strip().lower()
    return category == "demographics" and subcategory in {"age", "sex"}


def _build_canonical_leaf_node(
    trial_id: str,
    version_id: str,
    canonical_criterion: dict,
    ordinal: int,
) -> dict | None:
    criterion_text = _criterion_text(canonical_criterion)
    if not criterion_text:
        return None

    section = str(canonical_criterion.get("criterionType") or "inclusion").strip().lower()
    if section not in {"inclusion", "exclusion"}:
        section = "inclusion"

    rule_kind = str(canonical_criterion.get("ruleKind") or "").strip().lower()
    category = str(canonical_criterion.get("criterionCategory") or "").strip().lower()
    node_type = "note" if rule_kind in {"informational", "study_workflow", "site_process"} or category == "protocol_workflow" else "leaf"
    section_code = "EXC" if section == "exclusion" else "INC"
    criterion_id = str(canonical_criterion.get("criterionId") or "").strip() or f"{section_code}-{ordinal:03d}"
    return {
        "_id": f"{trial_id}::{version_id}::{criterion_id}",
        "trial_id": trial_id,
        "version_id": version_id,
        "section": section,
        "node_type": node_type,
        "source_kind": "markdown_leaf",
        "ordinal": ordinal,
        "depth": 1,
        "path": [section_code, f"{ordinal:03d}"],
        "parent_node_id": None,
        "ancestor_headers": [],
        "raw": {
            "text": criterion_text,
            "source_path": f"trialDefinitionCanonical.criteria.{criterion_id}",
            "lineage_version": 1,
        },
        "normalized": normalize_criterion_text(criterion_text),
        "compiler": {},
        "review": {},
        "audit": {
            "source_rule_kind": canonical_criterion.get("ruleKind"),
            "source_category": canonical_criterion.get("criterionCategory"),
            "source_subcategory": canonical_criterion.get("criterionSubcategory"),
        },
    }


def _manual_family_fallback(canonical_criterion: dict, *, section: str) -> str:
    category = str(canonical_criterion.get("criterionCategory") or "").strip().lower()
    subcategory = str(canonical_criterion.get("criterionSubcategory") or "").strip().lower()
    if category == "general_eligibility" and subcategory == "stage_or_extent":
        return "manual.stage_extent_context"
    if category == "safety_history":
        return "manual.safety_contraindication_history"
    if category == "prior_concomitant_therapy":
        return "manual.prior_exposure_contextual"
    if category == "disease_characteristics" and subcategory in {"risk", "conditions", "imaging"}:
        return "manual.complex_disease_setting"
    if category == "general_eligibility" and subcategory in {"oncology_treatment", "imaging"}:
        return "manual.prior_exposure_contextual" if subcategory == "oncology_treatment" else "manual.inclusion_context_other"
    return "manual.exclusion_context_other" if section == "exclusion" else "manual.inclusion_context_other"


def _apply_manual_family_fallback(compiled_node: dict, canonical_criterion: dict) -> dict:
    compiler = compiled_node.get("compiler") or {}
    if str(compiler.get("selected_family_id") or "").strip():
        return compiled_node
    if compiled_node.get("node_type") == "note":
        return compiled_node

    section = str(compiled_node.get("section") or "inclusion").strip().lower() or "inclusion"
    family_id = _manual_family_fallback(canonical_criterion, section=section)
    updated = copy.deepcopy(compiled_node)
    updated["compiler"] = {
        "parse_status": "needs_review",
        "computability": "manual_review_clinical",
        "selected_family_id": family_id,
        "selected_family_version": 1,
        "confidence": round(float(compiler.get("confidence") or 0.45), 2),
        "candidate_families": [
            {
                "family_id": family_id,
                "score": round(float(compiler.get("confidence") or 0.45), 2),
                "validator_passed": True,
                "leftover_tokens": [],
            }
        ],
        "compiled_rule": {
            "kind": section,
            "family": family_id,
            "logic": {
                "predicate": family_id,
                "operator": "manual_review_required",
                "value": True,
            },
            "required_patient_features": [],
            "prefilter_eligible": False,
        },
        "validator_report": {
            "section_compatible": True,
            "materialized_from_pharma_canonical": True,
        },
        "history": list((compiler.get("history") or [])),
    }
    updated["review"] = {
        "status": "pending",
        "decision": None,
        "final_family_id": None,
        "slot_overrides": {},
        "final_compiled_rule": None,
        "reviewer": None,
        "reviewed_at": None,
        "notes": "Materialized from pharma canonical criterion with manual family fallback.",
        "resolved_at": None,
    }
    return updated


def _materialize_trial_nodes(trial_doc: dict) -> tuple[str, list[dict]]:
    trial_id = str(_coalesce(trial_doc.get("nctId"), trial_doc.get("trialId"), trial_doc.get("_id")) or "").strip()
    if not trial_id:
        raise ValueError("Unable to determine trial identifier from pharma trial document")
    version_id = _trial_version_id(trial_doc, trial_id)
    active_families = list_active_rule_families()
    compiled_nodes: list[dict] = []

    for node in _build_structured_nodes(trial_id, version_id, trial_doc):
        compiled_nodes.append(compile_criterion_node(node, active_families))

    next_ordinal = len(compiled_nodes)
    for canonical_criterion in list(((trial_doc.get("trialDefinitionCanonical") or {}).get("criteria")) or []):
        if _criterion_is_handled_by_structured_nodes(canonical_criterion):
            continue
        next_ordinal += 1
        base_node = _build_canonical_leaf_node(trial_id, version_id, canonical_criterion, next_ordinal)
        if base_node is None:
            continue
        compiled = compile_criterion_node(base_node, active_families)
        compiled_nodes.append(_apply_manual_family_fallback(compiled, canonical_criterion))

    return version_id, compiled_nodes


def _build_version_document(trial_doc: dict, trial_id: str, version_id: str, source_hash: str, run_id: str) -> dict:
    canonical_criteria = list((((trial_doc.get("trialDefinitionCanonical") or {}).get("criteria")) or []))
    fetched_at = _parse_datetime(_coalesce(((trial_doc.get("trialVersion") or {}).get("fetchedAt")), trial_doc.get("fetchedAt"), trial_doc.get("updatedAt"))) or utcnow()
    return {
        "_id": version_id,
        "nct_id": trial_id,
        "source": str(((trial_doc.get("trialVersion") or {}).get("source")) or trial_doc.get("source") or "pharma_preloaded_corpus"),
        "source_hash": source_hash,
        "source_study_last_updated": ((trial_doc.get("trialVersion") or {}).get("sourceStudyLastUpdated")),
        "fetched_at": fetched_at,
        "ingested_by_run_id": run_id,
        "payload_meta": {
            "has_results": False,
            "has_large_docs": False,
            "large_doc_count": 0,
            "materializedFrom": "pharma_clinical_trials",
            "canonicalCriteriaCount": len(canonical_criteria),
        },
        "source_payload": {
            "materializedFrom": "pharma_clinical_trials",
            "trialId": trial_id,
            "title": trial_doc.get("title"),
            "briefTitle": trial_doc.get("briefTitle"),
            "phase": trial_doc.get("phase"),
            "status": trial_doc.get("status"),
            "conditions": list(trial_doc.get("conditions") or []),
            "keywords": list(trial_doc.get("keywords") or []),
            "sourceUrl": trial_doc.get("sourceUrl"),
            "trialVersion": copy.deepcopy(trial_doc.get("trialVersion") or {}),
            "trialDefinitionCanonical": copy.deepcopy(trial_doc.get("trialDefinitionCanonical") or {}),
            "trialPrefilterContract": copy.deepcopy(trial_doc.get("trialPrefilterContract") or {}),
            "siteCoverage": copy.deepcopy(trial_doc.get("siteCoverage") or []),
        },
    }


def _build_trial_document(trial_doc: dict, version_id: str, compiled_nodes: list[dict], run_id: str) -> dict:
    trial_id = str(_coalesce(trial_doc.get("nctId"), trial_doc.get("trialId"), trial_doc.get("_id")) or "").strip()
    title = str(_coalesce(trial_doc.get("title"), trial_doc.get("briefTitle"), trial_id) or "")
    brief_title = str(_coalesce(trial_doc.get("briefTitle"), trial_doc.get("title"), trial_id) or "")
    conditions = [str(item).strip() for item in (trial_doc.get("conditions") or []) if str(item).strip()]
    keywords = [str(item).strip() for item in (trial_doc.get("keywords") or []) if str(item).strip()]
    disease_area = _infer_disease_area(title, conditions)
    structured_eligibility = _build_structured_eligibility(trial_doc)
    criteria_summary = summarize_criteria(compiled_nodes)
    coverage_snapshot, review_snapshot, review_pending_count, compile_status = _build_projection_snapshots(compiled_nodes)
    match_contract = materialize_match_contract(
        {
            "title": title,
            "brief_title": brief_title,
            "conditions": conditions,
            "keywords": keywords,
            "disease_area": disease_area,
        },
        compiled_nodes,
    )
    runtime_readiness = _build_runtime_readiness(compiled_nodes)
    stage_relevance = _build_stage_relevance(
        {
            "title": title,
            "brief_title": brief_title,
            "conditions": conditions,
            "keywords": keywords,
            "disease_area": disease_area,
        },
        compiled_nodes,
        match_contract=match_contract,
    )
    site_coverage = list(trial_doc.get("siteCoverage") or [])
    updated_on_source = _coalesce(((trial_doc.get("trialVersion") or {}).get("sourceStudyLastUpdated")), trial_doc.get("updatedAt"), trial_doc.get("generatedAt"))
    last_ingested_at = _parse_datetime(_coalesce(trial_doc.get("updatedAt"), trial_doc.get("generatedAt"), ((trial_doc.get("trialVersion") or {}).get("generatedAt")), utcnow())) or utcnow()
    sponsor = _normalize_sponsor(trial_doc.get("sponsor"))
    summary_text = str(_coalesce(trial_doc.get("briefSummary"), trial_doc.get("summary"), "")) or ""
    trial_source_url = str(_coalesce(trial_doc.get("sourceUrl"), trial_doc.get("studyUrl"), trial_doc.get("url"), "") or "")

    return {
        "_id": trial_id,
        "nct_id": trial_id,
        "current_version_id": version_id,
        "title": title,
        "brief_title": brief_title,
        "acronym": trial_doc.get("acronym"),
        "phase": trial_doc.get("phase"),
        "study_type": trial_doc.get("studyType"),
        "status": trial_doc.get("status"),
        "sponsor": sponsor,
        "conditions": conditions[:20],
        "keywords": keywords[:20],
        "disease_area": disease_area,
        "structured_eligibility": structured_eligibility,
        "criteria_summary": criteria_summary,
        "compile": {
            "status": compile_status,
            "last_run_id": run_id,
            "parser_version": "ctgov_det_ingest_v1",
            "family_registry_version": "2026-04-03",
            "review_pending_count": review_pending_count,
        },
        "match_contract": match_contract,
        "stage_relevance": stage_relevance,
        "coverage_snapshot": coverage_snapshot,
        "review_snapshot": review_snapshot,
        "corpus_membership": [
            {
                "query_key": "pharma_preloaded_corpus",
                "condition": disease_area,
                "phase": trial_doc.get("phase"),
                "sponsor": sponsor.get("lead"),
                "first_seen_at": last_ingested_at,
                "last_seen_at": last_ingested_at,
            }
        ],
        "runtimeReadiness": runtime_readiness,
        "display": {
            "locations_count": len(site_coverage),
            "updated_on_source": updated_on_source,
            "last_ingested_at": last_ingested_at,
        },
        "summary": summary_text,
        "brief_summary": summary_text,
        "sourceUrl": trial_source_url,
        "siteCoverage": copy.deepcopy(site_coverage),
        "trialDefinitionCanonicalSummary": copy.deepcopy(((trial_doc.get("trialDefinitionCanonical") or {}).get("summary")) or {}),
        "materialization": {
            "sourceCollection": "pharma_clinical_trials",
            "materializedAt": utcnow(),
            "materializerVersion": 1,
        },
    }


def _criteria_documents(compiled_nodes: list[dict]) -> list[dict]:
    return [copy.deepcopy(node) for node in compiled_nodes]


def materialize_service_collections_from_pharma(
    pharma_trials_collection=None,
    *,
    trial_ids: list[str] | None = None,
    max_trials: int | None = None,
    force: bool = False,
) -> dict:
    if pharma_trials_collection is None:
        pharma_trials_collection = _resolve_pharma_source_collection()
    if pharma_trials_collection is None:
        return {
            "ok": False,
            "reason": "pharma_trials_collection_not_configured",
            "sourceCount": 0,
            "upsertedTrials": 0,
            "upsertedVersions": 0,
            "upsertedCriteria": 0,
            "skippedTrials": 0,
        }

    normalized_trial_ids = [str(item).strip() for item in (trial_ids or []) if str(item).strip()]
    query: dict[str, Any] = {}
    if normalized_trial_ids:
        query["$or"] = [
            {"nctId": {"$in": normalized_trial_ids}},
            {"trialId": {"$in": normalized_trial_ids}},
        ]
    source_count = int(
        pharma_trials_collection.count_documents(query)
        if query
        else pharma_trials_collection.estimated_document_count()
    )
    run_id = f"pharma_materialize_{utcnow().strftime('%Y%m%d_%H%M%S_%f')}"
    upserted_trials = 0
    upserted_versions = 0
    upserted_criteria = 0
    skipped_trials = 0

    cursor = pharma_trials_collection.find(
        query,
        {
            "_id": 1,
            "trialId": 1,
            "nctId": 1,
            "title": 1,
            "briefTitle": 1,
            "phase": 1,
            "status": 1,
            "studyType": 1,
            "sponsor": 1,
            "conditions": 1,
            "keywords": 1,
            "trialDefinitionCanonical": 1,
            "trialPrefilterContract": 1,
            "trialVersion": 1,
            "source": 1,
            "sourceUrl": 1,
            "studyUrl": 1,
            "url": 1,
            "siteCoverage": 1,
            "briefSummary": 1,
            "summary": 1,
            "structuredEligibility": 1,
            "generatedAt": 1,
            "updatedAt": 1,
            "fetchedAt": 1,
            "acronym": 1,
        },
    ).sort(
        [
            ("updatedAt", DESCENDING),
            ("generatedAt", DESCENDING),
            ("trialVersion.generatedAt", DESCENDING),
        ]
    ).batch_size(100)
    if max_trials is not None and int(max_trials) > 0:
        cursor = cursor.limit(int(max_trials))

    for source_row in cursor:
        trial_id = str(_coalesce(source_row.get("nctId"), source_row.get("trialId"), source_row.get("_id")) or "").strip()
        if not trial_id:
            continue

        version_id = _trial_version_id(source_row, trial_id)
        source_hash = str(((source_row.get("trialVersion") or {}).get("sourceHash")) or stable_json_hash({"trialId": trial_id, "versionId": version_id}))
        existing_trial = collections.trials.find_one({"_id": trial_id}, {"current_version_id": 1})
        existing_criteria_count = collections.trial_criteria.count_documents({"trial_id": trial_id, "version_id": version_id}, limit=1)
        if (
            (not force)
            and existing_trial
            and str(existing_trial.get("current_version_id") or "").strip() == version_id
            and existing_criteria_count > 0
        ):
            skipped_trials += 1
            continue

        materialized_version_id, compiled_nodes = _materialize_trial_nodes(source_row)
        version_document = _build_version_document(source_row, trial_id, materialized_version_id, source_hash, run_id)
        trial_document = _build_trial_document(source_row, materialized_version_id, compiled_nodes, run_id)
        criteria_documents = _criteria_documents(compiled_nodes)

        collections.trials.replace_one({"_id": trial_id}, trial_document, upsert=True)
        upserted_trials += 1

        collections.trial_versions.replace_one({"_id": version_document["_id"]}, version_document, upsert=True)
        upserted_versions += 1

        collections.trial_criteria.delete_many({"trial_id": trial_id, "version_id": materialized_version_id})
        if criteria_documents:
            for criterion_document in criteria_documents:
                collections.trial_criteria.replace_one(
                    {"_id": criterion_document["_id"]},
                    criterion_document,
                    upsert=True,
                )
            upserted_criteria += len(criteria_documents)

    status = {
        "ok": True,
        "runId": run_id,
        "sourceCount": source_count,
        "upsertedTrials": upserted_trials,
        "upsertedVersions": upserted_versions,
        "upsertedCriteria": upserted_criteria,
        "skippedTrials": skipped_trials,
        "serviceCounts": {
            "trials": int(collections.trials.estimated_document_count()),
            "versions": int(collections.trial_versions.estimated_document_count()),
            "criteria": int(collections.trial_criteria.estimated_document_count()),
        },
    }
    LOGGER.info("Clinical trials service materialization complete: %s", status)
    return status
