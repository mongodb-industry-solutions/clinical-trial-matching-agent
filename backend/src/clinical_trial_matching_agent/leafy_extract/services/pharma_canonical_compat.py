from __future__ import annotations

import os
import re


STRICT_CANONICAL_MODE = str(os.getenv("PHARMA_STRICT_CANONICAL_MODE", "true")).strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}
_CANONICAL_COMPAT_RUNTIME = None


def load_canonical_compat_runtime() -> dict:
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


def canonical_overall_to_legacy_assessment(value):
    normalized = str(value or "").strip().lower()
    if normalized == "candidate":
        return "likely_eligible"
    if normalized == "excluded":
        return "likely_ineligible"
    return "needs_review"


def canonical_status_to_legacy_status(*, section, status):
    normalized_status = str(status or "").strip().lower()
    normalized_section = str(section or "inclusion").strip().lower()
    if normalized_status == "met":
        return "PASS"
    if normalized_status == "not_met":
        return "FAIL"
    if normalized_status == "not_patient_rule":
        return "UNKNOWN" if normalized_section in {"inclusion", "exclusion"} else "UNKNOWN"
    return "UNKNOWN"


def canonical_status_to_evaluation_mode(status):
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


def criterion_state_from_canonical(*, section, status):
    normalized_status = str(status or "").strip().lower()
    normalized_section = str(section or "inclusion").strip().lower()
    if normalized_status not in {"met", "not_met"}:
        return "unknown"
    if normalized_section == "exclusion":
        return "not_triggered" if normalized_status == "met" else "triggered"
    return "satisfied" if normalized_status == "met" else "not_satisfied"


def humanize_family_id(value):
    text = str(value or "").strip()
    if not text:
        return "general"
    return text.replace(".", " ")


def short_criterion_label(text, fallback):
    raw = re.sub(r"\s+", " ", str(text or "").strip())
    if not raw:
        return str(fallback or "criterion").strip() or "criterion"
    return raw if len(raw) <= 56 else f"{raw[:55].rstrip()}..."


def criterion_lookup_for_trial(*, service_collections, trial_id, version_id):
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


def build_legacy_structured_eval_from_canonical(*, criteria_rows, criteria_lookup, overall):
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
        legacy_status = canonical_status_to_legacy_status(section=section, status=canonical_status)
        criterion_state = criterion_state_from_canonical(section=section, status=canonical_status)
        eligibility_impact = (
            "supports_eligibility"
            if legacy_status == "PASS"
            else "blocks_eligibility"
            if legacy_status == "FAIL"
            else "unknown"
        )
        evaluation_mode = canonical_status_to_evaluation_mode(canonical_status)
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

    overall_assessment = canonical_overall_to_legacy_assessment(overall)
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
                "criterion": short_criterion_label(row.get("text"), row.get("ruleId")),
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


def canonical_trial_match_map(*, trial_ids, patient_ref, limit):
    runtime = load_canonical_compat_runtime()
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


def canonical_candidate_match_map(*, trial_id, patient_refs, limit):
    runtime = load_canonical_compat_runtime()
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
    criteria_lookup = criterion_lookup_for_trial(
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


def load_canonical_trial_search_contract(*, trial_id: str):
    runtime = load_canonical_compat_runtime()
    if not runtime.get("ok"):
        return None, runtime.get("error")
    clean_trial_id = str(trial_id or "").strip()
    if not clean_trial_id:
        return None, "canonical_trial_search_contract_inputs_missing"
    runtime["materialize_trials"](trial_ids=[clean_trial_id], max_trials=1)
    trial_doc = runtime["collections"].trials.find_one(
        {"_id": clean_trial_id},
        {
            "_id": 1,
            "current_version_id": 1,
            "match_contract.search_contract": 1,
            "match_contract.hard_prefilter": 1,
            "stage_relevance.targetWindow": 1,
            "brief_title": 1,
            "title": 1,
        },
    )
    if not isinstance(trial_doc, dict):
        return None, "canonical_trial_search_contract_not_found"
    search_contract = ((trial_doc.get("match_contract") or {}).get("search_contract")) or {}
    return {
        "trialId": clean_trial_id,
        "title": trial_doc.get("brief_title") or trial_doc.get("title"),
        "versionId": trial_doc.get("current_version_id"),
        "stageTargetWindow": ((trial_doc.get("stage_relevance") or {}).get("targetWindow")),
        "hardPrefilter": ((trial_doc.get("match_contract") or {}).get("hard_prefilter")) or {},
        "searchContract": search_contract,
    }, None
