from __future__ import annotations

import random
import re
from datetime import datetime
from typing import Any

from dateutil import parser as date_parser

import runtime_state as runtime
from clinical_trial_matching_agent.leafy_extract.services.patient_curation import (
    build_patient_evidence_profile_v2,
    build_patient_match_profile_v2,
    build_screening_report_summary,
)
from clinical_trial_matching_agent.leafy_extract.services.pharma_services import (
    CURRENT_AUTHORITATIVE_LINK_STRATEGIES,
    build_breast_oncology_cohort_document,
    build_oncology_cohort_assessment_input,
    classify_link_record,
    is_authoritative_link_strategy,
)


class PatientProductError(RuntimeError):
    pass


class ScreenedPatientNotFoundError(PatientProductError):
    pass


class ScreenedPatientConflictError(PatientProductError):
    pass


def normalize_patient_ref(patient_ref_value: Any) -> str:
    raw = str(patient_ref_value or "").strip()
    if not raw:
        return ""
    if raw.startswith("Patient/"):
        return raw
    return f"Patient/{raw}"


def _parse_age_token(age_value: Any) -> int | None:
    if age_value is None:
        return None
    if isinstance(age_value, (int, float)):
        return max(int(age_value), 0)
    match = re.search(r"(\d{1,3})", str(age_value).strip().upper())
    if not match:
        return None
    return max(int(match.group(1)), 0)


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.parse(str(value))
    except Exception:
        return None


def _screening_summary_sort_key(summary: dict[str, Any] | None) -> float:
    payload = summary if isinstance(summary, dict) else {}
    report_epoch = payload.get("reportDateEpoch")
    if report_epoch is not None:
        return float(report_epoch)
    parsed_dt = _parse_iso_datetime(payload.get("reportDate"))
    return parsed_dt.timestamp() if parsed_dt is not None else 0.0


def _authoritative_screening_report_projection() -> dict[str, int]:
    return {
        "_id": 1,
        "appointmentID": 1,
        "patientID": 1,
        "patient-id": 1,
        "user": 1,
        "date": 1,
        "clinicalNotes": 1,
        "conclusions": 1,
        "conclusionsSNOMED": 1,
        "series.imageMetadata.PatientName": 1,
        "series.imageMetadata.PatientSex": 1,
        "series.imageMetadata.PatientAge": 1,
        "series.imageMetadata.PatientBirthDate": 1,
        "series.imageMetadata.StudyDescription": 1,
        "series.imageMetadata.Laterality": 1,
        "series.imageMetadata.biRads": 1,
        "syntheaPatientRef": 1,
        "syntheaLinkStrategy": 1,
    }


def _build_screening_summary(report_doc: dict[str, Any]) -> dict[str, Any]:
    return build_screening_report_summary(
        report_doc,
        parse_age_token_fn=_parse_age_token,
        serialize_doc_fn=runtime.serialize_doc,
        normalize_text_fn=runtime.normalize_pharma_text,
        parse_iso_datetime_fn=_parse_iso_datetime,
    )


def _load_latest_authoritative_screening_summary(patient_ref: str) -> dict[str, Any] | None:
    normalized_ref = normalize_patient_ref(patient_ref)
    if not normalized_ref or runtime.clinical_reports_collection is None:
        return None
    cursor = runtime.clinical_reports_collection.find(
        {
            "syntheaPatientRef": normalized_ref,
            "syntheaLinkStrategy": {"$in": sorted(CURRENT_AUTHORITATIVE_LINK_STRATEGIES)},
        },
        _authoritative_screening_report_projection(),
    )
    best_summary = None
    for report_doc in cursor:
        if not is_authoritative_link_strategy(report_doc.get("syntheaLinkStrategy")):
            continue
        summary = _build_screening_summary(report_doc)
        if best_summary is None or _screening_summary_sort_key(summary) >= _screening_summary_sort_key(best_summary):
            best_summary = summary
    return best_summary


def _load_latest_authoritative_linkage_payload(patient_ref: str) -> dict[str, Any] | None:
    normalized_ref = normalize_patient_ref(patient_ref)
    if not normalized_ref or runtime.leafy_synthea_links_collection is None:
        return None
    rows = list(
        runtime.leafy_synthea_links_collection.find(
            {"syntheaPatientRef": normalized_ref},
            {
                "_id": 0,
                "syntheaPatientRef": 1,
                "linkStrategy": 1,
                "linkageStatus": 1,
                "linkageConfidence": 1,
                "linkageEvidence": 1,
                "candidateOptions": 1,
                "linkedAt": 1,
                "resolvedAt": 1,
                "linkageVersion": 1,
                "leafyAppointmentId": 1,
                "leafyPatientId": 1,
                "leafyReportId": 1,
            },
        )
    )
    best_payload = None
    best_ts = None
    for row in rows:
        if not classify_link_record(row).get("authoritative"):
            continue
        row_ts = _parse_iso_datetime(row.get("resolvedAt") or row.get("linkedAt"))
        if best_payload is None or (row_ts is not None and (best_ts is None or row_ts >= best_ts)):
            best_payload = runtime.serialize_doc(row)
            best_ts = row_ts
    return best_payload


def _fallback_authoritative_linkage_payload(patient_ref: str, screening_summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "syntheaPatientRef": normalize_patient_ref(patient_ref),
        "linkStrategy": "screening_capture",
        "linkageStatus": "linked",
        "linkageConfidence": 1.0,
        "linkageEvidence": ["Authoritative screening-linked Synthea patient."],
        "candidateOptions": [],
        "linkedAt": None,
        "resolvedAt": None,
        "linkageVersion": "screening_report_authoritative_fallback_v1",
        "leafyAppointmentId": screening_summary.get("appointmentId"),
        "leafyPatientId": screening_summary.get("leafyPatientId"),
        "leafyReportId": screening_summary.get("reportId"),
    }


def _load_patient_facts_document(patient_ref: str) -> dict[str, Any] | None:
    normalized_ref = normalize_patient_ref(patient_ref)
    if not normalized_ref or runtime.pharma_patient_facts_collection is None:
        return None
    return runtime.pharma_patient_facts_collection.find_one({"patientRef": normalized_ref}, {"_id": 0})


def _load_patient_profile_document(patient_ref: str) -> dict[str, Any] | None:
    normalized_ref = normalize_patient_ref(patient_ref)
    if not normalized_ref or runtime.pharma_profiles_collection is None:
        return None
    return runtime.pharma_profiles_collection.find_one({"patientRef": normalized_ref}, {"_id": 0})


def _ensure_screened_patient(patient_ref: str) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    normalized_ref = normalize_patient_ref(patient_ref)
    if not normalized_ref:
        raise ScreenedPatientNotFoundError("patientRef is required")

    screening_summary = _load_latest_authoritative_screening_summary(normalized_ref)
    if not screening_summary:
        raise ScreenedPatientNotFoundError(
            "Patient evidence requires a Synthea patient with an authoritative completed screening record."
        )

    facts_doc = _load_patient_facts_document(normalized_ref)
    if not facts_doc:
        raise ScreenedPatientNotFoundError("Patient facts are not available for this screened Synthea patient.")

    linkage_payload = _load_latest_authoritative_linkage_payload(normalized_ref) or _fallback_authoritative_linkage_payload(
        normalized_ref,
        screening_summary,
    )
    return normalized_ref, screening_summary, linkage_payload, facts_doc


def build_screened_patient_evidence(patient_ref: str) -> dict[str, Any]:
    normalized_ref, screening_summary, linkage_payload, facts_doc = _ensure_screened_patient(patient_ref)
    evidence_profile = build_patient_evidence_profile_v2(
        patient_ref=normalized_ref,
        facts_doc=facts_doc,
        screening_summary=screening_summary,
        linkage_payload={
            "linkageStatus": linkage_payload.get("linkageStatus"),
            "linkageConfidence": linkage_payload.get("linkageConfidence"),
        },
        normalize_text_fn=runtime.normalize_pharma_text,
    )
    screening_imaging = (evidence_profile.get("screeningImaging") or {})
    identity = (evidence_profile.get("identity") or {})
    if not screening_imaging.get("present"):
        raise ScreenedPatientNotFoundError(
            "Patient evidence requires a completed screening summary, but none could be materialized."
        )
    if str(identity.get("linkageStatus") or "").strip().lower() == "conflict":
        raise ScreenedPatientConflictError(
            "Screening demographics conflict with the linked Synthea longitudinal record."
        )
    if str(identity.get("linkageStatus") or "").strip().lower() != "linked":
        raise ScreenedPatientNotFoundError(
            "Patient evidence requires an authoritative Synthea-linked screening record."
        )
    return {
        "patientRef": normalized_ref,
        "patientEvidenceProfile": evidence_profile,
        "screeningSummary": screening_summary,
        "patientLink": linkage_payload,
        "factsDocument": facts_doc,
    }


def build_screened_patient_match_profile(patient_ref: str) -> dict[str, Any]:
    evidence_bundle = build_screened_patient_evidence(patient_ref)
    normalized_ref = evidence_bundle["patientRef"]
    profile_doc = _load_patient_profile_document(normalized_ref)
    if not profile_doc:
        raise ScreenedPatientNotFoundError("Patient match profile is not available for this screened Synthea patient.")

    facts_doc = dict(evidence_bundle["factsDocument"])
    facts_doc["patientEvidenceProfile"] = evidence_bundle["patientEvidenceProfile"]
    oncology_assessment_input = build_oncology_cohort_assessment_input(
        patient_ref=normalized_ref,
        facts_doc=facts_doc,
        profile_doc=profile_doc,
    )
    oncology_cohort_document = build_breast_oncology_cohort_document(
        patient_ref=normalized_ref,
        assessment_input=oncology_assessment_input,
    )
    match_profile = build_patient_match_profile_v2(
        profile_doc=profile_doc,
        evidence_profile=evidence_bundle["patientEvidenceProfile"],
        oncology_cohort=oncology_cohort_document,
        normalize_text_fn=runtime.normalize_pharma_text,
    )
    return {
        **evidence_bundle,
        "patientMatchProfile": match_profile,
        "profileDocument": profile_doc,
        "oncologyCohort": oncology_cohort_document,
    }


def get_patient_linkage(patient_ref: str) -> dict[str, Any]:
    normalized_ref, screening_summary, linkage_payload, _facts_doc = _ensure_screened_patient(patient_ref)
    return {
        "patientRef": normalized_ref,
        "patientLink": linkage_payload,
        "screeningSummary": screening_summary,
    }


def select_random_screened_patient() -> dict[str, Any]:
    if runtime.clinical_reports_collection is None:
        raise ScreenedPatientNotFoundError("Clinical screening reports are not configured.")

    sampled_rows = list(
        runtime.clinical_reports_collection.aggregate(
            [
                {
                    "$match": {
                        "syntheaPatientRef": {"$exists": True, "$ne": ""},
                        "syntheaLinkStrategy": {"$in": sorted(CURRENT_AUTHORITATIVE_LINK_STRATEGIES)},
                    }
                },
                {"$sample": {"size": 24}},
                {"$project": {"syntheaPatientRef": 1, "syntheaLinkStrategy": 1}},
            ]
        )
    )
    candidate_refs = []
    seen = set()
    for row in sampled_rows:
        patient_ref = normalize_patient_ref(row.get("syntheaPatientRef"))
        if not patient_ref or patient_ref in seen:
            continue
        seen.add(patient_ref)
        candidate_refs.append(patient_ref)

    if not candidate_refs:
        candidate_refs = [
            normalize_patient_ref(value)
            for value in runtime.clinical_reports_collection.distinct(
                "syntheaPatientRef",
                {
                    "syntheaPatientRef": {"$exists": True, "$ne": ""},
                    "syntheaLinkStrategy": {"$in": sorted(CURRENT_AUTHORITATIVE_LINK_STRATEGIES)},
                },
            )
            if normalize_patient_ref(value)
        ]
        random.shuffle(candidate_refs)

    for patient_ref in candidate_refs:
        try:
            evidence_bundle = build_screened_patient_evidence(patient_ref)
            return {
                "patientRef": evidence_bundle["patientRef"],
                "patientLink": evidence_bundle["patientLink"],
                "screeningSummary": evidence_bundle["screeningSummary"],
                "selectionMode": "random_authoritative_screened_synthea_patient",
            }
        except PatientProductError:
            continue

    raise ScreenedPatientNotFoundError(
        "No screened Synthea patient with full product evidence artifacts is currently available."
    )


def _nested_value(payload: dict[str, Any] | None, *path: str) -> Any:
    current: Any = payload if isinstance(payload, dict) else {}
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _workspace_random_buckets(rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    return [
        (
            "random_authoritative_runnable_workspace_patient",
            [
                row
                for row in rows
                if bool(_nested_value(row, "cohortMembership", "authoritativeBreastOncology"))
                and bool(_nested_value(row, "runtimeReadiness", "runnable"))
            ],
        ),
        (
            "random_runnable_screening_linked_workspace_patient",
            [
                row
                for row in rows
                if bool(_nested_value(row, "runtimeReadiness", "runnable"))
                and bool(_nested_value(row, "mergeSummary", "authoritativeScreeningLinked"))
            ],
        ),
        (
            "random_runnable_workspace_patient",
            [
                row
                for row in rows
                if bool(_nested_value(row, "runtimeReadiness", "runnable"))
            ],
        ),
        (
            "random_linked_screening_workspace_patient",
            [
                row
                for row in rows
                if bool(_nested_value(row, "mergeSummary", "authoritativeScreeningLinked"))
            ],
        ),
        (
            "random_linked_workspace_patient",
            [
                row
                for row in rows
                if str(row.get("linkageStatus") or "").strip().lower() == "linked"
            ],
        ),
        ("random_workspace_patient", list(rows)),
    ]


def _build_workspace_patient_link(patient_ref: str, patient_summary: dict[str, Any], screening_summary: dict[str, Any]) -> dict[str, Any]:
    identity = (patient_summary.get("identity") or {}) if isinstance(patient_summary, dict) else {}
    merge_summary = (patient_summary.get("mergeSummary") or {}) if isinstance(patient_summary, dict) else {}
    return {
        "syntheaPatientRef": normalize_patient_ref(patient_ref),
        "linkageStatus": identity.get("linkageStatus"),
        "identityTrust": identity.get("identityTrust"),
        "authoritativeScreeningLinked": bool(merge_summary.get("authoritativeScreeningLinked")),
        "leafyAppointmentId": screening_summary.get("appointmentId"),
        "leafyPatientId": screening_summary.get("leafyPatientId"),
        "leafyReportId": screening_summary.get("reportId"),
        "source": "patient_workspace_catalog",
    }


def select_random_patient_workspace(*, focus: str = "breast_cancer") -> dict[str, Any]:
    try:
        catalog_payload = list_patient_catalog(limit=5000, focus=focus)
        rows = list(catalog_payload.get("patients") or [])
    except PatientProductError:
        rows = []

    for selection_mode, bucket in _workspace_random_buckets(rows):
        if not bucket:
            continue
        candidate_rows = list(bucket)
        while candidate_rows:
            selected_row = random.choice(candidate_rows)
            candidate_rows.remove(selected_row)
            normalized_ref = normalize_patient_ref(selected_row.get("patientRef"))
            if not normalized_ref:
                continue
            try:
                workspace = get_patient_workspace_detail(normalized_ref)
            except PatientProductError:
                continue
            patient_summary = workspace.get("patientSummary") or selected_row
            screening_summary = workspace.get("screeningSummary") or patient_summary.get("screeningSummary") or {}
            patient_link = _build_workspace_patient_link(normalized_ref, patient_summary, screening_summary)
            return {
                "patientRef": normalized_ref,
                "patientSummary": patient_summary,
                "patientEvidenceProfile": workspace.get("patientEvidenceProfile") or {},
                "patientMatchProfile": workspace.get("patientMatchProfile") or {},
                "screeningSummary": screening_summary,
                "patientLink": patient_link,
                "patientContext": {
                    "patientRef": normalized_ref,
                    "patientName": patient_summary.get("displayName") or normalized_ref,
                },
                "appointmentId": screening_summary.get("appointmentId") or "",
                "patientId": screening_summary.get("leafyPatientId") or patient_link.get("leafyPatientId") or "",
                "selectionMode": selection_mode,
                "sourceCollections": workspace.get("sourceCollections") or {},
            }

    screened_payload = select_random_screened_patient()
    normalized_ref = normalize_patient_ref(screened_payload.get("patientRef"))
    if not normalized_ref:
        raise ScreenedPatientNotFoundError("Unable to resolve a patientRef for random patient selection.")
    screening_summary = screened_payload.get("screeningSummary") or {}
    patient_link = dict(screened_payload.get("patientLink") or {})
    patient_link.setdefault("syntheaPatientRef", normalized_ref)
    return {
        "patientRef": normalized_ref,
        "patientSummary": None,
        "patientEvidenceProfile": {},
        "patientMatchProfile": {},
        "screeningSummary": screening_summary,
        "patientLink": patient_link,
        "patientContext": {
            "patientRef": normalized_ref,
            "patientName": normalized_ref,
        },
        "appointmentId": screening_summary.get("appointmentId") or "",
        "patientId": screening_summary.get("leafyPatientId") or patient_link.get("leafyPatientId") or "",
        "selectionMode": f"{screened_payload.get('selectionMode') or 'random_screened_patient'}_fallback",
        "sourceCollections": {
            "facts": runtime.PHARMA_PATIENT_FACTS_COLLECTION,
            "profiles": runtime.PHARMA_PATIENT_PROFILES_COLLECTION,
            "screeningReports": runtime.CLINICAL_REPORTS_COLLECTION,
            "links": runtime.LEAFY_SYNTHEA_LINKS_COLLECTION,
            "syntheaResources": runtime.SYNTHEA_RESOURCES_COLLECTION,
        },
    }


def get_patient_coverage_summary() -> dict[str, Any]:
    summary = runtime.build_patient_curation_coverage_summary_document()
    try:
        focused_catalog = list_patient_catalog(limit=5000, focus="breast_cancer")
        focused_summary = focused_catalog.get("summary") or {}
    except Exception:
        focused_summary = {}

    screening_linked_floor = int(focused_summary.get("screeningLinkedCount") or 0)
    if screening_linked_floor:
        summary["screeningLinkedPatients"] = max(
            int(summary.get("screeningLinkedPatients") or 0),
            screening_linked_floor,
        )
    summary["focusedCatalogSummary"] = focused_summary
    return summary


def _extract_screening_summary_from_profiles(
    evidence_profile: dict[str, Any] | None,
    match_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    evidence = evidence_profile if isinstance(evidence_profile, dict) else {}
    match = match_profile if isinstance(match_profile, dict) else {}
    screening = (evidence.get("screeningImaging") or {}) if isinstance(evidence.get("screeningImaging"), dict) else {}
    screening_summary = (match.get("screeningSummary") or {}) if isinstance(match.get("screeningSummary"), dict) else {}
    birads_values = list(screening.get("biradsValues") or screening_summary.get("biradsValues") or [])
    payload = {
        "present": bool(screening.get("present"))
        or bool(screening.get("reportId"))
        or bool(screening.get("appointmentId"))
        or bool(screening_summary.get("lastMammogramDate"))
        or bool(birads_values),
        "reportId": screening.get("reportId"),
        "appointmentId": screening.get("appointmentId"),
        "lastMammogramDate": screening.get("lastMammogramDate") or screening_summary.get("lastMammogramDate"),
        "biradsValues": birads_values,
        "biradsMax": screening.get("biradsMax") if screening.get("biradsMax") is not None else screening_summary.get("biradsMax"),
        "biradsLeft": screening.get("biradsLeft"),
        "biradsRight": screening.get("biradsRight"),
        "suspiciousLaterality": screening.get("suspiciousLaterality") or screening_summary.get("suspiciousLaterality"),
        "reportConclusion": screening.get("reportConclusion"),
        "provenance": list(screening.get("provenance") or []),
    }
    return payload


def _screening_report_counts(patient_ref: str) -> dict[str, int]:
    normalized_ref = normalize_patient_ref(patient_ref)
    if not normalized_ref or runtime.clinical_reports_collection is None:
        return {
            "screeningReportCount": 0,
            "authoritativeScreeningReportCount": 0,
        }
    screening_report_count = int(
        runtime.clinical_reports_collection.count_documents({"syntheaPatientRef": normalized_ref})
    )
    authoritative_screening_report_count = int(
        runtime.clinical_reports_collection.count_documents(
            {
                "syntheaPatientRef": normalized_ref,
                "syntheaLinkStrategy": {"$in": sorted(CURRENT_AUTHORITATIVE_LINK_STRATEGIES)},
            }
        )
    )
    return {
        "screeningReportCount": screening_report_count,
        "authoritativeScreeningReportCount": authoritative_screening_report_count,
    }


def _gap_label(value: Any) -> str:
    if isinstance(value, dict):
        for key in ["feature", "field", "name", "label"]:
            raw = str(value.get(key) or "").strip()
            if raw:
                return raw
        return runtime.normalize_pharma_text(str(value))
    return runtime.normalize_pharma_text(str(value or ""))


def _build_patient_catalog_row(
    patient_ref: str,
    profile_doc: dict[str, Any] | None,
    facts_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized_ref = normalize_patient_ref(patient_ref)
    serialized_profile_doc = runtime.serialize_doc(profile_doc) if profile_doc else {}
    serialized_facts_doc = runtime.serialize_doc(facts_doc) if facts_doc else {}
    match_profile = (serialized_profile_doc.get("patientMatchProfile") or {}) if serialized_profile_doc else {}
    evidence_profile = (serialized_facts_doc.get("patientEvidenceProfile") or {}) if serialized_facts_doc else {}
    identity = (match_profile.get("identity") or evidence_profile.get("identity") or {})
    demographics = (match_profile.get("demographics") or evidence_profile.get("demographics") or {})
    coverage_snapshot = (match_profile.get("coverageSnapshot") or evidence_profile.get("completeness") or {})
    cohort_membership = (match_profile.get("cohortMembership") or {})
    runtime_readiness = (match_profile.get("runtimeReadiness") or {})
    oncology_summary = (match_profile.get("oncologySummary") or {})
    stage_treatment_posture = (oncology_summary.get("stageTreatmentPosture") or {})
    match_filters = (match_profile.get("matchFilters") or {})
    resolved_features = (evidence_profile.get("resolvedFeatures") or {})
    tumor_markers = (oncology_summary.get("tumorMarkers") or {})
    screening_summary = _extract_screening_summary_from_profiles(evidence_profile, match_profile)
    screening_counts = _screening_report_counts(normalized_ref)
    gaps = [
        label
        for label in (_gap_label(item) for item in (match_profile.get("gaps") or evidence_profile.get("gaps") or []))
        if label
    ]
    primary_cancer_feature = (resolved_features.get("primaryCancer") or {})
    primary_cancer_status = str(match_filters.get("primaryCancerStatus") or primary_cancer_feature.get("status") or "").strip()
    primary_cancer_code = str(match_filters.get("primaryCancerCode") or primary_cancer_feature.get("resolvedValue") or "").strip()
    primary_cancer_text = str(((evidence_profile.get("oncology") or {}).get("primaryCancer") or {}).get("text") or "").strip()
    patient_id_fragment = normalized_ref.split("/", 1)[-1]
    return {
        "patientRef": normalized_ref,
        "displayName": f"Patient {patient_id_fragment[:8]}",
        "patientIdFragment": patient_id_fragment[:12],
        "demographics": {
            "sex": demographics.get("sex"),
            "ageYears": demographics.get("ageYears"),
            "birthDate": demographics.get("birthDate"),
        },
        "identity": identity,
        "linkageStatus": str(identity.get("linkageStatus") or runtime_readiness.get("linkageStatus") or "").strip().lower() or "unknown",
        "coveragePct": float(coverage_snapshot.get("coveragePct") or 0.0),
        "gapCount": int(coverage_snapshot.get("gapCount") or len(gaps)),
        "gaps": gaps,
        "cohortMembership": cohort_membership,
        "runtimeReadiness": runtime_readiness,
        "primaryCancer": {
            "status": primary_cancer_status or "unknown",
            "code": primary_cancer_code or None,
            "text": primary_cancer_text or None,
        },
        "oncologySummary": {
            "stageGroup": match_filters.get("stageGroupNormalized") or oncology_summary.get("stageGroup"),
            "stageBand": stage_treatment_posture.get("stageBand"),
            "diseaseWindow": match_filters.get("diseaseWindow") or stage_treatment_posture.get("diseaseWindow"),
            "treatmentSetting": match_filters.get("treatmentSetting") or stage_treatment_posture.get("treatmentSetting"),
            "metastaticStatus": match_filters.get("metastaticStatus"),
            "performanceECOG": match_filters.get("performanceECOG") if match_filters.get("performanceECOG") is not None else oncology_summary.get("performanceStatus"),
            "erStatus": match_filters.get("erStatus") or tumor_markers.get("ER"),
            "prStatus": tumor_markers.get("PR"),
            "her2Status": match_filters.get("her2Status") or tumor_markers.get("HER2"),
        },
        "screeningSummary": screening_summary,
        "mergeSummary": {
            "screeningReportCount": screening_counts["screeningReportCount"],
            "authoritativeScreeningReportCount": screening_counts["authoritativeScreeningReportCount"],
            "hasLeafyScreeningData": bool(screening_summary.get("present")) or screening_counts["screeningReportCount"] > 0,
            "authoritativeScreeningLinked": screening_counts["authoritativeScreeningReportCount"] > 0,
            "linkedSyntheaRecord": str(identity.get("linkageStatus") or "").strip().lower() == "linked",
        },
        "candidateSignals": list(match_profile.get("candidateSignals") or []),
        "collections": {
            "facts": runtime.PHARMA_PATIENT_FACTS_COLLECTION,
            "profiles": runtime.PHARMA_PATIENT_PROFILES_COLLECTION,
            "screeningReports": runtime.CLINICAL_REPORTS_COLLECTION,
            "links": runtime.LEAFY_SYNTHEA_LINKS_COLLECTION,
        },
    }


def _patient_catalog_query(
    *,
    focus: str = "breast_cancer",
    search: str | None = None,
    linked_only: bool = False,
    runnable_only: bool = False,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    normalized_focus = str(focus or "breast_cancer").strip().lower()
    if normalized_focus in {"breast_cancer", "breast_oncology"}:
        query["$or"] = [
            {"patientMatchProfile.matchFilters.primaryCancerCode": "breast_carcinoma"},
            {"patientMatchProfile.cohortMembership.authoritativeBreastOncology": True},
            {"patientMatchProfile.cohortMembership.metastaticBreastCancer": True},
            {"patientMatchProfile.cohortMembership.pharmacyPrescreen": True},
            {"patientMatchProfile.cohortMembership.screeningOnly": True},
        ]
    if linked_only:
        query["patientMatchProfile.identity.linkageStatus"] = "linked"
    if runnable_only:
        query["patientMatchProfile.runtimeReadiness.runnable"] = True
    search_text = str(search or "").strip()
    if search_text:
        search_clause = {
            "$or": [
                {"patientRef": {"$regex": search_text, "$options": "i"}},
                {"patientMatchProfile.matchFilters.primaryCancerCode": {"$regex": search_text, "$options": "i"}},
                {"patientMatchProfile.oncologySummary.stageGroup": {"$regex": search_text, "$options": "i"}},
                {"patientMatchProfile.matchFilters.erStatus": {"$regex": search_text, "$options": "i"}},
                {"patientMatchProfile.matchFilters.her2Status": {"$regex": search_text, "$options": "i"}},
            ]
        }
        if "$or" in query:
            query = {"$and": [{"$or": query["$or"]}, search_clause]}
        else:
            query = search_clause
    return query


def list_patient_catalog(
    *,
    limit: int = 50,
    offset: int = 0,
    focus: str = "breast_cancer",
    search: str | None = None,
    linked_only: bool = False,
    runnable_only: bool = False,
) -> dict[str, Any]:
    if runtime.pharma_profiles_collection is None:
        raise ScreenedPatientNotFoundError("Patient profiles collection is not configured.")

    query = _patient_catalog_query(
        focus=focus,
        search=search,
        linked_only=linked_only,
        runnable_only=runnable_only,
    )
    profile_rows = [
        runtime.serialize_doc(row)
        for row in runtime.pharma_profiles_collection.find(
            query,
            {
                "_id": 0,
                "patientRef": 1,
                "patientMatchProfile": 1,
            },
        )
    ]
    patient_refs = [normalize_patient_ref(row.get("patientRef")) for row in profile_rows if normalize_patient_ref(row.get("patientRef"))]
    facts_map: dict[str, dict[str, Any]] = {}
    if patient_refs and runtime.pharma_patient_facts_collection is not None:
        for row in runtime.pharma_patient_facts_collection.find(
            {"patientRef": {"$in": patient_refs}},
            {
                "_id": 0,
                "patientRef": 1,
                "patientEvidenceProfile": 1,
            },
        ):
            normalized_ref = normalize_patient_ref(row.get("patientRef"))
            if normalized_ref:
                facts_map[normalized_ref] = runtime.serialize_doc(row)

    rows = []
    for profile_row in profile_rows:
        normalized_ref = normalize_patient_ref(profile_row.get("patientRef"))
        if not normalized_ref:
            continue
        rows.append(
            _build_patient_catalog_row(
                normalized_ref,
                profile_row,
                facts_map.get(normalized_ref),
            )
        )

    rows.sort(
        key=lambda row: (
            0 if bool((row.get("cohortMembership") or {}).get("authoritativeBreastOncology")) else 1,
            0 if bool((row.get("runtimeReadiness") or {}).get("runnable")) else 1,
            0 if str(row.get("linkageStatus") or "").strip().lower() == "linked" else 1,
            -1 * float(row.get("coveragePct") or 0.0),
            str(row.get("patientRef") or ""),
        )
    )
    total_count = len(rows)
    paged_rows = rows[max(0, int(offset or 0)) : max(0, int(offset or 0)) + max(1, int(limit or 50))]
    summary = {
        "focus": str(focus or "breast_cancer").strip().lower() or "breast_cancer",
        "totalCount": total_count,
        "linkedCount": sum(1 for row in rows if row.get("linkageStatus") == "linked"),
        "runnableCount": sum(1 for row in rows if bool((row.get("runtimeReadiness") or {}).get("runnable"))),
        "authoritativeCount": sum(
            1 for row in rows if bool((row.get("cohortMembership") or {}).get("authoritativeBreastOncology"))
        ),
        "screeningLinkedCount": sum(
            1 for row in rows if bool((row.get("mergeSummary") or {}).get("authoritativeScreeningLinked"))
        ),
    }
    return {
        "patients": paged_rows,
        "totalCount": total_count,
        "summary": summary,
        "limit": max(1, int(limit or 50)),
        "offset": max(0, int(offset or 0)),
    }


def get_patient_workspace_detail(patient_ref: str) -> dict[str, Any]:
    normalized_ref = normalize_patient_ref(patient_ref)
    if not normalized_ref:
        raise ScreenedPatientNotFoundError("patientRef is required")
    profile_doc = _load_patient_profile_document(normalized_ref)
    facts_doc = _load_patient_facts_document(normalized_ref)
    if not profile_doc and not facts_doc:
        raise ScreenedPatientNotFoundError("Patient workspace detail not found.")
    patient_summary = _build_patient_catalog_row(normalized_ref, profile_doc, facts_doc)
    screening_summary = patient_summary.get("screeningSummary") or {}
    return {
        "patientRef": normalized_ref,
        "patientSummary": patient_summary,
        "patientEvidenceProfile": runtime.serialize_doc((facts_doc or {}).get("patientEvidenceProfile") or {}),
        "patientMatchProfile": runtime.serialize_doc((profile_doc or {}).get("patientMatchProfile") or {}),
        "screeningSummary": screening_summary,
        "factsDocument": runtime.serialize_doc(facts_doc) if facts_doc else None,
        "profileDocument": runtime.serialize_doc(profile_doc) if profile_doc else None,
        "sourceCollections": {
            "facts": runtime.PHARMA_PATIENT_FACTS_COLLECTION,
            "profiles": runtime.PHARMA_PATIENT_PROFILES_COLLECTION,
            "screeningReports": runtime.CLINICAL_REPORTS_COLLECTION,
            "links": runtime.LEAFY_SYNTHEA_LINKS_COLLECTION,
            "syntheaResources": runtime.SYNTHEA_RESOURCES_COLLECTION,
        },
    }


def _estimated_collection_count(collection: Any) -> int | None:
    if collection is None:
        return None
    try:
        return int(collection.estimated_document_count())
    except Exception:
        return None


def get_mongo_storage_overview() -> dict[str, Any]:
    trial_collection = runtime.clinical_trials_service_collections.trials
    summary = {
        "database": runtime.DATABASE_NAME,
        "focusLens": "breast_cancer",
        "collections": [
            {
                "name": runtime.PHARMA_TRIALS_COLLECTION,
                "role": "Leafy Hospital trial source corpus",
                "count": _estimated_collection_count(runtime.pharma_trials_collection),
                "category": "trials",
            },
            {
                "name": trial_collection.name,
                "role": "Canonical trial catalog",
                "count": _estimated_collection_count(trial_collection),
                "category": "trials",
            },
            {
                "name": runtime.clinical_trials_service_collections.trial_versions.name,
                "role": "Normalized source snapshots",
                "count": _estimated_collection_count(runtime.clinical_trials_service_collections.trial_versions),
                "category": "trials",
            },
            {
                "name": runtime.clinical_trials_service_collections.trial_criteria.name,
                "role": "Canonical inclusion/exclusion criteria",
                "count": _estimated_collection_count(runtime.clinical_trials_service_collections.trial_criteria),
                "category": "trials",
            },
            {
                "name": runtime.PHARMA_PATIENT_FACTS_COLLECTION,
                "role": "Merged patient evidence facts",
                "count": _estimated_collection_count(runtime.pharma_patient_facts_collection),
                "category": "patients",
            },
            {
                "name": runtime.PHARMA_PATIENT_PROFILES_COLLECTION,
                "role": "Match-ready patient profiles",
                "count": _estimated_collection_count(runtime.pharma_profiles_collection),
                "category": "patients",
            },
            {
                "name": runtime.CLINICAL_REPORTS_COLLECTION,
                "role": "Leafy screening reports",
                "count": _estimated_collection_count(runtime.clinical_reports_collection),
                "category": "patients",
            },
            {
                "name": runtime.LEAFY_SYNTHEA_LINKS_COLLECTION,
                "role": "Leafy to Synthea linkage records",
                "count": _estimated_collection_count(runtime.leafy_synthea_links_collection),
                "category": "patients",
            },
            {
                "name": runtime.SYNTHEA_RESOURCES_COLLECTION,
                "role": "Longitudinal Synthea FHIR resources",
                "count": _estimated_collection_count(runtime.synthea_resources_collection),
                "category": "patients",
            },
        ],
        "pipelines": {
            "trialPipeline": [
                runtime.PHARMA_TRIALS_COLLECTION,
                runtime.clinical_trials_service_collections.trials.name,
                runtime.clinical_trials_service_collections.trial_versions.name,
                runtime.clinical_trials_service_collections.trial_criteria.name,
            ],
            "patientPipeline": [
                runtime.SYNTHEA_RESOURCES_COLLECTION,
                runtime.CLINICAL_REPORTS_COLLECTION,
                runtime.LEAFY_SYNTHEA_LINKS_COLLECTION,
                runtime.PHARMA_PATIENT_FACTS_COLLECTION,
                runtime.PHARMA_PATIENT_PROFILES_COLLECTION,
            ],
        },
        "breastCancerLens": {
            "canonicalTrialCount": int(
                runtime.clinical_trials_service_collections.trials.count_documents({"disease_area": "breast_cancer"})
            ),
            "phase3RecruitingTrialCount": int(
                runtime.clinical_trials_service_collections.trials.count_documents(
                    {"disease_area": "breast_cancer", "phase": "PHASE3", "status": "RECRUITING"}
                )
            ),
            "breastCancerPatientCount": int(
                runtime.pharma_profiles_collection.count_documents(
                    {"patientMatchProfile.matchFilters.primaryCancerCode": "breast_carcinoma"}
                )
            )
            if runtime.pharma_profiles_collection is not None
            else 0,
            "authoritativeRunnablePatientCount": int(
                runtime.pharma_profiles_collection.count_documents(
                    {
                        "patientMatchProfile.cohortMembership.authoritativeBreastOncology": True,
                        "patientMatchProfile.runtimeReadiness.runnable": True,
                    }
                )
            )
            if runtime.pharma_profiles_collection is not None
            else 0,
        },
    }
    return summary


def run_demo_screening_enrichment(*, max_links: int = 250, dry_run: bool = False) -> dict[str, Any]:
    coverage_before = get_patient_coverage_summary()
    result = runtime.rebuild_demo_screened_patient_cohort(
        max_links=max_links,
        dry_run=dry_run,
    )
    pending_rows = runtime.list_pending_leafy_synthea_linkages(limit=200)
    coverage_after = get_patient_coverage_summary() if not dry_run else coverage_before
    return {
        "job": result,
        "coverageBefore": coverage_before,
        "coverageAfter": coverage_after,
        "pendingLinkageCount": len(pending_rows),
        "pendingLinkagesPreview": pending_rows[:12],
    }
