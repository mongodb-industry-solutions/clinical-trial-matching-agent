from __future__ import annotations

from collections import Counter, defaultdict
import re
from statistics import mean
from typing import Any

from app.database import collections, patient_collections
from app.services.compiler import (
    TRIAL_COMPILER_SUPPORTED_FAMILY_IDS,
    get_effective_computability,
    get_effective_compiled_rule,
)
from app.services.helpers import normalize_token, serialize_document, utcnow
from app.services.matcher import MATCHER_SUPPORTED_FAMILY_IDS


CORE_FAMILY_IDS = [
    "demographics.age.min",
    "demographics.sex.allowed",
    "oncology.primary_cancer.required",
    "biomarkers.ER.status.required",
    "biomarkers.HER2.status.required",
]
BENCHMARK_MATCH_READY_CRITERIA = [
    "authoritativeBreastOncology",
    "runtimeReadiness.benchmarkRunnable",
    "noCriticalConflicts",
    "primaryCancerResolved",
    "ERKnown",
    "HER2Known",
    "treatmentHistoryPresent",
    "usableLabsPresent",
]
OUTREACH_READY_CRITERIA = [
    "authoritativeBreastOncology",
    "retailOutreachReady",
    "runtimeReadiness.outreachRunnable",
    "linked",
    "noCriticalConflicts",
]
BENCHMARK_GATE_THRESHOLDS = {
    "minMatchReadyPatients": 25,
    "minRuntimeReadyTrials": 10,
}
AUTHORITATIVE_COHORT_CATEGORY = "confirmed_breast_oncology"
AUTHORITATIVE_COHORT_TIERS = {"ready"}
STAGE_TARGET_WINDOW_ORDER = [
    "stage_ii_iii_or_locally_advanced",
    "advanced_or_metastatic",
    "mixed_or_manual",
    "not_stage_specific",
    "unknown",
]


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


def _pct(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((float(count) / float(total)) * 100.0, 1)


def _is_known_status(value: Any) -> bool:
    status = normalize_token(value)
    return bool(status) and status not in {"unknown", "conflicting"}


def _value_is_populated(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        if "status" in value:
            return _is_known_status(value.get("status"))
        if "resolvedValue" in value:
            if _is_known_status(value.get("status")):
                return True
            return _value_is_populated(value.get("resolvedValue"))
        if "terms" in value:
            return _value_is_populated(value.get("terms"))
        return any(_value_is_populated(item) for item in value.values())
    if isinstance(value, list):
        return any(_value_is_populated(item) for item in value)
    if isinstance(value, str):
        normalized = normalize_token(value)
        return bool(normalized) and normalized != "unknown"
    return True


def _resolved_feature_known(evidence_profile: dict, feature_key: str) -> bool:
    feature = _get_path(evidence_profile, f"resolvedFeatures.{feature_key}") or {}
    return _value_is_populated(feature)


def _family_patient_coverage(match_profile: dict, evidence_profile: dict, family_doc: dict) -> bool:
    patient_support = (family_doc or {}).get("patientSupport") or {}
    paths = list(patient_support.get("evidencePaths") or [])
    prefilter_path = patient_support.get("prefilterPath")
    if prefilter_path:
        paths.append(prefilter_path)
    for path in paths:
        raw_path = str(path or "").strip()
        if not raw_path:
            continue
        if raw_path.startswith("patientEvidenceProfile."):
            value = _get_path(evidence_profile, raw_path.removeprefix("patientEvidenceProfile."))
        elif raw_path.startswith("patientMatchProfile."):
            value = _get_path(match_profile, raw_path.removeprefix("patientMatchProfile."))
        else:
            value = None
        if _value_is_populated(value):
            return True
    return False


def _effective_family_id(node: dict) -> str | None:
    review = node.get("review") or {}
    compiler = node.get("compiler") or {}
    family_id = review.get("final_family_id") or compiler.get("selected_family_id")
    text = str(family_id or "").strip()
    return text or None


def _is_current_trial_criterion(node: dict, current_versions: dict[str, str]) -> bool:
    trial_id = str(node.get("trial_id") or "").strip()
    version_id = str(node.get("version_id") or "").strip()
    return bool(trial_id and version_id and current_versions.get(trial_id) == version_id)


def _top_counter_rows(counter: Counter, *, limit: int = 12) -> list[dict]:
    return [
        {"reason": key, "count": count}
        for key, count in counter.most_common(limit)
    ]


def _normalize_stage_target_window(value: Any, *, default: str = "unknown") -> str:
    normalized = normalize_token(value)
    if normalized in {
        "stage_ii_iii_or_locally_advanced",
        "advanced_or_metastatic",
        "mixed_or_manual",
        "not_stage_specific",
    }:
        return normalized
    return default


def _patient_stage_target_window(match_profile: dict) -> str:
    return _normalize_stage_target_window(
        _get_path(match_profile, "runtimeReadiness.targetDiseaseWindow")
        or _get_path(match_profile, "matchFilters.diseaseWindow")
        or _get_path(match_profile, "oncologySummary.stageTreatmentPosture.diseaseWindow"),
        default="unknown",
    )


def _trial_stage_target_window(trial_doc: dict) -> str:
    return _normalize_stage_target_window(
        _get_path(trial_doc, "stage_relevance.targetWindow"),
        default="not_stage_specific",
    )


def _effective_stage_target_windows(target_window: str) -> list[str]:
    normalized = _normalize_stage_target_window(target_window)
    if normalized in {"stage_ii_iii_or_locally_advanced", "advanced_or_metastatic"}:
        return [normalized, "mixed_or_manual"]
    return [normalized]


def _ordered_stage_window_rows(window_counter_map: dict[str, Counter], *, value_keys: list[str]) -> list[dict]:
    rows: list[dict] = []
    seen = set()
    ordered_windows = list(STAGE_TARGET_WINDOW_ORDER) + sorted(
        key for key in window_counter_map.keys() if key not in STAGE_TARGET_WINDOW_ORDER
    )
    for target_window in ordered_windows:
        if target_window in seen or target_window not in window_counter_map:
            continue
        seen.add(target_window)
        counter = window_counter_map.get(target_window) or Counter()
        row = {"targetWindow": target_window}
        for key in value_keys:
            row[key] = int(counter.get(key) or 0)
        rows.append(row)
    return rows


def _recommended_stage_window(overlap_rows: list[dict]) -> str | None:
    overlap_map = {
        str(row.get("targetWindow") or "").strip(): row
        for row in overlap_rows
        if str(row.get("targetWindow") or "").strip()
    }
    for target_window in STAGE_TARGET_WINDOW_ORDER:
        row = overlap_map.get(target_window) or {}
        if int(row.get("authoritativePatients") or 0) > 0 and int(row.get("runtimeReadyTrials") or 0) > 0:
            return target_window
    for target_window in STAGE_TARGET_WINDOW_ORDER:
        row = overlap_map.get(target_window) or {}
        if int(row.get("patientCount") or 0) > 0 and int(row.get("trialCount") or 0) > 0:
            return target_window
    return None


def _profile_sort_key(match_profile: dict, evidence_profile: dict) -> tuple[Any, ...]:
    coverage = float(((match_profile.get("coverageSnapshot") or {}).get("coveragePct")) or 0.0)
    readiness_tier = str(((match_profile.get("oncologyCohort") or {}).get("matchingReadinessTier")) or "")
    linkage_status = str(((match_profile.get("identity") or {}).get("linkageStatus")) or "")
    patient_ref = str(match_profile.get("patientRef") or evidence_profile.get("patientRef") or "")
    tier_rank = {"ready": 0, "partial": 1, "limited": 2, "": 3}.get(readiness_tier, 4)
    linkage_rank = {"linked": 0, "unlinked": 1, "conflict": 2}.get(linkage_status, 3)
    return (tier_rank, linkage_rank, -coverage, patient_ref)


def _benchmark_runnable(match_profile: dict) -> bool:
    readiness = (match_profile.get("runtimeReadiness") or {})
    if "benchmarkRunnable" in readiness:
        return bool(readiness.get("benchmarkRunnable"))
    return bool(readiness.get("runnable"))


def _outreach_runnable(match_profile: dict) -> bool:
    readiness = (match_profile.get("runtimeReadiness") or {})
    if "outreachRunnable" in readiness:
        return bool(readiness.get("outreachRunnable"))
    return bool(readiness.get("runnable"))


def _retail_outreach_ready(match_profile: dict) -> bool:
    return bool(((match_profile.get("cohortMembership") or {}).get("retailOutreachReady")))


def _match_ready_reasons(match_profile: dict, evidence_profile: dict) -> list[str]:
    reasons: list[str] = []
    cohort = (match_profile.get("cohortMembership") or {})
    readiness = (match_profile.get("runtimeReadiness") or {})

    if not cohort.get("authoritativeBreastOncology"):
        reasons.append("not_authoritative_breast_oncology")
    if not _benchmark_runnable(match_profile):
        reasons.append(f"benchmark_not_runnable::{str(readiness.get('reasonIfBenchmarkBlocked') or readiness.get('reasonIfBlocked') or 'unknown').strip()}")
    if int(readiness.get("criticalConflictCount") or 0) > 0:
        reasons.append("critical_conflicts_present")
    if not _resolved_feature_known(evidence_profile, "primaryCancer"):
        reasons.append("primary_cancer_unresolved")
    if not _resolved_feature_known(evidence_profile, "ER"):
        reasons.append("er_unknown")
    if not _resolved_feature_known(evidence_profile, "HER2"):
        reasons.append("her2_unknown")
    if not _resolved_feature_known(evidence_profile, "treatmentHistory"):
        reasons.append("treatment_history_missing")
    if not _resolved_feature_known(evidence_profile, "keyLabs"):
        reasons.append("usable_labs_missing")
    return reasons


def _outreach_ready_reasons(match_profile: dict, evidence_profile: dict) -> list[str]:
    reasons: list[str] = []
    cohort = (match_profile.get("cohortMembership") or {})
    readiness = (match_profile.get("runtimeReadiness") or {})
    identity = (match_profile.get("identity") or {})

    if not cohort.get("authoritativeBreastOncology"):
        reasons.append("not_authoritative_breast_oncology")
    if not _retail_outreach_ready(match_profile):
        reasons.append("not_retail_outreach_ready")
    if not _outreach_runnable(match_profile):
        reasons.append(f"outreach_not_runnable::{str(readiness.get('reasonIfOutreachBlocked') or readiness.get('reasonIfBlocked') or 'unknown').strip()}")
    if str(readiness.get("linkageStatus") or identity.get("linkageStatus") or "").strip().lower() != "linked":
        reasons.append("linkage_not_linked")
    if int(readiness.get("criticalConflictCount") or identity.get("conflictCount") or 0) > 0:
        reasons.append("critical_conflicts_present")
    return reasons


def _authoritative_blocker_reasons(match_profile: dict, evidence_profile: dict) -> list[str]:
    if bool(((match_profile.get("cohortMembership") or {}).get("authoritativeBreastOncology"))):
        return []
    reasons = _authoritative_rejected_reasons(match_profile, evidence_profile)
    return reasons or ["not_in_authoritative_oncology_cohort"]


def _authoritative_gate_snapshot(match_profile: dict, evidence_profile: dict) -> dict:
    identity = (match_profile.get("identity") or {})
    readiness = (match_profile.get("runtimeReadiness") or {})
    oncology_cohort = (match_profile.get("oncologyCohort") or {})
    oncology_assessment = (match_profile.get("oncologyCohortAssessment") or {})
    cohort = (match_profile.get("cohortMembership") or {})

    linkage_status = str(identity.get("linkageStatus") or readiness.get("linkageStatus") or "unlinked").strip().lower() or "unlinked"
    critical_conflict_count = int(readiness.get("criticalConflictCount") or identity.get("conflictCount") or 0)
    candidate_category = str(
        oncology_assessment.get("candidateCategory")
        or oncology_cohort.get("candidateCategory")
        or ""
    ).strip().lower()
    readiness_tier = str(
        oncology_assessment.get("matchingReadinessTier")
        or oncology_cohort.get("matchingReadinessTier")
        or ""
    ).strip().lower()

    primary_cancer_present = _resolved_feature_known(evidence_profile, "primaryCancer")
    er_known = _resolved_feature_known(evidence_profile, "ER")
    pr_known = _resolved_feature_known(evidence_profile, "PR")
    her2_known = _resolved_feature_known(evidence_profile, "HER2")
    stage_known = any(
        [
            _resolved_feature_known(evidence_profile, "stageGroup"),
            _resolved_feature_known(evidence_profile, "metastaticFlag"),
            _resolved_feature_known(evidence_profile, "diseaseExtent"),
        ]
    )
    ecog_known = _resolved_feature_known(evidence_profile, "ECOG")
    treatment_known = _resolved_feature_known(evidence_profile, "treatmentHistory")

    oncology_feature_depth = sum(
        1
        for present in [
            primary_cancer_present,
            any([er_known, pr_known, her2_known]),
            stage_known,
            ecog_known,
            treatment_known,
        ]
        if present
    )
    fallback_authoritative_eligible = bool(
        critical_conflict_count == 0
        and primary_cancer_present
        and oncology_feature_depth >= 3
        and any([er_known or pr_known or her2_known, stage_known, treatment_known])
    )

    gates = (oncology_assessment.get("authoritativeGateSnapshot") or {}) if isinstance(oncology_assessment, dict) else {}
    if not gates:
        gates = {
            "linkageLinked": linkage_status == "linked",
            "noCriticalConflicts": critical_conflict_count == 0,
            "primaryCancerPresent": primary_cancer_present,
            "candidateCategoryConfirmed": candidate_category == AUTHORITATIVE_COHORT_CATEGORY,
            "readinessTierAuthoritative": readiness_tier in AUTHORITATIVE_COHORT_TIERS,
            "fallbackAuthoritativeEligible": fallback_authoritative_eligible,
        }

    return {
        "authoritativeBreastOncology": bool(cohort.get("authoritativeBreastOncology")),
        "retailOutreachReady": bool(cohort.get("retailOutreachReady")),
        "pharmacyPrescreen": bool(cohort.get("pharmacyPrescreen")),
        "screeningOnly": bool(cohort.get("screeningOnly")),
        "confirmedBreastOncology": bool(
            oncology_assessment.get("confirmedBreastOncology")
            if "confirmedBreastOncology" in oncology_assessment
            else candidate_category == AUTHORITATIVE_COHORT_CATEGORY
        ),
        "matchingReadinessTier": readiness_tier or None,
        "linkageStatus": linkage_status,
        "criticalConflictCount": critical_conflict_count,
        "identityTrust": str(((match_profile.get("identity") or {}).get("identityTrust")) or "").strip() or None,
        "benchmarkRunnable": _benchmark_runnable(match_profile),
        "outreachRunnable": _outreach_runnable(match_profile),
        "gates": gates,
    }


def _authoritative_rejected_reasons(match_profile: dict, evidence_profile: dict) -> list[str]:
    oncology_assessment = (match_profile.get("oncologyCohortAssessment") or {})
    persisted_reasons = list(oncology_assessment.get("authoritativeRejectedReasons") or [])
    if persisted_reasons or oncology_assessment.get("authoritativeBreastOncology"):
        return sorted(set(str(item).strip() for item in persisted_reasons if str(item).strip()))

    snapshot = _authoritative_gate_snapshot(match_profile, evidence_profile)
    if snapshot.get("authoritativeBreastOncology"):
        return []

    reasons: list[str] = []
    linkage_status = str(snapshot.get("linkageStatus") or "unlinked")
    critical_conflict_count = int(snapshot.get("criticalConflictCount") or 0)
    readiness_tier = str(snapshot.get("matchingReadinessTier") or "").strip().lower()
    gates = snapshot.get("gates") or {}
    oncology_cohort = (match_profile.get("oncologyCohort") or {})
    candidate_category = str(oncology_cohort.get("candidateCategory") or "").strip().lower()

    if linkage_status != "linked":
        reasons.append(f"linkage_status::{linkage_status}")
    if critical_conflict_count > 0:
        reasons.append("critical_conflicts_present")
    if not gates.get("primaryCancerPresent"):
        reasons.append("primary_cancer_not_present")
    if candidate_category and candidate_category != AUTHORITATIVE_COHORT_CATEGORY:
        reasons.append(f"candidate_category_not_confirmed::{candidate_category}")
    elif not candidate_category and not gates.get("fallbackAuthoritativeEligible"):
        reasons.append("candidate_category_missing_without_fallback")
    if candidate_category == AUTHORITATIVE_COHORT_CATEGORY and readiness_tier not in AUTHORITATIVE_COHORT_TIERS:
        reasons.append(f"readiness_tier_not_authoritative::{readiness_tier or 'unknown'}")
    if not candidate_category and not gates.get("fallbackAuthoritativeEligible"):
        reasons.append("fallback_authoritative_not_met")
    if (
        candidate_category == AUTHORITATIVE_COHORT_CATEGORY
        and readiness_tier in AUTHORITATIVE_COHORT_TIERS
        and linkage_status == "linked"
        and critical_conflict_count == 0
        and gates.get("primaryCancerPresent")
    ):
        reasons.append("cohort_rule_mismatch")
    if not reasons:
        reasons.append("authoritative_gate_unmet")
    return sorted(set(reasons))


def _outreach_rejected_reasons(match_profile: dict, evidence_profile: dict) -> list[str]:
    oncology_assessment = (match_profile.get("oncologyCohortAssessment") or {})
    persisted_reasons = list(oncology_assessment.get("outreachRejectedReasons") or [])
    if persisted_reasons or oncology_assessment.get("retailOutreachReady"):
        return sorted(set(str(item).strip() for item in persisted_reasons if str(item).strip()))

    if _retail_outreach_ready(match_profile):
        return []

    reasons = _outreach_ready_reasons(match_profile, evidence_profile)
    if not reasons:
        reasons.append("retail_outreach_gate_unmet")
    return sorted(set(reasons))


def _criterion_family_id(node: dict) -> str:
    review = node.get("review") or {}
    compiler = node.get("compiler") or {}
    return (
        str(((get_effective_compiled_rule(node) or {}).get("family")) or "").strip()
        or str(review.get("final_family_id") or "").strip()
        or str(compiler.get("selected_family_id") or "").strip()
        or str((((compiler.get("candidate_families") or [{}])[0]).get("family_id")) or "").strip()
        or "unassigned"
    )


def _criterion_triage_text(node: dict) -> str:
    ancestor_headers = [str(item).strip() for item in (node.get("ancestor_headers") or []) if str(item).strip()]
    raw_text = str(((node.get("raw") or {}).get("text")) or "").strip()
    normalized_text = str((((node.get("normalized") or {}).get("text")) or "")).strip()
    return normalize_token(" ".join(ancestor_headers + [raw_text or normalized_text]))


def _triage_contains(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, str(text or ""), flags=re.IGNORECASE))


def _classify_unassigned_cluster(node: dict) -> dict:
    text = _criterion_triage_text(node)
    section = str(node.get("section") or "").strip().lower() or "inclusion"

    if any(
        _triage_contains(text, pattern)
        for pattern in {
            r"\bhemoglobin\b",
            r"\bhaemoglobin\b",
            r"\bplatelet\b",
            r"\bplatelets\b",
            r"\bbilirubin\b",
            r"\bast\b",
            r"\balt\b",
            r"\bcreatinine\b",
            r"\banc\b",
            r"\bneutrophil count\b",
            r"\babsolute neutrophil\b",
            r"\bbone marrow function\b",
            r"\borgan function\b",
        }
    ):
        return {
            "clusterKey": "organ_function.lab_panel",
            "likelyFamilyId": "organ_function.lab_panel",
            "intendedDisposition": "deterministic_family",
        }

    prior_exposure_negative = any(
        phrase in text
        for phrase in {
            "prior treatment with",
            "previous treatment with",
            "must not have received",
            "no prior",
            "received prior",
            "prior exposure",
        }
    )
    if prior_exposure_negative:
        return {
            "clusterKey": "therapy.prior_exposure.prohibited_history",
            "likelyFamilyId": "therapy.prior_exposure.prohibited_history",
            "intendedDisposition": "deterministic_family",
        }

    if any(
        _triage_contains(text, pattern)
        for pattern in {
            r"\bpregnant\b",
            r"\bpregnancy\b",
            r"\bbreastfeeding\b",
            r"\bbreast feeding\b",
            r"\bnursing\b",
            r"\blactating\b",
            r"\bwocbp\b",
            r"\bchildbearing potential\b",
            r"\bcontraception\b",
            r"\bpregnancy test\b",
        }
    ):
        subtype = "pregnancy_breastfeeding"
        if any(term in text for term in {"contraception", "wocbp", "childbearing potential"}):
            subtype = "pregnancy_breastfeeding_contraception"
        return {
            "clusterKey": f"context.pregnancy_breastfeeding::{subtype}",
            "likelyFamilyId": "context.pregnancy_breastfeeding",
            "intendedDisposition": "questionnaire_fillable_family",
        }

    if any(
        _triage_contains(text, pattern)
        for pattern in {
            r"\binfection\b",
            r"\bhepatitis b\b",
            r"\bhepatitis c\b",
            r"\bhiv\b",
            r"\banti-tumor treatment\b",
            r"\banti tumor treatment\b",
            r"\binvestigational treatment\b",
            r"\binvestigational agents?\b",
            r"\bwithin 4 weeks\b",
            r"\bwithin 30 days\b",
            r"\bprior to the first dose\b",
        }
    ):
        subtype = "active_infection"
        if any(
            _triage_contains(text, pattern)
            for pattern in {
                r"\bwithin 4 weeks\b",
                r"\bwithin 30 days\b",
                r"\bprior to the first dose\b",
                r"\banti-tumor treatment\b",
                r"\banti tumor treatment\b",
                r"\binvestigational treatment\b",
                r"\binvestigational agents?\b",
            }
        ):
            subtype = "recent_systemic_therapy_window"
        if "infection" in text and subtype == "recent_systemic_therapy_window":
            subtype = "mixed_infection_and_recent_therapy"
        return {
            "clusterKey": f"context.infection_recent_systemic_therapy::{subtype}",
            "likelyFamilyId": "context.infection_recent_systemic_therapy",
            "intendedDisposition": "deterministic_family",
        }

    if any(
        phrase in text
        for phrase in {
            "significant concomitant illness",
            "clinically significant illness",
            "other serious illness",
            "serious physical or mental disease",
            "medical disorder or condition that",
            "investigator considers",
            "unsuitable for study",
        }
    ):
        return {
            "clusterKey": "concomitant_illness.significant",
            "likelyFamilyId": "concomitant_illness.significant",
            "intendedDisposition": "manual_family",
        }

    if any(
        phrase in text
        for phrase in {
            "contraindicated",
            "contraindication",
            "hypersensitivity",
            "allergic reaction",
            "allergy",
            "cannot receive",
            "not a candidate for",
        }
    ):
        return {
            "clusterKey": "manual.safety_contraindication_history",
            "likelyFamilyId": "manual.safety_contraindication_history",
            "intendedDisposition": "manual_family",
        }

    if any(
        _triage_contains(text, pattern)
        for pattern in {
            r"\bstage i-iii\b",
            r"\bstage i\b",
            r"\bstage ii\b",
            r"\bstage iii\b",
            r"\bstage 1\b",
            r"\bstage 2\b",
            r"\bstage 3\b",
            r"\bearly stage\b",
            r"\bresected invasive\b",
            r"\bresidual invasive disease\b",
            r"\bwithout evidence of distant metastases\b",
            r"\bnode-positive\b",
            r"\bnode positive\b",
            r"\bm0\b",
            r"\bn1\b",
            r"\bn2\b",
            r"\bn3\b",
        }
    ):
        return {
            "clusterKey": "manual.stage_extent_context",
            "likelyFamilyId": "manual.stage_extent_context",
            "intendedDisposition": "manual_family",
        }

    if any(
        phrase in text
        for phrase in {
            "locoregional recurrent",
            "locally advanced",
            "disease-free",
            "disease free",
            "high-risk",
            "high risk",
            "curative local therapy",
            "not amenable",
            "definitive surgery",
            "residual disease",
        }
    ):
        return {
            "clusterKey": "manual.complex_disease_setting",
            "likelyFamilyId": "manual.complex_disease_setting",
            "intendedDisposition": "manual_family",
        }

    if any(
        phrase in text
        for phrase in {
            "prior treatment with",
            "previous treatment with",
            "prior therapy",
            "previously treated",
            "prior exposure",
            "received prior",
            "must have received",
        }
    ):
        return {
            "clusterKey": "manual.prior_exposure_contextual",
            "likelyFamilyId": "manual.prior_exposure_contextual",
            "intendedDisposition": "manual_family",
        }

    if any(
        phrase in text
        for phrase in {
            "signed informed consent",
            "provide informed consent",
            "able to comply",
            "available for follow-up",
            "willing and able",
            "study procedures",
        }
    ):
        return {
            "clusterKey": "protocol_workflow.not_patient_rule",
            "likelyFamilyId": "protocol_workflow.not_patient_rule",
            "intendedDisposition": "not_patient_rule",
        }

    fallback_family_id = "manual.exclusion_context_other" if section == "exclusion" else "manual.inclusion_context_other"
    return {
        "clusterKey": f"manual.section_fallback::{section}",
        "likelyFamilyId": fallback_family_id,
        "intendedDisposition": "manual_family",
    }


def _build_unassigned_triage_rows(current_criteria: list[dict]) -> dict:
    cluster_rows: dict[str, dict] = {}
    disposition_counter = Counter()
    total_unassigned = 0

    for node in current_criteria:
        review = node.get("review") or {}
        if str(review.get("status") or "").strip() != "pending":
            continue
        if str(node.get("node_type") or "").strip() not in {"leaf", "synthetic_structured"}:
            continue
        if _criterion_family_id(node) != "unassigned":
            continue

        total_unassigned += 1
        classification = _classify_unassigned_cluster(node)
        cluster_key = str(classification.get("clusterKey") or "unassigned.misc").strip()
        likely_family_id = str(classification.get("likelyFamilyId") or "unassigned.miscellaneous").strip()
        intended_disposition = str(classification.get("intendedDisposition") or "manual_family").strip()
        disposition_counter[intended_disposition] += 1

        row = cluster_rows.setdefault(
            cluster_key,
            {
                "clusterKey": cluster_key,
                "likelyFamilyId": likely_family_id,
                "intendedDisposition": intended_disposition,
                "frequency": 0,
                "sectionCounts": Counter(),
                "trialIds": set(),
                "sampleCriterionText": None,
                "sampleCriterionId": None,
            },
        )
        row["frequency"] += 1
        row["sectionCounts"][str(node.get("section") or "").strip().lower() or "inclusion"] += 1
        trial_id = str(node.get("trial_id") or "").strip()
        if trial_id:
            row["trialIds"].add(trial_id)
        if row["sampleCriterionText"] is None:
            row["sampleCriterionText"] = str(((node.get("raw") or {}).get("text")) or "").strip()
            row["sampleCriterionId"] = str(node.get("_id") or "").strip() or None

    cluster_payload = []
    for row in cluster_rows.values():
        cluster_payload.append(
            {
                "clusterKey": row["clusterKey"],
                "likelyFamilyId": row["likelyFamilyId"],
                "intendedDisposition": row["intendedDisposition"],
                "frequency": int(row["frequency"]),
                "sampleCriterionText": row["sampleCriterionText"],
                "sampleCriterionId": row["sampleCriterionId"],
                "exampleTrialIds": sorted(row["trialIds"])[:8],
                "sectionCounts": {
                    "inclusion": int(row["sectionCounts"].get("inclusion") or 0),
                    "exclusion": int(row["sectionCounts"].get("exclusion") or 0),
                },
            }
        )
    cluster_payload.sort(key=lambda item: (-1 * int(item.get("frequency") or 0), str(item.get("clusterKey") or "")))

    return {
        "totalRows": total_unassigned,
        "summaryByDisposition": [
            {"disposition": disposition, "count": count}
            for disposition, count in disposition_counter.most_common()
        ],
        "clusters": cluster_payload,
    }


def _patient_impacting_nodes_for_trial(nodes: list[dict]) -> list[dict]:
    patient_nodes = []
    for node in nodes:
        if str(node.get("node_type") or "").strip() not in {"leaf", "synthetic_structured"}:
            continue
        computability = str(get_effective_computability(node) or "").strip()
        if computability == "not_patient_rule":
            continue
        patient_nodes.append(node)
    return patient_nodes


def _trial_runtime_blocked_by(trial_doc: dict, trial_nodes: list[dict], family_docs_by_id: dict[str, dict]) -> list[str]:
    runtime_readiness = (trial_doc.get("runtimeReadiness") or {})
    blocked_by: list[str] = []
    reason_if_blocked = str(runtime_readiness.get("reasonIfBlocked") or "").strip()
    if reason_if_blocked:
        blocked_by.append(f"runtime_gate::{reason_if_blocked}")
    if runtime_readiness.get("hasExclusionParseGap"):
        blocked_by.append("gate::exclusion_parse_gap")
    if int(runtime_readiness.get("manualPatientImpactingCount") or 0) > 0:
        blocked_by.append(f"gate::manual_patient_impacting::{int(runtime_readiness.get('manualPatientImpactingCount') or 0)}")
    if int(runtime_readiness.get("unsupportedPatientImpactingCount") or 0) > 0:
        blocked_by.append(f"gate::unsupported_patient_impacting::{int(runtime_readiness.get('unsupportedPatientImpactingCount') or 0)}")

    unsupported_counter = Counter()
    manual_counter = Counter()
    exclusion_gap_counter = Counter()
    unresolved_counter = Counter()

    for node in _patient_impacting_nodes_for_trial(trial_nodes):
        family_id = _criterion_family_id(node)
        review = node.get("review") or {}
        section = str(node.get("section") or "").strip().lower()
        computability = str(get_effective_computability(node) or "").strip()
        patient_support = ((family_docs_by_id.get(family_id) or {}).get("patientSupport") or {})
        supported = bool(patient_support.get("supported"))

        if computability == "deterministic":
            if not supported:
                unsupported_counter[family_id] += 1
        else:
            manual_counter[family_id] += 1
            if section == "exclusion":
                exclusion_gap_counter[family_id] += 1

        if str(review.get("status") or "").strip() == "pending":
            unresolved_counter[family_id] += 1

    for family_id, count in unsupported_counter.most_common(8):
        blocked_by.append(f"unsupported_family:{family_id}:{count}")
    for family_id, count in exclusion_gap_counter.most_common(8):
        blocked_by.append(f"exclusion_gap_family:{family_id}:{count}")
    for family_id, count in manual_counter.most_common(8):
        blocked_by.append(f"manual_family:{family_id}:{count}")
    for family_id, count in unresolved_counter.most_common(8):
        blocked_by.append(f"unresolved_family:{family_id}:{count}")

    deduped = []
    seen = set()
    for item in blocked_by:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _family_coverage_rows(family_docs: list[dict], authoritative_pairs: list[tuple[dict, dict]], current_criteria: list[dict]) -> list[dict]:
    usage_counts = Counter()
    deterministic_usage_counts = Counter()
    pending_usage_counts = Counter()
    for row in current_criteria:
        family_id = _effective_family_id(row)
        if not family_id:
            continue
        usage_counts[family_id] += 1
        if str((((row.get("compiler") or {}).get("computability")) or "")).strip() == "deterministic":
            deterministic_usage_counts[family_id] += 1
        if str((((row.get("review") or {}).get("status")) or "")).strip() == "pending":
            pending_usage_counts[family_id] += 1

    payload = []
    total_authoritative = len(authoritative_pairs)
    for family_doc in family_docs:
        family_id = str(family_doc.get("family_id") or "").strip()
        if not family_id:
            continue
        coverage_count = sum(
            1 for match_profile, evidence_profile in authoritative_pairs
            if _family_patient_coverage(match_profile, evidence_profile, family_doc)
        )
        payload.append(
            {
                "familyId": family_id,
                "trialCompilerSupport": family_id in TRIAL_COMPILER_SUPPORTED_FAMILY_IDS,
                "patientFeatureSupport": bool(((family_doc.get("patientSupport") or {}).get("supported"))),
                "matcherEvaluatorExists": family_id in MATCHER_SUPPORTED_FAMILY_IDS,
                "authoritativePopulationCoveragePct": _pct(coverage_count, total_authoritative),
                "enoughPatientsPopulated": _pct(coverage_count, total_authoritative) >= 25.0,
                "trialUsageCount": int(usage_counts.get(family_id) or 0),
                "trialDeterministicUsageCount": int(deterministic_usage_counts.get(family_id) or 0),
                "trialPendingUsageCount": int(pending_usage_counts.get(family_id) or 0),
            }
        )
    payload.sort(key=lambda row: (-1 * int(row.get("trialUsageCount") or 0), str(row.get("familyId") or "")))
    return payload


def build_corpus_proof_report(
    *,
    patient_facts_docs: list[dict],
    patient_profile_docs: list[dict],
    trial_docs: list[dict],
    current_criteria_docs: list[dict],
    rule_family_docs: list[dict],
    query: dict,
    git_commit: str,
    trial_runtime_tag: str,
    patient_rebuild_summary: dict,
    trial_refresh_summary: dict,
) -> dict:
    facts_by_ref = {
        str(doc.get("patientRef") or "").strip(): serialize_document(doc)
        for doc in patient_facts_docs
        if str(doc.get("patientRef") or "").strip()
    }
    profiles_by_ref = {
        str(doc.get("patientRef") or "").strip(): serialize_document(doc)
        for doc in patient_profile_docs
        if str(doc.get("patientRef") or "").strip()
    }
    patient_refs = sorted(set(list(facts_by_ref.keys()) + list(profiles_by_ref.keys())))

    population_counter = Counter()
    linkage_counter = Counter()
    population_linkage = defaultdict(Counter)
    authoritative_pairs: list[tuple[dict, dict]] = []
    authoritative_counter = 0
    retail_outreach_counter = 0
    pharmacy_counter = 0
    screening_counter = 0

    authoritative_blockers = Counter()
    outreach_blockers = Counter()
    match_ready_blockers = Counter()
    match_ready_patient_refs: list[str] = []
    authoritative_patient_refs: list[str] = []
    retail_outreach_patient_refs: list[str] = []
    patient_stage_windows = defaultdict(Counter)

    authoritative_primary_cancer = 0
    authoritative_er = 0
    authoritative_pr = 0
    authoritative_her2 = 0
    authoritative_stage = 0
    authoritative_ecog = 0
    authoritative_treatment = 0
    authoritative_labs = 0
    authoritative_disease_progression = 0
    authoritative_clean_linkage = 0

    for patient_ref in patient_refs:
        facts_doc = facts_by_ref.get(patient_ref) or {}
        profile_doc = profiles_by_ref.get(patient_ref) or {}
        evidence_profile = (facts_doc.get("patientEvidenceProfile") or {})
        match_profile = (profile_doc.get("patientMatchProfile") or {})
        cohort_membership = match_profile.get("cohortMembership") or {}
        readiness = match_profile.get("runtimeReadiness") or {}
        identity = match_profile.get("identity") or {}
        linkage_status = str(identity.get("linkageStatus") or readiness.get("linkageStatus") or "unlinked").strip().lower() or "unlinked"
        patient_target_window = _patient_stage_target_window(match_profile)

        patient_stage_windows[patient_target_window]["count"] += 1
        if cohort_membership.get("authoritativeBreastOncology"):
            patient_stage_windows[patient_target_window]["authoritativeCount"] += 1
        if _benchmark_runnable(match_profile):
            patient_stage_windows[patient_target_window]["benchmarkRunnableCount"] += 1

        if cohort_membership.get("authoritativeBreastOncology"):
            population_key = "authoritativeBreastOncology"
            authoritative_counter += 1
            authoritative_pairs.append((match_profile, evidence_profile))
            authoritative_patient_refs.append(patient_ref)
            authoritative_primary_cancer += int(_resolved_feature_known(evidence_profile, "primaryCancer"))
            authoritative_er += int(_resolved_feature_known(evidence_profile, "ER"))
            authoritative_pr += int(_resolved_feature_known(evidence_profile, "PR"))
            authoritative_her2 += int(_resolved_feature_known(evidence_profile, "HER2"))
            authoritative_stage += int(
                _resolved_feature_known(evidence_profile, "stageGroup")
                or _resolved_feature_known(evidence_profile, "metastaticFlag")
                or _resolved_feature_known(evidence_profile, "diseaseExtent")
            )
            authoritative_ecog += int(_resolved_feature_known(evidence_profile, "ECOG"))
            authoritative_treatment += int(_resolved_feature_known(evidence_profile, "treatmentHistory"))
            authoritative_labs += int(_resolved_feature_known(evidence_profile, "keyLabs"))
            authoritative_disease_progression += int(_resolved_feature_known(evidence_profile, "diseaseStatus"))
            authoritative_clean_linkage += int(linkage_status == "linked" and int(readiness.get("criticalConflictCount") or identity.get("conflictCount") or 0) == 0)

            readiness_reasons = _match_ready_reasons(match_profile, evidence_profile)
            if not readiness_reasons:
                match_ready_patient_refs.append(patient_ref)
            else:
                for reason in set(readiness_reasons):
                    match_ready_blockers[reason] += 1
            outreach_reasons = _outreach_rejected_reasons(match_profile, evidence_profile)
            if not outreach_reasons:
                retail_outreach_counter += 1
                retail_outreach_patient_refs.append(patient_ref)
            else:
                for reason in set(outreach_reasons):
                    outreach_blockers[reason] += 1
        elif cohort_membership.get("screeningOnly"):
            population_key = "screeningOnly"
            screening_counter += 1
            for reason in set(_authoritative_blocker_reasons(match_profile, evidence_profile)):
                authoritative_blockers[reason] += 1
            for reason in set(_outreach_rejected_reasons(match_profile, evidence_profile)):
                outreach_blockers[reason] += 1
        else:
            population_key = "pharmacyPrescreen"
            pharmacy_counter += 1
            for reason in set(_authoritative_blocker_reasons(match_profile, evidence_profile)):
                authoritative_blockers[reason] += 1
            for reason in set(_outreach_rejected_reasons(match_profile, evidence_profile)):
                outreach_blockers[reason] += 1

        population_counter[population_key] += 1
        linkage_counter[linkage_status] += 1
        population_linkage[population_key][linkage_status] += 1

    match_ready_patient_ref_set = set(match_ready_patient_refs)
    retail_outreach_patient_ref_set = set(retail_outreach_patient_refs)
    for patient_ref in patient_refs:
        profile_doc = profiles_by_ref.get(patient_ref) or {}
        match_profile = (profile_doc.get("patientMatchProfile") or {})
        patient_target_window = _patient_stage_target_window(match_profile)
        if patient_ref in match_ready_patient_ref_set:
            patient_stage_windows[patient_target_window]["matchReadyCount"] += 1
        if patient_ref in retail_outreach_patient_ref_set:
            patient_stage_windows[patient_target_window]["retailOutreachReadyCount"] += 1

    authoritative_total = len(authoritative_pairs)
    trial_rows = [serialize_document(row) for row in trial_docs]
    current_versions = {str(row.get("_id") or ""): str(row.get("current_version_id") or "") for row in trial_rows}
    current_criteria = [
        serialize_document(row)
        for row in current_criteria_docs
        if _is_current_trial_criterion(serialize_document(row), current_versions)
    ]

    family_rows = _family_coverage_rows(
        [serialize_document(row) for row in rule_family_docs],
        authoritative_pairs,
        current_criteria,
    )
    family_rows_by_id = {row["familyId"]: row for row in family_rows}

    runtime_ready_trials = [row for row in trial_rows if bool(((row.get("runtimeReadiness") or {}).get("runnable")))]
    runtime_ready_deterministic_trials = [
        row for row in trial_rows
        if str(((row.get("runtimeReadiness") or {}).get("runtimeState")) or "") == "runtime_ready_deterministic"
    ]
    runtime_ready_with_manual_trials = [
        row for row in trial_rows
        if str(((row.get("runtimeReadiness") or {}).get("runtimeState")) or "") == "runtime_ready_with_manual"
    ]
    blocked_trials = [row for row in trial_rows if not bool(((row.get("runtimeReadiness") or {}).get("runnable")))]
    trial_stage_windows = defaultdict(Counter)
    stage_specific_trials = 0
    stage_specific_runtime_ready_trials = 0
    manual_stage_context_trials = 0
    manual_complex_setting_trials = 0
    for row in trial_rows:
        runtime_readiness = row.get("runtimeReadiness") or {}
        stage_relevance = row.get("stage_relevance") or {}
        target_window = _trial_stage_target_window(row)
        trial_stage_windows[target_window]["count"] += 1
        if bool(runtime_readiness.get("runnable")):
            trial_stage_windows[target_window]["runtimeReadyCount"] += 1
        runtime_state = str(runtime_readiness.get("runtimeState") or "").strip()
        if runtime_state == "runtime_ready_deterministic":
            trial_stage_windows[target_window]["runtimeReadyDeterministicCount"] += 1
        if runtime_state == "runtime_ready_with_manual":
            trial_stage_windows[target_window]["runtimeReadyWithManualCount"] += 1
        if str(((row.get("compile") or {}).get("status")) or "") == "ready_with_review":
            trial_stage_windows[target_window]["readyWithReviewCount"] += 1
        if bool(stage_relevance.get("manualReviewRequired")):
            trial_stage_windows[target_window]["manualReviewRequiredCount"] += 1
        if target_window not in {"not_stage_specific", "unknown"}:
            stage_specific_trials += 1
            if bool(runtime_readiness.get("runnable")):
                stage_specific_runtime_ready_trials += 1
        if int(stage_relevance.get("manualStageContextCount") or 0) > 0:
            manual_stage_context_trials += 1
        if int(stage_relevance.get("manualComplexSettingCount") or 0) > 0:
            manual_complex_setting_trials += 1
    trial_runtime_blockers = Counter(
        str(((row.get("runtimeReadiness") or {}).get("reasonIfBlocked")) or "unknown").strip()
        for row in blocked_trials
    )
    unresolved_trial_families = Counter()
    unresolved_trial_family_trials = defaultdict(set)
    for row in current_criteria:
        review = row.get("review") or {}
        compiler = row.get("compiler") or {}
        if str(review.get("status") or "").strip() != "pending":
            continue
        if str(row.get("node_type") or "").strip() not in {"leaf", "synthetic_structured"}:
            continue
        family_id = (
            str(review.get("final_family_id") or "").strip()
            or str(compiler.get("selected_family_id") or "").strip()
            or str((((compiler.get("candidate_families") or [{}])[0]).get("family_id")) or "").strip()
            or "unassigned"
        )
        unresolved_trial_families[family_id] += 1
        unresolved_trial_family_trials[family_id].add(str(row.get("trial_id") or "").strip())

    unassigned_triage = _build_unassigned_triage_rows(current_criteria)

    authoritative_profiles_sorted = sorted(
        [
            {
                "patientRef": patient_ref,
                "matchProfile": profiles_by_ref.get(patient_ref, {}).get("patientMatchProfile") or {},
                "evidenceProfile": facts_by_ref.get(patient_ref, {}).get("patientEvidenceProfile") or {},
            }
            for patient_ref in authoritative_patient_refs
        ],
        key=lambda row: _profile_sort_key(row["matchProfile"], row["evidenceProfile"]),
    )
    benchmark_authoritative_patient_refs = [row["patientRef"] for row in authoritative_profiles_sorted[:100]]
    benchmark_match_ready_refs = [row for row in benchmark_authoritative_patient_refs if row in set(match_ready_patient_refs)][:50]
    retail_outreach_candidate_refs = [row for row in benchmark_authoritative_patient_refs if row in set(retail_outreach_patient_refs)][:50]

    benchmark_trial_ids = [
        str(row.get("_id") or "").strip()
        for row in sorted(
            runtime_ready_trials,
            key=lambda trial: (
                -1 * int(((trial.get("runtimeReadiness") or {}).get("deterministicPatientImpactingCount") or 0)),
                int(((trial.get("runtimeReadiness") or {}).get("manualPatientImpactingCount") or 0)),
                str(((trial.get("display") or {}).get("last_ingested_at")) or ""),
                str(trial.get("_id") or ""),
            ),
        )[:15]
        if str(row.get("_id") or "").strip()
    ]

    core_family_overlap = []
    for family_id in CORE_FAMILY_IDS:
        row = family_rows_by_id.get(family_id)
        core_family_overlap.append(
            {
                "familyId": family_id,
                "trialCompilerSupport": bool((row or {}).get("trialCompilerSupport")),
                "patientFeatureSupport": bool((row or {}).get("patientFeatureSupport")),
                "matcherEvaluatorExists": bool((row or {}).get("matcherEvaluatorExists")),
                "authoritativePopulationCoveragePct": float((row or {}).get("authoritativePopulationCoveragePct") or 0.0),
            }
        )

    benchmark_gate_reasons = []
    if len(match_ready_patient_refs) < BENCHMARK_GATE_THRESHOLDS["minMatchReadyPatients"]:
        benchmark_gate_reasons.append("insufficient_match_ready_patients")
    if len(runtime_ready_trials) < BENCHMARK_GATE_THRESHOLDS["minRuntimeReadyTrials"]:
        benchmark_gate_reasons.append("insufficient_runtime_ready_trials")
    if not all(
        row.get("trialCompilerSupport") and row.get("patientFeatureSupport") and row.get("matcherEvaluatorExists")
        for row in core_family_overlap
    ):
        benchmark_gate_reasons.append("core_family_support_overlap_incomplete")

    patient_stage_window_rows = _ordered_stage_window_rows(
        patient_stage_windows,
        value_keys=[
            "count",
            "authoritativeCount",
            "benchmarkRunnableCount",
            "matchReadyCount",
            "retailOutreachReadyCount",
        ],
    )
    trial_stage_window_rows = _ordered_stage_window_rows(
        trial_stage_windows,
        value_keys=[
            "count",
            "runtimeReadyCount",
            "runtimeReadyDeterministicCount",
            "runtimeReadyWithManualCount",
            "readyWithReviewCount",
            "manualReviewRequiredCount",
        ],
    )
    patient_stage_window_map = {
        str(row.get("targetWindow") or "").strip(): row for row in patient_stage_window_rows
    }
    trial_stage_window_map = {
        str(row.get("targetWindow") or "").strip(): row for row in trial_stage_window_rows
    }
    exact_stage_overlap_rows = []
    seen_stage_windows = set()
    for target_window in STAGE_TARGET_WINDOW_ORDER:
        patient_row = patient_stage_window_map.get(target_window) or {}
        trial_row = trial_stage_window_map.get(target_window) or {}
        if not any(
            [
                int(patient_row.get("count") or 0),
                int(trial_row.get("count") or 0),
                int(patient_row.get("authoritativeCount") or 0),
                int(trial_row.get("runtimeReadyCount") or 0),
            ]
        ):
            continue
        seen_stage_windows.add(target_window)
        exact_stage_overlap_rows.append(
            {
                "targetWindow": target_window,
                "patientCount": int(patient_row.get("count") or 0),
                "authoritativePatients": int(patient_row.get("authoritativeCount") or 0),
                "matchReadyPatients": int(patient_row.get("matchReadyCount") or 0),
                "retailOutreachReadyPatients": int(patient_row.get("retailOutreachReadyCount") or 0),
                "trialCount": int(trial_row.get("count") or 0),
                "runtimeReadyTrials": int(trial_row.get("runtimeReadyCount") or 0),
                "runtimeReadyDeterministicTrials": int(trial_row.get("runtimeReadyDeterministicCount") or 0),
                "runtimeReadyWithManualTrials": int(trial_row.get("runtimeReadyWithManualCount") or 0),
            }
        )
    for target_window in sorted(set(patient_stage_window_map.keys()) | set(trial_stage_window_map.keys())):
        if target_window in seen_stage_windows:
            continue
        patient_row = patient_stage_window_map.get(target_window) or {}
        trial_row = trial_stage_window_map.get(target_window) or {}
        exact_stage_overlap_rows.append(
            {
                "targetWindow": target_window,
                "patientCount": int(patient_row.get("count") or 0),
                "authoritativePatients": int(patient_row.get("authoritativeCount") or 0),
                "matchReadyPatients": int(patient_row.get("matchReadyCount") or 0),
                "retailOutreachReadyPatients": int(patient_row.get("retailOutreachReadyCount") or 0),
                "trialCount": int(trial_row.get("count") or 0),
                "runtimeReadyTrials": int(trial_row.get("runtimeReadyCount") or 0),
                "runtimeReadyDeterministicTrials": int(trial_row.get("runtimeReadyDeterministicCount") or 0),
                "runtimeReadyWithManualTrials": int(trial_row.get("runtimeReadyWithManualCount") or 0),
            }
        )
    effective_stage_overlap_rows = []
    seen_effective_stage_windows = set()
    for target_window in STAGE_TARGET_WINDOW_ORDER:
        patient_row = patient_stage_window_map.get(target_window) or {}
        exact_trial_row = trial_stage_window_map.get(target_window) or {}
        supporting_windows = _effective_stage_target_windows(target_window)
        supporting_trial_rows = [trial_stage_window_map.get(window) or {} for window in supporting_windows]
        supporting_trial_count = sum(int(row.get("count") or 0) for row in supporting_trial_rows)
        supporting_runtime_ready_count = sum(int(row.get("runtimeReadyCount") or 0) for row in supporting_trial_rows)
        supporting_runtime_ready_deterministic_count = sum(
            int(row.get("runtimeReadyDeterministicCount") or 0) for row in supporting_trial_rows
        )
        supporting_runtime_ready_with_manual_count = sum(
            int(row.get("runtimeReadyWithManualCount") or 0) for row in supporting_trial_rows
        )
        mixed_manual_trial_row = trial_stage_window_map.get("mixed_or_manual") or {}
        mixed_manual_support_count = 0
        mixed_manual_runtime_ready_count = 0
        mixed_manual_runtime_ready_deterministic_count = 0
        mixed_manual_runtime_ready_with_manual_count = 0
        if target_window in {"stage_ii_iii_or_locally_advanced", "advanced_or_metastatic"}:
            mixed_manual_support_count = int(mixed_manual_trial_row.get("count") or 0)
            mixed_manual_runtime_ready_count = int(mixed_manual_trial_row.get("runtimeReadyCount") or 0)
            mixed_manual_runtime_ready_deterministic_count = int(
                mixed_manual_trial_row.get("runtimeReadyDeterministicCount") or 0
            )
            mixed_manual_runtime_ready_with_manual_count = int(
                mixed_manual_trial_row.get("runtimeReadyWithManualCount") or 0
            )
        if not any(
            [
                int(patient_row.get("count") or 0),
                supporting_trial_count,
                int(patient_row.get("authoritativeCount") or 0),
                supporting_runtime_ready_count,
            ]
        ):
            continue
        seen_effective_stage_windows.add(target_window)
        effective_stage_overlap_rows.append(
            {
                "targetWindow": target_window,
                "patientCount": int(patient_row.get("count") or 0),
                "authoritativePatients": int(patient_row.get("authoritativeCount") or 0),
                "matchReadyPatients": int(patient_row.get("matchReadyCount") or 0),
                "retailOutreachReadyPatients": int(patient_row.get("retailOutreachReadyCount") or 0),
                "trialCount": supporting_trial_count,
                "runtimeReadyTrials": supporting_runtime_ready_count,
                "runtimeReadyDeterministicTrials": supporting_runtime_ready_deterministic_count,
                "runtimeReadyWithManualTrials": supporting_runtime_ready_with_manual_count,
                "exactTrialCount": int(exact_trial_row.get("count") or 0),
                "exactRuntimeReadyTrials": int(exact_trial_row.get("runtimeReadyCount") or 0),
                "exactRuntimeReadyDeterministicTrials": int(
                    exact_trial_row.get("runtimeReadyDeterministicCount") or 0
                ),
                "exactRuntimeReadyWithManualTrials": int(exact_trial_row.get("runtimeReadyWithManualCount") or 0),
                "mixedOrManualSupportingTrialCount": mixed_manual_support_count,
                "mixedOrManualSupportingRuntimeReadyTrials": mixed_manual_runtime_ready_count,
                "mixedOrManualSupportingRuntimeReadyDeterministicTrials": mixed_manual_runtime_ready_deterministic_count,
                "mixedOrManualSupportingRuntimeReadyWithManualTrials": mixed_manual_runtime_ready_with_manual_count,
                "supportingTrialWindows": [window for window in supporting_windows if window],
            }
        )
    for target_window in sorted(set(patient_stage_window_map.keys()) | set(trial_stage_window_map.keys())):
        if target_window in seen_effective_stage_windows:
            continue
        patient_row = patient_stage_window_map.get(target_window) or {}
        trial_row = trial_stage_window_map.get(target_window) or {}
        effective_stage_overlap_rows.append(
            {
                "targetWindow": target_window,
                "patientCount": int(patient_row.get("count") or 0),
                "authoritativePatients": int(patient_row.get("authoritativeCount") or 0),
                "matchReadyPatients": int(patient_row.get("matchReadyCount") or 0),
                "retailOutreachReadyPatients": int(patient_row.get("retailOutreachReadyCount") or 0),
                "trialCount": int(trial_row.get("count") or 0),
                "runtimeReadyTrials": int(trial_row.get("runtimeReadyCount") or 0),
                "runtimeReadyDeterministicTrials": int(trial_row.get("runtimeReadyDeterministicCount") or 0),
                "runtimeReadyWithManualTrials": int(trial_row.get("runtimeReadyWithManualCount") or 0),
                "exactTrialCount": int(trial_row.get("count") or 0),
                "exactRuntimeReadyTrials": int(trial_row.get("runtimeReadyCount") or 0),
                "exactRuntimeReadyDeterministicTrials": int(trial_row.get("runtimeReadyDeterministicCount") or 0),
                "exactRuntimeReadyWithManualTrials": int(trial_row.get("runtimeReadyWithManualCount") or 0),
                "mixedOrManualSupportingTrialCount": 0,
                "mixedOrManualSupportingRuntimeReadyTrials": 0,
                "mixedOrManualSupportingRuntimeReadyDeterministicTrials": 0,
                "mixedOrManualSupportingRuntimeReadyWithManualTrials": 0,
                "supportingTrialWindows": [target_window] if target_window else [],
            }
        )
    recommended_target_window = _recommended_stage_window(effective_stage_overlap_rows)
    recommended_target_window_summary = next(
        (
            row
            for row in effective_stage_overlap_rows
            if str(row.get("targetWindow") or "").strip() == recommended_target_window
        ),
        None,
    )

    return {
        "generatedAt": serialize_document(utcnow()),
        "gitCommit": git_commit,
        "trialRuntimeTag": trial_runtime_tag,
        "query": query,
        "patientRebuild": patient_rebuild_summary,
        "trialRefresh": trial_refresh_summary,
        "populationSummary": {
            "totalPatients": len(patient_refs),
            "authoritativeBreastOncology": authoritative_counter,
            "retailOutreachReady": retail_outreach_counter,
            "pharmacyPrescreen": pharmacy_counter,
            "screeningOnly": screening_counter,
        },
        "linkageSummary": {
            "linked": int(linkage_counter.get("linked") or 0),
            "conflict": int(linkage_counter.get("conflict") or 0),
            "unlinked": int(linkage_counter.get("unlinked") or 0),
        },
        "populationBreakdown": {
            population_key: {
                "count": int(population_counter.get(population_key) or 0),
                "linked": int(population_linkage[population_key].get("linked") or 0),
                "conflict": int(population_linkage[population_key].get("conflict") or 0),
                "unlinked": int(population_linkage[population_key].get("unlinked") or 0),
            }
            for population_key in ["authoritativeBreastOncology", "pharmacyPrescreen", "screeningOnly"]
        },
        "workflowSummary": {
            "retailOutreachReady": retail_outreach_counter,
            "retailOutreachReadyPctOfAuthoritative": _pct(retail_outreach_counter, authoritative_total),
            "pharmacyPrescreen": pharmacy_counter,
            "screeningOnly": screening_counter,
        },
        "authoritativeCoverage": {
            "primaryCancerResolvedPct": _pct(authoritative_primary_cancer, authoritative_total),
            "ERKnownPct": _pct(authoritative_er, authoritative_total),
            "PRKnownPct": _pct(authoritative_pr, authoritative_total),
            "HER2KnownPct": _pct(authoritative_her2, authoritative_total),
            "stageKnownPct": _pct(authoritative_stage, authoritative_total),
            "ECOGKnownPct": _pct(authoritative_ecog, authoritative_total),
            "treatmentHistoryKnownPct": _pct(authoritative_treatment, authoritative_total),
            "usableLabsPct": _pct(authoritative_labs, authoritative_total),
            "diseaseProgressionKnownPct": _pct(authoritative_disease_progression, authoritative_total),
            "cleanLinkagePct": _pct(authoritative_clean_linkage, authoritative_total),
        },
        "matchReadySubset": {
            "count": len(match_ready_patient_refs),
            "criteria": list(BENCHMARK_MATCH_READY_CRITERIA),
            "patientRefs": sorted(match_ready_patient_refs)[:100],
        },
        "retailOutreachSubset": {
            "count": len(retail_outreach_patient_refs),
            "criteria": list(OUTREACH_READY_CRITERIA),
            "patientRefs": sorted(retail_outreach_patient_refs)[:100],
        },
        "stageScopeSummary": {
            "patientTargetWindows": patient_stage_window_rows,
            "trialTargetWindows": trial_stage_window_rows,
            "overlapWindows": exact_stage_overlap_rows,
            "effectiveOverlapWindows": effective_stage_overlap_rows,
            "recommendedTargetWindow": recommended_target_window,
            "recommendedTargetWindowSummary": recommended_target_window_summary,
        },
        "trialSummary": {
            "totalBreastCancerTrials": len(trial_rows),
            "runtimeReadyTrials": len(runtime_ready_trials),
            "runtimeReadyDeterministicTrials": len(runtime_ready_deterministic_trials),
            "runtimeReadyWithManualTrials": len(runtime_ready_with_manual_trials),
            "runtimeReadyTrialIds": [str(row.get("_id") or "").strip() for row in runtime_ready_trials if str(row.get("_id") or "").strip()],
            "runtimeReadyDeterministicTrialIds": [str(row.get("_id") or "").strip() for row in runtime_ready_deterministic_trials if str(row.get("_id") or "").strip()],
            "runtimeReadyWithManualTrialIds": [str(row.get("_id") or "").strip() for row in runtime_ready_with_manual_trials if str(row.get("_id") or "").strip()],
            "blockedTrials": len(blocked_trials),
            "blockedTrialIds": [str(row.get("_id") or "").strip() for row in blocked_trials if str(row.get("_id") or "").strip()],
            "readyWithReviewTrials": sum(1 for row in trial_rows if str(((row.get("compile") or {}).get("status")) or "") == "ready_with_review"),
            "stageSpecificTrials": stage_specific_trials,
            "stageSpecificRuntimeReadyTrials": stage_specific_runtime_ready_trials,
            "manualStageContextTrials": manual_stage_context_trials,
            "manualComplexSettingTrials": manual_complex_setting_trials,
            "averageDeterministicPatientImpactingCount": round(mean([float(((row.get("runtimeReadiness") or {}).get("deterministicPatientImpactingCount") or 0)) for row in trial_rows]), 1) if trial_rows else 0.0,
            "averageManualPatientImpactingCount": round(mean([float(((row.get("runtimeReadiness") or {}).get("manualPatientImpactingCount") or 0)) for row in trial_rows]), 1) if trial_rows else 0.0,
            "averageUnsupportedPatientImpactingCount": round(mean([float(((row.get("runtimeReadiness") or {}).get("unsupportedPatientImpactingCount") or 0)) for row in trial_rows]), 1) if trial_rows else 0.0,
            "averageUnresolvedPatientImpactingCount": round(mean([float(((row.get("runtimeReadiness") or {}).get("unresolvedPatientImpactingCount") or 0)) for row in trial_rows]), 1) if trial_rows else 0.0,
        },
        "familyReadiness": family_rows,
        "coreFamilyOverlap": core_family_overlap,
        "blockers": {
            "patientAuthoritativeBlockers": _top_counter_rows(authoritative_blockers),
            "patientMatchReadyBlockers": _top_counter_rows(match_ready_blockers),
            "patientOutreachBlockers": _top_counter_rows(outreach_blockers),
            "trialRuntimeBlockers": _top_counter_rows(trial_runtime_blockers),
            "unresolvedTrialFamilies": [
                {
                    "familyId": family_id,
                    "count": count,
                    "trialCount": len(unresolved_trial_family_trials.get(family_id) or set()),
                }
                for family_id, count in unresolved_trial_families.most_common(12)
            ],
        },
        "unassignedTriage": unassigned_triage,
        "benchmarkCandidates": {
            "trialIds": benchmark_trial_ids,
            "patientRefs": benchmark_authoritative_patient_refs,
            "matchReadyPatientRefs": benchmark_match_ready_refs,
            "retailOutreachPatientRefs": retail_outreach_candidate_refs,
        },
        "benchmarkGate": {
            "proceed": len(benchmark_gate_reasons) == 0,
            "thresholds": dict(BENCHMARK_GATE_THRESHOLDS),
            "actual": {
                "matchReadyPatients": len(match_ready_patient_refs),
                "runtimeReadyTrials": len(runtime_ready_trials),
            },
            "reasons": benchmark_gate_reasons,
            "recommendedNextPass": "benchmark_pack" if len(benchmark_gate_reasons) == 0 else "coverage_expansion",
        },
    }


def build_calibration_diagnostic_report(
    *,
    patient_facts_docs: list[dict],
    patient_profile_docs: list[dict],
    trial_docs: list[dict],
    current_criteria_docs: list[dict],
    rule_family_docs: list[dict],
    query: dict,
    git_commit: str,
    trial_runtime_tag: str,
    patient_rebuild_summary: dict,
    trial_refresh_summary: dict,
) -> dict:
    facts_by_ref = {
        str(doc.get("patientRef") or "").strip(): serialize_document(doc)
        for doc in patient_facts_docs
        if str(doc.get("patientRef") or "").strip()
    }
    profiles_by_ref = {
        str(doc.get("patientRef") or "").strip(): serialize_document(doc)
        for doc in patient_profile_docs
        if str(doc.get("patientRef") or "").strip()
    }
    patient_refs = sorted(set(list(facts_by_ref.keys()) + list(profiles_by_ref.keys())))

    selected_patient_rows = []
    authoritative_rejection_counter = Counter()
    outreach_rejection_counter = Counter()
    selected_counter = {
        "linkedPatients": 0,
        "confirmedBreastOncologyPatients": 0,
        "readyTierPatients": 0,
        "authoritativeBreastOncologyPatients": 0,
        "retailOutreachReadyPatients": 0,
        "selectedDiagnosticPatients": 0,
        "selectedAuthoritativeRejectedPatients": 0,
        "selectedOutreachRejectedPatients": 0,
    }

    for patient_ref in patient_refs:
        facts_doc = facts_by_ref.get(patient_ref) or {}
        profile_doc = profiles_by_ref.get(patient_ref) or {}
        evidence_profile = (facts_doc.get("patientEvidenceProfile") or {})
        match_profile = (profile_doc.get("patientMatchProfile") or {})
        snapshot = _authoritative_gate_snapshot(match_profile, evidence_profile)
        candidate_category = str(((match_profile.get("oncologyCohort") or {}).get("candidateCategory")) or "").strip().lower()
        readiness_tier = str(snapshot.get("matchingReadinessTier") or "").strip().lower()
        linkage_status = str(snapshot.get("linkageStatus") or "").strip().lower()

        if linkage_status == "linked":
            selected_counter["linkedPatients"] += 1
        if candidate_category == AUTHORITATIVE_COHORT_CATEGORY:
            selected_counter["confirmedBreastOncologyPatients"] += 1
        if readiness_tier in AUTHORITATIVE_COHORT_TIERS:
            selected_counter["readyTierPatients"] += 1
        if snapshot.get("authoritativeBreastOncology"):
            selected_counter["authoritativeBreastOncologyPatients"] += 1
        if snapshot.get("retailOutreachReady"):
            selected_counter["retailOutreachReadyPatients"] += 1

        if not any([
            linkage_status == "linked",
            candidate_category == AUTHORITATIVE_COHORT_CATEGORY,
            readiness_tier in AUTHORITATIVE_COHORT_TIERS,
            snapshot.get("authoritativeBreastOncology"),
            snapshot.get("retailOutreachReady"),
        ]):
            continue

        selected_counter["selectedDiagnosticPatients"] += 1
        authoritative_rejected_reasons = _authoritative_rejected_reasons(match_profile, evidence_profile)
        outreach_rejected_reasons = _outreach_rejected_reasons(match_profile, evidence_profile)
        if authoritative_rejected_reasons:
            selected_counter["selectedAuthoritativeRejectedPatients"] += 1
            for reason in authoritative_rejected_reasons:
                authoritative_rejection_counter[reason] += 1
        if outreach_rejected_reasons:
            selected_counter["selectedOutreachRejectedPatients"] += 1
            for reason in outreach_rejected_reasons:
                outreach_rejection_counter[reason] += 1

        selected_patient_rows.append(
            {
                "patientRef": patient_ref,
                "confirmedBreastOncology": candidate_category == AUTHORITATIVE_COHORT_CATEGORY,
                "matchingReadinessTier": snapshot.get("matchingReadinessTier"),
                "linkageStatus": snapshot.get("linkageStatus"),
                "identityTrust": snapshot.get("identityTrust"),
                "criticalConflictCount": snapshot.get("criticalConflictCount"),
                "authoritativeBreastOncology": bool(snapshot.get("authoritativeBreastOncology")),
                "retailOutreachReady": bool(snapshot.get("retailOutreachReady")),
                "benchmarkRunnable": bool(snapshot.get("benchmarkRunnable")),
                "outreachRunnable": bool(snapshot.get("outreachRunnable")),
                "pharmacyPrescreen": bool(snapshot.get("pharmacyPrescreen")),
                "screeningOnly": bool(snapshot.get("screeningOnly")),
                "candidateCategory": candidate_category or None,
                "authoritativeGateSnapshot": snapshot.get("gates") or {},
                "authoritativeRejectedReasons": authoritative_rejected_reasons,
                "outreachRejectedReasons": outreach_rejected_reasons,
                "coveragePct": float((((match_profile.get("coverageSnapshot") or {}).get("coveragePct")) or 0.0)),
                "criticalMissingDomains": list((((match_profile.get("oncologyCohort") or {}).get("criticalMissingDomains")) or []))[:12],
            }
        )

    selected_patient_rows.sort(
        key=lambda row: (
            not bool(row.get("confirmedBreastOncology")),
            str(row.get("matchingReadinessTier") or "") != "ready",
            str(row.get("linkageStatus") or "") != "linked",
            int(row.get("criticalConflictCount") or 0),
            row.get("patientRef") or "",
        )
    )

    current_versions = {
        str(row.get("_id") or ""): str(row.get("current_version_id") or "")
        for row in [serialize_document(item) for item in trial_docs]
    }
    current_criteria = [
        serialize_document(row)
        for row in current_criteria_docs
        if _is_current_trial_criterion(serialize_document(row), current_versions)
    ]
    nodes_by_trial = defaultdict(list)
    for node in current_criteria:
        trial_id = str(node.get("trial_id") or "").strip()
        if trial_id:
            nodes_by_trial[trial_id].append(node)

    family_docs_by_id = {}
    for doc in [serialize_document(item) for item in rule_family_docs]:
        family_id = str(doc.get("family_id") or "").strip()
        if family_id and family_id not in family_docs_by_id:
            family_docs_by_id[family_id] = doc

    trial_rows = [serialize_document(row) for row in trial_docs]
    blocked_trial_rows = []
    trial_blocker_counter = Counter()
    unresolved_family_counter = Counter()
    for trial_doc in trial_rows:
        trial_id = str(trial_doc.get("_id") or "").strip()
        blocked_by = _trial_runtime_blocked_by(trial_doc, nodes_by_trial.get(trial_id) or [], family_docs_by_id)
        if not bool(((trial_doc.get("runtimeReadiness") or {}).get("runnable"))):
            for item in blocked_by:
                trial_blocker_counter[item] += 1
        for item in blocked_by:
            if item.startswith("unsupported_family:") or item.startswith("manual_family:") or item.startswith("unresolved_family:") or item.startswith("exclusion_gap_family:"):
                unresolved_family_counter[item.split(":")[1]] += 1
        blocked_trial_rows.append(
            {
                "trialId": trial_id,
                "runtimeReady": bool(((trial_doc.get("runtimeReadiness") or {}).get("runnable"))),
                "compileStatus": ((trial_doc.get("compile") or {}).get("status")),
                "runtimeReadiness": trial_doc.get("runtimeReadiness") or {},
                "blockedBy": blocked_by,
            }
        )

    blocked_trial_rows.sort(
        key=lambda row: (
            bool(row.get("runtimeReady")),
            str(((row.get("runtimeReadiness") or {}).get("reasonIfBlocked")) or ""),
            row.get("trialId") or "",
        )
    )

    return {
        "generatedAt": serialize_document(utcnow()),
        "gitCommit": git_commit,
        "trialRuntimeTag": trial_runtime_tag,
        "query": query,
        "patientRebuild": patient_rebuild_summary,
        "trialRefresh": trial_refresh_summary,
        "authoritativeCalibration": {
            **selected_counter,
            "topAuthoritativeRejectedReasons": _top_counter_rows(authoritative_rejection_counter, limit=20),
            "topOutreachRejectedReasons": _top_counter_rows(outreach_rejection_counter, limit=20),
            "patients": selected_patient_rows,
        },
        "trialRuntimeDiagnostics": {
            "totalTrials": len(trial_rows),
            "runtimeReadyTrials": sum(1 for row in blocked_trial_rows if row.get("runtimeReady")),
            "blockedTrials": sum(1 for row in blocked_trial_rows if not row.get("runtimeReady")),
            "topBlockedBy": _top_counter_rows(trial_blocker_counter, limit=20),
            "topBlockedReasons": _top_counter_rows(trial_blocker_counter, limit=20),
            "topUnresolvedFamilies": _top_counter_rows(unresolved_family_counter, limit=20),
            "trials": blocked_trial_rows,
        },
        "recommendedNextPass": {
            "order": [
                "cohort_gate_calibration",
                "coverage_expansion",
                "rerun_corpus_proof",
            ],
            "reason": "authoritative cohort is empty and runtime-ready trial coverage is still narrow under the current canonical runtime.",
        },
    }


class ClinicalTrialsCorpusProofService:
    def _load_runtime_documents(self, *, trial_ids: list[str], query: dict) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
        patient_facts_docs = list(
            patient_collections.facts.find(
                {},
                {"patientRef": 1, "patientEvidenceProfile": 1},
            )
        )
        patient_profile_docs = list(
            patient_collections.profiles.find(
                {},
                {"patientRef": 1, "patientMatchProfile": 1},
            )
        )
        trial_query = {"_id": {"$in": [str(item).strip() for item in trial_ids if str(item).strip()]}} if trial_ids else {
            "disease_area": "breast_cancer",
            "phase": query.get("phase"),
        }
        trial_docs = list(
            collections.trials.find(
                trial_query,
                {
                    "_id": 1,
                    "current_version_id": 1,
                    "compile": 1,
                    "runtimeReadiness": 1,
                    "stage_relevance": 1,
                    "display": 1,
                },
            )
        )
        trial_id_values = [str(row.get("_id") or "").strip() for row in trial_docs if str(row.get("_id") or "").strip()]
        current_criteria_docs = list(
            collections.trial_criteria.find(
                {"trial_id": {"$in": trial_id_values}},
                {
                    "trial_id": 1,
                    "version_id": 1,
                    "node_type": 1,
                    "section": 1,
                    "raw": 1,
                    "normalized": 1,
                    "ancestor_headers": 1,
                    "compiler": 1,
                    "review": 1,
                },
            )
        ) if trial_id_values else []
        rule_family_docs = list(
            collections.rule_families.find(
                {},
                {"family_id": 1, "patientSupport": 1, "status": 1},
            ).sort([("family_id", 1), ("version", -1)])
        )
        return patient_facts_docs, patient_profile_docs, trial_docs, current_criteria_docs, rule_family_docs

    def build_report(
        self,
        *,
        trial_ids: list[str],
        query: dict,
        git_commit: str,
        trial_runtime_tag: str,
        patient_rebuild_summary: dict,
        trial_refresh_summary: dict,
    ) -> dict:
        patient_facts_docs, patient_profile_docs, trial_docs, current_criteria_docs, rule_family_docs = self._load_runtime_documents(
            trial_ids=trial_ids,
            query=query,
        )
        return build_corpus_proof_report(
            patient_facts_docs=patient_facts_docs,
            patient_profile_docs=patient_profile_docs,
            trial_docs=trial_docs,
            current_criteria_docs=current_criteria_docs,
            rule_family_docs=rule_family_docs,
            query=query,
            git_commit=git_commit,
            trial_runtime_tag=trial_runtime_tag,
            patient_rebuild_summary=patient_rebuild_summary,
            trial_refresh_summary=trial_refresh_summary,
        )

    def build_diagnostic_report(
        self,
        *,
        trial_ids: list[str],
        query: dict,
        git_commit: str,
        trial_runtime_tag: str,
        patient_rebuild_summary: dict,
        trial_refresh_summary: dict,
    ) -> dict:
        patient_facts_docs, patient_profile_docs, trial_docs, current_criteria_docs, rule_family_docs = self._load_runtime_documents(
            trial_ids=trial_ids,
            query=query,
        )
        return build_calibration_diagnostic_report(
            patient_facts_docs=patient_facts_docs,
            patient_profile_docs=patient_profile_docs,
            trial_docs=trial_docs,
            current_criteria_docs=current_criteria_docs,
            rule_family_docs=rule_family_docs,
            query=query,
            git_commit=git_commit,
            trial_runtime_tag=trial_runtime_tag,
            patient_rebuild_summary=patient_rebuild_summary,
            trial_refresh_summary=trial_refresh_summary,
        )


proof_service = ClinicalTrialsCorpusProofService()
