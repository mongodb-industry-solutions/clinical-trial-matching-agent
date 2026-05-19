from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import re

from bson import ObjectId
from dateutil import parser as date_parser
from pymongo import UpdateOne
import requests

from .audit_trial_matching_dataset_readiness import build_dataset_readiness_summary
from .patient_curation import (
    PATIENT_CURATION_COVERAGE_SUMMARY_VERSION,
    PATIENT_EVIDENCE_PROFILE_VERSION,
    PATIENT_MATCH_PROFILE_VERSION,
    build_patient_curation_coverage_summary,
    build_patient_evidence_profile_v2,
    build_patient_match_profile_v2,
    build_screening_report_summary,
    normalize_gap_fillability,
)
from .pharma_evidence_tools import create_pharma_evidence_tools
from .pharma_rule_tools import create_pharma_rule_tools


BREAST_ONCOLOGY_COHORT_VERSION = "breast_oncology_cohort_v1"
BREAST_ONCOLOGY_COHORT_ASSESSMENT_INPUT_VERSION = "breast_oncology_cohort_assessment_input_v1"
PHARMA_LINKAGE_CONTRACT_VERSION = "leafy_synthea_linkage_contract_v2"
PHARMA_TRIAL_MATCH_CONTROL_PLANE_VERSION = "trial_matching_control_plane_v1"
PHARMA_TRIAL_MATCH_EVALUATION_RUN_VERSION = "trial_matching_evaluation_run_v1"
PHARMA_TRIAL_MATCH_IMPROVEMENT_RUN_VERSION = "trial_matching_improvement_run_v1"
PHARMA_TRIAL_PREFILTER_CONTRACT_VERSION = "trial_prefilter_contract_v1"
PHARMA_TRIAL_DEFINITION_CANONICAL_VERSION = "trial_definition_canonical_v1"
PHARMA_PATIENT_CURATION_OVERLAY_VERSION = "patient_curation_overlay_v1"
SCREENING_CAPTURE_LINK_STRATEGY = "screening_capture"
DETERMINISTIC_SCREENING_BACKFILL_LINK_STRATEGY = "deterministic_screening_backfill"
DEMO_SCREENING_CURATION_LINK_STRATEGY = "demo_screening_curation"
CURRENT_AUTHORITATIVE_LINK_STRATEGIES = {
    SCREENING_CAPTURE_LINK_STRATEGY,
    DEMO_SCREENING_CURATION_LINK_STRATEGY,
    "manual_review",
    "manual_override",
    "authoritative_import",
    "external_id_match",
}
SYNTHETIC_ECOG_OBSERVATION_VERSION = "synthetic_ecog_observation_v1"
SYNTHETIC_ECOG_OBSERVATION_CODE = "89247-1"
CANONICAL_FHIR_RUNTIME_DATASET_LABEL = "synthea_fhir_r4_sep2019_runtime"
CANONICAL_FHIR_RUNTIME_PIPELINE_VERSION = "2.0.0"
AUTHORITATIVE_LINK_STRATEGIES = {
    *CURRENT_AUTHORITATIVE_LINK_STRATEGIES,
}
LEGACY_HEURISTIC_LINK_STRATEGIES = {
    "analog_high_confidence",
    "analog_ambiguous",
    "deterministic_fallback",
    "legacy_cached",
}
BREAST_ONCOLOGY_SIGNAL_WEIGHTS = {
    "breast_oncology": 5,
    "breast_biopsy": 4,
    "receptor_status": 4,
    "stage_or_extent": 4,
    "oncology_treatment": 3,
    "breast_imaging": 2,
    "performance_status": 1,
}
BREAST_ONCOLOGY_DOMAIN_LABELS = {
    "demographics": "Demographics",
    "condition_history": "Condition history",
    "procedure_history": "Procedure history",
    "medication_history": "Medication history",
    "core_labs": "Core labs",
    "breast_oncology_signal": "Breast oncology signal",
    "breast_imaging": "Breast imaging",
    "pathology_or_biopsy": "Pathology or biopsy",
    "receptor_status": "Receptor status",
    "stage_or_extent": "Stage or extent",
    "performance_status": "Performance status",
    "oncology_treatment": "Oncology treatment",
}
BREAST_ONCOLOGY_CRITICAL_DOMAINS = [
    "pathology_or_biopsy",
    "receptor_status",
    "stage_or_extent",
    "performance_status",
    "oncology_treatment",
    "core_labs",
]

PHARMA_CONTROL_PLANE_DIMENSION_WEIGHTS = {
    "dataset_readiness": 20,
    "trial_contract_quality": 20,
    "patient_evidence_depth": 20,
    "linkage_provenance": 15,
    "retrieval_readiness": 10,
    "governance_posture": 10,
    "parser_ambiguity": 5,
}
PHARMA_CONTROL_PLANE_LINK_SAMPLE_LIMIT = 500
PHARMA_CONTROL_PLANE_FACT_SAMPLE_LIMIT = 600
PHARMA_CONTROL_PLANE_PROFILE_SAMPLE_LIMIT = 600
PHARMA_CONTROL_PLANE_REPORT_SAMPLE_LIMIT = 400
PHARMA_CONTROL_PLANE_TRIAL_SAMPLE_LIMIT = 1200
PHARMA_CONTROL_PLANE_COHORT_SAMPLE_LIMIT = 600
BREAST_ONCOLOGY_PATHOLOGY_HINTS = {
    "biopsy",
    "breast biopsy",
    "carcinoma",
    "ductal carcinoma",
    "lobular carcinoma",
    "pathology",
    "histology",
    "malignant",
    "invasive",
    "dcis",
    "lcis",
}
BREAST_ONCOLOGY_CATEGORY_BASE_PRIORITY = {
    "confirmed_breast_oncology": 80,
    "probable_breast_oncology": 60,
    "possible_breast_workup": 40,
    "general_oncology_signal": 20,
    "background_population": 0,
}
SPARSE_ONCOLOGY_DOMAIN_SUMMARY_VERSION = "oncology_sparse_domain_summary_v1"


def _overlay_has_value(value):
    if isinstance(value, bool):
        return True
    if value is None:
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return bool(str(value).strip())


def _overlay_merge_unique_strings(existing_values, incoming_values, *, limit=24):
    merged = []
    seen = set()
    for raw_value in list(existing_values or []) + list(incoming_values or []):
        text = str(raw_value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        merged.append(text)
        if len(merged) >= limit:
            break
    return merged


def _merge_oncology_signal_payload(existing_payload, incoming_payload):
    existing = existing_payload if isinstance(existing_payload, dict) else {}
    incoming = incoming_payload if isinstance(incoming_payload, dict) else {}
    return {
        "present": bool(existing.get("present") or incoming.get("present")),
        "matches": _overlay_merge_unique_strings(existing.get("matches") or [], incoming.get("matches") or [], limit=8),
        "supportRefs": _overlay_merge_unique_strings(existing.get("supportRefs") or [], incoming.get("supportRefs") or [], limit=8),
    }


def _infer_curation_overlay_signal_payloads(evidence_payload):
    evidence = evidence_payload if isinstance(evidence_payload, dict) else {}
    inferred = {}
    condition_terms = _overlay_merge_unique_strings([], evidence.get("conditionHistoryTerms") or [], limit=24)
    procedure_terms = _overlay_merge_unique_strings([], evidence.get("procedureHistoryTerms") or [], limit=24)
    pathology_summary = str(evidence.get("pathologySummary") or "").strip()
    pathology_blob = " ".join(condition_terms + procedure_terms + ([pathology_summary] if pathology_summary else [])).lower()
    receptor_status = evidence.get("receptorStatus") or {}
    stage_or_extent = evidence.get("stageOrExtent") or {}
    performance_status = evidence.get("performanceStatus") or {}
    treatment_history = evidence.get("oncologyTreatmentHistory") or {}
    overlay_support_refs = _overlay_merge_unique_strings([], evidence.get("supportRefs") or [], limit=8)

    def _mark_present(family_id, match_token):
        inferred[family_id] = {
            "present": True,
            "matches": _overlay_merge_unique_strings([], [match_token], limit=8),
            "supportRefs": list(overlay_support_refs),
        }

    if any(hint in pathology_blob for hint in BREAST_ONCOLOGY_PATHOLOGY_HINTS):
        _mark_present("breast_biopsy", "manual_pathology_review")
    if any(token in pathology_blob for token in ["breast", "mammary", "carcinoma", "oncology"]):
        _mark_present("breast_oncology", "manual_oncology_review")
    if any(_overlay_has_value((receptor_status or {}).get(key)) for key in ["hr", "er", "pr", "her2"]):
        _mark_present("receptor_status", "manual_receptor_review")
        inferred.setdefault("breast_oncology", {
            "present": True,
            "matches": ["manual_oncology_review"],
            "supportRefs": list(overlay_support_refs),
        })
    if any(
        key in (stage_or_extent or {}) and _overlay_has_value((stage_or_extent or {}).get(key))
        for key in ["stage", "tnm", "metastatic", "locallyAdvanced", "metastaticSites", "sites"]
    ):
        _mark_present("stage_or_extent", "manual_stage_review")
        inferred.setdefault("breast_oncology", {
            "present": True,
            "matches": ["manual_oncology_review"],
            "supportRefs": list(overlay_support_refs),
        })
    if "value" in (performance_status or {}) and (performance_status or {}).get("value") is not None:
        _mark_present("performance_status", "manual_performance_review")
    if _overlay_merge_unique_strings([], (treatment_history or {}).get("terms") or [], limit=24):
        _mark_present("oncology_treatment", "manual_treatment_review")
        inferred.setdefault("breast_oncology", {
            "present": True,
            "matches": ["manual_oncology_review"],
            "supportRefs": list(overlay_support_refs),
        })
    return inferred


def apply_patient_curation_overlay_to_fact_doc(fact_doc, overlay_doc):
    base_doc = dict(fact_doc or {})
    facts = dict(base_doc.get("facts") or {})
    overlay = overlay_doc if isinstance(overlay_doc, dict) else {}
    evidence = overlay.get("factsOverlay") or overlay.get("evidence") or {}
    if not isinstance(evidence, dict) or not evidence:
        return base_doc

    overlay_source = str(overlay.get("curationSource") or "demo_manual_review").strip() or "demo_manual_review"
    overlay_source_label = f"curated_overlay:{overlay_source}"
    overlay_updated_at = str(overlay.get("updatedAt") or overlay.get("resolvedAt") or "").strip() or None
    overlay_reviewer = str(overlay.get("reviewer") or "").strip() or None
    overlay_note = str(overlay.get("note") or evidence.get("note") or "").strip() or None
    overlay_support_refs = _overlay_merge_unique_strings([], evidence.get("supportRefs") or [], limit=12)
    curated_domains = []

    condition_terms = _overlay_merge_unique_strings([], evidence.get("conditionHistoryTerms") or [], limit=120)
    if condition_terms:
        existing = ((facts.get("conditionHistory") or {}).get("value")) or []
        facts["conditionHistory"] = {
            "value": _overlay_merge_unique_strings(existing, condition_terms, limit=120),
            "source": overlay_source_label,
        }
        curated_domains.append("conditionHistory")

    procedure_terms = _overlay_merge_unique_strings([], evidence.get("procedureHistoryTerms") or [], limit=120)
    if procedure_terms:
        existing = ((facts.get("procedureHistory") or {}).get("value")) or []
        facts["procedureHistory"] = {
            "value": _overlay_merge_unique_strings(existing, procedure_terms, limit=120),
            "source": overlay_source_label,
        }
        curated_domains.append("procedureHistory")

    pathology_summary = str(evidence.get("pathologySummary") or "").strip()
    if pathology_summary:
        facts["pathologySummary"] = {"value": pathology_summary, "source": overlay_source_label}
        curated_domains.append("pathologySummary")

    receptor_payload = evidence.get("receptorStatus") or {}
    if isinstance(receptor_payload, dict) and any(
        key in receptor_payload for key in ["hr", "er", "pr", "her2", "matches", "supportRefs"]
    ):
        existing_payload = (((facts.get("receptorStatus") or {}).get("value")) or {})
        merged_payload = dict(existing_payload)
        for key in ["hr", "er", "pr", "her2"]:
            if _overlay_has_value(receptor_payload.get(key)):
                merged_payload[key] = str(receptor_payload.get(key)).strip().lower()
        merged_payload["matches"] = _overlay_merge_unique_strings(
            existing_payload.get("matches") or [],
            receptor_payload.get("matches") or [],
            limit=8,
        )
        merged_payload["supportRefs"] = _overlay_merge_unique_strings(
            existing_payload.get("supportRefs") or [],
            (receptor_payload.get("supportRefs") or []) + overlay_support_refs,
            limit=12,
        )
        facts["receptorStatus"] = {"value": merged_payload, "source": overlay_source_label}
        curated_domains.append("receptorStatus")

    performance_payload = evidence.get("performanceStatus") or {}
    if isinstance(performance_payload, dict) and (
        "value" in performance_payload or performance_payload.get("matches") or performance_payload.get("supportRefs")
    ):
        existing_payload = (((facts.get("performanceStatus") or {}).get("value")) or {})
        merged_payload = dict(existing_payload)
        if _overlay_has_value(performance_payload.get("scale")):
            merged_payload["scale"] = str(performance_payload.get("scale") or "").strip() or "ECOG"
        else:
            merged_payload["scale"] = merged_payload.get("scale") or "ECOG"
        if "value" in performance_payload and performance_payload.get("value") is not None:
            merged_payload["value"] = int(performance_payload.get("value"))
        if _overlay_has_value(performance_payload.get("date")):
            merged_payload["date"] = str(performance_payload.get("date") or "").strip()
        merged_payload["matches"] = _overlay_merge_unique_strings(
            existing_payload.get("matches") or [],
            performance_payload.get("matches") or [],
            limit=8,
        )
        merged_payload["supportRefs"] = _overlay_merge_unique_strings(
            existing_payload.get("supportRefs") or [],
            (performance_payload.get("supportRefs") or []) + overlay_support_refs,
            limit=12,
        )
        facts["performanceStatus"] = {"value": merged_payload, "source": overlay_source_label}
        curated_domains.append("performanceStatus")

    stage_payload = evidence.get("stageOrExtent") or {}
    if isinstance(stage_payload, dict) and any(
        key in stage_payload for key in ["stage", "tnm", "metastatic", "locallyAdvanced", "metastaticSites", "sites", "matches", "supportRefs"]
    ):
        existing_payload = (((facts.get("stageOrExtent") or {}).get("value")) or {})
        merged_payload = dict(existing_payload)
        if _overlay_has_value(stage_payload.get("stage")):
            merged_payload["stage"] = str(stage_payload.get("stage") or "").strip().lower()
        merged_payload["tnm"] = _overlay_merge_unique_strings(existing_payload.get("tnm") or [], stage_payload.get("tnm") or [], limit=6)
        if "metastatic" in stage_payload and stage_payload.get("metastatic") in [True, False]:
            merged_payload["metastatic"] = bool(stage_payload.get("metastatic"))
        if "locallyAdvanced" in stage_payload and stage_payload.get("locallyAdvanced") in [True, False]:
            merged_payload["locallyAdvanced"] = bool(stage_payload.get("locallyAdvanced"))
        metastatic_sites = _overlay_merge_unique_strings(
            existing_payload.get("metastaticSites") or existing_payload.get("sites") or [],
            stage_payload.get("metastaticSites") or stage_payload.get("sites") or [],
            limit=12,
        )
        if metastatic_sites:
            merged_payload["metastaticSites"] = metastatic_sites
            merged_payload["sites"] = metastatic_sites
        merged_payload["matches"] = _overlay_merge_unique_strings(
            existing_payload.get("matches") or [],
            stage_payload.get("matches") or [],
            limit=8,
        )
        merged_payload["supportRefs"] = _overlay_merge_unique_strings(
            existing_payload.get("supportRefs") or [],
            (stage_payload.get("supportRefs") or []) + overlay_support_refs,
            limit=12,
        )
        facts["stageOrExtent"] = {"value": merged_payload, "source": overlay_source_label}
        curated_domains.append("stageOrExtent")

    treatment_payload = evidence.get("oncologyTreatmentHistory") or {}
    if isinstance(treatment_payload, dict) and (
        treatment_payload.get("terms") or treatment_payload.get("supportRefs")
    ):
        existing_payload = (((facts.get("oncologyTreatmentHistory") or {}).get("value")) or {})
        merged_payload = {
            "terms": _overlay_merge_unique_strings(existing_payload.get("terms") or [], treatment_payload.get("terms") or [], limit=24),
            "supportRefs": _overlay_merge_unique_strings(
                existing_payload.get("supportRefs") or [],
                (treatment_payload.get("supportRefs") or []) + overlay_support_refs,
                limit=12,
            ),
        }
        facts["oncologyTreatmentHistory"] = {"value": merged_payload, "source": overlay_source_label}
        curated_domains.append("oncologyTreatmentHistory")

    explicit_signal_map = evidence.get("oncologySignals") or {}
    inferred_signal_map = _infer_curation_overlay_signal_payloads(evidence)
    merged_signal_map = {}
    existing_signal_map = (((facts.get("oncologySignals") or {}).get("value")) or {})
    for family_id in set(BREAST_ONCOLOGY_SIGNAL_WEIGHTS.keys()).union(existing_signal_map.keys()).union(explicit_signal_map.keys()).union(inferred_signal_map.keys()):
        incoming_payload = explicit_signal_map.get(family_id) or inferred_signal_map.get(family_id) or {}
        if family_id in explicit_signal_map and family_id in inferred_signal_map:
            incoming_payload = _merge_oncology_signal_payload(explicit_signal_map.get(family_id), inferred_signal_map.get(family_id))
        merged_signal_map[family_id] = _merge_oncology_signal_payload(existing_signal_map.get(family_id), incoming_payload)
    if any((payload or {}).get("present") or (payload or {}).get("matches") or (payload or {}).get("supportRefs") for payload in merged_signal_map.values()):
        facts["oncologySignals"] = {"value": merged_signal_map, "source": overlay_source_label}
        curated_domains.append("oncologySignals")

    base_doc["facts"] = facts
    base_doc["curationOverlay"] = {
        "applied": bool(curated_domains),
        "overlayVersion": str(overlay.get("overlayVersion") or PHARMA_PATIENT_CURATION_OVERLAY_VERSION),
        "updatedAt": overlay_updated_at,
        "reviewer": overlay_reviewer,
        "curationSource": overlay_source,
        "note": overlay_note,
        "curatedDomains": _overlay_merge_unique_strings([], curated_domains, limit=24),
    }
    return base_doc
SPARSE_ONCOLOGY_DOMAIN_SPECS = [
    {
        "key": "receptor_status",
        "label": BREAST_ONCOLOGY_DOMAIN_LABELS["receptor_status"],
        "profileTerms": [
            "her2",
            "hormone receptor",
            "estrogen receptor",
            "progesterone receptor",
            "triple negative",
            "er positive",
            "pr positive",
            "hr+",
            "hr-",
        ],
        "reportTerms": [
            "her2",
            "her-2",
            "hormone receptor",
            "estrogen receptor",
            "progesterone receptor",
            "triple negative",
            "hr+",
            "hr-",
        ],
    },
    {
        "key": "performance_status",
        "label": BREAST_ONCOLOGY_DOMAIN_LABELS["performance_status"],
        "profileTerms": ["ecog", "performance status"],
        "reportTerms": ["ecog", "performance status"],
    },
    {
        "key": "stage_or_extent",
        "label": BREAST_ONCOLOGY_DOMAIN_LABELS["stage_or_extent"],
        "profileTerms": ["metastatic", "locally advanced", "stage iii", "stage iv", "tnm"],
        "reportTerms": ["metastatic", "locally advanced", "stage iii", "stage iv", "tnm"],
    },
    {
        "key": "oncology_treatment",
        "label": BREAST_ONCOLOGY_DOMAIN_LABELS["oncology_treatment"],
        "profileTerms": [
            "chemotherapy",
            "hormone therapy",
            "endocrine therapy",
            "trastuzumab",
            "tamoxifen",
            "taxane",
            "palbociclib",
            "letrozole",
            "ribociclib",
        ],
        "reportTerms": [
            "chemotherapy",
            "hormone therapy",
            "endocrine therapy",
            "trastuzumab",
            "tamoxifen",
            "taxane",
            "palbociclib",
            "letrozole",
            "ribociclib",
        ],
    },
]


def _normalize_sparse_control_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _domain_fact_is_present(domain_key, facts_document):
    facts_root = (facts_document or {}).get("facts") or facts_document or {}
    if domain_key == "receptor_status":
        payload = (((facts_root.get("receptorStatus") or {}).get("value")) or {})
        return bool(
            payload.get("hr")
            or payload.get("er")
            or payload.get("pr")
            or payload.get("her2")
            or payload.get("matches")
            or payload.get("supportRefs")
        )
    if domain_key == "performance_status":
        payload = (((facts_root.get("performanceStatus") or {}).get("value")) or {})
        return bool(
            payload.get("value") is not None
            or payload.get("matches")
            or payload.get("supportRefs")
        )
    if domain_key == "stage_or_extent":
        payload = (((facts_root.get("stageOrExtent") or {}).get("value")) or {})
        return bool(
            payload.get("stage")
            or payload.get("tnm")
            or payload.get("metastatic")
            or payload.get("locallyAdvanced")
            or payload.get("matches")
            or payload.get("supportRefs")
        )
    if domain_key == "oncology_treatment":
        payload = (((facts_root.get("oncologyTreatmentHistory") or {}).get("value")) or {})
        return bool(payload.get("terms") or payload.get("supportRefs"))
    return False


def _document_contains_sparse_terms(document, fields, search_terms):
    return bool(_extract_sparse_term_examples(document, fields, search_terms, max_items=1))


def _extract_sparse_term_examples(document, fields, search_terms, max_items=3):
    normalized_terms = [_normalize_sparse_control_text(item) for item in (search_terms or []) if _normalize_sparse_control_text(item)]
    if not normalized_terms:
        return []
    examples = []
    for field in fields:
        raw_value = (document or {}).get(field)
        if isinstance(raw_value, list):
            values = raw_value
        else:
            values = [raw_value]
        for value in values:
            normalized_value = _normalize_sparse_control_text(value)
            if not normalized_value:
                continue
            if any(term in normalized_value for term in normalized_terms):
                example = str(value).strip()
                if example and example not in examples:
                    examples.append(example[:120])
                    if len(examples) >= max(1, int(max_items or 3)):
                        return examples
    return examples


def build_sparse_oncology_domain_summary_from_inputs(*, patient_fact_rows, profile_rows, report_rows):
    fact_documents = list(patient_fact_rows or [])
    profile_documents = list(profile_rows or [])
    report_documents = list(report_rows or [])
    domains = []
    narrative_only = []
    source_present_unmaterialized = []
    unsupported = []

    for spec in SPARSE_ONCOLOGY_DOMAIN_SPECS:
        domain_key = spec["key"]
        fact_count = sum(1 for row in fact_documents if _domain_fact_is_present(domain_key, row))
        profile_examples = []
        profile_signal_count = 0
        for row in profile_documents:
            examples = _extract_sparse_term_examples(
                row,
                [
                    "conditionTerms",
                    "procedureTerms",
                    "diagnosticTerms",
                    "medicationTerms",
                    "observationTerms",
                    "documentTerms",
                    "featureTerms",
                ],
                spec.get("profileTerms") or [],
                max_items=2,
            )
            if not examples:
                continue
            profile_signal_count += 1
            for item in examples:
                if item not in profile_examples:
                    profile_examples.append(item)
                    if len(profile_examples) >= 3:
                        break
            if len(profile_examples) >= 3:
                continue
        report_examples = []
        report_signal_count = 0
        for row in report_documents:
            examples = _extract_sparse_term_examples(
                row,
                ["clinicalNotes", "conclusions"],
                spec.get("reportTerms") or [],
                max_items=1,
            )
            if not examples:
                continue
            report_signal_count += 1
            for item in examples:
                if item not in report_examples:
                    report_examples.append(item)
                    if len(report_examples) >= 2:
                        break
            if len(report_examples) >= 2:
                continue

        if fact_count > 0:
            status = "deterministic_in_cohort"
            tone = "pass"
            guidance = "Deterministic facts are already materialized for this domain in the governed cohort."
        elif profile_signal_count > 0:
            status = "synthea_signal_needs_materialization"
            tone = "review"
            guidance = "The canonical Synthea cohort shows source hints for this domain, but deterministic facts are still absent."
            source_present_unmaterialized.append(spec["label"])
        elif report_signal_count > 0:
            status = "leafy_narrative_only"
            tone = "review"
            guidance = "This domain is currently available only in Leafy narrative text, so trial-to-cohort screening cannot use it deterministically."
            narrative_only.append(spec["label"])
        else:
            status = "unsupported_in_current_demo_data"
            tone = "blocked"
            guidance = "Current demo sources do not expose this domain; a richer oncology cohort or supplemental data is required."
            unsupported.append(spec["label"])

        domains.append(
            {
                "key": domain_key,
                "label": spec["label"],
                "status": status,
                "tone": tone,
                "guidance": guidance,
                "patientFactCount": fact_count,
                "patientFactPct": round((fact_count / len(fact_documents)) * 100.0, 1) if fact_documents else 0.0,
                "syntheaProfileSignalCount": profile_signal_count,
                "syntheaProfileSignalPct": round((profile_signal_count / len(profile_documents)) * 100.0, 1) if profile_documents else 0.0,
                "syntheaExamples": profile_examples[:3],
                "leafyNarrativeSignalCount": report_signal_count,
                "leafyNarrativeSignalPct": round((report_signal_count / len(report_documents)) * 100.0, 1) if report_documents else 0.0,
                "leafyExamples": report_examples[:2],
            }
        )

    domains.sort(key=lambda item: (0 if item["status"] == "deterministic_in_cohort" else 1, item["label"]))
    materialized_count = sum(1 for item in domains if item["status"] == "deterministic_in_cohort")
    return {
        "summaryVersion": SPARSE_ONCOLOGY_DOMAIN_SUMMARY_VERSION,
        "patientFactDocs": len(fact_documents),
        "syntheaProfileDocs": len(profile_documents),
        "leafyReportDocs": len(report_documents),
        "deterministicDomainCount": materialized_count,
        "deterministicDomainPct": round((materialized_count / len(domains)) * 100.0, 1) if domains else 0.0,
        "domains": domains,
        "sourcePresentButUnmaterialized": source_present_unmaterialized,
        "leafyNarrativeOnly": narrative_only,
        "unsupportedInCurrentDemoData": unsupported,
    }


def normalize_link_strategy_token(value):
    token = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", token).strip("_")


def is_authoritative_link_strategy(value):
    return normalize_link_strategy_token(value) in CURRENT_AUTHORITATIVE_LINK_STRATEGIES


def extract_embedded_synthea_patient_ref(*, patient_context=None, report_doc=None, appointment_doc=None):
    context = patient_context if isinstance(patient_context, dict) else {}
    report = report_doc if isinstance(report_doc, dict) else {}
    appointment = appointment_doc if isinstance(appointment_doc, dict) else {}
    candidates = [
        context.get("syntheaPatientRef"),
        ((context.get("patientLink") or {}).get("syntheaPatientRef")),
        report.get("syntheaPatientRef"),
        ((report.get("patientLink") or {}).get("syntheaPatientRef")),
        appointment.get("syntheaPatientRef"),
        ((appointment.get("metadata") or {}).get("syntheaPatientRef")),
        (((((appointment.get("metadata") or {}).get("extraSearchParams") or {}).get("subject") or {}).get("syntheaPatientRef"))),
    ]
    for candidate in candidates:
        raw = str(candidate or "").strip()
        if not raw:
            continue
        return raw if raw.startswith("Patient/") else f"Patient/{raw}"
    return ""


def extract_embedded_synthea_link_strategy(*, patient_context=None, report_doc=None, appointment_doc=None):
    context = patient_context if isinstance(patient_context, dict) else {}
    report = report_doc if isinstance(report_doc, dict) else {}
    appointment = appointment_doc if isinstance(appointment_doc, dict) else {}
    candidates = [
        context.get("syntheaLinkStrategy"),
        ((context.get("patientLink") or {}).get("linkStrategy")),
        report.get("syntheaLinkStrategy"),
        ((report.get("patientLink") or {}).get("linkStrategy")),
        appointment.get("syntheaLinkStrategy"),
        ((appointment.get("metadata") or {}).get("syntheaLinkStrategy")),
        (((((appointment.get("metadata") or {}).get("extraSearchParams") or {}).get("subject") or {}).get("syntheaLinkStrategy"))),
    ]
    for candidate in candidates:
        normalized = normalize_link_strategy_token(candidate)
        if normalized:
            return normalized
    return ""


def extract_authoritative_embedded_synthea_patient_ref(*, patient_context=None, report_doc=None, appointment_doc=None):
    strategy = extract_embedded_synthea_link_strategy(
        patient_context=patient_context,
        report_doc=report_doc,
        appointment_doc=appointment_doc,
    )
    if not is_authoritative_link_strategy(strategy):
        return ""
    return extract_embedded_synthea_patient_ref(
        patient_context=patient_context,
        report_doc=report_doc,
        appointment_doc=appointment_doc,
    )


def build_link_identity_snapshot(*, patient_context=None, report_doc=None, appointment_doc=None):
    context = patient_context if isinstance(patient_context, dict) else {}
    report = report_doc if isinstance(report_doc, dict) else {}
    appointment = appointment_doc if isinstance(appointment_doc, dict) else {}
    leafy_patient_id = str(
        context.get("patientId")
        or report.get("patientID")
        or report.get("patient-id")
        or ""
    ).strip()
    leafy_appointment_id = str(context.get("appointmentId") or report.get("appointmentID") or "").strip()
    leafy_report_id = str(context.get("reportId") or report.get("_id") or "").strip()
    return {
        "leafyPatientId": leafy_patient_id,
        "leafyAppointmentId": leafy_appointment_id,
        "leafyReportId": leafy_report_id,
        "leafyPatientName": context.get("patientName") or report.get("user") or "",
        "ageSnapshot": context.get("age"),
        "sexSnapshot": context.get("sex"),
        "biradsSnapshot": context.get("biradsValues") or [],
        "centerSnapshot": context.get("center") or ((appointment.get("resource") or {}).get("location") or {}).get("display"),
    }


def build_link_lookup_clauses(identity_snapshot):
    snapshot = identity_snapshot if isinstance(identity_snapshot, dict) else {}
    lookup_clauses = []
    if snapshot.get("leafyPatientId"):
        lookup_clauses.append({"leafyPatientId": snapshot.get("leafyPatientId")})
    if snapshot.get("leafyAppointmentId"):
        lookup_clauses.append({"leafyAppointmentId": snapshot.get("leafyAppointmentId")})
    if snapshot.get("leafyReportId"):
        lookup_clauses.append({"leafyReportId": snapshot.get("leafyReportId")})
    return lookup_clauses


def classify_link_record(record):
    row = record if isinstance(record, dict) else {}
    normalized_strategy = normalize_link_strategy_token(row.get("linkStrategy"))
    normalized_version = str(row.get("linkageVersion") or "").strip()
    synthea_ref = str(row.get("syntheaPatientRef") or "").strip()
    authoritative = bool(
        synthea_ref
        and normalized_strategy in AUTHORITATIVE_LINK_STRATEGIES
        and normalized_version == PHARMA_LINKAGE_CONTRACT_VERSION
    )
    legacy_heuristic = bool(synthea_ref and not authoritative)
    if authoritative:
        linkage_status = "linked"
    elif legacy_heuristic:
        linkage_status = "legacy_heuristic_link"
    else:
        linkage_status = str(row.get("linkageStatus") or "").strip().lower() or "unlinked"
    return {
        "authoritative": authoritative,
        "legacyHeuristic": legacy_heuristic,
        "normalizedStrategy": normalized_strategy or None,
        "linkageStatus": linkage_status,
    }


def choose_screening_backfill_resolution(*, identity_snapshot=None, candidate_options=None, deterministic_ref=None):
    snapshot = identity_snapshot if isinstance(identity_snapshot, dict) else {}
    options = list(candidate_options or [])
    normalized_deterministic_ref = str(deterministic_ref or "").strip()
    if normalized_deterministic_ref and not normalized_deterministic_ref.startswith("Patient/"):
        normalized_deterministic_ref = f"Patient/{normalized_deterministic_ref}"

    screening_seed = (
        str(snapshot.get("leafyAppointmentId") or "").strip()
        or str(snapshot.get("leafyReportId") or "").strip()
        or str(snapshot.get("leafyPatientId") or "").strip()
        or str(snapshot.get("leafyPatientName") or "").strip()
    )
    if not screening_seed:
        return None

    for option in options:
        patient_ref = str((option or {}).get("patientRef") or "").strip()
        if not patient_ref:
            continue
        normalized_ref = patient_ref if patient_ref.startswith("Patient/") else f"Patient/{patient_ref}"
        return {
            "suggestedSyntheaPatientRef": normalized_ref,
            "linkStrategy": "candidate_only",
            "linkageStatus": "candidate_only",
            "linkageConfidence": 0.0,
            "linkageEvidence": [
                "Retired automatic screening backfill is preserved as a suggestion only; an authoritative link must be resolved explicitly.",
            ],
        }

    if normalized_deterministic_ref:
        return {
            "suggestedSyntheaPatientRef": normalized_deterministic_ref,
            "linkStrategy": "candidate_only",
            "linkageStatus": "candidate_only",
            "linkageConfidence": 0.0,
            "linkageEvidence": [
                "Retired automatic screening backfill is preserved as a deterministic suggestion only; an authoritative link must be resolved explicitly.",
            ],
        }

    return None


def build_linkage_control_plane_summary_from_rows(rows):
    documents = list(rows or [])
    status_counts = defaultdict(int)
    strategy_counts = defaultdict(int)
    unresolved_samples = []

    for document in documents:
        classification = classify_link_record(document)
        status_value = classification.get("linkageStatus") or "unlinked"
        status_counts[status_value] += 1
        strategy_value = classification.get("normalizedStrategy") or "unspecified"
        strategy_counts[strategy_value] += 1
        if status_value in {"candidate_only", "needs_review", "legacy_heuristic_link", "ambiguous", "unlinked"}:
            if len(unresolved_samples) < 12:
                unresolved_samples.append(
                    {
                        "leafyPatientId": document.get("leafyPatientId"),
                        "leafyAppointmentId": document.get("leafyAppointmentId"),
                        "leafyReportId": document.get("leafyReportId"),
                        "linkageStatus": status_value,
                        "linkStrategy": document.get("linkStrategy"),
                        "syntheaPatientRef": document.get("syntheaPatientRef"),
                        "candidateOptions": list(document.get("candidateOptions") or [])[:3],
                    }
                )

    total = len(documents)
    authoritative_count = status_counts.get("linked", 0)
    unresolved_count = total - authoritative_count
    return {
        "contractVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
        "summaryMode": "legacy_link_collection",
        "totalLinkDocuments": total,
        "authoritativeLinked": authoritative_count,
        "authoritativeLinkedPct": round((authoritative_count / total) * 100.0, 1) if total else 0.0,
        "unresolved": unresolved_count,
        "legacyHeuristicLinks": status_counts.get("legacy_heuristic_link", 0),
        "candidateOnly": status_counts.get("candidate_only", 0),
        "statusCounts": dict(status_counts),
        "strategyCounts": dict(strategy_counts),
        "unresolvedSamples": unresolved_samples,
    }


def build_linkage_control_plane_summary_from_screening_sources(
    *,
    appointment_total=0,
    appointment_with_ref=0,
    report_total=0,
    report_with_ref=0,
):
    appointment_total = int(appointment_total or 0)
    appointment_with_ref = int(appointment_with_ref or 0)
    report_total = int(report_total or 0)
    report_with_ref = int(report_with_ref or 0)
    base_total = report_total if report_total > 0 else appointment_total
    authoritative_count = report_with_ref if report_total > 0 else appointment_with_ref
    denominator = max(base_total, 1)
    unresolved_count = max(0, denominator - authoritative_count)
    status_counts = {
        "linked": authoritative_count,
        "unlinked": unresolved_count,
    }
    strategy_counts = {
        SCREENING_CAPTURE_LINK_STRATEGY: authoritative_count,
    }
    return {
        "contractVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
        "summaryMode": "embedded_screening_refs",
        "totalLinkDocuments": denominator,
        "authoritativeLinked": authoritative_count,
        "authoritativeLinkedPct": round((authoritative_count / denominator) * 100.0, 1) if denominator else 0.0,
        "unresolved": unresolved_count,
        "legacyHeuristicLinks": 0,
        "candidateOnly": 0,
        "statusCounts": status_counts,
        "strategyCounts": strategy_counts,
        "unresolvedSamples": [],
        "appointmentsTotal": appointment_total,
        "appointmentsWithSyntheaRef": appointment_with_ref,
        "reportsTotal": report_total,
        "reportsWithSyntheaRef": report_with_ref,
    }


def _blank_gap_fillability_counts():
    return {
        "questionnaire": 0,
        "site_coordinator": 0,
        "ehr_only": 0,
        "manual_review": 0,
    }


def build_gap_taxonomy_summary_from_profile_rows(profile_rows):
    rows = list(profile_rows or [])
    counts = _blank_gap_fillability_counts()
    feature_counters = {key: Counter() for key in counts}
    total_gaps = 0
    patients_with_gaps = 0

    for row in rows:
        profile = (row.get("patientMatchProfile") or row or {})
        gaps = list(profile.get("gaps") or [])
        if gaps:
            patients_with_gaps += 1
        for gap in gaps:
            total_gaps += 1
            fillable_by = normalize_gap_fillability((gap or {}).get("fillableBy"))
            counts[fillable_by] += 1
            feature = str((gap or {}).get("feature") or "unknown").strip() or "unknown"
            feature_counters[fillable_by][feature] += 1

    return {
        "totalGaps": total_gaps,
        "patientsWithGaps": patients_with_gaps,
        "counts": counts,
        "topByFillability": {
            key: [{"feature": feature, "count": count} for feature, count in counter.most_common(5)]
            for key, counter in feature_counters.items()
        },
    }


def build_trial_criterion_group_hotspot_summary_from_rows(trial_rows):
    rows = list(trial_rows or [])
    grouped = defaultdict(
        lambda: {
            "criterionGroup": "general",
            "totalCriteria": 0,
            "deterministicCriteria": 0,
            "manualReviewCriteria": 0,
            "narrativeCriteria": 0,
            "nonDeterministicCriteria": 0,
            "inclusionCriteria": 0,
            "exclusionCriteria": 0,
        }
    )

    def update_group(group_name, *, criterion_type, computability):
        normalized_group = str(group_name or "general").strip() or "general"
        row = grouped[normalized_group]
        row["criterionGroup"] = normalized_group
        row["totalCriteria"] += 1
        if str(criterion_type or "").strip().lower() == "exclusion":
            row["exclusionCriteria"] += 1
        else:
            row["inclusionCriteria"] += 1

        normalized_computability = str(computability or "").strip().lower()
        if normalized_computability == "deterministic":
            row["deterministicCriteria"] += 1
            return
        if normalized_computability == "narrative_adjudication":
            row["narrativeCriteria"] += 1
            row["nonDeterministicCriteria"] += 1
            return
        row["manualReviewCriteria"] += 1
        row["nonDeterministicCriteria"] += 1

    for trial_row in rows:
        canonical_criteria = list((((trial_row.get("trialDefinitionCanonical") or {}).get("criteria")) or []))
        if canonical_criteria:
            for criterion in canonical_criteria:
                rule_kind = str(criterion.get("ruleKind") or "patient_eligibility").strip().lower()
                if rule_kind not in {"", "patient_eligibility"}:
                    continue
                update_group(
                    ((criterion.get("ontologyBindings") or {}).get("primaryCriterionGroup"))
                    or criterion.get("criterionSubcategory")
                    or criterion.get("criterionCategory")
                    or "general",
                    criterion_type=criterion.get("criterionType"),
                    computability=criterion.get("computability"),
                )
            continue

        for rule in (trial_row.get("pharma_trial_rules") or []):
            rule_kind = str(rule.get("rule_kind") or "patient_eligibility").strip().lower()
            if rule_kind not in {"", "patient_eligibility"}:
                continue
            if bool(rule.get("deterministic_candidate")):
                computability = "deterministic"
            elif bool(rule.get("llm_candidate")):
                computability = "narrative_adjudication"
            else:
                computability = "manual_review"
            update_group(
                rule.get("primary_criterion_group") or "general",
                criterion_type=rule.get("type"),
                computability=computability,
            )

    hotspots = sorted(
        grouped.values(),
        key=lambda row: (
            -1 * int(row.get("nonDeterministicCriteria") or 0),
            -1 * int(row.get("totalCriteria") or 0),
            str(row.get("criterionGroup") or ""),
        ),
    )
    return {
        "totalGroups": len(grouped),
        "hotspots": hotspots[:8],
    }


def build_trial_control_plane_summary_from_inputs(
    *,
    readiness_summary,
    oncology_quality_summary,
    linkage_summary,
    trial_rows,
    sparse_domain_summary=None,
    patient_profile_rows=None,
):
    rows = list(trial_rows or [])
    gap_taxonomy_summary = build_gap_taxonomy_summary_from_profile_rows(patient_profile_rows or [])
    criterion_group_summary = build_trial_criterion_group_hotspot_summary_from_rows(rows)
    parser_warning_counts = defaultdict(int)
    patient_rule_count = 0
    deterministic_rule_count = 0
    llm_candidate_rule_count = 0
    canonical_definition_count = 0
    canonical_criterion_count = 0

    for row in rows:
        canonical_definition = row.get("trialDefinitionCanonical") or {}
        if str(canonical_definition.get("contractVersion") or "") == PHARMA_TRIAL_DEFINITION_CANONICAL_VERSION:
            canonical_definition_count += 1
            canonical_summary = canonical_definition.get("summary") or {}
            canonical_criterion_count += int(canonical_summary.get("totalCriteria") or len(canonical_definition.get("criteria") or []))
        for warning in (row.get("parserWarnings") or []):
            code = str((warning or {}).get("code") or "unknown").strip() or "unknown"
            parser_warning_counts[code] += 1
        for rule in (row.get("pharma_trial_rules") or []):
            if str(rule.get("rule_kind") or "patient_eligibility").strip().lower() not in {"", "patient_eligibility"}:
                continue
            patient_rule_count += 1
            if bool(rule.get("deterministic_candidate")):
                deterministic_rule_count += 1
            if bool(rule.get("llm_candidate")):
                llm_candidate_rule_count += 1

    deterministic_rule_pct = round((deterministic_rule_count / patient_rule_count) * 100.0, 1) if patient_rule_count else 0.0
    backlog = []
    if (linkage_summary or {}).get("legacyHeuristicLinks", 0) > 0:
        backlog.append("Review and replace legacy heuristic Leafy-Synthea links with authoritative manual resolutions.")
    if (linkage_summary or {}).get("candidateOnly", 0) > 0:
        backlog.append("Resolve candidate-only Leafy-Synthea links before relying on longitudinal patient evidence.")
    if deterministic_rule_pct < 80.0:
        backlog.append("Expand deterministic oncology fact coverage so more trial rules avoid manual review.")
    if rows and canonical_definition_count < len(rows):
        backlog.append("Backfill CDISC-aligned canonical trial definitions so every study has a governed criterion contract.")
    if isinstance(oncology_quality_summary, dict):
        top_backlog = oncology_quality_summary.get("topEnrichmentBacklog") or []
        if top_backlog:
            backlog.append(
                "Prioritize oncology fact enrichment for "
                + ", ".join(str(item.get("domain") or "") for item in top_backlog[:3] if str(item.get("domain") or "").strip())
                + "."
            )
    if isinstance(sparse_domain_summary, dict):
        if sparse_domain_summary.get("sourcePresentButUnmaterialized"):
            backlog.append(
                "Materialize sparse oncology domains from existing Synthea profile signals: "
                + ", ".join(sparse_domain_summary.get("sourcePresentButUnmaterialized")[:3])
                + "."
            )
        elif sparse_domain_summary.get("leafyNarrativeOnly"):
            backlog.append(
                "Promote grounded Leafy narrative extraction into governed evidence or load richer cohort data for "
                + ", ".join(sparse_domain_summary.get("leafyNarrativeOnly")[:3])
                + "."
            )
        elif sparse_domain_summary.get("unsupportedInCurrentDemoData"):
            backlog.append(
                "Supplement the canonical cohort with richer oncology data for "
                + ", ".join(sparse_domain_summary.get("unsupportedInCurrentDemoData")[:3])
                + "."
            )
    top_non_deterministic_groups = [
        row.get("criterionGroup")
        for row in (criterion_group_summary.get("hotspots") or [])
        if int(row.get("nonDeterministicCriteria") or 0) > 0
    ]
    if top_non_deterministic_groups:
        backlog.append(
            "Reduce non-deterministic burden in canonical criterion groups: "
            + ", ".join(str(item) for item in top_non_deterministic_groups[:3] if str(item).strip())
            + "."
        )
    top_ehr_only_features = (gap_taxonomy_summary.get("topByFillability") or {}).get("ehr_only") or []
    if top_ehr_only_features:
        backlog.append(
            "Backfill deterministic patient evidence for "
            + ", ".join(str(item.get("feature") or "") for item in top_ehr_only_features[:3] if str(item.get("feature") or "").strip())
            + " so non-questionnaire gaps shrink."
        )

    return {
        "controlPlaneVersion": PHARMA_TRIAL_MATCH_CONTROL_PLANE_VERSION,
        "runtimePosture": {
            "patientLinkagePolicy": "authoritative_link_required",
            "heuristicAutoLinkingEnabled": False,
            "retrievalAffectsEligibility": False,
            "fallbackSyntheticPatientSelectionEnabled": False,
        },
        "datasetReadiness": readiness_summary,
        "oncologyCohortQuality": oncology_quality_summary,
        "linkage": linkage_summary,
        "gapTaxonomy": gap_taxonomy_summary,
        "trialContracts": {
            "trialCount": len(rows),
            "canonicalDefinitionCount": canonical_definition_count,
            "canonicalDefinitionPct": round((canonical_definition_count / len(rows)) * 100.0, 1) if rows else 0.0,
            "canonicalCriterionCount": canonical_criterion_count,
            "patientEligibilityRuleCount": patient_rule_count,
            "deterministicRuleCount": deterministic_rule_count,
            "llmCandidateRuleCount": llm_candidate_rule_count,
            "deterministicRulePct": deterministic_rule_pct,
            "prefilterCoverage": {
                "hardAgeFilters": sum(1 for row in rows if (((row.get("trialPrefilterContract") or {}).get("hardFilters") or {}).get("age"))),
                "hardSexFilters": sum(1 for row in rows if (((row.get("trialPrefilterContract") or {}).get("hardFilters") or {}).get("sex"))),
                "softBiradsFilters": sum(1 for row in rows if (((row.get("trialPrefilterContract") or {}).get("softFilters") or {}).get("birads"))),
            },
            "parserWarningCounts": dict(parser_warning_counts),
            "criterionGroupCount": criterion_group_summary.get("totalGroups") or 0,
            "criterionGroupHotspots": criterion_group_summary.get("hotspots") or [],
        },
        "sparseOncologyDomains": sparse_domain_summary,
        "priorityBacklog": backlog[:8],
    }


def _normalize_control_plane_percent(value, default=0.0):
    try:
        numeric = float(value)
    except Exception:
        return float(default)
    return max(0.0, min(100.0, numeric))


def _control_plane_dimension_status(score):
    numeric = _normalize_control_plane_percent(score)
    if numeric >= 85.0:
        return "supported"
    if numeric >= 60.0:
        return "partial"
    return "gap"


def _serialize_summary_snapshot(summary):
    snapshot = dict(summary or {})
    snapshot.pop("recentEvaluationRuns", None)
    snapshot.pop("recentImprovementRuns", None)
    snapshot.pop("latestEvaluationRun", None)
    snapshot.pop("latestImprovementRun", None)
    return snapshot


def score_trial_matching_control_plane_summary(summary, *, mode="runtime_snapshot"):
    control_summary = summary if isinstance(summary, dict) else {}
    dataset_readiness = control_summary.get("datasetReadiness") or {}
    trial_contracts = control_summary.get("trialContracts") or {}
    sparse_domains = control_summary.get("sparseOncologyDomains") or {}
    linkage = control_summary.get("linkage") or {}
    runtime_posture = control_summary.get("runtimePosture") or {}
    oncology_quality = control_summary.get("oncologyCohortQuality") or {}
    readiness_checks = list(dataset_readiness.get("checks") or [])
    runtime_counts = dataset_readiness.get("runtimeCounts") or {}
    prefilter_coverage = trial_contracts.get("prefilterCoverage") or {}

    if readiness_checks:
        passed_checks = sum(1 for item in readiness_checks if item.get("passed"))
        readiness_score = round((passed_checks / max(len(readiness_checks), 1)) * 100.0, 1)
        if dataset_readiness.get("ready") is True:
            readiness_score = max(readiness_score, 95.0)
        elif dataset_readiness.get("ready") is False:
            readiness_score = min(readiness_score, 55.0)
    else:
        if dataset_readiness.get("ready") is True:
            readiness_score = 100.0
        elif dataset_readiness.get("ready") is False:
            readiness_score = 35.0
        else:
            readiness_score = 60.0

    canonical_pct = _normalize_control_plane_percent(trial_contracts.get("canonicalDefinitionPct"))
    deterministic_pct = _normalize_control_plane_percent(trial_contracts.get("deterministicRulePct"))
    trial_contract_score = round((canonical_pct * 0.55) + (deterministic_pct * 0.45), 1)

    deterministic_domain_pct = _normalize_control_plane_percent(sparse_domains.get("deterministicDomainPct"))
    candidate_coverage_pct = _normalize_control_plane_percent(oncology_quality.get("candidateAverageCoveragePct"))
    patient_evidence_score = round((deterministic_domain_pct * 0.7) + (candidate_coverage_pct * 0.3), 1)

    authoritative_link_pct = _normalize_control_plane_percent(linkage.get("authoritativeLinkedPct"))
    linkage_penalty = min(25.0, float(linkage.get("legacyHeuristicLinks") or 0) * 8.0)
    linkage_penalty += min(25.0, float(linkage.get("candidateOnly") or 0) * 4.0)
    linkage_score = round(max(0.0, authoritative_link_pct - linkage_penalty), 1)

    trial_count = max(int(trial_contracts.get("trialCount") or 0), 1)
    hard_filter_counts = [
        _normalize_control_plane_percent((float(prefilter_coverage.get("hardAgeFilters") or 0) / trial_count) * 100.0),
        _normalize_control_plane_percent((float(prefilter_coverage.get("hardSexFilters") or 0) / trial_count) * 100.0),
        _normalize_control_plane_percent((float(prefilter_coverage.get("softBiradsFilters") or 0) / trial_count) * 100.0),
    ]
    prefilter_score = sum(hard_filter_counts) / len(hard_filter_counts)
    embedding_score = 100.0 if int(runtime_counts.get("patientProfileEmbeddings") or 0) > 0 else 20.0
    retrieval_score = round((embedding_score * 0.6) + (prefilter_score * 0.4), 1)

    governance_score = 100.0
    if runtime_posture.get("heuristicAutoLinkingEnabled"):
        governance_score -= 45.0
    if runtime_posture.get("retrievalAffectsEligibility"):
        governance_score -= 35.0
    if runtime_posture.get("fallbackSyntheticPatientSelectionEnabled"):
        governance_score -= 20.0
    governance_score = round(max(0.0, governance_score), 1)

    parser_warning_total = sum(int(value or 0) for value in (trial_contracts.get("parserWarningCounts") or {}).values())
    parser_warning_density = _normalize_control_plane_percent((parser_warning_total / trial_count) * 100.0)
    llm_candidate_ratio = _normalize_control_plane_percent(
        (float(trial_contracts.get("llmCandidateRuleCount") or 0) / max(float(trial_contracts.get("patientEligibilityRuleCount") or 1), 1.0)) * 100.0
    )
    parser_ambiguity_score = round(max(0.0, 100.0 - (parser_warning_density * 0.45) - (llm_candidate_ratio * 0.35)), 1)

    dimensions = [
        {
            "id": "dataset_readiness",
            "label": "Dataset readiness",
            "score": readiness_score,
            "weight": PHARMA_CONTROL_PLANE_DIMENSION_WEIGHTS["dataset_readiness"],
            "summary": "Runtime data readiness, counts, and gating checks for the canonical Synthea cohort.",
            "observed": {
                "ready": dataset_readiness.get("ready"),
                "patientCount": dataset_readiness.get("patientCount"),
                "failedChecks": len([item for item in readiness_checks if not item.get("passed")]),
            },
            "recommendedAction": None if readiness_score >= 85.0 else "Repair the failing readiness checks or refresh the canonical cohort before trusting screening outputs.",
        },
        {
            "id": "trial_contract_quality",
            "label": "Trial contract quality",
            "score": trial_contract_score,
            "weight": PHARMA_CONTROL_PLANE_DIMENSION_WEIGHTS["trial_contract_quality"],
            "summary": "How completely the catalog is represented as governed canonical trial definitions and deterministic rules.",
            "observed": {
                "canonicalDefinitionPct": canonical_pct,
                "deterministicRulePct": deterministic_pct,
                "trialCount": trial_contracts.get("trialCount"),
            },
            "recommendedAction": None if trial_contract_score >= 85.0 else "Backfill canonical trial definitions and expand deterministic rule binding coverage.",
        },
        {
            "id": "patient_evidence_depth",
            "label": "Patient evidence depth",
            "score": patient_evidence_score,
            "weight": PHARMA_CONTROL_PLANE_DIMENSION_WEIGHTS["patient_evidence_depth"],
            "summary": "Coverage of deterministic oncology domains and average evidence completeness across the governed cohort.",
            "observed": {
                "deterministicDomainPct": deterministic_domain_pct,
                "candidateAverageCoveragePct": candidate_coverage_pct,
            },
            "recommendedAction": None if patient_evidence_score >= 85.0 else "Enrich sparse oncology domains so fewer criteria fall to UNKNOWN during adjudication.",
        },
        {
            "id": "linkage_provenance",
            "label": "Linkage & provenance",
            "score": linkage_score,
            "weight": PHARMA_CONTROL_PLANE_DIMENSION_WEIGHTS["linkage_provenance"],
            "summary": "Authoritative patient linkage posture and embedded screening capture quality.",
            "observed": {
                "authoritativeLinkedPct": authoritative_link_pct,
                "legacyHeuristicLinks": linkage.get("legacyHeuristicLinks") or 0,
                "candidateOnly": linkage.get("candidateOnly") or 0,
            },
            "recommendedAction": None if linkage_score >= 85.0 else "Resolve remaining patient linkage gaps so patient-specific evidence stays authoritative end to end.",
        },
        {
            "id": "retrieval_readiness",
            "label": "Retrieval readiness",
            "score": retrieval_score,
            "weight": PHARMA_CONTROL_PLANE_DIMENSION_WEIGHTS["retrieval_readiness"],
            "summary": "Vector/search accelerators and deterministic prefilters that improve recall without deciding eligibility.",
            "observed": {
                "patientProfileEmbeddings": runtime_counts.get("patientProfileEmbeddings") or 0,
                "prefilterCoverage": prefilter_coverage,
            },
            "recommendedAction": None if retrieval_score >= 85.0 else "Backfill patient profile embeddings or broaden safe prefilters so candidate search is reproducible and efficient.",
        },
        {
            "id": "governance_posture",
            "label": "Governance posture",
            "score": governance_score,
            "weight": PHARMA_CONTROL_PLANE_DIMENSION_WEIGHTS["governance_posture"],
            "summary": "Whether heuristics are disabled and retrieval remains subordinate to deterministic criterion adjudication.",
            "observed": {
                "heuristicAutoLinkingEnabled": bool(runtime_posture.get("heuristicAutoLinkingEnabled")),
                "retrievalAffectsEligibility": bool(runtime_posture.get("retrievalAffectsEligibility")),
                "fallbackSyntheticPatientSelectionEnabled": bool(runtime_posture.get("fallbackSyntheticPatientSelectionEnabled")),
            },
            "recommendedAction": None if governance_score >= 85.0 else "Disable heuristic shortcuts in the truth path and keep ranking subordinate to deterministic evidence.",
        },
        {
            "id": "parser_ambiguity",
            "label": "Parser ambiguity",
            "score": parser_ambiguity_score,
            "weight": PHARMA_CONTROL_PLANE_DIMENSION_WEIGHTS["parser_ambiguity"],
            "summary": "How much of the catalog still depends on ambiguous rule text, parser warnings, or LLM-candidate-only criteria.",
            "observed": {
                "parserWarningDensityPct": parser_warning_density,
                "llmCandidateRulePct": llm_candidate_ratio,
            },
            "recommendedAction": None if parser_ambiguity_score >= 85.0 else "Reduce parser ambiguity by improving CDISC-aligned criterion extraction and canonical rule typing.",
        },
    ]

    weighted_points = []
    status_counts = defaultdict(int)
    hotspots = []
    for item in dimensions:
        score = _normalize_control_plane_percent(item.get("score"))
        weight = float(item.get("weight") or 0)
        points = round((score / 100.0) * weight, 2)
        status = _control_plane_dimension_status(score)
        dimension = {
            **item,
            "score": score,
            "points": points,
            "status": status,
        }
        weighted_points.append(points)
        status_counts[status] += 1
        if status != "supported":
            hotspots.append(
                {
                    "id": dimension["id"],
                    "label": dimension["label"],
                    "score": score,
                    "status": status,
                    "recommendedAction": dimension.get("recommendedAction"),
                }
            )
        item.clear()
        item.update(dimension)

    overall_score = round(sum(weighted_points), 1)
    overview = {
        "overallScore": overall_score,
        "status": _control_plane_dimension_status(overall_score),
        "supportedCount": status_counts.get("supported", 0),
        "partialCount": status_counts.get("partial", 0),
        "gapCount": status_counts.get("gap", 0),
        "dimensionsTotal": len(dimensions),
        "mode": str(mode or "runtime_snapshot"),
    }
    return {
        "overview": overview,
        "dimensions": dimensions,
        "hotspots": hotspots[:6],
        "recommendations": [
            item.get("recommendedAction")
            for item in dimensions
            if item.get("recommendedAction")
        ][:6],
    }


def build_trial_matching_evaluation_run_document(*, summary, mode="runtime_snapshot", previous_run=None, note=""):
    summary_snapshot = _serialize_summary_snapshot(summary)
    score_payload = score_trial_matching_control_plane_summary(summary_snapshot, mode=mode)
    created_at = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
    run_hash = hashlib.sha1(
        json.dumps(
            {
                "createdAt": created_at,
                "mode": mode,
                "summary": summary_snapshot,
            },
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:10]
    run_id = f"pharma-eval-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{run_hash}"
    previous_overall = None
    if isinstance(previous_run, dict):
        previous_overall = ((previous_run.get("overview") or {}).get("overallScore"))
    current_overall = ((score_payload.get("overview") or {}).get("overallScore"))
    score_delta = None
    if previous_overall is not None:
        try:
            score_delta = round(float(current_overall) - float(previous_overall), 1)
        except Exception:
            score_delta = None
    return {
        "runId": run_id,
        "runType": "clinical_trial_matching_evaluation",
        "runVersion": PHARMA_TRIAL_MATCH_EVALUATION_RUN_VERSION,
        "createdAt": created_at,
        "mode": str(mode or "runtime_snapshot"),
        "note": str(note or "").strip(),
        "overview": {
            **(score_payload.get("overview") or {}),
            "previousOverallScore": previous_overall,
            "scoreDelta": score_delta,
        },
        "dimensions": score_payload.get("dimensions") or [],
        "hotspots": score_payload.get("hotspots") or [],
        "recommendations": score_payload.get("recommendations") or list(summary_snapshot.get("priorityBacklog") or [])[:6],
        "summarySnapshot": summary_snapshot,
    }


def build_trial_matching_improvement_run_document(*, evaluation_run, previous_run=None):
    current_run = evaluation_run if isinstance(evaluation_run, dict) else {}
    previous = previous_run if isinstance(previous_run, dict) else {}
    current_dimensions = {
        str(item.get("id")): item
        for item in (current_run.get("dimensions") or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    previous_dimensions = {
        str(item.get("id")): item
        for item in (previous.get("dimensions") or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    improved = []
    regressed = []
    stable_gaps = []
    for dimension_id, current_dimension in current_dimensions.items():
        current_score = _normalize_control_plane_percent(current_dimension.get("score"))
        current_status = _control_plane_dimension_status(current_score)
        previous_score = _normalize_control_plane_percent((previous_dimensions.get(dimension_id) or {}).get("score"))
        delta = round(current_score - previous_score, 1) if dimension_id in previous_dimensions else None
        if delta is not None and delta >= 2.0:
            improved.append({"id": dimension_id, "label": current_dimension.get("label"), "delta": delta})
        elif delta is not None and delta <= -2.0:
            regressed.append({"id": dimension_id, "label": current_dimension.get("label"), "delta": delta})
        if current_status == "gap":
            stable_gaps.append({"id": dimension_id, "label": current_dimension.get("label"), "score": current_score})

    created_at = current_run.get("createdAt") or datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
    improvement_id = f"pharma-improve-{str(current_run.get('runId') or '')}"
    current_overall = ((current_run.get("overview") or {}).get("overallScore"))
    previous_overall = ((previous.get("overview") or {}).get("overallScore")) if previous else None
    score_delta = None
    if previous_overall is not None:
        try:
            score_delta = round(float(current_overall) - float(previous_overall), 1)
        except Exception:
            score_delta = None
    return {
        "improvementRunId": improvement_id,
        "runType": "clinical_trial_matching_improvement",
        "runVersion": PHARMA_TRIAL_MATCH_IMPROVEMENT_RUN_VERSION,
        "createdAt": created_at,
        "evaluationRunId": current_run.get("runId"),
        "baselineEvaluationRunId": previous.get("runId") if previous else None,
        "overview": {
            "overallScore": current_overall,
            "previousOverallScore": previous_overall,
            "scoreDelta": score_delta,
            "improvedCount": len(improved),
            "regressedCount": len(regressed),
            "gapCount": len(stable_gaps),
        },
        "improvements": improved[:8],
        "regressions": regressed[:8],
        "stableGaps": stable_gaps[:8],
        "recommendedActions": list(current_run.get("recommendations") or [])[:6],
    }


def attach_trial_matching_run_drift(runs):
    serialized_runs = [dict(item or {}) for item in list(runs or [])]
    for index, item in enumerate(serialized_runs):
        previous = serialized_runs[index + 1] if index + 1 < len(serialized_runs) else None
        current_score = ((item.get("overview") or {}).get("overallScore"))
        previous_score = ((previous.get("overview") or {}).get("overallScore")) if previous else None
        delta = None
        try:
            if previous_score is not None and current_score is not None:
                delta = round(float(current_score) - float(previous_score), 1)
        except Exception:
            delta = None
        item["drift"] = {
            "previousRunId": previous.get("runId") if previous else None,
            "scoreDelta": delta,
        }
    return serialized_runs


def build_trial_prefilter_contract(*, eligibility, trial_rules):
    eligibility_payload = eligibility if isinstance(eligibility, dict) else {}
    rules = list(trial_rules or [])
    hard_filters = {}
    soft_filters = {}
    supported_groups = set()
    unsupported_groups = set()

    sex_values = [str(item).strip().lower() for item in (eligibility_payload.get("sex") or []) if str(item).strip()]
    if sex_values:
        hard_filters["sex"] = {"anyOf": sorted(list(set(sex_values)))}
        supported_groups.add("sex")

    age_min = eligibility_payload.get("ageMin")
    age_max = eligibility_payload.get("ageMax")
    if age_min is not None or age_max is not None:
        hard_filters["age"] = {"min": age_min, "max": age_max}
        supported_groups.add("age")

    birads_values = [int(value) for value in (eligibility_payload.get("biradsAny") or []) if str(value).isdigit() or isinstance(value, int)]
    if birads_values:
        soft_filters["birads"] = {
            "anyOf": sorted(list(set(birads_values))),
            "supportLevel": "semi_structured",
        }
        supported_groups.add("birads_soft")

    for rule in rules:
        rule_group = str(rule.get("primary_criterion_group") or "").strip().lower() or "general"
        concept_tags = {str(tag).strip().lower() for tag in (rule.get("concept_tags") or []) if str(tag).strip()}
        if rule_group in {"age", "sex"}:
            continue
        if "birads" in concept_tags:
            continue
        unsupported_groups.add(rule_group)

    supported_groups_list = sorted(list(supported_groups))
    unsupported_groups_list = sorted(list(unsupported_groups))
    hard_filter_count = len(hard_filters)
    return {
        "contractVersion": PHARMA_TRIAL_PREFILTER_CONTRACT_VERSION,
        "hardFilters": hard_filters,
        "softFilters": soft_filters,
        "supportedCriterionGroups": supported_groups_list,
        "unsupportedCriterionGroups": unsupported_groups_list,
        "requiresCriterionEvaluation": True,
        "hardFilterCount": hard_filter_count,
        "hardFilterCoveragePct": round((hard_filter_count / 2) * 100.0, 1) if hard_filter_count else 0.0,
    }


def resolve_trial_prefilter_contract(trial_doc):
    trial_document = trial_doc if isinstance(trial_doc, dict) else {}
    existing_contract = trial_document.get("trialPrefilterContract")
    if isinstance(existing_contract, dict) and existing_contract.get("contractVersion"):
        return existing_contract
    return build_trial_prefilter_contract(
        eligibility=trial_document.get("eligibility") or {},
        trial_rules=trial_document.get("pharma_trial_rules") or trial_document.get("atomicRules") or [],
    )


def build_trial_definition_canonical(*, trial_doc):
    trial_document = trial_doc if isinstance(trial_doc, dict) else {}
    trial_rules = list(trial_document.get("pharma_trial_rules") or trial_document.get("atomicRules") or [])
    parser_warnings = list(trial_document.get("parserWarnings") or [])

    def _normalized_token(value):
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    def _extract_context_payload(text_value):
        normalized = _normalized_token(text_value)
        if not normalized:
            return {}
        stages = sorted(list(set(re.findall(r"\bstage\s+[0-9ivx]+\b", normalized, flags=re.IGNORECASE))))[:4]
        cohorts = sorted(list(set(re.findall(r"\bcohort\s+[0-9a-z\-]+\b", normalized, flags=re.IGNORECASE))))[:4]
        arms = sorted(
            list(
                set(
                    re.findall(r"\b(?:arm\s+[a-z0-9\-]+|control arm|treatment arm)\b", normalized, flags=re.IGNORECASE)
                )
            )
        )[:4]
        parts = sorted(list(set(re.findall(r"\bpart\s+[a-z0-9\-]+\b", normalized, flags=re.IGNORECASE))))[:4]
        payload = {}
        if stages:
            payload["stages"] = [item.title() for item in stages]
        if cohorts:
            payload["cohorts"] = [item.title() for item in cohorts]
        if arms:
            payload["arms"] = [item.title() for item in arms]
        if parts:
            payload["parts"] = [item.title() for item in parts]
        return payload

    def _infer_category_payload(rule):
        normalized_text = _normalized_token(rule.get("text"))
        primary_group = _normalized_token(rule.get("primary_criterion_group"))
        concept_tags = {_normalized_token(tag) for tag in (rule.get("concept_tags") or []) if _normalized_token(tag)}
        rule_kind = _normalized_token(rule.get("rule_kind") or "patient_eligibility")

        if rule_kind not in {"", "patient_eligibility"}:
            return {
                "category": "protocol_workflow",
                "subcategory": primary_group or rule_kind or "workflow",
                "codeBase": "PROC",
                "usdmDomain": "studyDesign",
            }
        if "ecog" in normalized_text or "performance status" in normalized_text:
            return {
                "category": "performance_status",
                "subcategory": "ecog",
                "codeBase": "PERF",
                "usdmDomain": "eligibilityCriteria",
            }
        if any(token in normalized_text for token in ["her2", "estrogen receptor", "progesterone receptor", "er positive", "pr positive"]):
            return {
                "category": "biomarker",
                "subcategory": "receptor_status",
                "codeBase": "BIOMKR",
                "usdmDomain": "eligibilityCriteria",
            }
        if any(token in normalized_text for token in ["pregnant", "pregnancy", "breastfeeding", "lactating"]):
            return {
                "category": "reproductive_status",
                "subcategory": "pregnancy_lactation",
                "codeBase": "REPRO",
                "usdmDomain": "eligibilityCriteria",
            }
        if primary_group in {"age", "sex"} or concept_tags.intersection({"age", "sex"}):
            return {
                "category": "demographics",
                "subcategory": primary_group or "demographics",
                "codeBase": "DEMOG",
                "usdmDomain": "eligibilityCriteria",
            }
        if primary_group in {"labs"} or "labs" in concept_tags:
            return {
                "category": "laboratory",
                "subcategory": primary_group or "laboratory",
                "codeBase": "LAB",
                "usdmDomain": "eligibilityCriteria",
            }
        if primary_group in {"medications", "time_window", "hormonal"} or concept_tags.intersection({"medications", "time_window", "menopause"}):
            return {
                "category": "prior_concomitant_therapy",
                "subcategory": primary_group or "therapy_history",
                "codeBase": "THRPY",
                "usdmDomain": "eligibilityCriteria",
            }
        if primary_group in {"conditions", "risk", "pathology"} or concept_tags.intersection({"conditions", "risk", "pathology"}):
            return {
                "category": "disease_characteristics",
                "subcategory": primary_group or "disease_state",
                "codeBase": "DISEAS",
                "usdmDomain": "eligibilityCriteria",
            }
        if primary_group in {"allergy"} or "allergy" in concept_tags:
            return {
                "category": "safety_history",
                "subcategory": primary_group or "safety_history",
                "codeBase": "SAFE",
                "usdmDomain": "eligibilityCriteria",
            }
        return {
            "category": "general_eligibility",
            "subcategory": primary_group or "general",
            "codeBase": "ELIG",
            "usdmDomain": "eligibilityCriteria",
        }

    if not trial_rules:
        return {
            "contractVersion": PHARMA_TRIAL_DEFINITION_CANONICAL_VERSION,
            "standards": {
                "trialDefinition": "CDISC USDM aligned",
                "criterionIdentity": "CDISC ODM inspired",
                "criterionTabulation": "CDISC SDTM TI inspired",
                "patientOutcome": "CDISC IE compatible",
            },
            "studyIdentifiers": {
                "trialId": trial_document.get("trialId") or trial_document.get("nctId"),
                "nctId": trial_document.get("nctId") or trial_document.get("trialId"),
                "title": trial_document.get("title") or "",
                "source": trial_document.get("source") or "",
            },
            "summary": {
                "totalCriteria": 0,
                "inclusionCriteria": 0,
                "exclusionCriteria": 0,
                "patientEligibilityCriteria": 0,
                "deterministicCriteria": 0,
                "llmCandidateCriteria": 0,
                "nonPatientCriteria": 0,
                "categoryCounts": {},
                "computabilityCounts": {},
                "parserWarningCount": len(parser_warnings),
            },
            "criteria": [],
        }

    category_counts = defaultdict(int)
    computability_counts = defaultdict(int)
    criteria = []
    inclusion_count = 0
    exclusion_count = 0
    patient_eligibility_count = 0
    deterministic_count = 0
    llm_candidate_count = 0
    non_patient_count = 0

    for ordinal, rule in enumerate(trial_rules, start=1):
        if not isinstance(rule, dict):
            continue
        criterion_type = "exclusion" if _normalized_token(rule.get("type")) == "exclusion" else "inclusion"
        if criterion_type == "exclusion":
            exclusion_count += 1
        else:
            inclusion_count += 1

        category_payload = _infer_category_payload(rule)
        rule_kind = _normalized_token(rule.get("rule_kind") or "patient_eligibility")
        if rule_kind in {"", "patient_eligibility"}:
            patient_eligibility_count += 1
        else:
            non_patient_count += 1

        if bool(rule.get("deterministic_candidate")):
            computability = "deterministic"
            deterministic_count += 1
        elif bool(rule.get("llm_candidate")):
            computability = "narrative_adjudication"
            llm_candidate_count += 1
        elif rule_kind not in {"", "patient_eligibility"}:
            computability = "non_patient_protocol_rule"
        else:
            computability = "manual_review"

        category_counts[category_payload["category"]] += 1
        computability_counts[computability] += 1

        criterion_id = f"IE-{criterion_type[:3].upper()}-{ordinal:03d}"
        ietestcd_prefix = "IN" if criterion_type == "inclusion" else "EX"
        matcher_binding = dict(rule)
        matcher_binding["canonicalCriterionId"] = criterion_id

        criteria.append(
            {
                "criterionId": criterion_id,
                "ordinal": ordinal,
                "criterionType": criterion_type,
                "criterionText": str(rule.get("text") or "").strip(),
                "criterionCategory": category_payload["category"],
                "criterionSubcategory": category_payload["subcategory"],
                "ruleKind": rule_kind or "patient_eligibility",
                "computability": computability,
                "cohortContext": _extract_context_payload(rule.get("text") or ""),
                "ontologyBindings": {
                    "conceptTags": list(rule.get("concept_tags") or []),
                    "primaryCriterionGroup": rule.get("primary_criterion_group") or "general",
                    "dataNeeded": list(rule.get("data_needed") or []),
                },
                "predicate": {
                    "operator": rule.get("operator"),
                    "value": rule.get("value"),
                    "valueMax": rule.get("value_max"),
                    "timeWindowDays": ((rule.get("time_window") or {}).get("days")),
                },
                "cdisc": {
                    "alignment": "cdisc_aligned",
                    "usdmDomain": category_payload["usdmDomain"],
                    "sdtmTi": {
                        "tirl": criterion_type.upper(),
                        "ietestcd": f"{ietestcd_prefix}{category_payload['codeBase']}"[:8],
                        "ietest": str(rule.get("text") or "").strip()[:200],
                        "iecat": category_payload["category"],
                        "iescat": category_payload["subcategory"],
                    },
                },
                "matcherBinding": matcher_binding,
            }
        )

    return {
        "contractVersion": PHARMA_TRIAL_DEFINITION_CANONICAL_VERSION,
        "generatedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "standards": {
            "trialDefinition": "CDISC USDM aligned",
            "criterionIdentity": "CDISC ODM inspired",
            "criterionTabulation": "CDISC SDTM TI inspired",
            "patientOutcome": "CDISC IE compatible",
        },
        "studyIdentifiers": {
            "trialId": trial_document.get("trialId") or trial_document.get("nctId"),
            "nctId": trial_document.get("nctId") or trial_document.get("trialId"),
            "title": trial_document.get("title") or "",
            "source": trial_document.get("source") or "",
            "sourcePayloadHash": trial_document.get("sourcePayloadHash"),
            "trialVersionId": ((trial_document.get("trialVersion") or {}).get("versionId")),
        },
        "summary": {
            "totalCriteria": len(criteria),
            "inclusionCriteria": inclusion_count,
            "exclusionCriteria": exclusion_count,
            "patientEligibilityCriteria": patient_eligibility_count,
            "deterministicCriteria": deterministic_count,
            "llmCandidateCriteria": llm_candidate_count,
            "nonPatientCriteria": non_patient_count,
            "categoryCounts": dict(category_counts),
            "computabilityCounts": dict(computability_counts),
            "parserWarningCount": len(parser_warnings),
        },
        "criteria": criteria,
    }


def resolve_trial_definition_canonical(trial_doc):
    trial_document = trial_doc if isinstance(trial_doc, dict) else {}
    existing_definition = trial_document.get("trialDefinitionCanonical")
    if isinstance(existing_definition, dict) and existing_definition.get("contractVersion") == PHARMA_TRIAL_DEFINITION_CANONICAL_VERSION:
        return existing_definition
    return build_trial_definition_canonical(trial_doc=trial_document)


def build_trial_query_for_patient_context(patient_context):
    context = patient_context if isinstance(patient_context, dict) else {}
    clauses = []
    sex_value = str(context.get("sex") or "").strip().lower()
    age_value = context.get("age")

    if sex_value:
        clauses.append(
            {
                "$or": [
                    {"trialPrefilterContract.hardFilters.sex": {"$exists": False}},
                    {"trialPrefilterContract.hardFilters.sex.anyOf": sex_value},
                ]
            }
        )
    if isinstance(age_value, int):
        clauses.extend(
            [
                {
                    "$or": [
                        {"trialPrefilterContract.hardFilters.age.min": {"$exists": False}},
                        {"trialPrefilterContract.hardFilters.age.min": {"$lte": age_value}},
                    ]
                },
                {
                    "$or": [
                        {"trialPrefilterContract.hardFilters.age.max": {"$exists": False}},
                        {"trialPrefilterContract.hardFilters.age.max": {"$gte": age_value}},
                    ]
                },
            ]
        )
    if not clauses:
        return {}
    return {"$and": clauses}


def evaluate_patient_context_against_trial_prefilter(*, patient_context, trial_prefilter_contract):
    context = patient_context if isinstance(patient_context, dict) else {}
    contract = trial_prefilter_contract if isinstance(trial_prefilter_contract, dict) else {}
    hard_filters = contract.get("hardFilters") or {}
    soft_filters = contract.get("softFilters") or {}
    blocked_by = []
    missing_patient_data = []
    advisory_matches = []

    age_filter = hard_filters.get("age") or {}
    age_value = context.get("age")
    if age_filter:
        if age_value is None:
            missing_patient_data.append("age")
        else:
            minimum = age_filter.get("min")
            maximum = age_filter.get("max")
            if minimum is not None and age_value < int(minimum):
                blocked_by.append("age_min")
            if maximum is not None and age_value > int(maximum):
                blocked_by.append("age_max")

    sex_filter = hard_filters.get("sex") or {}
    sex_value = str(context.get("sex") or "").strip().lower()
    if sex_filter:
        allowed = {str(item).strip().lower() for item in (sex_filter.get("anyOf") or []) if str(item).strip()}
        if not sex_value:
            missing_patient_data.append("sex")
        elif allowed and sex_value not in allowed:
            blocked_by.append("sex")

    birads_filter = soft_filters.get("birads") or {}
    if birads_filter:
        patient_birads = {int(value) for value in (context.get("biradsValues") or []) if str(value).isdigit() or isinstance(value, int)}
        allowed_birads = {int(value) for value in (birads_filter.get("anyOf") or []) if str(value).isdigit() or isinstance(value, int)}
        if patient_birads and allowed_birads and patient_birads.intersection(allowed_birads):
            advisory_matches.append("birads")

    return {
        "passesHardFilters": len(blocked_by) == 0,
        "blockedBy": blocked_by,
        "missingPatientData": missing_patient_data,
        "advisoryMatches": advisory_matches,
    }


def evaluate_profile_against_trial_prefilter(*, profile_doc, trial_prefilter_contract):
    profile = profile_doc if isinstance(profile_doc, dict) else {}
    synthetic_context = {
        "age": profile.get("age"),
        "sex": profile.get("gender"),
        "biradsValues": [],
    }
    return evaluate_patient_context_against_trial_prefilter(
        patient_context=synthetic_context,
        trial_prefilter_contract=trial_prefilter_contract,
    )


def build_profile_query_from_trial_prefilter_contract(trial_prefilter_contract):
    contract = trial_prefilter_contract if isinstance(trial_prefilter_contract, dict) else {}
    hard_filters = contract.get("hardFilters") or {}
    query = {}

    def _coerce_int(value):
        try:
            return int(value)
        except Exception:
            return None

    sex_filter = hard_filters.get("sex") or {}
    normalized_sex_values = []
    for item in (sex_filter.get("anyOf") or []):
        token = re.sub(r"\s+", " ", str(item or "")).strip().lower()
        if token:
            normalized_sex_values.append(token)
    if normalized_sex_values:
        query["gender"] = {"$in": sorted(list(set(normalized_sex_values)))}

    age_filter = hard_filters.get("age") or {}
    age_min = age_filter.get("min")
    age_max = age_filter.get("max")
    if age_min is not None or age_max is not None:
        age_clause = {}
        if age_min is not None:
            parsed_min = _coerce_int(age_min)
            if parsed_min is not None:
                age_clause["$gte"] = max(parsed_min, 0)
        if age_max is not None:
            parsed_max = _coerce_int(age_max)
            if parsed_max is not None:
                age_clause["$lte"] = max(parsed_max, 0)
        if age_clause:
            query["age"] = age_clause

    return query


def _normalize_breast_oncology_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def _coerce_unique_terms(values, *, limit=24):
    terms = []
    seen = set()
    for value in values or []:
        normalized = _normalize_breast_oncology_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(normalized)
        if len(terms) >= limit:
            break
    return terms


def _fact_value(facts, key, default=None):
    payload = (facts or {}).get(key) or {}
    if isinstance(payload, dict) and "value" in payload:
        return payload.get("value")
    return default


def _signal_present(signal_payload, family):
    return bool(((signal_payload or {}).get(family) or {}).get("present"))


def _status_is_known(value):
    return _normalize_breast_oncology_text(value) not in {"", "unknown", "conflicting"}


def derive_synthetic_ecog_payload_from_facts(facts_document):
    facts = (facts_document or {}).get("facts") or {}
    oncology_signals = _fact_value(facts, "oncologySignals", {}) or {}
    stage_or_extent = _fact_value(facts, "stageOrExtent", {}) or {}
    oncology_treatment = _fact_value(facts, "oncologyTreatmentHistory", {}) or {}
    receptor_status = _fact_value(facts, "receptorStatus", {}) or {}
    age_value = _fact_value(facts, "age")
    performance_status = _fact_value(facts, "performanceStatus", {}) or {}
    if performance_status.get("value") is not None:
        return None

    has_oncology_context = bool(
        _signal_present(oncology_signals, "breast_oncology")
        or _signal_present(oncology_signals, "breast_biopsy")
        or stage_or_extent.get("stage")
        or stage_or_extent.get("metastatic")
        or stage_or_extent.get("locallyAdvanced")
        or (oncology_treatment.get("terms") or [])
        or receptor_status.get("hr")
        or receptor_status.get("er")
        or receptor_status.get("pr")
        or receptor_status.get("her2")
    )
    if not has_oncology_context:
        return None

    score = 0
    rationale = []
    if stage_or_extent.get("metastatic"):
        score = max(score, 1)
        rationale.append("metastatic disease signal")
    elif stage_or_extent.get("locallyAdvanced") or stage_or_extent.get("stage") in {"stage iii", "stage iv"}:
        score = max(score, 1)
        rationale.append("advanced stage or extent signal")

    treatment_terms = [str(item).strip().lower() for item in (oncology_treatment.get("terms") or []) if str(item).strip()]
    if any(term in {"chemotherapy", "taxane", "anthracycline", "systemic therapy"} for term in treatment_terms):
        score = max(score, 1)
        rationale.append("systemic oncology treatment history")

    try:
        numeric_age = int(age_value)
    except Exception:
        numeric_age = None
    if numeric_age is not None and numeric_age >= 75:
        score = max(score, 1)
        rationale.append("older oncology patient age")
    if numeric_age is not None and numeric_age >= 82 and (
        stage_or_extent.get("metastatic") or treatment_terms
    ):
        score = max(score, 2)
        rationale.append("advanced age plus active oncology burden")

    return {
        "scale": "ECOG",
        "value": min(max(score, 0), 2),
        "rationale": rationale[:4] or ["synthetic demo baseline"],
    }


def build_oncology_cohort_assessment_input(*, patient_ref, facts_doc=None, profile_doc=None):
    facts_document = facts_doc if isinstance(facts_doc, dict) else {}
    profile_document = profile_doc if isinstance(profile_doc, dict) else {}
    evidence_profile = facts_document.get("patientEvidenceProfile") or {}
    resolved_features = evidence_profile.get("resolvedFeatures") or {}
    identity = evidence_profile.get("identity") or {}
    oncology = evidence_profile.get("oncology") or {}
    screening = evidence_profile.get("screeningImaging") or {}
    facts = facts_document.get("facts") or {}
    oncology_signals = _fact_value(facts, "oncologySignals", {}) or {}
    receptor_status = _fact_value(facts, "receptorStatus", {}) or {}
    performance_status = _fact_value(facts, "performanceStatus", {}) or {}
    stage_or_extent = _fact_value(facts, "stageOrExtent", {}) or {}
    oncology_treatment = _fact_value(facts, "oncologyTreatmentHistory", {}) or {}
    age_value = _fact_value(facts, "age")
    sex_value = _normalize_breast_oncology_text(_fact_value(facts, "sex"))
    key_labs = _fact_value(facts, "keyLabs", {}) or {}
    condition_history = _coerce_unique_terms(_fact_value(facts, "conditionHistory", []) or [], limit=32)
    procedure_history = _coerce_unique_terms(_fact_value(facts, "procedureHistory", []) or [], limit=32)
    medication_history = _coerce_unique_terms(_fact_value(facts, "medicationHistory", []) or [], limit=32)

    profile_terms = []
    for key in [
        "conditionTerms",
        "procedureTerms",
        "diagnosticTerms",
        "medicationTerms",
        "observationTerms",
        "documentTerms",
        "featureTerms",
    ]:
        profile_terms.extend(profile_document.get(key) or [])
    normalized_profile_terms = _coerce_unique_terms(profile_terms, limit=80)

    primary_cancer_feature = resolved_features.get("primaryCancer") or {}
    primary_cancer_resolved = primary_cancer_feature.get("resolvedValue")
    primary_cancer_text = ""
    primary_cancer_code = ""
    if isinstance(primary_cancer_resolved, dict):
        primary_cancer_text = _normalize_breast_oncology_text(primary_cancer_resolved.get("text"))
        primary_cancer_code = _normalize_breast_oncology_text(primary_cancer_resolved.get("code"))
    primary_cancer_text = primary_cancer_text or _normalize_breast_oncology_text(((oncology.get("primaryCancer") or {}).get("text")))
    primary_cancer_code = primary_cancer_code or _normalize_breast_oncology_text(((oncology.get("primaryCancer") or {}).get("code")))
    primary_cancer_status = _normalize_breast_oncology_text(
        primary_cancer_feature.get("status") or ((oncology.get("primaryCancer") or {}).get("status"))
    )
    breast_primary_confirmed = bool(
        primary_cancer_status == "present"
        and any(term in f"{primary_cancer_text} {primary_cancer_code}" for term in ["breast", "mammary"])
    )

    er_status = _normalize_breast_oncology_text(
        (resolved_features.get("ER") or {}).get("status")
        or (((oncology.get("tumorMarkers") or {}).get("ER") or {}).get("status"))
    )
    pr_status = _normalize_breast_oncology_text(
        (resolved_features.get("PR") or {}).get("status")
        or (((oncology.get("tumorMarkers") or {}).get("PR") or {}).get("status"))
    )
    her2_status = _normalize_breast_oncology_text(
        (resolved_features.get("HER2") or {}).get("status")
        or (((oncology.get("tumorMarkers") or {}).get("HER2") or {}).get("status"))
    )
    ecog_status = _normalize_breast_oncology_text(
        (resolved_features.get("ECOG") or {}).get("status")
        or ((oncology.get("performanceStatus") or {}).get("status"))
    )
    stage_group_status = _normalize_breast_oncology_text(
        (resolved_features.get("stageGroup") or {}).get("status")
        or ((oncology.get("stageGroup") or {}).get("status"))
    )
    metastatic_status = _normalize_breast_oncology_text(
        (resolved_features.get("metastaticFlag") or {}).get("status")
        or ((oncology.get("metastatic") or {}).get("status"))
    )
    treatment_status = _normalize_breast_oncology_text(
        (resolved_features.get("treatmentHistory") or {}).get("status")
        or (((evidence_profile.get("treatments") or {}).get("priorSystemicTherapy") or {}).get("status"))
    )

    pathology_or_biopsy_present = _signal_present(oncology_signals, "breast_biopsy") or any(
        any(hint in term for hint in BREAST_ONCOLOGY_PATHOLOGY_HINTS)
        for term in normalized_profile_terms
    )
    receptor_status_present = bool(
        _signal_present(oncology_signals, "receptor_status")
        or receptor_status.get("er")
        or receptor_status.get("pr")
        or receptor_status.get("her2")
        or (receptor_status.get("matches") or [])
        or any(_status_is_known(status) for status in [er_status, pr_status, her2_status])
    )
    performance_status_present = bool(
        _signal_present(oncology_signals, "performance_status")
        or performance_status.get("value") is not None
        or any("ecog" in term for term in normalized_profile_terms)
        or _status_is_known(ecog_status)
    )
    stage_or_extent_present = bool(
        _signal_present(oncology_signals, "stage_or_extent")
        or stage_or_extent.get("stage")
        or (stage_or_extent.get("tnm") or [])
        or stage_or_extent.get("metastatic")
        or stage_or_extent.get("locallyAdvanced")
        or _status_is_known(stage_group_status)
        or _status_is_known(metastatic_status)
    )
    oncology_treatment_present = bool(
        _signal_present(oncology_signals, "oncology_treatment")
        or (oncology_treatment.get("terms") or [])
        or _status_is_known(treatment_status)
    )
    screening_present = bool(screening.get("present"))

    signal_presence = {
        family: {
            "present": _signal_present(oncology_signals, family),
            "matches": sorted(list((((oncology_signals or {}).get(family) or {}).get("matches") or [])))[:8],
            "supportRefs": sorted(list((((oncology_signals or {}).get(family) or {}).get("supportRefs") or [])))[:8],
        }
        for family in BREAST_ONCOLOGY_SIGNAL_WEIGHTS
    }
    signal_presence["breast_oncology"] = {
        "present": bool(signal_presence["breast_oncology"]["present"] or breast_primary_confirmed),
        "matches": sorted(
            list(
                set(
                    signal_presence["breast_oncology"]["matches"]
                    + ([primary_cancer_text] if primary_cancer_text else [])
                    + ([primary_cancer_code] if primary_cancer_code else [])
                )
            )
        )[:8],
        "supportRefs": sorted(
            list(
                set(
                    signal_presence["breast_oncology"]["supportRefs"]
                    + list(((oncology.get("primaryCancer") or {}).get("provenance") or []))
                )
            )
        )[:8],
    }
    signal_presence["breast_imaging"] = {
        "present": bool(signal_presence["breast_imaging"]["present"] or screening_present),
        "matches": signal_presence["breast_imaging"]["matches"][:8],
        "supportRefs": sorted(
            list(
                set(signal_presence["breast_imaging"]["supportRefs"] + list(screening.get("provenance") or []))
            )
        )[:8],
    }
    signal_presence["receptor_status"] = {
        "present": receptor_status_present,
        "matches": sorted(
            list(
                set(
                    signal_presence["receptor_status"]["matches"]
                    + list(receptor_status.get("matches") or [])
                    + [status for status in [er_status, pr_status, her2_status] if _status_is_known(status)]
                )
            )
        )[:8],
        "supportRefs": sorted(
            list(
                set(
                    signal_presence["receptor_status"]["supportRefs"]
                    + list(receptor_status.get("supportRefs") or [])
                    + list(((oncology.get("tumorMarkers") or {}).get("ER") or {}).get("supportRefs") or [])
                    + list(((oncology.get("tumorMarkers") or {}).get("PR") or {}).get("supportRefs") or [])
                    + list(((oncology.get("tumorMarkers") or {}).get("HER2") or {}).get("supportRefs") or [])
                )
            )
        )[:8],
    }
    signal_presence["performance_status"] = {
        "present": performance_status_present,
        "matches": sorted(
            list(
                set(
                    signal_presence["performance_status"]["matches"]
                    + list(performance_status.get("matches") or [])
                    + ([f"ecog:{(oncology.get('performanceStatus') or {}).get('value')}"] if (oncology.get("performanceStatus") or {}).get("value") is not None else [])
                )
            )
        )[:8],
        "supportRefs": sorted(
            list(
                set(
                    signal_presence["performance_status"]["supportRefs"]
                    + list(performance_status.get("supportRefs") or [])
                    + list(((oncology.get("performanceStatus") or {}).get("provenance") or []))
                )
            )
        )[:8],
    }
    signal_presence["stage_or_extent"] = {
        "present": stage_or_extent_present,
        "matches": sorted(
            list(
                set(
                    signal_presence["stage_or_extent"]["matches"]
                    + list(stage_or_extent.get("matches") or [])
                    + ([str((oncology.get("stageGroup") or {}).get("value"))] if (oncology.get("stageGroup") or {}).get("value") else [])
                )
            )
        )[:8],
        "supportRefs": sorted(
            list(
                set(
                    signal_presence["stage_or_extent"]["supportRefs"]
                    + list(stage_or_extent.get("supportRefs") or [])
                    + list(((oncology.get("stageGroup") or {}).get("provenance") or []))
                    + list(((oncology.get("metastatic") or {}).get("provenance") or []))
                )
            )
        )[:8],
    }
    signal_presence["oncology_treatment"] = {
        "present": oncology_treatment_present,
        "matches": sorted(
            list(
                set(
                    signal_presence["oncology_treatment"]["matches"]
                    + list(oncology_treatment.get("terms") or [])
                    + list((((evidence_profile.get("treatments") or {}).get("priorSystemicTherapy") or {}).get("terms") or []))
                )
            )
        )[:8],
        "supportRefs": sorted(
            list(
                set(
                    signal_presence["oncology_treatment"]["supportRefs"]
                    + list(oncology_treatment.get("supportRefs") or [])
                    + list((((evidence_profile.get("treatments") or {}).get("priorSystemicTherapy") or {}).get("provenance") or []))
                )
            )
        )[:8],
    }

    support_refs = set()
    evidence_terms = set()
    for family_payload in signal_presence.values():
        support_refs.update(family_payload.get("supportRefs") or [])
        evidence_terms.update(family_payload.get("matches") or [])
    for lab_payload in key_labs.values():
        if isinstance(lab_payload, dict):
            support_refs.update(lab_payload.get("supportRefs") or [])
            label_value = _normalize_breast_oncology_text(lab_payload.get("label"))
            if label_value:
                evidence_terms.add(label_value)
    evidence_terms.update(normalized_profile_terms[:12])

    return {
        "assessmentInputVersion": BREAST_ONCOLOGY_COHORT_ASSESSMENT_INPUT_VERSION,
        "patientRef": patient_ref,
        "patientFactsVersion": str(facts_document.get("factVersion") or ""),
        "patientEvidenceProfileVersion": str(evidence_profile.get("schemaVersion") or ""),
        "demographics": {
            "ageYears": age_value,
            "sex": sex_value,
        },
        "historyPresence": {
            "condition_history": bool(condition_history),
            "procedure_history": bool(procedure_history),
            "medication_history": bool(medication_history),
            "core_labs": bool(key_labs),
        },
        "pathologyOrBiopsyPresent": bool(pathology_or_biopsy_present),
        "signalPresence": signal_presence,
        "resolvedFeatureSummary": {
            "primaryCancerPresent": primary_cancer_status == "present",
            "primaryCancerCode": primary_cancer_code or None,
            "primaryCancerText": primary_cancer_text or None,
            "ERKnown": _status_is_known(er_status),
            "PRKnown": _status_is_known(pr_status),
            "HER2Known": _status_is_known(her2_status),
            "stageOrExtentKnown": stage_or_extent_present,
            "performanceStatusKnown": performance_status_present,
            "treatmentHistoryKnown": oncology_treatment_present,
            "screeningPresent": screening_present,
            "linkageStatus": _normalize_breast_oncology_text(identity.get("linkageStatus")) or "unlinked",
            "criticalConflictCount": len(identity.get("linkageConflicts") or []),
        },
        "supportRefs": sorted(list(support_refs))[:20],
        "topEvidenceTerms": sorted(list(evidence_terms))[:20],
        "profileResourceEventCount": int(profile_document.get("resourceEventCount") or 0),
    }


def build_breast_oncology_cohort_document(*, patient_ref, assessment_input, source_dataset=None):
    assessment = assessment_input if isinstance(assessment_input, dict) else {}
    signal_presence = assessment.get("signalPresence") or {}
    demographics = assessment.get("demographics") or {}
    history_presence = assessment.get("historyPresence") or {}
    pathology_or_biopsy_present = bool(assessment.get("pathologyOrBiopsyPresent"))

    coverage_domains = {
        "demographics": {"present": demographics.get("ageYears") is not None and bool(demographics.get("sex")), "label": BREAST_ONCOLOGY_DOMAIN_LABELS["demographics"]},
        "condition_history": {"present": bool(history_presence.get("condition_history")), "label": BREAST_ONCOLOGY_DOMAIN_LABELS["condition_history"]},
        "procedure_history": {"present": bool(history_presence.get("procedure_history")), "label": BREAST_ONCOLOGY_DOMAIN_LABELS["procedure_history"]},
        "medication_history": {"present": bool(history_presence.get("medication_history")), "label": BREAST_ONCOLOGY_DOMAIN_LABELS["medication_history"]},
        "core_labs": {"present": bool(history_presence.get("core_labs")), "label": BREAST_ONCOLOGY_DOMAIN_LABELS["core_labs"]},
        "breast_oncology_signal": {"present": bool((signal_presence.get("breast_oncology") or {}).get("present")), "label": BREAST_ONCOLOGY_DOMAIN_LABELS["breast_oncology_signal"]},
        "breast_imaging": {"present": bool((signal_presence.get("breast_imaging") or {}).get("present")), "label": BREAST_ONCOLOGY_DOMAIN_LABELS["breast_imaging"]},
        "pathology_or_biopsy": {"present": pathology_or_biopsy_present, "label": BREAST_ONCOLOGY_DOMAIN_LABELS["pathology_or_biopsy"]},
        "receptor_status": {"present": bool((signal_presence.get("receptor_status") or {}).get("present")), "label": BREAST_ONCOLOGY_DOMAIN_LABELS["receptor_status"]},
        "stage_or_extent": {"present": bool((signal_presence.get("stage_or_extent") or {}).get("present")), "label": BREAST_ONCOLOGY_DOMAIN_LABELS["stage_or_extent"]},
        "performance_status": {"present": bool((signal_presence.get("performance_status") or {}).get("present")), "label": BREAST_ONCOLOGY_DOMAIN_LABELS["performance_status"]},
        "oncology_treatment": {"present": bool((signal_presence.get("oncology_treatment") or {}).get("present")), "label": BREAST_ONCOLOGY_DOMAIN_LABELS["oncology_treatment"]},
    }
    weighted_signal_score = sum(
        weight for family, weight in BREAST_ONCOLOGY_SIGNAL_WEIGHTS.items() if bool((signal_presence.get(family) or {}).get("present"))
    )
    coverage_pct = round(
        (sum(1 for payload in coverage_domains.values() if payload.get("present")) / len(coverage_domains)) * 100.0,
        1,
    ) if coverage_domains else 0.0

    if bool((signal_presence.get("breast_oncology") or {}).get("present")) and (
        coverage_domains["pathology_or_biopsy"]["present"]
        or bool((signal_presence.get("receptor_status") or {}).get("present"))
        or bool((signal_presence.get("stage_or_extent") or {}).get("present"))
        or bool((signal_presence.get("oncology_treatment") or {}).get("present"))
    ):
        candidate_category = "confirmed_breast_oncology"
    elif bool((signal_presence.get("breast_oncology") or {}).get("present")):
        candidate_category = "probable_breast_oncology"
    elif bool((signal_presence.get("breast_imaging") or {}).get("present")) and (
        coverage_domains["pathology_or_biopsy"]["present"]
        or bool((signal_presence.get("oncology_treatment") or {}).get("present"))
    ):
        candidate_category = "possible_breast_workup"
    elif weighted_signal_score > 0:
        candidate_category = "general_oncology_signal"
    else:
        candidate_category = "background_population"

    candidate_reasons = []
    if bool((signal_presence.get("breast_oncology") or {}).get("present")):
        candidate_reasons.append("Breast-oncology terminology present")
    if coverage_domains["pathology_or_biopsy"]["present"]:
        candidate_reasons.append("Pathology or biopsy evidence present")
    if bool((signal_presence.get("receptor_status") or {}).get("present")):
        candidate_reasons.append("Receptor-status evidence present")
    if bool((signal_presence.get("stage_or_extent") or {}).get("present")):
        candidate_reasons.append("Stage or metastatic evidence present")
    if bool((signal_presence.get("oncology_treatment") or {}).get("present")):
        candidate_reasons.append("Oncology treatment evidence present")
    if bool((signal_presence.get("breast_imaging") or {}).get("present")):
        candidate_reasons.append("Breast-imaging evidence present")

    critical_missing_domains = []
    if candidate_category != "background_population":
        critical_missing_domains = [
            domain_id
            for domain_id in BREAST_ONCOLOGY_CRITICAL_DOMAINS
            if not (coverage_domains.get(domain_id) or {}).get("present")
        ]

    critical_missing_labels = [
        (coverage_domains.get(domain_id) or {}).get("label") or BREAST_ONCOLOGY_DOMAIN_LABELS.get(domain_id) or domain_id
        for domain_id in critical_missing_domains
    ]
    if candidate_category == "background_population":
        matching_readiness_tier = "not_candidate"
    elif candidate_category == "confirmed_breast_oncology" and len(critical_missing_domains) <= 1:
        matching_readiness_tier = "ready"
    elif candidate_category in {"confirmed_breast_oncology", "probable_breast_oncology"} and len(critical_missing_domains) <= 3:
        matching_readiness_tier = "partial"
    else:
        matching_readiness_tier = "limited"

    enrichment_priority_score = min(
        100,
        BREAST_ONCOLOGY_CATEGORY_BASE_PRIORITY[candidate_category]
        + (len(critical_missing_domains) * 6)
        + max(0, 30 - int(round(coverage_pct / 4.0))),
    )
    if candidate_category == "background_population":
        enrichment_priority_score = 0

    source_info = source_dataset if isinstance(source_dataset, dict) else {}
    return {
        "patientRef": patient_ref or assessment.get("patientRef"),
        "cohortVersion": BREAST_ONCOLOGY_COHORT_VERSION,
        "generatedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "assessmentInputVersion": str(assessment.get("assessmentInputVersion") or BREAST_ONCOLOGY_COHORT_ASSESSMENT_INPUT_VERSION),
        "patientFactsVersion": str(assessment.get("patientFactsVersion") or ""),
        "patientEvidenceProfileVersion": str(assessment.get("patientEvidenceProfileVersion") or ""),
        "sourceDataset": {
            "db": source_info.get("db"),
            "collection": source_info.get("collection"),
        },
        "candidateCategory": candidate_category,
        "matchingReadinessTier": matching_readiness_tier,
        "weightedSignalScore": weighted_signal_score,
        "coveragePct": coverage_pct,
        "coverageDomains": coverage_domains,
        "criticalMissingDomains": critical_missing_domains,
        "criticalMissingDomainLabels": critical_missing_labels,
        "candidateReasons": candidate_reasons,
        "enrichmentPriorityScore": enrichment_priority_score,
        "signalPresence": signal_presence,
        "supportRefs": list(assessment.get("supportRefs") or [])[:20],
        "topEvidenceTerms": list(assessment.get("topEvidenceTerms") or [])[:20],
        "profileResourceEventCount": int(assessment.get("profileResourceEventCount") or 0),
        "resolvedFeatureSummary": assessment.get("resolvedFeatureSummary") or {},
    }


def build_breast_oncology_cohort_quality_summary(rows):
    documents = rows if isinstance(rows, list) else []
    total_patients = len(documents)
    category_counts = defaultdict(int)
    readiness_tier_counts = defaultdict(int)
    coverage_sum = 0.0
    candidate_coverage_sum = 0.0
    candidate_count = 0
    candidate_domain_counts = defaultdict(int)
    critical_gap_counts = defaultdict(int)
    highest_priority = []

    for document in documents:
        category = str(document.get("candidateCategory") or "background_population")
        tier = str(document.get("matchingReadinessTier") or "not_candidate")
        coverage_pct = float(document.get("coveragePct") or 0.0)
        category_counts[category] += 1
        readiness_tier_counts[tier] += 1
        coverage_sum += coverage_pct

        if category != "background_population":
            candidate_count += 1
            candidate_coverage_sum += coverage_pct
            for domain_id, payload in (document.get("coverageDomains") or {}).items():
                if (payload or {}).get("present"):
                    candidate_domain_counts[domain_id] += 1
            for domain_id in document.get("criticalMissingDomains") or []:
                critical_gap_counts[domain_id] += 1
            highest_priority.append(
                {
                    "patientRef": document.get("patientRef"),
                    "candidateCategory": category,
                    "matchingReadinessTier": tier,
                    "enrichmentPriorityScore": int(document.get("enrichmentPriorityScore") or 0),
                    "criticalMissingDomains": list(document.get("criticalMissingDomains") or []),
                }
            )

    highest_priority = sorted(
        highest_priority,
        key=lambda item: (
            int(item.get("enrichmentPriorityScore") or 0),
            len(item.get("criticalMissingDomains") or []),
        ),
        reverse=True,
    )[:12]

    average_coverage_pct = round((coverage_sum / total_patients), 1) if total_patients else 0.0
    candidate_average_coverage_pct = round((candidate_coverage_sum / candidate_count), 1) if candidate_count else 0.0
    candidate_domain_coverage = {}
    if candidate_count:
        for domain_id, count in candidate_domain_counts.items():
            candidate_domain_coverage[domain_id] = {
                "count": count,
                "pct": round((count / candidate_count) * 100.0, 1),
                "label": BREAST_ONCOLOGY_DOMAIN_LABELS.get(domain_id) or domain_id,
            }

    top_enrichment_backlog = [
        {
            "domain": domain_id,
            "label": BREAST_ONCOLOGY_DOMAIN_LABELS.get(domain_id) or domain_id,
            "count": count,
        }
        for domain_id, count in sorted(critical_gap_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    return {
        "generatedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "cohortVersion": BREAST_ONCOLOGY_COHORT_VERSION,
        "totalPatients": total_patients,
        "candidatePatients": candidate_count,
        "candidatePatientPct": round((candidate_count / total_patients) * 100.0, 1) if total_patients else 0.0,
        "candidateCategoryCounts": dict(category_counts),
        "matchingReadinessTierCounts": dict(readiness_tier_counts),
        "averageCoveragePct": average_coverage_pct,
        "candidateAverageCoveragePct": candidate_average_coverage_pct,
        "candidateDomainCoverage": candidate_domain_coverage,
        "topEnrichmentBacklog": top_enrichment_backlog,
        "highestPriorityPatients": highest_priority,
    }


def create_pharma_services(*, deps):
    CLINICALTRIALS_API_BASE = deps["clinicaltrials_api_base"]
    CLINICALTRIALS_TIMEOUT_SEC = deps["clinicaltrials_timeout_sec"]
    PHARMA_RESOURCE_TYPES = deps["pharma_resource_types"]
    PHARMA_TRAIL_KEYWORDS = deps["pharma_trail_keywords"]
    SYNTHEA_PATIENT_REF_CACHE = deps["synthea_patient_ref_cache"]
    collection = deps["collection"]
    db_clinical_reports_collection = deps["db_clinical_reports_collection"]
    db_leafy_synthea_links_collection = deps.get("db_leafy_synthea_links_collection")
    db_pharma_evaluation_runs_collection = deps["db_pharma_evaluation_runs_collection"]
    db_pharma_feature_edges_collection = deps.get("db_pharma_feature_edges_collection")
    db_pharma_improvement_runs_collection = deps["db_pharma_improvement_runs_collection"]
    db_pharma_patient_facts_collection = deps["db_pharma_patient_facts_collection"]
    db_pharma_patient_curation_overlays_collection = deps.get("db_pharma_patient_curation_overlays_collection")
    db_pharma_oncology_cohort_collection = deps.get("db_pharma_oncology_cohort_collection")
    db_pharma_profiles_collection = deps["db_pharma_profiles_collection"]
    db_pharma_trials_collection = deps["db_pharma_trials_collection"]
    db_synthea_resources_collection = deps["db_synthea_resources_collection"]
    SYNTHEA_FHIR_DB_NAME = deps.get("synthea_fhir_db_name") or "hc_fhir_synthea_r4_sep2019_canonical"
    SYNTHEA_RESOURCES_COLLECTION_NAME = deps.get("synthea_resources_collection_name") or "fhir_resources"
    parse_int_value = deps["parse_int_value_fn"]
    safe_float = deps["safe_float_fn"]
    serialize_doc = deps["serialize_doc_fn"]
    VOYAGE_API_KEY = deps.get("voyage_api_key")
    VOYAGE_EMBEDDING_MODEL = deps.get("voyage_embedding_model") or "voyage-3.5"
    voyage_embed_text = deps.get("voyage_embed_text_fn")
    voyage_embed_texts = deps.get("voyage_embed_texts_fn")
    PHARMA_RULE_STOPWORDS = {
        "with", "without", "within", "must", "have", "been", "from", "that", "this", "and", "the",
        "for", "into", "than", "then", "such", "will", "patient", "patients", "subject", "subjects",
        "history", "current", "trial", "study", "include", "including", "exclusion", "inclusion",
    }
    PHARMA_RULE_MEDICATION_TOKENS = {
        "metformin", "glp-1", "semaglutide", "liraglutide", "insulin", "tamoxifen",
        "warfarin", "heparin", "anticoagulant", "aspirin", "hormone", "estrogen",
    }
    PHARMA_RULE_CONDITION_TOKENS = {
        "diabetes", "stroke", "dvt", "pe", "thromboembolism", "cancer", "carcinoma", "lcis", "dcis",
        "atypia", "pregnancy", "liver", "renal", "hepatitis", "hiv",
    }
    PHARMA_RULE_RISK_PATHS = {
        "pathology": ["atypia", "atypical", "lcis", "dcis", "lobular carcinoma", "ductal carcinoma"],
        "dcis_history": ["dcis", "ductal carcinoma in situ"],
        "mammographic_density": ["dense", "density", "mammographic density", "birads 4", "birads 5"],
        "genetics": ["brca", "chek2", "atm", "genetic", "mutation", "panel"],
        "risk_model_score": ["gail", "tyrer-cuzick", "risk model", "lifetime risk", "5-year risk"],
        "family_history": ["family history", "mother", "sister", "first-degree relative"],
    }

    def build_default_pharma_trials():
        # Deprecated: synthetic seeded trials are no longer part of the matching pipeline.
        return []


    def normalize_pharma_text(value):
        if value is None:
            return ""
        return re.sub(r"\s+", " ", str(value)).strip().lower()


    def parse_age_token(age_value):
        if age_value is None:
            return None
        if isinstance(age_value, (int, float)):
            return max(int(age_value), 0)
        age_text = str(age_value).strip().upper()
        match = re.search(r"(\d{1,3})", age_text)
        if not match:
            return None
        return max(int(match.group(1)), 0)


    def parse_age_years_token(age_text):
        normalized = normalize_pharma_text(age_text)
        if not normalized or normalized in {"n/a", "na", "none", "not specified"}:
            return None
        match = re.search(r"(\d{1,3})", normalized)
        if not match:
            return None
        return max(int(match.group(1)), 0)


    def parse_birthdate_to_age(birthdate_value):
        if not birthdate_value:
            return None
        try:
            if isinstance(birthdate_value, datetime):
                birth_date = birthdate_value.date()
            else:
                birth_date = date_parser.parse(str(birthdate_value)).date()
            today = datetime.utcnow().date()
            age = today.year - birth_date.year - (
                (today.month, today.day) < (birth_date.month, birth_date.day)
            )
            return max(age, 0)
        except Exception:
            return None


    def extract_codes_and_terms(resource):
        extracted_codes = []
        extracted_terms = []

        def append_text_term(raw_value):
            if raw_value is None:
                return
            text_value = re.sub(r"<[^>]+>", " ", str(raw_value))
            normalized_value = normalize_pharma_text(text_value)
            if normalized_value:
                extracted_terms.append(normalized_value)

        def append_from_codeable_concept(codeable):
            if not isinstance(codeable, dict):
                return
            append_text_term(codeable.get("text"))
            for coding in codeable.get("coding", []) or []:
                if not isinstance(coding, dict):
                    continue
                code_system = normalize_pharma_text(coding.get("system"))
                code_value = normalize_pharma_text(coding.get("code"))
                display_value = normalize_pharma_text(coding.get("display"))
                if code_value:
                    if code_system:
                        extracted_codes.append(f"{code_system}|{code_value}")
                    extracted_codes.append(code_value)
                if display_value:
                    extracted_terms.append(display_value)

        def append_from_codeable_field(value):
            if isinstance(value, dict):
                append_from_codeable_concept(value)
            elif isinstance(value, list):
                for item in value:
                    append_from_codeable_concept(item)

        append_from_codeable_concept(resource.get("code"))
        append_from_codeable_concept(resource.get("type"))
        append_from_codeable_concept(resource.get("medicationCodeableConcept"))
        append_from_codeable_concept(resource.get("valueCodeableConcept"))
        append_from_codeable_field(resource.get("category"))
        append_from_codeable_field(resource.get("interpretation"))
        append_from_codeable_field(resource.get("bodySite"))
        append_from_codeable_field(resource.get("method"))
        append_from_codeable_field(resource.get("clinicalStatus"))
        append_from_codeable_field(resource.get("verificationStatus"))

        specimen = resource.get("specimen") or {}
        if isinstance(specimen, dict):
            append_from_codeable_concept(specimen.get("type"))

        stage_items = resource.get("stage") or []
        if isinstance(stage_items, dict):
            stage_items = [stage_items]
        for stage_item in stage_items if isinstance(stage_items, list) else []:
            if not isinstance(stage_item, dict):
                continue
            append_from_codeable_concept(stage_item.get("summary"))
            append_from_codeable_concept(stage_item.get("type"))

        for component in resource.get("component") or []:
            if not isinstance(component, dict):
                continue
            append_from_codeable_concept(component.get("code"))
            append_from_codeable_concept(component.get("valueCodeableConcept"))
            append_text_term(component.get("valueString"))
            if component.get("valueInteger") is not None:
                append_text_term(component.get("valueInteger"))
            if component.get("valueBoolean") is not None:
                append_text_term(component.get("valueBoolean"))

        append_text_term(resource.get("valueString"))
        if resource.get("valueInteger") is not None:
            append_text_term(resource.get("valueInteger"))
        if resource.get("valueBoolean") is not None:
            append_text_term(resource.get("valueBoolean"))
        return extracted_codes, extracted_terms


    def extract_resource_event_datetime(resource):
        candidate_fields = [
            resource.get("effectiveDateTime"),
            resource.get("recordedDate"),
            resource.get("issued"),
            resource.get("authoredOn"),
            resource.get("onsetDateTime"),
            resource.get("date"),
            ((resource.get("performedPeriod") or {}).get("end")),
            ((resource.get("performedPeriod") or {}).get("start")),
            ((resource.get("period") or {}).get("end")),
            ((resource.get("period") or {}).get("start")),
        ]
        for field in candidate_fields:
            if not field:
                continue
            try:
                parsed = date_parser.parse(str(field))
                if isinstance(parsed, datetime) and parsed.tzinfo is not None:
                    parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
                return parsed
            except Exception:
                continue
        return None


    def parse_birads_values_from_text(text_value):
        normalized_text = normalize_pharma_text(text_value)
        if not normalized_text:
            return []
        values = set()
        for match in re.finditer(r"bi[\s-]*rads?\s*([1-5])", normalized_text):
            try:
                values.add(int(match.group(1)))
            except Exception:
                continue
        if not values:
            digit_tokens = re.findall(r"\b([3-5])\b", normalized_text)
            if "bi-rads" in normalized_text or "birads" in normalized_text:
                for token in digit_tokens[:3]:
                    values.add(int(token))
        return sorted(list(values))


    rule_tools = create_pharma_rule_tools(
        normalize_pharma_text_fn=normalize_pharma_text,
        parse_int_value_fn=parse_int_value,
        parse_age_years_token_fn=parse_age_years_token,
        parse_birads_values_from_text_fn=parse_birads_values_from_text,
        rule_stopwords=PHARMA_RULE_STOPWORDS,
        rule_medication_tokens=PHARMA_RULE_MEDICATION_TOKENS,
        rule_condition_tokens=PHARMA_RULE_CONDITION_TOKENS,
    )
    to_nullable_float = rule_tools["to_nullable_float"]
    build_source_hash = rule_tools["build_source_hash"]
    parse_time_window_days = rule_tools["parse_time_window_days"]
    extract_numeric_bounds = rule_tools["extract_numeric_bounds"]
    tokenize_rule_text = rule_tools["tokenize_rule_text"]
    infer_rule_type = rule_tools["infer_rule_type"]
    infer_concept_tags = rule_tools["infer_concept_tags"]
    infer_data_needed = rule_tools["infer_data_needed"]
    infer_rule_kind = rule_tools["infer_rule_kind"]
    infer_primary_criterion_group = rule_tools["infer_primary_criterion_group"]
    split_eligibility_sentences = rule_tools["split_eligibility_sentences"]
    build_pharma_trial_rules = rule_tools["build_pharma_trial_rules"]
    build_rule_parser_warnings = rule_tools["build_rule_parser_warnings"]
    build_pharma_rule_artifacts = rule_tools["build_pharma_rule_artifacts"]
    build_trial_version_payload = rule_tools["build_trial_version_payload"]
    merge_trial_version_history = rule_tools["merge_trial_version_history"]
    build_rule_chunks = rule_tools["build_rule_chunks"]
    evaluate_numeric_window = rule_tools["evaluate_numeric_window"]
    parse_iso_datetime = rule_tools["parse_iso_datetime"]
    days_since = rule_tools["days_since"]
    build_text_overlap_score = rule_tools["build_text_overlap_score"]


    def normalize_ctgov_study(study_doc, condition_query, sync_meta):
        protocol = study_doc.get("protocolSection") or {}
        identification = protocol.get("identificationModule") or {}
        status_module = protocol.get("statusModule") or {}
        conditions_module = protocol.get("conditionsModule") or {}
        design_module = protocol.get("designModule") or {}
        sponsor_module = protocol.get("sponsorCollaboratorsModule") or {}
        description_module = protocol.get("descriptionModule") or {}
        eligibility_module = protocol.get("eligibilityModule") or {}
        contacts_module = protocol.get("contactsLocationsModule") or {}
        interventions_module = protocol.get("armsInterventionsModule") or {}

        nct_id = str(identification.get("nctId") or "").strip()
        if not nct_id:
            return None

        title = (
            identification.get("officialTitle")
            or identification.get("briefTitle")
            or nct_id
        )
        brief_summary = description_module.get("briefSummary") or ""
        detailed_description = description_module.get("detailedDescription") or ""
        full_summary = brief_summary or detailed_description or ""
        if detailed_description:
            full_summary = f"{brief_summary} {detailed_description}".strip()

        phase_values = design_module.get("phases") or []
        phase_text = ", ".join(phase_values) if isinstance(phase_values, list) else str(phase_values or "")
        trial_status = status_module.get("overallStatus") or "UNKNOWN"
        condition_values = conditions_module.get("conditions") or []

        lead_sponsor = sponsor_module.get("leadSponsor") or {}
        sponsor_name = lead_sponsor.get("name") or "Unknown sponsor"

        intervention_terms = []
        for item in interventions_module.get("interventions") or []:
            if isinstance(item, dict):
                if item.get("name"):
                    intervention_terms.append(str(item.get("name")))
                if item.get("description"):
                    intervention_terms.append(str(item.get("description")))
        intervention_terms = intervention_terms[:12]

        study_text_parts = [
            title,
            full_summary,
            " ".join(condition_values),
            " ".join(intervention_terms),
            str(eligibility_module.get("eligibilityCriteria") or ""),
        ]
        aggregate_text = " ".join([part for part in study_text_parts if part]).strip()
        aggregate_text_normalized = normalize_pharma_text(aggregate_text)

        include_keywords = []
        for token in PHARMA_TRAIL_KEYWORDS:
            if token in aggregate_text_normalized:
                include_keywords.append(token)

        sex_value = normalize_pharma_text(eligibility_module.get("sex") or "")
        sex_list = []
        if sex_value in {"female", "male"}:
            sex_list = [sex_value]
        elif sex_value in {"all"}:
            sex_list = ["female", "male"]

        age_min = parse_age_years_token(eligibility_module.get("minimumAge"))
        age_max = parse_age_years_token(eligibility_module.get("maximumAge"))
        birads_values = parse_birads_values_from_text(aggregate_text)[:4]

        locations = contacts_module.get("locations") or []
        site_coverage = []
        for location in locations[:80]:
            if not isinstance(location, dict):
                continue
            geo_point = location.get("geoPoint") or {}
            site_coverage.append(
                {
                    "name": location.get("facility") or "Unknown site",
                    "city": location.get("city"),
                    "state": location.get("state"),
                    "country": location.get("country"),
                    "status": location.get("status"),
                    "latitude": geo_point.get("lat"),
                    "longitude": geo_point.get("lon"),
                }
            )

        source_study_last_updated = ((status_module.get("lastUpdatePostDateStruct") or {}).get("date"))
        rule_artifacts = build_pharma_rule_artifacts(
            eligibility_module=eligibility_module,
            aggregate_text=aggregate_text,
        )
        trial_rules = rule_artifacts.get("atomicRules") or []
        trial_prefilter_contract = build_trial_prefilter_contract(
            eligibility={
                "sex": sex_list,
                "ageMin": age_min,
                "ageMax": age_max,
                "biradsAny": birads_values,
            },
            trial_rules=trial_rules,
        )
        source_hash = build_source_hash(study_doc)
        trial_version = build_trial_version_payload(
            source_hash=source_hash,
            source_label="ClinicalTrials.gov API v2",
            sync_meta=sync_meta,
            source_study_last_updated=source_study_last_updated,
        )

        document = {
            "trialId": nct_id,
            "nctId": nct_id,
            "title": title,
            "sponsor": sponsor_name,
            "phase": phase_text or "Not specified",
            "status": trial_status,
            "diseaseArea": condition_query,
            "summary": full_summary[:7000],
            "eligibility": {
                "sex": sex_list,
                "ageMin": age_min,
                "ageMax": age_max,
                "biradsAny": birads_values,
                "minImageCount": 0,
                "includeKeywordsAny": sorted(list(set(include_keywords)))[:12],
                "rawCriteria": str(eligibility_module.get("eligibilityCriteria") or "")[:12000],
            },
            "siteCoverage": site_coverage,
            "tags": sorted(list(set(include_keywords + [normalize_pharma_text(phase_text)])))[:16],
            "conditions": condition_values[:20],
            "interventions": intervention_terms[:20],
            "source": "ClinicalTrials.gov API v2",
            "sourceUrl": f"https://clinicaltrials.gov/study/{nct_id}",
            "sourceStudyLastUpdated": source_study_last_updated,
            "sourceFetchedAt": sync_meta.get("fetchedAt"),
            "sourceSyncRunId": sync_meta.get("syncRunId"),
            "sourcePayloadHash": source_hash,
            "trialVersion": trial_version,
            "trialVersionHistory": [],
            "eligibilityRawText": rule_artifacts.get("eligibilityRawText") or str(eligibility_module.get("eligibilityCriteria") or "")[:12000],
            "parserWarnings": rule_artifacts.get("parserWarnings") or [],
            "atomicRules": trial_rules,
            "pharma_trial_rules": trial_rules,
            "trialPrefilterContract": trial_prefilter_contract,
            "criteriaMatchTexts": [rule.get("rule_match_text") for rule in trial_rules if rule.get("rule_match_text")][:120],
            "criteriaChunks": build_rule_chunks(trial_rules),
            "rawSourcePayload": study_doc,
        }
        document["trialDefinitionCanonical"] = build_trial_definition_canonical(trial_doc=document)
        document["matchText"] = build_trial_match_text(document)

        # Generate vector embedding for Atlas Vector Search
        try:
            if VOYAGE_API_KEY and voyage_embed_text:
                embedding = voyage_embed_text(document["matchText"], embedding_model=VOYAGE_EMBEDDING_MODEL)
                document["vectorEmbedding"] = embedding
                document["trialVectorEmbedding"] = embedding
                document["vectorEmbeddingModel"] = VOYAGE_EMBEDDING_MODEL
                document["vectorEmbeddingUpdatedAt"] = sync_meta.get("fetchedAt")
        except Exception as embed_err:
            logging.warning(f"Trial embedding skipped for {nct_id}: {embed_err}")

        return document


    def fetch_clinicaltrials_studies(
        condition_text,
        statuses,
        days_back,
        max_studies=4000,
        page_size=1000,
    ):
        base_url = f"{CLINICALTRIALS_API_BASE.rstrip('/')}/studies"
        page_size = min(max(int(page_size), 1), 1000)
        max_studies = max(int(max_studies), 1)
        days_back = max(int(days_back), 1)
        start_date = (datetime.utcnow().date() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        status_filter = ",".join([str(item).strip() for item in statuses if str(item).strip()])
        term_filter = f"AREA[LastUpdatePostDate]RANGE[{start_date},MAX]"

        next_page_token = None
        all_studies = []
        request_count = 0
        while len(all_studies) < max_studies:
            params = {
                "query.cond": condition_text,
                "query.term": term_filter,
                "pageSize": page_size,
            }
            if status_filter:
                params["filter.overallStatus"] = status_filter
            if next_page_token:
                params["pageToken"] = next_page_token

            response = requests.get(base_url, params=params, timeout=CLINICALTRIALS_TIMEOUT_SEC)
            request_count += 1
            response.raise_for_status()
            payload = response.json()
            studies = payload.get("studies") or []
            all_studies.extend(studies)
            next_page_token = payload.get("nextPageToken")
            if not next_page_token:
                break
            if request_count >= 80:
                break

        return {
            "studies": all_studies[:max_studies],
            "requestCount": request_count,
            "daysBack": days_back,
            "statusFilter": status_filter,
            "startDate": start_date,
        }


    def build_leafy_patient_context(appointment_id, patient_id=None):
        appointment_id_str = str(appointment_id or "").strip()
        if not appointment_id_str:
            raise ValueError("appointmentId is required")

        report_doc = db_clinical_reports_collection.find_one({"appointmentID": appointment_id_str})
        if not report_doc:
            raise ValueError("Clinical report not found for appointment")

        appointment_doc = None
        try:
            appointment_doc = collection.find_one({"_id": ObjectId(appointment_id_str)})
        except Exception:
            appointment_doc = collection.find_one({"_id": appointment_id_str})

        series = report_doc.get("series") or []
        birads_values = []
        parsed_age = None
        parsed_sex = ""
        for image_entry in series:
            if not isinstance(image_entry, dict):
                continue
            image_meta = image_entry.get("imageMetadata")
            if not isinstance(image_meta, dict):
                image_meta = {}
            if parsed_age is None:
                parsed_age = parse_age_token(image_meta.get("PatientAge"))
            if not parsed_sex:
                parsed_sex = normalize_pharma_text(image_meta.get("PatientSex"))
                if parsed_sex == "f":
                    parsed_sex = "female"
                elif parsed_sex == "m":
                    parsed_sex = "male"
            birads_raw = image_meta.get("biRads")
            if isinstance(birads_raw, dict):
                birads_value = birads_raw.get("BIRAD")
            else:
                birads_value = birads_raw
            if birads_value is None:
                continue
            try:
                birads_values.append(int(birads_value))
            except Exception:
                continue

        birth_date = (
            (((appointment_doc or {}).get("metadata") or {}).get("extraSearchParams") or {})
            .get("subject", {})
            .get("birthDate")
        )
        subject_gender = normalize_pharma_text(
            (
                (((appointment_doc or {}).get("metadata") or {}).get("extraSearchParams") or {})
                .get("subject", {})
                .get("gender")
            )
            or report_doc.get("sex")
            or parsed_sex
            or ""
        )
        if subject_gender == "f":
            subject_gender = "female"
        elif subject_gender == "m":
            subject_gender = "male"
        elif subject_gender not in {"female", "male"}:
            subject_gender = ""
        if parsed_age is None:
            parsed_age = parse_birthdate_to_age(birth_date)

        raw_notes = report_doc.get("clinicalNotes") or ""
        raw_conclusions = report_doc.get("conclusions") or ""
        text_blob = f"{raw_notes} {raw_conclusions}".strip()
        keywords = []
        text_blob_normalized = normalize_pharma_text(text_blob)
        for token in PHARMA_TRAIL_KEYWORDS:
            if token in text_blob_normalized:
                keywords.append(token)

        patient_context = {
            "appointmentId": appointment_id_str,
            "patientId": str(patient_id or report_doc.get("patientID") or report_doc.get("patient-id") or "").strip(),
            "patientName": report_doc.get("user") or "Unknown",
            "age": parsed_age,
            "sex": subject_gender,
            "biradsValues": sorted(list(set(birads_values))),
            "numberImages": len(series),
            "keywords": sorted(list(set(keywords))),
            "clinicalNotes": raw_notes,
            "conclusions": raw_conclusions,
            "center": ((appointment_doc or {}).get("resource") or {}).get("location", {}).get("display"),
            "appointmentStatus": ((appointment_doc or {}).get("resource") or {}).get("status"),
            "syntheaPatientRef": extract_authoritative_embedded_synthea_patient_ref(
                patient_context=None,
                report_doc=report_doc,
                appointment_doc=appointment_doc,
            ),
            "reportDate": serialize_doc(report_doc.get("date")),
            "semanticQuery": " ".join(
                [
                    f"{subject_gender} age {parsed_age}" if parsed_age is not None and subject_gender else (f"age {parsed_age}" if parsed_age is not None else ""),
                    f"birads {' '.join([str(v) for v in birads_values])}" if birads_values else "",
                    " ".join(keywords),
                    raw_conclusions[:450],
                ]
            ).strip(),
            "reportId": str(report_doc.get("_id")),
        }
        return patient_context, serialize_doc(report_doc), serialize_doc(appointment_doc)


    def _normalize_patient_ref(patient_ref_value):
        raw = str(patient_ref_value or "").strip()
        if not raw:
            return ""
        if raw.startswith("Patient/"):
            return raw
        return f"Patient/{raw}"


    def _synthea_patient_ref_exists(patient_ref_value):
        normalized_ref = _normalize_patient_ref(patient_ref_value)
        if not normalized_ref:
            return False
        patient_id = normalized_ref.split("/")[-1]
        return bool(
            db_synthea_resources_collection.find_one(
                {
                    "$or": [
                        {"resourceType": "Patient", "resourceId": patient_id},
                        {"resource.resourceType": "Patient", "resource.id": patient_id},
                    ]
                },
                {"_id": 1},
            )
        )


    PHARMA_PATIENT_FACT_VERSION = "oncology_facts_v1"
    DATASET_READINESS_MIN_PATIENTS = max(
        1,
        parse_int_value(os.getenv("TRIAL_MATCHING_DATASET_MIN_PATIENTS"), 25, 1),
    )
    ONCOLOGY_SIGNAL_PATTERNS = {
        "breast_oncology": ["breast", "mastectomy", "lumpectomy"],
        "breast_imaging": ["mammogram", "mammography", "breast ultrasound", "breast mri"],
        "breast_biopsy": ["breast biopsy"],
        "receptor_status": [
            "her2",
            "her-2",
            "erbb2",
            "estrogen receptor",
            "progesterone receptor",
            "hormone receptor",
            "estrogen+progesterone receptor",
            "er positive",
            "pr positive",
            "triple negative",
        ],
        "performance_status": ["ecog"],
        "stage_or_extent": ["stage", "tnm", "metast", "metastatic"],
        "oncology_treatment": [
            "trastuzumab",
            "tamoxifen",
            "anastrozole",
            "letrozole",
            "paclitaxel",
            "doxorubicin",
            "chemotherapy",
            "radiation therapy",
        ],
    }
    LAB_ALIASES = {
        "bilirubin": ["bilirubin"],
        "ast": ["ast", "aspartate aminotransferase"],
        "alt": ["alt", "alanine aminotransferase"],
        "creatinine": ["creatinine"],
        "hemoglobin": ["hemoglobin", "haemoglobin"],
        "platelet": ["platelet", "platelets"],
        "anc": ["absolute neutrophil", "neutrophil"],
    }
    ONCOLOGY_SYSTEMIC_THERAPY_TOKENS = {
        "systemic therapy",
        "chemotherapy",
        "endocrine therapy",
        "hormonal therapy",
        "anti-her2",
        "immunotherapy",
        "cdk4/6",
        "cdk 4/6",
        "trastuzumab",
        "tamoxifen",
        "anastrozole",
        "letrozole",
        "paclitaxel",
        "doxorubicin",
        "palbociclib",
        "ribociclib",
        "abemaciclib",
        "taxane",
        "anthracycline",
    }
    ONCOLOGY_LOCAL_THERAPY_TOKENS = {
        "surgery",
        "radiation",
        "radiation therapy",
        "mastectomy",
        "lumpectomy",
    }
    LAB_CANONICAL_UNITS = {
        "bilirubin": "mg/dL",
        "creatinine": "mg/dL",
        "hemoglobin": "g/dL",
        "platelet": "10^3/uL",
        "anc": "10^3/uL",
        "ast": "U/L",
        "alt": "U/L",
        "viral_load": "copies/mL",
    }


    def _resolve_resource_patient_ref(resource):
        for key in ("subject", "patient", "beneficiary"):
            reference_value = ((resource.get(key) or {}).get("reference") or "").strip()
            normalized = _normalize_patient_ref(reference_value)
            if normalized:
                return normalized
        return ""


    def _build_resource_ref(resource):
        resource_type = str(resource.get("resourceType") or "").strip()
        resource_id = str(resource.get("id") or "").strip()
        if resource_type and resource_id:
            return f"{resource_type}/{resource_id}"
        if resource_id:
            return resource_id
        return resource_type


    def _append_unique_term(target_set, values, limit=12):
        added = 0
        for value in values:
            normalized = normalize_pharma_text(value)
            if not normalized or normalized in target_set:
                continue
            target_set.add(normalized)
            added += 1
            if added >= limit:
                break


    def _normalize_unit_token(unit_value):
        return normalize_pharma_text(str(unit_value or "")).replace("micro", "u").replace("μ", "u")


    def _normalize_lab_value(lab_key, numeric_value, unit_value):
        value = to_nullable_float(numeric_value)
        if value is None:
            return {"value": None, "unit": None}
        normalized_unit = _normalize_unit_token(unit_value)
        canonical_unit = LAB_CANONICAL_UNITS.get(lab_key)
        if lab_key == "bilirubin":
            if "umol" in normalized_unit:
                return {"value": round(value / 17.1, 4), "unit": canonical_unit}
            return {"value": value, "unit": canonical_unit}
        if lab_key == "creatinine":
            if "umol" in normalized_unit:
                return {"value": round(value / 88.4, 4), "unit": canonical_unit}
            return {"value": value, "unit": canonical_unit}
        if lab_key == "hemoglobin":
            if normalized_unit in {"g/l", "gm/l"}:
                return {"value": round(value / 10.0, 4), "unit": canonical_unit}
            return {"value": value, "unit": canonical_unit}
        if lab_key in {"platelet", "anc"}:
            if any(token in normalized_unit for token in ["10^3/ul", "x10^3/ul", "k/ul", "10^9/l", "x10^9/l"]):
                return {"value": value, "unit": canonical_unit}
            if any(token in normalized_unit for token in ["/ul", "/mm3", "cells/ul", "cell/ul"]):
                return {"value": round(value / 1000.0, 4), "unit": canonical_unit}
            if not normalized_unit and ((lab_key == "anc" and value >= 100) or (lab_key == "platelet" and value >= 1000)):
                return {"value": round(value / 1000.0, 4), "unit": canonical_unit}
            return {"value": value, "unit": canonical_unit}
        return {"value": value, "unit": canonical_unit or unit_value}


    def _extract_observation_numeric_payload(resource):
        value_quantity = resource.get("valueQuantity") or {}
        numeric_value = None
        if isinstance(value_quantity, dict) and value_quantity.get("value") is not None:
            numeric_value = to_nullable_float(value_quantity.get("value"))
        if numeric_value is None and resource.get("valueInteger") is not None:
            numeric_value = to_nullable_float(resource.get("valueInteger"))
        if numeric_value is None and resource.get("valueString"):
            numeric_match = re.search(r"(\d+(?:\.\d+)?)", str(resource.get("valueString")))
            if numeric_match:
                numeric_value = to_nullable_float(numeric_match.group(1))
        reference_high = None
        reference_unit = None
        for reference_range in resource.get("referenceRange") or []:
            if not isinstance(reference_range, dict):
                continue
            high_payload = reference_range.get("high") or {}
            if isinstance(high_payload, dict) and high_payload.get("value") is not None:
                reference_high = to_nullable_float(high_payload.get("value"))
                reference_unit = high_payload.get("unit")
                break
        return {
            "value": numeric_value,
            "unit": value_quantity.get("unit") if isinstance(value_quantity, dict) else None,
            "referenceRangeHigh": reference_high,
            "referenceRangeUnit": reference_unit,
        }


    def _build_signal_blob(resource, normalized_terms):
        note_texts = []
        for note in resource.get("note") or []:
            if isinstance(note, dict) and note.get("text"):
                note_texts.append(str(note.get("text")))

        component_pairs = []
        for component in resource.get("component") or []:
            if not isinstance(component, dict):
                continue
            code_text = normalize_pharma_text(((component.get("code") or {}).get("text")) or "")
            value_text = normalize_pharma_text(
                ((component.get("valueCodeableConcept") or {}).get("text"))
                or component.get("valueString")
                or component.get("valueInteger")
                or component.get("valueBoolean")
                or ""
            )
            if code_text and value_text:
                component_pairs.append(f"{code_text} {value_text}")

        stage_parts = []
        stage_items = resource.get("stage") or []
        if isinstance(stage_items, dict):
            stage_items = [stage_items]
        for stage_item in stage_items if isinstance(stage_items, list) else []:
            if not isinstance(stage_item, dict):
                continue
            summary_text = ((stage_item.get("summary") or {}).get("text")) or ""
            type_text = ((stage_item.get("type") or {}).get("text")) or ""
            if summary_text:
                stage_parts.append(str(summary_text))
            if type_text:
                stage_parts.append(str(type_text))

        raw_parts = [
            " ".join(normalized_terms),
            re.sub(r"<[^>]+>", " ", str(((resource.get("text") or {}).get("div")) or "")),
            str(resource.get("conclusion") or ""),
            str(resource.get("description") or ""),
            str(resource.get("valueString") or ""),
            str(resource.get("valueInteger") if resource.get("valueInteger") is not None else ""),
            str(resource.get("valueBoolean") if resource.get("valueBoolean") is not None else ""),
            " ".join(component_pairs),
            " ".join(stage_parts),
            " ".join(note_texts),
        ]
        return normalize_pharma_text(" ".join([part for part in raw_parts if part]))


    def _extract_receptor_status_payload(signal_blob):
        payload = {"hr": None, "er": None, "pr": None, "her2": None, "matches": []}
        normalized_blob = normalize_pharma_text(signal_blob)
        if not normalized_blob:
            return payload

        receptor_patterns = {
            "hr": ["hr", "hormone receptor", "hormone-receptor", "estrogen+progesterone receptor"],
            "er": ["er", "estrogen receptor", "estrogen receptor ag", "estrogen+progesterone receptor"],
            "pr": ["pr", "progesterone receptor", "progesterone receptor ag", "estrogen+progesterone receptor"],
            "her2": ["her2", "her-2", "erbb2", "human epidermal growth factor receptor 2"],
        }
        status_tokens = ["positive", "negative", "low", "equivocal"]
        for receptor_key, receptor_aliases in receptor_patterns.items():
            for alias in receptor_aliases:
                for status_token in status_tokens:
                    alias_pattern = re.escape(alias)
                    if (
                        f"{alias} {status_token}" in normalized_blob
                        or f"{status_token} {alias}" in normalized_blob
                        or re.search(rf"\b{alias_pattern}\b[^.;,\n]{{0,120}}\b{status_token}\b", normalized_blob)
                        or re.search(rf"\b{status_token}\b[^.;,\n]{{0,80}}\b{alias_pattern}\b", normalized_blob)
                    ):
                        payload[receptor_key] = status_token
                        payload["matches"].append(f"{receptor_key}:{status_token}")
                        break
                if payload[receptor_key]:
                    break

        grouped_match = re.search(
            r"\b(positive|negative|low|equivocal)\s+(?:status\s+)?for\s+([a-z0-9,\-\s]+)",
            normalized_blob,
        )
        if grouped_match:
            grouped_status = grouped_match.group(1)
            grouped_receptors = grouped_match.group(2)
            for receptor_key, receptor_aliases in receptor_patterns.items():
                if any(alias in grouped_receptors for alias in receptor_aliases):
                    payload[receptor_key] = payload[receptor_key] or grouped_status
                    payload["matches"].append(f"{receptor_key}:{grouped_status}")

        shorthand_patterns = [
            ("hr", r"\bhr\s*([+-])"),
            ("her2", r"\bher[\s-]?2\s*([+-])"),
            ("er", r"\ber\s*([+-])"),
            ("pr", r"\bpr\s*([+-])"),
        ]
        for receptor_key, pattern in shorthand_patterns:
            shorthand_match = re.search(pattern, normalized_blob)
            if shorthand_match:
                payload[receptor_key] = "positive" if shorthand_match.group(1) == "+" else "negative"
                payload["matches"].append(f"{receptor_key}:{payload[receptor_key]}")

        if any(marker in normalized_blob for marker in ["[presence]", "by immune stain", "by fish"]):
            global_statuses = [status for status in status_tokens if re.search(rf"\b{status}\b", normalized_blob)]
            if len(set(global_statuses)) == 1:
                global_status = global_statuses[0]
                for receptor_key, receptor_aliases in receptor_patterns.items():
                    if payload.get(receptor_key):
                        continue
                    if any(alias in normalized_blob for alias in receptor_aliases):
                        payload[receptor_key] = global_status
                        payload["matches"].append(f"{receptor_key}:{global_status}")

        if "triple negative" in normalized_blob:
            payload["hr"] = payload["hr"] or "negative"
            payload["er"] = payload["er"] or "negative"
            payload["pr"] = payload["pr"] or "negative"
            payload["her2"] = payload["her2"] or "negative"
            payload["matches"].append("triple_negative")

        if payload.get("hr") is None:
            if payload.get("er") == "positive" or payload.get("pr") == "positive":
                payload["hr"] = "positive"
                payload["matches"].append("hr:positive")
            elif payload.get("er") == "negative" and payload.get("pr") == "negative":
                payload["hr"] = "negative"
                payload["matches"].append("hr:negative")
        payload["matches"] = sorted(list(set(payload["matches"])))[:8]
        return payload


    def _extract_performance_status_payload(signal_blob, resource=None):
        normalized_blob = normalize_pharma_text(signal_blob)
        resource_payload = resource if isinstance(resource, dict) else {}
        if not normalized_blob:
            normalized_blob = ""
        match = re.search(r"ecog(?: performance status)?\s*(?:of|=|:)?\s*([0-4])", normalized_blob)
        if match:
            return {
                "scale": "ECOG",
                "value": int(match.group(1)),
                "matches": [f"ecog:{match.group(1)}"],
            }

        code_payload = resource_payload.get("code") or {}
        coding_values = []
        for coding in code_payload.get("coding") or []:
            if not isinstance(coding, dict):
                continue
            coding_values.extend(
                [
                    normalize_pharma_text(coding.get("code")),
                    normalize_pharma_text(coding.get("display")),
                    normalize_pharma_text(coding.get("system")),
                ]
            )
        code_blob = normalize_pharma_text(
            " ".join(
                [
                    normalized_blob,
                    code_payload.get("text") or "",
                    (resource_payload.get("method") or {}).get("text") or "",
                    " ".join([item for item in coding_values if item]),
                ]
            )
        )
        is_ecog_observation = (
            resource_payload.get("resourceType") == "Observation"
            and (
                "89247-1" in coding_values
                or "ecog" in code_blob
                or "performance status" in code_blob
            )
        )
        if not is_ecog_observation:
            return {"scale": "ECOG", "value": None, "matches": []}

        numeric_value = resource_payload.get("valueInteger")
        if numeric_value is None:
            numeric_value = ((resource_payload.get("valueQuantity") or {}).get("value"))
        if numeric_value is None and resource_payload.get("valueString"):
            numeric_match = re.search(r"\b([0-4])\b", str(resource_payload.get("valueString")))
            if numeric_match:
                numeric_value = int(numeric_match.group(1))
        try:
            numeric_value = int(numeric_value)
        except Exception:
            numeric_value = None
        if numeric_value is None or numeric_value < 0 or numeric_value > 4:
            return {"scale": "ECOG", "value": None, "matches": []}
        return {
            "scale": "ECOG",
            "value": numeric_value,
            "matches": [f"ecog:{numeric_value}", "ecog_observation"],
        }


    def _has_oncology_stage_context(signal_blob):
        normalized_blob = normalize_pharma_text(signal_blob)
        if not normalized_blob:
            return False
        oncology_context_tokens = [
            "breast",
            "cancer",
            "carcinoma",
            "neoplasm",
            "oncology",
            "tumor",
            "metast",
            "tnm",
            "locally advanced",
            "unresectable",
            "recurrent",
            "mastectomy",
            "lumpectomy",
            "biopsy",
            "estrogen receptor",
            "progesterone receptor",
            "her2",
            "chemotherapy",
            "radiation therapy",
            "hormonal therapy",
        ]
        return any(token in normalized_blob for token in oncology_context_tokens)


    def _extract_stage_extent_payload(signal_blob):
        normalized_blob = normalize_pharma_text(signal_blob)
        payload = {
            "stage": None,
            "tnm": [],
            "metastatic": False,
            "locallyAdvanced": False,
            "unresectable": False,
            "recurrent": False,
            "advanced": False,
            "matches": [],
        }
        if not normalized_blob:
            return payload
        if not _has_oncology_stage_context(normalized_blob):
            return payload

        stage_match = re.search(r"\bstage\s+([0-4ivx]+[a-c]?)\b", normalized_blob)
        if stage_match:
            payload["stage"] = f"stage {stage_match.group(1)}".lower()
            payload["matches"].append(payload["stage"])

        tnm_matches = sorted(list(set(re.findall(r"\b(?:t[0-4][a-c]?|n[0-3][a-c]?|m[0-1][a-c]?)\b", normalized_blob))))[:6]
        if tnm_matches:
            payload["tnm"] = [item.upper() for item in tnm_matches]
            payload["matches"].extend([item.lower() for item in tnm_matches])

        if any(token in normalized_blob for token in ["metastatic", "metastasis", "stage iv", "m1"]):
            payload["metastatic"] = True
            payload["matches"].append("metastatic")
        if payload.get("stage") and any(token in payload.get("stage") for token in ["stage iii", "stage iiib", "stage iiic"]):
            payload["locallyAdvanced"] = True
            payload["matches"].append("stage_iii")
        if any(token in normalized_blob for token in ["locally advanced", "inoperable"]):
            payload["locallyAdvanced"] = True
            payload["matches"].append("locally_advanced")
        if any(token in normalized_blob for token in ["unresectable", "not amenable to resection", "cannot be resected"]):
            payload["unresectable"] = True
            payload["matches"].append("unresectable")
            payload["locallyAdvanced"] = True
            payload["matches"].append("locally_advanced")
        if any(token in normalized_blob for token in ["recurrent", "recurrence", "relapsed"]):
            payload["recurrent"] = True
            payload["matches"].append("recurrent")
        payload["advanced"] = bool(
            payload["metastatic"] or payload["locallyAdvanced"] or payload["unresectable"] or any(
                token in normalize_pharma_text(payload.get("stage") or "") for token in ["stage iii", "stage iv"]
            )
        )
        if payload["advanced"]:
            payload["matches"].append("advanced")

        payload["matches"] = sorted(list(set(payload["matches"])))[:8]
        return payload


    def _extract_oncology_treatment_payload(signal_blob, normalized_terms):
        normalized_blob = normalize_pharma_text(signal_blob)
        terms = set()
        classes = set()
        line_hints = set()

        for token in sorted(ONCOLOGY_SYSTEMIC_THERAPY_TOKENS.union(ONCOLOGY_LOCAL_THERAPY_TOKENS)):
            if token in normalized_blob:
                terms.add(token)
        for term in normalized_terms:
            normalized_term = normalize_pharma_text(term)
            if not normalized_term:
                continue
            if any(token in normalized_term for token in ONCOLOGY_SYSTEMIC_THERAPY_TOKENS.union(ONCOLOGY_LOCAL_THERAPY_TOKENS)):
                terms.add(normalized_term)

        if any(token in normalized_blob for token in ["treatment naive", "treatment-naive", "untreated", "no prior systemic"]):
            line_hints.add("treatment_naive")
        if any(token in normalized_blob for token in ["first line", "first-line", "1l", "frontline"]):
            line_hints.add("first_line")
        if any(token in normalized_blob for token in ["second line", "second-line", "2l"]):
            line_hints.add("second_line")
        if any(token in normalized_blob for token in ["third line", "third-line", "3l"]):
            line_hints.add("third_line")
        if any(token in normalized_blob for token in ["prior therapy", "prior systemic", "previously treated", "pretreated", "prior treatment"]):
            line_hints.add("prior_therapy")

        for term in list(terms):
            if term in ONCOLOGY_SYSTEMIC_THERAPY_TOKENS or any(token in term for token in ONCOLOGY_SYSTEMIC_THERAPY_TOKENS):
                classes.add("systemic")
            if term in ONCOLOGY_LOCAL_THERAPY_TOKENS or any(token in term for token in ONCOLOGY_LOCAL_THERAPY_TOKENS):
                classes.add("local")

        prior_systemic = None
        if "treatment_naive" in line_hints and "systemic" not in classes:
            prior_systemic = False
        elif "systemic" in classes or "prior_therapy" in line_hints or any(hint in line_hints for hint in ["second_line", "third_line"]):
            prior_systemic = True
        elif "first_line" in line_hints and "systemic" not in classes:
            prior_systemic = False

        return {
            "terms": sorted(list(terms))[:24],
            "classes": sorted(list(classes))[:6],
            "lineHints": sorted(list(line_hints))[:6],
            "priorSystemicTherapy": prior_systemic,
        }


    def _extract_oncology_treatment_terms(signal_blob, normalized_terms):
        payload = _extract_oncology_treatment_payload(signal_blob, normalized_terms)
        return list(payload.get("terms") or [])


    def audit_trial_matching_dataset_readiness(min_patients=None):
        min_count = max(1, parse_int_value(min_patients, DATASET_READINESS_MIN_PATIENTS, 1))
        return build_dataset_readiness_summary(
            db_synthea_resources_collection,
            db_name=SYNTHEA_FHIR_DB_NAME,
            collection_name=SYNTHEA_RESOURCES_COLLECTION_NAME,
            min_patients=min_count,
        )


    def build_fast_trial_matching_dataset_summary(min_patients=None):
        min_count = max(1, parse_int_value(min_patients, DATASET_READINESS_MIN_PATIENTS, 1))
        patient_resource_count = db_synthea_resources_collection.count_documents(
            {
                "$or": [
                    {"resourceType": "Patient"},
                    {"resource.resourceType": "Patient"},
                ]
            }
        )
        total_resources = db_synthea_resources_collection.estimated_document_count()
        patient_profiles = db_pharma_profiles_collection.estimated_document_count()
        patient_facts = db_pharma_patient_facts_collection.estimated_document_count()
        patient_facts_current = db_pharma_patient_facts_collection.count_documents({"factVersion": PHARMA_PATIENT_FACT_VERSION})
        trial_count = db_pharma_trials_collection.estimated_document_count()
        prefilter_contracts = db_pharma_trials_collection.count_documents(
            {"trialPrefilterContract.contractVersion": PHARMA_TRIAL_PREFILTER_CONTRACT_VERSION}
        )
        canonical_definitions = db_pharma_trials_collection.count_documents(
            {"trialDefinitionCanonical.contractVersion": PHARMA_TRIAL_DEFINITION_CANONICAL_VERSION}
        )
        embedded_trial_count = db_pharma_trials_collection.count_documents({"vectorEmbedding": {"$exists": True}})
        embedded_profile_count = db_pharma_profiles_collection.count_documents({"profileVectorEmbedding": {"$exists": True}})
        oncology_cohort_count = (
            db_pharma_oncology_cohort_collection.estimated_document_count()
            if db_pharma_oncology_cohort_collection is not None
            else 0
        )

        checks = [
            {
                "id": "min_patient_count",
                "label": f"At least {min_count} patient resources",
                "passed": patient_resource_count >= min_count,
                "observed": patient_resource_count,
                "expected": min_count,
            },
            {
                "id": "patient_profiles_present",
                "label": "Patient profiles materialized",
                "passed": patient_profiles > 0,
                "observed": patient_profiles,
                "expected": "> 0",
            },
            {
                "id": "patient_facts_current",
                "label": "Current deterministic patient facts available",
                "passed": patient_facts_current > 0,
                "observed": patient_facts_current,
                "expected": "> 0",
            },
            {
                "id": "trial_catalog_present",
                "label": "Trial catalog loaded",
                "passed": trial_count > 0,
                "observed": trial_count,
                "expected": "> 0",
            },
            {
                "id": "trial_prefilter_contracts_present",
                "label": "Trial prefilter contracts materialized",
                "passed": prefilter_contracts > 0,
                "observed": prefilter_contracts,
                "expected": "> 0",
            },
            {
                "id": "trial_canonical_definitions_present",
                "label": "CDISC-aligned trial definitions materialized",
                "passed": canonical_definitions > 0,
                "observed": canonical_definitions,
                "expected": "> 0",
            },
        ]

        return {
            "summaryMode": "fast_runtime",
            "db": SYNTHEA_FHIR_DB_NAME,
            "collection": SYNTHEA_RESOURCES_COLLECTION_NAME,
            "ready": all(bool(item.get("passed")) for item in checks),
            "patientCount": patient_resource_count,
            "checks": checks,
            "runtimeCounts": {
                "totalResources": total_resources,
                "patientProfiles": patient_profiles,
                "patientFacts": patient_facts,
                "patientFactsCurrent": patient_facts_current,
                "trialCatalog": trial_count,
                "trialPrefilterContracts": prefilter_contracts,
                "trialCanonicalDefinitions": canonical_definitions,
                "trialEmbeddings": embedded_trial_count,
                "patientProfileEmbeddings": embedded_profile_count,
                "oncologyCohortDocs": oncology_cohort_count,
            },
        }


    def build_pharma_patient_facts_document(patient_ref, patient_meta, resources):
        meta = patient_meta if isinstance(patient_meta, dict) else {}
        resource_rows = resources if isinstance(resources, list) else []
        resource_type_counts = defaultdict(int)
        active_meds = set()
        med_history = set()
        condition_history = set()
        procedure_history = set()
        risk_signals = set()
        receptor_status_state = {"hr": None, "er": None, "pr": None, "her2": None, "matches": set(), "supportRefs": set()}
        performance_status_state = {"scale": "ECOG", "value": None, "matches": set(), "supportRefs": set(), "date": None}
        stage_or_extent_state = {
            "stage": None,
            "tnm": set(),
            "metastatic": False,
            "locallyAdvanced": False,
            "matches": set(),
            "supportRefs": set(),
        }
        oncology_treatment_terms = set()
        oncology_treatment_support_refs = set()
        latest_bmi = {"value": None, "date": None, "supportRefs": []}
        latest_mammo_date = None
        labs = {}
        oncology_signal_state = {
            family: {"present": False, "matches": set(), "supportRefs": set()}
            for family in ONCOLOGY_SIGNAL_PATTERNS
        }
        last_event_date = None

        for resource in resource_rows:
            resource_type = str(resource.get("resourceType") or "").strip()
            resource_ref = _build_resource_ref(resource)
            event_date = extract_resource_event_datetime(resource)
            event_date_serialized = serialize_doc(event_date) if event_date is not None else None
            if event_date is not None and (last_event_date is None or event_date > last_event_date):
                last_event_date = event_date

            extracted_codes, extracted_terms = extract_codes_and_terms(resource)
            normalized_terms = [normalize_pharma_text(item) for item in extracted_terms if normalize_pharma_text(item)]
            signal_blob = _build_signal_blob(resource, normalized_terms)
            resource_type_counts[resource_type] += 1

            for family, patterns in ONCOLOGY_SIGNAL_PATTERNS.items():
                matches = [pattern for pattern in patterns if pattern in signal_blob]
                if not matches:
                    continue
                oncology_signal_state[family]["present"] = True
                oncology_signal_state[family]["matches"].update(matches[:4])
                if resource_ref:
                    oncology_signal_state[family]["supportRefs"].add(resource_ref)
                risk_signals.update(matches[:4])

            receptor_payload = _extract_receptor_status_payload(signal_blob)
            if any(receptor_payload.get(key) for key in ["er", "pr", "her2"]) or receptor_payload.get("matches"):
                for key in ["hr", "er", "pr", "her2"]:
                    if receptor_payload.get(key) and not receptor_status_state.get(key):
                        receptor_status_state[key] = receptor_payload.get(key)
                receptor_status_state["matches"].update(receptor_payload.get("matches") or [])
                if resource_ref:
                    receptor_status_state["supportRefs"].add(resource_ref)

            performance_payload = _extract_performance_status_payload(signal_blob, resource=resource)
            performance_value = performance_payload.get("value")
            if performance_value is not None:
                existing_date = parse_iso_datetime(performance_status_state.get("date"))
                should_replace = existing_date is None or (event_date is not None and event_date > existing_date)
                if should_replace:
                    performance_status_state["value"] = performance_value
                    performance_status_state["date"] = event_date_serialized
                performance_status_state["matches"].update(performance_payload.get("matches") or [])
                if resource_ref:
                    performance_status_state["supportRefs"].add(resource_ref)

            stage_payload = _extract_stage_extent_payload(signal_blob)
            if (
                stage_payload.get("stage")
                or stage_payload.get("tnm")
                or stage_payload.get("metastatic")
                or stage_payload.get("locallyAdvanced")
                or stage_payload.get("matches")
            ):
                if stage_payload.get("stage"):
                    stage_or_extent_state["stage"] = stage_payload.get("stage")
                stage_or_extent_state["tnm"].update(stage_payload.get("tnm") or [])
                stage_or_extent_state["metastatic"] = bool(
                    stage_or_extent_state["metastatic"] or stage_payload.get("metastatic")
                )
                stage_or_extent_state["locallyAdvanced"] = bool(
                    stage_or_extent_state["locallyAdvanced"] or stage_payload.get("locallyAdvanced")
                )
                stage_or_extent_state["matches"].update(stage_payload.get("matches") or [])
                if resource_ref:
                    stage_or_extent_state["supportRefs"].add(resource_ref)

            treatment_terms = _extract_oncology_treatment_terms(signal_blob, normalized_terms)
            if treatment_terms:
                oncology_treatment_terms.update(treatment_terms[:12])
                if resource_ref:
                    oncology_treatment_support_refs.add(resource_ref)

            if resource_type == "Condition":
                _append_unique_term(condition_history, normalized_terms, limit=16)
            elif resource_type == "MedicationRequest":
                _append_unique_term(med_history, normalized_terms, limit=16)
                med_status = normalize_pharma_text(resource.get("status"))
                if med_status in {"active", "on-hold"}:
                    _append_unique_term(active_meds, normalized_terms, limit=16)
            elif resource_type == "Procedure":
                _append_unique_term(procedure_history, normalized_terms, limit=16)
            elif resource_type in {"Procedure", "DiagnosticReport", "Observation", "ImagingStudy"}:
                if any(term in signal_blob for term in ONCOLOGY_SIGNAL_PATTERNS["breast_imaging"]):
                    if latest_mammo_date is None or (event_date is not None and event_date > latest_mammo_date):
                        latest_mammo_date = event_date

            if resource_type == "Observation":
                numeric_payload = _extract_observation_numeric_payload(resource)
                numeric_value = numeric_payload.get("value")
                obs_text = " ".join(normalized_terms + extracted_codes).strip()
                if numeric_value is not None and (
                    "body mass index" in obs_text or "bmi" in obs_text or "39156-5" in obs_text
                ):
                    latest_seen = parse_iso_datetime(latest_bmi.get("date"))
                    if latest_seen is None or (event_date is not None and event_date > latest_seen):
                        latest_bmi = {
                            "value": numeric_value,
                            "date": event_date_serialized,
                            "supportRefs": [resource_ref] if resource_ref else [],
                        }

                for lab_key, aliases in LAB_ALIASES.items():
                    if not any(alias in obs_text for alias in aliases):
                        continue
                    existing_date = parse_iso_datetime((labs.get(lab_key) or {}).get("date"))
                    should_replace = (
                        lab_key not in labs
                        or existing_date is None
                        or (event_date is not None and event_date > existing_date)
                    )
                    if should_replace:
                        labs[lab_key] = {
                            "value": numeric_value,
                            "unit": numeric_payload.get("unit"),
                            "date": event_date_serialized,
                            "label": normalized_terms[0] if normalized_terms else lab_key,
                            "supportRefs": [resource_ref] if resource_ref else [],
                        }

        oncology_signals = {}
        for family, state in oncology_signal_state.items():
            oncology_signals[family] = {
                "present": bool(state["present"]),
                "matches": sorted(list(state["matches"]))[:8],
                "supportRefs": sorted(list(state["supportRefs"]))[:8],
            }

        receptor_status_payload = {
            "hr": receptor_status_state.get("hr"),
            "er": receptor_status_state.get("er"),
            "pr": receptor_status_state.get("pr"),
            "her2": receptor_status_state.get("her2"),
            "matches": sorted(list(receptor_status_state.get("matches") or []))[:8],
            "supportRefs": sorted(list(receptor_status_state.get("supportRefs") or []))[:8],
        }
        performance_status_payload = {
            "scale": "ECOG",
            "value": performance_status_state.get("value"),
            "date": performance_status_state.get("date"),
            "matches": sorted(list(performance_status_state.get("matches") or []))[:8],
            "supportRefs": sorted(list(performance_status_state.get("supportRefs") or []))[:8],
        }
        stage_or_extent_payload = {
            "stage": stage_or_extent_state.get("stage"),
            "tnm": sorted(list(stage_or_extent_state.get("tnm") or []))[:6],
            "metastatic": bool(stage_or_extent_state.get("metastatic")),
            "locallyAdvanced": bool(stage_or_extent_state.get("locallyAdvanced")),
            "matches": sorted(list(stage_or_extent_state.get("matches") or []))[:8],
            "supportRefs": sorted(list(stage_or_extent_state.get("supportRefs") or []))[:8],
        }
        oncology_treatment_payload = {
            "terms": sorted(list(oncology_treatment_terms))[:24],
            "supportRefs": sorted(list(oncology_treatment_support_refs))[:12],
        }

        facts = {
            "age": {"value": meta.get("age"), "source": "synthea-facts"},
            "sex": {"value": meta.get("gender"), "source": "synthea-facts"},
            "birthDate": {"value": meta.get("birthDate"), "source": "synthea-facts"},
            "latestBmi": {"value": latest_bmi.get("value"), "date": latest_bmi.get("date"), "source": "synthea-facts"},
            "lastMammogramDate": {
                "value": serialize_doc(latest_mammo_date) if latest_mammo_date is not None else None,
                "source": "synthea-facts",
            },
            "activeMedications": {"value": sorted(list(active_meds))[:80], "source": "synthea-facts"},
            "medicationHistory": {"value": sorted(list(med_history))[:120], "source": "synthea-facts"},
            "conditionHistory": {"value": sorted(list(condition_history))[:120], "source": "synthea-facts"},
            "procedureHistory": {"value": sorted(list(procedure_history))[:120], "source": "synthea-facts"},
            "keyLabs": {"value": labs, "source": "synthea-facts"},
            "riskSignals": {"value": sorted(list(risk_signals))[:80], "source": "synthea-facts"},
            "oncologySignals": {"value": oncology_signals, "source": "synthea-facts"},
            "receptorStatus": {"value": receptor_status_payload, "source": "synthea-facts"},
            "performanceStatus": {"value": performance_status_payload, "source": "synthea-facts"},
            "stageOrExtent": {"value": stage_or_extent_payload, "source": "synthea-facts"},
            "oncologyTreatmentHistory": {"value": oncology_treatment_payload, "source": "synthea-facts"},
        }

        completeness_domains = {
            "demographics": bool(meta.get("age") is not None and meta.get("gender")),
            "conditions": bool(condition_history),
            "medications": bool(med_history),
            "procedures": bool(procedure_history),
            "labs": bool(labs),
            "breast_oncology": bool((oncology_signals.get("breast_oncology") or {}).get("present")),
            "breast_imaging": bool((oncology_signals.get("breast_imaging") or {}).get("present")),
            "receptor_status": bool(
                receptor_status_payload.get("hr")
                or receptor_status_payload.get("er")
                or receptor_status_payload.get("pr")
                or receptor_status_payload.get("her2")
                or receptor_status_payload.get("matches")
            ),
            "stage_or_extent": bool(
                stage_or_extent_payload.get("stage")
                or stage_or_extent_payload.get("tnm")
                or stage_or_extent_payload.get("metastatic")
                or stage_or_extent_payload.get("locallyAdvanced")
            ),
            "performance_status": performance_status_payload.get("value") is not None,
            "oncology_treatment": bool(oncology_treatment_payload.get("terms")),
        }
        completeness_pct = round(
            (sum(1 for value in completeness_domains.values() if value) / len(completeness_domains)) * 100.0,
            1,
        ) if completeness_domains else 0.0

        return {
            "patientRef": patient_ref,
            "patientName": meta.get("name") or "",
            "factVersion": PHARMA_PATIENT_FACT_VERSION,
            "generatedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "sourceDataset": {
                "db": SYNTHEA_FHIR_DB_NAME,
                "collection": SYNTHEA_RESOURCES_COLLECTION_NAME,
                "sourceKinds": ["observed", "derived"],
            },
            "facts": facts,
            "sourceSummary": {
                "resourceTypeCounts": dict(resource_type_counts),
                "resourceCount": int(sum(resource_type_counts.values())),
                "lastEventDate": serialize_doc(last_event_date) if last_event_date is not None else None,
            },
            "completeness": {
                "domains": completeness_domains,
                "coveragePct": completeness_pct,
            },
        }


    def rebuild_pharma_patient_facts(patient_refs=None):
        target_patient_refs = {
            _normalize_patient_ref(item)
            for item in (patient_refs or [])
            if _normalize_patient_ref(item)
        } or None
        patient_meta_map = {}
        patient_cursor = db_synthea_resources_collection.find(
            {"resource.resourceType": "Patient"},
            {"resource.id": 1, "resource.gender": 1, "resource.birthDate": 1, "resource.name": 1}
        )
        for doc in patient_cursor:
            resource = doc.get("resource") or {}
            patient_id = str(resource.get("id") or "").strip()
            if not patient_id:
                continue
            patient_ref = _normalize_patient_ref(patient_id)
            if target_patient_refs is not None and patient_ref not in target_patient_refs:
                continue
            patient_name = ""
            names = resource.get("name") or []
            if names and isinstance(names[0], dict):
                given = " ".join(names[0].get("given") or [])
                family = names[0].get("family") or ""
                patient_name = f"{given} {family}".strip()
            patient_meta_map[patient_ref] = {
                "patientRef": patient_ref,
                "name": patient_name,
                "gender": normalize_pharma_text(resource.get("gender")),
                "birthDate": resource.get("birthDate"),
                "age": parse_birthdate_to_age(resource.get("birthDate")),
            }

        grouped_resources = defaultdict(list)
        cursor = db_synthea_resources_collection.find(
            {"resource.resourceType": {"$ne": "Patient"}},
            {
                "resource.id": 1,
                "resource.resourceType": 1,
                "resource.subject.reference": 1,
                "resource.patient.reference": 1,
                "resource.beneficiary.reference": 1,
                "resource.status": 1,
                "resource.code": 1,
                "resource.type": 1,
                "resource.medicationCodeableConcept": 1,
                "resource.valueCodeableConcept": 1,
                "resource.valueQuantity": 1,
                "resource.valueString": 1,
                "resource.valueInteger": 1,
                "resource.valueBoolean": 1,
                "resource.text.div": 1,
                "resource.category": 1,
                "resource.interpretation": 1,
                "resource.bodySite": 1,
                "resource.method": 1,
                "resource.clinicalStatus": 1,
                "resource.verificationStatus": 1,
                "resource.specimen.type": 1,
                "resource.stage": 1,
                "resource.component": 1,
                "resource.effectiveDateTime": 1,
                "resource.recordedDate": 1,
                "resource.issued": 1,
                "resource.authoredOn": 1,
                "resource.onsetDateTime": 1,
                "resource.date": 1,
                "resource.performedPeriod": 1,
                "resource.period": 1,
                "resource.note": 1,
                "resource.conclusion": 1,
                "resource.description": 1,
            }
        )
        for doc in cursor:
            resource = doc.get("resource") or {}
            patient_ref = _resolve_resource_patient_ref(resource)
            if not patient_ref:
                continue
            if target_patient_refs is not None and patient_ref not in target_patient_refs:
                continue
            grouped_resources[patient_ref].append(resource)

        screening_summary_map = _load_latest_linked_screening_summary_by_patient_ref()
        linkage_payload_map = _load_latest_linkage_payload_by_patient_ref()
        overlay_map = {}
        if db_pharma_patient_curation_overlays_collection is not None:
            try:
                overlay_cursor = db_pharma_patient_curation_overlays_collection.find(
                    {},
                    {
                        "_id": 0,
                        "patientRef": 1,
                        "overlayVersion": 1,
                        "updatedAt": 1,
                        "curationSource": 1,
                        "reviewer": 1,
                        "note": 1,
                        "factsOverlay": 1,
                        "evidence": 1,
                    },
                )
                for overlay_row in overlay_cursor:
                    patient_ref = str(overlay_row.get("patientRef") or "").strip()
                    if target_patient_refs is not None and patient_ref not in target_patient_refs:
                        continue
                    if patient_ref:
                        overlay_map[patient_ref] = overlay_row
            except Exception:
                overlay_map = {}
        write_ops = []
        generated = 0
        screening_enriched = 0
        conflict_count = 0
        active_patient_refs = sorted(
            set((target_patient_refs or set()))
            | set(patient_meta_map.keys())
            | set(grouped_resources.keys())
            | set(overlay_map.keys())
        )
        for patient_ref in active_patient_refs:
            fact_doc = build_pharma_patient_facts_document(
                patient_ref,
                patient_meta_map.get(patient_ref) or {"patientRef": patient_ref},
                grouped_resources.get(patient_ref) or [],
            )
            if overlay_map.get(patient_ref):
                fact_doc = apply_patient_curation_overlay_to_fact_doc(fact_doc, overlay_map.get(patient_ref))
            evidence_profile = build_patient_evidence_profile_v2(
                patient_ref=patient_ref,
                facts_doc=fact_doc,
                screening_summary=screening_summary_map.get(patient_ref),
                linkage_payload=linkage_payload_map.get(patient_ref),
                normalize_text_fn=normalize_pharma_text,
            )
            fact_doc["patientEvidenceProfile"] = evidence_profile
            if screening_summary_map.get(patient_ref):
                screening_enriched += 1
            if ((evidence_profile.get("identity") or {}).get("linkageStatus")) == "conflict":
                conflict_count += 1
            write_ops.append(
                UpdateOne(
                    {"patientRef": patient_ref},
                    {"$set": fact_doc},
                    upsert=True,
                )
            )
            generated += 1

        if write_ops:
            db_pharma_patient_facts_collection.bulk_write(write_ops, ordered=False)
        if target_patient_refs is None:
            if active_patient_refs:
                db_pharma_patient_facts_collection.delete_many({"patientRef": {"$nin": active_patient_refs}})
            else:
                db_pharma_patient_facts_collection.delete_many({})

        return {
            "generatedPatientFacts": generated,
            "collectionCount": db_pharma_patient_facts_collection.estimated_document_count(),
            "factVersion": PHARMA_PATIENT_FACT_VERSION,
            "patientEvidenceProfileVersion": PATIENT_EVIDENCE_PROFILE_VERSION,
            "screeningEnrichedPatients": screening_enriched,
            "conflictPatients": conflict_count,
            "incremental": bool(target_patient_refs),
            "targetedPatientCount": len(active_patient_refs),
        }


    def rebuild_pharma_trial_prefilter_contracts(batch_size=500):
        write_ops = []
        processed = 0
        updated = 0
        cursor = db_pharma_trials_collection.find(
            {},
            {
                "_id": 1,
                "eligibility": 1,
                "pharma_trial_rules": 1,
                "atomicRules": 1,
                "trialPrefilterContract": 1,
            }
        )
        for trial_doc in cursor:
            processed += 1
            contract = resolve_trial_prefilter_contract(trial_doc)
            if not isinstance(contract, dict) or not contract.get("contractVersion"):
                continue
            existing_contract = trial_doc.get("trialPrefilterContract") or {}
            if existing_contract == contract:
                continue
            write_ops.append(
                UpdateOne(
                    {"_id": trial_doc.get("_id")},
                    {"$set": {"trialPrefilterContract": contract}},
                )
            )
            updated += 1
            if len(write_ops) >= max(1, int(batch_size or 500)):
                db_pharma_trials_collection.bulk_write(write_ops, ordered=False)
                write_ops = []
        if write_ops:
            db_pharma_trials_collection.bulk_write(write_ops, ordered=False)
        current_count = db_pharma_trials_collection.count_documents(
            {"trialPrefilterContract.contractVersion": PHARMA_TRIAL_PREFILTER_CONTRACT_VERSION}
        )
        return {
            "processedTrials": processed,
            "updatedTrials": updated,
            "materializedContracts": current_count,
            "contractVersion": PHARMA_TRIAL_PREFILTER_CONTRACT_VERSION,
        }

    def rebuild_pharma_trial_canonical_definitions(batch_size=500):
        write_ops = []
        processed = 0
        updated = 0
        cursor = db_pharma_trials_collection.find(
            {},
            {
                "_id": 1,
                "trialId": 1,
                "nctId": 1,
                "title": 1,
                "source": 1,
                "sourcePayloadHash": 1,
                "trialVersion": 1,
                "parserWarnings": 1,
                "pharma_trial_rules": 1,
                "atomicRules": 1,
                "trialDefinitionCanonical": 1,
            }
        )
        for trial_doc in cursor:
            processed += 1
            canonical_definition = resolve_trial_definition_canonical(trial_doc)
            if not isinstance(canonical_definition, dict) or not canonical_definition.get("contractVersion"):
                continue
            existing_definition = trial_doc.get("trialDefinitionCanonical") or {}
            if existing_definition == canonical_definition:
                continue
            write_ops.append(
                UpdateOne(
                    {"_id": trial_doc.get("_id")},
                    {"$set": {"trialDefinitionCanonical": canonical_definition}},
                )
            )
            updated += 1
            if len(write_ops) >= max(1, int(batch_size or 500)):
                db_pharma_trials_collection.bulk_write(write_ops, ordered=False)
                write_ops = []
        if write_ops:
            db_pharma_trials_collection.bulk_write(write_ops, ordered=False)
        current_count = db_pharma_trials_collection.count_documents(
            {"trialDefinitionCanonical.contractVersion": PHARMA_TRIAL_DEFINITION_CANONICAL_VERSION}
        )
        return {
            "processedTrials": processed,
            "updatedTrials": updated,
            "materializedDefinitions": current_count,
            "contractVersion": PHARMA_TRIAL_DEFINITION_CANONICAL_VERSION,
        }


    def _derive_breast_oncology_cohort_rows(patient_refs=None, sample_limit=None):
        target_patient_refs = {
            _normalize_patient_ref(item)
            for item in (patient_refs or [])
            if _normalize_patient_ref(item)
        } or None
        safe_sample_limit = max(1, int(sample_limit or 0)) if sample_limit else None
        if target_patient_refs is None and safe_sample_limit is not None:
            sampled_patient_refs = []
            facts_seed_cursor = db_pharma_patient_facts_collection.find(
                {},
                {
                    "patientRef": 1,
                },
            ).limit(safe_sample_limit)
            for facts_seed_doc in facts_seed_cursor:
                patient_ref = str(facts_seed_doc.get("patientRef") or "").strip()
                if patient_ref:
                    sampled_patient_refs.append(patient_ref)
            target_patient_refs = set(sampled_patient_refs) or set()
        profile_map = {}
        profile_cursor = db_pharma_profiles_collection.find(
            {"patientRef": {"$in": sorted(target_patient_refs)}} if target_patient_refs is not None else {},
            {
                "patientRef": 1,
                "conditionTerms": 1,
                "procedureTerms": 1,
                "diagnosticTerms": 1,
                "medicationTerms": 1,
                "observationTerms": 1,
                "documentTerms": 1,
                "featureTerms": 1,
                "resourceEventCount": 1,
            }
        )
        for document in profile_cursor:
            patient_ref = str(document.get("patientRef") or "").strip()
            if patient_ref:
                profile_map[patient_ref] = document

        rows = []
        facts_cursor = db_pharma_patient_facts_collection.find(
            {"patientRef": {"$in": sorted(target_patient_refs)}} if target_patient_refs is not None else {},
            {
                "patientRef": 1,
                "factVersion": 1,
                "facts": 1,
                "patientEvidenceProfile": 1,
            }
        )
        for facts_doc in facts_cursor:
            patient_ref = str(facts_doc.get("patientRef") or "").strip()
            if not patient_ref:
                continue
            assessment_input = build_oncology_cohort_assessment_input(
                patient_ref=patient_ref,
                facts_doc=facts_doc,
                profile_doc=profile_map.get(patient_ref) or {},
            )
            rows.append(
                build_breast_oncology_cohort_document(
                    patient_ref=patient_ref,
                    assessment_input=assessment_input,
                    source_dataset={
                        "db": SYNTHEA_FHIR_DB_NAME,
                        "collection": SYNTHEA_RESOURCES_COLLECTION_NAME,
                    },
                )
            )
        return rows


    def rebuild_breast_oncology_cohort(patient_refs=None):
        target_patient_refs = {
            _normalize_patient_ref(item)
            for item in (patient_refs or [])
            if _normalize_patient_ref(item)
        } or None
        derived_rows = _derive_breast_oncology_cohort_rows(patient_refs=target_patient_refs)
        quality_summary = build_breast_oncology_cohort_quality_summary(derived_rows)
        quality_summary["db"] = collection.database.name if hasattr(collection, "database") else None
        quality_summary["collection"] = db_pharma_oncology_cohort_collection.name if db_pharma_oncology_cohort_collection is not None and hasattr(db_pharma_oncology_cohort_collection, "name") else None
        quality_summary["summaryMode"] = "materialized_collection" if db_pharma_oncology_cohort_collection is not None else "derived_runtime"

        if db_pharma_oncology_cohort_collection is None:
            return {
                "generatedCohortDocs": len(derived_rows),
                "collectionCount": 0,
                "cohortVersion": BREAST_ONCOLOGY_COHORT_VERSION,
                "qualitySummary": quality_summary,
                "materialized": False,
            }

        write_ops = []
        active_patient_refs = []
        for cohort_doc in derived_rows:
            patient_ref = str(cohort_doc.get("patientRef") or "").strip()
            if not patient_ref:
                continue
            active_patient_refs.append(patient_ref)
            write_ops.append(
                UpdateOne(
                    {"patientRef": patient_ref},
                    {"$set": cohort_doc},
                    upsert=True,
                )
            )

        if write_ops:
            db_pharma_oncology_cohort_collection.bulk_write(write_ops, ordered=False)
        if target_patient_refs is None:
            if active_patient_refs:
                db_pharma_oncology_cohort_collection.delete_many({"patientRef": {"$nin": active_patient_refs}})
            else:
                db_pharma_oncology_cohort_collection.delete_many({})

        return {
            "generatedCohortDocs": len(write_ops),
            "collectionCount": db_pharma_oncology_cohort_collection.estimated_document_count(),
            "cohortVersion": BREAST_ONCOLOGY_COHORT_VERSION,
            "qualitySummary": quality_summary,
            "materialized": True,
            "incremental": bool(target_patient_refs),
            "targetedPatientCount": len(active_patient_refs),
        }


    def audit_breast_oncology_cohort_quality(sample_limit=None):
        safe_sample_limit = max(1, int(sample_limit or 0)) if sample_limit else None
        if db_pharma_oncology_cohort_collection is not None:
            cursor = db_pharma_oncology_cohort_collection.find(
                {},
                {
                    "patientRef": 1,
                    "candidateCategory": 1,
                    "matchingReadinessTier": 1,
                    "coveragePct": 1,
                    "coverageDomains": 1,
                    "criticalMissingDomains": 1,
                    "enrichmentPriorityScore": 1,
                }
            )
            if safe_sample_limit is not None:
                cursor = cursor.limit(safe_sample_limit)
            rows = list(cursor)
            summary_mode = "materialized_collection_sample" if safe_sample_limit is not None else "materialized_collection"
        else:
            rows = _derive_breast_oncology_cohort_rows(sample_limit=safe_sample_limit)
            summary_mode = "derived_runtime_sample" if safe_sample_limit is not None else "derived_runtime"
        summary = build_breast_oncology_cohort_quality_summary(rows)
        summary["db"] = collection.database.name if hasattr(collection, "database") else None
        summary["collection"] = db_pharma_oncology_cohort_collection.name if db_pharma_oncology_cohort_collection is not None and hasattr(db_pharma_oncology_cohort_collection, "name") else None
        summary["summaryMode"] = summary_mode
        if safe_sample_limit is not None:
            summary["sampled"] = True
            summary["sampleLimit"] = safe_sample_limit
            summary["sampledPatients"] = len(rows)
        return summary


    def _screening_summary_sort_key(summary):
        payload = summary if isinstance(summary, dict) else {}
        report_epoch = payload.get("reportDateEpoch")
        if report_epoch is not None:
            return float(report_epoch)
        report_date = payload.get("reportDate")
        parsed_dt = parse_iso_datetime(report_date) if report_date else None
        if parsed_dt is not None:
            return parsed_dt.timestamp()
        return 0.0


    def _load_latest_linked_screening_summary_by_patient_ref():
        projection = {
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
        summary_map = {}
        report_cursor = db_clinical_reports_collection.find(
            {"syntheaPatientRef": {"$exists": True, "$ne": ""}},
            projection,
        )
        for report_doc in report_cursor:
            if not is_authoritative_link_strategy(report_doc.get("syntheaLinkStrategy")):
                continue
            patient_ref = _normalize_patient_ref(report_doc.get("syntheaPatientRef"))
            if not patient_ref:
                continue
            screening_summary = build_screening_report_summary(
                report_doc,
                parse_age_token_fn=parse_age_token,
                serialize_doc_fn=serialize_doc,
                normalize_text_fn=normalize_pharma_text,
                parse_iso_datetime_fn=parse_iso_datetime,
            )
            existing_summary = summary_map.get(patient_ref)
            if existing_summary is None or _screening_summary_sort_key(screening_summary) >= _screening_summary_sort_key(existing_summary):
                summary_map[patient_ref] = screening_summary
        return summary_map


    def _load_latest_linkage_payload_by_patient_ref():
        payload_map = {}
        if db_leafy_synthea_links_collection is None:
            return payload_map

        projection = {
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
        }
        cursor = db_leafy_synthea_links_collection.find(
            {"syntheaPatientRef": {"$exists": True, "$ne": ""}},
            projection,
        )
        for row in cursor:
            if not classify_link_record(row).get("authoritative"):
                continue
            patient_ref = _normalize_patient_ref(row.get("syntheaPatientRef"))
            if not patient_ref:
                continue
            current_row = payload_map.get(patient_ref)
            current_ts = parse_iso_datetime((current_row or {}).get("resolvedAt") or (current_row or {}).get("linkedAt")) if current_row else None
            row_ts = parse_iso_datetime(row.get("resolvedAt") or row.get("linkedAt"))
            if current_row is None or (row_ts is not None and (current_ts is None or row_ts >= current_ts)):
                payload_map[patient_ref] = serialize_doc(row)
        return payload_map


    evidence_tools = create_pharma_evidence_tools(
        normalize_pharma_text_fn=normalize_pharma_text,
        parse_int_value_fn=parse_int_value,
        extract_resource_event_datetime_fn=extract_resource_event_datetime,
        extract_codes_and_terms_fn=extract_codes_and_terms,
        serialize_doc_fn=serialize_doc,
        build_pharma_trial_rules_fn=build_pharma_trial_rules,
        parse_birads_values_from_text_fn=parse_birads_values_from_text,
        evaluate_numeric_window_fn=evaluate_numeric_window,
        tokenize_rule_text_fn=tokenize_rule_text,
        parse_iso_datetime_fn=parse_iso_datetime,
        days_since_fn=days_since,
        normalize_patient_ref_fn=_normalize_patient_ref,
        db_pharma_patient_facts_collection=db_pharma_patient_facts_collection,
        db_synthea_resources_collection=db_synthea_resources_collection,
        pharma_rule_risk_paths=PHARMA_RULE_RISK_PATHS,
        pharma_rule_medication_tokens=PHARMA_RULE_MEDICATION_TOKENS,
        pharma_rule_condition_tokens=PHARMA_RULE_CONDITION_TOKENS,
    )
    build_leafy_note_snippets = evidence_tools["build_leafy_note_snippets"]
    build_patient_evidence_pack = evidence_tools["build_patient_evidence_pack"]
    build_risk_evidence = evidence_tools["build_risk_evidence"]
    evaluate_rule_with_evidence = evidence_tools["evaluate_rule_with_evidence"]
    evaluate_trial_structured_match = evidence_tools["evaluate_trial_structured_match"]


    def build_trial_match_text(trial_doc):
        eligibility = trial_doc.get("eligibility") or {}
        rule_rows = trial_doc.get("pharma_trial_rules") or []
        rule_text = " ".join(
            [
                f"{item.get('type')}: {item.get('text')}"
                for item in rule_rows[:24]
                if isinstance(item, dict) and item.get("text")
            ]
        )
        return " | ".join(
            [
                f"title: {trial_doc.get('title', '')}",
                f"summary: {trial_doc.get('summary', '')}",
                f"conditions: {', '.join(trial_doc.get('conditions') or [])}",
                f"interventions: {', '.join(trial_doc.get('interventions') or [])}",
                f"keywords: {', '.join(eligibility.get('includeKeywordsAny') or [])}",
                f"birads: {', '.join([str(v) for v in (eligibility.get('biradsAny') or [])])}",
                f"rules: {rule_text}",
                f"phase: {trial_doc.get('phase', '')}",
                f"sponsor: {trial_doc.get('sponsor', '')}",
            ]
        ).strip()


    def build_profile_query_for_trial(trial_doc):
        trial_prefilter_contract = resolve_trial_prefilter_contract(trial_doc)
        return build_profile_query_from_trial_prefilter_contract(trial_prefilter_contract)


    def estimate_trial_coverage_with_synthea(trial_doc):
        profile_query = build_profile_query_for_trial(trial_doc)
        trial_prefilter_contract = resolve_trial_prefilter_contract(trial_doc)
        try:
            total = db_pharma_profiles_collection.count_documents(profile_query)
            sample_cursor = db_pharma_profiles_collection.find(
                profile_query,
                {"patientRef": 1, "age": 1, "gender": 1, "featureTerms": 1, "resourceTypeCounts": 1}
            ).sort("resourceEventCount", -1).limit(3)
            sample = [serialize_doc(doc) for doc in sample_cursor]
        except Exception:
            total = 0
            sample = []
        return {
            "query": profile_query,
            "mode": "trial_prefilter_contract",
            "trialPrefilterContract": trial_prefilter_contract,
            "estimatedPrefilterCandidates": total,
            "candidateEstimateOnly": True,
            "estimatedEligiblePatients": total,
            "samplePatients": sample,
        }


    def infer_synthea_analog_patients(patient_context, limit=5):
        pipeline = []
        query = {}

        patient_age = patient_context.get("age")
        if isinstance(patient_age, int):
            query["age"] = {
                "$gte": max(patient_age - 6, 0),
                "$lte": patient_age + 6,
            }

        patient_sex = normalize_pharma_text(patient_context.get("sex") or "")
        if patient_sex:
            query["gender"] = patient_sex

        if query:
            pipeline.append({"$match": query})

        keywords = patient_context.get("keywords") or []
        if keywords:
            pipeline.extend([
                {"$addFields": {"sharedKeywords": {"$setIntersection": ["$featureTerms", keywords]}}},
                {"$addFields": {"sharedKeywordCount": {"$size": "$sharedKeywords"}}},
                {"$sort": {"sharedKeywordCount": -1, "resourceEventCount": -1}},
            ])
        else:
            pipeline.append({"$sort": {"resourceEventCount": -1}})

        pipeline.append({"$limit": max(1, min(limit, 12))})
        pipeline.append({
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
        })

        try:
            rows = list(db_pharma_profiles_collection.aggregate(pipeline, allowDiskUse=True))
        except Exception:
            rows = []

        return rows, pipeline


    def _load_synthea_patient_refs(force_reload=False):
        if not force_reload and SYNTHEA_PATIENT_REF_CACHE.get("refs"):
            return SYNTHEA_PATIENT_REF_CACHE.get("refs") or []

        refs = []
        try:
            profile_refs = db_pharma_profiles_collection.distinct("patientRef")
            refs = sorted(
                list(
                    {
                        _normalize_patient_ref(item)
                        for item in profile_refs
                        if _normalize_patient_ref(item)
                    }
                )
            )
        except Exception:
            refs = []

        if not refs:
            try:
                patient_cursor = db_synthea_resources_collection.find(
                    {"resource.resourceType": "Patient", "resource.id": {"$exists": True}},
                    {"resource.id": 1}
                )
                refs = sorted(
                    list(
                        {
                            _normalize_patient_ref((row.get("resource") or {}).get("id"))
                            for row in patient_cursor
                            if _normalize_patient_ref((row.get("resource") or {}).get("id"))
                        }
                    )
                )
            except Exception:
                refs = []

        SYNTHEA_PATIENT_REF_CACHE["refs"] = refs
        SYNTHEA_PATIENT_REF_CACHE["loadedAt"] = datetime.utcnow().isoformat() + "Z"
        return refs


    def _pick_deterministic_synthea_ref(seed_value):
        refs = _load_synthea_patient_refs()
        if not refs:
            return ""
        seed_text = str(seed_value or "").strip() or "leafy-default-seed"
        digest = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()
        index = int(digest[:8], 16) % len(refs)
        return refs[index]


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


    def _upsert_linkage_record(identity_snapshot, update_fields):
        snapshot = identity_snapshot if isinstance(identity_snapshot, dict) else {}
        seed_key = (
            snapshot.get("leafyPatientId")
            or snapshot.get("leafyAppointmentId")
            or snapshot.get("leafyReportId")
            or snapshot.get("leafyPatientName")
            or "unknown"
        )
        update_doc = dict(snapshot)
        update_doc.update(update_fields or {})
        update_doc["linkKey"] = f"leafy::{seed_key}"
        if db_leafy_synthea_links_collection is None:
            return update_doc
        db_leafy_synthea_links_collection.update_one(
            {"linkKey": f"leafy::{seed_key}"},
            {"$set": serialize_doc(update_doc)},
            upsert=True,
        )
        return update_doc


    def _parse_screening_birthdate_token(value):
        raw_value = str(value or "").strip()
        if not raw_value:
            return None
        try:
            if re.fullmatch(r"\d{8}", raw_value):
                return datetime.strptime(raw_value, "%Y%m%d").date()
            return date_parser.parse(raw_value).date()
        except Exception:
            return None


    def _is_placeholder_screening_name(value):
        normalized = normalize_pharma_text(value)
        return normalized in {
            "",
            "unknown",
            "hc_provider",
            "hc-provider",
            "de_identified_patient",
            "de-identified patient",
            "masked_patient_id",
            "masked appointment id",
            "masked patient id",
            "masked report id",
        }


    def _load_screening_context_from_link_row(link_row):
        row = link_row if isinstance(link_row, dict) else {}
        report_doc = None
        report_id = str(row.get("leafyReportId") or "").strip()
        if report_id and db_clinical_reports_collection is not None:
            report_query = {"_id": report_id}
            try:
                report_query = {"_id": ObjectId(report_id)}
            except Exception:
                report_query = {"_id": report_id}
            report_doc = db_clinical_reports_collection.find_one(
                report_query,
                {
                    "_id": 1,
                    "appointmentID": 1,
                    "patientID": 1,
                    "patient-id": 1,
                    "user": 1,
                    "date": 1,
                    "clinicalNotes": 1,
                    "conclusions": 1,
                    "series.imageMetadata.PatientName": 1,
                    "series.imageMetadata.PatientSex": 1,
                    "series.imageMetadata.PatientAge": 1,
                    "series.imageMetadata.PatientBirthDate": 1,
                    "series.imageMetadata.biRads": 1,
                },
            )

        patient_name = str(row.get("leafyPatientName") or "").strip()
        age_value = row.get("ageSnapshot")
        sex_value = str(row.get("sexSnapshot") or "").strip().lower() or None
        birthdate_value = None
        birads_values = []
        report_date = None

        if report_doc:
            report_date = serialize_doc(report_doc.get("date"))
            for series_row in report_doc.get("series") or []:
                image_meta = (series_row or {}).get("imageMetadata") or {}
                candidate_name = str(image_meta.get("PatientName") or "").strip()
                if candidate_name and _is_placeholder_screening_name(patient_name):
                    patient_name = candidate_name
                if age_value is None:
                    age_value = parse_age_token(image_meta.get("PatientAge"))
                if not sex_value:
                    raw_sex = normalize_pharma_text(image_meta.get("PatientSex") or "")
                    sex_value = raw_sex or sex_value
                if birthdate_value is None:
                    birthdate_value = _parse_screening_birthdate_token(image_meta.get("PatientBirthDate"))
                birads_raw = image_meta.get("biRads")
                birads_value = birads_raw.get("BIRAD") if isinstance(birads_raw, dict) else birads_raw
                try:
                    if birads_value is not None:
                        birads_values.append(int(birads_value))
                except Exception:
                    continue

        return {
            "reportDoc": report_doc,
            "patientName": patient_name,
            "age": parse_age_token(age_value),
            "sex": normalize_pharma_text(sex_value or "") or None,
            "birthDate": birthdate_value,
            "biradsValues": sorted(list(set(birads_values))),
            "reportDate": report_date,
        }


    def _load_synthea_patient_demo_snapshot(patient_ref):
        normalized_ref = _normalize_patient_ref(patient_ref)
        if not normalized_ref:
            return None
        patient_id = normalized_ref.split("/", 1)[-1]
        patient_doc = None
        if db_synthea_resources_collection is not None:
            patient_doc = db_synthea_resources_collection.find_one(
                {"resource.resourceType": "Patient", "resource.id": patient_id},
                {"_id": 0, "resource.id": 1, "resource.gender": 1, "resource.birthDate": 1, "resource.name": 1},
            )
        profile_doc = None
        if db_pharma_profiles_collection is not None:
            profile_doc = db_pharma_profiles_collection.find_one(
                {"patientRef": normalized_ref},
                {
                    "_id": 0,
                    "patientRef": 1,
                    "name": 1,
                    "gender": 1,
                    "birthDate": 1,
                    "age": 1,
                    "patientMatchProfile.matchFilters.primaryCancerCode": 1,
                    "patientMatchProfile.runtimeReadiness": 1,
                    "patientMatchProfile.cohortMembership": 1,
                },
            )
        resource = (patient_doc or {}).get("resource") or {}
        profile = profile_doc or {}
        names = resource.get("name") or []
        patient_name = str(profile.get("name") or "").strip()
        if not patient_name and names and isinstance(names[0], dict):
            given = " ".join(names[0].get("given") or [])
            family = names[0].get("family") or ""
            patient_name = f"{given} {family}".strip()
        birthdate = str(resource.get("birthDate") or profile.get("birthDate") or "").strip() or None
        gender = normalize_pharma_text(resource.get("gender") or profile.get("gender") or "") or None
        age_value = profile.get("age")
        if age_value is None and birthdate:
            age_value = parse_birthdate_to_age(birthdate)
        match_filters = ((profile.get("patientMatchProfile") or {}).get("matchFilters") or {})
        cohort_membership = ((profile.get("patientMatchProfile") or {}).get("cohortMembership") or {})
        runtime_readiness = ((profile.get("patientMatchProfile") or {}).get("runtimeReadiness") or {})
        return {
            "patientRef": normalized_ref,
            "patientName": patient_name,
            "birthDate": birthdate,
            "birthDateValue": _parse_screening_birthdate_token(birthdate),
            "gender": gender,
            "age": parse_age_token(age_value),
            "primaryCancerCode": match_filters.get("primaryCancerCode"),
            "cohortMembership": cohort_membership,
            "runtimeReadiness": runtime_readiness,
        }


    def _collect_demo_link_candidates(link_row):
        row = link_row if isinstance(link_row, dict) else {}
        candidate_rows = []
        seen = set()
        suggested_ref = _normalize_patient_ref(row.get("suggestedSyntheaPatientRef"))
        if suggested_ref:
            candidate_rows.append(
                {
                    "patientRef": suggested_ref,
                    "sharedKeywordCount": 0,
                    "resourceEventCount": 0,
                    "source": "stored_suggestion",
                }
            )
            seen.add(suggested_ref)
        for option in row.get("candidateOptions") or []:
            patient_ref = _normalize_patient_ref((option or {}).get("patientRef"))
            if not patient_ref or patient_ref in seen:
                continue
            seen.add(patient_ref)
            candidate_rows.append(
                {
                    "patientRef": patient_ref,
                    "sharedKeywordCount": int((option or {}).get("sharedKeywordCount") or 0),
                    "resourceEventCount": int((option or {}).get("resourceEventCount") or 0),
                    "source": str((option or {}).get("source") or "analog_candidate"),
                }
            )
        return candidate_rows


    def _score_demo_screening_candidate(*, screening_context, patient_demo, candidate_row, repeated_suggestion_count):
        screening = screening_context if isinstance(screening_context, dict) else {}
        candidate = candidate_row if isinstance(candidate_row, dict) else {}
        patient = patient_demo if isinstance(patient_demo, dict) else {}
        screening_sex = normalize_pharma_text(screening.get("sex") or "")
        patient_gender = normalize_pharma_text(patient.get("gender") or "")
        if not patient.get("patientRef"):
            return {"eligible": False, "score": 0, "reasons": ["missing_candidate_patient"], "ageDelta": None, "birthDateDeltaDays": None}
        if patient_gender not in {"female", "male"}:
            return {"eligible": False, "score": 0, "reasons": ["missing_candidate_gender"], "ageDelta": None, "birthDateDeltaDays": None}
        if screening_sex and screening_sex != patient_gender:
            return {"eligible": False, "score": 0, "reasons": ["sex_mismatch"], "ageDelta": None, "birthDateDeltaDays": None}

        patient_age = parse_age_token(patient.get("age"))
        screening_age = parse_age_token(screening.get("age"))
        age_delta = abs(int(patient_age) - int(screening_age)) if patient_age is not None and screening_age is not None else None
        if patient_age is not None and patient_age < 18:
            return {"eligible": False, "score": 0, "reasons": ["candidate_is_not_adult"], "ageDelta": age_delta, "birthDateDeltaDays": None}
        if age_delta is not None and age_delta > 2:
            return {"eligible": False, "score": 0, "reasons": ["age_delta_too_large"], "ageDelta": age_delta, "birthDateDeltaDays": None}

        patient_birth_date = patient.get("birthDateValue")
        screening_birth_date = screening.get("birthDate")
        birthdate_delta_days = None
        if patient_birth_date and screening_birth_date:
            birthdate_delta_days = abs((patient_birth_date - screening_birth_date).days)
            if birthdate_delta_days > 400:
                return {
                    "eligible": False,
                    "score": 0,
                    "reasons": ["birthdate_delta_too_large"],
                    "ageDelta": age_delta,
                    "birthDateDeltaDays": birthdate_delta_days,
                }

        score = 25
        reasons = ["sex_match"]
        if age_delta is not None:
            if age_delta == 0:
                score += 40
                reasons.append("exact_age_match")
            elif age_delta == 1:
                score += 30
                reasons.append("age_within_one_year")
            elif age_delta == 2:
                score += 18
                reasons.append("age_within_two_years")

        if birthdate_delta_days is not None:
            if birthdate_delta_days <= 31:
                score += 60
                reasons.append("birthdate_within_one_month")
            elif birthdate_delta_days <= 183:
                score += 45
                reasons.append("birthdate_within_six_months")
            elif birthdate_delta_days <= 366:
                score += 30
                reasons.append("birthdate_within_one_year")

        if int(repeated_suggestion_count or 0) >= 3:
            score += 10
            reasons.append("repeated_demo_candidate")
        if int(candidate.get("sharedKeywordCount") or 0) > 0:
            score += min(10, int(candidate.get("sharedKeywordCount") or 0) * 2)
            reasons.append("shared_keywords")
        if str(patient.get("primaryCancerCode") or "").strip().lower() == "breast_carcinoma":
            score += 8
            reasons.append("breast_oncology_profile")
        return {
            "eligible": True,
            "score": int(score),
            "reasons": reasons,
            "ageDelta": age_delta,
            "birthDateDeltaDays": birthdate_delta_days,
        }


    def _select_demo_screening_link_candidate(link_row, *, repeated_suggestion_counts=None):
        row = link_row if isinstance(link_row, dict) else {}
        screening_context = _load_screening_context_from_link_row(row)
        candidate_rows = _collect_demo_link_candidates(row)
        if not candidate_rows:
            return None
        suggestion_counts = repeated_suggestion_counts if isinstance(repeated_suggestion_counts, dict) else {}
        ranked = []
        for candidate_row in candidate_rows:
            patient_demo = _load_synthea_patient_demo_snapshot(candidate_row.get("patientRef"))
            score_payload = _score_demo_screening_candidate(
                screening_context=screening_context,
                patient_demo=patient_demo,
                candidate_row=candidate_row,
                repeated_suggestion_count=suggestion_counts.get(candidate_row.get("patientRef"), 0),
            )
            if not score_payload.get("eligible"):
                continue
            ranked.append(
                {
                    "candidate": candidate_row,
                    "patient": patient_demo,
                    "screening": screening_context,
                    "score": int(score_payload.get("score") or 0),
                    "reasons": list(score_payload.get("reasons") or []),
                    "ageDelta": score_payload.get("ageDelta"),
                    "birthDateDeltaDays": score_payload.get("birthDateDeltaDays"),
                }
            )
        ranked.sort(
            key=lambda item: (
                -1 * int(item.get("score") or 0),
                int(item.get("ageDelta") if item.get("ageDelta") is not None else 99),
                int(item.get("birthDateDeltaDays") if item.get("birthDateDeltaDays") is not None else 999999),
                -1 * int(((item.get("candidate") or {}).get("resourceEventCount") or 0)),
                str(((item.get("candidate") or {}).get("patientRef") or "")),
            )
        )
        if not ranked:
            return None
        best = ranked[0]
        runner_up = ranked[1] if len(ranked) > 1 else None
        if int(best.get("score") or 0) < 75:
            return None
        if runner_up and (int(best.get("score") or 0) - int(runner_up.get("score") or 0)) < 12:
            return None
        return best


    def promote_demo_screening_links(*, max_links=None, dry_run=False):
        if db_leafy_synthea_links_collection is None or db_clinical_reports_collection is None:
            return {
                "dryRun": bool(dry_run),
                "disabled": True,
                "reason": "Linkage or screening collections are not configured.",
            }

        resolved_limit = max(1, min(int(max_links or 250), 2000))
        rows = list(
            db_leafy_synthea_links_collection.find(
                {},
                {
                    "_id": 1,
                    "linkKey": 1,
                    "leafyPatientId": 1,
                    "leafyAppointmentId": 1,
                    "leafyReportId": 1,
                    "leafyPatientName": 1,
                    "syntheaPatientRef": 1,
                    "suggestedSyntheaPatientRef": 1,
                    "linkStrategy": 1,
                    "linkageStatus": 1,
                    "candidateOptions": 1,
                    "ageSnapshot": 1,
                    "sexSnapshot": 1,
                    "resolvedAt": 1,
                    "linkageVersion": 1,
                },
            ).sort("resolvedAt", -1).limit(resolved_limit)
        )
        repeated_suggestion_counts = Counter(
            _normalize_patient_ref(row.get("suggestedSyntheaPatientRef"))
            for row in rows
            if _normalize_patient_ref(row.get("suggestedSyntheaPatientRef"))
        )

        promoted = []
        skipped = 0
        for row in rows:
            classification = classify_link_record(row)
            if classification.get("authoritative"):
                skipped += 1
                continue
            selected = _select_demo_screening_link_candidate(
                row,
                repeated_suggestion_counts=repeated_suggestion_counts,
            )
            if not selected:
                skipped += 1
                continue

            patient_ref = _normalize_patient_ref(((selected.get("candidate") or {}).get("patientRef")))
            if not patient_ref:
                skipped += 1
                continue

            linked_at = datetime.utcnow().isoformat() + "Z"
            evidence = [
                "Demo-screened Synthea linkage promoted from screening demographics and analog candidate review.",
                *[str(reason) for reason in (selected.get("reasons") or [])[:4]],
            ]
            promoted.append(
                {
                    "linkKey": row.get("linkKey"),
                    "leafyPatientId": row.get("leafyPatientId"),
                    "leafyAppointmentId": row.get("leafyAppointmentId"),
                    "leafyReportId": row.get("leafyReportId"),
                    "syntheaPatientRef": patient_ref,
                    "linkStrategy": DEMO_SCREENING_CURATION_LINK_STRATEGY,
                    "linkageStatus": "linked",
                    "linkageConfidence": round(min(0.99, max(0.75, float(selected.get("score") or 0) / 100.0)), 4),
                    "linkageEvidence": evidence[:6],
                    "candidateOptions": row.get("candidateOptions") or [],
                    "linkedAt": linked_at,
                    "resolvedAt": linked_at,
                    "linkageVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
                    "suggestedSyntheaPatientRef": patient_ref,
                    "resolutionNote": "Auto-promoted for demo screened cohort after demographic compatibility review.",
                    "demoPromotion": {
                        "score": int(selected.get("score") or 0),
                        "ageDelta": selected.get("ageDelta"),
                        "birthDateDeltaDays": selected.get("birthDateDeltaDays"),
                        "candidateSource": (selected.get("candidate") or {}).get("source"),
                        "reasons": selected.get("reasons") or [],
                    },
                }
            )

            if dry_run:
                continue

            db_leafy_synthea_links_collection.update_one(
                {"_id": row.get("_id")},
                {
                    "$set": serialize_doc(
                        {
                            "syntheaPatientRef": patient_ref,
                            "suggestedSyntheaPatientRef": patient_ref,
                            "linkStrategy": DEMO_SCREENING_CURATION_LINK_STRATEGY,
                            "linkageStatus": "linked",
                            "linkageConfidence": round(min(0.99, max(0.75, float(selected.get("score") or 0) / 100.0)), 4),
                            "linkageEvidence": evidence[:6],
                            "candidateOptions": row.get("candidateOptions") or [],
                            "linkedAt": linked_at,
                            "resolvedAt": linked_at,
                            "linkageVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
                            "resolutionNote": "Auto-promoted for demo screened cohort after demographic compatibility review.",
                            "demoPromotion": {
                                "score": int(selected.get("score") or 0),
                                "ageDelta": selected.get("ageDelta"),
                                "birthDateDeltaDays": selected.get("birthDateDeltaDays"),
                                "candidateSource": (selected.get("candidate") or {}).get("source"),
                                "reasons": selected.get("reasons") or [],
                            },
                        }
                    )
                },
            )

        if dry_run:
            return {
                "dryRun": True,
                "processedRows": len(rows),
                "promotedCount": len(promoted),
                "skippedRows": skipped,
                "promotions": promoted,
            }

        normalize_authoritative_linkage_policy(max_records=max(resolved_limit, len(rows)))
        return {
            "dryRun": False,
            "processedRows": len(rows),
            "promotedCount": len(promoted),
            "skippedRows": skipped,
            "promotions": promoted,
        }


    def rebuild_demo_screened_patient_cohort(*, max_links=None, dry_run=False):
        normalization_result = normalize_authoritative_linkage_policy(max_records=max_links or 5000)
        promotion_result = promote_demo_screening_links(max_links=max_links, dry_run=dry_run)
        if dry_run:
            return {
                "dryRun": True,
                "normalization": normalization_result,
                "promotion": promotion_result,
            }
        authoritative_screened_refs = sorted(
            {
                _normalize_patient_ref(item)
                for item in db_clinical_reports_collection.distinct(
                    "syntheaPatientRef",
                    {
                        "syntheaPatientRef": {"$exists": True, "$ne": None},
                        "syntheaLinkStrategy": {"$in": sorted(CURRENT_AUTHORITATIVE_LINK_STRATEGIES)},
                    },
                )
                if _normalize_patient_ref(item)
            }
        ) if db_clinical_reports_collection is not None else []
        facts_result = rebuild_pharma_patient_facts(patient_refs=authoritative_screened_refs)
        profiles_result = rebuild_pharma_patient_profiles(
            embed_profiles=False,
            patient_refs=authoritative_screened_refs,
        )
        cohort_result = rebuild_breast_oncology_cohort(patient_refs=authoritative_screened_refs)
        coverage_summary = build_patient_curation_coverage_summary_document()
        return {
            "dryRun": False,
            "normalization": normalization_result,
            "promotion": promotion_result,
            "facts": facts_result,
            "profiles": profiles_result,
            "cohort": cohort_result,
            "coverageSummary": coverage_summary,
            "targetedPatientRefs": authoritative_screened_refs,
        }


    def resolve_leafy_synthea_link(patient_context, report_doc=None, appointment_doc=None, synthea_patient_ref=None, note=None):
        identity_snapshot = build_link_identity_snapshot(
            patient_context=patient_context,
            report_doc=report_doc,
            appointment_doc=appointment_doc,
        )
        resolved_at = datetime.utcnow().isoformat() + "Z"
        normalized_ref = _normalize_patient_ref(synthea_patient_ref)
        if normalized_ref:
            update_doc = _upsert_linkage_record(
                identity_snapshot,
                {
                    "syntheaPatientRef": normalized_ref,
                    "suggestedSyntheaPatientRef": normalized_ref,
                    "linkStrategy": "manual_review",
                    "linkageStatus": "linked",
                    "linkageConfidence": 1.0,
                    "linkageEvidence": ["Authoritative manual linkage resolution."],
                    "candidateOptions": [],
                    "linkedAt": resolved_at,
                    "resolvedAt": resolved_at,
                    "linkageVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
                    "resolutionNote": str(note or "").strip() or None,
                },
            )
            return {
                "syntheaPatientRef": normalized_ref,
                "suggestedSyntheaPatientRef": normalized_ref,
                "linkStrategy": "manual_review",
                "fromCache": False,
                "linkageStatus": "linked",
                "linkageConfidence": 1.0,
                "linkageEvidence": ["Authoritative manual linkage resolution."],
                "linkedAt": update_doc.get("linkedAt"),
                "linkageVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
                "candidateOptions": [],
                "analogCandidates": [],
                "analogPipeline": [],
            }

        update_doc = _upsert_linkage_record(
            identity_snapshot,
            {
                "syntheaPatientRef": None,
                "suggestedSyntheaPatientRef": None,
                "linkStrategy": "manual_unlink",
                "linkageStatus": "unlinked",
                "linkageConfidence": 0.0,
                "linkageEvidence": ["Manual unlink applied; no authoritative Synthea patient link is set."],
                "candidateOptions": [],
                "linkedAt": None,
                "resolvedAt": resolved_at,
                "linkageVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
                "resolutionNote": str(note or "").strip() or None,
            },
        )
        return {
            "syntheaPatientRef": "",
            "suggestedSyntheaPatientRef": "",
            "linkStrategy": "manual_unlink",
            "fromCache": False,
            "linkageStatus": "unlinked",
            "linkageConfidence": 0.0,
            "linkageEvidence": ["Manual unlink applied; no authoritative Synthea patient link is set."],
            "linkedAt": update_doc.get("linkedAt"),
            "linkageVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
            "candidateOptions": [],
            "analogCandidates": [],
            "analogPipeline": [],
        }


    def normalize_authoritative_linkage_policy(*, max_records=None):
        resolved_limit = max(1, min(int(max_records or 5000), 20000))
        normalized_at = datetime.utcnow().isoformat() + "Z"
        authoritative_by_appointment = {}
        authoritative_by_report = {}
        authoritative_by_patient = {}
        downgraded_links = 0
        normalized_reports = 0
        normalized_appointments = 0

        def remember_authoritative_link(row):
            normalized_ref = _normalize_patient_ref(row.get("syntheaPatientRef"))
            normalized_strategy = normalize_link_strategy_token(row.get("linkStrategy"))
            if not (normalized_ref and is_authoritative_link_strategy(normalized_strategy)):
                return
            payload = {
                "syntheaPatientRef": normalized_ref,
                "linkStrategy": normalized_strategy,
            }
            appointment_id = str(row.get("leafyAppointmentId") or "").strip()
            report_id = str(row.get("leafyReportId") or "").strip()
            patient_id = str(row.get("leafyPatientId") or "").strip()
            if appointment_id and appointment_id not in authoritative_by_appointment:
                authoritative_by_appointment[appointment_id] = payload
            if report_id and report_id not in authoritative_by_report:
                authoritative_by_report[report_id] = payload
            if patient_id and patient_id not in authoritative_by_patient:
                authoritative_by_patient[patient_id] = payload

        if db_leafy_synthea_links_collection is not None:
            link_rows = list(
                db_leafy_synthea_links_collection.find(
                    {},
                    {
                        "_id": 1,
                        "linkKey": 1,
                        "leafyPatientId": 1,
                        "leafyAppointmentId": 1,
                        "leafyReportId": 1,
                        "syntheaPatientRef": 1,
                        "suggestedSyntheaPatientRef": 1,
                        "linkStrategy": 1,
                        "linkageStatus": 1,
                        "linkageConfidence": 1,
                        "linkageEvidence": 1,
                        "candidateOptions": 1,
                        "linkedAt": 1,
                        "resolvedAt": 1,
                        "linkageVersion": 1,
                    },
                ).sort("resolvedAt", -1).limit(resolved_limit)
            )
            for row in link_rows:
                classification = classify_link_record(row)
                if classification.get("authoritative"):
                    remember_authoritative_link(row)
                    db_leafy_synthea_links_collection.update_one(
                        {"_id": row.get("_id")},
                        {
                            "$set": {
                                "linkageStatus": "linked",
                                "linkageVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
                            }
                        },
                    )
                    continue

                current_ref = _normalize_patient_ref(row.get("syntheaPatientRef"))
                suggested_ref = current_ref or _normalize_patient_ref(row.get("suggestedSyntheaPatientRef"))
                candidate_options = list(row.get("candidateOptions") or [])
                if current_ref and not any((item or {}).get("patientRef") == current_ref for item in candidate_options):
                    retired_candidate = _build_candidate_option(
                        {
                            "patientRef": current_ref,
                            "sharedKeywordCount": row.get("sharedKeywordCount") or 0,
                            "resourceEventCount": 0,
                        },
                        source="retired_link",
                    )
                    if retired_candidate:
                        candidate_options.insert(0, retired_candidate)
                db_leafy_synthea_links_collection.update_one(
                    {"_id": row.get("_id")},
                    {
                        "$set": serialize_doc(
                            {
                                "syntheaPatientRef": None,
                                "suggestedSyntheaPatientRef": suggested_ref or None,
                                "linkStrategy": "candidate_only" if suggested_ref else "manual_unlink",
                                "linkageStatus": "candidate_only" if suggested_ref else "unlinked",
                                "linkageConfidence": 0.0,
                                "linkageEvidence": [
                                    "Linkage record normalized to the authoritative-only policy; explicit re-resolution is required before longitudinal matching."
                                ],
                                "candidateOptions": candidate_options[:3],
                                "linkedAt": None,
                                "resolvedAt": normalized_at,
                                "linkageVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
                                "legacySyntheaPatientRef": current_ref or "",
                                "legacyLinkStrategy": row.get("linkStrategy") or "",
                                "legacyLinkageVersion": row.get("linkageVersion") or "",
                            }
                        )
                    },
                )
                downgraded_links += 1

        def desired_payload_for_screening(*, report_doc=None, appointment_doc=None):
            report = report_doc if isinstance(report_doc, dict) else {}
            appointment = appointment_doc if isinstance(appointment_doc, dict) else {}
            embedded_ref = extract_authoritative_embedded_synthea_patient_ref(
                report_doc=report,
                appointment_doc=appointment,
            )
            embedded_strategy = extract_embedded_synthea_link_strategy(
                report_doc=report,
                appointment_doc=appointment,
            )
            if embedded_ref and is_authoritative_link_strategy(embedded_strategy):
                return {
                    "syntheaPatientRef": embedded_ref,
                    "linkStrategy": embedded_strategy,
                }
            report_id = str(report.get("_id") or "").strip()
            appointment_id = str(
                appointment.get("_id")
                or report.get("appointmentID")
                or ""
            ).strip()
            patient_id = str(report.get("patientID") or report.get("patient-id") or "").strip()
            return (
                authoritative_by_report.get(report_id)
                or authoritative_by_appointment.get(appointment_id)
                or authoritative_by_patient.get(patient_id)
                or {"syntheaPatientRef": "", "linkStrategy": ""}
            )

        appointment_rows = list(
            collection.find(
                {},
                {
                    "_id": 1,
                    "syntheaPatientRef": 1,
                    "syntheaLinkStrategy": 1,
                    "metadata.syntheaPatientRef": 1,
                    "metadata.syntheaLinkStrategy": 1,
                    "metadata.extraSearchParams.subject.syntheaPatientRef": 1,
                    "metadata.extraSearchParams.subject.syntheaLinkStrategy": 1,
                },
            ).limit(resolved_limit)
        )
        for row in appointment_rows:
            desired = desired_payload_for_screening(appointment_doc=row)
            desired_ref = _normalize_patient_ref(desired.get("syntheaPatientRef"))
            desired_strategy = normalize_link_strategy_token(desired.get("linkStrategy"))
            current_ref = _normalize_patient_ref(
                row.get("syntheaPatientRef")
                or ((row.get("metadata") or {}).get("syntheaPatientRef"))
                or (((((row.get("metadata") or {}).get("extraSearchParams") or {}).get("subject") or {}).get("syntheaPatientRef")))
            )
            current_strategy = extract_embedded_synthea_link_strategy(appointment_doc=row)
            if current_ref == desired_ref and current_strategy == desired_strategy:
                continue
            collection.update_one(
                {"_id": row.get("_id")},
                {
                    "$set": {
                        "syntheaPatientRef": desired_ref or "",
                        "syntheaLinkStrategy": desired_strategy or "",
                        "metadata.syntheaPatientRef": desired_ref or "",
                        "metadata.syntheaLinkStrategy": desired_strategy or "",
                        "metadata.extraSearchParams.subject.syntheaPatientRef": desired_ref or "",
                        "metadata.extraSearchParams.subject.syntheaLinkStrategy": desired_strategy or "",
                    }
                },
            )
            normalized_appointments += 1

        report_rows = list(
            db_clinical_reports_collection.find(
                {},
                {
                    "_id": 1,
                    "appointmentID": 1,
                    "patientID": 1,
                    "patient-id": 1,
                    "syntheaPatientRef": 1,
                    "syntheaLinkStrategy": 1,
                },
            ).limit(resolved_limit)
        )
        for row in report_rows:
            desired = desired_payload_for_screening(report_doc=row)
            desired_ref = _normalize_patient_ref(desired.get("syntheaPatientRef"))
            desired_strategy = normalize_link_strategy_token(desired.get("linkStrategy"))
            current_ref = _normalize_patient_ref(row.get("syntheaPatientRef"))
            current_strategy = normalize_link_strategy_token(row.get("syntheaLinkStrategy"))
            if current_ref == desired_ref and current_strategy == desired_strategy:
                continue
            db_clinical_reports_collection.update_one(
                {"_id": row.get("_id")},
                {
                    "$set": {
                        "syntheaPatientRef": desired_ref or "",
                        "syntheaLinkStrategy": desired_strategy or "",
                    }
                },
            )
            normalized_reports += 1

        return {
            "normalizedAt": normalized_at,
            "processedLimit": resolved_limit,
            "downgradedLinkRecords": downgraded_links,
            "normalizedAppointments": normalized_appointments,
            "normalizedReports": normalized_reports,
            "authoritativeAppointmentLinks": len(authoritative_by_appointment),
            "authoritativeReportLinks": len(authoritative_by_report),
            "authoritativePatientLinks": len(authoritative_by_patient),
            "policy": "authoritative_links_only",
        }


    def list_pending_leafy_synthea_linkages(limit=50):
        if db_leafy_synthea_links_collection is None:
            return []
        resolved_limit = max(1, min(int(limit or 50), 200))
        rows = list(
            db_leafy_synthea_links_collection.find(
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
                    "linkageVersion": 1,
                    "candidateOptions": 1,
                    "resolvedAt": 1,
                },
            ).sort("resolvedAt", -1).limit(500)
        )
        pending = []
        for row in rows:
            classification = classify_link_record(row)
            status_value = classification.get("linkageStatus") or "unlinked"
            if status_value == "linked":
                continue
            pending.append(
                {
                    "leafyPatientId": row.get("leafyPatientId"),
                    "leafyAppointmentId": row.get("leafyAppointmentId"),
                    "leafyReportId": row.get("leafyReportId"),
                    "leafyPatientName": row.get("leafyPatientName"),
                    "linkageStatus": status_value,
                    "linkStrategy": row.get("linkStrategy"),
                    "syntheaPatientRef": row.get("syntheaPatientRef"),
                    "suggestedSyntheaPatientRef": row.get("suggestedSyntheaPatientRef"),
                    "candidateOptions": list(row.get("candidateOptions") or [])[:3],
                    "resolvedAt": row.get("resolvedAt"),
                }
            )
            if len(pending) >= resolved_limit:
                break
        return pending


    def backfill_embedded_screening_synthea_refs(*, authoritative_only=True, max_updates=None):
        resolved_limit = max(1, min(int(max_updates or 5000), 20000))
        source_processed = 0
        source_appointment_updates = 0
        source_report_updates = 0

        appointment_rows = list(
            collection.find(
                {},
                {
                    "_id": 1,
                    "syntheaPatientRef": 1,
                    "syntheaLinkStrategy": 1,
                    "metadata.syntheaPatientRef": 1,
                    "metadata.syntheaLinkStrategy": 1,
                    "metadata.extraSearchParams.subject.syntheaPatientRef": 1,
                    "metadata.extraSearchParams.subject.syntheaLinkStrategy": 1,
                },
            ).limit(resolved_limit)
        )
        for row in appointment_rows:
            appointment_id = row.get("_id")
            appointment_id_str = str(appointment_id or "").strip()
            if not appointment_id_str:
                continue
            synthea_ref = _normalize_patient_ref(
                row.get("syntheaPatientRef")
                or ((row.get("metadata") or {}).get("syntheaPatientRef"))
                or (((((row.get("metadata") or {}).get("extraSearchParams") or {}).get("subject") or {}).get("syntheaPatientRef")))
            )
            link_strategy = extract_embedded_synthea_link_strategy(appointment_doc=row)
            if not is_authoritative_link_strategy(link_strategy):
                continue
            if not synthea_ref or not _synthea_patient_ref_exists(synthea_ref):
                continue
            source_processed += 1
            appointment_result = collection.update_one(
                {"_id": appointment_id},
                {
                    "$set": {
                        "syntheaPatientRef": synthea_ref,
                        "metadata.syntheaPatientRef": synthea_ref,
                        "metadata.extraSearchParams.subject.syntheaPatientRef": synthea_ref,
                    }
                },
            )
            source_appointment_updates += int(appointment_result.modified_count or 0)
            report_result = db_clinical_reports_collection.update_many(
                {"appointmentID": appointment_id_str},
                {"$set": {"syntheaPatientRef": synthea_ref}},
            )
            source_report_updates += int(report_result.modified_count or 0)

        rows = []
        if db_leafy_synthea_links_collection is not None:
            rows = list(
                db_leafy_synthea_links_collection.find(
                    {},
                    {
                        "_id": 0,
                        "leafyAppointmentId": 1,
                        "leafyReportId": 1,
                        "leafyPatientId": 1,
                        "syntheaPatientRef": 1,
                        "linkStrategy": 1,
                        "linkageVersion": 1,
                    },
                ).sort("resolvedAt", -1).limit(resolved_limit)
            )
        appointment_updates = 0
        report_updates = 0
        skipped = 0
        processed = 0

        for row in rows:
            classification = classify_link_record(row)
            if authoritative_only and not classification.get("authoritative"):
                skipped += 1
                continue
            synthea_ref = _normalize_patient_ref(row.get("syntheaPatientRef"))
            if not synthea_ref:
                skipped += 1
                continue

            processed += 1
            appointment_id = str(row.get("leafyAppointmentId") or "").strip()
            report_id = str(row.get("leafyReportId") or "").strip()
            patient_id = str(row.get("leafyPatientId") or "").strip()

            if appointment_id:
                appointment_query = {"_id": appointment_id}
                try:
                    appointment_query = {"_id": ObjectId(appointment_id)}
                except Exception:
                    pass
                appointment_result = collection.update_one(
                    appointment_query,
                    {
                        "$set": {
                            "syntheaPatientRef": synthea_ref,
                            "metadata.syntheaPatientRef": synthea_ref,
                            "metadata.extraSearchParams.subject.syntheaPatientRef": synthea_ref,
                        }
                    },
                )
                appointment_updates += int(appointment_result.modified_count or 0)

            report_query = None
            if report_id:
                report_query = {"_id": report_id}
                try:
                    report_query = {"_id": ObjectId(report_id)}
                except Exception:
                    pass
            elif appointment_id:
                report_query = {"appointmentID": appointment_id}
            elif patient_id:
                report_query = {"$or": [{"patientID": patient_id}, {"patient-id": patient_id}]}

            if report_query:
                report_result = db_clinical_reports_collection.update_many(
                    report_query,
                    {"$set": {"syntheaPatientRef": synthea_ref}},
                )
                report_updates += int(report_result.modified_count or 0)

        return {
            "authoritativeOnly": bool(authoritative_only),
            "sourceDerivedAppointmentsProcessed": source_processed,
            "sourceDerivedAppointmentUpdates": source_appointment_updates,
            "sourceDerivedReportUpdates": source_report_updates,
            "processedLinkRows": processed,
            "skippedLinkRows": skipped,
            "updatedAppointments": appointment_updates,
            "updatedReports": report_updates,
        }


    def seed_missing_screening_synthea_refs(*, replace_invalid=True, max_reports=None):
        del replace_invalid, max_reports
        return {
            "disabled": True,
            "policy": "authoritative_links_only",
            "reason": "Deterministic screening backfill has been retired. Resolve authoritative screening-to-FHIR links explicitly instead.",
        }


    def upsert_synthetic_ecog_observations(*, overwrite=False, max_patients=None):
        cursor = db_pharma_patient_facts_collection.find(
            {},
            {
                "_id": 0,
                "patientRef": 1,
                "facts": 1,
                "generatedAt": 1,
                "sourceSummary.lastEventDate": 1,
            },
        ).limit(max(1, min(int(max_patients or 5000), 20000)))
        upserted = 0
        skipped_existing = 0
        skipped_ineligible = 0
        write_ops = []
        now_iso = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

        for facts_doc in cursor:
            patient_ref = _normalize_patient_ref(facts_doc.get("patientRef"))
            if not patient_ref:
                skipped_ineligible += 1
                continue
            synthetic_payload = derive_synthetic_ecog_payload_from_facts(facts_doc)
            if not synthetic_payload:
                skipped_ineligible += 1
                continue

            patient_id = patient_ref.split("/", 1)[1]
            observation_id = f"leafy-synth-ecog-{patient_id}"
            logical_ref = f"Observation/{observation_id}"
            if not overwrite and db_synthea_resources_collection.find_one(
                {"logicalRef": logical_ref},
                {"_id": 1},
            ):
                skipped_existing += 1
                continue

            effective_date = (
                (((facts_doc.get("sourceSummary") or {}).get("lastEventDate")) or facts_doc.get("generatedAt") or now_iso)
            )
            rationale_text = "; ".join(synthetic_payload.get("rationale") or [])
            resource = {
                "resourceType": "Observation",
                "id": observation_id,
                "meta": {
                    "tag": [
                        {
                            "system": "https://leafy.mongodb.com/fhir/enrichment",
                            "code": "synthetic-demo",
                            "display": "Synthetic demo enrichment",
                        },
                        {
                            "system": "https://leafy.mongodb.com/fhir/enrichment",
                            "code": SYNTHETIC_ECOG_OBSERVATION_VERSION,
                            "display": "Synthetic ECOG observation",
                        },
                    ]
                },
                "status": "final",
                "category": [
                    {
                        "coding": [
                            {
                                "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                "code": "survey",
                                "display": "Survey",
                            }
                        ],
                        "text": "Survey",
                    }
                ],
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": SYNTHETIC_ECOG_OBSERVATION_CODE,
                            "display": "ECOG Performance Status score",
                        }
                    ],
                    "text": "ECOG performance status",
                },
                "subject": {"reference": patient_ref},
                "effectiveDateTime": effective_date,
                "issued": now_iso,
                "valueInteger": int(synthetic_payload["value"]),
                "method": {"text": "Synthetic demo enrichment"},
                "note": [
                    {
                        "text": (
                            "Synthetic demo ECOG observation derived from normalized oncology facts for "
                            f"trial-matching demonstration. Rationale: {rationale_text or 'synthetic demo baseline'}."
                        )
                    }
                ],
            }
            normalized_sha = hashlib.sha256(
                json.dumps(resource, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            canonical_doc = {
                "datasetLabel": CANONICAL_FHIR_RUNTIME_DATASET_LABEL,
                "pipelineVersion": f"{CANONICAL_FHIR_RUNTIME_PIPELINE_VERSION}+{SYNTHETIC_ECOG_OBSERVATION_VERSION}",
                "ingestedAt": now_iso,
                "resourceType": "Observation",
                "resourceId": observation_id,
                "logicalRef": logical_ref,
                "resource": resource,
                "searchIndex": {
                    "date": [
                        {"name": "effectiveDateTime", "path": "effectiveDateTime", "value": effective_date},
                        {"name": "issued", "path": "issued", "value": now_iso},
                    ],
                    "reference": [
                        {
                            "name": "subject",
                            "path": "subject.reference",
                            "value": patient_ref,
                            "targetType": "Patient",
                        }
                    ],
                    "string": [
                        {"name": "text", "path": "code.text", "value": "ECOG performance status"},
                    ],
                    "token": [
                        {"name": "status", "path": "status", "code": "final", "system": None, "display": None},
                        {
                            "name": "category",
                            "path": "category",
                            "code": "survey",
                            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                            "display": "Survey",
                        },
                        {
                            "name": "code",
                            "path": "code",
                            "code": SYNTHETIC_ECOG_OBSERVATION_CODE,
                            "system": "http://loinc.org",
                            "display": "ECOG Performance Status score",
                        },
                    ],
                },
                "compartments": {"patientRefs": [patient_ref]},
                "source": {
                    "bundleType": "synthetic_demo",
                    "entryFullUrl": logical_ref,
                    "fileName": "generated://leafy/synthetic-ecog",
                    "filePath": "generated://leafy/synthetic-ecog",
                    "requestMethod": "UPSERT",
                    "requestUrl": "Observation",
                    "sourceKind": "synthetic_demo",
                    "derivedFrom": {
                        "patientFactsVersion": PHARMA_PATIENT_FACT_VERSION,
                        "rationale": synthetic_payload.get("rationale") or [],
                    },
                },
                "hashes": {
                    "rawResourceSha256": normalized_sha,
                    "normalizedResourceSha256": normalized_sha,
                },
                "narrative": {
                    "summaryText": f"Synthetic ECOG performance status {synthetic_payload['value']}",
                },
            }
            write_ops.append(
                UpdateOne(
                    {"logicalRef": logical_ref},
                    {"$set": canonical_doc},
                    upsert=True,
                )
            )
            upserted += 1

        if write_ops:
            db_synthea_resources_collection.bulk_write(write_ops, ordered=False)

        return {
            "upsertedSyntheticEcogObservations": upserted,
            "skippedExisting": skipped_existing,
            "skippedIneligible": skipped_ineligible,
            "observationVersion": SYNTHETIC_ECOG_OBSERVATION_VERSION,
        }


    def list_trial_matching_evaluation_runs(limit=10):
        safe_limit = max(1, min(int(limit or 10), 50))
        rows = list(
            db_pharma_evaluation_runs_collection.find({}, {"_id": 0}).sort("createdAt", -1).limit(safe_limit)
        )
        return attach_trial_matching_run_drift(rows)


    def list_trial_matching_improvement_runs(limit=10):
        safe_limit = max(1, min(int(limit or 10), 50))
        return list(
            db_pharma_improvement_runs_collection.find({}, {"_id": 0}).sort("createdAt", -1).limit(safe_limit)
        )


    def run_trial_matching_control_plane_evaluation(mode="runtime_snapshot", note=""):
        normalized_mode = str(mode or "runtime_snapshot").strip().lower() or "runtime_snapshot"
        if normalized_mode not in {"runtime_snapshot", "full_audit"}:
            normalized_mode = "runtime_snapshot"
        previous_run = db_pharma_evaluation_runs_collection.find_one({}, {"_id": 0}, sort=[("createdAt", -1)])
        summary = build_trial_matching_control_plane_summary(mode=normalized_mode)
        run_document = build_trial_matching_evaluation_run_document(
            summary=summary,
            mode=normalized_mode,
            previous_run=previous_run,
            note=note,
        )
        db_pharma_evaluation_runs_collection.insert_one(run_document)
        improvement_document = build_trial_matching_improvement_run_document(
            evaluation_run=run_document,
            previous_run=previous_run,
        )
        db_pharma_improvement_runs_collection.insert_one(improvement_document)
        refreshed_summary = build_trial_matching_control_plane_summary()
        return {
            "run": run_document,
            "improvement": improvement_document,
            "summary": refreshed_summary,
        }


    def build_trial_matching_control_plane_summary(mode="runtime_snapshot"):
        normalized_mode = str(mode or "runtime_snapshot").strip().lower() or "runtime_snapshot"
        full_audit = normalized_mode == "full_audit"
        link_sample_limit = None if full_audit else PHARMA_CONTROL_PLANE_LINK_SAMPLE_LIMIT
        fact_sample_limit = None if full_audit else PHARMA_CONTROL_PLANE_FACT_SAMPLE_LIMIT
        profile_sample_limit = None if full_audit else PHARMA_CONTROL_PLANE_PROFILE_SAMPLE_LIMIT
        report_sample_limit = None if full_audit else PHARMA_CONTROL_PLANE_REPORT_SAMPLE_LIMIT
        trial_sample_limit = None if full_audit else PHARMA_CONTROL_PLANE_TRIAL_SAMPLE_LIMIT
        cohort_sample_limit = None if full_audit else PHARMA_CONTROL_PLANE_COHORT_SAMPLE_LIMIT

        readiness_summary = build_fast_trial_matching_dataset_summary()
        oncology_quality_summary = None
        try:
            oncology_quality_summary = audit_breast_oncology_cohort_quality(sample_limit=cohort_sample_limit)
        except Exception:
            oncology_quality_summary = None

        if db_leafy_synthea_links_collection is not None:
            link_cursor = db_leafy_synthea_links_collection.find(
                {},
                {
                    "_id": 0,
                    "leafyPatientId": 1,
                    "leafyAppointmentId": 1,
                    "leafyReportId": 1,
                    "syntheaPatientRef": 1,
                    "suggestedSyntheaPatientRef": 1,
                    "linkStrategy": 1,
                    "linkageStatus": 1,
                    "linkageVersion": 1,
                    "candidateOptions": 1,
                },
            )
            if link_sample_limit is not None:
                link_cursor = link_cursor.limit(link_sample_limit)
            link_rows = list(link_cursor)
            linkage_summary = build_linkage_control_plane_summary_from_rows(link_rows)
        else:
            appointment_with_ref = collection.count_documents(
                {
                    "$or": [
                        {
                            "syntheaPatientRef": {"$exists": True, "$nin": ["", None]},
                            "syntheaLinkStrategy": {"$in": sorted(CURRENT_AUTHORITATIVE_LINK_STRATEGIES)},
                        },
                        {
                            "metadata.syntheaPatientRef": {"$exists": True, "$nin": ["", None]},
                            "metadata.syntheaLinkStrategy": {"$in": sorted(CURRENT_AUTHORITATIVE_LINK_STRATEGIES)},
                        },
                        {
                            "metadata.extraSearchParams.subject.syntheaPatientRef": {"$exists": True, "$nin": ["", None]},
                            "metadata.extraSearchParams.subject.syntheaLinkStrategy": {"$in": sorted(CURRENT_AUTHORITATIVE_LINK_STRATEGIES)},
                        },
                    ]
                }
            )
            report_total = db_clinical_reports_collection.estimated_document_count()
            report_with_ref = db_clinical_reports_collection.count_documents(
                {
                    "syntheaPatientRef": {"$exists": True, "$nin": ["", None]},
                    "syntheaLinkStrategy": {"$in": sorted(CURRENT_AUTHORITATIVE_LINK_STRATEGIES)},
                }
            )
            linkage_summary = build_linkage_control_plane_summary_from_screening_sources(
                appointment_total=collection.estimated_document_count(),
                appointment_with_ref=appointment_with_ref,
                report_total=report_total,
                report_with_ref=report_with_ref,
            )
        patient_fact_cursor = db_pharma_patient_facts_collection.find(
            {},
            {
                "_id": 0,
                "facts.receptorStatus": 1,
                "facts.performanceStatus": 1,
                "facts.stageOrExtent": 1,
                "facts.oncologyTreatmentHistory": 1,
            },
        )
        if fact_sample_limit is not None:
            patient_fact_cursor = patient_fact_cursor.limit(fact_sample_limit)
        patient_fact_rows = list(patient_fact_cursor)

        profile_cursor = db_pharma_profiles_collection.find(
            {},
            {
                "_id": 0,
                "conditionTerms": 1,
                "procedureTerms": 1,
                "diagnosticTerms": 1,
                "medicationTerms": 1,
                "observationTerms": 1,
                "documentTerms": 1,
                "featureTerms": 1,
                "patientMatchProfile.gaps": 1,
            },
        )
        if profile_sample_limit is not None:
            profile_cursor = profile_cursor.limit(profile_sample_limit)
        profile_rows = list(profile_cursor)

        report_cursor = db_clinical_reports_collection.find(
            {},
            {
                "_id": 0,
                "clinicalNotes": 1,
                "conclusions": 1,
            },
        )
        if report_sample_limit is not None:
            report_cursor = report_cursor.limit(report_sample_limit)
        report_rows = list(report_cursor)

        trial_cursor = db_pharma_trials_collection.find(
            {},
            {
                "_id": 0,
                "trialDefinitionCanonical.contractVersion": 1,
                "trialDefinitionCanonical.summary.totalCriteria": 1,
                "trialPrefilterContract.hardFilters.age": 1,
                "trialPrefilterContract.hardFilters.sex": 1,
                "trialPrefilterContract.softFilters.birads": 1,
                "parserWarnings.code": 1,
                "pharma_trial_rules.rule_kind": 1,
                "pharma_trial_rules.type": 1,
                "pharma_trial_rules.primary_criterion_group": 1,
                "pharma_trial_rules.deterministic_candidate": 1,
                "pharma_trial_rules.llm_candidate": 1,
            },
        )
        if trial_sample_limit is not None:
            trial_cursor = trial_cursor.limit(trial_sample_limit)
        trial_rows = list(trial_cursor)

        sparse_domain_summary = build_sparse_oncology_domain_summary_from_inputs(
            patient_fact_rows=patient_fact_rows,
            profile_rows=profile_rows,
            report_rows=report_rows,
        )
        summary = build_trial_control_plane_summary_from_inputs(
            readiness_summary=readiness_summary,
            oncology_quality_summary=oncology_quality_summary,
            linkage_summary=linkage_summary,
            trial_rows=trial_rows,
            sparse_domain_summary=sparse_domain_summary,
            patient_profile_rows=profile_rows,
        )

        if trial_sample_limit is not None:
            summary["summaryMode"] = "runtime_sampled"
            summary["sampled"] = True
            summary["sampleSizes"] = {
                "links": len(link_rows) if db_leafy_synthea_links_collection is not None else 0,
                "patientFacts": len(patient_fact_rows),
                "patientProfiles": len(profile_rows),
                "clinicalReports": len(report_rows),
                "trials": len(trial_rows),
            }
            trial_contracts = summary.get("trialContracts") or {}
            runtime_trial_count = int((((readiness_summary.get("runtimeCounts") or {}).get("trialCatalog")) or 0))
            if runtime_trial_count > 0:
                trial_contracts["trialCount"] = runtime_trial_count
            trial_contracts["sampled"] = True
            trial_contracts["sampleLimit"] = trial_sample_limit
            trial_contracts["sampledTrialRows"] = len(trial_rows)
            summary["trialContracts"] = trial_contracts

        recent_evaluation_runs = list_trial_matching_evaluation_runs(limit=8)
        recent_improvement_runs = list_trial_matching_improvement_runs(limit=8)
        summary["recentEvaluationRuns"] = recent_evaluation_runs
        summary["recentImprovementRuns"] = recent_improvement_runs
        summary["latestEvaluationRun"] = recent_evaluation_runs[0] if recent_evaluation_runs else None
        summary["latestImprovementRun"] = recent_improvement_runs[0] if recent_improvement_runs else None
        return summary


    def _resolve_linked_synthea_patient_ref(patient_context, report_doc=None, appointment_doc=None, allow_analog=True):
        context = patient_context if isinstance(patient_context, dict) else {}
        identity_snapshot = build_link_identity_snapshot(
            patient_context=context,
            report_doc=report_doc,
            appointment_doc=appointment_doc,
        )
        embedded_synthea_ref = extract_embedded_synthea_patient_ref(
            patient_context=context,
            report_doc=report_doc,
            appointment_doc=appointment_doc,
        )
        normalized_embedded_synthea_ref = _normalize_patient_ref(embedded_synthea_ref)
        embedded_link_strategy = extract_embedded_synthea_link_strategy(
            patient_context=context,
            report_doc=report_doc,
            appointment_doc=appointment_doc,
        )
        if (
            normalized_embedded_synthea_ref
            and is_authoritative_link_strategy(embedded_link_strategy)
            and _synthea_patient_ref_exists(normalized_embedded_synthea_ref)
        ):
            resolved_at = datetime.utcnow().isoformat() + "Z"
            _upsert_linkage_record(
                identity_snapshot,
                {
                    "syntheaPatientRef": normalized_embedded_synthea_ref,
                    "suggestedSyntheaPatientRef": normalized_embedded_synthea_ref,
                    "linkStrategy": embedded_link_strategy,
                    "linkageStatus": "linked",
                    "linkageConfidence": 1.0,
                    "linkageEvidence": ["Direct Synthea patient reference embedded on the screening appointment/report."],
                    "candidateOptions": [],
                    "linkedAt": resolved_at,
                    "resolvedAt": resolved_at,
                    "linkageVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
                },
            )
            return {
                "syntheaPatientRef": normalized_embedded_synthea_ref,
                "suggestedSyntheaPatientRef": normalized_embedded_synthea_ref,
                "linkStrategy": embedded_link_strategy,
                "fromCache": False,
                "linkageStatus": "linked",
                "linkageConfidence": 1.0,
                "linkageEvidence": ["Direct Synthea patient reference embedded on the screening appointment/report."],
                "linkedAt": resolved_at,
                "linkageVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
                "candidateOptions": [],
                "analogCandidates": [],
                "analogPipeline": [],
            }
        lookup_clauses = build_link_lookup_clauses(identity_snapshot)

        existing_link = None
        if lookup_clauses and db_leafy_synthea_links_collection is not None:
            existing_link = db_leafy_synthea_links_collection.find_one(
                {"$or": lookup_clauses},
                sort=[("resolvedAt", -1)]
            )

        existing_classification = classify_link_record(existing_link)
        if existing_classification.get("authoritative") and existing_link and existing_link.get("syntheaPatientRef"):
            return {
                "syntheaPatientRef": _normalize_patient_ref(existing_link.get("syntheaPatientRef")),
                "linkStrategy": existing_link.get("linkStrategy") or "manual_review",
                "fromCache": True,
                "linkageStatus": "linked",
                "linkageConfidence": 1.0,
                "linkageEvidence": existing_link.get("linkageEvidence") or [],
                "linkedAt": existing_link.get("linkedAt") or existing_link.get("resolvedAt"),
                "linkageVersion": existing_link.get("linkageVersion") or PHARMA_LINKAGE_CONTRACT_VERSION,
                "candidateOptions": existing_link.get("candidateOptions") or [],
                "analogCandidates": [],
                "analogPipeline": [],
            }

        analog_candidates = []
        analog_pipeline = []
        strategy = "unlinked"
        linkage_status = "unlinked"
        linkage_confidence = 0.0
        linkage_evidence = []
        candidate_options = []
        if allow_analog and context:
            analog_candidates, analog_pipeline = infer_synthea_analog_patients(context, limit=4)
            for item in analog_candidates[:3]:
                candidate_option = _build_candidate_option(item)
                if candidate_option:
                    candidate_options.append(candidate_option)
        if (
            normalized_embedded_synthea_ref
            and _synthea_patient_ref_exists(normalized_embedded_synthea_ref)
            and not is_authoritative_link_strategy(embedded_link_strategy)
            and not any((item or {}).get("patientRef") == normalized_embedded_synthea_ref for item in candidate_options)
        ):
            embedded_candidate = _build_candidate_option(
                {
                    "patientRef": normalized_embedded_synthea_ref,
                    "sharedKeywordCount": 0,
                    "resourceEventCount": 0,
                },
                source="embedded_legacy_ref",
            )
            if embedded_candidate:
                candidate_options.insert(0, embedded_candidate)

        existing_suggested_ref = _normalize_patient_ref((existing_link or {}).get("suggestedSyntheaPatientRef"))
        existing_candidate_ref = _normalize_patient_ref((existing_link or {}).get("syntheaPatientRef"))
        if existing_candidate_ref and not any((item or {}).get("patientRef") == existing_candidate_ref for item in candidate_options):
            legacy_candidate = _build_candidate_option(
                {
                    "patientRef": existing_candidate_ref,
                    "sharedKeywordCount": (existing_link or {}).get("sharedKeywordCount") or 0,
                    "resourceEventCount": 0,
                },
                source="stored_link",
            )
            if legacy_candidate:
                candidate_options.insert(0, legacy_candidate)
        elif existing_suggested_ref and not any((item or {}).get("patientRef") == existing_suggested_ref for item in candidate_options):
            suggested_candidate = _build_candidate_option(
                {
                    "patientRef": existing_suggested_ref,
                    "sharedKeywordCount": 0,
                    "resourceEventCount": 0,
                },
                source="stored_suggestion",
            )
            if suggested_candidate:
                candidate_options.insert(0, suggested_candidate)

        if existing_classification.get("legacyHeuristic"):
            strategy = "legacy_heuristic_link"
            linkage_status = "legacy_heuristic_link"
            linkage_confidence = 0.0
            linkage_evidence = [
                "Stored Synthea link was created by a legacy heuristic policy and is no longer trusted automatically.",
            ]
        elif candidate_options:
            strategy = "candidate_only"
            linkage_status = "candidate_only"
            linkage_confidence = 0.0
            linkage_evidence = [
                "Candidate Synthea matches are available, but no authoritative link has been confirmed.",
            ]

        resolved_at = datetime.utcnow().isoformat() + "Z"
        update_doc = _upsert_linkage_record(
            identity_snapshot,
            {
            "syntheaPatientRef": None,
            "suggestedSyntheaPatientRef": (candidate_options[0] or {}).get("patientRef") if candidate_options else None,
            "linkStrategy": strategy,
            "linkageStatus": linkage_status,
            "linkageConfidence": round(float(linkage_confidence), 4),
            "linkageEvidence": linkage_evidence[:6],
            "candidateOptions": candidate_options[:3],
            "linkedAt": None,
            "resolvedAt": resolved_at,
            "linkageVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
            "sharedKeywordCount": (candidate_options[0] or {}).get("sharedKeywordCount") if candidate_options else None,
            },
        )

        return {
            "syntheaPatientRef": "",
            "suggestedSyntheaPatientRef": update_doc.get("suggestedSyntheaPatientRef") or "",
            "linkStrategy": strategy,
            "fromCache": False,
            "linkageStatus": linkage_status,
            "linkageConfidence": round(float(linkage_confidence), 4),
            "linkageEvidence": linkage_evidence[:6],
            "linkedAt": None,
            "linkageVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
            "candidateOptions": candidate_options[:3],
            "analogCandidates": analog_candidates,
            "analogPipeline": analog_pipeline,
        }


    def build_leafy_candidates_for_trial(trial_doc, limit=8):
        def assessment_rank(value):
            normalized = normalize_pharma_text(value)
            if normalized == "likely_eligible":
                return 2
            if normalized == "needs_review":
                return 1
            if normalized == "likely_ineligible":
                return 0
            return -1

        trial_eligibility = trial_doc.get("eligibility") or {}
        report_cursor = db_clinical_reports_collection.find(
            {},
            {
                "_id": 1,
                "appointmentID": 1,
                "patientID": 1,
                "patient-id": 1,
                "user": 1,
                "series": 1,
                "clinicalNotes": 1,
                "conclusions": 1,
                "date": 1,
            }
        )

        rows = []
        for report_doc in report_cursor:
            series = report_doc.get("series") or []
            birads_values = []
            parsed_age = None
            for image_entry in series:
                if not isinstance(image_entry, dict):
                    continue
                image_meta = image_entry.get("imageMetadata")
                if not isinstance(image_meta, dict):
                    image_meta = {}
                if parsed_age is None:
                    parsed_age = parse_age_token(image_meta.get("PatientAge"))
                birads_raw = image_meta.get("biRads")
                if isinstance(birads_raw, dict):
                    birads_value = birads_raw.get("BIRAD")
                else:
                    birads_value = birads_raw
                if birads_value is None:
                    continue
                try:
                    birads_values.append(int(birads_value))
                except Exception:
                    continue

            raw_notes = report_doc.get("clinicalNotes") or ""
            raw_conclusions = report_doc.get("conclusions") or ""
            text_blob = f"{raw_notes} {raw_conclusions}".strip()
            normalized_text = normalize_pharma_text(text_blob)
            keywords = [token for token in PHARMA_TRAIL_KEYWORDS if token in normalized_text]

            patient_context = {
                "age": parsed_age,
                "sex": "female",
                "biradsValues": sorted(list(set(birads_values))),
                "numberImages": len(series),
                "keywords": sorted(list(set(keywords))),
            }
            structured = evaluate_trial_structured_match(trial_doc, patient_context)
            if (
                normalize_pharma_text(structured.get("overallAssessment")) == "likely_ineligible"
                and safe_float(structured.get("structuredScore"), 0) < 0.45
            ):
                continue

            rows.append(
                {
                    "source": "leafy",
                    "appointmentId": str(report_doc.get("appointmentID") or ""),
                    "patientId": str(report_doc.get("patientID") or report_doc.get("patient-id") or ""),
                    "patientName": report_doc.get("user") or "Unknown",
                    "age": parsed_age,
                    "biradsValues": sorted(list(set(birads_values))),
                    "numberImages": len(series),
                    "keywords": sorted(list(set(keywords)))[:10],
                    "structuredScore": structured.get("structuredScore"),
                    "isEligibleStructured": structured.get("isEligibleStructured"),
                    "overallAssessment": structured.get("overallAssessment"),
                    "matchReasons": (structured.get("matchReasons") or [])[:4],
                    "missingCriteria": (structured.get("missingCriteria") or [])[:4],
                    "decisionSummary": structured.get("decisionSummary") or {},
                    "parserWarnings": (structured.get("parserWarnings") or [])[:8],
                    "validatorWarnings": (structured.get("validatorWarnings") or [])[:12],
                    "criterionBreakdown": structured.get("criterionBreakdown") or [],
                    "coverageMeter": structured.get("coverageMeter") or {},
                    "riskEvidence": structured.get("riskEvidence") or {},
                    "ruleEvaluations": (structured.get("ruleEvaluations") or [])[:20],
                    "criteriaLevelMatches": (structured.get("criteriaLevelMatches") or [])[:10],
                    "trialEligibilitySnapshot": {
                        "sex": trial_eligibility.get("sex") or [],
                        "ageMin": trial_eligibility.get("ageMin"),
                        "ageMax": trial_eligibility.get("ageMax"),
                        "biradsAny": trial_eligibility.get("biradsAny") or [],
                    },
                    "reportDate": serialize_doc(report_doc.get("date")),
                }
            )

        rows_sorted = sorted(
            rows,
            key=lambda item: (
                assessment_rank(item.get("overallAssessment")),
                safe_float(item.get("structuredScore"), 0),
                item.get("numberImages") or 0
            ),
            reverse=True
        )
        return rows_sorted[:max(1, min(limit, 30))]


    def ensure_pharma_indexes():
        db_pharma_trials_collection.create_index("trialId", unique=True)
        db_pharma_trials_collection.create_index("nctId", unique=True)
        db_pharma_trials_collection.create_index([("status", 1), ("diseaseArea", 1)])
        db_pharma_trials_collection.create_index([("source", 1), ("sourceStudyLastUpdated", -1)])
        db_pharma_trials_collection.create_index([("source", 1), ("status", 1), ("phase", 1), ("sourceStudyLastUpdated", -1)])
        db_pharma_trials_collection.create_index("trialPrefilterContract.hardFilters.sex.anyOf")
        db_pharma_trials_collection.create_index("trialPrefilterContract.hardFilters.age.min")
        db_pharma_trials_collection.create_index("trialPrefilterContract.hardFilters.age.max")
        db_pharma_trials_collection.create_index("sourcePayloadHash")
        db_pharma_trials_collection.create_index("trialVersion.sourceHash")
        db_pharma_trials_collection.create_index("trialDefinitionCanonical.contractVersion")
        db_pharma_trials_collection.create_index("trialDefinitionCanonical.criteria.criterionId")
        db_pharma_trials_collection.create_index("trialDefinitionCanonical.criteria.cdisc.sdtmTi.iecat")
        db_pharma_trials_collection.create_index("trialDefinitionCanonical.criteria.cdisc.sdtmTi.ietestcd")
        db_pharma_trials_collection.create_index("trialDefinitionCanonical.criteria.ontologyBindings.primaryCriterionGroup")
        db_pharma_trials_collection.create_index("pharma_trial_rules.concept_tags")
        db_pharma_trials_collection.create_index("pharma_trial_rules.type")
        db_pharma_profiles_collection.create_index("patientRef", unique=True)
        db_pharma_patient_facts_collection.create_index("patientRef", unique=True)
        db_pharma_patient_facts_collection.create_index("factVersion")
        db_pharma_patient_facts_collection.create_index("generatedAt")
        db_pharma_patient_facts_collection.create_index("patientEvidenceProfile.schemaVersion")
        db_pharma_patient_facts_collection.create_index("patientEvidenceProfile.identity.linkageStatus")
        db_pharma_patient_facts_collection.create_index("patientEvidenceProfile.completeness.coveragePct")
        if db_pharma_patient_curation_overlays_collection is not None:
            db_pharma_patient_curation_overlays_collection.create_index("patientRef", unique=True)
            db_pharma_patient_curation_overlays_collection.create_index("overlayVersion")
            db_pharma_patient_curation_overlays_collection.create_index("updatedAt")
        if db_pharma_oncology_cohort_collection is not None:
            db_pharma_oncology_cohort_collection.create_index("patientRef", unique=True)
            db_pharma_oncology_cohort_collection.create_index("cohortVersion")
            db_pharma_oncology_cohort_collection.create_index("candidateCategory")
            db_pharma_oncology_cohort_collection.create_index("matchingReadinessTier")
            db_pharma_oncology_cohort_collection.create_index("enrichmentPriorityScore")
        db_pharma_profiles_collection.create_index([("gender", 1), ("age", 1)])
        db_pharma_profiles_collection.create_index("featureTerms")
        db_pharma_profiles_collection.create_index("profileVectorEmbeddingModel")
        db_pharma_profiles_collection.create_index("patientMatchProfile.schemaVersion")
        db_pharma_profiles_collection.create_index("patientMatchProfile.matchFilters.sex")
        db_pharma_profiles_collection.create_index("patientMatchProfile.matchFilters.ageYears")
        db_pharma_profiles_collection.create_index("patientMatchProfile.identity.linkageStatus")
        db_pharma_evaluation_runs_collection.create_index("runId", unique=True)
        db_pharma_evaluation_runs_collection.create_index("createdAt")
        db_pharma_evaluation_runs_collection.create_index("mode")
        db_pharma_improvement_runs_collection.create_index("improvementRunId", unique=True)
        db_pharma_improvement_runs_collection.create_index("evaluationRunId")
        db_pharma_improvement_runs_collection.create_index("createdAt")
        if db_pharma_feature_edges_collection is not None:
            db_pharma_feature_edges_collection.create_index("edgeKey", unique=True)
            db_pharma_feature_edges_collection.create_index([("nodeFrom", 1), ("nodeTo", 1)])
        if db_leafy_synthea_links_collection is not None:
            db_leafy_synthea_links_collection.create_index("leafyPatientId")
            db_leafy_synthea_links_collection.create_index("leafyAppointmentId")
            db_leafy_synthea_links_collection.create_index("leafyReportId")
            db_leafy_synthea_links_collection.create_index("syntheaPatientRef")
            db_leafy_synthea_links_collection.create_index("linkageStatus")
            db_leafy_synthea_links_collection.create_index("linkStrategy")
            db_leafy_synthea_links_collection.create_index("linkageVersion")
            db_leafy_synthea_links_collection.create_index("resolvedAt")
        db_synthea_resources_collection.create_index([("datasetLabel", 1), ("logicalRef", 1)], unique=True)
        db_synthea_resources_collection.create_index([("resourceType", 1), ("resourceId", 1)])
        db_synthea_resources_collection.create_index("resourceType")
        db_synthea_resources_collection.create_index("logicalRef")
        db_synthea_resources_collection.create_index("resource.resourceType")
        db_synthea_resources_collection.create_index("compartments.patientRefs")
        db_synthea_resources_collection.create_index("compartments.encounterRefs")
        db_synthea_resources_collection.create_index("searchIndex.reference.value")
        db_synthea_resources_collection.create_index("searchIndex.token.code")
        db_synthea_resources_collection.create_index("searchIndex.token.system")
        db_synthea_resources_collection.create_index("searchIndex.string.value")
        db_synthea_resources_collection.create_index("searchIndex.date.value")
        db_synthea_resources_collection.create_index("searchIndex.quantity.code")
        db_synthea_resources_collection.create_index("searchIndex.number.value")


    def _build_synthea_resource_identity(document):
        if not isinstance(document, dict):
            return None
        dataset_label = str(document.get("datasetLabel") or "").strip()
        logical_ref = str(document.get("logicalRef") or "").strip()
        if dataset_label and logical_ref:
            return {"datasetLabel": dataset_label, "logicalRef": logical_ref}

        resource = document.get("resource") or {}
        resource_type = str(document.get("resourceType") or resource.get("resourceType") or "").strip()
        resource_id = str(document.get("resourceId") or resource.get("id") or "").strip()
        if resource_type and resource_id:
            return {"resourceType": resource_type, "resourceId": resource_id}
        return None


    def _flush_synthea_resource_batch(batch):
        if not batch:
            return {"inserted": 0, "matched": 0, "modified": 0, "upserted": 0}
        result = db_synthea_resources_collection.bulk_write(batch, ordered=False)
        return {
            "inserted": 0,
            "matched": int(result.matched_count),
            "modified": int(result.modified_count),
            "upserted": int(result.upserted_count),
        }


    def _insert_synthea_resource_batch(batch):
        if not batch:
            return {"inserted": 0, "matched": 0, "modified": 0, "upserted": 0}
        result = db_synthea_resources_collection.insert_many(batch, ordered=False)
        return {
            "inserted": len(result.inserted_ids),
            "matched": 0,
            "modified": 0,
            "upserted": 0,
        }


    def sync_synthea_resources_from_ndjson(ndjson_path, replace_existing=False, batch_size=800):
        if not ndjson_path or not os.path.exists(ndjson_path):
            return {
                "loadedFromFile": False,
                "documentsLoaded": 0,
                "existingCount": db_synthea_resources_collection.estimated_document_count(),
                "warning": "Configured Synthea resources NDJSON file path does not exist",
            }

        if replace_existing:
            db_synthea_resources_collection.drop()

        totals = {
            "loadedFromFile": True,
            "documentsLoaded": 0,
            "inserted": 0,
            "linesRead": 0,
            "invalidLines": 0,
            "matched": 0,
            "modified": 0,
            "upserted": 0,
            "skippedWithoutIdentity": 0,
        }
        normalized_batch_size = max(1, int(batch_size or 800))
        collection_was_empty = db_synthea_resources_collection.estimated_document_count() == 0
        if not collection_was_empty:
            ensure_pharma_indexes()
        batch = []

        with open(ndjson_path, "r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                totals["linesRead"] += 1
                try:
                    document = json.loads(line)
                except json.JSONDecodeError:
                    totals["invalidLines"] += 1
                    continue

                identity_filter = _build_synthea_resource_identity(document)
                if not identity_filter:
                    totals["skippedWithoutIdentity"] += 1
                    continue

                if collection_was_empty:
                    batch.append(document)
                else:
                    batch.append(UpdateOne(identity_filter, {"$set": document}, upsert=True))
                if len(batch) >= normalized_batch_size:
                    batch_summary = (
                        _insert_synthea_resource_batch(batch)
                        if collection_was_empty
                        else _flush_synthea_resource_batch(batch)
                    )
                    for key, value in batch_summary.items():
                        totals[key] += value
                    totals["documentsLoaded"] = totals["inserted"] + totals["modified"] + totals["upserted"]
                    batch = []
                    logging.info(
                        "Synced Synthea resources through line %s into %s.%s",
                        line_number,
                        SYNTHEA_FHIR_DB_NAME,
                        SYNTHEA_RESOURCES_COLLECTION_NAME,
                    )

        batch_summary = (
            _insert_synthea_resource_batch(batch)
            if collection_was_empty
            else _flush_synthea_resource_batch(batch)
        )
        for key, value in batch_summary.items():
            totals[key] += value
        totals["documentsLoaded"] = totals["inserted"] + totals["modified"] + totals["upserted"]
        ensure_pharma_indexes()
        totals["existingCount"] = db_synthea_resources_collection.count_documents({})
        return totals


    def load_synthea_resources_if_missing(ndjson_path):
        current_count = db_synthea_resources_collection.estimated_document_count()
        if current_count > 0:
            return {
                "loadedFromFile": False,
                "documentsLoaded": 0,
                "existingCount": current_count,
            }

        if not ndjson_path or not os.path.exists(ndjson_path):
            return {
                "loadedFromFile": False,
                "documentsLoaded": 0,
                "existingCount": 0,
                "warning": "Synthea resources collection is empty and ndjson file path does not exist",
            }

        return sync_synthea_resources_from_ndjson(ndjson_path, replace_existing=False, batch_size=800)

    def build_profile_match_text(profile_doc):
        profile = profile_doc if isinstance(profile_doc, dict) else {}
        age_value = profile.get("age")
        gender_value = normalize_pharma_text(profile.get("gender") or "")
        patient_match_profile = profile.get("patientMatchProfile") or {}
        key_terms = []
        for key in [
            "conditionTerms",
            "procedureTerms",
            "diagnosticTerms",
            "medicationTerms",
            "observationTerms",
            "documentTerms",
            "featureTerms",
        ]:
            values = profile.get(key) or []
            if not isinstance(values, list):
                continue
            for item in values[:40]:
                token = normalize_pharma_text(item)
                if token:
                    key_terms.append(token)
        deduped_terms = []
        seen = set()
        for token in key_terms:
            if token in seen:
                continue
            seen.add(token)
            deduped_terms.append(token)
            if len(deduped_terms) >= 200:
                break
        candidate_signals = [
            normalize_pharma_text(item)
            for item in (patient_match_profile.get("candidateSignals") or [])
            if normalize_pharma_text(item)
        ]
        for token in candidate_signals:
            if token in seen:
                continue
            seen.add(token)
            deduped_terms.append(token)
            if len(deduped_terms) >= 230:
                break
        screening_summary = patient_match_profile.get("screeningSummary") or {}
        if screening_summary.get("biradsMax") is not None:
            deduped_terms.append(f"birads {screening_summary.get('biradsMax')}")
        suspicious_laterality = normalize_pharma_text(screening_summary.get("suspiciousLaterality") or "")
        if suspicious_laterality:
            deduped_terms.append(f"laterality {suspicious_laterality}")
        header = f"patient profile age {age_value if age_value is not None else 'unknown'} gender {gender_value or 'unknown'}"
        return f"{header} {' '.join(deduped_terms)}".strip()[:4200]

    def batch_embed_profile_documents(documents, batch_size=64):
        if not isinstance(documents, list) or len(documents) == 0:
            return documents

        for doc in documents:
            profile_match_text = str(doc.get("profileMatchText") or "").strip()
            if not profile_match_text:
                profile_match_text = build_profile_match_text(doc)
            doc["profileMatchText"] = profile_match_text

        if not VOYAGE_API_KEY or not voyage_embed_texts:
            return documents

        embedded_count = 0
        normalized_batch_size = max(1, int(batch_size or 64))
        texts = [str(doc.get("profileMatchText") or "").strip() for doc in documents]
        for start in range(0, len(texts), normalized_batch_size):
            batch_texts = texts[start:start + normalized_batch_size]
            batch_docs = documents[start:start + normalized_batch_size]
            try:
                embeddings = voyage_embed_texts(
                    batch_texts,
                    embedding_model=VOYAGE_EMBEDDING_MODEL,
                    input_type="document",
                )
                for idx, embedding in enumerate(embeddings):
                    if not isinstance(embedding, list) or idx >= len(batch_docs):
                        continue
                    batch_docs[idx]["profileVectorEmbedding"] = embedding
                    batch_docs[idx]["profileVectorEmbeddingModel"] = VOYAGE_EMBEDDING_MODEL
                    embedded_count += 1
            except Exception as batch_error:
                logging.warning(f"Batch profile embedding failed (offset {start}): {batch_error}")

        logging.info(f"Batch embedded {embedded_count}/{len(documents)} patient profiles")
        return documents


    def rebuild_pharma_patient_profiles(embed_profiles=True, patient_refs=None):
        target_patient_refs = {
            _normalize_patient_ref(item)
            for item in (patient_refs or [])
            if _normalize_patient_ref(item)
        } or None
        facts_map = {}
        facts_cursor = db_pharma_patient_facts_collection.find(
            {"patientRef": {"$in": sorted(target_patient_refs)}} if target_patient_refs is not None else {},
            {
                "patientRef": 1,
                "factVersion": 1,
                "facts": 1,
                "patientEvidenceProfile": 1,
            }
        )
        for doc in facts_cursor:
            patient_ref = str(doc.get("patientRef") or "").strip()
            if patient_ref:
                facts_map[patient_ref] = doc

        patient_meta_map = {}
        patient_cursor = db_synthea_resources_collection.find(
            {"resource.resourceType": "Patient"},
            {"resource.id": 1, "resource.gender": 1, "resource.birthDate": 1, "resource.name": 1}
        )
        for doc in patient_cursor:
            resource = doc.get("resource") or {}
            patient_id = str(resource.get("id") or "").strip()
            if not patient_id:
                continue
            patient_ref = _normalize_patient_ref(patient_id)
            if target_patient_refs is not None and patient_ref not in target_patient_refs:
                continue
            patient_name = ""
            names = resource.get("name") or []
            if names and isinstance(names[0], dict):
                given = " ".join(names[0].get("given") or [])
                family = names[0].get("family") or ""
                patient_name = f"{given} {family}".strip()
            patient_meta_map[patient_ref] = {
                "patientRef": patient_ref,
                "name": patient_name,
                "gender": normalize_pharma_text(resource.get("gender")),
                "birthDate": resource.get("birthDate"),
                "age": parse_birthdate_to_age(resource.get("birthDate")),
            }

        profile_map = {}
        cursor = db_synthea_resources_collection.find(
            {"resource.resourceType": {"$in": list(PHARMA_RESOURCE_TYPES - {"Patient"})}},
            {
                "resource.resourceType": 1,
                "resource.subject.reference": 1,
                "resource.patient.reference": 1,
                "resource.beneficiary.reference": 1,
                "resource.code": 1,
                "resource.type": 1,
                "resource.medicationCodeableConcept": 1,
                "resource.valueCodeableConcept": 1,
                "resource.valueString": 1,
                "resource.valueInteger": 1,
                "resource.valueBoolean": 1,
                "resource.text.div": 1,
                "resource.category": 1,
                "resource.interpretation": 1,
                "resource.bodySite": 1,
                "resource.method": 1,
                "resource.clinicalStatus": 1,
                "resource.verificationStatus": 1,
                "resource.specimen.type": 1,
                "resource.stage": 1,
                "resource.component": 1,
                "resource.note": 1,
                "resource.conclusion": 1,
                "resource.description": 1,
                "resource.effectiveDateTime": 1,
                "resource.recordedDate": 1,
                "resource.issued": 1,
                "resource.authoredOn": 1,
                "resource.onsetDateTime": 1,
                "resource.date": 1,
                "resource.performedPeriod": 1,
                "resource.period": 1,
            }
        )
        for doc in cursor:
            resource = doc.get("resource") or {}
            resource_type = str(resource.get("resourceType") or "").strip()
            patient_ref = _resolve_resource_patient_ref(resource)
            if not patient_ref:
                continue
            if target_patient_refs is not None and patient_ref not in target_patient_refs:
                continue

            profile = profile_map.setdefault(
                patient_ref,
                {
                    **patient_meta_map.get(patient_ref, {
                        "patientRef": patient_ref,
                        "name": "",
                        "gender": "",
                        "birthDate": None,
                        "age": None,
                    }),
                    "conditionCodes": set(),
                    "conditionTerms": set(),
                    "procedureCodes": set(),
                    "procedureTerms": set(),
                    "diagnosticCodes": set(),
                    "diagnosticTerms": set(),
                    "medicationCodes": set(),
                    "medicationTerms": set(),
                    "observationCodes": set(),
                    "observationTerms": set(),
                    "documentTerms": set(),
                    "featureTerms": set(),
                    "resourceTypeCounts": defaultdict(int),
                    "resourceEventCount": 0,
                    "lastEventDate": None,
                }
            )

            extracted_codes, extracted_terms = extract_codes_and_terms(resource)
            normalized_terms = [normalize_pharma_text(item) for item in extracted_terms if normalize_pharma_text(item)]
            for term in normalized_terms:
                profile["featureTerms"].add(term)

            if resource_type == "Condition":
                profile["conditionCodes"].update(extracted_codes)
                profile["conditionTerms"].update(normalized_terms)
            elif resource_type == "Procedure":
                profile["procedureCodes"].update(extracted_codes)
                profile["procedureTerms"].update(normalized_terms)
            elif resource_type == "MedicationRequest":
                profile["medicationCodes"].update(extracted_codes)
                profile["medicationTerms"].update(normalized_terms)
            elif resource_type == "Observation":
                profile["observationCodes"].update(extracted_codes)
                profile["observationTerms"].update(normalized_terms)
            elif resource_type == "DiagnosticReport":
                profile["diagnosticCodes"].update(extracted_codes)
                profile["diagnosticTerms"].update(normalized_terms)
            elif resource_type == "DocumentReference":
                profile["documentTerms"].update(normalized_terms)

            profile["resourceTypeCounts"][resource_type] += 1
            profile["resourceEventCount"] += 1

            event_date = extract_resource_event_datetime(resource)
            if event_date is not None:
                if profile["lastEventDate"] is None or event_date > profile["lastEventDate"]:
                    profile["lastEventDate"] = event_date

        profile_documents = []
        for patient_ref, profile in profile_map.items():
            feature_terms_sorted = sorted(list(profile["featureTerms"]))[:300]
            document = {
                "patientRef": patient_ref,
                "name": profile.get("name"),
                "gender": profile.get("gender"),
                "birthDate": profile.get("birthDate"),
                "age": profile.get("age"),
                "conditionCodes": sorted(list(profile["conditionCodes"]))[:120],
                "conditionTerms": sorted(list(profile["conditionTerms"]))[:120],
                "procedureCodes": sorted(list(profile["procedureCodes"]))[:120],
                "procedureTerms": sorted(list(profile["procedureTerms"]))[:120],
                "diagnosticCodes": sorted(list(profile["diagnosticCodes"]))[:120],
                "diagnosticTerms": sorted(list(profile["diagnosticTerms"]))[:120],
                "medicationCodes": sorted(list(profile["medicationCodes"]))[:120],
                "medicationTerms": sorted(list(profile["medicationTerms"]))[:120],
                "observationCodes": sorted(list(profile["observationCodes"]))[:120],
                "observationTerms": sorted(list(profile["observationTerms"]))[:120],
                "documentTerms": sorted(list(profile["documentTerms"]))[:120],
                "featureTerms": feature_terms_sorted,
                "resourceTypeCounts": dict(profile["resourceTypeCounts"]),
                "resourceEventCount": int(profile.get("resourceEventCount", 0)),
                "lastEventDate": profile.get("lastEventDate"),
                "updatedAt": datetime.utcnow(),
            }
            oncology_assessment_input = build_oncology_cohort_assessment_input(
                patient_ref=patient_ref,
                facts_doc=facts_map.get(patient_ref) or {},
                profile_doc=document,
            )
            oncology_cohort_document = build_breast_oncology_cohort_document(
                patient_ref=patient_ref,
                assessment_input=oncology_assessment_input,
                source_dataset={
                    "db": SYNTHEA_FHIR_DB_NAME,
                    "collection": SYNTHEA_RESOURCES_COLLECTION_NAME,
                },
            )
            patient_match_profile = build_patient_match_profile_v2(
                profile_doc=document,
                evidence_profile=((facts_map.get(patient_ref) or {}).get("patientEvidenceProfile") or {}),
                oncology_cohort=oncology_cohort_document,
                normalize_text_fn=normalize_pharma_text,
            )
            document["patientMatchProfile"] = patient_match_profile
            document["profileMatchText"] = build_profile_match_text(document)
            profile_documents.append(document)

        if embed_profiles:
            profile_documents = batch_embed_profile_documents(profile_documents, batch_size=64)

        write_ops = []
        for document in profile_documents:
            write_ops.append(
                UpdateOne(
                    {"patientRef": document.get("patientRef")},
                    {"$set": document},
                    upsert=True
                )
            )

        if write_ops:
            db_pharma_profiles_collection.bulk_write(write_ops, ordered=False)
        active_profile_refs = sorted([str(document.get("patientRef") or "").strip() for document in profile_documents if str(document.get("patientRef") or "").strip()])
        if target_patient_refs is None:
            if active_profile_refs:
                db_pharma_profiles_collection.delete_many({"patientRef": {"$nin": active_profile_refs}})
            else:
                db_pharma_profiles_collection.delete_many({})

        return {
            "profileCount": len(write_ops),
            "collectionCount": db_pharma_profiles_collection.estimated_document_count(),
            "patientMatchProfileVersion": PATIENT_MATCH_PROFILE_VERSION,
            "embeddedProfiles": bool(embed_profiles),
            "incremental": bool(target_patient_refs),
            "targetedPatientCount": len(active_profile_refs),
        }


    def build_patient_curation_coverage_summary_document():
        facts_documents = list(
            db_pharma_patient_facts_collection.find(
                {},
                {
                    "patientRef": 1,
                    "patientEvidenceProfile": 1,
                }
            )
        )
        profile_documents = list(
            db_pharma_profiles_collection.find(
                {},
                {
                    "patientRef": 1,
                    "patientMatchProfile": 1,
                }
            )
        )
        summary = build_patient_curation_coverage_summary(
            facts_documents=facts_documents,
            profile_documents=profile_documents,
        )
        summary["factsCollection"] = db_pharma_patient_facts_collection.name if hasattr(db_pharma_patient_facts_collection, "name") else None
        summary["profilesCollection"] = db_pharma_profiles_collection.name if hasattr(db_pharma_profiles_collection, "name") else None
        return summary


    def rebuild_pharma_feature_edges():
        if db_pharma_feature_edges_collection is None:
            return {
                "edgeDocumentsUpserted": 0,
                "collectionCount": 0,
                "enabled": False,
                "reason": "feature_edges_collection_disabled",
            }
        profiles_cursor = db_pharma_profiles_collection.find(
            {},
            {
                "patientRef": 1,
                "conditionCodes": 1,
                "procedureCodes": 1,
                "diagnosticCodes": 1,
                "observationCodes": 1,
                "featureTerms": 1,
            }
        )

        write_ops = []
        for profile in profiles_cursor:
            patient_ref = str(profile.get("patientRef") or "").strip()
            if not patient_ref:
                continue

            patient_node = f"patient::{patient_ref}"
            features = set()
            for code in profile.get("conditionCodes") or []:
                features.add(f"condition::{normalize_pharma_text(code)}")
            for code in profile.get("procedureCodes") or []:
                features.add(f"procedure::{normalize_pharma_text(code)}")
            for code in profile.get("diagnosticCodes") or []:
                features.add(f"diagnostic::{normalize_pharma_text(code)}")
            for code in profile.get("observationCodes") or []:
                features.add(f"observation::{normalize_pharma_text(code)}")
            for term in (profile.get("featureTerms") or [])[:60]:
                token = normalize_pharma_text(term)
                if token:
                    features.add(f"term::{token}")

            for feature_node in features:
                if not feature_node:
                    continue
                forward_key = f"{patient_node}=>{feature_node}"
                reverse_key = f"{feature_node}=>{patient_node}"
                write_ops.append(
                    UpdateOne(
                        {"edgeKey": forward_key},
                        {
                            "$set": {
                                "edgeKey": forward_key,
                                "nodeFrom": patient_node,
                                "nodeTo": feature_node,
                                "relation": "has_feature",
                                "patientRef": patient_ref,
                                "updatedAt": datetime.utcnow(),
                            }
                        },
                        upsert=True
                    )
                )
                write_ops.append(
                    UpdateOne(
                        {"edgeKey": reverse_key},
                        {
                            "$set": {
                                "edgeKey": reverse_key,
                                "nodeFrom": feature_node,
                                "nodeTo": patient_node,
                                "relation": "feature_of",
                                "patientRef": patient_ref,
                                "updatedAt": datetime.utcnow(),
                            }
                        },
                        upsert=True
                    )
                )

        if write_ops:
            db_pharma_feature_edges_collection.bulk_write(write_ops, ordered=False)

        return {
            "edgeDocumentsUpserted": len(write_ops),
            "collectionCount": db_pharma_feature_edges_collection.estimated_document_count(),
            "enabled": True,
        }


    def seed_default_trials(reload_existing=False):
        if reload_existing:
            db_pharma_trials_collection.delete_many({"trialId": {"$regex": r"^NCT-LHV-BC-"}})
        return {
            "seededTrials": 0,
            "collectionCount": db_pharma_trials_collection.estimated_document_count(),
            "deprecated": True,
        }

    def batch_embed_trial_documents(documents, batch_size=64):
        """Batch-embed matchText for a list of trial documents using voyage_embed_texts."""
        if not VOYAGE_API_KEY or not voyage_embed_texts:
            return documents

        texts = [doc.get("matchText") or "" for doc in documents]
        embedded_count = 0
        criteria_embedded_count = 0
        for start in range(0, len(texts), batch_size):
            batch_texts = texts[start:start + batch_size]
            batch_docs = documents[start:start + batch_size]
            try:
                embeddings = voyage_embed_texts(batch_texts, embedding_model=VOYAGE_EMBEDDING_MODEL, input_type="document")
                for idx, emb in enumerate(embeddings):
                    if isinstance(emb, list) and idx < len(batch_docs):
                        batch_docs[idx]["vectorEmbedding"] = emb
                        batch_docs[idx]["vectorEmbeddingModel"] = VOYAGE_EMBEDDING_MODEL
                        embedded_count += 1
            except Exception as batch_err:
                logging.warning(f"Batch trial embedding failed (offset {start}): {batch_err}")

            criteria_texts = []
            criteria_map = []
            for doc_idx, trial_doc in enumerate(batch_docs):
                rule_chunks = trial_doc.get("criteriaChunks") or []
                for chunk_idx, chunk_text in enumerate(rule_chunks[:4]):
                    normalized_chunk = str(chunk_text or "").strip()
                    if not normalized_chunk:
                        continue
                    criteria_texts.append(normalized_chunk)
                    criteria_map.append((doc_idx, chunk_idx))
            if criteria_texts:
                try:
                    criteria_embeddings = voyage_embed_texts(
                        criteria_texts,
                        embedding_model=VOYAGE_EMBEDDING_MODEL,
                        input_type="document",
                    )
                    for idx, embedding in enumerate(criteria_embeddings):
                        if not isinstance(embedding, list) or idx >= len(criteria_map):
                            continue
                        doc_idx, chunk_idx = criteria_map[idx]
                        trial_doc = batch_docs[doc_idx]
                        chunk_embeddings = trial_doc.get("criteriaChunkEmbeddings") or []
                        while len(chunk_embeddings) <= chunk_idx:
                            chunk_embeddings.append(None)
                        chunk_embeddings[chunk_idx] = embedding
                        trial_doc["criteriaChunkEmbeddings"] = chunk_embeddings
                        criteria_embedded_count += 1
                except Exception as chunk_err:
                    logging.warning(f"Criteria chunk embedding failed (offset {start}): {chunk_err}")

        logging.info(
            f"Batch embedded {embedded_count}/{len(documents)} trial docs and {criteria_embedded_count} criteria chunks"
        )
        return documents

    return {
        "build_default_pharma_trials": build_default_pharma_trials,
        "normalize_pharma_text": normalize_pharma_text,
        "parse_age_token": parse_age_token,
        "parse_age_years_token": parse_age_years_token,
        "parse_birthdate_to_age": parse_birthdate_to_age,
        "extract_codes_and_terms": extract_codes_and_terms,
        "extract_resource_event_datetime": extract_resource_event_datetime,
        "parse_birads_values_from_text": parse_birads_values_from_text,
        "build_source_hash": build_source_hash,
        "build_pharma_trial_rules": build_pharma_trial_rules,
        "build_trial_version_payload": build_trial_version_payload,
        "merge_trial_version_history": merge_trial_version_history,
        "build_rule_chunks": build_rule_chunks,
        "build_trial_prefilter_contract": build_trial_prefilter_contract,
        "resolve_trial_prefilter_contract": resolve_trial_prefilter_contract,
        "build_trial_definition_canonical": build_trial_definition_canonical,
        "resolve_trial_definition_canonical": resolve_trial_definition_canonical,
        "build_trial_query_for_patient_context": build_trial_query_for_patient_context,
        "evaluate_patient_context_against_trial_prefilter": evaluate_patient_context_against_trial_prefilter,
        "evaluate_profile_against_trial_prefilter": evaluate_profile_against_trial_prefilter,
        "normalize_ctgov_study": normalize_ctgov_study,
        "fetch_clinicaltrials_studies": fetch_clinicaltrials_studies,
        "build_leafy_patient_context": build_leafy_patient_context,
        "build_patient_evidence_profile_v2": build_patient_evidence_profile_v2,
        "build_patient_match_profile_v2": build_patient_match_profile_v2,
        "build_oncology_cohort_assessment_input": build_oncology_cohort_assessment_input,
        "build_breast_oncology_cohort_document": build_breast_oncology_cohort_document,
        "audit_trial_matching_dataset_readiness": audit_trial_matching_dataset_readiness,
        "audit_breast_oncology_cohort_quality": audit_breast_oncology_cohort_quality,
        "build_patient_curation_coverage_summary_document": build_patient_curation_coverage_summary_document,
        "build_patient_evidence_pack": build_patient_evidence_pack,
        "evaluate_trial_structured_match": evaluate_trial_structured_match,
        "build_trial_match_text": build_trial_match_text,
        "build_profile_query_for_trial": build_profile_query_for_trial,
        "build_profile_match_text": build_profile_match_text,
        "estimate_trial_coverage_with_synthea": estimate_trial_coverage_with_synthea,
        "infer_synthea_analog_patients": infer_synthea_analog_patients,
        "resolve_leafy_synthea_link": resolve_leafy_synthea_link,
        "normalize_authoritative_linkage_policy": normalize_authoritative_linkage_policy,
        "list_pending_leafy_synthea_linkages": list_pending_leafy_synthea_linkages,
        "promote_demo_screening_links": promote_demo_screening_links,
        "rebuild_demo_screened_patient_cohort": rebuild_demo_screened_patient_cohort,
        "backfill_embedded_screening_synthea_refs": backfill_embedded_screening_synthea_refs,
        "seed_missing_screening_synthea_refs": seed_missing_screening_synthea_refs,
        "upsert_synthetic_ecog_observations": upsert_synthetic_ecog_observations,
        "build_trial_matching_control_plane_summary": build_trial_matching_control_plane_summary,
        "list_trial_matching_evaluation_runs": list_trial_matching_evaluation_runs,
        "list_trial_matching_improvement_runs": list_trial_matching_improvement_runs,
        "run_trial_matching_control_plane_evaluation": run_trial_matching_control_plane_evaluation,
        "_normalize_patient_ref": _normalize_patient_ref,
        "is_authoritative_link_strategy": is_authoritative_link_strategy,
        "extract_embedded_synthea_link_strategy": extract_embedded_synthea_link_strategy,
        "extract_authoritative_embedded_synthea_patient_ref": extract_authoritative_embedded_synthea_patient_ref,
        "_load_synthea_patient_refs": _load_synthea_patient_refs,
        "_pick_deterministic_synthea_ref": _pick_deterministic_synthea_ref,
        "choose_screening_backfill_resolution": choose_screening_backfill_resolution,
        "_resolve_linked_synthea_patient_ref": _resolve_linked_synthea_patient_ref,
        "build_leafy_candidates_for_trial": build_leafy_candidates_for_trial,
        "ensure_pharma_indexes": ensure_pharma_indexes,
        "sync_synthea_resources_from_ndjson": sync_synthea_resources_from_ndjson,
        "load_synthea_resources_if_missing": load_synthea_resources_if_missing,
        "rebuild_pharma_trial_prefilter_contracts": rebuild_pharma_trial_prefilter_contracts,
        "rebuild_pharma_trial_canonical_definitions": rebuild_pharma_trial_canonical_definitions,
        "rebuild_pharma_patient_profiles": rebuild_pharma_patient_profiles,
        "rebuild_pharma_patient_facts": rebuild_pharma_patient_facts,
        "rebuild_breast_oncology_cohort": rebuild_breast_oncology_cohort,
        "rebuild_pharma_feature_edges": rebuild_pharma_feature_edges,
        "seed_default_trials": seed_default_trials,
        "batch_embed_trial_documents": batch_embed_trial_documents,
        "batch_embed_profile_documents": batch_embed_profile_documents,
    }
