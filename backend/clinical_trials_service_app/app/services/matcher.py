from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.config import get_settings
from app.services.compiler import get_effective_compiled_rule, get_effective_computability
from app.services.helpers import normalize_token, serialize_document, utcnow


READY_TRIAL_STATUSES = {"ready_auto", "ready_with_review", "compiled"}
MATCH_RESULT_STATUSES = {
    "met",
    "not_met",
    "unknown",
    "not_patient_rule",
    "blocked_by_linkage_conflict",
    "fillable_by_questionnaire",
    "manual_review_required",
}
OVERALL_RESULT_PRIORITY = {
    "candidate": 0,
    "needs_more_data": 1,
    "manual_review_required": 2,
    "needs_identity_resolution": 3,
    "excluded": 4,
}
FAMILY_FOR_PREFILTER_KEY = {
    "demographics.age_years.min": "demographics.age.min",
    "demographics.age_years.max": "demographics.age.max",
    "demographics.sex.any_of": "demographics.sex.allowed",
    "performance.ecog.max": "performance.ecog.max",
    "oncology.primary_cancer.any_of": "oncology.primary_cancer.required",
    "oncology.disease_extent.any_of": "oncology.advanced_or_metastatic.required",
    "metastases.cns.active": "metastases.cns.active",
}
CANONICAL_RUNTIME_MODE = "canonical_only"
LEGACY_BEHAVIOR_ALLOWED = False
POPULATION_MODE_AUTHORITATIVE = "authoritative_oncology"
POPULATION_MODE_PHARMACY = "pharmacy_prescreen"
STAGE_TARGET_WINDOWS = {
    "stage_ii_iii_or_locally_advanced",
    "advanced_or_metastatic",
    "mixed_or_manual",
    "not_stage_specific",
}
MATCHER_SUPPORTED_FAMILY_IDS = {
    "demographics.age.min",
    "demographics.age.max",
    "demographics.sex.allowed",
    "eligibility.healthy_volunteers.allowed",
    "performance.ecog.max",
    "organ_function.lab_panel",
    "therapy.prior_exposure.prohibited_history",
    "context.pregnancy_breastfeeding",
    "context.infection_recent_systemic_therapy",
    "concomitant_illness.significant",
    "metastases.cns.active",
    "sample_submission.willingness",
    "oncology.primary_cancer.required",
    "oncology.advanced_or_metastatic.required",
    "biomarkers.ER.status.required",
    "biomarkers.HER2.status.required",
}

LAB_CANONICAL_UNITS = {
    "anc": "10^3/uL",
    "platelet": "10^3/uL",
    "hemoglobin": "g/dL",
    "bilirubin": "mg/dL",
    "ast": "U/L",
    "alt": "U/L",
    "creatinine": "mg/dL",
    "creatinine_clearance": "mL/min",
    "egfr": "mL/min",
}

PRIOR_EXPOSURE_ALIAS_MAP = {
    "perjeta": {"perjeta", "pertuzumab"},
    "fulvestrant": {"fulvestrant"},
    "lapatinib": {"lapatinib", "lapatinib tosylate"},
    "elacestrant": {"elacestrant"},
    "arv_471": {"arv-471", "arv 471"},
    "parp_inhibitor": {"parp inhibitor", "parp inhibitors"},
    "cdk4_6_inhibitor": {"cdk4/6 inhibitor", "cdk46 inhibitor", "cdk 4/6 inhibitor"},
    "topoisomerase_i_inhibitor": {"topoisomerase i inhibitor", "topoisomerase 1 inhibitor"},
    "trop2_targeted_therapy": {"trop-2-targeted therapy", "trop 2 targeted therapy", "trop-2 targeted therapy"},
    "chemotherapy": {"chemotherapy"},
    "endocrine_therapy": {"endocrine therapy", "hormonal therapy"},
    "biologic_therapy": {"biologic therapy", "biological therapy"},
    "radiation": {"radiation", "radiation therapy"},
    "surgery": {"surgery"},
    "investigational_agent": {"investigational agent", "investigational agents", "investigational treatment"},
    "mtor_inhibitor": {"mtor inhibitor", "mtor inhibitors"},
    "pi3k_inhibitor": {"pi3k inhibitor", "pi3k inhibitors"},
    "akt_inhibitor": {"akt pathway inhibitor", "akt pathway inhibitors", "akt inhibitor", "akt inhibitors"},
}


class MatchNotFoundError(ValueError):
    pass


class MatchRuntimeGateError(ValueError):
    pass


def _settings():
    return get_settings()


def _collections():
    from app.database import collections

    return collections


def _patient_collections():
    from app.database import patient_collections

    return patient_collections


def _get_path(payload: dict | None, path: str | None) -> Any:
    current = payload or {}
    if not path:
        return None
    for part in str(path).split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return None
        return normalize_token(stripped)
    return value


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [normalize_token(item) for item in value if normalize_token(item)]
    if value in (None, ""):
        return []
    return [normalize_token(value)]


def _normalize_gap_fillable_by(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace("/", "_").replace(" ", "_")
    if not normalized:
        return "manual_review"
    if "questionnaire" in normalized:
        return "questionnaire"
    if normalized in {"site_coordinator", "coordinator", "site"}:
        return "site_coordinator"
    if any(token in normalized for token in {"ehr", "record", "chart", "pathology"}):
        return "ehr_only"
    return "manual_review"


def _comparison_status(*, patient_value: Any, operator: str, target_value: Any) -> str:
    if patient_value is None:
        return "unknown"

    if operator in {"=", "=="}:
        return "met" if patient_value == target_value else "not_met"
    if operator == "!=":
        return "met" if patient_value != target_value else "not_met"
    if operator == "<=":
        return "met" if patient_value <= target_value else "not_met"
    if operator == ">=":
        return "met" if patient_value >= target_value else "not_met"
    if operator == "<":
        return "met" if patient_value < target_value else "not_met"
    if operator == ">":
        return "met" if patient_value > target_value else "not_met"
    if operator == "in":
        return "met" if patient_value in list(target_value or []) else "not_met"
    if operator == "intersects":
        patient_values = set(_string_list(patient_value))
        target_values = set(_string_list(target_value))
        if not patient_values:
            return "unknown"
        return "met" if patient_values.intersection(target_values) else "not_met"
    if operator == "not_intersects":
        patient_values = set(_string_list(patient_value))
        target_values = set(_string_list(target_value))
        if not patient_values:
            return "unknown"
        return "met" if not patient_values.intersection(target_values) else "not_met"
    return "manual_review_required"


def _build_gap(*, feature: str, family_id: str, criterion_id: str | None, fillable_by: str) -> dict:
    return {
        "feature": feature,
        "familyId": family_id,
        "criterionId": criterion_id,
        "fillableBy": _normalize_gap_fillable_by(fillable_by),
    }


def _build_result(
    *,
    status: str,
    family_id: str,
    support: dict,
    criterion_id: str | None,
    reason: str,
    patient_value: Any = None,
    required_value: Any = None,
    gap: dict | None = None,
    evidence_refs: list[str] | None = None,
    source_of_truth: str | None = None,
) -> dict:
    payload = {
        "status": status if status in MATCH_RESULT_STATUSES else "manual_review_required",
        "familyId": family_id,
        "criterionId": criterion_id,
        "reason": reason,
        "patientValue": patient_value,
        "requiredValue": required_value,
        "evidencePaths": list((support or {}).get("evidencePaths") or []),
        "evidenceRefs": [str(item).strip() for item in (evidence_refs or []) if str(item).strip()],
        "evaluator": (support or {}).get("evaluator"),
        "prefilterPath": (support or {}).get("prefilterPath"),
        "sourceOfTruth": source_of_truth,
    }
    if gap:
        payload["gap"] = gap
    return payload


def _build_family_map(rule_families: list[dict]) -> dict[str, dict]:
    family_map: dict[str, dict] = {}
    for family in rule_families:
        family_id = str(family.get("family_id") or "").strip()
        if not family_id:
            continue
        existing = family_map.get(family_id)
        if existing is None or int(family.get("version") or 0) > int(existing.get("version") or 0):
            family_map[family_id] = family
    return family_map


def _patient_bundle_from_documents(*, patient_ref: str, facts_doc: dict | None, profile_doc: dict | None) -> dict:
    facts_document = facts_doc or {}
    profile_document = profile_doc or {}
    patient_evidence_profile = deepcopy((facts_document.get("patientEvidenceProfile") or {}))
    patient_match_profile = deepcopy((profile_document.get("patientMatchProfile") or {}))
    return {
        "patientRef": patient_ref,
        "patientName": facts_document.get("patientName") or profile_document.get("name"),
        "facts": facts_document,
        "profile": profile_document,
        "patientEvidenceProfile": patient_evidence_profile,
        "patientMatchProfile": patient_match_profile,
        "linkageStatus": _get_path(patient_evidence_profile, "identity.linkageStatus")
        or _get_path(patient_match_profile, "identity.linkageStatus")
        or "unlinked",
    }


def _patient_has_linkage_conflict(patient_bundle: dict) -> bool:
    return str(patient_bundle.get("linkageStatus") or "").strip().lower() == "conflict"


def _runtime_readiness(payload: dict | None) -> dict:
    return deepcopy((payload or {}).get("runtimeReadiness") or {})


def _resolved_feature(patient_bundle: dict, feature_key: str) -> dict:
    return deepcopy(_get_path(patient_bundle.get("patientEvidenceProfile"), f"resolvedFeatures.{feature_key}") or {})


def _feature_status(patient_bundle: dict, feature_key: str) -> str:
    return normalize_token((_resolved_feature(patient_bundle, feature_key).get("status") or "unknown"))


def _feature_value(patient_bundle: dict, feature_key: str) -> Any:
    return _resolved_feature(patient_bundle, feature_key).get("resolvedValue")


def _feature_conflict(patient_bundle: dict, feature_key: str) -> bool:
    feature = _resolved_feature(patient_bundle, feature_key)
    return bool(feature.get("conflict")) or _feature_status(patient_bundle, feature_key) == "conflicting"


def _feature_refs(patient_bundle: dict, feature_key: str) -> list[str]:
    refs = _resolved_feature(patient_bundle, feature_key).get("evidenceRefs") or []
    return [str(item).strip() for item in refs if str(item).strip()]


def _feature_source_of_truth(patient_bundle: dict, feature_key: str) -> str | None:
    value = _resolved_feature(patient_bundle, feature_key).get("sourceOfTruth")
    return str(value).strip() if value is not None else None


def _feature_is_unknown(patient_bundle: dict, feature_key: str) -> bool:
    status = _feature_status(patient_bundle, feature_key)
    return status in {"", "unknown"}


def _trial_runtime_readiness(trial_doc: dict) -> dict:
    return deepcopy((trial_doc.get("runtimeReadiness") or {}))


def _patient_runtime_readiness(patient_bundle: dict) -> dict:
    return deepcopy((_get_path(patient_bundle.get("patientMatchProfile"), "runtimeReadiness") or {}))


def _patient_authoritative_cohort(patient_bundle: dict) -> bool:
    return bool(_get_path(patient_bundle.get("patientMatchProfile"), "cohortMembership.authoritativeBreastOncology"))


def _patient_retail_outreach_ready(patient_bundle: dict) -> bool:
    return bool(_get_path(patient_bundle.get("patientMatchProfile"), "cohortMembership.retailOutreachReady"))


def _patient_screening_only(patient_bundle: dict) -> bool:
    return bool(_get_path(patient_bundle.get("patientMatchProfile"), "cohortMembership.screeningOnly"))


def _patient_pharmacy_prescreen_cohort(patient_bundle: dict) -> bool:
    return bool(_get_path(patient_bundle.get("patientMatchProfile"), "cohortMembership.pharmacyPrescreen"))


def _patient_benchmark_runnable(patient_bundle: dict) -> bool:
    readiness = _patient_runtime_readiness(patient_bundle)
    if "benchmarkRunnable" in readiness:
        return bool(readiness.get("benchmarkRunnable"))
    return bool(readiness.get("runnable"))


def _patient_outreach_runnable(patient_bundle: dict) -> bool:
    readiness = _patient_runtime_readiness(patient_bundle)
    if "outreachRunnable" in readiness:
        return bool(readiness.get("outreachRunnable"))
    return bool(readiness.get("runnable"))


def _patient_linkage_status(patient_bundle: dict) -> str:
    readiness = _patient_runtime_readiness(patient_bundle)
    return str(
        readiness.get("linkageStatus")
        or _get_path(patient_bundle.get("patientMatchProfile"), "identity.linkageStatus")
        or patient_bundle.get("linkageStatus")
        or ""
    ).strip().lower()


def _normalize_population_mode(value: Any) -> str:
    normalized = normalize_token(value)
    if normalized == POPULATION_MODE_PHARMACY:
        return POPULATION_MODE_PHARMACY
    return POPULATION_MODE_AUTHORITATIVE


def _patient_in_population_mode(patient_bundle: dict, population_mode: str) -> bool:
    if _patient_linkage_status(patient_bundle) != "linked":
        return False
    if population_mode == POPULATION_MODE_PHARMACY:
        return bool(
            _patient_retail_outreach_ready(patient_bundle)
            or _patient_pharmacy_prescreen_cohort(patient_bundle)
        )
    return _patient_authoritative_cohort(patient_bundle)


def _canonical_ranking_key(row: dict) -> tuple[Any, ...]:
    trace_summary = row.get("traceSummary") or {}
    return (
        OVERALL_RESULT_PRIORITY.get(row.get("overall"), 99),
        int(trace_summary.get("manualReviewRequiredCount") or 0),
        int(trace_summary.get("unknownCount") or 0) + int(trace_summary.get("fillableByQuestionnaireCount") or 0),
        -1 * float(row.get("evidenceCompletenessPct") or 0.0),
        str(row.get("trialId") or row.get("patientRef") or ""),
    )


def _blocked_run_status_for_patient(patient_bundle: dict, *, population_mode: str) -> str:
    readiness = _patient_runtime_readiness(patient_bundle)
    linkage_status = _patient_linkage_status(patient_bundle)
    if linkage_status != "linked":
        return "needs_identity_resolution"
    if population_mode == POPULATION_MODE_AUTHORITATIVE and not _patient_benchmark_runnable(patient_bundle):
        return "manual_review_required"
    if population_mode == POPULATION_MODE_PHARMACY and not _patient_outreach_runnable(patient_bundle) and _patient_retail_outreach_ready(patient_bundle):
        return "needs_identity_resolution"
    if population_mode == POPULATION_MODE_PHARMACY:
        return "needs_more_data"
    return "manual_review_required"


def _normalize_stage_target_window(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in STAGE_TARGET_WINDOWS else None


def _effective_stage_target_windows(stage_target_window: str | None) -> list[str]:
    normalized = _normalize_stage_target_window(stage_target_window)
    if not normalized:
        return []
    if normalized in {"stage_ii_iii_or_locally_advanced", "advanced_or_metastatic"}:
        return [normalized, "mixed_or_manual"]
    return [normalized]


def _patient_stage_target_window(patient_bundle: dict | None) -> str | None:
    match_profile = (patient_bundle or {}).get("patientMatchProfile") or {}
    cohort_membership = (match_profile.get("cohortMembership") or {})
    match_filters = (match_profile.get("matchFilters") or {})
    if bool(cohort_membership.get("breastCancerStageIiIii")):
        return "stage_ii_iii_or_locally_advanced"
    if bool(cohort_membership.get("metastaticBreastCancer")):
        return "advanced_or_metastatic"
    disease_window = _normalize_stage_target_window(match_filters.get("diseaseWindow"))
    if disease_window in {"stage_ii_iii_or_locally_advanced", "advanced_or_metastatic"}:
        return disease_window
    return None


def _build_trial_scope(*, patient_bundle: dict | None = None, requested_stage_target_window: str | None = None, trial_ids: list[str] | None = None) -> dict:
    normalized_requested = _normalize_stage_target_window(requested_stage_target_window)
    derived_from_patient = _patient_stage_target_window(patient_bundle)
    effective_target = normalized_requested or derived_from_patient
    explicit_trial_selection = bool([str(item).strip() for item in (trial_ids or []) if str(item).strip()])
    applied_target = None if explicit_trial_selection else effective_target
    if explicit_trial_selection:
        source = "explicit_trial_selection"
    elif normalized_requested:
        source = "request"
    elif derived_from_patient:
        source = "patient_stage_posture"
    else:
        source = "full_runtime_catalog"
    effective_windows = _effective_stage_target_windows(applied_target)
    return {
        "requestedTargetWindow": normalized_requested,
        "derivedTargetWindow": derived_from_patient,
        "appliedTargetWindow": applied_target,
        "effectiveTargetWindows": effective_windows,
        "source": source,
    }


def _match_run_contract_versions(*, patient_bundle: dict | None = None, trial_doc: dict | None = None, results: list[dict] | None = None) -> dict:
    settings = _settings()
    contract_versions = {
        "trialVersionId": (trial_doc or {}).get("current_version_id"),
        "trialContractVersion": settings.parser_version,
        "patientEvidenceProfileVersion": _get_path((patient_bundle or {}), "patientEvidenceProfile.schemaVersion"),
        "patientMatchProfileVersion": _get_path((patient_bundle or {}), "patientMatchProfile.schemaVersion"),
        "ruleFamilyRegistryVersion": settings.family_registry_version,
    }
    if not contract_versions["trialVersionId"] and results:
        contract_versions["trialVersionIds"] = {
            str(row.get("trialId")): row.get("trialVersionId")
            for row in results
            if row.get("trialId") and row.get("trialVersionId")
        }
    return contract_versions


def _trial_contract_metadata(trial_doc: dict | None) -> dict:
    match_contract = deepcopy(((trial_doc or {}).get("match_contract")) or {})
    hard_prefilter = deepcopy((match_contract.get("hard_prefilter")) or {})
    return {
        "trialVersionId": (trial_doc or {}).get("current_version_id"),
        "compileStatus": _get_path(trial_doc, "compile.status"),
        "stageRelevance": deepcopy(((trial_doc or {}).get("stage_relevance")) or {}),
        "hardPrefilterKeys": sorted(hard_prefilter.keys()),
        "hardPrefilterKeyCount": len(hard_prefilter),
        "deterministicCriterionCount": len(match_contract.get("deterministic_criterion_ids") or []),
        "manualCriterionCount": len(match_contract.get("manual_criterion_ids") or []),
    }


def _rule_traversal_summary_from_results(results: list[dict]) -> dict:
    summary = {
        "prefilterEvaluatedCount": 0,
        "criteriaEvaluatedCount": 0,
        "totalEvaluatedCount": 0,
        "actionableEvaluatedCount": 0,
        "deterministicCriterionCount": 0,
        "manualCriterionCount": 0,
        "notPatientRuleCriterionCount": 0,
    }
    for row in results or []:
        traversal = row.get("ruleTraversal") or {}
        for key in list(summary.keys()):
            summary[key] += int(traversal.get(key) or 0)
    return summary


def _result_contract_metadata_map(results: list[dict]) -> dict:
    trial_contracts = {}
    for row in results or []:
        trial_id = str(row.get("trialId") or "").strip()
        contract_metadata = row.get("contractMetadata") or {}
        if trial_id and contract_metadata:
            trial_contracts[trial_id] = contract_metadata
    return {
        "trialContracts": trial_contracts,
        "trialCount": len(trial_contracts),
    }


def _unknown_status_for_support(support: dict, *, family_id: str, criterion_id: str | None, feature: str, reason: str) -> dict:
    questionnaire_fillable = bool((support or {}).get("questionnaireFillable"))
    status = "fillable_by_questionnaire" if questionnaire_fillable else "unknown"
    fillable_by = "questionnaire" if questionnaire_fillable else "site_ehr_or_manual"
    return _build_result(
        status=status,
        family_id=family_id,
        support=support,
        criterion_id=criterion_id,
        reason=reason,
        gap=_build_gap(
            feature=feature,
            family_id=family_id,
            criterion_id=criterion_id,
            fillable_by=fillable_by,
        ),
    )


def _conflicting_feature_result(*, support: dict, family_id: str, criterion_id: str | None, feature: str, reason: str, patient_bundle: dict) -> dict:
    if _patient_has_linkage_conflict(patient_bundle):
        return _build_result(
            status="blocked_by_linkage_conflict",
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason=reason,
            gap=_build_gap(
                feature=feature,
                family_id=family_id,
                criterion_id=criterion_id,
                fillable_by="manual_review",
            ),
            evidence_refs=_feature_refs(patient_bundle, feature),
            source_of_truth=_feature_source_of_truth(patient_bundle, feature),
        )
    return _build_result(
        status="manual_review_required",
        family_id=family_id,
        support=support,
        criterion_id=criterion_id,
        reason=reason,
        gap=_build_gap(
            feature=feature,
            family_id=family_id,
            criterion_id=criterion_id,
            fillable_by="manual_review",
        ),
        evidence_refs=_feature_refs(patient_bundle, feature),
        source_of_truth=_feature_source_of_truth(patient_bundle, feature),
    )


def _marker_status(patient_bundle: dict, marker_key: str) -> str | None:
    normalized = _normalize_scalar(_feature_value(patient_bundle, marker_key))
    if normalized in {None, "unknown"}:
        return None
    return normalized


def _primary_cancer_code(patient_bundle: dict) -> str | None:
    status = _normalize_scalar(_feature_status(patient_bundle, "primaryCancer"))
    if status != "present":
        return None
    payload = _feature_value(patient_bundle, "primaryCancer")
    if isinstance(payload, dict):
        code = normalize_token(payload.get("code") or "")
        if code:
            return code
        text = normalize_token(payload.get("text") or "")
    else:
        text = normalize_token(payload or "")
    if "breast" in text:
        return "breast_carcinoma"
    return "primary_cancer_present"


def _disease_extent_values(patient_bundle: dict) -> list[str]:
    return _string_list(_feature_value(patient_bundle, "diseaseExtent"))


def _condition_terms(patient_bundle: dict) -> list[str]:
    return _string_list(_get_path(patient_bundle.get("patientEvidenceProfile"), "treatments.conditionHistory"))


def _derive_cns_metastases_active(patient_bundle: dict) -> bool | None:
    status = _feature_status(patient_bundle, "cnsMetastasesActive")
    if status == "present":
        return True
    if status == "absent":
        return False
    return None


def _derive_is_healthy_volunteer(patient_bundle: dict) -> bool | None:
    status = _feature_status(patient_bundle, "healthyVolunteer")
    if status == "present":
        return True
    if status == "absent":
        return False
    return None


def _normalize_lab_unit(unit_value: Any) -> str:
    return normalize_token(str(unit_value or "")).replace("μ", "u").replace("micro", "u")


def _normalize_lab_value(lab_key: str, numeric_value: Any, unit_value: Any) -> tuple[float | None, str | None]:
    try:
        value = float(numeric_value)
    except Exception:
        return None, None

    normalized_unit = _normalize_lab_unit(unit_value)
    if lab_key == "bilirubin":
        if "umol" in normalized_unit:
            return round(value / 17.1, 4), LAB_CANONICAL_UNITS[lab_key]
        return value, LAB_CANONICAL_UNITS[lab_key]
    if lab_key == "creatinine":
        if "umol" in normalized_unit:
            return round(value / 88.4, 4), LAB_CANONICAL_UNITS[lab_key]
        return value, LAB_CANONICAL_UNITS[lab_key]
    if lab_key == "hemoglobin":
        if normalized_unit in {"g/l", "gm/l"}:
            return round(value / 10.0, 4), LAB_CANONICAL_UNITS[lab_key]
        return value, LAB_CANONICAL_UNITS[lab_key]
    if lab_key in {"platelet", "anc"}:
        if any(token in normalized_unit for token in {"10^3/ul", "x10^3/ul", "10^9/l", "x10^9/l", "k/ul", "10*3/ul"}):
            return value, LAB_CANONICAL_UNITS[lab_key]
        if any(token in normalized_unit for token in {"/ul", "cells/ul", "cell/ul", "/mm3"}):
            return round(value / 1000.0, 4), LAB_CANONICAL_UNITS[lab_key]
        if not normalized_unit and ((lab_key == "anc" and value >= 100.0) or (lab_key == "platelet" and value >= 1000.0)):
            return round(value / 1000.0, 4), LAB_CANONICAL_UNITS[lab_key]
        return value, LAB_CANONICAL_UNITS[lab_key]
    return value, LAB_CANONICAL_UNITS.get(lab_key) or (str(unit_value).strip() if unit_value is not None else None)


def _patient_lab_payload(patient_bundle: dict, lab_key: str) -> dict | None:
    labs_payload = _feature_value(patient_bundle, "keyLabs") or _get_path(patient_bundle.get("patientEvidenceProfile"), "labs.values") or {}
    if not isinstance(labs_payload, dict):
        return None
    direct = labs_payload.get(lab_key)
    if isinstance(direct, dict):
        return direct
    alias_map = {
        "creatinine_clearance": {"crcl", "creatinine clearance"},
        "egfr": {"egfr", "estimated glomerular filtration rate"},
    }
    for alias in alias_map.get(lab_key, set()):
        direct = labs_payload.get(alias)
        if isinstance(direct, dict):
            return direct
    return None


def _evaluate_lab_panel_result(*, patient_bundle: dict, clauses: list[dict], support: dict, family_id: str, criterion_id: str | None) -> dict:
    unknown_clauses: list[str] = []
    failing_clauses: list[dict] = []
    evaluated_patient_values: dict[str, Any] = {}
    evidence_refs = _feature_refs(patient_bundle, "keyLabs")
    source_of_truth = _feature_source_of_truth(patient_bundle, "keyLabs")

    if _feature_conflict(patient_bundle, "keyLabs"):
        return _conflicting_feature_result(
            support=support,
            family_id=family_id,
            criterion_id=criterion_id,
            feature="keyLabs",
            reason="Laboratory evidence is conflicting across curated sources.",
            patient_bundle=patient_bundle,
        )

    if not clauses:
        return _build_result(
            status="manual_review_required",
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Laboratory panel rule has no evaluable clauses.",
            gap=_build_gap(
                feature="keyLabs",
                family_id=family_id,
                criterion_id=criterion_id,
                fillable_by="manual_review",
            ),
        )

    for clause in clauses:
        lab_key = str(clause.get("labKey") or "").strip()
        patient_lab = _patient_lab_payload(patient_bundle, lab_key)
        if not isinstance(patient_lab, dict) or patient_lab.get("value") is None:
            unknown_clauses.append(lab_key or "unknown_lab")
            continue
        if clause.get("usesUln"):
            ratio_to_uln = patient_lab.get("ratioToULN")
            if ratio_to_uln is None and patient_lab.get("uln") not in {None, 0}:
                try:
                    ratio_to_uln = float(patient_lab.get("value")) / float(patient_lab.get("uln"))
                except Exception:
                    ratio_to_uln = None
            if ratio_to_uln is None:
                unknown_clauses.append(f"{lab_key}::missing_uln_ratio")
                continue
            patient_value = float(ratio_to_uln)
            evaluated_patient_values[lab_key] = {"value": patient_value, "unit": "xULN"}
        else:
            patient_value, canonical_unit = _normalize_lab_value(lab_key, patient_lab.get("value"), patient_lab.get("unit"))
            if patient_value is None:
                unknown_clauses.append(lab_key)
                continue
            evaluated_patient_values[lab_key] = {"value": patient_value, "unit": canonical_unit}

        status = _comparison_status(
            patient_value=patient_value,
            operator=str(clause.get("operator") or "="),
            target_value=float(clause.get("value")),
        )
        if status == "not_met":
            failing_clauses.append(clause)
        elif status == "unknown":
            unknown_clauses.append(lab_key)

    if failing_clauses:
        return _build_result(
            status="not_met",
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="One or more laboratory thresholds were not satisfied.",
            patient_value=evaluated_patient_values,
            required_value=clauses,
            evidence_refs=evidence_refs,
            source_of_truth=source_of_truth,
        )
    if unknown_clauses:
        return _build_result(
            status="unknown",
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="One or more required laboratory values are missing or cannot be normalized.",
            patient_value=evaluated_patient_values or None,
            required_value=clauses,
            gap=_build_gap(
                feature="keyLabs",
                family_id=family_id,
                criterion_id=criterion_id,
                fillable_by="site_ehr_or_manual",
            ),
            evidence_refs=evidence_refs,
            source_of_truth=source_of_truth,
        )
    return _build_result(
        status="met",
        family_id=family_id,
        support=support,
        criterion_id=criterion_id,
        reason="All laboratory panel thresholds were satisfied.",
        patient_value=evaluated_patient_values,
        required_value=clauses,
        evidence_refs=evidence_refs,
        source_of_truth=source_of_truth,
    )


def _patient_treatment_terms(patient_bundle: dict) -> list[str]:
    terms = _feature_value(patient_bundle, "treatmentHistory")
    if isinstance(terms, list):
        normalized_terms = [normalize_token(item) for item in terms if normalize_token(item)]
    else:
        normalized_terms = []
    if normalized_terms:
        return normalized_terms
    fallback = _get_path(patient_bundle.get("patientEvidenceProfile"), "treatments.priorSystemicTherapy.terms") or []
    return [normalize_token(item) for item in fallback if normalize_token(item)]


def _expand_prior_exposure_targets(targets: list[Any]) -> set[str]:
    expanded: set[str] = set()
    for target in targets or []:
        normalized = normalize_token(target)
        if not normalized:
            continue
        expanded.add(normalized)
        for alias in PRIOR_EXPOSURE_ALIAS_MAP.get(normalized, set()):
            expanded.add(normalize_token(alias))
    return expanded


def evaluate_family_rule(
    *,
    family_id: str,
    compiled_rule: dict,
    patient_bundle: dict,
    family_doc: dict | None,
    criterion_id: str | None = None,
) -> dict:
    family_document = family_doc or {}
    support = family_document.get("patientSupport") or {}

    if not support.get("supported"):
        return _build_result(
            status="manual_review_required",
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Family is not yet connected to curated patient evidence.",
            gap=_build_gap(
                feature=family_id,
                family_id=family_id,
                criterion_id=criterion_id,
                fillable_by="manual_review",
            ),
        )

    if support.get("blockOnLinkageConflict") and _patient_has_linkage_conflict(patient_bundle):
        return _build_result(
            status="blocked_by_linkage_conflict",
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Patient linkage conflict blocks deterministic evaluation.",
            gap=_build_gap(
                feature="identity.linkage",
                family_id=family_id,
                criterion_id=criterion_id,
                fillable_by="manual_review",
            ),
        )

    logic = compiled_rule.get("logic") or {}
    operator = str(logic.get("operator") or "=").strip()
    target_value = logic.get("value")

    if family_id == "demographics.age.min":
        if _feature_conflict(patient_bundle, "age"):
            return _conflicting_feature_result(
                support=support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="age",
                reason="Patient age is conflicting across curated sources.",
                patient_bundle=patient_bundle,
            )
        patient_value = _feature_value(patient_bundle, "age")
        if patient_value is None:
            return _unknown_status_for_support(
                support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="age",
                reason="Missing patient age.",
            )
        status = _comparison_status(patient_value=int(patient_value), operator=operator, target_value=int(target_value))
        return _build_result(
            status=status,
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Compared patient age to minimum age rule.",
            patient_value=int(patient_value),
            required_value=target_value,
            evidence_refs=_feature_refs(patient_bundle, "age"),
            source_of_truth=_feature_source_of_truth(patient_bundle, "age"),
        )

    if family_id == "demographics.age.max":
        if _feature_conflict(patient_bundle, "age"):
            return _conflicting_feature_result(
                support=support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="age",
                reason="Patient age is conflicting across curated sources.",
                patient_bundle=patient_bundle,
            )
        patient_value = _feature_value(patient_bundle, "age")
        if patient_value is None:
            return _unknown_status_for_support(
                support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="age",
                reason="Missing patient age.",
            )
        status = _comparison_status(patient_value=int(patient_value), operator=operator, target_value=int(target_value))
        return _build_result(
            status=status,
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Compared patient age to maximum age rule.",
            patient_value=int(patient_value),
            required_value=target_value,
            evidence_refs=_feature_refs(patient_bundle, "age"),
            source_of_truth=_feature_source_of_truth(patient_bundle, "age"),
        )

    if family_id == "demographics.sex.allowed":
        if _feature_conflict(patient_bundle, "sex"):
            return _conflicting_feature_result(
                support=support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="sex",
                reason="Patient sex is conflicting across curated sources.",
                patient_bundle=patient_bundle,
            )
        patient_value = _normalize_scalar(_feature_value(patient_bundle, "sex"))
        if patient_value in {None, "unknown"}:
            return _unknown_status_for_support(
                support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="sex",
                reason="Missing patient sex.",
            )
        required_values = [normalize_token(item) for item in list(target_value or []) if normalize_token(item)]
        status = _comparison_status(patient_value=patient_value, operator=operator, target_value=required_values)
        return _build_result(
            status=status,
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Compared patient sex to allowed sexes.",
            patient_value=patient_value,
            required_value=required_values,
            evidence_refs=_feature_refs(patient_bundle, "sex"),
            source_of_truth=_feature_source_of_truth(patient_bundle, "sex"),
        )

    if family_id == "eligibility.healthy_volunteers.allowed":
        if _feature_conflict(patient_bundle, "healthyVolunteer"):
            return _conflicting_feature_result(
                support=support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="healthyVolunteer",
                reason="Healthy-volunteer status is conflicting across curated sources.",
                patient_bundle=patient_bundle,
            )
        patient_value = _derive_is_healthy_volunteer(patient_bundle)
        if patient_value is None:
            return _unknown_status_for_support(
                support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="healthyVolunteer",
                reason="Healthy-volunteer status is not explicitly curated.",
            )
        status = _comparison_status(patient_value=patient_value, operator=operator, target_value=bool(target_value))
        return _build_result(
            status=status,
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Derived healthy-volunteer status from curated clinical context.",
            patient_value=patient_value,
            required_value=bool(target_value),
            evidence_refs=_feature_refs(patient_bundle, "healthyVolunteer"),
            source_of_truth=_feature_source_of_truth(patient_bundle, "healthyVolunteer"),
        )

    if family_id == "performance.ecog.max":
        if _feature_conflict(patient_bundle, "ECOG"):
            return _conflicting_feature_result(
                support=support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="ECOG",
                reason="Performance status is conflicting across curated sources.",
                patient_bundle=patient_bundle,
            )
        patient_value = _feature_value(patient_bundle, "ECOG")
        if patient_value is None:
            return _unknown_status_for_support(
                support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="ECOG",
                reason="Missing ECOG performance status.",
            )
        status = _comparison_status(patient_value=int(patient_value), operator=operator, target_value=int(target_value))
        return _build_result(
            status=status,
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Compared patient ECOG performance status to trial threshold.",
            patient_value=int(patient_value),
            required_value=int(target_value),
            evidence_refs=_feature_refs(patient_bundle, "ECOG"),
            source_of_truth=_feature_source_of_truth(patient_bundle, "ECOG"),
        )

    if family_id == "organ_function.lab_panel":
        clauses = list((logic.get("clauses") or []))
        return _evaluate_lab_panel_result(
            patient_bundle=patient_bundle,
            clauses=clauses,
            support=support,
            family_id=family_id,
            criterion_id=criterion_id,
        )

    if family_id == "therapy.prior_exposure.prohibited_history":
        if _feature_conflict(patient_bundle, "treatmentHistory"):
            return _conflicting_feature_result(
                support=support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="treatmentHistory",
                reason="Prior therapy history is conflicting across curated sources.",
                patient_bundle=patient_bundle,
            )
        patient_terms = set(_patient_treatment_terms(patient_bundle))
        if not patient_terms:
            return _build_result(
                status="unknown",
                family_id=family_id,
                support=support,
                criterion_id=criterion_id,
                reason="Missing curated prior therapy history.",
                gap=_build_gap(
                    feature="treatmentHistory",
                    family_id=family_id,
                    criterion_id=criterion_id,
                    fillable_by="site_ehr_or_manual",
                ),
                evidence_refs=_feature_refs(patient_bundle, "treatmentHistory"),
                source_of_truth=_feature_source_of_truth(patient_bundle, "treatmentHistory"),
            )
        target_terms = _expand_prior_exposure_targets(list(target_value or []))
        patient_matches = sorted(term for term in patient_terms if term in target_terms)
        status = "met" if not patient_matches else "not_met"
        return _build_result(
            status=status,
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Compared curated prior therapy history to prohibited exposure set.",
            patient_value=sorted(patient_terms)[:16],
            required_value=sorted(target_terms),
            evidence_refs=_feature_refs(patient_bundle, "treatmentHistory"),
            source_of_truth=_feature_source_of_truth(patient_bundle, "treatmentHistory"),
        )

    if family_id == "context.pregnancy_breastfeeding":
        requirements = list((logic.get("requirements") or []))
        statuses: list[str] = []
        satisfied_payload: dict[str, Any] = {}
        for requirement in requirements:
            feature = str(requirement.get("feature") or "").strip()
            expected = str(requirement.get("expected") or "").strip()
            if feature in {"pregnancy", "breastfeeding"}:
                if _feature_conflict(patient_bundle, feature):
                    return _conflicting_feature_result(
                        support=support,
                        family_id=family_id,
                        criterion_id=criterion_id,
                        feature=feature,
                        reason=f"{feature} evidence is conflicting across curated sources.",
                        patient_bundle=patient_bundle,
                    )
                feature_status = _feature_status(patient_bundle, feature)
                if feature_status in {"", "unknown"}:
                    statuses.append("fillable_by_questionnaire")
                    continue
                if expected == "absent":
                    status = "met" if feature_status == "absent" else "not_met"
                else:
                    status = "met" if feature_status == expected else "not_met"
                statuses.append(status)
                satisfied_payload[feature] = feature_status
                continue
            if feature == "contraceptionAcknowledged":
                statuses.append("fillable_by_questionnaire")
                continue

        if "not_met" in statuses:
            return _build_result(
                status="not_met",
                family_id=family_id,
                support=support,
                criterion_id=criterion_id,
                reason="Pregnancy or breastfeeding context conflicts with the trial requirement.",
                patient_value=satisfied_payload or None,
                required_value=requirements,
                evidence_refs=_feature_refs(patient_bundle, "pregnancy") + _feature_refs(patient_bundle, "breastfeeding"),
            )
        if any(status in {"unknown", "fillable_by_questionnaire"} for status in statuses):
            return _build_result(
                status="fillable_by_questionnaire",
                family_id=family_id,
                support=support,
                criterion_id=criterion_id,
                reason="Pregnancy, breastfeeding, or contraception context needs questionnaire confirmation.",
                patient_value=satisfied_payload or None,
                required_value=requirements,
                gap=_build_gap(
                    feature="pregnancy_breastfeeding",
                    family_id=family_id,
                    criterion_id=criterion_id,
                    fillable_by="questionnaire",
                ),
                evidence_refs=_feature_refs(patient_bundle, "pregnancy") + _feature_refs(patient_bundle, "breastfeeding"),
            )
        return _build_result(
            status="met",
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Pregnancy and breastfeeding context satisfies the trial requirement.",
            patient_value=satisfied_payload or None,
            required_value=requirements,
            evidence_refs=_feature_refs(patient_bundle, "pregnancy") + _feature_refs(patient_bundle, "breastfeeding"),
        )

    if family_id == "context.infection_recent_systemic_therapy":
        requirements = list((logic.get("requirements") or []))
        statuses: list[str] = []
        patient_payload: dict[str, Any] = {}
        for requirement in requirements:
            requirement_type = str(requirement.get("type") or "").strip()
            if requirement_type == "active_infection_absent":
                if _feature_conflict(patient_bundle, "activeInfection"):
                    return _conflicting_feature_result(
                        support=support,
                        family_id=family_id,
                        criterion_id=criterion_id,
                        feature="activeInfection",
                        reason="Active infection evidence is conflicting across curated sources.",
                        patient_bundle=patient_bundle,
                    )
                feature_status = _feature_status(patient_bundle, "activeInfection")
                if feature_status in {"", "unknown"}:
                    statuses.append("fillable_by_questionnaire")
                elif feature_status == "absent":
                    statuses.append("met")
                    patient_payload["activeInfection"] = "absent"
                else:
                    statuses.append("not_met")
                    patient_payload["activeInfection"] = feature_status
                continue
            if requirement_type == "recent_systemic_therapy_window":
                statuses.append("unknown")
                patient_payload["recentSystemicTherapyWindowDays"] = int(requirement.get("windowDays") or 0)

        if "not_met" in statuses:
            return _build_result(
                status="not_met",
                family_id=family_id,
                support=support,
                criterion_id=criterion_id,
                reason="Curated infection/systemic-therapy context conflicts with the trial requirement.",
                patient_value=patient_payload or None,
                required_value=requirements,
                evidence_refs=_feature_refs(patient_bundle, "activeInfection") + _feature_refs(patient_bundle, "treatmentHistory"),
            )
        if "fillable_by_questionnaire" in statuses:
            return _build_result(
                status="fillable_by_questionnaire",
                family_id=family_id,
                support=support,
                criterion_id=criterion_id,
                reason="Current infection context requires questionnaire confirmation.",
                patient_value=patient_payload or None,
                required_value=requirements,
                gap=_build_gap(
                    feature="activeInfection",
                    family_id=family_id,
                    criterion_id=criterion_id,
                    fillable_by="questionnaire",
                ),
                evidence_refs=_feature_refs(patient_bundle, "activeInfection"),
            )
        if "unknown" in statuses:
            return _build_result(
                status="unknown",
                family_id=family_id,
                support=support,
                criterion_id=criterion_id,
                reason="Recent systemic therapy timing is not yet deterministically available in curated evidence.",
                patient_value=patient_payload or None,
                required_value=requirements,
                gap=_build_gap(
                    feature="treatmentHistory",
                    family_id=family_id,
                    criterion_id=criterion_id,
                    fillable_by="site_ehr_or_manual",
                ),
                evidence_refs=_feature_refs(patient_bundle, "treatmentHistory"),
                source_of_truth=_feature_source_of_truth(patient_bundle, "treatmentHistory"),
            )
        return _build_result(
            status="met",
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Infection and recent systemic therapy context satisfy the trial requirement.",
            patient_value=patient_payload or None,
            required_value=requirements,
            evidence_refs=_feature_refs(patient_bundle, "activeInfection") + _feature_refs(patient_bundle, "treatmentHistory"),
        )

    if family_id == "concomitant_illness.significant":
        return _build_result(
            status="manual_review_required",
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Significant concomitant illness remains an explicit manual family.",
            gap=_build_gap(
                feature="concomitantIllness",
                family_id=family_id,
                criterion_id=criterion_id,
                fillable_by="manual_review",
            ),
        )

    if family_id == "metastases.cns.active":
        if _feature_conflict(patient_bundle, "cnsMetastasesActive"):
            return _conflicting_feature_result(
                support=support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="cnsMetastasesActive",
                reason="CNS metastases evidence is conflicting across curated sources.",
                patient_bundle=patient_bundle,
            )
        patient_value = _derive_cns_metastases_active(patient_bundle)
        if patient_value is None:
            return _unknown_status_for_support(
                support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="cnsMetastasesActive",
                reason="Active CNS metastases are not deterministically present in curated evidence.",
            )
        status = _comparison_status(patient_value=bool(patient_value), operator=operator, target_value=bool(target_value))
        return _build_result(
            status=status,
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Evaluated curated evidence for active CNS metastases.",
            patient_value=bool(patient_value),
            required_value=bool(target_value),
            evidence_refs=_feature_refs(patient_bundle, "cnsMetastasesActive"),
            source_of_truth=_feature_source_of_truth(patient_bundle, "cnsMetastasesActive"),
        )

    if family_id == "sample_submission.willingness":
        if _feature_conflict(patient_bundle, "sampleSubmissionWilling"):
            return _conflicting_feature_result(
                support=support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="sampleSubmissionWilling",
                reason="Sample-submission willingness is conflicting across curated sources.",
                patient_bundle=patient_bundle,
            )
        patient_value = _feature_value(patient_bundle, "sampleSubmissionWilling")
        if patient_value is None:
            return _unknown_status_for_support(
                support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="sampleSubmissionWilling",
                reason="Sample-submission willingness is not captured in records.",
            )
        status = _comparison_status(patient_value=bool(patient_value), operator=operator, target_value=bool(target_value))
        return _build_result(
            status=status,
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Compared patient sample-submission willingness to trial requirement.",
            patient_value=bool(patient_value),
            required_value=bool(target_value),
            evidence_refs=_feature_refs(patient_bundle, "sampleSubmissionWilling"),
            source_of_truth=_feature_source_of_truth(patient_bundle, "sampleSubmissionWilling"),
        )

    if family_id == "oncology.primary_cancer.required":
        if _feature_conflict(patient_bundle, "primaryCancer"):
            return _conflicting_feature_result(
                support=support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="primaryCancer",
                reason="Primary cancer evidence is conflicting across curated sources.",
                patient_bundle=patient_bundle,
            )
        patient_value = _primary_cancer_code(patient_bundle)
        if patient_value is None:
            return _unknown_status_for_support(
                support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="primaryCancer",
                reason="Missing curated primary cancer confirmation.",
            )
        normalized_target = _string_list(target_value)
        status = _comparison_status(patient_value=patient_value, operator=operator, target_value=normalized_target)
        return _build_result(
            status=status,
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Compared curated primary cancer signal to trial requirement.",
            patient_value=patient_value,
            required_value=normalized_target,
            evidence_refs=_feature_refs(patient_bundle, "primaryCancer"),
            source_of_truth=_feature_source_of_truth(patient_bundle, "primaryCancer"),
        )

    if family_id == "oncology.advanced_or_metastatic.required":
        if _feature_conflict(patient_bundle, "diseaseExtent") or _feature_conflict(patient_bundle, "metastaticFlag"):
            return _conflicting_feature_result(
                support=support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="diseaseExtent",
                reason="Disease extent evidence is conflicting across curated sources.",
                patient_bundle=patient_bundle,
            )
        patient_value = _disease_extent_values(patient_bundle)
        if not patient_value:
            return _unknown_status_for_support(
                support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="diseaseExtent",
                reason="Missing advanced or metastatic disease-extent evidence.",
            )
        status = _comparison_status(patient_value=patient_value, operator=operator, target_value=target_value)
        return _build_result(
            status=status,
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Compared curated disease extent to trial requirement.",
            patient_value=patient_value,
            required_value=target_value,
            evidence_refs=_feature_refs(patient_bundle, "diseaseExtent"),
            source_of_truth=_feature_source_of_truth(patient_bundle, "diseaseExtent"),
        )

    if family_id == "biomarkers.ER.status.required":
        if _feature_conflict(patient_bundle, "ER"):
            return _conflicting_feature_result(
                support=support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="ER",
                reason="ER status is conflicting across curated sources.",
                patient_bundle=patient_bundle,
            )
        patient_value = _marker_status(patient_bundle, "ER")
        if patient_value is None:
            return _unknown_status_for_support(
                support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="ER",
                reason="Missing ER status.",
            )
        status = _comparison_status(patient_value=patient_value, operator=operator, target_value=_normalize_scalar(target_value))
        return _build_result(
            status=status,
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Compared curated ER status to trial requirement.",
            patient_value=patient_value,
            required_value=_normalize_scalar(target_value),
            evidence_refs=_feature_refs(patient_bundle, "ER"),
            source_of_truth=_feature_source_of_truth(patient_bundle, "ER"),
        )

    if family_id == "biomarkers.HER2.status.required":
        if _feature_conflict(patient_bundle, "HER2"):
            return _conflicting_feature_result(
                support=support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="HER2",
                reason="HER2 status is conflicting across curated sources.",
                patient_bundle=patient_bundle,
            )
        patient_value = _marker_status(patient_bundle, "HER2")
        if patient_value is None:
            return _unknown_status_for_support(
                support,
                family_id=family_id,
                criterion_id=criterion_id,
                feature="HER2",
                reason="Missing HER2 status.",
            )
        status = _comparison_status(patient_value=patient_value, operator=operator, target_value=_normalize_scalar(target_value))
        return _build_result(
            status=status,
            family_id=family_id,
            support=support,
            criterion_id=criterion_id,
            reason="Compared curated HER2 status to trial requirement.",
            patient_value=patient_value,
            required_value=_normalize_scalar(target_value),
            evidence_refs=_feature_refs(patient_bundle, "HER2"),
            source_of_truth=_feature_source_of_truth(patient_bundle, "HER2"),
        )

    return _build_result(
        status="manual_review_required",
        family_id=family_id,
        support=support,
        criterion_id=criterion_id,
        reason="No evaluator is available for this family yet.",
        gap=_build_gap(
            feature=family_id,
            family_id=family_id,
            criterion_id=criterion_id,
            fillable_by="manual_review",
        ),
    )


def evaluate_hard_prefilter(*, key: str, value: Any, patient_bundle: dict, family_map: dict[str, dict]) -> dict:
    family_id = FAMILY_FOR_PREFILTER_KEY.get(key)
    if not family_id:
        return {
            "key": key,
            "status": "manual_review_required",
            "reason": "No evaluator mapping exists for this hard-prefilter key.",
            "requiredValue": value,
        }

    if key == "demographics.age_years.min":
        compiled_rule = {"logic": {"operator": ">=", "value": value}}
    elif key == "demographics.age_years.max":
        compiled_rule = {"logic": {"operator": "<=", "value": value}}
    elif key == "demographics.sex.any_of":
        compiled_rule = {"logic": {"operator": "in", "value": list(value or [])}}
    elif key == "performance.ecog.max":
        compiled_rule = {"logic": {"operator": "<=", "value": value}}
    elif key == "oncology.primary_cancer.any_of":
        compiled_rule = {"logic": {"operator": "in", "value": list(value or [])}}
    elif key == "oncology.disease_extent.any_of":
        compiled_rule = {"logic": {"operator": "intersects", "value": list(value or [])}}
    elif key == "metastases.cns.active":
        if value is False:
            compiled_rule = {"logic": {"operator": "!=", "value": True}}
        else:
            compiled_rule = {"logic": {"operator": "=", "value": value}}
    else:
        compiled_rule = {"logic": {"operator": "=", "value": value}}

    result = evaluate_family_rule(
        family_id=family_id,
        compiled_rule=compiled_rule,
        patient_bundle=patient_bundle,
        family_doc=family_map.get(family_id),
        criterion_id=None,
    )
    return {
        "key": key,
        "status": result.get("status"),
        "familyId": family_id,
        "reason": result.get("reason"),
        "requiredValue": value,
        "patientValue": result.get("patientValue"),
        "evidencePaths": result.get("evidencePaths") or [],
        "evidenceRefs": result.get("evidenceRefs") or [],
        "evaluator": result.get("evaluator"),
        "sourceOfTruth": result.get("sourceOfTruth"),
        "gap": result.get("gap"),
    }


def evaluate_trial_match(
    *,
    trial_doc: dict,
    criteria_nodes: list[dict],
    patient_bundle: dict,
    family_map: dict[str, dict],
) -> dict:
    hard_prefilter = deepcopy(((trial_doc.get("match_contract") or {}).get("hard_prefilter")) or {})
    prefilter_rows = [
        evaluate_hard_prefilter(key=key, value=value, patient_bundle=patient_bundle, family_map=family_map)
        for key, value in sorted(hard_prefilter.items())
    ]

    criterion_rows = []
    for node in criteria_nodes:
        criterion_id = node.get("_id")
        computability = get_effective_computability(node)
        if computability == "not_patient_rule":
            criterion_rows.append(
                {
                    "criterionId": criterion_id,
                    "familyId": ((node.get("compiler") or {}).get("selected_family_id")),
                    "status": "not_patient_rule",
                    "reason": "Criterion is context-only or otherwise not a patient rule.",
                }
            )
            continue

        if computability != "deterministic":
            criterion_rows.append(
                {
                    "criterionId": criterion_id,
                    "familyId": ((node.get("review") or {}).get("final_family_id"))
                    or ((node.get("compiler") or {}).get("selected_family_id")),
                    "status": "manual_review_required",
                    "reason": "Criterion is not yet deterministic for automated matching.",
                    "gap": _build_gap(
                        feature="manualReview",
                        family_id=((node.get("review") or {}).get("final_family_id"))
                        or ((node.get("compiler") or {}).get("selected_family_id"))
                        or "unassigned",
                        criterion_id=criterion_id,
                        fillable_by="manual_review",
                    ),
                }
            )
            continue

        compiled_rule = get_effective_compiled_rule(node) or {}
        family_id = str(compiled_rule.get("family") or ((node.get("compiler") or {}).get("selected_family_id")) or "").strip()
        result = evaluate_family_rule(
            family_id=family_id,
            compiled_rule=compiled_rule,
            patient_bundle=patient_bundle,
            family_doc=family_map.get(family_id),
            criterion_id=criterion_id,
        )
        criterion_rows.append(
            {
                "criterionId": criterion_id,
                "familyId": family_id,
                "section": node.get("section"),
                "status": result.get("status"),
                "reason": result.get("reason"),
                "patientValue": result.get("patientValue"),
                "requiredValue": result.get("requiredValue"),
                "evidencePaths": result.get("evidencePaths") or [],
                "evidenceRefs": result.get("evidenceRefs") or [],
                "evaluator": result.get("evaluator"),
                "sourceOfTruth": result.get("sourceOfTruth"),
                "gap": result.get("gap"),
            }
        )

    gap_rows = []
    for row in prefilter_rows + criterion_rows:
        gap = row.get("gap")
        if isinstance(gap, dict):
            gap_rows.append(gap)
    unique_gap_rows = []
    seen_gaps = set()
    for gap in gap_rows:
        signature = (
            gap.get("feature"),
            gap.get("familyId"),
            gap.get("criterionId"),
            gap.get("fillableBy"),
        )
        if signature in seen_gaps:
            continue
        seen_gaps.add(signature)
        unique_gap_rows.append(gap)

    all_rows = prefilter_rows + criterion_rows
    all_statuses = [row.get("status") for row in all_rows]
    if "blocked_by_linkage_conflict" in all_statuses or _patient_has_linkage_conflict(patient_bundle):
        overall = "needs_identity_resolution"
    elif "not_met" in all_statuses:
        overall = "excluded"
    elif "manual_review_required" in all_statuses:
        overall = "manual_review_required"
    elif any(status in {"unknown", "fillable_by_questionnaire"} for status in all_statuses):
        overall = "needs_more_data"
    else:
        overall = "candidate"

    trace_summary = {
        "metCount": sum(1 for row in all_rows if row.get("status") == "met"),
        "notMetCount": sum(1 for row in all_rows if row.get("status") == "not_met"),
        "unknownCount": sum(1 for row in all_rows if row.get("status") == "unknown"),
        "manualReviewRequiredCount": sum(1 for row in all_rows if row.get("status") == "manual_review_required"),
        "fillableByQuestionnaireCount": sum(1 for row in all_rows if row.get("status") == "fillable_by_questionnaire"),
        "blockedByLinkageConflictCount": sum(1 for row in all_rows if row.get("status") == "blocked_by_linkage_conflict"),
        "notPatientRuleCount": sum(1 for row in all_rows if row.get("status") == "not_patient_rule"),
    }
    actionable_total = max(
        1,
        sum(1 for row in all_rows if row.get("status") not in {"not_patient_rule"})
    )
    evidence_completeness_pct = round(
        (
            (
                int(trace_summary["metCount"])
                + int(trace_summary["notMetCount"])
            )
            / actionable_total
        ) * 100.0,
        1,
    )
    deterministic_criterion_count = sum(
        1 for node in criteria_nodes if get_effective_computability(node) == "deterministic"
    )
    manual_criterion_count = sum(
        1 for node in criteria_nodes if get_effective_computability(node) not in {"deterministic", "not_patient_rule"}
    )
    not_patient_rule_count = sum(
        1 for node in criteria_nodes if get_effective_computability(node) == "not_patient_rule"
    )
    rule_traversal = {
        "prefilterEvaluatedCount": len(prefilter_rows),
        "criteriaEvaluatedCount": len(criterion_rows),
        "totalEvaluatedCount": len(all_rows),
        "actionableEvaluatedCount": actionable_total,
        "deterministicCriterionCount": deterministic_criterion_count,
        "manualCriterionCount": manual_criterion_count,
        "notPatientRuleCriterionCount": not_patient_rule_count,
    }
    contract_metadata = _trial_contract_metadata(trial_doc)

    return {
        "trialId": trial_doc.get("_id") or trial_doc.get("nct_id"),
        "title": trial_doc.get("brief_title") or trial_doc.get("title"),
        "compileStatus": _get_path(trial_doc, "compile.status"),
        "trialVersionId": trial_doc.get("current_version_id"),
        "runtimeReadiness": _trial_runtime_readiness(trial_doc),
        "overall": overall,
        "linkageStatus": patient_bundle.get("linkageStatus"),
        "evidenceCompletenessPct": evidence_completeness_pct,
        "traceSummary": trace_summary,
        "prefilter": {
            "evaluations": prefilter_rows,
            "candidate": not any(row.get("status") == "not_met" for row in prefilter_rows),
        },
        "ruleTraversal": rule_traversal,
        "contractMetadata": contract_metadata,
        "criteria": criterion_rows,
        "gaps": unique_gap_rows,
    }


class ClinicalTrialMatcherService:
    def list_match_runs(self, *, limit: int = 20) -> list[dict]:
        cursor = _collections().match_runs.find().sort([("startedAt", -1)]).limit(limit)
        return [serialize_document(row) for row in cursor]

    def get_match_run(self, run_id: str) -> dict | None:
        row = _collections().match_runs.find_one({"_id": run_id})
        return serialize_document(row) if row else None

    def match_patient_to_trials(self, patient_ref: str, payload: dict | None = None) -> dict:
        request = payload or {}
        population_mode = _normalize_population_mode(request.get("population_mode"))
        requested_stage_target_window = _normalize_stage_target_window(request.get("stage_target_window"))
        run_type = str(request.get("run_type") or "interactive").strip() or "interactive"
        benchmark_pack_id = str(request.get("benchmark_pack_id") or "").strip() or None
        benchmark_label = str(request.get("benchmark_label") or "").strip() or None
        patient_bundle = self._load_patient_bundle(patient_ref)
        patient_readiness = _patient_runtime_readiness(patient_bundle)
        trial_scope = _build_trial_scope(
            patient_bundle=patient_bundle,
            requested_stage_target_window=requested_stage_target_window,
            trial_ids=request.get("trial_ids"),
        )
        if _settings().strict_canonical_mode and not _patient_in_population_mode(patient_bundle, population_mode):
            run_document = self._build_blocked_patient_run(
                patient_bundle=patient_bundle,
                population_mode=population_mode,
                requested_stage_target_window=requested_stage_target_window,
                trial_ids=request.get("trial_ids"),
                run_type=run_type,
                benchmark_pack_id=benchmark_pack_id,
                benchmark_label=benchmark_label,
            )
            _collections().match_runs.insert_one(run_document)
            return {"run_id": run_document["_id"], "run": serialize_document(run_document)}

        family_map = self._family_map()
        trials = self._load_trials(
            trial_ids=request.get("trial_ids"),
            stage_target_window=trial_scope.get("appliedTargetWindow"),
        )
        criteria_by_trial = self._load_criteria_for_trials(trials)
        results = [
            evaluate_trial_match(
                trial_doc=trial_doc,
                criteria_nodes=criteria_by_trial.get(trial_doc.get("_id"), []),
                patient_bundle=patient_bundle,
                family_map=family_map,
            )
            for trial_doc in trials
        ]
        results.sort(key=_canonical_ranking_key)
        limited_results = results[: max(1, min(int(request.get("limit") or 20), 200))]
        summary = self._summarize_results(limited_results)
        run_document = {
            "_id": self._build_run_id(),
            "direction": "patient_to_trials",
            "runType": run_type,
            "patientRef": patient_ref,
            "trialIds": [row.get("trialId") for row in limited_results],
            "runStatus": "completed",
            "runtimeMode": CANONICAL_RUNTIME_MODE,
            "populationMode": population_mode,
            "legacyBehaviorAllowed": LEGACY_BEHAVIOR_ALLOWED,
            "legacyInputsUsed": [],
            "contractVersions": _match_run_contract_versions(patient_bundle=patient_bundle, results=limited_results),
            "contractMetadata": _result_contract_metadata_map(limited_results),
            "trialScope": trial_scope,
            "catalogCount": len(trials),
            "evaluatedCount": len(results),
            "startedAt": utcnow(),
            "finishedAt": utcnow(),
            "prefilter": {
                "candidateTrialCount": sum(1 for row in results if ((row.get("prefilter") or {}).get("candidate"))),
                "scannedTrialCount": len(trials),
            },
            "summary": summary,
            "ruleTraversalSummary": _rule_traversal_summary_from_results(limited_results),
            "results": limited_results,
            "patient": {
                "patientRef": patient_ref,
                "patientName": patient_bundle.get("patientName"),
                "linkageStatus": patient_bundle.get("linkageStatus"),
                "coveragePct": _get_path(patient_bundle.get("patientMatchProfile"), "coverageSnapshot.coveragePct"),
                "runtimeReadiness": patient_readiness,
                "cohortMembership": _get_path(patient_bundle.get("patientMatchProfile"), "cohortMembership") or {},
            },
            "error": None,
        }
        if benchmark_pack_id or benchmark_label:
            run_document["benchmark"] = {
                "packId": benchmark_pack_id,
                "label": benchmark_label,
            }
        _collections().match_runs.insert_one(run_document)
        return {"run_id": run_document["_id"], "run": serialize_document(run_document)}

    def match_trial_to_patients(self, trial_id: str, payload: dict | None = None) -> dict:
        request = payload or {}
        population_mode = _normalize_population_mode(request.get("population_mode"))
        run_type = str(request.get("run_type") or "interactive").strip() or "interactive"
        benchmark_pack_id = str(request.get("benchmark_pack_id") or "").strip() or None
        benchmark_label = str(request.get("benchmark_label") or "").strip() or None
        trial_doc = _collections().trials.find_one({"_id": trial_id, "compile.status": {"$in": list(READY_TRIAL_STATUSES)}})
        if not trial_doc:
            raise MatchNotFoundError(f"Trial {trial_id} not found or not ready for matching")
        runtime_readiness = _trial_runtime_readiness(trial_doc)
        if _settings().strict_canonical_mode and not bool(runtime_readiness.get("runnable")):
            raise MatchRuntimeGateError(
                f"Trial {trial_id} is not runtime-ready for canonical screening: {runtime_readiness.get('reasonIfBlocked') or 'blocked'}"
            )

        patient_bundles = self._load_patient_bundles(
            patient_refs=request.get("patient_refs"),
            include_conflict_patients=bool(request.get("include_conflict_patients")),
            population_mode=population_mode,
        )
        family_map = self._family_map()
        criteria_nodes = self._load_criteria_for_trials([trial_doc]).get(trial_id, [])
        results = []
        for patient_bundle in patient_bundles:
            match_result = evaluate_trial_match(
                trial_doc=trial_doc,
                criteria_nodes=criteria_nodes,
                patient_bundle=patient_bundle,
                family_map=family_map,
            )
            results.append(
                {
                    "patientRef": patient_bundle.get("patientRef"),
                    "patientName": patient_bundle.get("patientName"),
                    "overall": match_result.get("overall"),
                    "linkageStatus": patient_bundle.get("linkageStatus"),
                    "coveragePct": _get_path(patient_bundle.get("patientMatchProfile"), "coverageSnapshot.coveragePct"),
                    "runtimeReadiness": _patient_runtime_readiness(patient_bundle),
                    "cohortMembership": _get_path(patient_bundle.get("patientMatchProfile"), "cohortMembership") or {},
                    "prefilter": match_result.get("prefilter"),
                    "criteria": match_result.get("criteria"),
                    "gaps": match_result.get("gaps"),
                    "traceSummary": match_result.get("traceSummary") or {},
                    "ruleTraversal": match_result.get("ruleTraversal") or {},
                    "contractMetadata": match_result.get("contractMetadata") or {},
                    "evidenceCompletenessPct": match_result.get("evidenceCompletenessPct"),
                }
            )
        results.sort(key=_canonical_ranking_key)
        limited_results = results[: max(1, min(int(request.get("limit") or 50), 500))]
        summary = self._summarize_results(limited_results)
        run_document = {
            "_id": self._build_run_id(),
            "direction": "trial_to_patients",
            "runType": run_type,
            "trialId": trial_id,
            "patientRefs": [row.get("patientRef") for row in limited_results],
            "runStatus": "completed",
            "runtimeMode": CANONICAL_RUNTIME_MODE,
            "populationMode": population_mode,
            "legacyBehaviorAllowed": LEGACY_BEHAVIOR_ALLOWED,
            "legacyInputsUsed": [],
            "contractVersions": _match_run_contract_versions(patient_bundle=None, trial_doc=trial_doc),
            "contractMetadata": _trial_contract_metadata(trial_doc),
            "trialScope": {
                "requestedTargetWindow": _normalize_stage_target_window(_get_path(trial_doc, "stage_relevance.targetWindow")),
                "derivedTargetWindow": None,
                "appliedTargetWindow": _normalize_stage_target_window(_get_path(trial_doc, "stage_relevance.targetWindow")),
                "effectiveTargetWindows": _effective_stage_target_windows(_get_path(trial_doc, "stage_relevance.targetWindow")),
                "source": "selected_trial_contract",
            },
            "catalogCount": len(patient_bundles),
            "evaluatedCount": len(results),
            "startedAt": utcnow(),
            "finishedAt": utcnow(),
            "prefilter": {
                "candidatePatientCount": sum(1 for row in results if ((row.get("prefilter") or {}).get("candidate"))),
                "scannedPatientCount": len(patient_bundles),
            },
            "summary": summary,
            "ruleTraversalSummary": _rule_traversal_summary_from_results(limited_results),
            "results": limited_results,
            "trial": {
                "trialId": trial_id,
                "title": trial_doc.get("brief_title") or trial_doc.get("title"),
                "compileStatus": _get_path(trial_doc, "compile.status"),
                "runtimeReadiness": runtime_readiness,
                "currentVersionId": trial_doc.get("current_version_id"),
                "contractMetadata": _trial_contract_metadata(trial_doc),
            },
            "error": None,
        }
        if benchmark_pack_id or benchmark_label:
            run_document["benchmark"] = {
                "packId": benchmark_pack_id,
                "label": benchmark_label,
            }
        _collections().match_runs.insert_one(run_document)
        return {"run_id": run_document["_id"], "run": serialize_document(run_document)}

    def match_patient_to_trials_stream(self, patient_ref: str, payload: dict | None = None):
        """Yields progress dicts then a final {"type":"done","run":{...}} event."""
        request = payload or {}
        population_mode = _normalize_population_mode(request.get("population_mode"))
        requested_stage_target_window = _normalize_stage_target_window(request.get("stage_target_window"))
        run_type = str(request.get("run_type") or "interactive").strip() or "interactive"
        benchmark_pack_id = str(request.get("benchmark_pack_id") or "").strip() or None
        benchmark_label = str(request.get("benchmark_label") or "").strip() or None

        yield {"type": "step", "step": 1, "steps": 5, "label": "Building patient context"}
        patient_bundle = self._load_patient_bundle(patient_ref)
        patient_readiness = _patient_runtime_readiness(patient_bundle)
        trial_scope = _build_trial_scope(
            patient_bundle=patient_bundle,
            requested_stage_target_window=requested_stage_target_window,
            trial_ids=request.get("trial_ids"),
        )

        if _settings().strict_canonical_mode and not _patient_in_population_mode(patient_bundle, population_mode):
            run_document = self._build_blocked_patient_run(
                patient_bundle=patient_bundle,
                population_mode=population_mode,
                requested_stage_target_window=requested_stage_target_window,
                trial_ids=request.get("trial_ids"),
                run_type=run_type,
                benchmark_pack_id=benchmark_pack_id,
                benchmark_label=benchmark_label,
            )
            _collections().match_runs.insert_one(run_document)
            yield {"type": "done", "run": serialize_document(run_document)}
            return

        family_map = self._family_map()

        yield {"type": "step", "step": 2, "steps": 5, "label": "Loading trial catalog"}
        trials = self._load_trials(
            trial_ids=request.get("trial_ids"),
            stage_target_window=trial_scope.get("appliedTargetWindow"),
        )

        yield {"type": "step", "step": 3, "steps": 5, "label": f"Applying prefilters ({len(trials)} trials)"}
        criteria_by_trial = self._load_criteria_for_trials(trials)

        yield {"type": "step", "step": 4, "steps": 5, "label": f"Evaluating criteria (0 of {len(trials)} trials)", "current": 0, "total": len(trials)}
        results = []
        for i, trial_doc in enumerate(trials):
            result = evaluate_trial_match(
                trial_doc=trial_doc,
                criteria_nodes=criteria_by_trial.get(trial_doc.get("_id"), []),
                patient_bundle=patient_bundle,
                family_map=family_map,
            )
            results.append(result)
            yield {"type": "progress", "step": 4, "steps": 5, "label": f"Evaluated {i + 1} of {len(trials)} trials", "current": i + 1, "total": len(trials)}

        yield {"type": "step", "step": 5, "steps": 5, "label": "Ranking results"}
        results.sort(key=_canonical_ranking_key)
        limited_results = results[: max(1, min(int(request.get("limit") or 20), 200))]
        summary = self._summarize_results(limited_results)
        run_document = {
            "_id": self._build_run_id(),
            "direction": "patient_to_trials",
            "runType": run_type,
            "patientRef": patient_ref,
            "trialIds": [row.get("trialId") for row in limited_results],
            "runStatus": "completed",
            "runtimeMode": CANONICAL_RUNTIME_MODE,
            "populationMode": population_mode,
            "legacyBehaviorAllowed": LEGACY_BEHAVIOR_ALLOWED,
            "legacyInputsUsed": [],
            "contractVersions": _match_run_contract_versions(patient_bundle=patient_bundle, results=limited_results),
            "contractMetadata": _result_contract_metadata_map(limited_results),
            "trialScope": trial_scope,
            "catalogCount": len(trials),
            "evaluatedCount": len(results),
            "startedAt": utcnow(),
            "finishedAt": utcnow(),
            "prefilter": {
                "candidateTrialCount": sum(1 for row in results if ((row.get("prefilter") or {}).get("candidate"))),
                "scannedTrialCount": len(trials),
            },
            "summary": summary,
            "ruleTraversalSummary": _rule_traversal_summary_from_results(limited_results),
            "results": limited_results,
            "patient": {
                "patientRef": patient_ref,
                "patientName": patient_bundle.get("patientName"),
                "linkageStatus": patient_bundle.get("linkageStatus"),
                "coveragePct": _get_path(patient_bundle.get("patientMatchProfile"), "coverageSnapshot.coveragePct"),
                "runtimeReadiness": patient_readiness,
                "cohortMembership": _get_path(patient_bundle.get("patientMatchProfile"), "cohortMembership") or {},
            },
            "error": None,
        }
        if benchmark_pack_id or benchmark_label:
            run_document["benchmark"] = {"packId": benchmark_pack_id, "label": benchmark_label}
        _collections().match_runs.insert_one(run_document)
        yield {"type": "done", "run": serialize_document(run_document)}

    def match_trial_to_patients_stream(self, trial_id: str, payload: dict | None = None):
        """Yields progress dicts then a final {"type":"done","run":{...}} event."""
        request = payload or {}
        population_mode = _normalize_population_mode(request.get("population_mode"))
        run_type = str(request.get("run_type") or "interactive").strip() or "interactive"
        benchmark_pack_id = str(request.get("benchmark_pack_id") or "").strip() or None
        benchmark_label = str(request.get("benchmark_label") or "").strip() or None

        yield {"type": "step", "step": 1, "steps": 4, "label": "Loading trial contract"}
        trial_doc = _collections().trials.find_one({"_id": trial_id, "compile.status": {"$in": list(READY_TRIAL_STATUSES)}})
        if not trial_doc:
            yield {"type": "error", "message": f"Trial {trial_id} not found or not ready for matching"}
            return
        runtime_readiness = _trial_runtime_readiness(trial_doc)
        if _settings().strict_canonical_mode and not bool(runtime_readiness.get("runnable")):
            yield {"type": "error", "message": f"Trial {trial_id} is not runtime-ready: {runtime_readiness.get('reasonIfBlocked') or 'blocked'}"}
            return

        yield {"type": "step", "step": 2, "steps": 4, "label": "Loading patient registry"}
        patient_bundles = self._load_patient_bundles(
            patient_refs=request.get("patient_refs"),
            include_conflict_patients=bool(request.get("include_conflict_patients")),
            population_mode=population_mode,
        )
        family_map = self._family_map()
        criteria_nodes = self._load_criteria_for_trials([trial_doc]).get(trial_id, [])

        yield {"type": "step", "step": 3, "steps": 4, "label": f"Evaluating criteria (0 of {len(patient_bundles)} patients)", "current": 0, "total": len(patient_bundles)}
        results = []
        for i, patient_bundle in enumerate(patient_bundles):
            match_result = evaluate_trial_match(
                trial_doc=trial_doc,
                criteria_nodes=criteria_nodes,
                patient_bundle=patient_bundle,
                family_map=family_map,
            )
            results.append({
                "patientRef": patient_bundle.get("patientRef"),
                "patientName": patient_bundle.get("patientName"),
                "overall": match_result.get("overall"),
                "linkageStatus": patient_bundle.get("linkageStatus"),
                "coveragePct": _get_path(patient_bundle.get("patientMatchProfile"), "coverageSnapshot.coveragePct"),
                "runtimeReadiness": _patient_runtime_readiness(patient_bundle),
                "cohortMembership": _get_path(patient_bundle.get("patientMatchProfile"), "cohortMembership") or {},
                "prefilter": match_result.get("prefilter"),
                "criteria": match_result.get("criteria"),
                "gaps": match_result.get("gaps"),
                "traceSummary": match_result.get("traceSummary") or {},
                "ruleTraversal": match_result.get("ruleTraversal") or {},
                "contractMetadata": match_result.get("contractMetadata") or {},
                "evidenceCompletenessPct": match_result.get("evidenceCompletenessPct"),
            })
            yield {"type": "progress", "step": 3, "steps": 4, "label": f"Evaluated {i + 1} of {len(patient_bundles)} patients", "current": i + 1, "total": len(patient_bundles)}

        yield {"type": "step", "step": 4, "steps": 4, "label": "Ranking candidates"}
        results.sort(key=_canonical_ranking_key)
        limited_results = results[: max(1, min(int(request.get("limit") or 50), 500))]
        summary = self._summarize_results(limited_results)
        run_document = {
            "_id": self._build_run_id(),
            "direction": "trial_to_patients",
            "runType": run_type,
            "trialId": trial_id,
            "patientRefs": [row.get("patientRef") for row in limited_results],
            "runStatus": "completed",
            "runtimeMode": CANONICAL_RUNTIME_MODE,
            "populationMode": population_mode,
            "legacyBehaviorAllowed": LEGACY_BEHAVIOR_ALLOWED,
            "legacyInputsUsed": [],
            "contractVersions": _match_run_contract_versions(patient_bundle=None, trial_doc=trial_doc),
            "contractMetadata": _trial_contract_metadata(trial_doc),
            "trialScope": {
                "requestedTargetWindow": _normalize_stage_target_window(_get_path(trial_doc, "stage_relevance.targetWindow")),
                "derivedTargetWindow": None,
                "appliedTargetWindow": _normalize_stage_target_window(_get_path(trial_doc, "stage_relevance.targetWindow")),
                "effectiveTargetWindows": _effective_stage_target_windows(_get_path(trial_doc, "stage_relevance.targetWindow")),
                "source": "selected_trial_contract",
            },
            "catalogCount": len(patient_bundles),
            "evaluatedCount": len(results),
            "startedAt": utcnow(),
            "finishedAt": utcnow(),
            "prefilter": {
                "candidatePatientCount": sum(1 for row in results if ((row.get("prefilter") or {}).get("candidate"))),
                "scannedPatientCount": len(patient_bundles),
            },
            "summary": summary,
            "ruleTraversalSummary": _rule_traversal_summary_from_results(limited_results),
            "results": limited_results,
            "trial": {
                "trialId": trial_id,
                "title": trial_doc.get("brief_title") or trial_doc.get("title"),
                "compileStatus": _get_path(trial_doc, "compile.status"),
                "runtimeReadiness": runtime_readiness,
                "currentVersionId": trial_doc.get("current_version_id"),
                "contractMetadata": _trial_contract_metadata(trial_doc),
            },
            "error": None,
        }
        if benchmark_pack_id or benchmark_label:
            run_document["benchmark"] = {"packId": benchmark_pack_id, "label": benchmark_label}
        _collections().match_runs.insert_one(run_document)
        yield {"type": "done", "run": serialize_document(run_document)}

    def _build_run_id(self) -> str:
        now = utcnow()
        return f"match_{now.strftime('%Y%m%d_%H%M%S_%f')}"

    def _family_map(self) -> dict[str, dict]:
        families = list(_collections().rule_families.find().sort([("family_id", 1), ("version", -1)]))
        return _build_family_map(families)

    def _load_trials(self, *, trial_ids: list[str] | None = None, stage_target_window: str | None = None) -> list[dict]:
        query: dict[str, Any] = {"compile.status": {"$in": list(READY_TRIAL_STATUSES)}}
        if _settings().strict_canonical_mode:
            query["runtimeReadiness.runnable"] = True
        if trial_ids:
            query["_id"] = {"$in": [str(item).strip() for item in trial_ids if str(item).strip()]}
        else:
            effective_stage_windows = _effective_stage_target_windows(stage_target_window)
            if effective_stage_windows:
                query["stage_relevance.targetWindow"] = {"$in": effective_stage_windows}
        cursor = _collections().trials.find(query).sort([("display.last_ingested_at", -1)])
        return list(cursor)

    def _load_criteria_for_trials(self, trials: list[dict]) -> dict[str, list[dict]]:
        criteria_by_trial: dict[str, list[dict]] = {}
        for trial_doc in trials:
            trial_id = trial_doc.get("_id")
            version_id = trial_doc.get("current_version_id")
            if not trial_id or not version_id:
                continue
            criteria_by_trial[trial_id] = list(
                _collections().trial_criteria.find({"trial_id": trial_id, "version_id": version_id}).sort([("ordinal", 1)])
            )
        return criteria_by_trial

    def _load_patient_bundle(self, patient_ref: str) -> dict:
        patient_collections = _patient_collections()
        facts_doc = patient_collections.facts.find_one({"patientRef": patient_ref})
        profile_doc = patient_collections.profiles.find_one({"patientRef": patient_ref})
        if not facts_doc and not profile_doc:
            raise MatchNotFoundError(f"Patient {patient_ref} not found in curated patient collections")
        return _patient_bundle_from_documents(patient_ref=patient_ref, facts_doc=facts_doc, profile_doc=profile_doc)

    def _load_patient_bundles(
        self,
        *,
        patient_refs: list[str] | None = None,
        include_conflict_patients: bool,
        population_mode: str,
    ) -> list[dict]:
        patient_collections = _patient_collections()
        query: dict[str, Any] = {}
        if patient_refs:
            query["patientRef"] = {"$in": [str(item).strip() for item in patient_refs if str(item).strip()]}
        elif _settings().strict_canonical_mode:
            query["patientMatchProfile.identity.linkageStatus"] = "linked"
            if population_mode == POPULATION_MODE_PHARMACY:
                query["$or"] = [
                    {
                        "patientMatchProfile.cohortMembership.retailOutreachReady": True,
                        "patientMatchProfile.runtimeReadiness.outreachRunnable": True,
                    },
                    {
                        "patientMatchProfile.cohortMembership.pharmacyPrescreen": True,
                        "patientMatchProfile.runtimeReadiness.mode": POPULATION_MODE_PHARMACY,
                        "patientMatchProfile.runtimeReadiness.runnable": True,
                    },
                ]
            else:
                query["patientMatchProfile.cohortMembership.authoritativeBreastOncology"] = True
                query["patientMatchProfile.runtimeReadiness.benchmarkRunnable"] = True
        if not include_conflict_patients and "patientMatchProfile.identity.linkageStatus" not in query:
            query["patientMatchProfile.identity.linkageStatus"] = {"$ne": "conflict"}
        profile_docs = list(patient_collections.profiles.find(query))
        facts_docs = list(
            patient_collections.facts.find(
                {"patientRef": {"$in": [str(doc.get("patientRef") or "").strip() for doc in profile_docs if str(doc.get("patientRef") or "").strip()]}}
            )
        )
        facts_by_ref = {str(doc.get("patientRef") or "").strip(): doc for doc in facts_docs}
        bundles = []
        for profile_doc in profile_docs:
            patient_ref = str(profile_doc.get("patientRef") or "").strip()
            if not patient_ref:
                continue
            bundles.append(
                _patient_bundle_from_documents(
                    patient_ref=patient_ref,
                    facts_doc=facts_by_ref.get(patient_ref),
                    profile_doc=profile_doc,
                )
            )
        return bundles

    def _build_blocked_patient_run(
        self,
        *,
        patient_bundle: dict,
        population_mode: str,
        requested_stage_target_window: str | None = None,
        trial_ids: list[str] | None = None,
        run_type: str = "interactive",
        benchmark_pack_id: str | None = None,
        benchmark_label: str | None = None,
    ) -> dict:
        readiness = _patient_runtime_readiness(patient_bundle)
        trial_scope = _build_trial_scope(
            patient_bundle=patient_bundle,
            requested_stage_target_window=requested_stage_target_window,
            trial_ids=trial_ids,
        )
        run_document = {
            "_id": self._build_run_id(),
            "direction": "patient_to_trials",
            "runType": run_type,
            "patientRef": patient_bundle.get("patientRef"),
            "trialIds": [],
            "runStatus": "blocked",
            "runtimeMode": CANONICAL_RUNTIME_MODE,
            "populationMode": population_mode,
            "legacyBehaviorAllowed": LEGACY_BEHAVIOR_ALLOWED,
            "legacyInputsUsed": [],
            "contractVersions": _match_run_contract_versions(patient_bundle=patient_bundle),
            "contractMetadata": {"trialContracts": {}, "trialCount": 0},
            "trialScope": trial_scope,
            "catalogCount": 0,
            "evaluatedCount": 0,
            "startedAt": utcnow(),
            "finishedAt": utcnow(),
            "prefilter": {
                "candidateTrialCount": 0,
                "scannedTrialCount": 0,
            },
            "summary": self._summarize_results([]),
            "ruleTraversalSummary": _rule_traversal_summary_from_results([]),
            "results": [],
            "blocked": {
                "overall": _blocked_run_status_for_patient(patient_bundle, population_mode=population_mode),
                "reason": readiness.get("reasonIfBlocked")
                or (
                    "patient_not_in_pharmacy_prescreen_pool"
                    if population_mode == POPULATION_MODE_PHARMACY
                    else "patient_not_in_authoritative_oncology_pool"
                ),
            },
            "patient": {
                "patientRef": patient_bundle.get("patientRef"),
                "patientName": patient_bundle.get("patientName"),
                "linkageStatus": patient_bundle.get("linkageStatus"),
                "coveragePct": _get_path(patient_bundle.get("patientMatchProfile"), "coverageSnapshot.coveragePct"),
                "runtimeReadiness": readiness,
                "cohortMembership": _get_path(patient_bundle.get("patientMatchProfile"), "cohortMembership") or {},
            },
            "error": None,
        }
        if benchmark_pack_id or benchmark_label:
            run_document["benchmark"] = {
                "packId": benchmark_pack_id,
                "label": benchmark_label,
            }
        return run_document

    def _summarize_results(self, results: list[dict]) -> dict:
        counts = {
            "candidate": 0,
            "excluded": 0,
            "needs_more_data": 0,
            "manual_review_required": 0,
            "needs_identity_resolution": 0,
        }
        for row in results:
            overall = row.get("overall")
            if overall in counts:
                counts[overall] += 1
        return counts


matcher_service = ClinicalTrialMatcherService()
