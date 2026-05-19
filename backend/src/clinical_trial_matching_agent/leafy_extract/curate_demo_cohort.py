#!/usr/bin/env python3

import argparse
import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from services.pharma_services import (
    PHARMA_PATIENT_CURATION_OVERLAY_VERSION,
    classify_link_record,
)


CURATED_COHORT_MANIFEST_VERSION = "curated_stage_ii_iii_demo_cohort_manifest_v1"
TIER_RANK = {
    "ready": 0,
    "partial": 1,
    "limited": 2,
    "not_candidate": 3,
}
CATEGORY_RANK = {
    "confirmed_breast_oncology": 0,
    "probable_breast_oncology": 1,
    "possible_breast_workup": 2,
    "general_oncology_signal": 3,
    "background_population": 4,
}
ONCOLOGY_SIGNAL_FAMILIES = [
    "breast_oncology",
    "breast_biopsy",
    "receptor_status",
    "stage_or_extent",
    "performance_status",
    "oncology_treatment",
]


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_patient_ref(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    return raw if raw.startswith("Patient/") else f"Patient/{raw}"


def _string_list(values, *, limit=24):
    merged = []
    seen = set()
    for raw_value in values or []:
        text = str(raw_value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        merged.append(text)
        if len(merged) >= limit:
            break
    return merged


def _has_value(value):
    if isinstance(value, bool):
        return True
    if value is None:
        return False
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return bool(str(value).strip())


def _selection_sort_key(row):
    payload = row if isinstance(row, dict) else {}
    return (
        CATEGORY_RANK.get(str(payload.get("candidateCategory") or "background_population"), 99),
        TIER_RANK.get(str(payload.get("matchingReadinessTier") or "not_candidate"), 99),
        -1 * int(payload.get("enrichmentPriorityScore") or 0),
        -1 * float(payload.get("coveragePct") or 0.0),
        str(payload.get("patientRef") or ""),
    )


def _select_target_patients(rows, *, limit):
    selected = [
        row for row in (rows or [])
        if str((row or {}).get("candidateCategory") or "").strip() != "background_population"
    ]
    return sorted(selected, key=_selection_sort_key)[: max(1, int(limit or 12))]


def _candidate_refs_from_link_row(row):
    payload = row if isinstance(row, dict) else {}
    refs = []
    suggested_ref = _normalize_patient_ref(payload.get("suggestedSyntheaPatientRef"))
    if suggested_ref:
        refs.append(suggested_ref)
    direct_ref = _normalize_patient_ref(payload.get("syntheaPatientRef"))
    if direct_ref:
        refs.append(direct_ref)
    for item in payload.get("candidateOptions") or []:
        patient_ref = _normalize_patient_ref((item or {}).get("patientRef"))
        if patient_ref:
            refs.append(patient_ref)
    return _string_list(refs, limit=6)


def _pick_best_target_ref(candidate_refs, target_by_ref):
    best_ref = ""
    best_key = None
    for patient_ref in _string_list(candidate_refs, limit=6):
        target_row = target_by_ref.get(patient_ref)
        if not target_row:
            continue
        target_key = _selection_sort_key(target_row)
        if best_key is None or target_key < best_key:
            best_ref = patient_ref
            best_key = target_key
    return best_ref


def _summarize_fact_doc(fact_doc):
    row = fact_doc if isinstance(fact_doc, dict) else {}
    evidence = (row.get("patientEvidenceProfile") or {})
    resolved = (evidence.get("resolvedFeatures") or {})
    oncology = (evidence.get("oncology") or {})
    treatments = (evidence.get("treatments") or {})
    identity = (evidence.get("identity") or {})
    return {
        "linkageStatus": identity.get("linkageStatus") or "unlinked",
        "coveragePct": ((evidence.get("completeness") or {}).get("coveragePct")) or 0.0,
        "stageGroup": ((resolved.get("stageGroup") or {}).get("resolvedValue")) or ((oncology.get("stageGroup") or {}).get("value")),
        "ER": ((resolved.get("ER") or {}).get("status")) or (((oncology.get("tumorMarkers") or {}).get("ER") or {}).get("status")) or "unknown",
        "PR": ((resolved.get("PR") or {}).get("status")) or (((oncology.get("tumorMarkers") or {}).get("PR") or {}).get("status")) or "unknown",
        "HER2": ((resolved.get("HER2") or {}).get("status")) or (((oncology.get("tumorMarkers") or {}).get("HER2") or {}).get("status")) or "unknown",
        "ECOG": (resolved.get("ECOG") or {}).get("resolvedValue"),
        "treatmentHistory": list((((treatments.get("priorSystemicTherapy") or {}).get("terms")) or []))[:12],
        "gapCount": len(evidence.get("gaps") or []),
        "curationOverlayApplied": bool((row.get("curationOverlay") or {}).get("applied")),
    }


def _build_curation_batch_entry(index, link_row, target_row, fact_summary):
    screening_episode = {
        "patientId": link_row.get("leafyPatientId") or "",
        "appointmentId": link_row.get("leafyAppointmentId") or "",
        "reportId": link_row.get("leafyReportId") or "",
        "patientName": link_row.get("leafyPatientName") or "",
    }
    recommended_ref = _normalize_patient_ref(link_row.get("suggestedSyntheaPatientRef"))
    target_ref = _normalize_patient_ref(target_row.get("patientRef"))
    return {
        "batchEntryId": f"curated-cohort-{index:03d}",
        "reviewStatus": "pending_review",
        "targetPatientRef": target_ref,
        "targetPatientPriority": {
            "candidateCategory": target_row.get("candidateCategory"),
            "matchingReadinessTier": target_row.get("matchingReadinessTier"),
            "coveragePct": target_row.get("coveragePct"),
            "criticalMissingDomains": list(target_row.get("criticalMissingDomains") or [])[:12],
            "candidateReasons": list(target_row.get("candidateReasons") or [])[:6],
        },
        "screeningEpisode": screening_episode,
        "recommendedBySystem": {
            "suggestedSyntheaPatientRef": recommended_ref,
            "candidateOptions": list(link_row.get("candidateOptions") or [])[:3],
            "linkageStatus": link_row.get("linkageStatus") or "unlinked",
            "linkStrategy": link_row.get("linkStrategy") or "",
            "resolvedAt": link_row.get("resolvedAt"),
        },
        "currentPatientState": fact_summary,
        "linkageResolution": {
            "apply": False,
            "syntheaPatientRef": target_ref,
            "strategy": "manual_review",
            "note": "",
        },
        "curatedEvidence": {
            "apply": False,
            "reviewer": "",
            "note": "",
            "supportRefs": [],
            "conditionHistoryTerms": [],
            "procedureHistoryTerms": [],
            "pathologySummary": "",
            "receptorStatus": {
                "hr": None,
                "er": None,
                "pr": None,
                "her2": None,
                "matches": [],
                "supportRefs": [],
            },
            "stageOrExtent": {
                "stage": None,
                "tnm": [],
                "metastatic": None,
                "locallyAdvanced": None,
                "metastaticSites": [],
                "supportRefs": [],
            },
            "performanceStatus": {
                "scale": "ECOG",
                "value": None,
                "date": None,
                "supportRefs": [],
            },
            "oncologyTreatmentHistory": {
                "terms": [],
                "supportRefs": [],
            },
            "oncologySignals": {
                family_id: False for family_id in ONCOLOGY_SIGNAL_FAMILIES
            },
        },
    }


def _overlay_payload_from_entry(entry):
    curated_evidence = (entry or {}).get("curatedEvidence") or {}
    if not isinstance(curated_evidence, dict):
        return None
    facts_overlay = {
        "supportRefs": _string_list(curated_evidence.get("supportRefs") or [], limit=12),
        "conditionHistoryTerms": _string_list(curated_evidence.get("conditionHistoryTerms") or [], limit=120),
        "procedureHistoryTerms": _string_list(curated_evidence.get("procedureHistoryTerms") or [], limit=120),
        "pathologySummary": str(curated_evidence.get("pathologySummary") or "").strip(),
        "receptorStatus": dict(curated_evidence.get("receptorStatus") or {}),
        "stageOrExtent": dict(curated_evidence.get("stageOrExtent") or {}),
        "performanceStatus": dict(curated_evidence.get("performanceStatus") or {}),
        "oncologyTreatmentHistory": dict(curated_evidence.get("oncologyTreatmentHistory") or {}),
        "oncologySignals": {},
    }
    explicit_signals = curated_evidence.get("oncologySignals") or {}
    for family_id in ONCOLOGY_SIGNAL_FAMILIES:
        if explicit_signals.get(family_id):
            facts_overlay["oncologySignals"][family_id] = {
                "present": True,
                "matches": [f"manual_{family_id}"],
                "supportRefs": list(facts_overlay["supportRefs"]),
            }

    if not any(
        [
            facts_overlay["supportRefs"],
            facts_overlay["conditionHistoryTerms"],
            facts_overlay["procedureHistoryTerms"],
            facts_overlay["pathologySummary"],
            any(_has_value((facts_overlay["receptorStatus"] or {}).get(key)) for key in ["hr", "er", "pr", "her2"]),
            any(
                key in (facts_overlay["stageOrExtent"] or {}) and _has_value((facts_overlay["stageOrExtent"] or {}).get(key))
                for key in ["stage", "tnm", "metastatic", "locallyAdvanced", "metastaticSites"]
            ),
            "value" in (facts_overlay["performanceStatus"] or {}) and (facts_overlay["performanceStatus"] or {}).get("value") is not None,
            _string_list(((facts_overlay["oncologyTreatmentHistory"] or {}).get("terms")) or [], limit=24),
            facts_overlay["oncologySignals"],
        ]
    ):
        return None

    return facts_overlay


def _load_materialized_target_rows(cohort_collection):
    return list(
        cohort_collection.find(
            {"candidateCategory": {"$ne": "background_population"}},
            {
                "_id": 0,
                "patientRef": 1,
                "candidateCategory": 1,
                "matchingReadinessTier": 1,
                "coveragePct": 1,
                "criticalMissingDomains": 1,
                "candidateReasons": 1,
                "enrichmentPriorityScore": 1,
                "resolvedFeatureSummary": 1,
            },
        )
    )


def _derive_target_rows(*, facts_collection, profiles_collection, build_assessment_input, build_cohort_document):
    profile_map = {}
    for row in profiles_collection.find({}, {"_id": 0}):
        patient_ref = _normalize_patient_ref((row or {}).get("patientRef"))
        if patient_ref:
            profile_map[patient_ref] = row

    target_rows = []
    for facts_doc in facts_collection.find(
        {},
        {
            "_id": 0,
            "patientRef": 1,
            "factVersion": 1,
            "facts": 1,
            "patientEvidenceProfile": 1,
        },
    ):
        patient_ref = _normalize_patient_ref((facts_doc or {}).get("patientRef"))
        if not patient_ref:
            continue
        assessment_input = build_assessment_input(
            patient_ref=patient_ref,
            facts_doc=facts_doc,
            profile_doc=profile_map.get(patient_ref) or {},
        )
        cohort_row = build_cohort_document(
            patient_ref=patient_ref,
            assessment_input=assessment_input,
        )
        if str((cohort_row or {}).get("candidateCategory") or "").strip() == "background_population":
            continue
        target_rows.append(cohort_row)
    return target_rows


def _build_candidate_option(item, *, source="analog_candidate"):
    payload = item if isinstance(item, dict) else {}
    patient_ref = _normalize_patient_ref(payload.get("patientRef"))
    if not patient_ref:
        return None
    return {
        "patientRef": patient_ref,
        "sharedKeywordCount": int(payload.get("sharedKeywordCount") or 0),
        "resourceEventCount": int(payload.get("resourceEventCount") or 0),
        "source": source,
    }


def _find_target_candidates_for_screening(*, patient_context, profiles_collection, target_refs, limit=4):
    normalized_target_refs = [
        _normalize_patient_ref(value)
        for value in (target_refs or [])
        if _normalize_patient_ref(value)
    ]
    if not normalized_target_refs:
        return []

    query = {"patientRef": {"$in": normalized_target_refs}}
    patient_age = patient_context.get("age")
    if isinstance(patient_age, int):
        query["age"] = {
            "$gte": max(patient_age - 6, 0),
            "$lte": patient_age + 6,
        }

    patient_sex = str(patient_context.get("sex") or "").strip().lower()
    if patient_sex:
        query["gender"] = patient_sex

    pipeline = [{"$match": query}]
    keywords = list(patient_context.get("keywords") or [])
    if keywords:
        pipeline.extend(
            [
                {"$addFields": {"sharedKeywords": {"$setIntersection": ["$featureTerms", keywords]}}},
                {"$addFields": {"sharedKeywordCount": {"$size": "$sharedKeywords"}}},
                {"$sort": {"sharedKeywordCount": -1, "resourceEventCount": -1}},
            ]
        )
    else:
        pipeline.append({"$sort": {"resourceEventCount": -1}})

    pipeline.append({"$limit": max(1, min(int(limit or 4), 12))})
    pipeline.append(
        {
            "$project": {
                "_id": 0,
                "patientRef": 1,
                "age": 1,
                "gender": 1,
                "sharedKeywords": 1,
                "sharedKeywordCount": 1,
                "resourceTypeCounts": 1,
                "resourceEventCount": 1,
            }
        }
    )

    try:
        return list(profiles_collection.aggregate(pipeline, allowDiskUse=True))
    except Exception:
        return []


def _load_target_rows(deps):
    cohort_collection = deps["db_pharma_oncology_cohort_collection"]
    if cohort_collection is not None:
        return _load_materialized_target_rows(cohort_collection)

    facts_collection = deps["db_pharma_patient_facts_collection"]
    profiles_collection = deps["db_pharma_profiles_collection"]
    build_assessment_input = deps["build_oncology_cohort_assessment_input"]
    build_cohort_document = deps["build_breast_oncology_cohort_document"]
    if (
        facts_collection is None
        or profiles_collection is None
        or build_assessment_input is None
        or build_cohort_document is None
    ):
        raise RuntimeError(
            "Unable to derive the breast oncology cohort because required patient facts, profiles, or builders are unavailable."
        )
    return _derive_target_rows(
        facts_collection=facts_collection,
        profiles_collection=profiles_collection,
        build_assessment_input=build_assessment_input,
        build_cohort_document=build_cohort_document,
    )


def _load_pending_link_rows_from_collection(links_collection, *, max_pending):
    resolved_limit = max(50, int(max_pending or 200))
    rows = []
    cursor = links_collection.find(
        {},
        {
            "_id": 0,
            "leafyPatientId": 1,
            "leafyAppointmentId": 1,
            "leafyReportId": 1,
            "leafyPatientName": 1,
            "syntheaPatientRef": 1,
            "suggestedSyntheaPatientRef": 1,
            "linkStrategy": 1,
            "linkageStatus": 1,
            "candidateOptions": 1,
            "resolvedAt": 1,
        },
    ).sort("resolvedAt", -1).limit(resolved_limit)
    for row in cursor:
        classification = classify_link_record(row)
        if (classification.get("linkageStatus") or "unlinked") == "linked":
            continue
        rows.append(row)
    return rows


def _derive_pending_link_rows_from_screening(
    *,
    reports_collection,
    profiles_collection=None,
    build_patient_context,
    infer_analog_patients,
    target_refs=None,
    max_pending=200,
):
    resolved_limit = max(50, int(max_pending or 200))
    scan_limit = max(250, resolved_limit * 8)
    normalized_target_refs = {
        _normalize_patient_ref(value)
        for value in (target_refs or [])
        if _normalize_patient_ref(value)
    }
    matched_target_refs = set()
    seen_appointments = set()
    rows = []
    cursor = reports_collection.find(
        {},
        {
            "_id": 1,
            "appointmentID": 1,
            "patientID": 1,
            "patient-id": 1,
            "user": 1,
            "date": 1,
        },
    ).sort("date", -1).limit(scan_limit)
    for report_row in cursor:
        appointment_id = str(report_row.get("appointmentID") or "").strip()
        if not appointment_id or appointment_id in seen_appointments:
            continue
        seen_appointments.add(appointment_id)
        patient_id = str(report_row.get("patientID") or report_row.get("patient-id") or "").strip()
        try:
            patient_context, report_doc, _appointment_doc = build_patient_context(
                appointment_id=appointment_id,
                patient_id=patient_id or None,
            )
        except Exception:
            continue
        if _normalize_patient_ref((patient_context or {}).get("syntheaPatientRef")):
            continue
        analog_candidates = []
        if normalized_target_refs and profiles_collection is not None:
            analog_candidates = _find_target_candidates_for_screening(
                patient_context=patient_context,
                profiles_collection=profiles_collection,
                target_refs=normalized_target_refs,
                limit=4,
            )
        if not analog_candidates:
            analog_candidates, _analog_pipeline = infer_analog_patients(patient_context, limit=4)
        candidate_options = []
        for item in analog_candidates[:3]:
            option = _build_candidate_option(item)
            if option:
                candidate_options.append(option)
        if not candidate_options:
            continue
        candidate_refs = [
            _normalize_patient_ref((item or {}).get("patientRef"))
            for item in candidate_options
            if _normalize_patient_ref((item or {}).get("patientRef"))
        ]
        if normalized_target_refs and not any(ref in normalized_target_refs for ref in candidate_refs):
            continue
        matched_target_refs.update(ref for ref in candidate_refs if ref in normalized_target_refs)
        rows.append(
            {
                "leafyPatientId": patient_context.get("patientId") or patient_id,
                "leafyAppointmentId": appointment_id,
                "leafyReportId": patient_context.get("reportId") or str(report_row.get("_id") or "").strip(),
                "leafyPatientName": patient_context.get("patientName") or report_row.get("user") or "",
                "syntheaPatientRef": "",
                "suggestedSyntheaPatientRef": candidate_options[0]["patientRef"],
                "linkStrategy": "candidate_only_runtime",
                "linkageStatus": "candidate_only",
                "candidateOptions": candidate_options,
                "resolvedAt": report_doc.get("date") or report_row.get("date"),
            }
        )
        enough_rows = len(rows) >= resolved_limit
        enough_target_coverage = (
            not normalized_target_refs
            or len(matched_target_refs) >= min(len(normalized_target_refs), resolved_limit)
        )
        if enough_rows and enough_target_coverage:
            break
    return rows


def _load_pending_link_rows(deps, *, target_refs=None, max_pending=200):
    links_collection = deps["db_leafy_synthea_links_collection"]
    pending_rows = []
    if links_collection is not None:
        pending_rows = _load_pending_link_rows_from_collection(
            links_collection,
            max_pending=max_pending,
        )
    if pending_rows:
        return pending_rows

    reports_collection = deps["db_clinical_reports_collection"]
    profiles_collection = deps["db_pharma_profiles_collection"]
    build_patient_context = deps["build_leafy_patient_context"]
    infer_analog_patients = deps["infer_synthea_analog_patients"]
    if (
        reports_collection is None
        or build_patient_context is None
        or infer_analog_patients is None
    ):
        raise RuntimeError(
            "Unable to derive pending screening candidates because clinical reports or runtime linkage builders are unavailable."
        )
    return _derive_pending_link_rows_from_screening(
        reports_collection=reports_collection,
        profiles_collection=profiles_collection,
        build_patient_context=build_patient_context,
        infer_analog_patients=infer_analog_patients,
        target_refs=target_refs,
        max_pending=max_pending,
    )


def _load_runtime_deps():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)

    from app import (
        build_breast_oncology_cohort_document,
        build_leafy_patient_context,
        build_oncology_cohort_assessment_input,
        db_clinical_reports_collection,
        db_leafy_synthea_links_collection,
        db_pharma_oncology_cohort_collection,
        db_pharma_patient_curation_overlays_collection,
        db_pharma_patient_facts_collection,
        db_pharma_profiles_collection,
        infer_synthea_analog_patients,
        normalize_authoritative_linkage_policy,
        rebuild_breast_oncology_cohort,
        rebuild_pharma_patient_facts,
        rebuild_pharma_patient_profiles,
        resolve_leafy_synthea_link,
    )

    return {
        "build_breast_oncology_cohort_document": build_breast_oncology_cohort_document,
        "build_leafy_patient_context": build_leafy_patient_context,
        "build_oncology_cohort_assessment_input": build_oncology_cohort_assessment_input,
        "db_clinical_reports_collection": db_clinical_reports_collection,
        "db_leafy_synthea_links_collection": db_leafy_synthea_links_collection,
        "db_pharma_oncology_cohort_collection": db_pharma_oncology_cohort_collection,
        "db_pharma_patient_curation_overlays_collection": db_pharma_patient_curation_overlays_collection,
        "db_pharma_patient_facts_collection": db_pharma_patient_facts_collection,
        "db_pharma_profiles_collection": db_pharma_profiles_collection,
        "infer_synthea_analog_patients": infer_synthea_analog_patients,
        "normalize_authoritative_linkage_policy": normalize_authoritative_linkage_policy,
        "rebuild_breast_oncology_cohort": rebuild_breast_oncology_cohort,
        "rebuild_pharma_patient_facts": rebuild_pharma_patient_facts,
        "rebuild_pharma_patient_profiles": rebuild_pharma_patient_profiles,
        "resolve_leafy_synthea_link": resolve_leafy_synthea_link,
    }


def export_template(*, limit, max_pending, output_path):
    deps = _load_runtime_deps()
    facts_collection = deps["db_pharma_patient_facts_collection"]

    target_rows = _load_target_rows(deps)
    selected_targets = _select_target_patients(target_rows, limit=limit)
    target_by_ref = {str((row or {}).get("patientRef") or "").strip(): row for row in selected_targets}
    patient_refs = list(target_by_ref.keys())

    facts_map = {}
    if patient_refs:
        for row in facts_collection.find(
            {"patientRef": {"$in": patient_refs}},
            {"_id": 0, "patientRef": 1, "patientEvidenceProfile": 1, "curationOverlay": 1},
        ):
            patient_ref = str(row.get("patientRef") or "").strip()
            if patient_ref:
                facts_map[patient_ref] = _summarize_fact_doc(row)

    pending_rows = []
    for row in _load_pending_link_rows(
        deps,
        target_refs=patient_refs,
        max_pending=max_pending,
    ):
        classification = classify_link_record(row)
        if (classification.get("linkageStatus") or "unlinked") == "linked":
            continue
        candidate_refs = _candidate_refs_from_link_row(row)
        best_ref = _pick_best_target_ref(candidate_refs, target_by_ref)
        if not best_ref:
            continue
        pending_rows.append((best_ref, row))

    batch_entries = []
    matched_refs = set()
    for index, (best_ref, row) in enumerate(pending_rows[: max_pending], start=1):
        matched_refs.add(best_ref)
        batch_entries.append(
            _build_curation_batch_entry(
                index,
                row,
                target_by_ref.get(best_ref) or {},
                facts_map.get(best_ref) or {},
            )
        )

    manifest = {
        "schemaVersion": CURATED_COHORT_MANIFEST_VERSION,
        "generatedAt": _utc_now(),
        "selectionPolicy": {
            "focus": "breast_cancer_stage_ii_iii_demonstrator",
            "limit": int(limit),
            "sortOrder": [
                "candidateCategory",
                "matchingReadinessTier",
                "enrichmentPriorityScore",
                "coveragePct",
            ],
        },
        "summary": {
            "selectedTargetPatients": len(selected_targets),
            "pendingScreeningMatches": len(batch_entries),
            "matchedTargetPatients": len(matched_refs),
            "unmatchedTargetPatients": max(0, len(selected_targets) - len(matched_refs)),
        },
        "targetPatients": [
            {
                "patientRef": row.get("patientRef"),
                "candidateCategory": row.get("candidateCategory"),
                "matchingReadinessTier": row.get("matchingReadinessTier"),
                "coveragePct": row.get("coveragePct"),
                "criticalMissingDomains": list(row.get("criticalMissingDomains") or [])[:12],
                "candidateReasons": list(row.get("candidateReasons") or [])[:6],
                "resolvedFeatureSummary": row.get("resolvedFeatureSummary") or {},
                "currentPatientState": facts_map.get(str(row.get("patientRef") or "").strip()) or {},
            }
            for row in selected_targets
        ],
        "curationBatch": batch_entries,
    }

    rendered = json.dumps(manifest, indent=2, sort_keys=True)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    else:
        print(rendered)


def apply_manifest(*, input_path, dry_run, skip_rebuild, max_records):
    deps = _load_runtime_deps()
    overlays_collection = deps["db_pharma_patient_curation_overlays_collection"]
    resolve_link = deps["resolve_leafy_synthea_link"]
    normalize_linkage = deps["normalize_authoritative_linkage_policy"]
    rebuild_facts = deps["rebuild_pharma_patient_facts"]
    rebuild_profiles = deps["rebuild_pharma_patient_profiles"]
    rebuild_cohort = deps["rebuild_breast_oncology_cohort"]

    with open(input_path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)

    entries = list(manifest.get("curationBatch") or [])
    summary = {
        "schemaVersion": CURATED_COHORT_MANIFEST_VERSION,
        "inputPath": input_path,
        "processedEntries": len(entries),
        "linkagesApplied": 0,
        "overlaysApplied": 0,
        "dryRun": bool(dry_run),
        "skipRebuild": bool(skip_rebuild),
        "normalization": None,
        "rebuildPatientFacts": None,
        "rebuildPatientProfiles": None,
        "rebuildBreastOncologyCohort": None,
    }

    for entry in entries:
        review_status = str(entry.get("reviewStatus") or "").strip().lower()
        linkage_resolution = entry.get("linkageResolution") or {}
        curated_evidence = entry.get("curatedEvidence") or {}
        target_ref = _normalize_patient_ref(
            linkage_resolution.get("syntheaPatientRef") or entry.get("targetPatientRef")
        )
        if review_status not in {"approved", "confirmed"} and not linkage_resolution.get("apply") and not curated_evidence.get("apply"):
            continue
        if not target_ref:
            continue

        screening_episode = entry.get("screeningEpisode") or {}
        patient_context = {
            "patientId": str(screening_episode.get("patientId") or "").strip(),
            "appointmentId": str(screening_episode.get("appointmentId") or "").strip(),
            "reportId": str(screening_episode.get("reportId") or "").strip(),
            "patientName": str(screening_episode.get("patientName") or "").strip(),
        }

        if linkage_resolution.get("apply"):
            note = str(linkage_resolution.get("note") or "").strip()
            if dry_run:
                summary["linkagesApplied"] += 1
            else:
                resolve_link(
                    patient_context=patient_context,
                    synthea_patient_ref=target_ref,
                    note=note,
                )
                summary["linkagesApplied"] += 1

        if curated_evidence.get("apply"):
            overlay_payload = _overlay_payload_from_entry(entry)
            if overlay_payload:
                overlay_doc = {
                    "patientRef": target_ref,
                    "overlayVersion": PHARMA_PATIENT_CURATION_OVERLAY_VERSION,
                    "updatedAt": _utc_now(),
                    "curationSource": "demo_manual_review",
                    "reviewer": str(curated_evidence.get("reviewer") or "").strip() or None,
                    "note": str(curated_evidence.get("note") or "").strip() or None,
                    "factsOverlay": overlay_payload,
                }
                if dry_run:
                    summary["overlaysApplied"] += 1
                else:
                    overlays_collection.update_one(
                        {"patientRef": target_ref},
                        {"$set": overlay_doc},
                        upsert=True,
                    )
                    summary["overlaysApplied"] += 1

    if not dry_run and (summary["linkagesApplied"] or summary["overlaysApplied"]):
        summary["normalization"] = normalize_linkage(max_records=max_records)
        if not skip_rebuild:
            summary["rebuildPatientFacts"] = rebuild_facts()
            summary["rebuildPatientProfiles"] = rebuild_profiles()
            summary["rebuildBreastOncologyCohort"] = rebuild_cohort()

    print(json.dumps(summary, indent=2, sort_keys=True, default=str))


def main():
    parser = argparse.ArgumentParser(
        description="Export and apply curated Stage II/III breast-oncology demo cohort batches."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser(
        "export-template",
        help="Export the highest-priority authoritative-linkage curation batch template.",
    )
    export_parser.add_argument("--limit", type=int, default=12, help="Maximum target patients to include.")
    export_parser.add_argument(
        "--max-pending",
        type=int,
        default=200,
        help="Maximum pending linkage records to scan and include.",
    )
    export_parser.add_argument("--output", type=str, default="", help="Optional output JSON path.")

    apply_parser = subparsers.add_parser(
        "apply",
        help="Apply approved authoritative links and curated evidence overlays from a reviewed manifest.",
    )
    apply_parser.add_argument("--input", type=str, required=True, help="Reviewed manifest JSON path.")
    apply_parser.add_argument("--dry-run", action="store_true", help="Show what would be applied without writing data.")
    apply_parser.add_argument("--skip-rebuild", action="store_true", help="Skip normalization and derived rebuild steps.")
    apply_parser.add_argument(
        "--max-records",
        type=int,
        default=5000,
        help="Maximum record count for post-apply linkage normalization.",
    )

    args = parser.parse_args()
    if args.command == "export-template":
        export_template(limit=args.limit, max_pending=args.max_pending, output_path=str(args.output or "").strip())
        return
    if args.command == "apply":
        apply_manifest(
            input_path=args.input,
            dry_run=bool(args.dry_run),
            skip_rebuild=bool(args.skip_rebuild),
            max_records=args.max_records,
        )
        return


if __name__ == "__main__":
    main()
