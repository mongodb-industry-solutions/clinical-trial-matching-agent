from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from dateutil import parser as date_parser
from flask import Flask

import runtime_state as runtime
from clinical_trial_matching_agent.leafy_extract.routes.pharma_blueprint import (  # noqa: E402
    create_pharma_blueprint,
)


def _normalize_patient_ref_local(patient_ref_value: Any) -> str:
    raw = str(patient_ref_value or "").strip()
    if not raw:
        return ""
    if raw.startswith("Patient/"):
        return raw
    return f"Patient/{raw}"


def _extract_code_label(code_obj: Any) -> str:
    if isinstance(code_obj, list):
        for item in code_obj:
            label = _extract_code_label(item)
            if label:
                return label
        return ""
    if not isinstance(code_obj, dict):
        return ""
    text_value = str(code_obj.get("text") or "").strip()
    if text_value:
        return text_value
    coding = code_obj.get("coding") or []
    if coding and isinstance(coding[0], dict):
        return str(coding[0].get("display") or coding[0].get("code") or "").strip()
    return ""


def _extract_resource_event_date(resource: dict[str, Any] | None) -> datetime | None:
    if not isinstance(resource, dict):
        return None
    candidate_values = [
        resource.get("effectiveDateTime"),
        resource.get("issued"),
        resource.get("recordedDate"),
        resource.get("authoredOn"),
        resource.get("onsetDateTime"),
        resource.get("occurrenceDateTime"),
        resource.get("performedDateTime"),
        resource.get("date"),
        ((resource.get("period") or {}).get("start") if isinstance(resource.get("period"), dict) else None),
        (
            (resource.get("performedPeriod") or {}).get("start")
            if isinstance(resource.get("performedPeriod"), dict)
            else None
        ),
        ((resource.get("meta") or {}).get("lastUpdated") if isinstance(resource.get("meta"), dict) else None),
    ]
    for value in candidate_values:
        if not value:
            continue
        try:
            parsed = date_parser.parse(str(value))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except Exception:
            continue
    return None


def _extract_observation_value(resource: dict[str, Any] | None) -> str:
    if not isinstance(resource, dict):
        return ""
    quantity = resource.get("valueQuantity")
    if isinstance(quantity, dict):
        quantity_value = quantity.get("value")
        quantity_unit = quantity.get("unit") or quantity.get("code") or ""
        if quantity_value is not None:
            return f"{quantity_value} {quantity_unit}".strip()
    if resource.get("valueString"):
        return str(resource.get("valueString"))
    if resource.get("valueInteger") is not None:
        return str(resource.get("valueInteger"))
    if resource.get("valueBoolean") is not None:
        return str(resource.get("valueBoolean"))
    if isinstance(resource.get("valueCodeableConcept"), dict):
        return _extract_code_label(resource.get("valueCodeableConcept"))
    components = resource.get("component") or []
    rendered_components = []
    for component in components[:3]:
        if not isinstance(component, dict):
            continue
        component_name = _extract_code_label(component.get("code"))
        component_value = _extract_observation_value(component)
        if component_name and component_value:
            rendered_components.append(f"{component_name}: {component_value}")
    return " | ".join(rendered_components)


def _build_synthea_emr_visualization(
    patient_ref: str,
    resources: list[dict[str, Any]],
    timeline_limit: int = 40,
) -> dict:
    normalized_ref = _normalize_patient_ref_local(patient_ref)
    patient_resource = None
    type_counts: dict[str, int] = defaultdict(int)
    timeline = []
    medications = []
    labs = []
    conditions = []
    encounters = []
    procedures = []

    for resource in resources:
        if not isinstance(resource, dict):
            continue
        resource_type = str(resource.get("resourceType") or "Unknown").strip() or "Unknown"
        type_counts[resource_type] += 1
        if resource_type == "Patient":
            patient_resource = resource
            continue

        event_dt = _extract_resource_event_date(resource)
        event_date = event_dt.isoformat() if event_dt else None

        if resource_type == "MedicationRequest":
            medication_name = _extract_code_label(resource.get("medicationCodeableConcept")) or "Medication"
            medications.append(
                {
                    "id": resource.get("id"),
                    "name": medication_name,
                    "status": resource.get("status"),
                    "intent": resource.get("intent"),
                    "authoredOn": resource.get("authoredOn"),
                }
            )
            timeline.append(
                {
                    "resourceType": resource_type,
                    "date": event_date,
                    "title": medication_name,
                    "status": resource.get("status"),
                    "reference": f"MedicationRequest/{resource.get('id')}" if resource.get("id") else None,
                }
            )
            continue

        if resource_type == "Observation":
            observation_name = _extract_code_label(resource.get("code")) or "Observation"
            observation_value = _extract_observation_value(resource)
            if observation_value:
                labs.append(
                    {
                        "id": resource.get("id"),
                        "name": observation_name,
                        "value": observation_value,
                        "status": resource.get("status"),
                        "effectiveDateTime": resource.get("effectiveDateTime") or event_date,
                    }
                )
            timeline.append(
                {
                    "resourceType": resource_type,
                    "date": event_date,
                    "title": observation_name,
                    "value": observation_value,
                    "status": resource.get("status"),
                    "reference": f"Observation/{resource.get('id')}" if resource.get("id") else None,
                }
            )
            continue

        if resource_type == "Condition":
            condition_name = _extract_code_label(resource.get("code")) or "Condition"
            conditions.append(
                {
                    "id": resource.get("id"),
                    "name": condition_name,
                    "clinicalStatus": _extract_code_label(resource.get("clinicalStatus")),
                    "verificationStatus": _extract_code_label(resource.get("verificationStatus")),
                    "onsetDateTime": resource.get("onsetDateTime"),
                }
            )
            timeline.append(
                {
                    "resourceType": resource_type,
                    "date": event_date,
                    "title": condition_name,
                    "status": _extract_code_label(resource.get("clinicalStatus")),
                    "reference": f"Condition/{resource.get('id')}" if resource.get("id") else None,
                }
            )
            continue

        if resource_type == "Encounter":
            encounter_name = (
                _extract_code_label(resource.get("type"))
                or _extract_code_label(resource.get("serviceType"))
                or "Encounter"
            )
            encounters.append(
                {
                    "id": resource.get("id"),
                    "name": encounter_name,
                    "status": resource.get("status"),
                    "class": (resource.get("class") or {}).get("code") if isinstance(resource.get("class"), dict) else None,
                    "start": (resource.get("period") or {}).get("start") if isinstance(resource.get("period"), dict) else None,
                    "end": (resource.get("period") or {}).get("end") if isinstance(resource.get("period"), dict) else None,
                }
            )
            timeline.append(
                {
                    "resourceType": resource_type,
                    "date": event_date,
                    "title": encounter_name,
                    "status": resource.get("status"),
                    "reference": f"Encounter/{resource.get('id')}" if resource.get("id") else None,
                }
            )
            continue

        if resource_type == "Procedure":
            procedure_name = _extract_code_label(resource.get("code")) or "Procedure"
            procedures.append(
                {
                    "id": resource.get("id"),
                    "name": procedure_name,
                    "status": resource.get("status"),
                    "performedDateTime": resource.get("performedDateTime")
                    or ((resource.get("performedPeriod") or {}).get("start") if isinstance(resource.get("performedPeriod"), dict) else None),
                }
            )
            timeline.append(
                {
                    "resourceType": resource_type,
                    "date": event_date,
                    "title": procedure_name,
                    "status": resource.get("status"),
                    "reference": f"Procedure/{resource.get('id')}" if resource.get("id") else None,
                }
            )
            continue

        timeline.append(
            {
                "resourceType": resource_type,
                "date": event_date,
                "title": _extract_code_label(resource.get("code")) or resource_type,
                "status": resource.get("status"),
                "reference": f"{resource_type}/{resource.get('id')}" if resource.get("id") else None,
            }
        )

    timeline_sorted = sorted(
        timeline,
        key=lambda item: _extract_resource_event_date({"effectiveDateTime": item.get("date")}) or datetime.min,
        reverse=True,
    )[: max(10, min(int(timeline_limit or 40), 120))]

    medications_sorted = sorted(
        medications,
        key=lambda item: _extract_resource_event_date({"effectiveDateTime": item.get("authoredOn")}) or datetime.min,
        reverse=True,
    )
    labs_sorted = sorted(
        labs,
        key=lambda item: _extract_resource_event_date({"effectiveDateTime": item.get("effectiveDateTime")}) or datetime.min,
        reverse=True,
    )

    patient_name = ""
    patient_gender = ""
    patient_birth_date = ""
    if isinstance(patient_resource, dict):
        names = patient_resource.get("name") or []
        if names and isinstance(names[0], dict):
            given = " ".join(names[0].get("given") or [])
            family = names[0].get("family") or ""
            patient_name = f"{given} {family}".strip()
        patient_gender = str(patient_resource.get("gender") or "").strip()
        patient_birth_date = str(patient_resource.get("birthDate") or "").strip()

    return {
        "patient": {
            "patientRef": normalized_ref,
            "name": patient_name or normalized_ref,
            "gender": patient_gender,
            "birthDate": patient_birth_date,
        },
        "summary": {
            "totalResources": len(resources),
            "resourceTypeCounts": dict(sorted(type_counts.items(), key=lambda item: item[0])),
            "timelineEvents": len(timeline),
            "medications": len(medications),
            "labs": len(labs),
            "conditions": len(conditions),
            "encounters": len(encounters),
            "procedures": len(procedures),
        },
        "timeline": timeline_sorted,
        "domains": {
            "medications": medications_sorted[:20],
            "labs": labs_sorted[:20],
            "conditions": conditions[:20],
            "encounters": encounters[:20],
            "procedures": procedures[:20],
        },
    }


def _invoke_agent_tool(name: str, args: dict[str, Any] | None = None):
    start = time.perf_counter()
    payload = args if isinstance(args, dict) else {}
    trace = {"tool": name, "ok": False}
    try:
        if name != "synthea_patient_emr":
            raise ValueError(f"Unsupported standalone tool: {name}")

        patient_ref = _normalize_patient_ref_local(payload.get("patientRef"))
        if not patient_ref:
            result = {
                "patientRef": "",
                "summary": {"totalResources": 0, "resourceTypeCounts": {}},
                "resources": [],
                "timeline": [],
                "domains": {},
                "patient": {},
            }
        else:
            patient_id = patient_ref.split("/")[-1]
            limit_value = max(10, min(runtime.parse_int_value(payload.get("limit"), 120, 1), 500))
            query = {
                "$or": [
                    {"resource.resourceType": "Patient", "resource.id": patient_id},
                    {"resource.subject.reference": patient_ref},
                    {"resource.patient.reference": patient_ref},
                    {"resource.beneficiary.reference": patient_ref},
                ]
            }
            projection = {"_id": 0, "resource": 1}
            cursor = runtime.synthea_resources_collection.find(query, projection).limit(5000)

            all_resources = []
            type_counts: dict[str, int] = defaultdict(int)
            for row in cursor:
                resource = runtime.serialize_doc((row or {}).get("resource") or {})
                resource_type = str(resource.get("resourceType") or "Unknown")
                type_counts[resource_type] += 1
                all_resources.append(resource)

            patient_resource = None
            non_patient_resources = []
            for resource in all_resources:
                if str(resource.get("resourceType") or "") == "Patient" and patient_resource is None:
                    patient_resource = resource
                    continue
                non_patient_resources.append(resource)

            non_patient_resources_sorted = sorted(
                non_patient_resources,
                key=lambda item: _extract_resource_event_date(item) or datetime.min,
                reverse=True,
            )

            selected_resources = []
            selected_ids = set()
            important_types = [
                "Observation",
                "MedicationRequest",
                "Condition",
                "Encounter",
                "Procedure",
                "DiagnosticReport",
            ]
            per_type_quota = 4

            for resource_type in important_types:
                taken = 0
                for resource in non_patient_resources_sorted:
                    if str(resource.get("resourceType") or "") != resource_type:
                        continue
                    resource_id = f"{resource_type}/{resource.get('id') or id(resource)}"
                    if resource_id in selected_ids:
                        continue
                    selected_ids.add(resource_id)
                    selected_resources.append(resource)
                    taken += 1
                    if taken >= per_type_quota:
                        break

            for resource in non_patient_resources_sorted:
                if len(selected_resources) >= max(0, limit_value - (1 if patient_resource else 0)):
                    break
                resource_type = str(resource.get("resourceType") or "Unknown")
                resource_id = f"{resource_type}/{resource.get('id') or id(resource)}"
                if resource_id in selected_ids:
                    continue
                selected_ids.add(resource_id)
                selected_resources.append(resource)

            resources = ([patient_resource] if patient_resource else []) + selected_resources
            visualization = _build_synthea_emr_visualization(patient_ref, resources, timeline_limit=60)
            summary = visualization.get("summary") or {}
            summary["resourceTypeCounts"] = dict(sorted(type_counts.items(), key=lambda item: item[0]))
            summary["limit"] = limit_value
            summary["linkedPatientRef"] = patient_ref
            summary["totalMatchedResources"] = len(all_resources)
            summary["selectedResources"] = len(resources)
            result = {
                "patientRef": patient_ref,
                "summary": summary,
                "resources": resources,
                "timeline": visualization.get("timeline") or [],
                "domains": visualization.get("domains") or {},
                "patient": visualization.get("patient") or {},
            }

        trace["ok"] = True
        return result, trace | {"durationMs": round((time.perf_counter() - start) * 1000.0, 2)}
    except Exception as exc:
        runtime.LOGGER.exception("Standalone tool %s failed", name)
        trace["error"] = str(exc)
        return {}, trace | {"durationMs": round((time.perf_counter() - start) * 1000.0, 2)}


def create_compat_app() -> Flask:
    app = Flask("clinical_trial_matching_agent_pharma")
    app.register_blueprint(
        create_pharma_blueprint(
            deps={
                "parse_bool_value_fn": runtime.parse_bool_value,
                "parse_int_value_fn": runtime.parse_int_value,
                "ensure_pharma_indexes_fn": runtime.ensure_pharma_indexes,
                "audit_trial_matching_dataset_readiness_fn": runtime.audit_trial_matching_dataset_readiness,
                "audit_breast_oncology_cohort_quality_fn": runtime.audit_breast_oncology_cohort_quality,
                "build_patient_curation_coverage_summary_fn": runtime.build_patient_curation_coverage_summary_document,
                "load_synthea_resources_if_missing_fn": runtime.load_synthea_resources_if_missing,
                "rebuild_pharma_patient_profiles_fn": runtime.rebuild_pharma_patient_profiles,
                "rebuild_pharma_patient_facts_fn": runtime.rebuild_pharma_patient_facts,
                "rebuild_breast_oncology_cohort_fn": runtime.rebuild_breast_oncology_cohort,
                "rebuild_pharma_feature_edges_fn": runtime.rebuild_pharma_feature_edges,
                "synthea_resources_ndjson_path": runtime.SYNTHEA_RESOURCES_NDJSON_PATH,
                "db_pharma_trials_collection": runtime.pharma_trials_collection,
                "serialize_doc_fn": runtime.serialize_doc,
                "logger": logging,
                "clinicaltrials_api_base": runtime.CLINICALTRIALS_API_BASE,
                "clinicaltrials_timeout_sec": runtime.CLINICALTRIALS_TIMEOUT_SEC,
                "clinicaltrials_default_condition": runtime.CLINICALTRIALS_DEFAULT_CONDITION,
                "clinicaltrials_sync_days_back": runtime.CLINICALTRIALS_SYNC_DAYS_BACK,
                "active_trial_statuses": runtime.ACTIVE_TRIAL_STATUSES,
                "active_trial_statuses_all": runtime.ACTIVE_TRIAL_STATUSES_ALL,
                "db_clinical_reports_collection": runtime.clinical_reports_collection,
                "fetch_clinicaltrials_studies_fn": runtime.fetch_clinicaltrials_studies,
                "normalize_ctgov_study_fn": runtime.normalize_ctgov_study,
                "build_trial_prefilter_contract_fn": runtime.build_trial_prefilter_contract,
                "resolve_trial_prefilter_contract_fn": runtime.resolve_trial_prefilter_contract,
                "resolve_trial_definition_canonical_fn": runtime.resolve_trial_definition_canonical,
                "build_trial_query_for_patient_context_fn": runtime.build_trial_query_for_patient_context,
                "evaluate_patient_context_against_trial_prefilter_fn": runtime.evaluate_patient_context_against_trial_prefilter,
                "evaluate_profile_against_trial_prefilter_fn": runtime.evaluate_profile_against_trial_prefilter,
                "build_leafy_patient_context_fn": runtime.build_leafy_patient_context,
                "build_patient_evidence_profile_fn": runtime.build_patient_evidence_profile_v2,
                "build_patient_match_profile_fn": runtime.build_patient_match_profile_v2,
                "build_oncology_cohort_assessment_input_fn": runtime.build_oncology_cohort_assessment_input,
                "build_breast_oncology_cohort_document_fn": runtime.build_breast_oncology_cohort_document,
                "infer_synthea_analog_patients_fn": runtime.infer_synthea_analog_patients,
                "resolve_leafy_synthea_link_fn": runtime.resolve_leafy_synthea_link,
                "list_pending_leafy_synthea_linkages_fn": runtime.list_pending_leafy_synthea_linkages,
                "build_trial_matching_control_plane_summary_fn": runtime.build_trial_matching_control_plane_summary,
                "list_trial_matching_evaluation_runs_fn": runtime.list_trial_matching_evaluation_runs,
                "list_trial_matching_improvement_runs_fn": runtime.list_trial_matching_improvement_runs,
                "run_trial_matching_control_plane_evaluation_fn": runtime.run_trial_matching_control_plane_evaluation,
                "resolve_linked_synthea_patient_ref_fn": runtime.resolve_linked_synthea_patient_ref,
                "build_patient_evidence_pack_fn": runtime.build_patient_evidence_pack,
                "merge_trial_version_history_fn": runtime.merge_trial_version_history,
                "invoke_agent_tool_fn": _invoke_agent_tool,
                "db_synthea_resources_collection": runtime.synthea_resources_collection,
                "db_pharma_patient_facts_collection": runtime.pharma_patient_facts_collection,
                "db_leafy_synthea_links_collection": runtime.leafy_synthea_links_collection,
                "synthea_fhir_db": runtime.SYNTHEA_FHIR_DB,
                "synthea_resources_collection": runtime.SYNTHEA_RESOURCES_COLLECTION,
                "voyage_api_key": runtime.VOYAGE_API_KEY,
                "voyage_embedding_model": runtime.VOYAGE_EMBEDDING_MODEL,
                "voyage_rerank_model": runtime.VOYAGE_RERANK_MODEL,
                "voyage_embed_text_fn": runtime.voyage_embed_text,
                "voyage_embed_texts_fn": runtime.voyage_embed_texts,
                "voyage_rerank_results_fn": None,
                "voyage_pharma_vector_index_name": runtime.VOYAGE_PHARMA_VECTOR_INDEX_NAME,
                "voyage_pharma_patient_vector_index_name": runtime.VOYAGE_PHARMA_PATIENT_VECTOR_INDEX_NAME,
                "batch_embed_trial_documents_fn": runtime.batch_embed_trial_documents,
                "batch_embed_profile_documents_fn": runtime.batch_embed_profile_documents,
                "evaluate_trial_structured_match_fn": runtime.evaluate_trial_structured_match,
                "build_trial_match_text_fn": runtime.build_trial_match_text,
                "build_profile_match_text_fn": runtime.build_profile_match_text,
                "safe_float_fn": runtime.safe_float,
                "estimate_trial_coverage_with_synthea_fn": runtime.estimate_trial_coverage_with_synthea,
                "build_profile_query_for_trial_fn": runtime.build_profile_query_for_trial,
                "db_pharma_profiles_collection": runtime.pharma_profiles_collection,
                "normalize_pharma_text_fn": runtime.normalize_pharma_text,
                "db_pharma_feature_edges_collection": runtime.pharma_feature_edges_collection,
                "pharma_feature_edges_collection_name": runtime.PHARMA_FEATURE_EDGES_COLLECTION,
                "pharma_oncology_cohort_collection_name": runtime.PHARMA_ONCOLOGY_COHORT_COLLECTION,
                "bedrock_default_model": runtime.BEDROCK_DEFAULT_MODEL,
            }
        )
    )
    return app
