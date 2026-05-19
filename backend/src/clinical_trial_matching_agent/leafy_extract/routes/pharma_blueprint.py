from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
import math
import os
import re
import time
from threading import Lock, Thread

import requests
from flask import Blueprint, jsonify, request
from pymongo import UpdateOne

from clinical_trial_matching_agent.leafy_extract.services import pharma_canonical_compat as canonical_compat
from clinical_trial_matching_agent.leafy_extract.services.pharma_services import (
    CURRENT_AUTHORITATIVE_LINK_STRATEGIES,
)


STRICT_CANONICAL_MODE = str(os.getenv("PHARMA_STRICT_CANONICAL_MODE", "true")).strip().lower() in {
    "1", "true", "yes", "y", "on"
}
COMPAT_RUNTIME_MODE = "canonical_compat" if STRICT_CANONICAL_MODE else "hybrid_compat"
_CANONICAL_COMPAT_RUNTIME = None


def _safe_route_score(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def assessment_rank(value):
    normalized = str(value or "").strip().lower()
    if normalized == "likely_eligible":
        return 2
    if normalized == "needs_review":
        return 1
    if normalized == "likely_ineligible":
        return 0
    return -1


def compute_trial_match_ranking(
    *,
    structured_score,
    completeness_score,
    semantic_score=None,
    retrieval_score=0.0,
    hybrid_search_used=False,
    overall_assessment="needs_review",
):
    structured_val = max(0.0, min(1.0, _safe_route_score(structured_score, 0.0)))
    completeness_val = max(0.0, min(1.0, _safe_route_score(completeness_score, 0.0)))
    retrieval_val = max(0.0, min(1.0, _safe_route_score(retrieval_score, 0.0)))
    semantic_val = None if semantic_score is None else max(0.0, min(1.0, _safe_route_score(semantic_score, 0.0)))

    base_vector_weight = 0.20 if hybrid_search_used else (0.10 if semantic_val is not None else 0.0)
    base_retrieval_weight = 0.10 if hybrid_search_used else 0.05
    vector_confidence_factor = 0.50 + (0.50 * completeness_val)
    retrieval_confidence_factor = completeness_val

    vector_weight = round(base_vector_weight * vector_confidence_factor, 4) if semantic_val is not None else 0.0
    retrieval_weight = round(base_retrieval_weight * retrieval_confidence_factor, 4)
    structured_weight = round(max(0.0, 1.0 - vector_weight - retrieval_weight), 4)

    blended_score = (
        structured_weight * structured_val
        + (vector_weight * semantic_val if semantic_val is not None else 0.0)
        + retrieval_weight * retrieval_val
    )
    completeness_penalty = round(0.35 + (0.65 * completeness_val), 4)
    review_cap = 1.0
    normalized_assessment = str(overall_assessment or "").strip().lower()
    if normalized_assessment == "needs_review":
        review_cap = 0.65
    elif normalized_assessment == "likely_ineligible":
        review_cap = 0.05

    final_score = round(min(blended_score * completeness_penalty, review_cap), 4)
    return {
        "finalScore": final_score,
        "weights": {
            "structured": structured_weight,
            "vectorSearch": vector_weight if semantic_val is not None else 0.0,
            "retrievalSignals": retrieval_weight,
        },
        "vectorConfidenceFactor": round(vector_confidence_factor if semantic_val is not None else 0.0, 4),
        "retrievalConfidenceFactor": round(retrieval_confidence_factor, 4),
        "completenessPenaltyFactor": completeness_penalty,
        "reviewCap": review_cap,
    }


def _load_canonical_compat_runtime():
    global _CANONICAL_COMPAT_RUNTIME
    if _CANONICAL_COMPAT_RUNTIME is not None:
        return _CANONICAL_COMPAT_RUNTIME
    try:
        from app.database import collections as service_collections
        from app.services.matcher import matcher_service
        from clinical_trial_matching_agent.standalone.service_materializer import (
            materialize_service_collections_from_pharma,
        )

        _CANONICAL_COMPAT_RUNTIME = {
            "ok": True,
            "collections": service_collections,
            "matcher_service": matcher_service,
            "materialize_trials": materialize_service_collections_from_pharma,
        }
    except Exception as exc:
        _CANONICAL_COMPAT_RUNTIME = {
            "ok": False,
            "error": str(exc),
        }
    return _CANONICAL_COMPAT_RUNTIME


def _canonical_overall_to_legacy_assessment(value):
    normalized = str(value or "").strip().lower()
    if normalized == "candidate":
        return "likely_eligible"
    if normalized == "excluded":
        return "likely_ineligible"
    return "needs_review"


def _canonical_status_to_legacy_status(*, section, status):
    normalized_status = str(status or "").strip().lower()
    normalized_section = str(section or "inclusion").strip().lower()
    if normalized_status == "met":
        return "PASS"
    if normalized_status == "not_met":
        return "FAIL"
    if normalized_status == "not_patient_rule":
        return "UNKNOWN" if normalized_section in {"inclusion", "exclusion"} else "UNKNOWN"
    return "UNKNOWN"


def _canonical_status_to_evaluation_mode(status):
    normalized = str(status or "").strip().lower()
    if normalized == "fillable_by_questionnaire":
        return "questionnaire_fillable"
    if normalized == "manual_review_required":
        return "manual_review_required"
    if normalized == "blocked_by_linkage_conflict":
        return "linkage_conflict"
    if normalized == "not_patient_rule":
        return "not_patient_rule"
    if normalized == "unknown":
        return "deterministic_missing_data"
    return "deterministic"


def _criterion_state_from_canonical(*, section, status):
    normalized_status = str(status or "").strip().lower()
    normalized_section = str(section or "inclusion").strip().lower()
    if normalized_status not in {"met", "not_met"}:
        return "unknown"
    if normalized_section == "exclusion":
        return "not_triggered" if normalized_status == "met" else "triggered"
    return "satisfied" if normalized_status == "met" else "not_satisfied"


def _humanize_family_id(value):
    text = str(value or "").strip()
    if not text:
        return "general"
    return text.replace(".", " ")


def _short_criterion_label(text, fallback):
    raw = re.sub(r"\s+", " ", str(text or "").strip())
    if not raw:
        return str(fallback or "criterion").strip() or "criterion"
    return raw if len(raw) <= 56 else f"{raw[:55].rstrip()}..."


def _criterion_lookup_for_trial(*, service_collections, trial_id, version_id):
    lookup = {}
    if not trial_id or not version_id:
        return lookup
    cursor = service_collections.trial_criteria.find(
        {"trial_id": trial_id, "version_id": version_id},
        {
            "_id": 1,
            "section": 1,
            "raw.text": 1,
            "compiler.selected_family_id": 1,
            "compiler.compiled_rule": 1,
            "review.final_family_id": 1,
            "review.final_compiled_rule": 1,
        },
    )
    for node in cursor:
        criterion_id = str(node.get("_id") or "").strip()
        if not criterion_id:
            continue
        review_payload = node.get("review") or {}
        compiler_payload = node.get("compiler") or {}
        compiled_rule = review_payload.get("final_compiled_rule") or compiler_payload.get("compiled_rule") or {}
        family_id = (
            review_payload.get("final_family_id")
            or compiler_payload.get("selected_family_id")
            or str(compiled_rule.get("family") or "").strip()
        )
        lookup[criterion_id] = {
            "section": str(node.get("section") or "inclusion").strip().lower() or "inclusion",
            "text": str(((node.get("raw") or {}).get("text")) or "").strip(),
            "familyId": str(family_id or "").strip(),
            "operator": ((compiled_rule.get("logic") or {}).get("operator")),
            "requiredFeatures": list(compiled_rule.get("required_patient_features") or []),
        }
    return lookup


def _build_legacy_structured_eval_from_canonical(*, criteria_rows, criteria_lookup, overall):
    actionable_rows = []
    for row in list(criteria_rows or []):
        if not isinstance(row, dict):
            continue
        status_value = str(row.get("status") or "").strip().lower()
        if status_value == "not_patient_rule":
            continue
        criterion_id = str(row.get("criterionId") or "").strip()
        lookup = criteria_lookup.get(criterion_id, {})
        section = str(row.get("section") or lookup.get("section") or "inclusion").strip().lower() or "inclusion"
        canonical_status = str(row.get("status") or "").strip().lower()
        legacy_status = _canonical_status_to_legacy_status(section=section, status=canonical_status)
        criterion_state = _criterion_state_from_canonical(section=section, status=canonical_status)
        eligibility_impact = (
            "supports_eligibility"
            if legacy_status == "PASS"
            else "blocks_eligibility"
            if legacy_status == "FAIL"
            else "unknown"
        )
        evaluation_mode = _canonical_status_to_evaluation_mode(canonical_status)
        evidence_paths = [str(item).strip() for item in (row.get("evidencePaths") or []) if str(item).strip()]
        evidence_refs = [str(item).strip() for item in (row.get("evidenceRefs") or []) if str(item).strip()]
        evidence_pointers = evidence_paths[:6] or evidence_refs[:6]
        source_of_truth = str(row.get("sourceOfTruth") or "").strip()
        missing_data = []
        gap = row.get("gap") if isinstance(row.get("gap"), dict) else {}
        gap_feature = str(gap.get("feature") or "").strip()
        if gap_feature:
            missing_data.append(gap_feature)
        if canonical_status == "fillable_by_questionnaire":
            missing_data.append("questionnaire_confirmation")
        elif canonical_status == "blocked_by_linkage_conflict":
            missing_data.append("identity_linkage")
        manual_review_reason = str(row.get("reason") or "").strip()
        actionable_rows.append(
            {
                "ruleId": criterion_id or str(row.get("familyId") or "").strip(),
                "canonicalCriterionId": criterion_id or None,
                "type": section,
                "text": str(lookup.get("text") or criterion_id or row.get("familyId") or "criterion").strip(),
                "ruleKind": "patient_eligibility",
                "primaryCriterionGroup": str(lookup.get("familyId") or row.get("familyId") or section or "general").strip(),
                "conceptTags": [str(lookup.get("familyId") or row.get("familyId") or "general").strip()],
                "dataNeeded": sorted(list(set(missing_data + list(lookup.get("requiredFeatures") or []))))[:8],
                "status": legacy_status,
                "criterionState": criterion_state,
                "eligibilityImpact": eligibility_impact,
                "evaluationMode": evaluation_mode,
                "requiresManualReview": bool(legacy_status == "UNKNOWN"),
                "manualReviewReason": manual_review_reason,
                "evidencePointers": evidence_pointers,
                "evidenceSources": [source_of_truth] if source_of_truth else [],
                "patientValue": row.get("patientValue"),
                "trialValue": row.get("requiredValue"),
                "missingData": sorted(list(set(missing_data)))[:8],
                "operator": lookup.get("operator"),
            }
        )

    total_rules = len(actionable_rows)
    denominator = max(total_rules, 1)
    pass_count = sum(1 for row in actionable_rows if row.get("status") == "PASS")
    fail_count = sum(1 for row in actionable_rows if row.get("status") == "FAIL")
    unknown_count = total_rules - pass_count - fail_count
    evaluated_count = pass_count + fail_count
    inclusion_fail_count = sum(
        1 for row in actionable_rows if row.get("status") == "FAIL" and str(row.get("type") or "").strip().lower() == "inclusion"
    )
    exclusion_hit_count = sum(
        1 for row in actionable_rows if row.get("status") == "FAIL" and str(row.get("type") or "").strip().lower() == "exclusion"
    )

    if total_rules == 0:
        eligibility_score = 0.0
        completeness_score = 0.0
        structured_score = 0.0
    else:
        if fail_count > 0:
            eligibility_score = 0.02
        else:
            eligibility_score = min(1.0, max(0.0, pass_count / float(denominator)))
        completeness_score = min(1.0, max(0.0, evaluated_count / float(denominator)))
        structured_score = round(float(eligibility_score * completeness_score), 4)
        if fail_count > 0:
            structured_score = min(structured_score, 0.05)

    overall_assessment = _canonical_overall_to_legacy_assessment(overall)
    if total_rules == 0:
        decision_reason = "No canonical patient-eligibility criteria were available to evaluate."
    elif fail_count > 0:
        decision_reason = (
            f"{inclusion_fail_count} inclusion criterion(s) did not pass and "
            f"{exclusion_hit_count} exclusion criterion(s) were triggered."
        )
    elif unknown_count > 0:
        decision_reason = f"{unknown_count} of {total_rules} criteria still need more evidence or review."
    else:
        decision_reason = f"All {total_rules} canonical criteria passed with available evidence."

    criterion_breakdown = []
    for row in actionable_rows[:24]:
        criterion_breakdown.append(
            {
                "criterion": _short_criterion_label(row.get("text"), row.get("ruleId")),
                "status": "pass" if row.get("status") == "PASS" else "fail" if row.get("status") == "FAIL" else "unknown",
                "detail": str(row.get("manualReviewReason") or decision_reason),
                "passCount": 1 if row.get("status") == "PASS" else 0,
                "failCount": 1 if row.get("status") == "FAIL" else 0,
                "unknownCount": 1 if row.get("status") == "UNKNOWN" else 0,
                "patientValue": row.get("patientValue"),
                "trialValue": row.get("trialValue"),
            }
        )

    missing_criteria = sorted(
        {
            missing_item
            for row in actionable_rows
            if row.get("status") == "UNKNOWN"
            for missing_item in (row.get("missingData") or [])
            if str(missing_item).strip()
        }
    )[:10]
    criteria_level_matches = [
        {
            "ruleId": row.get("ruleId"),
            "type": row.get("type"),
            "text": row.get("text"),
            "score": 1.0 if row.get("status") == "PASS" else 0.0,
            "conceptTags": row.get("conceptTags") or [],
        }
        for row in actionable_rows
        if row.get("status") == "PASS"
    ][:10]
    coverage_meter = {
        "hasRules": bool(total_rules > 0),
        "totalRules": total_rules,
        "evaluatedRules": evaluated_count,
        "deterministicRules": total_rules,
        "deterministicEvaluated": evaluated_count,
        "llmWithCitations": 0,
        "unknownRules": unknown_count,
        "deterministicCoveragePct": round((evaluated_count / max(total_rules, 1)) * 100.0, 1),
        "llmCoveragePct": 0.0,
        "unknownPct": round((unknown_count / max(total_rules, 1)) * 100.0, 1),
        "overallCoveragePct": round((evaluated_count / max(total_rules, 1)) * 100.0, 1),
    }
    decision_summary = {
        "assessment": overall_assessment,
        "reason": decision_reason,
        "contractVersion": "canonical_service_compat_v1",
        "totalRules": total_rules,
        "evaluatedRules": evaluated_count,
        "passRules": pass_count,
        "failRules": fail_count,
        "unknownRules": unknown_count,
        "inclusionFails": inclusion_fail_count,
        "exclusionHits": exclusion_hit_count,
        "eligibilityScore": round(float(eligibility_score), 4),
        "evidenceCompletenessScore": round(float(completeness_score), 4),
        "hardFail": bool(fail_count > 0),
        "manualReviewRequired": bool(overall_assessment == "needs_review"),
        "manualReviewRuleCount": unknown_count,
        "retrievalAffectsEligibility": False,
    }
    return {
        "isEligibleStructured": bool(overall_assessment == "likely_eligible"),
        "overallAssessment": overall_assessment,
        "structuredScore": structured_score,
        "eligibilityScore": round(float(eligibility_score), 4),
        "evidenceCompletenessScore": round(float(completeness_score), 4),
        "matchReasons": [f"{row.get('type')} rule matched: {str(row.get('text') or '').strip()[:140]}" for row in actionable_rows if row.get("status") == "PASS"][:8],
        "missingCriteria": missing_criteria,
        "decisionSummary": decision_summary,
        "parserWarnings": [],
        "validatorWarnings": [],
        "ruleStatusCounts": {
            "pass": pass_count,
            "fail": fail_count,
            "unknown": unknown_count,
            "total": total_rules,
        },
        "criterionBreakdown": criterion_breakdown,
        "criteriaLevelMatches": criteria_level_matches,
        "ruleEvaluations": actionable_rows,
        "coverageMeter": coverage_meter,
    }


def _canonical_trial_match_map(*, trial_ids, patient_ref, limit):
    runtime = _load_canonical_compat_runtime()
    if not runtime.get("ok"):
        return None, runtime.get("error")
    clean_trial_ids = [str(item).strip() for item in (trial_ids or []) if str(item).strip()]
    if not clean_trial_ids or not str(patient_ref or "").strip():
        return None, "canonical_trial_match_inputs_missing"
    runtime["materialize_trials"](trial_ids=clean_trial_ids, max_trials=max(1, len(clean_trial_ids)))
    response = runtime["matcher_service"].match_patient_to_trials(
        str(patient_ref).strip(),
        {
            "trial_ids": clean_trial_ids,
            "limit": max(1, len(clean_trial_ids), int(limit or len(clean_trial_ids))),
        },
    )
    run = response.get("run") if isinstance(response, dict) else {}
    results = list((run or {}).get("results") or [])
    return {
        "runId": (run or {}).get("_id"),
        "trialVersionById": {
            str(item.get("trialId") or "").strip(): item for item in results if str(item.get("trialId") or "").strip()
        },
        "collections": runtime["collections"],
    }, None


def _canonical_candidate_match_map(*, trial_id, patient_refs, limit):
    runtime = _load_canonical_compat_runtime()
    if not runtime.get("ok"):
        return None, runtime.get("error")
    clean_trial_id = str(trial_id or "").strip()
    clean_patient_refs = [str(item).strip() for item in (patient_refs or []) if str(item).strip()]
    if not clean_trial_id or not clean_patient_refs:
        return None, "canonical_candidate_match_inputs_missing"
    runtime["materialize_trials"](trial_ids=[clean_trial_id], max_trials=1)
    response = runtime["matcher_service"].match_trial_to_patients(
        clean_trial_id,
        {
            "patient_refs": clean_patient_refs,
            "limit": max(1, len(clean_patient_refs), int(limit or len(clean_patient_refs))),
        },
    )
    run = response.get("run") if isinstance(response, dict) else {}
    trial_payload = (run or {}).get("trial") or {}
    criteria_lookup = _criterion_lookup_for_trial(
        service_collections=runtime["collections"],
        trial_id=clean_trial_id,
        version_id=trial_payload.get("currentVersionId"),
    )
    results = list((run or {}).get("results") or [])
    return {
        "runId": (run or {}).get("_id"),
        "criteriaLookup": criteria_lookup,
        "patientVersionByRef": {
            str(item.get("patientRef") or "").strip(): item for item in results if str(item.get("patientRef") or "").strip()
        },
    }, None


def run_patient_trial_match_pipeline(
    *,
    question=None,
    patient_context=None,
    report_doc=None,
    appointment_doc=None,
    appointment_id=None,
    patient_id=None,
    top_k=6,
    rerank_enabled=True,
    trial_filters=None,
    build_leafy_patient_context_fn,
    infer_synthea_analog_patients_fn,
    resolve_linked_synthea_patient_ref_fn,
    build_patient_evidence_pack_fn,
    build_trial_query_for_patient_context_fn,
    evaluate_patient_context_against_trial_prefilter_fn,
    resolve_trial_prefilter_contract_fn,
    resolve_trial_definition_canonical_fn,
    evaluate_trial_structured_match_fn,
    build_trial_match_text_fn,
    estimate_trial_coverage_with_synthea_fn,
    db_pharma_trials_collection,
    active_trial_statuses_all,
    voyage_api_key,
    voyage_embed_text_fn,
    voyage_embedding_model,
    voyage_rerank_results_fn,
    voyage_rerank_model,
    voyage_pharma_vector_index_name,
    serialize_doc_fn,
    safe_float_fn,
    normalize_pharma_text_fn,
    logger,
):
    def merge_mongo_clauses(*query_parts):
        clauses = []
        for part in query_parts:
            if not isinstance(part, dict) or not part:
                continue
            clauses.append(part)
        if not clauses:
            return {}
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    def build_trial_list_query_from_filters(filter_source):
        filters = filter_source if isinstance(filter_source, dict) else {}
        status_filter = str(filters.get("status") or "all").strip()
        phase_filter = str(filters.get("phase") or "all").strip()
        source_filter = str(filters.get("source") or "all").strip()
        sponsor_filter = str(filters.get("sponsor") or "").strip()
        country_filter = str(filters.get("country") or "").strip()
        text_query = str(filters.get("q") or "").strip()

        query = {}
        if status_filter and status_filter.lower() != "all":
            query["status"] = status_filter
        if phase_filter and phase_filter.lower() != "all":
            query["phase"] = phase_filter
        if source_filter and source_filter.lower() not in {"all", "ctgov"}:
            query["source"] = source_filter
        if sponsor_filter:
            query["sponsor"] = {"$regex": re.escape(sponsor_filter), "$options": "i"}
        if country_filter:
            query["siteCoverage.country"] = {"$regex": re.escape(country_filter), "$options": "i"}
        if text_query:
            query["$or"] = [
                {"title": {"$regex": re.escape(text_query), "$options": "i"}},
                {"summary": {"$regex": re.escape(text_query), "$options": "i"}},
                {"conditions": {"$elemMatch": {"$regex": re.escape(text_query), "$options": "i"}}},
                {"tags": {"$elemMatch": {"$regex": re.escape(text_query), "$options": "i"}}},
            ]
        return query

    def get_vector_index_readiness(collection_obj, index_name, expected_path=None, expected_dims=1024):
        payload = {
            "indexName": index_name,
            "exists": False,
            "pathMatches": None,
            "dimensionsMatches": None,
            "configuredPath": None,
            "configuredDimensions": None,
            "error": None,
        }
        try:
            try:
                indexes = list(collection_obj.list_search_indexes(name=index_name))
            except TypeError:
                indexes = list(collection_obj.list_search_indexes(index_name))
            except Exception:
                indexes = list(collection_obj.list_search_indexes())
            target = None
            for item in indexes:
                if not isinstance(item, dict):
                    continue
                name_value = item.get("name") or item.get("indexName")
                if name_value == index_name:
                    target = item
                    break
            if not target and indexes:
                target = indexes[0]
            if not target:
                return payload
            payload["exists"] = True

            definition = target.get("latestDefinition") or target.get("definition") or {}
            fields = definition.get("fields") if isinstance(definition, dict) else None
            vector_field = None

            def walk_fields(node):
                nonlocal vector_field
                if vector_field is not None:
                    return
                if isinstance(node, list):
                    for child in node:
                        walk_fields(child)
                    return
                if not isinstance(node, dict):
                    return
                node_type = str(node.get("type") or "").lower()
                if node_type in {"vector", "knnvector", "vectorsearch"}:
                    vector_field = node
                    return
                for child in node.values():
                    walk_fields(child)

            walk_fields(fields)
            if isinstance(vector_field, dict):
                payload["configuredPath"] = vector_field.get("path")
                configured_dims = vector_field.get("numDimensions") or vector_field.get("dimensions")
                payload["configuredDimensions"] = configured_dims
                if expected_path:
                    payload["pathMatches"] = str(payload["configuredPath"] or "") == str(expected_path)
                if configured_dims is not None:
                    payload["dimensionsMatches"] = int(configured_dims) == int(expected_dims)
            return payload
        except Exception as index_error:
            payload["error"] = str(index_error)
            return payload

    def assess_patient_context_status(context_payload, patient_link, evidence_pack=None):
        context_data = context_payload if isinstance(context_payload, dict) else {}
        link = patient_link if isinstance(patient_link, dict) else {}
        linkage_status = str(link.get("linkageStatus") or "").strip().lower()
        missing_fields = list((evidence_pack or {}).get("missing_fields") or [])[:12] if isinstance(evidence_pack, dict) else []
        blockers = []
        if context_data.get("age") is None:
            blockers.append("age")
        if not str(context_data.get("sex") or "").strip():
            blockers.append("sex")
        if not (context_data.get("biradsValues") or []):
            blockers.append("biradsValues")

        if len(blockers) >= 3:
            return {
                "status": "insufficient_leafy_data",
                "message": "Leafy evidence is insufficient for reliable deterministic adjudication.",
                "nextAction": "Complete core screening fields (age/sex/BI-RADS).",
                "blockers": blockers,
                "missingFields": missing_fields,
                "canRunDeterministicMatch": False,
                "screeningOnly": False,
                "longitudinalEvidenceReady": False,
            }

        if linkage_status == "conflict":
            return {
                "status": "screening_only_ready",
                "message": "Screening context is ready for deterministic matching, but the linked longitudinal identity is conflicting.",
                "nextAction": "Run with screening evidence now and resolve the linkage conflict before relying on longitudinal evidence.",
                "blockers": blockers + ["identity_linkage_conflict"],
                "missingFields": missing_fields,
                "canRunDeterministicMatch": True,
                "screeningOnly": True,
                "longitudinalEvidenceReady": False,
            }

        if linkage_status == "ambiguous":
            return {
                "status": "screening_only_ready",
                "message": "Screening context is ready for deterministic matching, but the longitudinal patient link is still ambiguous.",
                "nextAction": "Run with screening evidence now and resolve linkage from candidateOptions to enrich the evidence pack.",
                "blockers": blockers,
                "missingFields": missing_fields,
                "canRunDeterministicMatch": True,
                "screeningOnly": True,
                "longitudinalEvidenceReady": False,
            }

        if linkage_status == "legacy_heuristic_link":
            return {
                "status": "screening_only_ready",
                "message": "Screening context is ready for deterministic matching, but the existing longitudinal link is heuristic only.",
                "nextAction": "Run with screening evidence now and replace the heuristic link with an authoritative linked patient record.",
                "blockers": blockers,
                "missingFields": missing_fields,
                "canRunDeterministicMatch": True,
                "screeningOnly": True,
                "longitudinalEvidenceReady": False,
            }
        if linkage_status == "candidate_only":
            return {
                "status": "screening_only_ready",
                "message": "Screening context is ready for deterministic matching. Longitudinal linkage is still pending, so unresolved criteria will remain explicit for review.",
                "nextAction": "Resolve linkage from candidateOptions to enrich this patient with longitudinal evidence.",
                "blockers": blockers,
                "missingFields": missing_fields,
                "canRunDeterministicMatch": True,
                "screeningOnly": True,
                "longitudinalEvidenceReady": False,
            }
        if linkage_status not in {"linked"}:
            return {
                "status": "screening_only_ready",
                "message": "Screening context is ready for deterministic matching, but no linked longitudinal patient record is available yet.",
                "nextAction": "Resolve an authoritative screening-to-FHIR patient link to enrich the evidence pack.",
                "blockers": blockers,
                "missingFields": missing_fields,
                "canRunDeterministicMatch": True,
                "screeningOnly": True,
                "longitudinalEvidenceReady": False,
            }

        return {
            "status": "ready",
            "message": "Patient context is linked and ready for trial matching.",
            "nextAction": None,
            "blockers": blockers,
            "missingFields": missing_fields[:12],
            "canRunDeterministicMatch": True,
            "screeningOnly": False,
            "longitudinalEvidenceReady": True,
        }

    def cosine_similarity(left_vector, right_vector):
        if not isinstance(left_vector, list) or not isinstance(right_vector, list):
            return 0.0
        if not left_vector or not right_vector:
            return 0.0
        if len(left_vector) != len(right_vector):
            return 0.0
        dot = 0.0
        norm_left = 0.0
        norm_right = 0.0
        for left_value, right_value in zip(left_vector, right_vector):
            try:
                left_num = float(left_value)
                right_num = float(right_value)
            except Exception:
                return 0.0
            dot += left_num * right_num
            norm_left += left_num * left_num
            norm_right += right_num * right_num
        if norm_left <= 0.0 or norm_right <= 0.0:
            return 0.0
        return float(dot / (math.sqrt(norm_left) * math.sqrt(norm_right)))

    try:
        top_k_value = max(1, min(int(top_k or 6), 20))
    except Exception:
        top_k_value = 6
    question_text = str(question or "").strip()
    filter_payload = trial_filters if isinstance(trial_filters, dict) else {}
    provided_context = dict(patient_context or {}) if isinstance(patient_context, dict) else {}
    context = dict(provided_context)
    report_payload = report_doc if isinstance(report_doc, dict) else {}
    appointment_payload = appointment_doc if isinstance(appointment_doc, dict) else {}
    appointment_id_str = str(
        appointment_id
        or context.get("appointmentId")
        or report_payload.get("appointmentID")
        or ""
    ).strip()
    patient_id_str = str(
        patient_id
        or context.get("patientId")
        or report_payload.get("patientID")
        or report_payload.get("patient-id")
        or ""
    ).strip() or None

    if appointment_id_str:
        context, report_payload, appointment_payload = build_leafy_patient_context_fn(
            appointment_id=appointment_id_str,
            patient_id=patient_id_str,
        )
    else:
        context = dict(provided_context)
        report_payload = serialize_doc_fn(report_payload)
        appointment_payload = serialize_doc_fn(appointment_payload)

    matching_query = question_text or context.get("semanticQuery") or "breast cancer trial matching"
    if not context:
        patient_context_status = {
            "status": "missing_context",
            "message": "No patient context is available for trial matching.",
            "nextAction": "Load a patient report or appointment before requesting trial matches.",
            "blockers": ["appointmentId|patientId|reportId"],
            "canRunDeterministicMatch": False,
            "screeningOnly": False,
        }
        return {
            "success": True,
            "query": matching_query,
            "patientContext": {},
            "patientEvidencePack": {},
            "patientContextStatus": patient_context_status,
            "patientLink": {},
            "matches": [],
            "syntheaAnalogs": [],
            "pipelineVisibility": {
                "matcher": "shared_pharma_navigator_trial_match",
                "patientContextStatus": patient_context_status,
                "syntheaAnalogPipeline": [],
                "patientLink": {},
            },
            "agenticResearchOps": [],
        }

    analog_candidates, analog_pipeline = infer_synthea_analog_patients_fn(context, limit=5)
    patient_link = resolve_linked_synthea_patient_ref_fn(
        patient_context=context,
        report_doc=report_payload,
        appointment_doc=appointment_payload,
        allow_analog=True,
    )
    evidence_pack = build_patient_evidence_pack_fn(
        patient_context=context,
        report_doc=report_payload,
        appointment_doc=appointment_payload,
        synthea_patient_ref=patient_link.get("syntheaPatientRef"),
        linkage_payload=patient_link,
    )
    if not evidence_pack and isinstance(provided_context.get("evidencePack"), dict):
        evidence_pack = provided_context.get("evidencePack") or {}

    patient_context_status = assess_patient_context_status(context, patient_link, evidence_pack)
    context["evidencePack"] = evidence_pack

    if not patient_context_status.get("canRunDeterministicMatch"):
        return {
            "success": True,
            "query": matching_query,
            "patientContext": context,
            "patientEvidencePack": evidence_pack,
            "patientContextStatus": patient_context_status,
            "patientLink": patient_link,
            "matches": [],
            "syntheaAnalogs": analog_candidates,
            "pipelineVisibility": {
                "matcher": "shared_pharma_navigator_trial_match",
                "patientContextStatus": patient_context_status,
                "syntheaAnalogPipeline": analog_pipeline,
                "patientLink": patient_link,
            },
            "agenticResearchOps": [],
        }

    timings = {}
    hybrid_search_used = False
    num_candidates = 0
    candidate_pool = max(top_k_value * 3, 20)
    vector_search_count = 0
    vector_fallback_reason = None
    query_embedding = None
    embedding_error = None
    trial_prefilter_query = build_trial_query_for_patient_context_fn(context)
    trial_status_query = {"status": {"$in": active_trial_statuses_all}}
    trial_catalog_query = build_trial_list_query_from_filters(filter_payload)
    trial_candidate_query = merge_mongo_clauses(trial_status_query, trial_prefilter_query, trial_catalog_query)
    catalog_filter_applied = bool(trial_catalog_query)
    prefilter_filter_used_in_search = False
    prefilter_post_filter_applied = False
    candidate_search_mode = "filtered_catalog_structured_scan" if catalog_filter_applied else "structured_trial_prefilter"
    prefiltered_trial_count = db_pharma_trials_collection.count_documents(trial_candidate_query)
    vector_readiness = get_vector_index_readiness(
        db_pharma_trials_collection,
        voyage_pharma_vector_index_name,
        expected_path="vectorEmbedding",
        expected_dims=1024,
    )

    if voyage_api_key and voyage_embed_text_fn:
        try:
            t0 = time.time()
            query_embedding = voyage_embed_text_fn(
                matching_query,
                embedding_model=voyage_embedding_model,
            )
            timings["embeddingMs"] = round((time.time() - t0) * 1000, 1)
        except Exception as embed_err:
            embedding_error = str(embed_err)

    if (not catalog_filter_applied) and query_embedding is not None and vector_readiness.get("exists"):
        try:
            num_candidates = max(top_k_value * 8, 60)
            vector_filter = trial_candidate_query or trial_status_query
            vector_search_stage = {
                "$vectorSearch": {
                    "index": voyage_pharma_vector_index_name,
                    "path": "vectorEmbedding",
                    "queryVector": query_embedding,
                    "numCandidates": num_candidates,
                    "limit": candidate_pool,
                    "filter": vector_filter,
                }
            }
            prefilter_filter_used_in_search = True
            pipeline = [
                vector_search_stage,
                {"$addFields": {"vectorSearchScore": {"$meta": "vectorSearchScore"}}},
                {"$project": {"vectorEmbedding": 0, "criteriaChunkEmbeddings": 0, "rawSourcePayload": 0}},
            ]
            t1 = time.time()
            trials = list(db_pharma_trials_collection.aggregate(pipeline))
            timings["vectorSearchMs"] = round((time.time() - t1) * 1000, 1)
            vector_search_count = len(trials)
            hybrid_search_used = True
            candidate_search_mode = "vector_plus_trial_prefilter"
        except Exception as vs_err:
            vector_error_text = str(vs_err)
            needs_filter_mapping = "needs to be indexed as filter" in vector_error_text.lower()
            if needs_filter_mapping:
                try:
                    vector_search_stage = {
                        "$vectorSearch": {
                            "index": voyage_pharma_vector_index_name,
                            "path": "vectorEmbedding",
                            "queryVector": query_embedding,
                            "numCandidates": num_candidates,
                            "limit": candidate_pool,
                        }
                    }
                    pipeline = [vector_search_stage]
                    pipeline.append({"$match": trial_status_query})
                    pipeline.append({"$match": trial_prefilter_query})
                    pipeline.extend(
                        [
                            {"$addFields": {"vectorSearchScore": {"$meta": "vectorSearchScore"}}},
                            {"$project": {"vectorEmbedding": 0, "criteriaChunkEmbeddings": 0, "rawSourcePayload": 0}},
                        ]
                    )
                    t1 = time.time()
                    trials = list(db_pharma_trials_collection.aggregate(pipeline))
                    timings["vectorSearchMs"] = round((time.time() - t1) * 1000, 1)
                    vector_search_count = len(trials)
                    hybrid_search_used = True
                    prefilter_post_filter_applied = True
                    prefilter_filter_used_in_search = False
                    candidate_search_mode = "vector_plus_post_prefilter"
                    vector_fallback_reason = "Atlas vector filter fields are not mapped yet; used vector search first and applied status plus deterministic trial prefilters afterward."
                except Exception as retry_error:
                    logger.warning("$vectorSearch failed, falling back to find(): %s", retry_error)
                    vector_fallback_reason = f"{vector_error_text} | Retry with post-vector prefilter failed: {str(retry_error)}"
                    hybrid_search_used = False
            else:
                logger.warning("$vectorSearch failed, falling back to find(): %s", vs_err)
                vector_fallback_reason = vector_error_text
                hybrid_search_used = False
    elif catalog_filter_applied:
        vector_fallback_reason = "Filtered catalog run evaluates the selected trial set directly instead of using vector preselection."
    elif query_embedding is None:
        if embedding_error:
            vector_fallback_reason = f"Query embedding failed: {embedding_error}"
        else:
            vector_fallback_reason = "Voyage embedding is not configured."
    elif not vector_readiness.get("exists"):
        vector_fallback_reason = f"Atlas Search index '{voyage_pharma_vector_index_name}' is missing."

    if not hybrid_search_used:
        trials = list(
            db_pharma_trials_collection.find(
                trial_candidate_query,
                {"vectorEmbedding": 0, "criteriaChunkEmbeddings": 0, "rawSourcePayload": 0},
            )
        )

    t2 = time.time()
    trial_rows = []
    criteria_vector_used = False
    for trial in trials:
        trial_prefilter_contract = resolve_trial_prefilter_contract_fn(trial)
        trial_definition_canonical = resolve_trial_definition_canonical_fn(trial)
        prefilter_evaluation = evaluate_patient_context_against_trial_prefilter_fn(
            patient_context=context,
            trial_prefilter_contract=trial_prefilter_contract,
        )
        if not prefilter_evaluation.get("passesHardFilters"):
            continue
        structured = evaluate_trial_structured_match_fn(
            trial,
            context,
            semantic_query=matching_query,
        )
        row_evidence_pack = structured.pop("evidencePack", None)
        if row_evidence_pack and not evidence_pack:
            evidence_pack = row_evidence_pack
            context["evidencePack"] = evidence_pack
        row = serialize_doc_fn(trial)
        row.update(structured)
        row["trialPrefilterContract"] = trial_prefilter_contract
        row["trialDefinitionCanonical"] = trial_definition_canonical
        row["prefilterEvaluation"] = prefilter_evaluation
        vector_search_score = trial.get("vectorSearchScore")
        row["vectorSearchScore"] = safe_float_fn(vector_search_score, 0.0) if vector_search_score is not None else None
        row["semanticScore"] = row["vectorSearchScore"]
        retrieval_matches = list(structured.get("semanticRetrievalMatches") or structured.get("criteriaLevelMatches") or [])
        criteria_vector_matches = []
        criteria_vector_score = 0.0
        if query_embedding is not None:
            chunk_vectors = trial.get("criteriaChunkEmbeddings") or []
            chunk_texts = trial.get("criteriaChunks") or []
            for chunk_index, chunk_vector in enumerate(chunk_vectors[:12]):
                score = cosine_similarity(query_embedding, chunk_vector)
                if score <= 0:
                    continue
                chunk_text = chunk_texts[chunk_index] if chunk_index < len(chunk_texts) else f"criteria chunk #{chunk_index + 1}"
                criteria_vector_matches.append(
                    {
                        "ruleId": f"chunk-{chunk_index + 1}",
                        "type": "criteria_chunk",
                        "text": chunk_text,
                        "score": round(float(score), 4),
                        "source": "criteriaChunkEmbeddings",
                    }
                )
            criteria_vector_matches = sorted(
                criteria_vector_matches,
                key=lambda item: safe_float_fn(item.get("score"), 0.0),
                reverse=True,
            )[:6]
            if criteria_vector_matches:
                criteria_vector_used = True
                criteria_vector_score = max(
                    [safe_float_fn(item.get("score"), 0.0) for item in criteria_vector_matches],
                    default=0.0,
                )

        combined_matches = sorted(
            retrieval_matches + criteria_vector_matches,
            key=lambda item: safe_float_fn(item.get("score"), 0.0),
            reverse=True,
        )
        deduped_matches = []
        seen_keys = set()
        for match_item in combined_matches:
            key_value = f"{match_item.get('ruleId') or ''}::{normalize_pharma_text_fn(match_item.get('text') or '')}"
            if key_value in seen_keys:
                continue
            seen_keys.add(key_value)
            deduped_matches.append(match_item)
            if len(deduped_matches) >= 10:
                break

        row["retrievalMatches"] = deduped_matches
        row["criteriaLevelMatches"] = deduped_matches
        row["retrievalScore"] = safe_float_fn(
            max([item.get("score", 0) for item in deduped_matches], default=0.0),
            0.0,
        )
        row["criteriaLevelScore"] = row["retrievalScore"]
        row["criteriaVectorScore"] = round(float(criteria_vector_score), 4)
        row["finalScore"] = structured["structuredScore"]
        trial_rows.append(row)
    timings["structuredMs"] = round((time.time() - t2) * 1000, 1)

    rerank_applied = False
    if (not hybrid_search_used) and rerank_enabled and voyage_api_key and trial_rows and voyage_rerank_results_fn:
        docs_for_rerank = [build_trial_match_text_fn(trial) for trial in trial_rows]
        try:
            reranked = voyage_rerank_results_fn(
                query_text=matching_query,
                documents_to_rank=docs_for_rerank,
                rerank_model=voyage_rerank_model,
                top_k=min(max(top_k_value * 2, 6), len(docs_for_rerank)),
            )
            for row in reranked:
                idx = row.get("index")
                if idx is None or idx >= len(trial_rows):
                    continue
                semantic_score = safe_float_fn(row.get("relevance_score"), 0.0)
                trial_rows[idx]["semanticScore"] = round(float(semantic_score), 4)
            rerank_applied = True
        except Exception as rerank_error:
            logger.error("Pharma trial rerank failed: %s", rerank_error)

    blend_formula = "structured eligibility is primary; retrieval signals are gated by evidence completeness"
    for row in trial_rows:
        semantic = row.get("semanticScore")
        eligibility_val = safe_float_fn(row.get("eligibilityScore"), safe_float_fn(row.get("structuredScore"), 0.0))
        completeness_val = safe_float_fn(row.get("evidenceCompletenessScore"), 0.0)
        structured_val = safe_float_fn(row.get("structuredScore"), 0.0)
        retrieval_val = safe_float_fn(row.get("retrievalScore"), 0.0)
        ranking_payload = compute_trial_match_ranking(
            structured_score=structured_val,
            completeness_score=completeness_val,
            semantic_score=semantic,
            retrieval_score=retrieval_val,
            hybrid_search_used=hybrid_search_used,
            overall_assessment=row.get("overallAssessment"),
        )
        row["finalScore"] = ranking_payload["finalScore"]
        row["scoreBreakdown"] = {
            "eligibilityScore": round(float(eligibility_val), 4),
            "evidenceCompletenessScore": round(float(completeness_val), 4),
            "weights": ranking_payload["weights"],
            "retrievalScore": round(float(retrieval_val), 4),
            "semanticScore": round(float(safe_float_fn(semantic, 0.0)), 4) if semantic is not None else None,
            "vectorConfidenceFactor": ranking_payload["vectorConfidenceFactor"],
            "retrievalConfidenceFactor": ranking_payload["retrievalConfidenceFactor"],
            "completenessPenaltyFactor": ranking_payload["completenessPenaltyFactor"],
            "reviewCap": ranking_payload["reviewCap"],
            "hardFailPenaltyApplied": bool(normalize_pharma_text_fn(row.get("overallAssessment")) == "likely_ineligible"),
        }
        row["syntheaCoverage"] = estimate_trial_coverage_with_synthea_fn(row)

    canonical_trial_runtime = None
    canonical_trial_error = None
    canonical_trial_criteria_cache = {}
    canonical_patient_ref = str((patient_link or {}).get("syntheaPatientRef") or "").strip()
    if STRICT_CANONICAL_MODE and trial_rows and canonical_patient_ref:
        canonical_trial_runtime, canonical_trial_error = _canonical_trial_match_map(
            trial_ids=[row.get("trialId") or row.get("nctId") for row in trial_rows],
            patient_ref=canonical_patient_ref,
            limit=len(trial_rows),
        )
        if canonical_trial_runtime:
            canonical_rows = canonical_trial_runtime.get("trialVersionById") or {}
            service_collections = canonical_trial_runtime.get("collections")
            for row in trial_rows:
                trial_key = str(row.get("trialId") or row.get("nctId") or "").strip()
                canonical_row = canonical_rows.get(trial_key)
                if not canonical_row or service_collections is None:
                    continue
                trial_version_id = str(canonical_row.get("trialVersionId") or "").strip()
                if trial_key not in canonical_trial_criteria_cache:
                    canonical_trial_criteria_cache[trial_key] = _criterion_lookup_for_trial(
                        service_collections=service_collections,
                        trial_id=trial_key,
                        version_id=trial_version_id,
                    )
                compat_eval = _build_legacy_structured_eval_from_canonical(
                    criteria_rows=canonical_row.get("criteria") or [],
                    criteria_lookup=canonical_trial_criteria_cache.get(trial_key) or {},
                    overall=canonical_row.get("overall"),
                )
                row.update(
                    {
                        "isEligibleStructured": compat_eval.get("isEligibleStructured"),
                        "overallAssessment": compat_eval.get("overallAssessment"),
                        "structuredScore": compat_eval.get("structuredScore"),
                        "eligibilityScore": compat_eval.get("eligibilityScore"),
                        "evidenceCompletenessScore": compat_eval.get("evidenceCompletenessScore"),
                        "matchReasons": compat_eval.get("matchReasons") or [],
                        "missingCriteria": compat_eval.get("missingCriteria") or [],
                        "decisionSummary": compat_eval.get("decisionSummary") or {},
                        "parserWarnings": compat_eval.get("parserWarnings") or [],
                        "validatorWarnings": compat_eval.get("validatorWarnings") or [],
                        "criterionBreakdown": compat_eval.get("criterionBreakdown") or [],
                        "criteriaLevelMatches": compat_eval.get("criteriaLevelMatches") or [],
                        "ruleEvaluations": compat_eval.get("ruleEvaluations") or [],
                        "coverageMeter": compat_eval.get("coverageMeter") or {},
                        "canonicalCompat": {
                            "applied": True,
                            "runId": canonical_trial_runtime.get("runId"),
                            "matcher": "clinical_trials_service_app",
                        },
                    }
                )
                semantic = row.get("semanticScore")
                retrieval_val = safe_float_fn(row.get("retrievalScore"), 0.0)
                ranking_payload = compute_trial_match_ranking(
                    structured_score=compat_eval.get("structuredScore"),
                    completeness_score=compat_eval.get("evidenceCompletenessScore"),
                    semantic_score=semantic,
                    retrieval_score=retrieval_val,
                    hybrid_search_used=hybrid_search_used,
                    overall_assessment=compat_eval.get("overallAssessment"),
                )
                row["finalScore"] = ranking_payload["finalScore"]
                row["scoreBreakdown"] = {
                    "eligibilityScore": compat_eval.get("eligibilityScore"),
                    "evidenceCompletenessScore": compat_eval.get("evidenceCompletenessScore"),
                    "weights": ranking_payload["weights"],
                    "retrievalScore": round(float(retrieval_val), 4),
                    "semanticScore": round(float(safe_float_fn(semantic, 0.0)), 4) if semantic is not None else None,
                    "vectorConfidenceFactor": ranking_payload["vectorConfidenceFactor"],
                    "retrievalConfidenceFactor": ranking_payload["retrievalConfidenceFactor"],
                    "completenessPenaltyFactor": ranking_payload["completenessPenaltyFactor"],
                    "reviewCap": ranking_payload["reviewCap"],
                    "hardFailPenaltyApplied": bool(
                        normalize_pharma_text_fn(compat_eval.get("overallAssessment")) == "likely_ineligible"
                    ),
                }

    trial_rows_sorted = sorted(
        trial_rows,
        key=lambda item: (
            assessment_rank(item.get("overallAssessment")),
            safe_float_fn(item.get("finalScore"), 0),
            safe_float_fn(item.get("structuredScore"), 0),
        ),
        reverse=True,
    )
    ranked_rows = trial_rows_sorted[:top_k_value]
    runtime_weight_snapshot = (((ranked_rows[0] or {}).get("scoreBreakdown") or {}).get("weights") if ranked_rows else {}) or {}
    timings["totalMs"] = round(sum(v for v in timings.values() if isinstance(v, (int, float))), 1)

    research_ops = []
    for index, row in enumerate(ranked_rows[:3], start=1):
        coverage = (row.get("syntheaCoverage") or {}).get("estimatedEligiblePatients") or 0
        research_ops.append(
            {
                "priority": index,
                "action": f"Open site outreach for {row.get('trialId')}",
                "reason": f"Final score {row.get('finalScore')} with synthetic cohort estimate {coverage}",
            }
        )

    return {
        "success": True,
        "query": matching_query,
        "patientContext": context,
        "patientEvidencePack": evidence_pack,
        "patientContextStatus": patient_context_status,
        "patientLink": patient_link,
        "matches": ranked_rows,
        "syntheaAnalogs": analog_candidates,
        "pipelineVisibility": {
            "matcher": "shared_pharma_navigator_trial_match",
            "candidateSearchMode": candidate_search_mode,
            "hybridSearchEnabled": hybrid_search_used,
            "vectorSearchIndex": voyage_pharma_vector_index_name if hybrid_search_used else None,
            "embeddingModel": voyage_embedding_model if hybrid_search_used else None,
            "embeddingDimensions": 1024 if hybrid_search_used else None,
            "vectorSearchCandidates": num_candidates if hybrid_search_used else None,
            "vectorSearchLimit": candidate_pool if hybrid_search_used else None,
            "vectorSearchReturned": vector_search_count if hybrid_search_used else None,
            "vectorReadiness": vector_readiness,
            "vectorFallbackReason": vector_fallback_reason,
            "trialPrefilter": {
                "mode": "deterministic_hard_filters",
                "query": trial_prefilter_query,
                "catalogFilters": filter_payload,
                "catalogFilterApplied": catalog_filter_applied,
                "candidateQuery": trial_candidate_query,
                "prefilteredTrialCount": prefiltered_trial_count,
                "appliedInlineVectorFilter": prefilter_filter_used_in_search,
                "appliedPostVectorMatch": prefilter_post_filter_applied,
            },
            "structuredRuleEngine": {
                "uses": [
                    "atomic rules",
                    "risk paths",
                    "deterministic pass/fail/unknown",
                    "missing-data checklist",
                    "rule_kind_filtering(patient_eligibility only)",
                    "compiled canonical criteria" if canonical_trial_runtime else "legacy atomic compatibility rules",
                ],
                "runtimeWeights": {
                    "structured": runtime_weight_snapshot.get("structured"),
                    "vectorSearch": runtime_weight_snapshot.get("vectorSearch"),
                    "retrievalSignals": runtime_weight_snapshot.get("retrievalSignals"),
                },
                "adaptiveWeighting": True,
                "canonicalCompat": {
                    "enabled": STRICT_CANONICAL_MODE,
                    "applied": bool(canonical_trial_runtime),
                    "runId": (canonical_trial_runtime or {}).get("runId") if isinstance(canonical_trial_runtime, dict) else None,
                    "error": canonical_trial_error,
                },
            },
            "criteriaEmbeddingEnabled": True,
            "criteriaEmbeddingSource": (
                "criteriaChunkEmbeddings+criteriaChunks"
                if criteria_vector_used
                else "criteriaChunks(token-overlap)"
            ),
            "criteriaVectorUsed": criteria_vector_used,
            "rerankApplied": rerank_applied,
            "blendFormula": blend_formula,
            "timings": timings,
            "syntheaAnalogPipeline": analog_pipeline,
            "patientLink": patient_link,
            "patientContextStatus": patient_context_status,
        },
        "agenticResearchOps": research_ops,
    }


def create_pharma_blueprint(*, deps):
    parse_bool_value = deps["parse_bool_value_fn"]
    parse_int_value = deps["parse_int_value_fn"]
    ensure_pharma_indexes = deps["ensure_pharma_indexes_fn"]
    audit_trial_matching_dataset_readiness = deps["audit_trial_matching_dataset_readiness_fn"]
    audit_breast_oncology_cohort_quality = deps["audit_breast_oncology_cohort_quality_fn"]
    build_patient_curation_coverage_summary = deps["build_patient_curation_coverage_summary_fn"]
    load_synthea_resources_if_missing = deps["load_synthea_resources_if_missing_fn"]
    rebuild_pharma_patient_profiles = deps["rebuild_pharma_patient_profiles_fn"]
    rebuild_pharma_patient_facts = deps["rebuild_pharma_patient_facts_fn"]
    rebuild_breast_oncology_cohort = deps["rebuild_breast_oncology_cohort_fn"]
    rebuild_pharma_feature_edges = deps["rebuild_pharma_feature_edges_fn"]
    SYNTHEA_RESOURCES_NDJSON_PATH = deps["synthea_resources_ndjson_path"]
    db_pharma_trials_collection = deps["db_pharma_trials_collection"]
    serialize_doc = deps["serialize_doc_fn"]
    logging = deps["logger"]
    CLINICALTRIALS_API_BASE = deps["clinicaltrials_api_base"]
    CLINICALTRIALS_TIMEOUT_SEC = deps["clinicaltrials_timeout_sec"]
    CLINICALTRIALS_DEFAULT_CONDITION = deps["clinicaltrials_default_condition"]
    CLINICALTRIALS_SYNC_DAYS_BACK = deps["clinicaltrials_sync_days_back"]
    ACTIVE_TRIAL_STATUSES = deps["active_trial_statuses"]
    ACTIVE_TRIAL_STATUSES_ALL = deps["active_trial_statuses_all"]
    db_clinical_reports_collection = deps["db_clinical_reports_collection"]
    fetch_clinicaltrials_studies = deps["fetch_clinicaltrials_studies_fn"]
    normalize_ctgov_study = deps["normalize_ctgov_study_fn"]
    build_trial_prefilter_contract = deps["build_trial_prefilter_contract_fn"]
    resolve_trial_prefilter_contract = deps["resolve_trial_prefilter_contract_fn"]
    resolve_trial_definition_canonical = deps["resolve_trial_definition_canonical_fn"]
    build_trial_query_for_patient_context = deps["build_trial_query_for_patient_context_fn"]
    evaluate_patient_context_against_trial_prefilter = deps["evaluate_patient_context_against_trial_prefilter_fn"]
    evaluate_profile_against_trial_prefilter = deps["evaluate_profile_against_trial_prefilter_fn"]
    build_leafy_patient_context = deps["build_leafy_patient_context_fn"]
    build_patient_evidence_profile = deps["build_patient_evidence_profile_fn"]
    build_patient_match_profile = deps["build_patient_match_profile_fn"]
    build_oncology_cohort_assessment_input = deps["build_oncology_cohort_assessment_input_fn"]
    build_breast_oncology_cohort_document = deps["build_breast_oncology_cohort_document_fn"]
    infer_synthea_analog_patients = deps["infer_synthea_analog_patients_fn"]
    resolve_leafy_synthea_link = deps["resolve_leafy_synthea_link_fn"]
    list_pending_leafy_synthea_linkages = deps["list_pending_leafy_synthea_linkages_fn"]
    build_trial_matching_control_plane_summary = deps["build_trial_matching_control_plane_summary_fn"]
    list_trial_matching_evaluation_runs = deps["list_trial_matching_evaluation_runs_fn"]
    list_trial_matching_improvement_runs = deps["list_trial_matching_improvement_runs_fn"]
    run_trial_matching_control_plane_evaluation = deps["run_trial_matching_control_plane_evaluation_fn"]
    _resolve_linked_synthea_patient_ref = deps["resolve_linked_synthea_patient_ref_fn"]
    build_patient_evidence_pack = deps["build_patient_evidence_pack_fn"]
    merge_trial_version_history = deps["merge_trial_version_history_fn"]
    _invoke_agent_tool = deps["invoke_agent_tool_fn"]
    db_synthea_resources_collection = deps["db_synthea_resources_collection"]
    db_pharma_patient_facts_collection = deps["db_pharma_patient_facts_collection"]
    SYNTHEA_FHIR_DB = deps["synthea_fhir_db"]
    SYNTHEA_RESOURCES_COLLECTION = deps["synthea_resources_collection"]
    VOYAGE_API_KEY = deps["voyage_api_key"]
    VOYAGE_EMBEDDING_MODEL = deps["voyage_embedding_model"]
    VOYAGE_RERANK_MODEL = deps["voyage_rerank_model"]
    voyage_embed_text = deps["voyage_embed_text_fn"]
    voyage_embed_texts = deps["voyage_embed_texts_fn"]
    voyage_rerank_results = deps["voyage_rerank_results_fn"]
    VOYAGE_PHARMA_VECTOR_INDEX_NAME = deps["voyage_pharma_vector_index_name"]
    VOYAGE_PHARMA_PATIENT_VECTOR_INDEX_NAME = deps["voyage_pharma_patient_vector_index_name"]
    batch_embed_trial_documents = deps["batch_embed_trial_documents_fn"]
    batch_embed_profile_documents = deps["batch_embed_profile_documents_fn"]
    evaluate_trial_structured_match = deps["evaluate_trial_structured_match_fn"]
    build_trial_match_text = deps["build_trial_match_text_fn"]
    build_profile_match_text = deps["build_profile_match_text_fn"]
    safe_float = deps["safe_float_fn"]
    estimate_trial_coverage_with_synthea = deps["estimate_trial_coverage_with_synthea_fn"]
    build_profile_query_for_trial = deps["build_profile_query_for_trial_fn"]
    db_pharma_profiles_collection = deps["db_pharma_profiles_collection"]
    normalize_pharma_text = deps["normalize_pharma_text_fn"]
    db_pharma_feature_edges_collection = deps.get("db_pharma_feature_edges_collection")
    PHARMA_FEATURE_EDGES_COLLECTION = deps.get("pharma_feature_edges_collection_name")
    PHARMA_ONCOLOGY_COHORT_COLLECTION = deps["pharma_oncology_cohort_collection_name"]
    BEDROCK_DEFAULT_MODEL = deps.get("bedrock_default_model", "us.anthropic.claude-sonnet-4-20250514-v1:0")
    db_leafy_synthea_links_collection = deps.get("db_leafy_synthea_links_collection")

    blueprint = Blueprint("pharma_routes", __name__)
    source_status_cache = {
        "external": None,
        "externalExpiresAt": 0.0,
        "payload": None,
        "payloadExpiresAt": 0.0,
        "vectorReadiness": None,
        "vectorExpiresAt": 0.0,
    }
    source_status_cache_ttl_sec = 300.0
    source_vector_status_cache_ttl_sec = 900.0
    control_plane_cache = {
        "payload": None,
        "expiresAt": 0.0,
    }
    control_plane_cache_ttl_sec = 300.0
    trial_list_cache = {}
    trial_list_cache_ttl_sec = 90.0

    def load_cached_external_source_status():
        now = time.time()
        cached_payload = source_status_cache.get("external")
        expires_at = float(source_status_cache.get("externalExpiresAt") or 0.0)
        if cached_payload and now < expires_at:
            return dict(cached_payload)

        external_status = {
            "reachable": False,
            "apiVersion": None,
            "dataTimestamp": None,
            "error": None,
        }
        try:
            response = requests.get(
                f"{CLINICALTRIALS_API_BASE.rstrip('/')}/version",
                timeout=min(CLINICALTRIALS_TIMEOUT_SEC, 3)
            )
            response.raise_for_status()
            payload = response.json()
            external_status = {
                "reachable": True,
                "apiVersion": payload.get("apiVersion"),
                "dataTimestamp": payload.get("dataTimestamp"),
                "error": None,
            }
        except Exception as external_error:
            external_status["error"] = str(external_error)

        source_status_cache["external"] = dict(external_status)
        source_status_cache["externalExpiresAt"] = now + source_status_cache_ttl_sec
        return external_status

    def load_cached_vector_readiness():
        now = time.time()
        cached_payload = source_status_cache.get("vectorReadiness")
        expires_at = float(source_status_cache.get("vectorExpiresAt") or 0.0)
        if cached_payload and now < expires_at:
            return dict(cached_payload)

        payload = {
            "trialIndex": get_vector_index_readiness(
                db_pharma_trials_collection,
                VOYAGE_PHARMA_VECTOR_INDEX_NAME,
                expected_path="vectorEmbedding",
                expected_dims=1024,
            ),
            "patientIndex": get_vector_index_readiness(
                db_pharma_profiles_collection,
                VOYAGE_PHARMA_PATIENT_VECTOR_INDEX_NAME,
                expected_path="profileVectorEmbedding",
                expected_dims=1024,
            ),
        }
        source_status_cache["vectorReadiness"] = dict(payload)
        source_status_cache["vectorExpiresAt"] = now + source_vector_status_cache_ttl_sec
        return payload

    def load_cached_trial_list(cache_key):
        entry = trial_list_cache.get(cache_key) or {}
        expires_at = float(entry.get("expiresAt") or 0.0)
        if entry.get("payload") and time.time() < expires_at:
            return dict(entry["payload"])
        if cache_key in trial_list_cache:
            trial_list_cache.pop(cache_key, None)
        return None

    def store_cached_trial_list(cache_key, payload):
        trial_list_cache[cache_key] = {
            "payload": dict(payload or {}),
            "expiresAt": time.time() + trial_list_cache_ttl_sec,
        }

    def merge_mongo_clauses(*query_parts):
        clauses = []
        for part in query_parts:
            if not isinstance(part, dict) or not part:
                continue
            clauses.append(part)
        if not clauses:
            return {}
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    def build_trial_list_query_from_filters(filter_source):
        filters = filter_source if isinstance(filter_source, dict) else {}
        status_filter = str(filters.get("status") or "all").strip()
        phase_filter = str(filters.get("phase") or "all").strip()
        source_filter = str(filters.get("source") or "all").strip()
        sponsor_filter = str(filters.get("sponsor") or "").strip()
        country_filter = str(filters.get("country") or "").strip()
        text_query = str(filters.get("q") or "").strip()

        query = {}
        if status_filter and status_filter.lower() != "all":
            query["status"] = status_filter
        if phase_filter and phase_filter.lower() != "all":
            query["phase"] = phase_filter
        if source_filter and source_filter.lower() != "all":
            if source_filter == "ctgov":
                query["source"] = "ClinicalTrials.gov API v2"
        if sponsor_filter:
            query["sponsor"] = {"$regex": re.escape(sponsor_filter), "$options": "i"}
        if country_filter:
            query["siteCoverage.country"] = {"$regex": re.escape(country_filter), "$options": "i"}
        if text_query:
            query["$or"] = [
                {"title": {"$regex": re.escape(text_query), "$options": "i"}},
                {"summary": {"$regex": re.escape(text_query), "$options": "i"}},
                {"conditions": {"$elemMatch": {"$regex": re.escape(text_query), "$options": "i"}}},
                {"tags": {"$elemMatch": {"$regex": re.escape(text_query), "$options": "i"}}},
            ]
        return query

    def get_vector_index_readiness(collection_obj, index_name, expected_path=None, expected_dims=1024):
        payload = {
            "indexName": index_name,
            "exists": False,
            "pathMatches": None,
            "dimensionsMatches": None,
            "configuredPath": None,
            "configuredDimensions": None,
            "error": None,
        }
        try:
            try:
                indexes = list(collection_obj.list_search_indexes(name=index_name))
            except TypeError:
                indexes = list(collection_obj.list_search_indexes(index_name))
            except Exception:
                indexes = list(collection_obj.list_search_indexes())
            target = None
            for item in indexes:
                if not isinstance(item, dict):
                    continue
                name_value = item.get("name") or item.get("indexName")
                if name_value == index_name:
                    target = item
                    break
            if not target and indexes:
                target = indexes[0]
            if not target:
                return payload
            payload["exists"] = True

            definition = (
                target.get("latestDefinition")
                or target.get("definition")
                or {}
            )
            fields = definition.get("fields") if isinstance(definition, dict) else None
            vector_field = None

            def walk_fields(node):
                nonlocal vector_field
                if vector_field is not None:
                    return
                if isinstance(node, list):
                    for child in node:
                        walk_fields(child)
                    return
                if not isinstance(node, dict):
                    return
                node_type = str(node.get("type") or "").lower()
                if node_type in {"vector", "knnvector", "vectorsearch"}:
                    vector_field = node
                    return
                for child in node.values():
                    walk_fields(child)

            walk_fields(fields)
            if isinstance(vector_field, dict):
                payload["configuredPath"] = vector_field.get("path")
                configured_dims = vector_field.get("numDimensions") or vector_field.get("dimensions")
                payload["configuredDimensions"] = configured_dims
                if expected_path:
                    payload["pathMatches"] = (str(payload["configuredPath"] or "") == str(expected_path))
                if configured_dims is not None:
                    payload["dimensionsMatches"] = (int(configured_dims) == int(expected_dims))
            return payload
        except Exception as index_error:
            payload["error"] = str(index_error)
            return payload

    def assess_patient_context_status(patient_context, patient_link, evidence_pack=None):
        context = patient_context if isinstance(patient_context, dict) else {}
        link = patient_link if isinstance(patient_link, dict) else {}
        linkage_status = str(link.get("linkageStatus") or "").strip().lower()
        missing_fields = list((evidence_pack or {}).get("missing_fields") or [])[:12] if isinstance(evidence_pack, dict) else []
        blockers = []
        if context.get("age") is None:
            blockers.append("age")
        if not str(context.get("sex") or "").strip():
            blockers.append("sex")
        if not (context.get("biradsValues") or []):
            blockers.append("biradsValues")

        if len(blockers) >= 3:
            return {
                "status": "insufficient_leafy_data",
                "message": "Leafy evidence is insufficient for reliable deterministic adjudication.",
                "nextAction": "Complete core screening fields (age/sex/BI-RADS).",
                "blockers": blockers,
                "missingFields": missing_fields,
                "canRunDeterministicMatch": False,
                "screeningOnly": False,
                "longitudinalEvidenceReady": False,
            }

        if linkage_status == "ambiguous":
            return {
                "status": "screening_only_ready",
                "message": "Screening context is ready for deterministic matching, but the longitudinal patient link is still ambiguous.",
                "nextAction": "Run with screening evidence now and resolve linkage from candidateOptions to enrich the evidence pack.",
                "blockers": blockers,
                "missingFields": missing_fields,
                "canRunDeterministicMatch": True,
                "screeningOnly": True,
                "longitudinalEvidenceReady": False,
            }
        if linkage_status == "legacy_heuristic_link":
            return {
                "status": "screening_only_ready",
                "message": "Screening context is ready for deterministic matching, but the existing longitudinal link is heuristic only.",
                "nextAction": "Run with screening evidence now and replace the heuristic link with an authoritative linked patient record.",
                "blockers": blockers,
                "missingFields": missing_fields,
                "canRunDeterministicMatch": True,
                "screeningOnly": True,
                "longitudinalEvidenceReady": False,
            }
        if linkage_status == "candidate_only":
            return {
                "status": "screening_only_ready",
                "message": "Screening context is ready for deterministic matching. Longitudinal linkage is still pending, so unresolved criteria will remain explicit for review.",
                "nextAction": "Resolve linkage from candidateOptions to enrich this patient with longitudinal evidence.",
                "blockers": blockers,
                "missingFields": missing_fields,
                "canRunDeterministicMatch": True,
                "screeningOnly": True,
                "longitudinalEvidenceReady": False,
            }
        if linkage_status not in {"linked"}:
            return {
                "status": "screening_only_ready",
                "message": "Screening context is ready for deterministic matching, but no linked longitudinal patient record is available yet.",
                "nextAction": "Resolve an authoritative screening-to-FHIR patient link to enrich the evidence pack.",
                "blockers": blockers,
                "missingFields": missing_fields,
                "canRunDeterministicMatch": True,
                "screeningOnly": True,
                "longitudinalEvidenceReady": False,
            }

        return {
            "status": "ready",
            "message": "Patient context is linked and ready for trial matching.",
            "nextAction": None,
            "blockers": blockers,
            "missingFields": missing_fields[:12],
            "canRunDeterministicMatch": True,
            "screeningOnly": False,
            "longitudinalEvidenceReady": True,
        }

    def cosine_similarity(left_vector, right_vector):
        if not isinstance(left_vector, list) or not isinstance(right_vector, list):
            return 0.0
        if not left_vector or not right_vector:
            return 0.0
        if len(left_vector) != len(right_vector):
            return 0.0
        dot = 0.0
        norm_left = 0.0
        norm_right = 0.0
        for left_value, right_value in zip(left_vector, right_vector):
            try:
                left_num = float(left_value)
                right_num = float(right_value)
            except Exception:
                return 0.0
            dot += left_num * right_num
            norm_left += left_num * left_num
            norm_right += right_num * right_num
        if norm_left <= 0.0 or norm_right <= 0.0:
            return 0.0
        return float(dot / (math.sqrt(norm_left) * math.sqrt(norm_right)))

    def parse_json_object(text_value):
        raw_text = str(text_value or "").strip()
        if not raw_text:
            return {}
        try:
            parsed = json.loads(raw_text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass
        match = re.search(r"\{[\s\S]*\}", raw_text)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def build_rule_llm_context(rule_eval, evidence_pack, max_snippets=8):
        rule_payload = rule_eval if isinstance(rule_eval, dict) else {}
        evidence = evidence_pack if isinstance(evidence_pack, dict) else {}
        facts = evidence.get("facts") or {}
        snippets = evidence.get("snippets") or []
        available_fact_paths = []
        compact_facts = {}
        for key, value in list(facts.items())[:20]:
            fact_path = f"facts.{key}"
            available_fact_paths.append(fact_path)
            compact_facts[key] = {
                "value": (value or {}).get("value") if isinstance(value, dict) else value,
                "source": (value or {}).get("source") if isinstance(value, dict) else None,
            }
            if isinstance(value, dict) and value.get("date") is not None:
                compact_facts[key]["date"] = value.get("date")

        snippet_rows = []
        for index, snippet in enumerate(snippets[:max_snippets], start=1):
            if not isinstance(snippet, dict):
                continue
            snippet_id = f"snippets.{index}"
            snippet_rows.append(
                {
                    "pointer": snippet_id,
                    "source": snippet.get("source"),
                    "date": snippet.get("date"),
                    "text": str(snippet.get("text") or "")[:420],
                }
            )

        available_pointers = set(available_fact_paths + [row.get("pointer") for row in snippet_rows if row.get("pointer")])
        return {
            "rule": {
                "ruleId": rule_payload.get("ruleId"),
                "type": rule_payload.get("type"),
                "text": rule_payload.get("text"),
                "conceptTags": rule_payload.get("conceptTags") or [],
                "dataNeeded": rule_payload.get("dataNeeded") or [],
                "missingData": rule_payload.get("missingData") or [],
            },
            "facts": compact_facts,
            "snippets": snippet_rows,
            "availablePointers": sorted(list(available_pointers)),
        }

    def normalize_llm_rule_output(*, raw_output, rule_eval, llm_context, model_name, prompt_version):
        payload = raw_output if isinstance(raw_output, dict) else {}
        status = str(payload.get("status") or "UNKNOWN").strip().upper()
        if status not in {"PASS", "FAIL", "UNKNOWN"}:
            status = "UNKNOWN"
        candidate_citations = payload.get("evidencePointers")
        if not isinstance(candidate_citations, list):
            candidate_citations = payload.get("citations")
        if not isinstance(candidate_citations, list):
            candidate_citations = []
        allowed_pointers = set(llm_context.get("availablePointers") or [])
        evidence_pointers = []
        for item in candidate_citations:
            pointer = str(item or "").strip()
            if pointer and pointer in allowed_pointers and pointer not in evidence_pointers:
                evidence_pointers.append(pointer)
        missing_data = payload.get("missingData")
        if not isinstance(missing_data, list):
            missing_data = list(rule_eval.get("missingData") or [])
        missing_data = [str(item).strip() for item in missing_data if str(item).strip()]
        reasoning = str(payload.get("reasoning") or payload.get("message") or "").strip()

        if status in {"PASS", "FAIL"} and not evidence_pointers:
            status = "UNKNOWN"
            if "evidencePointers" not in missing_data:
                missing_data.append("evidencePointers")
            evaluation_mode = "llm_missing_citation"
        elif status in {"PASS", "FAIL"}:
            evaluation_mode = "llm_cited"
        else:
            evaluation_mode = "llm_adjudication_required"

        return {
            "ruleId": rule_eval.get("ruleId"),
            "status": status,
            "evaluationMode": evaluation_mode,
            "evidencePointers": evidence_pointers[:6],
            "missingData": missing_data[:8],
            "patientValue": payload.get("patientValue") or ("narrative_evidence" if evidence_pointers else None),
            "reasoning": reasoning[:320],
            "llmMeta": {
                "modelVersion": model_name,
                "promptVersion": prompt_version,
                "processedAt": datetime.utcnow().isoformat() + "Z",
            },
        }

    llm_adjudication_jobs = {}
    llm_adjudication_jobs_lock = Lock()
    llm_adjudication_job_ttl_sec = 1800

    def prune_llm_adjudication_jobs():
        now_epoch = time.time()
        stale_ids = []
        with llm_adjudication_jobs_lock:
            for job_id, payload in llm_adjudication_jobs.items():
                updated_epoch = float(payload.get("updatedAtEpoch") or payload.get("createdAtEpoch") or 0.0)
                if updated_epoch <= 0.0:
                    continue
                if (now_epoch - updated_epoch) > llm_adjudication_job_ttl_sec:
                    stale_ids.append(job_id)
            for job_id in stale_ids:
                llm_adjudication_jobs.pop(job_id, None)

    def build_llm_adjudication_job_payload(job_id, job_row):
        row = job_row if isinstance(job_row, dict) else {}
        return {
            "jobId": job_id,
            "status": row.get("status"),
            "createdAt": row.get("createdAt"),
            "updatedAt": row.get("updatedAt"),
            "progress": row.get("progress") or {},
            "error": row.get("error"),
        }
    @blueprint.route("/pharma/bootstrap", methods=["POST"])
    def bootstrap_pharma_data():
        try:
            payload = request.get_json(silent=True) or {}
            rebuild_profiles = parse_bool_value(payload.get("rebuildPatientProfiles"), True)
            rebuild_patient_facts = parse_bool_value(payload.get("rebuildPatientFacts"), True)
            rebuild_oncology_cohort = parse_bool_value(
                payload.get("rebuildOncologyCohort"),
                bool(PHARMA_ONCOLOGY_COHORT_COLLECTION),
            )
            rebuild_edges = parse_bool_value(payload.get("rebuildFeatureEdges"), False)
            load_synthea_if_empty = parse_bool_value(payload.get("loadSyntheaIfEmpty"), True)
            ensure_indexes = parse_bool_value(payload.get("ensureIndexes"), True)
    
            bootstrap_summary = {}
            if ensure_indexes:
                ensure_pharma_indexes()
                bootstrap_summary["indexesEnsured"] = True
    
            if load_synthea_if_empty:
                bootstrap_summary["syntheaLoad"] = load_synthea_resources_if_missing(SYNTHEA_RESOURCES_NDJSON_PATH)

            if rebuild_patient_facts:
                bootstrap_summary["patientFactsBuild"] = rebuild_pharma_patient_facts()
            if rebuild_profiles:
                bootstrap_summary["profileBuild"] = rebuild_pharma_patient_profiles()
            if rebuild_oncology_cohort:
                bootstrap_summary["oncologyCohortBuild"] = rebuild_breast_oncology_cohort()
            if rebuild_edges:
                bootstrap_summary["edgeBuild"] = rebuild_pharma_feature_edges()
            bootstrap_summary["datasetReadiness"] = audit_trial_matching_dataset_readiness()
            bootstrap_summary["oncologyCohortQuality"] = audit_breast_oncology_cohort_quality()
            bootstrap_summary["patientCurationCoverage"] = build_patient_curation_coverage_summary()

            return jsonify({
                "success": True,
                "summary": bootstrap_summary
            }), 200
        except Exception as e:
            logging.error(f"Error bootstrapping pharma data: {e}")
            return jsonify({"success": False, "error": str(e)}), 500


    @blueprint.route("/pharma/dataset_readiness", methods=["GET"])
    def get_trial_matching_dataset_readiness():
        try:
            min_patients = request.args.get("minPatients")
            summary = audit_trial_matching_dataset_readiness(min_patients=min_patients)
            return jsonify({"success": True, "summary": summary}), 200
        except Exception as e:
            logging.error(f"Error loading trial-matching dataset readiness: {e}")
            return jsonify({"success": False, "error": str(e)}), 500


    @blueprint.route("/pharma/oncology_cohort_quality", methods=["GET"])
    def get_oncology_cohort_quality():
        try:
            summary = audit_breast_oncology_cohort_quality()
            return jsonify(
                {
                    "success": True,
                    "summary": summary,
                    "collection": PHARMA_ONCOLOGY_COHORT_COLLECTION or None,
                }
            ), 200
        except Exception as e:
            logging.error(f"Error loading oncology cohort quality: {e}")
            return jsonify({"success": False, "error": str(e)}), 500


    @blueprint.route("/pharma/patient_curation/coverage", methods=["GET"])
    def get_patient_curation_coverage():
        try:
            summary = build_patient_curation_coverage_summary()
            return jsonify({"success": True, "summary": summary}), 200
        except Exception as e:
            logging.error(f"Error loading patient curation coverage: {e}")
            return jsonify({"success": False, "error": str(e)}), 500


    @blueprint.route("/pharma/patient_evidence_profile", methods=["GET"])
    def get_patient_evidence_profile():
        try:
            appointment_id = str(request.args.get("appointmentId") or "").strip()
            patient_ref = str(request.args.get("patientRef") or "").strip()
            patient_id = str(request.args.get("patientId") or "").strip() or None
            include_raw = parse_bool_value(request.args.get("includeRaw"), False)

            if appointment_id:
                patient_context, report_doc, appointment_doc = build_leafy_patient_context(
                    appointment_id=appointment_id,
                    patient_id=patient_id,
                )
                patient_link = _resolve_linked_synthea_patient_ref(
                    patient_context=patient_context,
                    report_doc=report_doc,
                    appointment_doc=appointment_doc,
                    allow_analog=True,
                )
                evidence_pack = build_patient_evidence_pack(
                    patient_context=patient_context,
                    report_doc=report_doc,
                    appointment_doc=appointment_doc,
                    synthea_patient_ref=patient_link.get("syntheaPatientRef"),
                    linkage_payload=patient_link,
                )
                response_payload = {
                    "success": True,
                    "patientEvidenceProfile": evidence_pack.get("patientEvidenceProfile") or {},
                    "patientLink": patient_link,
                }
                if include_raw:
                    response_payload["patientContext"] = patient_context
                    response_payload["report"] = report_doc
                    response_payload["appointment"] = appointment_doc
                    response_payload["evidencePack"] = evidence_pack
                return jsonify(response_payload), 200

            if patient_ref:
                normalized_ref = patient_ref if patient_ref.startswith("Patient/") else f"Patient/{patient_ref}"
                facts_doc = db_pharma_patient_facts_collection.find_one(
                    {"patientRef": normalized_ref},
                    {"_id": 0},
                )
                if not facts_doc:
                    return jsonify({"success": False, "error": "Patient evidence profile not found"}), 404
                stored_evidence = (facts_doc.get("patientEvidenceProfile") or {})
                stored_identity = (stored_evidence.get("identity") or {})
                stored_screening = (stored_evidence.get("screeningImaging") or {})
                screening_summary = None
                if stored_screening:
                    screening_summary = {
                        "reportId": stored_screening.get("reportId"),
                        "appointmentId": stored_screening.get("appointmentId"),
                        "reportDate": stored_screening.get("lastMammogramDate"),
                        "biradsValues": list(stored_screening.get("biradsValues") or []),
                        "biradsLeft": stored_screening.get("biradsLeft"),
                        "biradsRight": stored_screening.get("biradsRight"),
                        "biradsMax": stored_screening.get("biradsMax"),
                        "suspiciousLaterality": stored_screening.get("suspiciousLaterality"),
                        "reportConclusion": stored_screening.get("reportConclusion"),
                        "sourceRefs": list(stored_screening.get("provenance") or []),
                    }
                recomputed_profile = build_patient_evidence_profile(
                    patient_ref=normalized_ref,
                    facts_doc=facts_doc,
                    screening_summary=screening_summary,
                    linkage_payload={
                        "linkageStatus": stored_identity.get("linkageStatus"),
                        "linkageConfidence": stored_identity.get("linkageConfidence"),
                    },
                    normalize_text_fn=normalize_pharma_text,
                )
                return jsonify(
                    {
                        "success": True,
                        "patientRef": normalized_ref,
                        "patientEvidenceProfile": recomputed_profile,
                        "factsDocument": facts_doc if include_raw else None,
                    }
                ), 200

            return jsonify({"success": False, "error": "appointmentId or patientRef is required"}), 400
        except ValueError as value_error:
            return jsonify({"success": False, "error": str(value_error)}), 404
        except Exception as e:
            logging.error(f"Error loading patient evidence profile: {e}")
            return jsonify({"success": False, "error": str(e)}), 500


    @blueprint.route("/pharma/patient_match_profile", methods=["GET"])
    def get_patient_match_profile():
        try:
            appointment_id = str(request.args.get("appointmentId") or "").strip()
            patient_ref = str(request.args.get("patientRef") or "").strip()
            patient_id = str(request.args.get("patientId") or "").strip() or None
            include_raw = parse_bool_value(request.args.get("includeRaw"), False)

            if appointment_id and not patient_ref:
                patient_context, report_doc, appointment_doc = build_leafy_patient_context(
                    appointment_id=appointment_id,
                    patient_id=patient_id,
                )
                patient_link = _resolve_linked_synthea_patient_ref(
                    patient_context=patient_context,
                    report_doc=report_doc,
                    appointment_doc=appointment_doc,
                    allow_analog=True,
                )
                patient_ref = str(patient_link.get("syntheaPatientRef") or "").strip()
                if not patient_ref:
                    return jsonify(
                        {
                            "success": False,
                            "error": "No linked longitudinal patient profile is available for this screening episode.",
                            "patientLink": patient_link,
                        }
                    ), 404

            if not patient_ref:
                return jsonify({"success": False, "error": "appointmentId or patientRef is required"}), 400

            normalized_ref = patient_ref if patient_ref.startswith("Patient/") else f"Patient/{patient_ref}"
            facts_doc = db_pharma_patient_facts_collection.find_one({"patientRef": normalized_ref}, {"_id": 0})
            profile_doc = db_pharma_profiles_collection.find_one({"patientRef": normalized_ref}, {"_id": 0})
            if not profile_doc:
                return jsonify({"success": False, "error": "Patient match profile not found"}), 404
            if not facts_doc:
                return jsonify({"success": False, "error": "Patient evidence profile not found"}), 404

            stored_evidence = (facts_doc.get("patientEvidenceProfile") or {})
            stored_identity = (stored_evidence.get("identity") or {})
            stored_screening = (stored_evidence.get("screeningImaging") or {})
            screening_summary = None
            if stored_screening:
                screening_summary = {
                    "reportId": stored_screening.get("reportId"),
                    "appointmentId": stored_screening.get("appointmentId"),
                    "reportDate": stored_screening.get("lastMammogramDate"),
                    "biradsValues": list(stored_screening.get("biradsValues") or []),
                    "biradsLeft": stored_screening.get("biradsLeft"),
                    "biradsRight": stored_screening.get("biradsRight"),
                    "biradsMax": stored_screening.get("biradsMax"),
                    "suspiciousLaterality": stored_screening.get("suspiciousLaterality"),
                    "reportConclusion": stored_screening.get("reportConclusion"),
                    "sourceRefs": list(stored_screening.get("provenance") or []),
                }
            recomputed_evidence = build_patient_evidence_profile(
                patient_ref=normalized_ref,
                facts_doc=facts_doc,
                screening_summary=screening_summary,
                linkage_payload={
                    "linkageStatus": stored_identity.get("linkageStatus"),
                    "linkageConfidence": stored_identity.get("linkageConfidence"),
                },
                normalize_text_fn=normalize_pharma_text,
            )
            recomputed_facts_doc = dict(facts_doc)
            recomputed_facts_doc["patientEvidenceProfile"] = recomputed_evidence
            oncology_assessment_input = build_oncology_cohort_assessment_input(
                patient_ref=normalized_ref,
                facts_doc=recomputed_facts_doc,
                profile_doc=profile_doc,
            )
            oncology_cohort_document = build_breast_oncology_cohort_document(
                patient_ref=normalized_ref,
                assessment_input=oncology_assessment_input,
            )
            recomputed_match_profile = build_patient_match_profile(
                profile_doc=profile_doc,
                evidence_profile=recomputed_evidence,
                oncology_cohort=oncology_cohort_document,
                normalize_text_fn=normalize_pharma_text,
            )

            return jsonify(
                {
                    "success": True,
                    "patientRef": normalized_ref,
                    "patientMatchProfile": recomputed_match_profile,
                    "profileDocument": profile_doc if include_raw else None,
                }
            ), 200
        except ValueError as value_error:
            return jsonify({"success": False, "error": str(value_error)}), 404
        except Exception as e:
            logging.error(f"Error loading patient match profile: {e}")
            return jsonify({"success": False, "error": str(e)}), 500


    @blueprint.route("/pharma/trials/backfill_embeddings", methods=["POST"])
    def backfill_trial_embeddings():
        """Backfill trial and patient-profile embeddings used by pharma matching pipelines."""
        try:
            payload = request.get_json(silent=True) or {}
            batch_size = min(parse_int_value(payload.get("batchSize"), 64, 1), 128)
            limit = min(parse_int_value(payload.get("limit"), 5000, 1), 30000)
            include_profiles = parse_bool_value(payload.get("includePatientProfiles"), True)

            if not VOYAGE_API_KEY or not voyage_embed_texts:
                return jsonify({"success": False, "error": "VOYAGE_API_KEY is not configured"}), 400

            t0 = time.time()
            trial_cursor = db_pharma_trials_collection.find(
                {
                    "$or": [
                        {"vectorEmbedding": {"$exists": False}},
                        {"criteriaChunkEmbeddings": {"$exists": False}},
                    ],
                    "matchText": {"$exists": True},
                },
                {"_id": 1, "nctId": 1, "matchText": 1, "criteriaChunks": 1, "pharma_trial_rules": 1}
            ).limit(limit)
            trial_docs = list(trial_cursor)

            trial_docs = batch_embed_trial_documents(trial_docs, batch_size=batch_size)
            trial_ops = []
            trials_embedded_count = 0
            trials_criteria_embedded_count = 0
            for doc in trial_docs:
                set_payload = {}
                if isinstance(doc.get("vectorEmbedding"), list):
                    set_payload["vectorEmbedding"] = doc.get("vectorEmbedding")
                    set_payload["vectorEmbeddingModel"] = VOYAGE_EMBEDDING_MODEL
                    trials_embedded_count += 1
                if isinstance(doc.get("criteriaChunkEmbeddings"), list) and len(doc.get("criteriaChunkEmbeddings") or []) > 0:
                    set_payload["criteriaChunkEmbeddings"] = doc.get("criteriaChunkEmbeddings")
                    trials_criteria_embedded_count += 1
                if set_payload:
                    trial_ops.append(
                        UpdateOne(
                            {"_id": doc.get("_id")},
                            {"$set": set_payload}
                        )
                    )
            if trial_ops:
                db_pharma_trials_collection.bulk_write(trial_ops, ordered=False)

            profile_docs = []
            profiles_embedded_count = 0
            profile_ops = []
            if include_profiles:
                profile_cursor = db_pharma_profiles_collection.find(
                    {
                        "$or": [
                            {"profileVectorEmbedding": {"$exists": False}},
                            {"profileMatchText": {"$exists": False}},
                        ]
                    },
                    {
                        "_id": 1,
                        "patientRef": 1,
                        "age": 1,
                        "gender": 1,
                        "conditionTerms": 1,
                        "procedureTerms": 1,
                        "diagnosticTerms": 1,
                        "medicationTerms": 1,
                        "observationTerms": 1,
                        "documentTerms": 1,
                        "featureTerms": 1,
                        "profileMatchText": 1,
                    }
                ).limit(limit)
                profile_docs = list(profile_cursor)
                for doc in profile_docs:
                    if not str(doc.get("profileMatchText") or "").strip():
                        doc["profileMatchText"] = build_profile_match_text(doc)
                profile_docs = batch_embed_profile_documents(profile_docs, batch_size=batch_size)
                for doc in profile_docs:
                    set_payload = {}
                    if str(doc.get("profileMatchText") or "").strip():
                        set_payload["profileMatchText"] = str(doc.get("profileMatchText")).strip()
                    if isinstance(doc.get("profileVectorEmbedding"), list):
                        set_payload["profileVectorEmbedding"] = doc.get("profileVectorEmbedding")
                        set_payload["profileVectorEmbeddingModel"] = VOYAGE_EMBEDDING_MODEL
                        profiles_embedded_count += 1
                    if set_payload:
                        profile_ops.append(
                            UpdateOne(
                                {"_id": doc.get("_id")},
                                {"$set": set_payload}
                            )
                        )
                if profile_ops:
                    db_pharma_profiles_collection.bulk_write(profile_ops, ordered=False)

            elapsed_ms = round((time.time() - t0) * 1000, 1)
            return jsonify({
                "success": True,
                "trialsProcessed": len(trial_docs),
                "trialsEmbedded": trials_embedded_count,
                "trialsCriteriaEmbedded": trials_criteria_embedded_count,
                "patientProfilesProcessed": len(profile_docs),
                "patientProfilesEmbedded": profiles_embedded_count,
                "elapsedMs": elapsed_ms,
                "totalTrials": db_pharma_trials_collection.estimated_document_count(),
                "embeddedTotal": db_pharma_trials_collection.count_documents({"vectorEmbedding": {"$exists": True}}),
                "patientProfilesTotal": db_pharma_profiles_collection.estimated_document_count(),
                "patientProfilesEmbeddedTotal": db_pharma_profiles_collection.count_documents({"profileVectorEmbedding": {"$exists": True}}),
            }), 200
        except Exception as e:
            logging.error(f"Error backfilling trial embeddings: {e}")
            return jsonify({"success": False, "error": str(e)}), 500


    @blueprint.route("/pharma/trials/list", methods=["GET"])
    def list_pharma_trials():
        try:
            page = parse_int_value(request.args.get("page"), 1, 1)
            limit = min(parse_int_value(request.args.get("limit"), 20, 1), 100)
            skip = (page - 1) * limit
            include_facets = parse_bool_value(request.args.get("includeFacets"), True)
            summary_only = parse_bool_value(request.args.get("summaryOnly"), False)
    
            sort_field = request.args.get("sortField", "sourceStudyLastUpdated")
            sort_direction = -1 if str(request.args.get("sortOrder", "desc")).lower() == "desc" else 1
            allowed_sort_fields = {
                "sourceStudyLastUpdated",
                "updatedAt",
                "trialId",
                "status",
                "phase",
                "title",
                "sponsor",
            }
            if sort_field not in allowed_sort_fields:
                sort_field = "sourceStudyLastUpdated"
            query = build_trial_list_query_from_filters(
                {
                    "status": request.args.get("status", "all"),
                    "phase": request.args.get("phase", "all"),
                    "source": request.args.get("source", "all"),
                    "sponsor": request.args.get("sponsor", ""),
                    "country": request.args.get("country", ""),
                    "q": request.args.get("q", ""),
                }
            )
            cache_key = None
            if include_facets:
                cache_key = f"pharma_trials_list::{request.query_string.decode('utf-8', 'ignore')}"
                cached_payload = load_cached_trial_list(cache_key)
                if cached_payload:
                    return jsonify(cached_payload), 200
    
            rows = []
            total_count = 0
            facets = {}
            if include_facets:
                trials_projection = {
                    "matchText": 0,
                    "vectorEmbedding": 0,
                    "criteriaChunkEmbeddings": 0,
                    "rawSourcePayload": 0,
                }
                facet_pipeline = [
                    {"$match": query},
                    {"$facet": {
                        "totalCount": [
                            {"$count": "count"},
                        ],
                        "status": [
                            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
                            {"$sort": {"count": -1}},
                            {"$limit": 20},
                        ],
                        "phase": [
                            {"$group": {"_id": "$phase", "count": {"$sum": 1}}},
                            {"$sort": {"count": -1}},
                            {"$limit": 20},
                        ],
                        "country": [
                            {"$unwind": {"path": "$siteCoverage", "preserveNullAndEmptyArrays": False}},
                            {"$match": {"siteCoverage.country": {"$exists": True, "$nin": [None, ""]}}},
                            {"$group": {"_id": "$siteCoverage.country", "count": {"$sum": 1}}},
                            {"$sort": {"count": -1, "_id": 1}},
                            {"$limit": 40},
                        ],
                        "sponsor": [
                            {"$group": {"_id": "$sponsor", "count": {"$sum": 1}}},
                            {"$sort": {"count": -1}},
                            {"$limit": 10},
                        ],
                        "source": [
                            {"$group": {"_id": "$source", "count": {"$sum": 1}}},
                            {"$sort": {"count": -1}},
                            {"$limit": 10},
                        ],
                    }},
                ]
                if not summary_only:
                    facet_pipeline[1]["$facet"]["trials"] = [
                        {"$sort": {sort_field: sort_direction}},
                        {"$skip": skip},
                        {"$limit": limit},
                        {"$project": trials_projection},
                    ]
                facet_rows = list(
                    db_pharma_trials_collection.aggregate(
                        facet_pipeline,
                        allowDiskUse=True,
                    )
                )
                facet_doc = facet_rows[0] if facet_rows else {}
                total_count = int((((facet_doc.get("totalCount") or [{}])[0]).get("count")) or 0)
                rows = list(facet_doc.get("trials") or []) if not summary_only else []
                facets = {
                    "status": [{"value": row.get("_id"), "count": row.get("count", 0)} for row in (facet_doc.get("status") or [])],
                    "phase": [{"value": row.get("_id"), "count": row.get("count", 0)} for row in (facet_doc.get("phase") or [])],
                    "sponsor": [] if summary_only else [{"value": row.get("_id"), "count": row.get("count", 0)} for row in (facet_doc.get("sponsor") or [])],
                    "source": [] if summary_only else [{"value": row.get("_id"), "count": row.get("count", 0)} for row in (facet_doc.get("source") or [])],
                    "country": [{"value": row.get("_id"), "count": row.get("count", 0)} for row in (facet_doc.get("country") or [])],
                }
            elif not summary_only:
                rows = list(
                    db_pharma_trials_collection.find(
                        query,
                        {
                            "matchText": 0,
                            "vectorEmbedding": 0,
                            "criteriaChunkEmbeddings": 0,
                            "rawSourcePayload": 0,
                        }
                    )
                    .sort(sort_field, sort_direction)
                    .skip(skip)
                    .limit(limit)
                )
                total_count = db_pharma_trials_collection.count_documents(query)
            else:
                total_count = db_pharma_trials_collection.count_documents(query)

            if not summary_only and rows:
                contract_write_ops = []
                for row in rows:
                    existing_contract = row.get("trialPrefilterContract") or {}
                    existing_definition = row.get("trialDefinitionCanonical") or {}
                    resolved_contract = existing_contract
                    resolved_definition = existing_definition
                    needs_contract = not (isinstance(existing_contract, dict) and existing_contract.get("contractVersion"))
                    needs_definition = not (isinstance(existing_definition, dict) and existing_definition.get("contractVersion"))

                    if needs_contract:
                        resolved_contract = resolve_trial_prefilter_contract(row)
                    if needs_definition:
                        resolved_definition = resolve_trial_definition_canonical(row)

                    if isinstance(resolved_contract, dict) and resolved_contract.get("contractVersion"):
                        row["trialPrefilterContract"] = resolved_contract
                    if isinstance(resolved_definition, dict) and resolved_definition.get("contractVersion"):
                        row["trialDefinitionCanonical"] = resolved_definition

                    set_payload = {}
                    if existing_contract != resolved_contract and isinstance(resolved_contract, dict) and resolved_contract.get("contractVersion"):
                        set_payload["trialPrefilterContract"] = resolved_contract
                    if existing_definition != resolved_definition and isinstance(resolved_definition, dict) and resolved_definition.get("contractVersion"):
                        set_payload["trialDefinitionCanonical"] = resolved_definition
                    if set_payload:
                        contract_write_ops.append(
                            UpdateOne(
                                {"_id": row.get("_id")},
                                {"$set": set_payload},
                            )
                        )
                if contract_write_ops:
                    db_pharma_trials_collection.bulk_write(contract_write_ops, ordered=False)

            response_payload = {
                "success": True,
                "count": len(rows),
                "totalCount": total_count,
                "page": page,
                "limit": limit,
                "facets": facets,
                "trials": [serialize_doc(row) for row in rows],
                "pipelineVisibility": {
                    "matchQuery": query,
                    "sort": {sort_field: sort_direction},
                    "skip": skip,
                    "limit": limit,
                    "summaryOnly": summary_only,
                },
            }
            if cache_key:
                store_cached_trial_list(cache_key, response_payload)
            return jsonify(response_payload), 200
        except Exception as e:
            logging.error(f"Error listing pharma trials: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    
    
    @blueprint.route("/pharma/source/status", methods=["GET"])
    @blueprint.route("/pharma/source_status", methods=["GET"])
    def get_pharma_source_status():
        try:
            now = time.time()
            cached_payload = source_status_cache.get("payload")
            expires_at = float(source_status_cache.get("payloadExpiresAt") or 0.0)
            if cached_payload and now < expires_at:
                return jsonify(cached_payload), 200

            trial_rollup_rows = list(
                db_pharma_trials_collection.aggregate(
                    [
                        {
                            "$facet": {
                                "sources": [
                                    {
                                        "$group": {
                                            "_id": "$source",
                                            "count": {"$sum": 1},
                                            "latestSourceDate": {"$max": "$sourceStudyLastUpdated"},
                                            "latestFetchedAt": {"$max": "$sourceFetchedAt"},
                                        }
                                    },
                                    {"$sort": {"count": -1}},
                                ],
                                "catalog": [
                                    {
                                        "$group": {
                                            "_id": None,
                                            "totalTrials": {"$sum": 1},
                                            "activeTrials": {
                                                "$sum": {
                                                    "$cond": [
                                                        {"$in": ["$status", list(ACTIVE_TRIAL_STATUSES_ALL)]},
                                                        1,
                                                        0,
                                                    ]
                                                }
                                            },
                                            "recruitingTrials": {
                                                "$sum": {
                                                    "$cond": [
                                                        {"$in": ["$status", ["RECRUITING", "Recruiting"]]},
                                                        1,
                                                        0,
                                                    ]
                                                }
                                            },
                                            "trialsEmbedded": {
                                                "$sum": {
                                                    "$cond": [
                                                        {"$gt": [{"$size": {"$ifNull": ["$vectorEmbedding", []]}}, 0]},
                                                        1,
                                                        0,
                                                    ]
                                                }
                                            },
                                            "trialsCriteriaEmbedded": {
                                                "$sum": {
                                                    "$cond": [
                                                        {"$gt": [{"$size": {"$ifNull": ["$criteriaChunkEmbeddings", []]}}, 0]},
                                                        1,
                                                        0,
                                                    ]
                                                }
                                            },
                                        }
                                    }
                                ],
                            }
                        }
                    ],
                    allowDiskUse=True
                )
            )
            trial_rollup = trial_rollup_rows[0] if trial_rollup_rows else {}
            catalog_rollup = ((trial_rollup.get("catalog") or [{}])[0]) or {}

            profile_rollup_rows = list(
                db_pharma_profiles_collection.aggregate(
                    [
                        {
                            "$group": {
                                "_id": None,
                                "patientProfiles": {"$sum": 1},
                                "patientProfilesEmbedded": {
                                    "$sum": {
                                        "$cond": [
                                            {"$gt": [{"$size": {"$ifNull": ["$profileVectorEmbedding", []]}}, 0]},
                                            1,
                                            0,
                                        ]
                                    }
                                },
                            }
                        }
                    ],
                    allowDiskUse=True,
                )
            )
            profile_rollup = profile_rollup_rows[0] if profile_rollup_rows else {}

            external_status = load_cached_external_source_status()
            vector_readiness = load_cached_vector_readiness()

            response_payload = {
                "success": True,
                "local": {
                    "totalTrials": int(catalog_rollup.get("totalTrials") or 0),
                    "activeTrials": int(catalog_rollup.get("activeTrials") or 0),
                    "recruitingTrials": int(catalog_rollup.get("recruitingTrials") or 0),
                    "patientProfiles": int(profile_rollup.get("patientProfiles") or 0),
                    "patientProfilesEmbedded": int(profile_rollup.get("patientProfilesEmbedded") or 0),
                    "trialsEmbedded": int(catalog_rollup.get("trialsEmbedded") or 0),
                    "trialsCriteriaEmbedded": int(catalog_rollup.get("trialsCriteriaEmbedded") or 0),
                    "sources": serialize_doc(trial_rollup.get("sources") or []),
                    "vectorReadiness": vector_readiness,
                },
                "external": external_status,
                "defaults": {
                    "condition": CLINICALTRIALS_DEFAULT_CONDITION,
                    "daysBack": CLINICALTRIALS_SYNC_DAYS_BACK,
                }
            }
            source_status_cache["payload"] = response_payload
            source_status_cache["payloadExpiresAt"] = now + source_status_cache_ttl_sec
            return jsonify(response_payload), 200
        except Exception as e:
            logging.error(f"Error loading pharma source status: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    
    
    @blueprint.route("/pharma/sync/clinicaltrials", methods=["POST"])
    def sync_pharma_clinicaltrials():
        try:
            payload = request.get_json(silent=True) or {}
            condition_text = str(payload.get("condition") or CLINICALTRIALS_DEFAULT_CONDITION).strip()
            days_back = parse_int_value(payload.get("daysBack"), CLINICALTRIALS_SYNC_DAYS_BACK, 1)
            max_studies = min(parse_int_value(payload.get("maxStudies"), 4000, 1), 30000)
            page_size = min(parse_int_value(payload.get("pageSize"), 1000, 1), 1000)
    
            statuses = payload.get("statuses")
            if isinstance(statuses, str):
                statuses = [item.strip() for item in statuses.split(",") if item.strip()]
            if not isinstance(statuses, list) or len(statuses) == 0:
                statuses = list(ACTIVE_TRIAL_STATUSES)
    
            sync_run_id = f"sync-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
            fetched_at = datetime.utcnow()
            source_status_cache["payload"] = None
            source_status_cache["payloadExpiresAt"] = 0.0
            source_status_cache["vectorReadiness"] = None
            source_status_cache["vectorExpiresAt"] = 0.0
            source_status_cache["external"] = None
            source_status_cache["externalExpiresAt"] = 0.0
            trial_list_cache.clear()
            control_plane_cache["payload"] = None
            control_plane_cache["expiresAt"] = 0.0
            fetch_result = fetch_clinicaltrials_studies(
                condition_text=condition_text,
                statuses=statuses,
                days_back=days_back,
                max_studies=max_studies,
                page_size=page_size,
            )
            studies = fetch_result.get("studies") or []
    
            normalized_docs = []
            for study in studies:
                normalized = normalize_ctgov_study(
                    study_doc=study,
                    condition_query=condition_text,
                    sync_meta={
                        "syncRunId": sync_run_id,
                        "fetchedAt": fetched_at,
                    }
                )
                if not normalized:
                    continue
                normalized["updatedAt"] = fetched_at
                normalized_docs.append(normalized)
            normalized_count = len(normalized_docs)

            # Batch-embed trial documents for Atlas Vector Search
            embedding_count = 0
            try:
                batch_embed_trial_documents(normalized_docs, batch_size=64)
                embedding_count = sum(1 for d in normalized_docs if d.get("vectorEmbedding"))
            except Exception as embed_err:
                logging.error(f"Batch embedding during sync failed: {embed_err}")

            existing_map = {}
            nct_ids = [doc.get("nctId") for doc in normalized_docs if doc.get("nctId")]
            if nct_ids:
                existing_cursor = db_pharma_trials_collection.find(
                    {"nctId": {"$in": nct_ids}},
                    {"nctId": 1, "trialVersion": 1, "trialVersionHistory": 1}
                )
                existing_map = {row.get("nctId"): row for row in existing_cursor if row.get("nctId")}

            write_ops = []
            for normalized in normalized_docs:
                merged_doc = merge_trial_version_history(
                    existing_map.get(normalized.get("nctId")),
                    normalized,
                )
                write_ops.append(
                    UpdateOne(
                        {"nctId": normalized["nctId"]},
                        {"$set": merged_doc},
                        upsert=True
                    )
                )
    
            upserted = 0
            modified = 0
            matched = 0
            if write_ops:
                bulk_result = db_pharma_trials_collection.bulk_write(write_ops, ordered=False)
                upserted = int(bulk_result.upserted_count or 0)
                modified = int(bulk_result.modified_count or 0)
                matched = int(bulk_result.matched_count or 0)
    
            return jsonify({
                "success": True,
                "syncRunId": sync_run_id,
                "condition": condition_text,
                "statuses": statuses,
                "daysBack": days_back,
                "fetchedAt": fetched_at.isoformat() + "Z",
                "fetch": {
                    "requestCount": fetch_result.get("requestCount", 0),
                    "startDate": fetch_result.get("startDate"),
                    "returnedStudies": len(studies),
                },
                "write": {
                    "normalizedCount": normalized_count,
                    "embeddedCount": embedding_count,
                    "upsertedCount": upserted,
                    "modifiedCount": modified,
                    "matchedCount": matched,
                    "collectionCount": db_pharma_trials_collection.estimated_document_count(),
                }
            }), 200
        except requests.exceptions.HTTPError as http_error:
            detail = http_error.response.text if http_error.response is not None else str(http_error)
            return jsonify({
                "success": False,
                "error": "ClinicalTrials.gov HTTP error",
                "detail": detail
            }), 502
        except requests.exceptions.RequestException as request_error:
            return jsonify({
                "success": False,
                "error": "ClinicalTrials.gov connection error",
                "detail": str(request_error)
            }), 502
        except Exception as e:
            logging.error(f"Error syncing ClinicalTrials.gov studies: {e}")
            return jsonify({"success": False, "error": str(e)}), 500


    @blueprint.route("/pharma/control_plane", methods=["GET"])
    def get_trial_matching_control_plane():
        try:
            now = time.time()
            cached_summary = control_plane_cache.get("payload")
            expires_at = float(control_plane_cache.get("expiresAt") or 0.0)
            if cached_summary and now < expires_at:
                summary = dict(cached_summary)
            else:
                summary = build_trial_matching_control_plane_summary()
                control_plane_cache["payload"] = dict(summary or {})
                control_plane_cache["expiresAt"] = now + control_plane_cache_ttl_sec
            return jsonify({"success": True, "summary": summary}), 200
        except Exception as e:
            logging.error(f"Error loading trial-matching control plane: {e}")
            return jsonify({"success": False, "error": str(e)}), 500


    @blueprint.route("/pharma/control_plane/evaluations/runs", methods=["GET"])
    def get_trial_matching_evaluation_runs():
        try:
            limit = min(parse_int_value(request.args.get("limit"), 10, 1), 50)
            runs = list_trial_matching_evaluation_runs(limit=limit)
            return jsonify({"success": True, "runs": runs}), 200
        except Exception as e:
            logging.error(f"Error loading trial-matching evaluation runs: {e}")
            return jsonify({"success": False, "error": str(e)}), 500


    @blueprint.route("/pharma/control_plane/improvements/runs", methods=["GET"])
    def get_trial_matching_improvement_runs():
        try:
            limit = min(parse_int_value(request.args.get("limit"), 10, 1), 50)
            runs = list_trial_matching_improvement_runs(limit=limit)
            return jsonify({"success": True, "runs": runs}), 200
        except Exception as e:
            logging.error(f"Error loading trial-matching improvement runs: {e}")
            return jsonify({"success": False, "error": str(e)}), 500


    @blueprint.route("/pharma/control_plane/evaluations/run", methods=["POST"])
    def execute_trial_matching_control_plane_evaluation():
        try:
            payload = request.get_json(silent=True) or {}
            mode = str(payload.get("mode") or "runtime_snapshot").strip().lower() or "runtime_snapshot"
            note = str(payload.get("note") or "").strip()
            result = run_trial_matching_control_plane_evaluation(mode=mode, note=note)
            control_plane_cache["payload"] = (
                dict((result or {}).get("summary") or {})
                if isinstance(result, dict)
                else None
            )
            control_plane_cache["expiresAt"] = time.time() + control_plane_cache_ttl_sec
            return jsonify({"success": True, **result}), 200
        except Exception as e:
            logging.error(f"Error running trial-matching control-plane evaluation: {e}")
            return jsonify({"success": False, "error": str(e)}), 500


    @blueprint.route("/pharma/linkage/pending", methods=["GET"])
    def get_pending_linkages():
        try:
            limit = min(parse_int_value(request.args.get("limit"), 50, 1), 200)
            rows = list_pending_leafy_synthea_linkages(limit=limit)
            return jsonify({"success": True, "count": len(rows), "rows": rows}), 200
        except Exception as e:
            logging.error(f"Error loading pending patient linkages: {e}")
            return jsonify({"success": False, "error": str(e)}), 500


    @blueprint.route("/pharma/linkage/resolve", methods=["POST"])
    def resolve_patient_linkage():
        try:
            payload = request.get_json(silent=True) or {}
            appointment_id = str(payload.get("appointmentId") or "").strip()
            patient_id = str(payload.get("patientId") or "").strip() or None
            synthea_patient_ref = str(payload.get("syntheaPatientRef") or "").strip()
            note = str(payload.get("note") or "").strip()
            unlink = parse_bool_value(payload.get("unlink"), False)

            if not appointment_id and not patient_id:
                return jsonify({"success": False, "error": "appointmentId or patientId is required"}), 400

            patient_context = {}
            report_doc = {}
            appointment_doc = {}
            if appointment_id:
                try:
                    patient_context, report_doc, appointment_doc = build_leafy_patient_context(
                        appointment_id=appointment_id,
                        patient_id=patient_id,
                    )
                except Exception:
                    patient_context = {
                        "appointmentId": appointment_id,
                        "patientId": patient_id or "",
                    }
            else:
                patient_context = {"patientId": patient_id or ""}

            link_payload = resolve_leafy_synthea_link(
                patient_context=patient_context,
                report_doc=report_doc,
                appointment_doc=appointment_doc,
                synthea_patient_ref="" if unlink else synthea_patient_ref,
                note=note,
            )
            return jsonify({"success": True, "patientLink": link_payload}), 200
        except Exception as e:
            logging.error(f"Error resolving patient linkage: {e}")
            return jsonify({"success": False, "error": str(e)}), 500


    @blueprint.route("/pharma/patient_context", methods=["GET"])
    def get_pharma_patient_context():
        try:
            appointment_id = request.args.get("appointmentId")
            patient_id = request.args.get("patientId")
            if not appointment_id:
                return jsonify({"success": False, "error": "appointmentId is required"}), 400
    
            patient_context, report_doc, appointment_doc = build_leafy_patient_context(
                appointment_id=appointment_id,
                patient_id=patient_id
            )
            analog_candidates, analog_pipeline = infer_synthea_analog_patients(patient_context, limit=6)
            patient_link = _resolve_linked_synthea_patient_ref(
                patient_context=patient_context,
                report_doc=report_doc,
                appointment_doc=appointment_doc,
                allow_analog=True
            )
            evidence_pack = build_patient_evidence_pack(
                patient_context=patient_context,
                report_doc=report_doc,
                appointment_doc=appointment_doc,
                synthea_patient_ref=patient_link.get("syntheaPatientRef"),
                linkage_payload=patient_link,
            )
            context_status = assess_patient_context_status(patient_context, patient_link, evidence_pack)
            patient_context["evidencePack"] = evidence_pack

            return jsonify({
                "success": True,
                "patientContext": patient_context,
                "evidencePack": evidence_pack,
                "patientContextStatus": context_status,
                "report": report_doc,
                "appointment": appointment_doc,
                "syntheaAnalogs": analog_candidates,
                "patientLink": patient_link,
                "pipelineVisibility": {
                    "syntheaAnalogPipeline": analog_pipeline,
                    "patientLink": patient_link,
                    "patientContextStatus": context_status,
                }
            }), 200
        except ValueError as value_error:
            return jsonify({"success": False, "error": str(value_error)}), 404
        except Exception as e:
            logging.error(f"Error loading pharma patient context: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    @blueprint.route("/pharma/patient_context/random", methods=["GET"])
    def get_random_pharma_patient_context():
        try:
            appointment_id = None
            patient_id = None
            selection_mode = "random_authoritative_screened_patient"

            # Product rule: patient evidence must begin from a real screening episode
            # that already carries an authoritative Synthea link.
            sample_rows = list(
                db_clinical_reports_collection.aggregate([
                    {"$match": {
                        "appointmentID": {"$exists": True, "$ne": ""},
                        "syntheaPatientRef": {"$exists": True, "$ne": ""},
                        "syntheaLinkStrategy": {"$in": sorted(CURRENT_AUTHORITATIVE_LINK_STRATEGIES)},
                    }},
                    {"$sample": {"size": 1}},
                    {"$project": {"appointmentID": 1, "patientID": 1, "syntheaPatientRef": 1, "syntheaLinkStrategy": 1}},
                ])
            )
            if not sample_rows:
                return jsonify({
                    "success": False,
                    "error": "No authoritative screened Synthea patient is available for random patient selection.",
                }), 404
            row = sample_rows[0] or {}
            appointment_id = str(row.get("appointmentID") or "").strip()
            patient_id = str(row.get("patientID") or "").strip() or None

            patient_context, report_doc, appointment_doc = build_leafy_patient_context(
                appointment_id=appointment_id,
                patient_id=patient_id,
            )
            analog_candidates, analog_pipeline = infer_synthea_analog_patients(patient_context, limit=6)
            patient_link = _resolve_linked_synthea_patient_ref(
                patient_context=patient_context,
                report_doc=report_doc,
                appointment_doc=appointment_doc,
                allow_analog=True,
            )
            evidence_pack = build_patient_evidence_pack(
                patient_context=patient_context,
                report_doc=report_doc,
                appointment_doc=appointment_doc,
                synthea_patient_ref=patient_link.get("syntheaPatientRef"),
                linkage_payload=patient_link,
            )
            context_status = assess_patient_context_status(patient_context, patient_link, evidence_pack)
            patient_context["evidencePack"] = evidence_pack

            return jsonify({
                "success": True,
                "selectionMode": selection_mode,
                "appointmentId": appointment_id,
                "patientId": patient_id,
                "patientContext": patient_context,
                "evidencePack": evidence_pack,
                "patientContextStatus": context_status,
                "report": report_doc,
                "appointment": appointment_doc,
                "syntheaAnalogs": analog_candidates,
                "patientLink": patient_link,
                "pipelineVisibility": {
                    "syntheaAnalogPipeline": analog_pipeline,
                    "patientLink": patient_link,
                    "patientContextStatus": context_status,
                }
            }), 200
        except ValueError as value_error:
            return jsonify({"success": False, "error": str(value_error)}), 404
        except Exception as e:
            logging.error(f"Error loading random pharma patient context: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    
    
    @blueprint.route("/pharma/linkage/reconcile", methods=["POST"])
    def reconcile_patient_linkage():
        try:
            payload = request.get_json(silent=True) or {}
            limit = min(parse_int_value(payload.get("limit"), 5000, 1), 30000)
            reports_cursor = db_clinical_reports_collection.find(
                {},
                {
                    "_id": 1,
                    "appointmentID": 1,
                    "patientID": 1,
                    "patient-id": 1,
                    "user": 1,
                    "series.imageMetadata.PatientAge": 1,
                    "series.imageMetadata.biRads": 1,
                    "conclusions": 1,
                    "clinicalNotes": 1,
                }
            ).limit(limit)

            total = 0
            linked = 0
            ambiguous = 0
            candidate_only = 0
            legacy_heuristic = 0
            unlinked = 0
            errors = 0
            sample = []

            for report in reports_cursor:
                total += 1
                appointment_id = str(report.get("appointmentID") or "").strip()
                patient_id = str(report.get("patientID") or report.get("patient-id") or "").strip() or None
                context = None
                appointment_doc = None
                try:
                    context, _, appointment_doc = build_leafy_patient_context(
                        appointment_id=appointment_id,
                        patient_id=patient_id
                    )
                except Exception:
                    context = {
                        "appointmentId": appointment_id,
                        "patientId": patient_id or "",
                        "patientName": report.get("user") or "",
                        "clinicalNotes": report.get("clinicalNotes") or "",
                        "conclusions": report.get("conclusions") or "",
                        "keywords": [],
                    }

                try:
                    link = _resolve_linked_synthea_patient_ref(
                        patient_context=context,
                        report_doc=report,
                        appointment_doc=appointment_doc,
                        allow_analog=True,
                    )
                except Exception:
                    errors += 1
                    continue

                linkage_status = str(link.get("linkageStatus") or "unlinked").lower()
                if linkage_status == "linked":
                    linked += 1
                elif linkage_status == "ambiguous":
                    ambiguous += 1
                elif linkage_status == "candidate_only":
                    candidate_only += 1
                elif linkage_status == "legacy_heuristic_link":
                    legacy_heuristic += 1
                else:
                    unlinked += 1

                if len(sample) < 12:
                    sample.append(
                        {
                            "appointmentId": appointment_id,
                            "patientId": patient_id,
                            "linkageStatus": linkage_status,
                            "syntheaPatientRef": link.get("syntheaPatientRef"),
                            "suggestedSyntheaPatientRef": link.get("suggestedSyntheaPatientRef"),
                            "linkageConfidence": link.get("linkageConfidence"),
                            "linkStrategy": link.get("linkStrategy"),
                        }
                    )

            denominator = max(total, 1)
            kpis = {
                "linkedPct": round((linked / denominator) * 100.0, 1),
                "ambiguousPct": round((ambiguous / denominator) * 100.0, 1),
                "candidateOnlyPct": round((candidate_only / denominator) * 100.0, 1),
                "legacyHeuristicPct": round((legacy_heuristic / denominator) * 100.0, 1),
                "unlinkedPct": round((unlinked / denominator) * 100.0, 1),
            }

            return jsonify(
                {
                    "success": True,
                    "totalProcessed": total,
                    "linked": linked,
                    "ambiguous": ambiguous,
                    "candidateOnly": candidate_only,
                    "legacyHeuristic": legacy_heuristic,
                    "unlinked": unlinked,
                    "errors": errors,
                    "kpis": kpis,
                    "sample": sample,
                }
            ), 200
        except Exception as e:
            logging.error(f"Error reconciling patient linkage: {e}")
            return jsonify({"success": False, "error": str(e)}), 500


    @blueprint.route("/pharma/synthea/patient_emr", methods=["GET"])
    def get_synthea_patient_emr():
        try:
            patient_ref = str(request.args.get("patientRef") or "").strip()
            if not patient_ref:
                return jsonify({"success": False, "error": "patientRef is required"}), 400
    
            limit = min(parse_int_value(request.args.get("limit"), 120, 1), 500)
            result, trace = _invoke_agent_tool(
                "synthea_patient_emr",
                {"patientRef": patient_ref, "limit": limit}
            )
            if not trace.get("ok"):
                return jsonify({"success": False, "error": trace.get("error"), "trace": trace}), 500
    
            return jsonify(
                {
                    "success": True,
                    "patientRef": patient_ref,
                    "summary": (result or {}).get("summary") or {},
                    "resources": (result or {}).get("resources") or [],
                    "timeline": (result or {}).get("timeline") or [],
                    "domains": (result or {}).get("domains") or {},
                    "patient": (result or {}).get("patient") or {},
                    "pipelineVisibility": {
                        "toolTrace": [trace]
                    }
                }
            ), 200
        except Exception as e:
            logging.error(f"Error loading Synthea EMR bundle: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    
    
    @blueprint.route("/pharma/synthea/dataset_summary", methods=["GET"])
    def get_synthea_dataset_summary():
        try:
            top_n = min(parse_int_value(request.args.get("topN"), 12, 1), 40)
            total_docs = db_synthea_resources_collection.estimated_document_count()
    
            resource_type_rows = list(
                db_synthea_resources_collection.aggregate(
                    [
                        {"$group": {"_id": "$resource.resourceType", "count": {"$sum": 1}}},
                        {"$sort": {"count": -1}},
                        {"$limit": top_n}
                    ],
                    allowDiskUse=True
                )
            )
            total_resource_types = len(
                db_synthea_resources_collection.distinct("resource.resourceType")
            )
            patient_count = db_synthea_resources_collection.count_documents({"resource.resourceType": "Patient"})
    
            top_patient_rows = list(
                db_synthea_resources_collection.aggregate(
                    [
                        {"$match": {"resource.resourceType": {"$ne": "Patient"}}},
                        {
                            "$project": {
                                "refs": [
                                    "$resource.subject.reference",
                                    "$resource.patient.reference",
                                    "$resource.beneficiary.reference"
                                ]
                            }
                        },
                        {"$unwind": "$refs"},
                        {"$match": {"refs": {"$type": "string", "$regex": "^Patient/"}}},
                        {"$group": {"_id": "$refs", "count": {"$sum": 1}}},
                        {"$sort": {"count": -1}},
                        {"$limit": min(top_n, 15)}
                    ],
                    allowDiskUse=True
                )
            )
            readiness_summary = audit_trial_matching_dataset_readiness()

            return jsonify(
                {
                    "success": True,
                    "summary": {
                        "database": SYNTHEA_FHIR_DB,
                        "collection": SYNTHEA_RESOURCES_COLLECTION,
                        "totalResources": total_docs,
                        "patientResources": patient_count,
                        "resourceTypes": total_resource_types,
                        "datasetReadiness": readiness_summary,
                    },
                    "resourceTypeCounts": serialize_doc(resource_type_rows),
                    "topPatientRefsByEvents": serialize_doc(top_patient_rows),
                }
            ), 200
        except Exception as e:
            logging.error(f"Error generating Synthea dataset summary: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    
    
    @blueprint.route("/pharma/trials/match", methods=["POST"])
    def match_pharma_trials():
        try:
            payload = request.get_json(silent=True) or {}
            appointment_id = str(payload.get("appointmentId") or "").strip()
            patient_id = str(payload.get("patientId") or "").strip() or None
            top_k = min(parse_int_value(payload.get("topK"), 6, 1), 20)
            rerank_enabled = parse_bool_value(payload.get("rerankEnabled"), True)
            user_query = str(payload.get("query") or "").strip()
            trial_filters = payload.get("trialFilters") if isinstance(payload.get("trialFilters"), dict) else {}

            if not appointment_id:
                return jsonify({"success": False, "error": "appointmentId is required"}), 400

            result = run_patient_trial_match_pipeline(
                question=user_query,
                appointment_id=appointment_id,
                patient_id=patient_id,
                top_k=top_k,
                rerank_enabled=rerank_enabled,
                trial_filters=trial_filters,
                build_leafy_patient_context_fn=build_leafy_patient_context,
                infer_synthea_analog_patients_fn=infer_synthea_analog_patients,
                resolve_linked_synthea_patient_ref_fn=_resolve_linked_synthea_patient_ref,
                build_patient_evidence_pack_fn=build_patient_evidence_pack,
                build_trial_query_for_patient_context_fn=build_trial_query_for_patient_context,
                evaluate_patient_context_against_trial_prefilter_fn=evaluate_patient_context_against_trial_prefilter,
                resolve_trial_prefilter_contract_fn=resolve_trial_prefilter_contract,
                resolve_trial_definition_canonical_fn=resolve_trial_definition_canonical,
                evaluate_trial_structured_match_fn=evaluate_trial_structured_match,
                build_trial_match_text_fn=build_trial_match_text,
                estimate_trial_coverage_with_synthea_fn=estimate_trial_coverage_with_synthea,
                db_pharma_trials_collection=db_pharma_trials_collection,
                active_trial_statuses_all=ACTIVE_TRIAL_STATUSES_ALL,
                voyage_api_key=VOYAGE_API_KEY,
                voyage_embed_text_fn=voyage_embed_text,
                voyage_embedding_model=VOYAGE_EMBEDDING_MODEL,
                voyage_rerank_results_fn=voyage_rerank_results,
                voyage_rerank_model=VOYAGE_RERANK_MODEL,
                voyage_pharma_vector_index_name=VOYAGE_PHARMA_VECTOR_INDEX_NAME,
                serialize_doc_fn=serialize_doc,
                safe_float_fn=safe_float,
                normalize_pharma_text_fn=normalize_pharma_text,
                logger=logging,
            )
            result["runtimeMode"] = COMPAT_RUNTIME_MODE
            return jsonify(result), 200
        except ValueError as value_error:
            return jsonify({"success": False, "error": str(value_error)}), 404
        except Exception as e:
            logging.error(f"Error matching pharma trials: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    
    @blueprint.route("/pharma/trials/llm_adjudicate", methods=["POST"])
    def adjudicate_trial_rules_with_llm():
        try:
            payload = request.get_json(silent=True) or {}
            async_mode = parse_bool_value(payload.get("asyncMode"), False)

            def run_adjudication_core(core_payload, progress_callback=None):
                trial_id = str(core_payload.get("trialId") or core_payload.get("nctId") or "").strip()
                appointment_id = str(core_payload.get("appointmentId") or "").strip()
                patient_id = str(core_payload.get("patientId") or "").strip() or None
                semantic_query = str(core_payload.get("query") or "").strip()
                max_checks = min(parse_int_value(core_payload.get("maxChecks"), 10, 1), 10)
                parallelism = min(parse_int_value(core_payload.get("parallelism"), 4, 1), 10)
                force_refresh = parse_bool_value(core_payload.get("forceRefresh"), False)
                prompt_version = str(core_payload.get("promptVersion") or "pharma-llm-adjudication-v1").strip()
                model_name = str(core_payload.get("model") or BEDROCK_DEFAULT_MODEL).strip()

                if not trial_id:
                    raise ValueError("trialId is required")
                if not appointment_id:
                    raise ValueError("appointmentId is required")

                trial_doc = db_pharma_trials_collection.find_one({"trialId": trial_id})
                if not trial_doc:
                    trial_doc = db_pharma_trials_collection.find_one({"nctId": trial_id})
                if not trial_doc:
                    raise LookupError("Trial not found")

                patient_context, report_doc, appointment_doc = build_leafy_patient_context(
                    appointment_id=appointment_id,
                    patient_id=patient_id
                )
                patient_link = _resolve_linked_synthea_patient_ref(
                    patient_context=patient_context,
                    report_doc=report_doc,
                    appointment_doc=appointment_doc,
                    allow_analog=True,
                )
                evidence_pack = None
                evidence_pack = build_patient_evidence_pack(
                    patient_context=patient_context,
                    report_doc=report_doc,
                    appointment_doc=appointment_doc,
                    synthea_patient_ref=patient_link.get("syntheaPatientRef"),
                    linkage_payload=patient_link,
                )
                patient_context_status = assess_patient_context_status(patient_context, patient_link, evidence_pack)
                patient_context["evidencePack"] = evidence_pack
                if not patient_context_status.get("canRunDeterministicMatch"):
                    return {
                        "success": True,
                        "trialId": trial_id,
                        "patientContextStatus": patient_context_status,
                        "updatedMatch": None,
                        "adjudication": {
                            "processedChecks": 0,
                            "requestedChecks": 0,
                            "parallelism": parallelism,
                            "message": "Patient context is not ready for LLM adjudication.",
                        },
                    }

                query_text = semantic_query or patient_context.get("semanticQuery") or "breast cancer trial matching"
                base_eval = evaluate_trial_structured_match(
                    trial_doc,
                    patient_context,
                    semantic_query=query_text,
                )
                queue = list(base_eval.get("llmAdjudicationQueue") or [])[:max_checks]
                trial_version = (base_eval.get("trialVersion") or {}).get("versionId") or trial_doc.get("sourcePayloadHash") or trial_doc.get("trialId")
                patient_version = base_eval.get("patientVersion") or ""
                cache_key_text = f"{trial_version}|{patient_version}|{prompt_version}|{max_checks}|{model_name}"
                cache_key = hashlib.sha256(cache_key_text.encode("utf-8")).hexdigest()[:28]
                cache_root = trial_doc.get("llmAdjudicationCache")
                cache_entry = (cache_root or {}).get(cache_key) if isinstance(cache_root, dict) else None
                used_cache = bool(cache_entry and not force_refresh)

                adjudication_map = {}
                execution_rows = []
                if progress_callback:
                    progress_callback(
                        {
                            "requestedChecks": len(queue),
                            "processedChecks": 0,
                            "parallelism": parallelism,
                            "maxChecks": max_checks,
                            "usedCache": used_cache,
                            "cacheKey": cache_key,
                            "promptVersion": prompt_version,
                            "modelVersion": model_name,
                            "executionRows": [],
                        }
                    )
                if used_cache:
                    cached_adjudications = cache_entry.get("adjudications")
                    if isinstance(cached_adjudications, dict):
                        adjudication_map = cached_adjudications
                    execution_rows = list(cache_entry.get("executionRows") or [])[:max_checks]
                    if progress_callback:
                        progress_callback(
                            {
                                "processedChecks": len(adjudication_map),
                                "executionRows": execution_rows[:max_checks],
                                "usedCache": True,
                            }
                        )
                elif queue:
                    has_llm = True
                    bedrock_runtime = None
                    try:
                        from bedrock.client import BedrockClient
                        bedrock_runtime = BedrockClient()._get_bedrock_client(runtime=True)
                    except Exception as import_error:
                        logging.error(f"Failed to initialize Bedrock client for pharma adjudication: {import_error}")
                        has_llm = False

                    def run_single_rule(rule_eval):
                        started = time.time()
                        llm_context = build_rule_llm_context(rule_eval, evidence_pack)
                        if not has_llm or bedrock_runtime is None:
                            output = normalize_llm_rule_output(
                                raw_output={
                                    "status": "UNKNOWN",
                                    "missingData": (rule_eval.get("missingData") or []) + ["bedrock_client"],
                                    "reasoning": "LLM adjudication unavailable because Bedrock is not configured.",
                                },
                                rule_eval=rule_eval,
                                llm_context=llm_context,
                                model_name=model_name,
                                prompt_version=prompt_version,
                            )
                            return output, {
                                "ruleId": rule_eval.get("ruleId"),
                                "status": output.get("status"),
                                "durationMs": round((time.time() - started) * 1000, 1),
                                "source": "fallback_no_llm",
                            }

                        system_prompt = (
                            "You adjudicate ONE clinical-trial eligibility rule using ONLY provided evidence. "
                            "Return strict JSON with keys: status, evidencePointers, missingData, reasoning, patientValue. "
                            "Allowed status: PASS, FAIL, UNKNOWN. "
                            "If you cannot cite pointers from availablePointers, return UNKNOWN."
                        )
                        user_payload = {
                            "instruction": "Adjudicate this rule.",
                            "rule": llm_context.get("rule") or {},
                            "facts": llm_context.get("facts") or {},
                            "snippets": llm_context.get("snippets") or [],
                            "availablePointers": llm_context.get("availablePointers") or [],
                        }
                        raw_response = {}
                        try:
                            bedrock_request = {
                                "anthropic_version": "bedrock-2023-05-31",
                                "max_tokens": 360,
                                "temperature": 0,
                                "system": system_prompt,
                                "messages": [
                                    {"role": "user", "content": json.dumps(user_payload)},
                                ],
                            }
                            llm_response = bedrock_runtime.invoke_model(
                                modelId=model_name,
                                body=json.dumps(bedrock_request),
                                contentType="application/json",
                                accept="application/json",
                            )
                            response_body = json.loads(llm_response["body"].read())
                            content = str(response_body["content"][0]["text"] or "").strip()
                            raw_response = parse_json_object(content)
                        except Exception as llm_error:
                            raw_response = {
                                "status": "UNKNOWN",
                                "missingData": (rule_eval.get("missingData") or []) + ["llm_error"],
                                "reasoning": f"LLM adjudication failed: {llm_error}",
                            }

                        output = normalize_llm_rule_output(
                            raw_output=raw_response,
                            rule_eval=rule_eval,
                            llm_context=llm_context,
                            model_name=model_name,
                            prompt_version=prompt_version,
                        )
                        return output, {
                            "ruleId": rule_eval.get("ruleId"),
                            "status": output.get("status"),
                            "durationMs": round((time.time() - started) * 1000, 1),
                            "source": "llm",
                        }

                    started_all = time.time()
                    max_workers = max(1, min(parallelism, len(queue)))
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        future_map = {
                            executor.submit(run_single_rule, rule_eval): rule_eval.get("ruleId")
                            for rule_eval in queue
                        }
                        for future in as_completed(future_map):
                            result_payload, execution_payload = future.result()
                            rule_id = str(result_payload.get("ruleId") or "").strip()
                            if rule_id:
                                adjudication_map[rule_id] = result_payload
                            execution_rows.append(execution_payload)
                            if progress_callback:
                                progress_callback(
                                    {
                                        "processedChecks": len(adjudication_map),
                                        "executionRows": execution_rows[:max_checks],
                                    }
                                )
                    total_duration_ms = round((time.time() - started_all) * 1000, 1)

                    cache_payload = {
                        "cacheKey": cache_key,
                        "trialVersion": trial_version,
                        "patientVersion": patient_version,
                        "promptVersion": prompt_version,
                        "modelVersion": model_name,
                        "adjudications": adjudication_map,
                        "executionRows": execution_rows[:max_checks],
                        "createdAt": datetime.utcnow().isoformat() + "Z",
                        "durationMs": total_duration_ms,
                    }
                    db_pharma_trials_collection.update_one(
                        {"_id": trial_doc.get("_id")},
                        {"$set": {f"llmAdjudicationCache.{cache_key}": cache_payload}},
                        upsert=False,
                    )

                final_eval = evaluate_trial_structured_match(
                    trial_doc,
                    patient_context,
                    semantic_query=query_text,
                    llm_adjudications=adjudication_map,
                )
                updated_match = {
                    "trialId": trial_doc.get("trialId"),
                    "nctId": trial_doc.get("nctId"),
                    "title": trial_doc.get("title"),
                    "status": trial_doc.get("status"),
                    "phase": trial_doc.get("phase"),
                    "sponsor": trial_doc.get("sponsor"),
                    "source": trial_doc.get("source"),
                    "sourceUrl": trial_doc.get("sourceUrl"),
                    "trialVersion": final_eval.get("trialVersion") or {},
                    "sourcePayloadHash": final_eval.get("sourcePayloadHash"),
                    "patientVersion": final_eval.get("patientVersion"),
                    "isEligibleStructured": final_eval.get("isEligibleStructured"),
                    "overallAssessment": final_eval.get("overallAssessment"),
                    "structuredScore": final_eval.get("structuredScore"),
                    "eligibilityScore": final_eval.get("eligibilityScore"),
                    "evidenceCompletenessScore": final_eval.get("evidenceCompletenessScore"),
                    "matchReasons": final_eval.get("matchReasons") or [],
                    "missingCriteria": final_eval.get("missingCriteria") or [],
                    "decisionSummary": final_eval.get("decisionSummary") or {},
                    "criterionBreakdown": final_eval.get("criterionBreakdown") or [],
                    "ruleEvaluations": final_eval.get("ruleEvaluations") or [],
                    "llmAdjudicationQueue": final_eval.get("llmAdjudicationQueue") or [],
                    "riskEvidence": final_eval.get("riskEvidence") or {},
                    "coverageMeter": final_eval.get("coverageMeter") or {},
                    "criteriaLevelMatches": final_eval.get("criteriaLevelMatches") or [],
                    "parserWarnings": final_eval.get("parserWarnings") or [],
                    "validatorWarnings": final_eval.get("validatorWarnings") or [],
                }
                return {
                    "success": True,
                    "trialId": trial_doc.get("trialId"),
                    "patientContextStatus": patient_context_status,
                    "updatedMatch": serialize_doc(updated_match),
                    "adjudication": {
                        "requestedChecks": len(queue),
                        "processedChecks": len(adjudication_map),
                        "parallelism": parallelism,
                        "maxChecks": max_checks,
                        "usedCache": used_cache,
                        "cacheKey": cache_key,
                        "promptVersion": prompt_version,
                        "modelVersion": model_name,
                        "executionRows": execution_rows[:max_checks],
                    },
                }

            if async_mode:
                prune_llm_adjudication_jobs()
                base_trial = str(payload.get("trialId") or payload.get("nctId") or "").strip() or "unknown"
                base_appointment = str(payload.get("appointmentId") or "").strip() or "unknown"
                job_seed = f"{base_trial}|{base_appointment}|{datetime.utcnow().isoformat()}|{time.time()}"
                job_id = hashlib.sha256(job_seed.encode("utf-8")).hexdigest()[:20]
                created_at = datetime.utcnow().isoformat() + "Z"
                with llm_adjudication_jobs_lock:
                    llm_adjudication_jobs[job_id] = {
                        "status": "queued",
                        "createdAt": created_at,
                        "updatedAt": created_at,
                        "createdAtEpoch": time.time(),
                        "updatedAtEpoch": time.time(),
                        "progress": {
                            "requestedChecks": 0,
                            "processedChecks": 0,
                            "parallelism": min(parse_int_value(payload.get("parallelism"), 4, 1), 10),
                            "maxChecks": min(parse_int_value(payload.get("maxChecks"), 10, 1), 10),
                            "executionRows": [],
                        },
                        "result": None,
                        "error": None,
                    }

                def worker():
                    started_at = datetime.utcnow().isoformat() + "Z"
                    with llm_adjudication_jobs_lock:
                        row = llm_adjudication_jobs.get(job_id) or {}
                        row["status"] = "running"
                        row["startedAt"] = started_at
                        row["updatedAt"] = started_at
                        row["updatedAtEpoch"] = time.time()
                        llm_adjudication_jobs[job_id] = row

                    def on_progress(progress_patch):
                        with llm_adjudication_jobs_lock:
                            row = llm_adjudication_jobs.get(job_id) or {}
                            progress_row = row.get("progress") if isinstance(row.get("progress"), dict) else {}
                            progress_row.update(progress_patch or {})
                            row["progress"] = progress_row
                            row["updatedAt"] = datetime.utcnow().isoformat() + "Z"
                            row["updatedAtEpoch"] = time.time()
                            llm_adjudication_jobs[job_id] = row

                    try:
                        result_payload = run_adjudication_core(payload, progress_callback=on_progress)
                        with llm_adjudication_jobs_lock:
                            row = llm_adjudication_jobs.get(job_id) or {}
                            row["status"] = "completed"
                            row["result"] = result_payload
                            row["error"] = None
                            row["updatedAt"] = datetime.utcnow().isoformat() + "Z"
                            row["updatedAtEpoch"] = time.time()
                            llm_adjudication_jobs[job_id] = row
                    except Exception as worker_error:
                        with llm_adjudication_jobs_lock:
                            row = llm_adjudication_jobs.get(job_id) or {}
                            row["status"] = "failed"
                            row["error"] = str(worker_error)
                            row["updatedAt"] = datetime.utcnow().isoformat() + "Z"
                            row["updatedAtEpoch"] = time.time()
                            llm_adjudication_jobs[job_id] = row

                Thread(target=worker, daemon=True).start()
                with llm_adjudication_jobs_lock:
                    job_payload = build_llm_adjudication_job_payload(job_id, llm_adjudication_jobs.get(job_id) or {})
                return jsonify({"success": True, "job": job_payload}), 202

            result_payload = run_adjudication_core(payload)
            return jsonify(result_payload), 200
        except ValueError as value_error:
            return jsonify({"success": False, "error": str(value_error)}), 400
        except LookupError as lookup_error:
            return jsonify({"success": False, "error": str(lookup_error)}), 404
        except Exception as e:
            logging.error(f"Error running pharma LLM adjudication: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    @blueprint.route("/pharma/trials/llm_adjudicate/status", methods=["GET"])
    def get_llm_adjudication_status():
        try:
            prune_llm_adjudication_jobs()
            job_id = str(request.args.get("jobId") or "").strip()
            if not job_id:
                return jsonify({"success": False, "error": "jobId is required"}), 400
            with llm_adjudication_jobs_lock:
                job_row = llm_adjudication_jobs.get(job_id)
            if not isinstance(job_row, dict):
                return jsonify({"success": False, "error": "jobId not found or expired"}), 404
            response_payload = {
                "success": True,
                "job": build_llm_adjudication_job_payload(job_id, job_row),
                "result": job_row.get("result") if str(job_row.get("status")) == "completed" else None,
            }
            return jsonify(response_payload), 200
        except Exception as e:
            logging.error(f"Error reading pharma LLM adjudication status: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    
    @blueprint.route("/pharma/trials/candidate_patients", methods=["POST"])
    def match_trial_to_patients():
        try:
            payload = request.get_json(silent=True) or {}
            trial_id = str(payload.get("trialId") or payload.get("nctId") or "").strip()
            top_k = min(parse_int_value(payload.get("topK"), 10, 1), 40)
            if not trial_id:
                return jsonify({"success": False, "error": "trialId is required"}), 400
    
            trial_doc = db_pharma_trials_collection.find_one({"trialId": trial_id})
            if not trial_doc:
                trial_doc = db_pharma_trials_collection.find_one({"nctId": trial_id})
            if not trial_doc:
                return jsonify({"success": False, "error": "Trial not found"}), 404

            trial_doc_serialized = serialize_doc(trial_doc)
            trial_prefilter_contract = resolve_trial_prefilter_contract(trial_doc)
            trial_definition_canonical = resolve_trial_definition_canonical(trial_doc)
            eligibility = trial_doc.get("eligibility") or {}
            trial_keywords = [normalize_pharma_text(item) for item in (eligibility.get("includeKeywordsAny") or []) if normalize_pharma_text(item)]
            profile_query = build_profile_query_for_trial(trial_doc)
            trial_semantic_query_text = build_trial_match_text(trial_doc)
            canonical_search_contract_runtime = None
            canonical_search_contract = None
            canonical_search_contract_error = None
            search_contract_source = "legacy_trial_prefilter_contract"
            if STRICT_CANONICAL_MODE:
                canonical_search_contract_runtime, canonical_search_contract_error = canonical_compat.load_canonical_trial_search_contract(
                    trial_id=trial_id
                )
                if isinstance(canonical_search_contract_runtime, dict):
                    canonical_search_contract = canonical_search_contract_runtime.get("searchContract") or {}
                    candidate_profile_query = canonical_search_contract.get("mongoProfileQuery")
                    if isinstance(candidate_profile_query, dict) and candidate_profile_query:
                        profile_query = candidate_profile_query
                    keyword_terms = ((canonical_search_contract.get("signals") or {}).get("keywordTerms")) or []
                    normalized_keyword_terms = [
                        normalize_pharma_text(item)
                        for item in keyword_terms
                        if normalize_pharma_text(item)
                    ]
                    if normalized_keyword_terms:
                        trial_keywords = normalized_keyword_terms
                    semantic_query_override = str(((canonical_search_contract.get("semantic") or {}).get("queryText")) or "").strip()
                    if semantic_query_override:
                        trial_semantic_query_text = semantic_query_override
                    search_contract_source = "compiled_canonical_search_contract"
            keyword_count = len(trial_keywords)
            synthea_total_candidates = db_pharma_profiles_collection.count_documents(profile_query)

            candidate_search_mode = "structured_trial_prefilter"
            patient_profiles_embedded_total = db_pharma_profiles_collection.count_documents({"profileVectorEmbedding": {"$exists": True}})
            candidate_pool_limit = max(top_k * 4, 40)
            vector_enabled = bool(
                VOYAGE_API_KEY
                and voyage_embed_text
                and VOYAGE_PHARMA_PATIENT_VECTOR_INDEX_NAME
                and patient_profiles_embedded_total > 0
            )
            vector_used = False
            vector_fallback_reason = None if vector_enabled else (
                "Patient profile embeddings are unavailable; using structured deterministic retrieval."
                if patient_profiles_embedded_total == 0
                else "Voyage key or patient vector index is not configured."
            )
            vector_returned = 0
            retrieval_blend_formula = "0.80 keyword + 0.20 activity after deterministic trial prefilter"
            synthea_pipeline = []
            synthea_pipeline_visibility = []
            vector_filter_used_in_search = False
            vector_post_filter_applied = False

            vector_scoring_stages = [
                {
                    "$addFields": {
                        "vectorSearchScore": {"$meta": "vectorSearchScore"},
                        "sharedKeywords": {
                            "$setIntersection": [
                                {"$ifNull": ["$featureTerms", []]},
                                trial_keywords
                            ]
                        },
                        "resourceEventCountSafe": {"$ifNull": ["$resourceEventCount", 0]},
                    }
                },
                {
                    "$addFields": {
                        "sharedKeywordCount": {"$size": "$sharedKeywords"},
                        "keywordScore": {
                            "$cond": [
                                {"$gt": [keyword_count, 0]},
                                {"$divide": [{"$size": "$sharedKeywords"}, keyword_count]},
                                0.0
                            ]
                        },
                        "activityScore": {
                            "$min": [
                                1,
                                {"$divide": [{"$ifNull": ["$resourceEventCountSafe", 0]}, 40]}
                            ]
                        },
                    }
                },
                {
                    "$addFields": {
                        "retrievalScore": {
                            "$round": [
                                {
                                    "$add": [
                                        {"$multiply": ["$vectorSearchScore", 0.75]},
                                        {"$multiply": ["$keywordScore", 0.15]},
                                        {"$multiply": ["$activityScore", 0.10]}
                                    ]
                                },
                                4
                            ]
                        },
                        "trialFitScore": {
                            "$round": [
                                {
                                    "$add": [
                                        {"$multiply": ["$vectorSearchScore", 0.75]},
                                        {"$multiply": ["$keywordScore", 0.15]},
                                        {"$multiply": ["$activityScore", 0.10]}
                                    ]
                                },
                                4
                            ]
                        }
                    }
                },
                {"$sort": {"retrievalScore": -1, "vectorSearchScore": -1, "sharedKeywordCount": -1}},
                {"$limit": candidate_pool_limit},
                {
                    "$project": {
                        "_id": 0,
                        "source": {"$literal": "synthea"},
                        "patientRef": 1,
                        "name": 1,
                        "age": 1,
                        "gender": 1,
                        "sharedKeywords": 1,
                        "sharedKeywordCount": 1,
                        "resourceTypeCounts": 1,
                        "resourceEventCount": "$resourceEventCountSafe",
                        "keywordScore": 1,
                        "activityScore": 1,
                        "vectorSearchScore": 1,
                        "retrievalScore": 1,
                        "trialFitScore": 1,
                    }
                }
            ]

            if vector_enabled:
                vector_num_candidates = max(top_k * 12, 120)
                vector_limit = max(top_k * 4, 40)
                vector_filter = {}
                query_embedding = None
                if isinstance(profile_query, dict):
                    if "age" in profile_query:
                        vector_filter["age"] = profile_query.get("age")
                    if "gender" in profile_query:
                        vector_filter["gender"] = profile_query.get("gender")
                try:
                    query_embedding = voyage_embed_text(
                        trial_semantic_query_text,
                        embedding_model=VOYAGE_EMBEDDING_MODEL,
                    )
                    vector_stage = {
                        "$vectorSearch": {
                            "index": VOYAGE_PHARMA_PATIENT_VECTOR_INDEX_NAME,
                            "path": "profileVectorEmbedding",
                            "queryVector": query_embedding,
                            "numCandidates": vector_num_candidates,
                            "limit": vector_limit,
                        }
                    }
                    if vector_filter:
                        vector_stage["$vectorSearch"]["filter"] = vector_filter
                        vector_filter_used_in_search = True

                    synthea_pipeline = [vector_stage] + vector_scoring_stages
                    synthea_pipeline_visibility = [
                        {
                            "$vectorSearch": {
                                "index": VOYAGE_PHARMA_PATIENT_VECTOR_INDEX_NAME,
                                "path": "profileVectorEmbedding",
                                "numCandidates": vector_num_candidates,
                                "limit": vector_limit,
                                "filter": vector_filter or None,
                            }
                        }
                    ] + synthea_pipeline[1:]
                    synthea_rows = list(db_pharma_profiles_collection.aggregate(synthea_pipeline, allowDiskUse=True))
                    vector_returned = len(synthea_rows)
                    if vector_returned > 0:
                        vector_used = True
                        candidate_search_mode = "vector_plus_trial_prefilter"
                        retrieval_blend_formula = "0.75 vector + 0.15 keyword + 0.10 activity after deterministic trial prefilter"
                    else:
                        synthea_pipeline = []
                        vector_fallback_reason = "Vector search returned zero patient profiles; fell back to structured deterministic retrieval."
                except Exception as vector_error:
                    vector_error_text = str(vector_error)
                    needs_filter_mapping = bool(vector_filter) and ("needs to be indexed as filter" in vector_error_text.lower())
                    if needs_filter_mapping:
                        try:
                            if query_embedding is None:
                                query_embedding = voyage_embed_text(
                                    trial_semantic_query_text,
                                    embedding_model=VOYAGE_EMBEDDING_MODEL,
                                )
                            vector_stage = {
                                "$vectorSearch": {
                                    "index": VOYAGE_PHARMA_PATIENT_VECTOR_INDEX_NAME,
                                    "path": "profileVectorEmbedding",
                                    "queryVector": query_embedding,
                                    "numCandidates": vector_num_candidates,
                                    "limit": vector_limit,
                                }
                            }
                            synthea_pipeline = [vector_stage]
                            if profile_query:
                                synthea_pipeline.append({"$match": profile_query})
                            synthea_pipeline.extend(vector_scoring_stages)
                            synthea_pipeline_visibility = [
                                {
                                    "$vectorSearch": {
                                        "index": VOYAGE_PHARMA_PATIENT_VECTOR_INDEX_NAME,
                                        "path": "profileVectorEmbedding",
                                        "numCandidates": vector_num_candidates,
                                        "limit": vector_limit,
                                        "filter": None,
                                    }
                                }
                            ]
                            if profile_query:
                                synthea_pipeline_visibility.append({"$match": profile_query})
                            synthea_pipeline_visibility.extend(vector_scoring_stages)
                            synthea_rows = list(db_pharma_profiles_collection.aggregate(synthea_pipeline, allowDiskUse=True))
                            vector_used = True
                            vector_returned = len(synthea_rows)
                            vector_post_filter_applied = bool(profile_query)
                            candidate_search_mode = "vector_plus_post_prefilter"
                            retrieval_blend_formula = "0.75 vector + 0.15 keyword + 0.10 activity after deterministic trial prefilter"
                            vector_fallback_reason = "Atlas vector filter fields are not mapped yet; used post-vector structured match."
                        except Exception as retry_error:
                            vector_fallback_reason = (
                                f"{vector_error_text} | Retry without inline vector filter failed: {str(retry_error)}"
                            )
                            synthea_pipeline = []
                    else:
                        vector_fallback_reason = vector_error_text
                        synthea_pipeline = []

            if not synthea_pipeline:
                synthea_pipeline = []
                if profile_query:
                    synthea_pipeline.append({"$match": profile_query})
                synthea_pipeline.extend(
                    [
                        {
                            "$addFields": {
                                "sharedKeywords": {
                                    "$setIntersection": [
                                        {"$ifNull": ["$featureTerms", []]},
                                        trial_keywords
                                    ]
                                },
                                "resourceEventCountSafe": {"$ifNull": ["$resourceEventCount", 0]},
                            }
                        },
                        {
                            "$addFields": {
                                "sharedKeywordCount": {"$size": "$sharedKeywords"},
                                "keywordScore": {
                                    "$cond": [
                                        {"$gt": [keyword_count, 0]},
                                        {"$divide": [{"$size": "$sharedKeywords"}, keyword_count]},
                                        0.5
                                    ]
                                },
                                "activityScore": {
                                    "$min": [
                                        1,
                                        {"$divide": [{"$ifNull": ["$resourceEventCountSafe", 0]}, 40]}
                                    ]
                                },
                            }
                        },
                        {
                            "$addFields": {
                                "retrievalScore": {
                                    "$round": [
                                        {
                                            "$add": [
                                                {"$multiply": ["$keywordScore", 0.8]},
                                                {"$multiply": ["$activityScore", 0.2]}
                                            ]
                                        },
                                        4
                                    ]
                                },
                                "trialFitScore": {
                                    "$round": [
                                        {
                                            "$add": [
                                                {"$multiply": ["$keywordScore", 0.8]},
                                                {"$multiply": ["$activityScore", 0.2]}
                                            ]
                                        },
                                        4
                                    ]
                                }
                            }
                        },
                        {"$sort": {"retrievalScore": -1, "sharedKeywordCount": -1, "resourceEventCountSafe": -1}},
                        {"$limit": candidate_pool_limit},
                        {
                            "$project": {
                                "_id": 0,
                                "source": {"$literal": "synthea"},
                                "patientRef": 1,
                                "name": 1,
                                "age": 1,
                                "gender": 1,
                                "sharedKeywords": 1,
                                "sharedKeywordCount": 1,
                                "resourceTypeCounts": 1,
                                "resourceEventCount": "$resourceEventCountSafe",
                                "keywordScore": 1,
                                "activityScore": 1,
                                "retrievalScore": 1,
                                "trialFitScore": 1,
                            }
                        }
                    ]
                )
                synthea_pipeline_visibility = synthea_pipeline
                synthea_rows = list(db_pharma_profiles_collection.aggregate(synthea_pipeline, allowDiskUse=True))

            enriched_synthea_rows = []
            final_blend_formula = "0.55 structured + 0.35 retrieval + 0.10 criteria_semantic, then assessment penalty"
            llm_candidate_reason = (
                "Candidate ranking uses deterministic structured evaluation only. "
                "LLM adjudication is patient-specific and runs from the trial detail action."
            )
            for row in synthea_rows:
                prefilter_evaluation = evaluate_profile_against_trial_prefilter(
                    profile_doc=row,
                    trial_prefilter_contract=trial_prefilter_contract,
                )
                if not prefilter_evaluation.get("passesHardFilters"):
                    continue
                patient_ref = row.get("patientRef")
                candidate_context = {
                    "age": row.get("age"),
                    "sex": row.get("gender"),
                    "keywords": row.get("sharedKeywords") or [],
                }
                candidate_evidence = build_patient_evidence_pack(
                    patient_context=candidate_context,
                    synthea_patient_ref=patient_ref,
                )
                candidate_context["evidencePack"] = candidate_evidence
                structured_eval = evaluate_trial_structured_match(
                    trial_doc,
                    candidate_context,
                    semantic_query=" ".join(row.get("sharedKeywords") or []),
                )
                structured_eval.pop("evidencePack", None)
                retrieval_score = safe_float(row.get("retrievalScore"), safe_float(row.get("trialFitScore"), 0.0))
                structured_score = safe_float(structured_eval.get("structuredScore"), 0.0)
                criteria_matches = structured_eval.get("criteriaLevelMatches") or []
                criteria_score = safe_float(
                    max([item.get("score", 0) for item in criteria_matches], default=0.0),
                    0.0
                )
                completeness_score = safe_float(structured_eval.get("evidenceCompletenessScore"), 0.0)
                decision_summary = structured_eval.get("decisionSummary") or {}
                overall_assessment = normalize_pharma_text(structured_eval.get("overallAssessment"))
                combined_score = (0.55 * structured_score) + (0.35 * retrieval_score) + (0.10 * criteria_score)
                combined_score = combined_score * (0.5 + (0.5 * completeness_score))
                if overall_assessment == "likely_ineligible":
                    fail_count = safe_float(decision_summary.get("failCount"), 0.0)
                    total_rules = max(
                        safe_float((structured_eval.get("coverageMeter") or {}).get("totalRules"), 0.0),
                        1.0
                    )
                    fail_ratio = min(1.0, fail_count / total_rules)
                    # Penalize ineligible rows while preserving score separation between candidates.
                    ineligible_penalty = max(0.08, 0.35 - (0.20 * fail_ratio))
                    combined_score = combined_score * ineligible_penalty
                elif overall_assessment == "needs_review":
                    combined_score = combined_score * 0.85
                combined_score = max(0.0, min(1.0, combined_score))
                row_payload = {
                    **row,
                    "trialPrefilterContract": trial_prefilter_contract,
                    "trialDefinitionCanonical": trial_definition_canonical,
                    "prefilterEvaluation": prefilter_evaluation,
                    "isEligibleStructured": structured_eval.get("isEligibleStructured"),
                    "structuredScore": structured_score,
                    "eligibilityScore": structured_eval.get("eligibilityScore"),
                    "evidenceCompletenessScore": structured_eval.get("evidenceCompletenessScore"),
                    "coverageMeter": structured_eval.get("coverageMeter") or {},
                    "riskEvidence": structured_eval.get("riskEvidence") or {},
                    "criterionBreakdown": structured_eval.get("criterionBreakdown") or [],
                    "ruleEvaluations": (structured_eval.get("ruleEvaluations") or [])[:20],
                    "criteriaLevelMatches": criteria_matches[:10],
                    "missingCriteria": (structured_eval.get("missingCriteria") or [])[:8],
                    "overallAssessment": structured_eval.get("overallAssessment"),
                    "decisionSummary": decision_summary,
                    "parserWarnings": (structured_eval.get("parserWarnings") or [])[:8],
                    "validatorWarnings": (structured_eval.get("validatorWarnings") or [])[:12],
                    "llmApplied": False,
                    "llmReason": llm_candidate_reason,
                    "semanticScore": (
                        round(safe_float(row.get("vectorSearchScore"), 0.0), 4)
                        if row.get("vectorSearchScore") is not None
                        else None
                    ),
                    "retrievalScore": round(retrieval_score, 4),
                    "criteriaLevelScore": round(criteria_score, 4),
                    "trialFitScore": round(combined_score, 4),
                }
                enriched_synthea_rows.append(row_payload)

            canonical_candidate_runtime = None
            canonical_candidate_error = None
            if STRICT_CANONICAL_MODE and enriched_synthea_rows:
                canonical_candidate_runtime, canonical_candidate_error = _canonical_candidate_match_map(
                    trial_id=trial_id,
                    patient_refs=[row.get("patientRef") for row in enriched_synthea_rows],
                    limit=len(enriched_synthea_rows),
                )
                if canonical_candidate_runtime:
                    criteria_lookup = canonical_candidate_runtime.get("criteriaLookup") or {}
                    canonical_rows = canonical_candidate_runtime.get("patientVersionByRef") or {}
                    for row_payload in enriched_synthea_rows:
                        patient_ref = str(row_payload.get("patientRef") or "").strip()
                        canonical_row = canonical_rows.get(patient_ref)
                        if not canonical_row:
                            continue
                        compat_eval = _build_legacy_structured_eval_from_canonical(
                            criteria_rows=canonical_row.get("criteria") or [],
                            criteria_lookup=criteria_lookup,
                            overall=canonical_row.get("overall"),
                        )
                        row_payload.update(
                            {
                                "prefilterEvaluation": {
                                    "passesHardFilters": bool(((canonical_row.get("prefilter") or {}).get("candidate"))),
                                    "evaluations": ((canonical_row.get("prefilter") or {}).get("evaluations")) or [],
                                },
                                "isEligibleStructured": compat_eval.get("isEligibleStructured"),
                                "structuredScore": compat_eval.get("structuredScore"),
                                "eligibilityScore": compat_eval.get("eligibilityScore"),
                                "evidenceCompletenessScore": compat_eval.get("evidenceCompletenessScore"),
                                "coverageMeter": compat_eval.get("coverageMeter") or {},
                                "criterionBreakdown": compat_eval.get("criterionBreakdown") or [],
                                "ruleEvaluations": (compat_eval.get("ruleEvaluations") or [])[:20],
                                "criteriaLevelMatches": (compat_eval.get("criteriaLevelMatches") or [])[:10],
                                "missingCriteria": (compat_eval.get("missingCriteria") or [])[:8],
                                "overallAssessment": compat_eval.get("overallAssessment"),
                                "decisionSummary": compat_eval.get("decisionSummary") or {},
                                "parserWarnings": compat_eval.get("parserWarnings") or [],
                                "validatorWarnings": compat_eval.get("validatorWarnings") or [],
                                "canonicalCompat": {
                                    "applied": True,
                                    "runId": canonical_candidate_runtime.get("runId"),
                                    "matcher": "clinical_trials_service_app",
                                },
                            }
                        )
                        retrieval_score = safe_float(row_payload.get("retrievalScore"), safe_float(row_payload.get("trialFitScore"), 0.0))
                        structured_score = safe_float(row_payload.get("structuredScore"), 0.0)
                        completeness_score = safe_float(row_payload.get("evidenceCompletenessScore"), 0.0)
                        overall_assessment = normalize_pharma_text(row_payload.get("overallAssessment"))
                        combined_score = (0.65 * structured_score) + (0.35 * retrieval_score)
                        combined_score = combined_score * (0.5 + (0.5 * completeness_score))
                        if overall_assessment == "likely_ineligible":
                            fail_count = safe_float(((row_payload.get("decisionSummary") or {}).get("failRules")), 0.0)
                            total_rules = max(
                                safe_float(((row_payload.get("coverageMeter") or {}).get("totalRules")), 0.0),
                                1.0,
                            )
                            fail_ratio = min(1.0, fail_count / total_rules)
                            combined_score = combined_score * max(0.08, 0.35 - (0.20 * fail_ratio))
                        elif overall_assessment == "needs_review":
                            combined_score = combined_score * 0.85
                        row_payload["trialFitScore"] = round(max(0.0, min(1.0, combined_score)), 4)
            synthea_rows = sorted(
                enriched_synthea_rows,
                key=lambda item: (
                    assessment_rank(item.get("overallAssessment")),
                    safe_float(item.get("trialFitScore"), 0),
                    safe_float(item.get("structuredScore"), 0)
                ),
                reverse=True
            )[:top_k]
            leafy_rows = []
    
            return jsonify({
                "success": True,
                "runtimeMode": COMPAT_RUNTIME_MODE,
                "trial": {
                    "trialId": trial_doc_serialized.get("trialId"),
                    "title": trial_doc_serialized.get("title"),
                    "status": trial_doc_serialized.get("status"),
                    "phase": trial_doc_serialized.get("phase"),
                    "source": trial_doc_serialized.get("source"),
                    "sourceUrl": trial_doc_serialized.get("sourceUrl"),
                    "trialVersion": trial_doc_serialized.get("trialVersion") or {},
                    "sourcePayloadHash": trial_doc_serialized.get("sourcePayloadHash"),
                },
                "summary": {
                    "syntheaCandidatesTotal": synthea_total_candidates,
                    "syntheaReturned": len(synthea_rows),
                    "cohortSource": "canonical_synthea_fhir",
                },
                "syntheaCandidates": serialize_doc(synthea_rows),
                "leafyCandidates": serialize_doc(leafy_rows),
                "pipelineVisibility": {
                    "syntheaMatchQuery": profile_query,
                    "syntheaPrefilterQuery": profile_query,
                    "syntheaRankingPipeline": synthea_pipeline_visibility,
                    "trialPrefilter": {
                        "mode": "deterministic_hard_filters",
                        "contract": trial_prefilter_contract,
                        "searchContractSource": search_contract_source,
                        "canonicalSearchContract": canonical_search_contract,
                        "canonicalSearchContractError": canonical_search_contract_error,
                        "prefilteredCandidateCount": synthea_total_candidates,
                    },
                    "candidateSearch": {
                        "mode": candidate_search_mode,
                        "cohortSource": "canonical_synthea_fhir",
                        "cohortLabel": "Unified patient registry",
                        "singleGovernedCohort": True,
                        "vectorEnabled": vector_enabled,
                        "vectorUsed": vector_used,
                        "vectorFilterUsed": vector_filter_used_in_search,
                        "vectorPostFilterApplied": vector_post_filter_applied,
                        "vectorSearchIndex": VOYAGE_PHARMA_PATIENT_VECTOR_INDEX_NAME if vector_used else None,
                        "vectorReturned": vector_returned,
                        "patientProfilesEmbedded": patient_profiles_embedded_total,
                        "vectorFallbackReason": vector_fallback_reason,
                        "retrievalBlendFormula": retrieval_blend_formula,
                        "finalBlendFormula": final_blend_formula,
                        "llmAdjudication": {
                            "applied": False,
                            "reason": llm_candidate_reason,
                        },
                        "searchContractSource": search_contract_source,
                        "searchContract": canonical_search_contract,
                        "searchContractError": canonical_search_contract_error,
                        "semanticQueryText": trial_semantic_query_text[:900],
                        "canonicalCompat": {
                            "enabled": STRICT_CANONICAL_MODE,
                            "applied": bool(canonical_candidate_runtime),
                            "runId": (canonical_candidate_runtime or {}).get("runId") if isinstance(canonical_candidate_runtime, dict) else None,
                            "error": canonical_candidate_error,
                            "decisionSurface": "compiled_service_criteria" if canonical_candidate_runtime else "legacy_atomic_rules",
                        },
                    },
                    "leafyRuleEngine": {
                        "function": "evaluate_trial_structured_match",
                        "criteria": ["atomic_rules", "risk_paths", "coverage_meter", "unknown_tracking", "criteria_level_semantic"]
                    }
                }
            }), 200
        except Exception as e:
            logging.error(f"Error matching selected trial to patient candidates: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    
    
    @blueprint.route("/pharma/cohort_graph", methods=["POST"])
    def get_pharma_cohort_graph():
        try:
            if db_pharma_feature_edges_collection is None or not PHARMA_FEATURE_EDGES_COLLECTION:
                return jsonify({
                    "success": False,
                    "error": "Cohort graph is deprecated in the lean runtime. Use deterministic trial-to-patient screening instead.",
                }), 410
            payload = request.get_json(silent=True) or {}
            appointment_id = str(payload.get("appointmentId") or "").strip()
            seed_patient_ref = str(payload.get("seedPatientRef") or "").strip()
            max_depth = min(parse_int_value(payload.get("maxDepth"), 2, 1), 4)
            max_similar = min(parse_int_value(payload.get("maxSimilarPatients"), 8, 1), 20)
    
            patient_context = None
            analog_candidates = []
            analog_pipeline = []
            if appointment_id and not seed_patient_ref:
                patient_context, _, _ = build_leafy_patient_context(appointment_id)
                patient_link = _resolve_linked_synthea_patient_ref(
                    patient_context=patient_context,
                    allow_analog=False,
                )
                seed_patient_ref = str(patient_link.get("syntheaPatientRef") or "").strip()

            if not seed_patient_ref:
                return jsonify({
                    "success": False,
                    "error": "seedPatientRef not found. Provide seedPatientRef or resolve an authoritative patient link first."
                }), 400
    
            seed_node = f"patient::{seed_patient_ref}"
            graph_pipeline = [
                {"$match": {"nodeFrom": seed_node}},
                {"$group": {"_id": "$nodeFrom", "startNodes": {"$addToSet": "$nodeTo"}}},
                {
                    "$graphLookup": {
                        "from": PHARMA_FEATURE_EDGES_COLLECTION,
                        "startWith": "$startNodes",
                        "connectFromField": "nodeTo",
                        "connectToField": "nodeFrom",
                        "as": "network",
                        "maxDepth": max_depth,
                        "depthField": "depth"
                    }
                },
                {"$project": {"_id": 0, "seedNode": "$_id", "startNodes": 1, "network": 1}},
            ]
            graph_rows = list(db_pharma_feature_edges_collection.aggregate(graph_pipeline, allowDiskUse=True))
            network_edges = graph_rows[0].get("network", []) if graph_rows else []
    
            patient_overlap_counter = defaultdict(int)
            feature_counter = defaultdict(int)
            for edge in network_edges:
                node_from = str(edge.get("nodeFrom") or "")
                node_to = str(edge.get("nodeTo") or "")
                if node_to.startswith("patient::") and node_to != seed_node:
                    patient_overlap_counter[node_to] += 1
                if node_to.startswith("term::") or node_to.startswith("condition::"):
                    feature_counter[node_to] += 1
                if node_from.startswith("term::") or node_from.startswith("condition::"):
                    feature_counter[node_from] += 1
    
            top_patient_nodes = sorted(
                patient_overlap_counter.items(),
                key=lambda item: item[1],
                reverse=True
            )[:max_similar]
            top_feature_nodes = sorted(
                feature_counter.items(),
                key=lambda item: item[1],
                reverse=True
            )[:12]
    
            node_items = [{"id": seed_node, "type": "patient", "label": seed_patient_ref, "group": "index"}]
            edge_items = []
    
            for patient_node, overlap_score in top_patient_nodes:
                node_items.append({
                    "id": patient_node,
                    "type": "patient",
                    "label": patient_node.replace("patient::", ""),
                    "group": "similar",
                    "overlapScore": overlap_score
                })
                edge_items.append({
                    "from": seed_node,
                    "to": patient_node,
                    "type": "shared-features",
                    "weight": overlap_score
                })
    
            for feature_node, feature_weight in top_feature_nodes:
                node_items.append({
                    "id": feature_node,
                    "type": "feature",
                    "label": feature_node.split("::", 1)[-1],
                    "group": "feature",
                    "weight": feature_weight
                })
    
            return jsonify({
                "success": True,
                "seedPatientRef": seed_patient_ref,
                "patientContext": patient_context,
                "syntheaAnalogs": analog_candidates,
                "graph": {
                    "nodes": node_items,
                    "edges": edge_items,
                    "stats": {
                        "edgeDocumentsTraversed": len(network_edges),
                        "similarPatientsFound": len(top_patient_nodes),
                        "featuresFound": len(top_feature_nodes),
                    }
                },
                "pipelineVisibility": {
                    "graphLookupPipeline": graph_pipeline,
                    "syntheaAnalogPipeline": analog_pipeline
                }
            }), 200
        except ValueError as value_error:
            return jsonify({"success": False, "error": str(value_error)}), 404
        except Exception as e:
            logging.error(f"Error generating pharma cohort graph: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    return blueprint
