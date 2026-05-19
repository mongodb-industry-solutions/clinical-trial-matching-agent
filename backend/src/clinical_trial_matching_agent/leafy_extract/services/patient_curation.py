from datetime import datetime
import hashlib


PATIENT_EVIDENCE_PROFILE_VERSION = "patient_evidence_profile_v2"
PATIENT_MATCH_PROFILE_VERSION = "patient_match_profile_v2"
PATIENT_CURATION_COVERAGE_SUMMARY_VERSION = "patient_curation_coverage_summary_v1"
AUTHORITATIVE_ONCOLOGY_CATEGORY = "confirmed_breast_oncology"
AUTHORITATIVE_ONCOLOGY_TIERS = {"ready"}
BREAST_PRIMARY_CANCER_HINTS = [
    "breast",
    "mammary",
    "mastectomy",
    "lumpectomy",
    "ductal carcinoma",
    "lobular carcinoma",
    "dcis",
    "lcis",
    "breast biopsy",
]


def _as_list(value):
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _has_value(value):
    if value is None or value == "":
        return False
    if isinstance(value, (list, dict)) and len(value) == 0:
        return False
    return True


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value, default=None):
    try:
        return int(value)
    except Exception:
        return default


def _normalize_gender(value, normalize_text_fn):
    normalized = normalize_text_fn(value or "")
    if normalized in {"f", "female", "woman"}:
        return "female"
    if normalized in {"m", "male", "man"}:
        return "male"
    return ""


def _serialize_datetime(value, serialize_doc_fn):
    if value is None:
        return None
    try:
        return serialize_doc_fn(value)
    except Exception:
        return value


def _normalize_biomarker_status(value, normalize_text_fn):
    normalized = normalize_text_fn(value or "")
    if normalized in {"positive", "pos", "+", "detected", "overexpressed"}:
        return "positive"
    if normalized in {"negative", "neg", "-", "not detected"}:
        return "negative"
    if normalized in {"equivocal", "borderline"}:
        return "equivocal"
    return "unknown"


def _marker_payload(marker_value, support_refs=None):
    value = marker_value if marker_value not in (None, "") else None
    return {
        "status": "unknown" if value is None else _normalize_biomarker_status(value, lambda item: str(item or "").strip().lower()),
        "value": value,
        "supportRefs": list(support_refs or [])[:8],
    }


def _term_status(term_values):
    return "present" if _has_value(term_values) else "unknown"


def normalize_gap_fillability(value):
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


def _build_gap(feature, reason, fillable_by):
    return {
        "feature": feature,
        "reason": reason,
        "fillableBy": normalize_gap_fillability(fillable_by),
    }


def _normalize_stage_group_text(stage_value, normalize_text_fn):
    normalized = normalize_text_fn(stage_value or "")
    if not normalized:
        return None
    return normalized.replace("_", " ").strip() or None


def _classify_stage_band(stage_text):
    normalized = str(stage_text or "").strip().lower()
    if not normalized:
        return "unknown"
    if "stage iv" in normalized or "stage 4" in normalized:
        return "stage_iv"
    if "stage iii" in normalized or "stage 3" in normalized:
        return "stage_iii"
    if "stage ii" in normalized or "stage 2" in normalized:
        return "stage_ii"
    if "stage i" in normalized or "stage 1" in normalized:
        return "stage_i"
    if "stage 0" in normalized or "dcis" in normalized:
        return "stage_0"
    return "other"


def _build_stage_treatment_posture(*, stage_value, metastatic_status, disease_extent_values, treatment_status, normalize_text_fn):
    normalized_stage_group = _normalize_stage_group_text(stage_value, normalize_text_fn)
    stage_band = _classify_stage_band(normalized_stage_group)
    normalized_extent = [
        normalize_text_fn(item).replace("_", " ").strip()
        for item in _as_list(disease_extent_values)
        if normalize_text_fn(item).strip()
    ]
    metastatic_or_stage_iv = bool(
        metastatic_status == "present"
        or stage_band == "stage_iv"
        or any("metastatic" in item for item in normalized_extent)
    )
    locally_advanced_nonmetastatic = bool(
        not metastatic_or_stage_iv
        and any(token in item for item in normalized_extent for token in ["advanced", "recurrent", "regional"])
    )
    stage_ii_iii = stage_band in {"stage_ii", "stage_iii"}
    if metastatic_or_stage_iv:
        disease_window = "advanced_or_metastatic"
    elif stage_ii_iii or locally_advanced_nonmetastatic:
        disease_window = "stage_ii_iii_or_locally_advanced"
    elif stage_band in {"stage_0", "stage_i"}:
        disease_window = "stage_0_i"
    else:
        disease_window = "unknown"
    if metastatic_or_stage_iv:
        treatment_setting = "metastatic_systemic"
    elif stage_ii_iii or locally_advanced_nonmetastatic:
        treatment_setting = "curative_or_locally_advanced"
    elif stage_band in {"stage_0", "stage_i"}:
        treatment_setting = "early_stage"
    elif treatment_status == "present":
        treatment_setting = "treatment_history_present_stage_unclear"
    else:
        treatment_setting = "unknown"
    return {
        "normalizedStageGroup": normalized_stage_group,
        "stageBand": stage_band,
        "stageIiIii": stage_ii_iii,
        "metastaticOrStageIv": metastatic_or_stage_iv,
        "diseaseWindow": disease_window,
        "treatmentSetting": treatment_setting,
        "diseaseExtent": normalized_extent[:8],
        "treatmentHistoryPresent": treatment_status == "present",
    }


def _dedupe_refs(values):
    cleaned = []
    seen = set()
    for item in values or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned[:12]


def _resolved_feature_payload(
    *,
    resolved_value,
    status,
    conflict=False,
    resolution_policy,
    source_of_truth,
    evidence_refs=None,
):
    normalized_status = str(status or "unknown").strip().lower() or "unknown"
    return {
        "resolvedValue": resolved_value,
        "status": normalized_status,
        "conflict": bool(conflict or normalized_status == "conflicting"),
        "resolutionPolicy": resolution_policy,
        "sourceOfTruth": source_of_truth,
        "evidenceRefs": _dedupe_refs(evidence_refs),
    }


def _infer_primary_breast_cancer(
    *,
    condition_terms,
    procedure_terms,
    treatment_terms,
    oncology_signals,
    receptor_status,
    stage_or_extent,
    normalize_text_fn,
):
    normalized_condition_terms = [str(item).strip() for item in (condition_terms or []) if str(item).strip()]
    normalized_procedure_terms = [str(item).strip() for item in (procedure_terms or []) if str(item).strip()]
    normalized_treatment_terms = [str(item).strip() for item in (treatment_terms or []) if str(item).strip()]
    signal_matches = []
    if isinstance(oncology_signals, dict):
        signal_matches.extend(list((((oncology_signals.get("breast_oncology") or {}).get("matches")) or [])))
        signal_matches.extend(list((((oncology_signals.get("breast_biopsy") or {}).get("matches")) or [])))
        signal_matches.extend(list((((oncology_signals.get("breast_imaging") or {}).get("matches")) or [])))
    signal_matches.extend(list((receptor_status or {}).get("matches") or []))
    signal_matches.extend(list((stage_or_extent or {}).get("matches") or []))

    candidate_terms = normalized_condition_terms + normalized_procedure_terms + normalized_treatment_terms + signal_matches
    breast_specific_terms = [
        term for term in candidate_terms
        if any(hint in normalize_text_fn(term) for hint in BREAST_PRIMARY_CANCER_HINTS)
    ]
    breast_signal = bool(
        (((oncology_signals or {}).get("breast_oncology") or {}).get("present"))
        or (((oncology_signals or {}).get("breast_biopsy") or {}).get("present"))
        or breast_specific_terms
    )
    primary_cancer_text = None
    if breast_specific_terms:
        primary_cancer_text = breast_specific_terms[0]
    elif breast_signal:
        primary_cancer_text = "breast cancer"

    return {
        "present": breast_signal,
        "text": primary_cancer_text,
        "code": "breast_carcinoma" if breast_signal else None,
    }


def _build_runtime_oncology_cohort_assessment(
    *,
    candidate_category,
    readiness_tier,
    linkage_status,
    critical_conflict_count,
    primary_cancer_status,
    authoritative_breast_oncology,
    retail_outreach_ready,
    fallback_authoritative_eligible,
):
    normalized_category = str(candidate_category or "").strip().lower()
    normalized_tier = str(readiness_tier or "").strip().lower()
    normalized_linkage = str(linkage_status or "unlinked").strip().lower() or "unlinked"
    primary_cancer_present = str(primary_cancer_status or "").strip().lower() == "present"

    gates = {
        "linkageLinked": normalized_linkage == "linked",
        "noCriticalConflicts": int(critical_conflict_count or 0) == 0,
        "primaryCancerPresent": primary_cancer_present,
        "candidateCategoryConfirmed": normalized_category == AUTHORITATIVE_ONCOLOGY_CATEGORY,
        "readinessTierAuthoritative": normalized_tier in AUTHORITATIVE_ONCOLOGY_TIERS,
        "fallbackAuthoritativeEligible": bool(fallback_authoritative_eligible),
    }

    authoritative_rejected_reasons = []
    if not authoritative_breast_oncology:
        if normalized_linkage != "linked":
            authoritative_rejected_reasons.append(f"linkage_status::{normalized_linkage}")
        if int(critical_conflict_count or 0) > 0:
            authoritative_rejected_reasons.append("critical_conflicts_present")
        if not primary_cancer_present:
            authoritative_rejected_reasons.append("primary_cancer_not_present")
        if normalized_category and normalized_category != AUTHORITATIVE_ONCOLOGY_CATEGORY:
            authoritative_rejected_reasons.append(f"candidate_category_not_confirmed::{normalized_category}")
        elif not normalized_category and not bool(fallback_authoritative_eligible):
            authoritative_rejected_reasons.append("candidate_category_missing_without_fallback")
        if normalized_category == AUTHORITATIVE_ONCOLOGY_CATEGORY and normalized_tier not in AUTHORITATIVE_ONCOLOGY_TIERS:
            authoritative_rejected_reasons.append(f"readiness_tier_not_authoritative::{normalized_tier or 'unknown'}")
        if not normalized_category and not bool(fallback_authoritative_eligible):
            authoritative_rejected_reasons.append("fallback_authoritative_not_met")
        if (
            normalized_category == AUTHORITATIVE_ONCOLOGY_CATEGORY
            and normalized_tier in AUTHORITATIVE_ONCOLOGY_TIERS
            and int(critical_conflict_count or 0) == 0
            and primary_cancer_present
            and normalized_linkage == "linked"
        ):
            authoritative_rejected_reasons.append("cohort_rule_mismatch")
        if not authoritative_rejected_reasons:
            authoritative_rejected_reasons.append("authoritative_gate_unmet")

    outreach_rejected_reasons = []
    if not retail_outreach_ready:
        if not authoritative_breast_oncology:
            outreach_rejected_reasons.append("not_authoritative_breast_oncology")
        if normalized_linkage != "linked":
            outreach_rejected_reasons.append(f"linkage_status::{normalized_linkage}")
        if int(critical_conflict_count or 0) > 0:
            outreach_rejected_reasons.append("critical_conflicts_present")
        if not outreach_rejected_reasons:
            outreach_rejected_reasons.append("retail_outreach_gate_unmet")

    return {
        "confirmedBreastOncology": normalized_category == AUTHORITATIVE_ONCOLOGY_CATEGORY,
        "candidateCategory": normalized_category or None,
        "matchingReadinessTier": normalized_tier or None,
        "authoritativeBreastOncology": bool(authoritative_breast_oncology),
        "retailOutreachReady": bool(retail_outreach_ready),
        "authoritativeGateSnapshot": gates,
        "authoritativeRejectedReasons": sorted(set(authoritative_rejected_reasons)),
        "outreachRejectedReasons": sorted(set(outreach_rejected_reasons)),
    }


def build_screening_report_summary(
    report_doc,
    *,
    parse_age_token_fn,
    serialize_doc_fn,
    normalize_text_fn,
    parse_iso_datetime_fn,
):
    report = report_doc if isinstance(report_doc, dict) else {}
    series = report.get("series") or []

    birads_values = []
    birads_by_laterality = {}
    patient_name = ""
    sex = ""
    age_years = None
    birth_date = None
    report_date = _serialize_datetime(report.get("date"), serialize_doc_fn)
    parsed_report_dt = parse_iso_datetime_fn(report_date) if report_date else None

    for entry in series:
        image_meta = (entry or {}).get("imageMetadata") or {}
        if not patient_name:
            patient_name = str(image_meta.get("PatientName") or "").strip()
        if not sex:
            sex = _normalize_gender(image_meta.get("PatientSex"), normalize_text_fn)
        if age_years is None:
            age_years = parse_age_token_fn(image_meta.get("PatientAge"))
        if not birth_date:
            raw_birth_date = str(image_meta.get("PatientBirthDate") or "").strip()
            if len(raw_birth_date) == 8 and raw_birth_date.isdigit():
                birth_date = f"{raw_birth_date[:4]}-{raw_birth_date[4:6]}-{raw_birth_date[6:8]}"

        laterality = normalize_text_fn(image_meta.get("Laterality") or "")
        laterality = laterality[:1].upper() if laterality[:1] in {"l", "r"} else laterality[:1].upper()
        birads_payload = image_meta.get("biRads")
        if isinstance(birads_payload, dict):
            birads_value = birads_payload.get("BIRAD")
        else:
            birads_value = birads_payload
        birads_int = _safe_int(birads_value)
        if birads_int is None:
            continue
        birads_values.append(birads_int)
        if laterality:
            birads_by_laterality[laterality] = max(
                birads_int,
                _safe_int(birads_by_laterality.get(laterality), birads_int),
            )

    suspicious_laterality = None
    if birads_by_laterality:
        suspicious_code = sorted(
            birads_by_laterality.items(),
            key=lambda item: (-1 * _safe_int(item[1], 0), item[0]),
        )[0][0].lower()
        suspicious_laterality = "left" if suspicious_code == "l" else ("right" if suspicious_code == "r" else suspicious_code)

    return {
        "reportId": str(report.get("_id") or ""),
        "appointmentId": str(report.get("appointmentID") or "").strip(),
        "leafyPatientId": str(report.get("patientID") or report.get("patient-id") or "").strip(),
        "patientName": patient_name or str(report.get("user") or "").strip(),
        "reportDate": report_date,
        "reportDateEpoch": parsed_report_dt.timestamp() if parsed_report_dt is not None else None,
        "sex": sex or "",
        "ageYears": age_years,
        "birthDate": birth_date,
        "numberImages": len(series),
        "biradsValues": sorted(list(set(birads_values))),
        "biradsLeft": birads_by_laterality.get("L"),
        "biradsRight": birads_by_laterality.get("R"),
        "biradsMax": max(birads_values) if birads_values else None,
        "suspiciousLaterality": suspicious_laterality,
        "studyDescription": str((((series[0] or {}).get("imageMetadata") or {}).get("StudyDescription") or "")).strip() if series else "",
        "reportConclusion": str(report.get("conclusions") or "").strip(),
        "clinicalNotes": str(report.get("clinicalNotes") or "").strip(),
        "conclusionsSNOMED": str(report.get("conclusionsSNOMED") or "").strip(),
        "sourceRefs": [f"clinicalreports/{str(report.get('_id') or '')}"] if report.get("_id") else [],
    }


def build_patient_evidence_profile_v2(
    *,
    patient_ref,
    facts_doc,
    screening_summary=None,
    linkage_payload=None,
    normalize_text_fn,
):
    facts_document = facts_doc if isinstance(facts_doc, dict) else {}
    facts_root = (facts_document.get("facts") or {})
    screening = screening_summary if isinstance(screening_summary, dict) else {}
    linkage = linkage_payload if isinstance(linkage_payload, dict) else {}
    patient_ref_value = str(patient_ref or facts_document.get("patientRef") or "").strip()

    age_payload = facts_root.get("age") or {}
    sex_payload = facts_root.get("sex") or {}
    birth_date_payload = facts_root.get("birthDate") or {}
    labs_payload = ((facts_root.get("keyLabs") or {}).get("value")) or {}
    condition_history = _as_list((facts_root.get("conditionHistory") or {}).get("value"))
    procedure_history = _as_list((facts_root.get("procedureHistory") or {}).get("value"))
    medication_history = _as_list((facts_root.get("medicationHistory") or {}).get("value"))
    oncology_signals = ((facts_root.get("oncologySignals") or {}).get("value")) or {}
    receptor_status = ((facts_root.get("receptorStatus") or {}).get("value")) or {}
    performance_status = ((facts_root.get("performanceStatus") or {}).get("value")) or {}
    stage_or_extent = ((facts_root.get("stageOrExtent") or {}).get("value")) or {}
    oncology_treatment = ((facts_root.get("oncologyTreatmentHistory") or {}).get("value")) or {}

    synthea_age = age_payload.get("value")
    synthea_sex = _normalize_gender(sex_payload.get("value"), normalize_text_fn)
    synthea_birth_date = str(birth_date_payload.get("value") or "").strip() or None

    screening_present = bool(
        screening
        and any(
            _has_value(screening.get(key))
            for key in ["reportId", "appointmentId", "reportDate", "biradsValues", "reportConclusion"]
        )
    )
    linkage_status = str(linkage.get("linkageStatus") or "unlinked").strip().lower() or "unlinked"
    linkage_strategy = normalize_text_fn(linkage.get("linkStrategy") or "")
    linkage_confidence = _safe_float(linkage.get("linkageConfidence"), 1.0 if linkage_status == "linked" else 0.0)
    linkage_conflicts = []
    linkage_warnings = []

    screening_sex = _normalize_gender(screening.get("sex"), normalize_text_fn)
    if synthea_sex and screening_sex and synthea_sex != screening_sex:
        linkage_conflicts.append(
            {
                "field": "sex",
                "syntheaValue": synthea_sex,
                "screeningValue": screening_sex,
                "reason": "Screening demographics disagree with linked longitudinal record.",
            }
        )
    screening_age = screening.get("ageYears")
    if synthea_age is not None and screening_age is not None and abs(int(synthea_age) - int(screening_age)) > 1:
        linkage_conflicts.append(
            {
                "field": "ageYears",
                "syntheaValue": synthea_age,
                "screeningValue": screening_age,
                "reason": "Screening age disagrees with linked longitudinal record.",
            }
        )
    screening_birth_date = str(screening.get("birthDate") or "").strip() or None
    if synthea_birth_date and screening_birth_date and synthea_birth_date != screening_birth_date:
        linkage_conflicts.append(
            {
                "field": "birthDate",
                "syntheaValue": synthea_birth_date,
                "screeningValue": screening_birth_date,
                "reason": "Screening birth date disagrees with linked longitudinal record.",
            }
        )
    trusted_curated_link = linkage_status == "linked" and linkage_strategy in {"manual_review", "demo_screening_curation"}
    if linkage_conflicts:
        if trusted_curated_link:
            linkage_warnings = list(linkage_conflicts)
            linkage_conflicts = []
            linkage_confidence = max(linkage_confidence, 0.75)
        else:
            linkage_status = "conflict"
            linkage_confidence = min(linkage_confidence, 0.2)

    condition_terms = [str(item).strip() for item in condition_history if str(item).strip()]
    procedure_terms = [str(item).strip() for item in procedure_history if str(item).strip()]
    primary_cancer_payload = _infer_primary_breast_cancer(
        condition_terms=condition_terms,
        procedure_terms=procedure_terms,
        treatment_terms=list(oncology_treatment.get("terms") or []),
        oncology_signals=oncology_signals,
        receptor_status=receptor_status,
        stage_or_extent=stage_or_extent,
        normalize_text_fn=normalize_text_fn,
    )
    primary_cancer_status = "present" if primary_cancer_payload.get("present") else "unknown"
    primary_cancer_text = primary_cancer_payload.get("text")
    primary_cancer_code = primary_cancer_payload.get("code")

    receptor_support = list(receptor_status.get("supportRefs") or [])[:8]
    er_value = receptor_status.get("er")
    pr_value = receptor_status.get("pr")
    her2_value = receptor_status.get("her2")
    receptor_known = any(
        _normalize_biomarker_status(marker_value, normalize_text_fn) != "unknown"
        for marker_value in [er_value, pr_value, her2_value]
    )

    performance_value = performance_status.get("value")
    performance_known = performance_value is not None
    stage_value = stage_or_extent.get("stage")
    stage_known = _has_value(stage_value) or bool(stage_or_extent.get("tnm"))
    metastatic_value = stage_or_extent.get("metastatic")
    metastatic_present = metastatic_value is True
    metastatic_status = "present" if metastatic_value is True else ("absent" if metastatic_value is False else "unknown")
    treatment_terms = list(oncology_treatment.get("terms") or [])[:24]
    treatment_known = bool(treatment_terms)
    labs_known = bool(labs_payload)
    metastatic_sites = [
        str(item).strip()
        for item in _as_list(stage_or_extent.get("metastaticSites") or stage_or_extent.get("sites"))
        if str(item).strip()
    ][:24]
    cns_metastases_present = any(
        token in normalize_text_fn(" ".join(metastatic_sites + condition_terms))
        for token in ["brain metast", "cns metast", "central nervous system metast", "leptomeningeal"]
    )
    cns_metastases_status = "present" if cns_metastases_present else ("unknown" if not metastatic_sites else "absent")
    disease_extent_values = []
    if metastatic_present:
        disease_extent_values.extend(["advanced", "metastatic"])
    normalized_stage_value = normalize_text_fn(stage_value or "")
    if normalized_stage_value:
        if "stage iv" in normalized_stage_value or "stage 4" in normalized_stage_value or "advanced" in normalized_stage_value:
            disease_extent_values.append("advanced")
        if "metastatic" in normalized_stage_value:
            disease_extent_values.append("metastatic")
        if "recurrent" in normalized_stage_value:
            disease_extent_values.append("recurrent")
    disease_extent_values = sorted(list(dict.fromkeys(disease_extent_values)))
    completeness_domains = {
        "demographics": bool(synthea_age is not None and synthea_sex),
        "breastOncology": primary_cancer_status == "present",
        "receptorStatus": receptor_known,
        "stageOrExtent": stage_known or metastatic_present,
        "performanceStatus": performance_known,
        "treatmentHistory": treatment_known,
        "labs": labs_known,
        "screeningImaging": screening_present,
    }
    coverage_pct = round(
        (sum(1 for present in completeness_domains.values() if present) / len(completeness_domains)) * 100.0,
        1,
    ) if completeness_domains else 0.0

    gaps = []
    if not completeness_domains["receptorStatus"]:
        gaps.append(_build_gap("receptorStatus", "Missing ER/PR/HER2 evidence.", "pathology_or_site_ehr"))
    if not completeness_domains["stageOrExtent"]:
        gaps.append(_build_gap("stageOrExtent", "Missing stage or metastatic extent evidence.", "site_ehr_or_manual"))
    if not completeness_domains["performanceStatus"]:
        gaps.append(_build_gap("performanceStatus", "Missing ECOG or equivalent performance status.", "questionnaire_or_site_ehr"))
    if not completeness_domains["treatmentHistory"]:
        gaps.append(_build_gap("treatmentHistory", "Missing oncology treatment history.", "site_ehr"))
    if not completeness_domains["labs"]:
        gaps.append(_build_gap("labs", "Missing current structured lab values.", "site_ehr"))
    if linkage_conflicts:
        gaps.append(_build_gap("identityLinkage", "Linked screening and longitudinal identities conflict.", "manual_review"))

    source_refs = [patient_ref_value] if patient_ref_value else []
    source_refs.extend([item for item in screening.get("sourceRefs") or [] if item])
    source_refs = sorted(list(dict.fromkeys(source_refs)))
    canonical_patient_key = f"pat_{hashlib.sha256(patient_ref_value.encode('utf-8')).hexdigest()[:12]}" if patient_ref_value else None
    identity_conflict = bool(linkage_conflicts)
    age_evidence_refs = _dedupe_refs(source_refs)
    sex_evidence_refs = _dedupe_refs(source_refs)
    primary_cancer_evidence_refs = _dedupe_refs(
        list(((oncology_signals.get("breast_oncology") or {}).get("supportRefs")) or [])
        + list(((oncology_signals.get("breast_biopsy") or {}).get("supportRefs")) or [])
        + list(((oncology_signals.get("breast_imaging") or {}).get("supportRefs")) or [])
        + list(receptor_status.get("supportRefs") or [])
        + list(stage_or_extent.get("supportRefs") or [])
        + list(oncology_treatment.get("supportRefs") or [])
    )
    stage_support_refs = list(stage_or_extent.get("supportRefs") or [])[:8]
    performance_support_refs = list(performance_status.get("supportRefs") or [])[:8]
    treatment_support_refs = list(oncology_treatment.get("supportRefs") or [])[:8]

    resolved_features = {
        "age": _resolved_feature_payload(
            resolved_value=synthea_age,
            status="conflicting" if any(conflict.get("field") == "ageYears" for conflict in linkage_conflicts) else ("present" if synthea_age is not None else "unknown"),
            conflict=any(conflict.get("field") == "ageYears" for conflict in linkage_conflicts),
            resolution_policy="prefer_longitudinal_demographics_when_linked",
            source_of_truth="longitudinal_fhir_curated",
            evidence_refs=age_evidence_refs,
        ),
        "sex": _resolved_feature_payload(
            resolved_value=synthea_sex or None,
            status="conflicting" if any(conflict.get("field") == "sex" for conflict in linkage_conflicts) else ("present" if synthea_sex else "unknown"),
            conflict=any(conflict.get("field") == "sex" for conflict in linkage_conflicts),
            resolution_policy="prefer_longitudinal_demographics_when_linked",
            source_of_truth="longitudinal_fhir_curated",
            evidence_refs=sex_evidence_refs,
        ),
        "primaryCancer": _resolved_feature_payload(
            resolved_value={"code": primary_cancer_code, "text": primary_cancer_text} if primary_cancer_code else None,
            status=primary_cancer_status,
            resolution_policy="prefer_curated_oncology_signal",
            source_of_truth="curated_oncology_evidence",
            evidence_refs=primary_cancer_evidence_refs,
        ),
        "stageGroup": _resolved_feature_payload(
            resolved_value=stage_value,
            status="present" if _has_value(stage_value) else "unknown",
            resolution_policy="prefer_structured_stage_signal",
            source_of_truth="longitudinal_fhir_curated",
            evidence_refs=stage_support_refs,
        ),
        "metastaticFlag": _resolved_feature_payload(
            resolved_value=metastatic_value if metastatic_value in [True, False] else None,
            status=metastatic_status,
            resolution_policy="prefer_structured_extent_signal",
            source_of_truth="longitudinal_fhir_curated",
            evidence_refs=stage_support_refs,
        ),
        "metastaticSites": _resolved_feature_payload(
            resolved_value=metastatic_sites,
            status="present" if metastatic_sites else "unknown",
            resolution_policy="prefer_structured_metastatic_sites",
            source_of_truth="longitudinal_fhir_curated",
            evidence_refs=stage_support_refs,
        ),
        "diseaseExtent": _resolved_feature_payload(
            resolved_value=disease_extent_values,
            status="present" if disease_extent_values else "unknown",
            resolution_policy="derive_from_stage_and_metastatic_flag",
            source_of_truth="resolved_feature_derivation",
            evidence_refs=stage_support_refs,
        ),
        "ER": _resolved_feature_payload(
            resolved_value=_normalize_biomarker_status(er_value, normalize_text_fn) if er_value not in (None, "") else None,
            status=_normalize_biomarker_status(er_value, normalize_text_fn) if er_value not in (None, "") else "unknown",
            resolution_policy="prefer_curated_tumor_marker_observation",
            source_of_truth="curated_oncology_evidence",
            evidence_refs=receptor_support,
        ),
        "PR": _resolved_feature_payload(
            resolved_value=_normalize_biomarker_status(pr_value, normalize_text_fn) if pr_value not in (None, "") else None,
            status=_normalize_biomarker_status(pr_value, normalize_text_fn) if pr_value not in (None, "") else "unknown",
            resolution_policy="prefer_curated_tumor_marker_observation",
            source_of_truth="curated_oncology_evidence",
            evidence_refs=receptor_support,
        ),
        "HER2": _resolved_feature_payload(
            resolved_value=_normalize_biomarker_status(her2_value, normalize_text_fn) if her2_value not in (None, "") else None,
            status=_normalize_biomarker_status(her2_value, normalize_text_fn) if her2_value not in (None, "") else "unknown",
            resolution_policy="prefer_curated_tumor_marker_observation",
            source_of_truth="curated_oncology_evidence",
            evidence_refs=receptor_support,
        ),
        "ECOG": _resolved_feature_payload(
            resolved_value=performance_value,
            status="present" if performance_known else "unknown",
            resolution_policy="prefer_curated_performance_status",
            source_of_truth="longitudinal_fhir_curated",
            evidence_refs=performance_support_refs,
        ),
        "diseaseStatus": _resolved_feature_payload(
            resolved_value=None,
            status="unknown",
            resolution_policy="await_curated_disease_status",
            source_of_truth="not_available",
            evidence_refs=[],
        ),
        "treatmentHistory": _resolved_feature_payload(
            resolved_value=treatment_terms,
            status=_term_status(treatment_terms),
            resolution_policy="prefer_curated_oncology_treatment_terms",
            source_of_truth="longitudinal_fhir_curated",
            evidence_refs=treatment_support_refs,
        ),
        "keyLabs": _resolved_feature_payload(
            resolved_value=labs_payload if labs_known else None,
            status="present" if labs_known else "unknown",
            resolution_policy="prefer_latest_structured_lab_values",
            source_of_truth="longitudinal_fhir_curated",
            evidence_refs=[],
        ),
        "pregnancy": _resolved_feature_payload(
            resolved_value=None,
            status="unknown",
            resolution_policy="questionnaire_or_site_ehr",
            source_of_truth="not_available",
            evidence_refs=[],
        ),
        "breastfeeding": _resolved_feature_payload(
            resolved_value=None,
            status="unknown",
            resolution_policy="questionnaire_or_site_ehr",
            source_of_truth="not_available",
            evidence_refs=[],
        ),
        "activeInfection": _resolved_feature_payload(
            resolved_value=None,
            status="unknown",
            resolution_policy="questionnaire_or_site_ehr",
            source_of_truth="not_available",
            evidence_refs=[],
        ),
        "cnsMetastasesActive": _resolved_feature_payload(
            resolved_value=True if cns_metastases_present else (False if cns_metastases_status == "absent" else None),
            status=cns_metastases_status,
            resolution_policy="derive_from_metastatic_sites_and_condition_history",
            source_of_truth="resolved_feature_derivation",
            evidence_refs=_dedupe_refs(stage_support_refs + condition_terms[:3]),
        ),
        "sampleSubmissionWilling": _resolved_feature_payload(
            resolved_value=None,
            status="unknown",
            resolution_policy="questionnaire_only",
            source_of_truth="not_available",
            evidence_refs=[],
        ),
        "healthyVolunteer": _resolved_feature_payload(
            resolved_value=False if primary_cancer_status == "present" or condition_terms or medication_history else None,
            status="absent" if primary_cancer_status == "present" or condition_terms or medication_history else "unknown",
            resolution_policy="derive_from_curated_clinical_context",
            source_of_truth="resolved_feature_derivation",
            evidence_refs=_dedupe_refs(source_refs),
        ),
    }

    evidence_profile = {
        "schemaVersion": PATIENT_EVIDENCE_PROFILE_VERSION,
        "generatedAt": facts_document.get("generatedAt") or datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "identity": {
            "canonicalPatientKey": canonical_patient_key,
            "linkageStatus": linkage_status,
            "linkageConfidence": round(linkage_confidence, 4),
            "sourceRefs": source_refs,
            "linkageConflicts": linkage_conflicts,
            "linkageWarnings": linkage_warnings,
            "linkageStrategy": linkage_strategy or None,
        },
        "demographics": {
            "sex": synthea_sex or "unknown",
            "birthDate": synthea_birth_date,
            "ageYears": synthea_age,
            "screeningSnapshot": {
                "sex": screening_sex or None,
                "birthDate": screening_birth_date,
                "ageYears": screening_age,
            } if screening_present else None,
        },
        "oncology": {
            "primaryCancer": {
                "status": primary_cancer_status,
                "text": primary_cancer_text,
                "code": primary_cancer_code,
                "provenance": primary_cancer_evidence_refs,
            },
            "laterality": screening.get("suspiciousLaterality") or None,
            "stageGroup": {
                "status": "present" if _has_value(stage_value) else "unknown",
                "value": stage_value,
                "provenance": stage_support_refs,
            },
            "metastatic": {
                "status": metastatic_status,
                "value": metastatic_value if metastatic_value in [True, False] else None,
                "provenance": stage_support_refs,
            },
            "metastaticSites": metastatic_sites,
            "tumorMarkers": {
                "ER": _marker_payload(er_value, receptor_support),
                "PR": _marker_payload(pr_value, receptor_support),
                "HER2": _marker_payload(her2_value, receptor_support),
            },
            "performanceStatus": {
                "status": "present" if performance_known else "unknown",
                "scale": performance_status.get("scale") or "ECOG",
                "value": performance_value,
                "date": performance_status.get("date"),
                "provenance": performance_support_refs,
            },
            "diseaseStatus": {
                "status": "unknown",
                "value": "unknown",
                "date": None,
            },
            "measurableDisease": {
                "recistEligible": "unknown",
                "boneOnlyDisease": "unknown",
            },
        },
        "treatments": {
            "priorSystemicTherapy": {
                "status": _term_status(treatment_terms),
                "terms": treatment_terms,
                "provenance": treatment_support_refs,
            },
            "medicationHistory": medication_history[:24],
            "conditionHistory": condition_terms[:24],
            "procedureHistory": [str(item).strip() for item in procedure_history if str(item).strip()][:24],
        },
        "labs": {
            "availableKeys": sorted(list(labs_payload.keys())),
            "values": labs_payload,
        },
        "screeningImaging": {
            "present": screening_present,
            "reportId": screening.get("reportId") or None,
            "appointmentId": screening.get("appointmentId") or None,
            "lastMammogramDate": screening.get("reportDate") or None,
            "biradsValues": list(screening.get("biradsValues") or []),
            "biradsLeft": screening.get("biradsLeft"),
            "biradsRight": screening.get("biradsRight"),
            "biradsMax": screening.get("biradsMax"),
            "suspiciousLaterality": screening.get("suspiciousLaterality"),
            "reportConclusion": screening.get("reportConclusion") or None,
            "provenance": list(screening.get("sourceRefs") or []),
        },
        "context": {
            "activeInfection": "unknown",
            "pregnant": "unknown",
            "breastfeeding": "unknown",
            "sampleSubmissionWilling": None,
        },
        "resolvedFeatures": resolved_features,
        "completeness": {
            "domains": completeness_domains,
            "coveragePct": coverage_pct,
        },
        "gaps": gaps[:16],
    }
    return evidence_profile


def build_patient_match_profile_v2(
    *,
    profile_doc,
    evidence_profile=None,
    oncology_cohort=None,
    normalize_text_fn,
):
    profile = profile_doc if isinstance(profile_doc, dict) else {}
    evidence = evidence_profile if isinstance(evidence_profile, dict) else {}
    oncology_cohort_doc = oncology_cohort if isinstance(oncology_cohort, dict) else {}
    identity = (evidence.get("identity") or {})
    demographics = (evidence.get("demographics") or {})
    oncology = (evidence.get("oncology") or {})
    screening = (evidence.get("screeningImaging") or {})
    completeness = (evidence.get("completeness") or {})
    resolved = (evidence.get("resolvedFeatures") or {})

    er_status = ((resolved.get("ER") or {}).get("status")) or (((oncology.get("tumorMarkers") or {}).get("ER") or {}).get("status")) or "unknown"
    pr_status = ((resolved.get("PR") or {}).get("status")) or (((oncology.get("tumorMarkers") or {}).get("PR") or {}).get("status")) or "unknown"
    her2_status = ((resolved.get("HER2") or {}).get("status")) or (((oncology.get("tumorMarkers") or {}).get("HER2") or {}).get("status")) or "unknown"
    performance_value = ((resolved.get("ECOG") or {}).get("resolvedValue"))
    stage_value = ((resolved.get("stageGroup") or {}).get("resolvedValue")) or ((oncology.get("stageGroup") or {}).get("value"))
    disease_extent_values = ((resolved.get("diseaseExtent") or {}).get("resolvedValue")) or []
    primary_cancer_feature = (resolved.get("primaryCancer") or {})
    primary_cancer_value = primary_cancer_feature.get("resolvedValue") or {}
    primary_cancer_status = primary_cancer_feature.get("status") or ((oncology.get("primaryCancer") or {}).get("status")) or "unknown"
    primary_cancer_code = primary_cancer_value.get("code") if isinstance(primary_cancer_value, dict) else None
    metastatic_status = ((resolved.get("metastaticFlag") or {}).get("status")) or ((oncology.get("metastatic") or {}).get("status")) or "unknown"
    treatment_status = ((resolved.get("treatmentHistory") or {}).get("status")) or _term_status(((evidence.get("treatments") or {}).get("priorSystemicTherapy") or {}).get("terms"))
    stage_treatment_posture = _build_stage_treatment_posture(
        stage_value=stage_value,
        metastatic_status=metastatic_status,
        disease_extent_values=disease_extent_values,
        treatment_status=treatment_status,
        normalize_text_fn=normalize_text_fn,
    )
    linkage_status = identity.get("linkageStatus") or "unlinked"
    linkage_conflicts = list(identity.get("linkageConflicts") or [])
    critical_conflict_count = len(linkage_conflicts)
    oncology_candidate_category = str(oncology_cohort_doc.get("candidateCategory") or "").strip().lower()
    oncology_readiness_tier = str(oncology_cohort_doc.get("matchingReadinessTier") or "").strip().lower()
    oncology_feature_depth = sum(
        1
        for present in [
            primary_cancer_status == "present",
            any(status != "unknown" for status in [er_status, pr_status, her2_status]),
            bool(stage_value) or metastatic_status in {"present", "absent"},
            performance_value is not None,
            treatment_status == "present",
        ]
        if present
    )
    fallback_authoritative = bool(
        critical_conflict_count == 0
        and primary_cancer_status == "present"
        and oncology_feature_depth >= 3
        and any(
            present
            for present in [
                any(status != "unknown" for status in [er_status, pr_status, her2_status]),
                bool(stage_value) or metastatic_status in {"present", "absent"},
                treatment_status == "present",
            ]
        )
    )
    authoritative_breast_oncology = bool(
        linkage_status == "linked"
        and critical_conflict_count == 0
        and primary_cancer_status == "present"
        and (
            (
                oncology_candidate_category == AUTHORITATIVE_ONCOLOGY_CATEGORY
                and oncology_readiness_tier in AUTHORITATIVE_ONCOLOGY_TIERS
            )
            or (not oncology_candidate_category and fallback_authoritative)
        )
    )
    if critical_conflict_count > 0 or linkage_status == "conflict":
        identity_trust = "conflicted_identity"
    elif linkage_status == "linked":
        identity_trust = "linked_composite"
    elif screening.get("present"):
        identity_trust = "screening_signal_only"
    else:
        identity_trust = "unresolved_identity"
    retail_outreach_ready = bool(
        authoritative_breast_oncology
        and linkage_status == "linked"
        and critical_conflict_count == 0
    )
    oncology_cohort_assessment = _build_runtime_oncology_cohort_assessment(
        candidate_category=oncology_candidate_category,
        readiness_tier=oncology_readiness_tier,
        linkage_status=linkage_status,
        critical_conflict_count=critical_conflict_count,
        primary_cancer_status=primary_cancer_status,
        authoritative_breast_oncology=authoritative_breast_oncology,
        retail_outreach_ready=retail_outreach_ready,
        fallback_authoritative_eligible=fallback_authoritative,
    )
    screening_only = bool(screening.get("present")) and primary_cancer_status != "present" and linkage_status != "linked"
    pharmacy_prescreen = bool(
        linkage_status == "linked"
        and
        not authoritative_breast_oncology
        and not screening_only
        and linkage_status != "conflict"
    )
    if authoritative_breast_oncology:
        runtime_mode = "authoritative_oncology"
        runtime_runnable = True
        reason_if_blocked = None
    elif linkage_status == "conflict":
        runtime_mode = "linkage_required"
        runtime_runnable = False
        reason_if_blocked = "identity_linkage_conflict"
    elif linkage_status != "linked":
        runtime_mode = "linkage_required"
        runtime_runnable = False
        reason_if_blocked = "authoritative_fhir_link_required"
    else:
        runtime_mode = "pharmacy_prescreen"
        runtime_runnable = pharmacy_prescreen
        reason_if_blocked = None if pharmacy_prescreen else "not_authoritative_breast_oncology"

    benchmark_runnable = bool(authoritative_breast_oncology)
    benchmark_blocked_reasons = list(oncology_cohort_assessment.get("authoritativeRejectedReasons") or [])
    outreach_runnable = bool(retail_outreach_ready)
    outreach_blocked_reasons = list(oncology_cohort_assessment.get("outreachRejectedReasons") or [])

    candidate_signals = []
    if primary_cancer_status == "present":
        candidate_signals.append("primary_cancer")
    if screening.get("present"):
        candidate_signals.append("screening_imaging")
    if er_status != "unknown":
        candidate_signals.append(f"er_{er_status}")
    if her2_status != "unknown":
        candidate_signals.append(f"her2_{her2_status}")
    if stage_value:
        candidate_signals.append(normalize_text_fn(stage_value).replace(" ", "_"))
    if stage_treatment_posture.get("stageIiIii"):
        candidate_signals.append("stage_ii_iii")
    if stage_treatment_posture.get("metastaticOrStageIv"):
        candidate_signals.append("stage_iv_or_metastatic")
    if stage_treatment_posture.get("diseaseWindow") and stage_treatment_posture.get("diseaseWindow") != "unknown":
        candidate_signals.append(f"disease_window_{stage_treatment_posture['diseaseWindow']}")
    if stage_treatment_posture.get("treatmentSetting") and stage_treatment_posture.get("treatmentSetting") != "unknown":
        candidate_signals.append(f"treatment_setting_{stage_treatment_posture['treatmentSetting']}")
    if performance_value is not None:
        candidate_signals.append(f"ecog_{performance_value}")
    candidate_signals = list(dict.fromkeys(candidate_signals))

    return {
        "schemaVersion": PATIENT_MATCH_PROFILE_VERSION,
        "generatedAt": evidence.get("generatedAt") or datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "identity": {
            "canonicalPatientKey": identity.get("canonicalPatientKey"),
            "linkageStatus": linkage_status,
            "linkageConfidence": identity.get("linkageConfidence"),
            "conflictCount": critical_conflict_count,
            "identityTrust": identity_trust,
        },
        "demographics": {
            "sex": (resolved.get("sex") or {}).get("resolvedValue") or demographics.get("sex") or normalize_text_fn(profile.get("gender") or "") or "unknown",
            "ageYears": (resolved.get("age") or {}).get("resolvedValue") if (resolved.get("age") or {}).get("resolvedValue") is not None else (demographics.get("ageYears") if demographics.get("ageYears") is not None else profile.get("age")),
            "birthDate": demographics.get("birthDate") or profile.get("birthDate"),
        },
        "matchFilters": {
            "sex": (resolved.get("sex") or {}).get("resolvedValue") or demographics.get("sex") or normalize_text_fn(profile.get("gender") or "") or None,
            "ageYears": (resolved.get("age") or {}).get("resolvedValue") if (resolved.get("age") or {}).get("resolvedValue") is not None else (demographics.get("ageYears") if demographics.get("ageYears") is not None else profile.get("age")),
            "biradsMax": screening.get("biradsMax"),
            "screeningEvidence": bool(screening.get("present")),
            "primaryCancerStatus": primary_cancer_status,
            "primaryCancerCode": primary_cancer_code,
            "erStatus": er_status,
            "her2Status": her2_status,
            "stageKnown": bool(stage_value),
            "performanceStatusKnown": performance_value is not None,
            "performanceECOG": performance_value,
            "metastaticStatus": metastatic_status,
            "stageGroupNormalized": stage_treatment_posture.get("normalizedStageGroup"),
            "stageBand": stage_treatment_posture.get("stageBand"),
            "stageIiIii": stage_treatment_posture.get("stageIiIii"),
            "diseaseWindow": stage_treatment_posture.get("diseaseWindow"),
            "treatmentSetting": stage_treatment_posture.get("treatmentSetting"),
            "cnsMetastasesStatus": ((resolved.get("cnsMetastasesActive") or {}).get("status")) or "unknown",
            "linkageStatus": linkage_status,
        },
        "coverageSnapshot": {
            "coveragePct": completeness.get("coveragePct") or 0.0,
            "domains": completeness.get("domains") or {},
            "gapCount": len(evidence.get("gaps") or []),
        },
        "oncologyCohort": {
            "candidateCategory": oncology_candidate_category or None,
            "matchingReadinessTier": oncology_readiness_tier or None,
            "coveragePct": oncology_cohort_doc.get("coveragePct"),
            "criticalMissingDomains": list(oncology_cohort_doc.get("criticalMissingDomains") or [])[:12],
        },
        "oncologyCohortAssessment": oncology_cohort_assessment,
        "oncologySummary": {
            "primaryCancer": primary_cancer_status,
            "stageGroup": stage_value,
            "stageTreatmentPosture": stage_treatment_posture,
            "laterality": oncology.get("laterality"),
            "performanceStatus": performance_value,
            "tumorMarkers": {
                "ER": er_status,
                "PR": pr_status,
                "HER2": her2_status,
            },
        },
        "screeningSummary": {
            "biradsValues": list(screening.get("biradsValues") or []),
            "biradsMax": screening.get("biradsMax"),
            "suspiciousLaterality": screening.get("suspiciousLaterality"),
            "lastMammogramDate": screening.get("lastMammogramDate"),
        },
        "runtimeReadiness": {
            "mode": runtime_mode,
            "runnable": runtime_runnable,
            "reasonIfBlocked": reason_if_blocked,
            "linkageStatus": linkage_status,
            "criticalConflictCount": critical_conflict_count,
            "benchmarkRunnable": benchmark_runnable,
            "outreachRunnable": outreach_runnable,
            "reasonIfBenchmarkBlocked": benchmark_blocked_reasons[0] if benchmark_blocked_reasons else None,
            "reasonIfOutreachBlocked": outreach_blocked_reasons[0] if outreach_blocked_reasons else None,
            "benchmarkBlockedReasons": benchmark_blocked_reasons[:6],
            "outreachBlockedReasons": outreach_blocked_reasons[:6],
            "targetDiseaseWindow": stage_treatment_posture.get("diseaseWindow"),
        },
        "cohortMembership": {
            "authoritativeBreastOncology": authoritative_breast_oncology,
            "retailOutreachReady": retail_outreach_ready,
            "pharmacyPrescreen": pharmacy_prescreen,
            "screeningOnly": screening_only,
            "breastCancerStageIiIii": bool(primary_cancer_status == "present" and stage_treatment_posture.get("stageIiIii") and not stage_treatment_posture.get("metastaticOrStageIv")),
            "metastaticBreastCancer": bool(primary_cancer_status == "present" and stage_treatment_posture.get("metastaticOrStageIv")),
        },
        "candidateSignals": candidate_signals,
        "gaps": list(evidence.get("gaps") or [])[:12],
    }


def build_patient_curation_coverage_summary(*, facts_documents, profile_documents):
    evidence_profiles = []
    for document in facts_documents or []:
        profile = ((document or {}).get("patientEvidenceProfile") or {})
        if profile.get("schemaVersion") == PATIENT_EVIDENCE_PROFILE_VERSION:
            evidence_profiles.append(profile)

    match_profiles = []
    for document in profile_documents or []:
        profile = ((document or {}).get("patientMatchProfile") or {})
        if profile.get("schemaVersion") == PATIENT_MATCH_PROFILE_VERSION:
            match_profiles.append(profile)

    linkage_status_counts = {}
    domain_counts = {}
    gap_counts = {}
    screening_linked = 0
    conflicts = 0
    runtime_mode_counts = {}
    identity_trust_counts = {}
    cohort_membership_counts = {
        "authoritativeBreastOncology": 0,
        "retailOutreachReady": 0,
        "pharmacyPrescreen": 0,
        "screeningOnly": 0,
    }

    for profile in evidence_profiles:
        identity = profile.get("identity") or {}
        linkage_status = str(identity.get("linkageStatus") or "unlinked")
        linkage_status_counts[linkage_status] = linkage_status_counts.get(linkage_status, 0) + 1
        if linkage_status == "conflict":
            conflicts += 1
        screening_present = bool(((profile.get("screeningImaging") or {}).get("present")))
        if screening_present:
            screening_linked += 1
        for domain_key, present in ((profile.get("completeness") or {}).get("domains") or {}).items():
            bucket = domain_counts.setdefault(domain_key, {"present": 0, "total": 0})
            bucket["total"] += 1
            if present:
                bucket["present"] += 1
        for gap in profile.get("gaps") or []:
            feature = str((gap or {}).get("feature") or "").strip()
            if feature:
                gap_counts[feature] = gap_counts.get(feature, 0) + 1

    for profile in match_profiles:
        runtime_mode = str(((profile.get("runtimeReadiness") or {}).get("mode")) or "").strip()
        if runtime_mode:
            runtime_mode_counts[runtime_mode] = runtime_mode_counts.get(runtime_mode, 0) + 1
        identity_trust = str(((profile.get("identity") or {}).get("identityTrust")) or "").strip()
        if identity_trust:
            identity_trust_counts[identity_trust] = identity_trust_counts.get(identity_trust, 0) + 1
        for key in cohort_membership_counts.keys():
            if bool(((profile.get("cohortMembership") or {}).get(key))):
                cohort_membership_counts[key] += 1

    domain_coverage_pct = {}
    for domain_key, payload in domain_counts.items():
        total = int(payload.get("total") or 0)
        present = int(payload.get("present") or 0)
        domain_coverage_pct[domain_key] = round((present / total) * 100.0, 1) if total else 0.0

    top_gap_backlog = [
        {"feature": feature, "count": count}
        for feature, count in sorted(gap_counts.items(), key=lambda item: (-1 * item[1], item[0]))[:12]
    ]

    return {
        "summaryVersion": PATIENT_CURATION_COVERAGE_SUMMARY_VERSION,
        "generatedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "totalEvidenceProfiles": len(evidence_profiles),
        "totalMatchProfiles": len(match_profiles),
        "screeningLinkedPatients": screening_linked,
        "conflictPatients": conflicts,
        "linkageStatusCounts": linkage_status_counts,
        "runtimeModeCounts": runtime_mode_counts,
        "identityTrustCounts": identity_trust_counts,
        "cohortMembershipCounts": cohort_membership_counts,
        "domainCoveragePct": domain_coverage_pct,
        "topGapBacklog": top_gap_backlog,
    }
