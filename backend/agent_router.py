from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

import runtime_state as runtime
from clinical_trial_matching_agent.leafy_extract.routes.pharma_blueprint import (
    run_patient_trial_match_pipeline,
)


router = APIRouter()


class ClinicalTrialAgentInvokeRequest(BaseModel):
    question: str | None = None
    appointment_id: str | None = Field(default=None, alias="appointmentId")
    patient_id: str | None = Field(default=None, alias="patientId")
    top_k: int = Field(default=5, ge=1, le=20, alias="topK")
    rerank_enabled: bool = Field(default=True, alias="rerankEnabled")
    patient_context: dict[str, Any] | None = Field(default=None, alias="patientContext")
    trial_filters: dict[str, Any] | None = Field(default=None, alias="trialFilters")

    model_config = {
        "populate_by_name": True,
    }


def _build_agent_status(*, ready: bool, severity: str, title: str, message: str, details: dict | None = None) -> dict:
    payload = {
        "state": "live" if ready else "error",
        "availability": "available" if ready else "unavailable",
        "severity": severity,
        "title": title,
        "message": message,
        "mode": "external",
        "liveAgentUsed": ready,
        "fallbackUsed": False,
    }
    if isinstance(details, dict) and details:
        payload["details"] = details
    return payload


def _feature_readiness() -> dict[str, bool]:
    return {
        "mongo": runtime.clinical_trials_db is not None,
        "clinicalTrialsMongo": runtime.clinical_trials_db is not None,
        "leafyHospitalMongo": runtime.leafy_hospital_db is not None,
        "appointments": runtime.appointments_collection is not None,
        "clinicalReports": runtime.clinical_reports_collection is not None,
        "pharmaTrials": runtime.pharma_trials_collection is not None,
        "pharmaProfiles": runtime.pharma_profiles_collection is not None,
        "pharmaFacts": runtime.pharma_patient_facts_collection is not None,
        "leafySyntheaLinks": runtime.leafy_synthea_links_collection is not None,
        "syntheaResources": runtime.synthea_resources_collection is not None,
    }


@router.get("/api/v1/agents/clinical-trial-matching/health")
def clinical_trial_matching_agent_health() -> dict:
    feature_readiness = _feature_readiness()
    ready = all(
        feature_readiness.get(key)
        for key in [
            "clinicalTrialsMongo",
            "leafyHospitalMongo",
            "appointments",
            "clinicalReports",
            "pharmaTrials",
            "pharmaProfiles",
            "pharmaFacts",
        ]
    )
    message = (
        "Standalone clinical-trial matching agent is ready for Leafy control-plane requests."
        if ready
        else "Standalone clinical-trial matching agent is missing one or more required data dependencies."
    )
    return {
        "ok": ready,
        "service": "clinical_trial_matching_agent",
        "agentStatus": _build_agent_status(
            ready=ready,
            severity="info" if ready else "error",
            title="Clinical trial matching agent available" if ready else "Clinical trial matching agent unavailable",
            message=message,
            details={"featureReadiness": feature_readiness},
        ),
        "featureReadiness": feature_readiness,
        "datasetReadiness": runtime.TRIAL_MATCHING_DATASET_READINESS,
    }


@router.post("/api/v1/agents/clinical-trial-matching/invoke")
def invoke_clinical_trial_matching_agent(request: ClinicalTrialAgentInvokeRequest) -> dict:
    payload = request.model_dump(by_alias=False)
    patient_context = payload.get("patient_context") or {}
    if not isinstance(patient_context, dict):
        patient_context = {}
    appointment_id = str(payload.get("appointment_id") or patient_context.get("appointmentId") or "").strip()
    patient_id = str(payload.get("patient_id") or patient_context.get("patientId") or "").strip()
    question = str(payload.get("question") or "").strip()
    top_k = int(payload.get("top_k") or 5)
    rerank_enabled = bool(payload.get("rerank_enabled", True))
    trial_filters = payload.get("trial_filters") if isinstance(payload.get("trial_filters"), dict) else {}

    result = run_patient_trial_match_pipeline(
        question=question,
        patient_context=patient_context,
        appointment_id=appointment_id,
        patient_id=patient_id or None,
        top_k=top_k,
        rerank_enabled=rerank_enabled,
        trial_filters=trial_filters,
        build_leafy_patient_context_fn=runtime.build_leafy_patient_context,
        infer_synthea_analog_patients_fn=runtime.infer_synthea_analog_patients,
        resolve_linked_synthea_patient_ref_fn=runtime.resolve_linked_synthea_patient_ref,
        build_patient_evidence_pack_fn=runtime.build_patient_evidence_pack,
        build_trial_query_for_patient_context_fn=runtime.build_trial_query_for_patient_context,
        evaluate_patient_context_against_trial_prefilter_fn=runtime.evaluate_patient_context_against_trial_prefilter,
        resolve_trial_prefilter_contract_fn=runtime.resolve_trial_prefilter_contract,
        resolve_trial_definition_canonical_fn=runtime.resolve_trial_definition_canonical,
        evaluate_trial_structured_match_fn=runtime.evaluate_trial_structured_match,
        build_trial_match_text_fn=runtime.build_trial_match_text,
        estimate_trial_coverage_with_synthea_fn=runtime.estimate_trial_coverage_with_synthea,
        db_pharma_trials_collection=runtime.pharma_trials_collection,
        active_trial_statuses_all=runtime.ACTIVE_TRIAL_STATUSES_ALL,
        voyage_api_key=runtime.VOYAGE_API_KEY,
        voyage_embed_text_fn=runtime.voyage_embed_text,
        voyage_embedding_model=runtime.VOYAGE_EMBEDDING_MODEL,
        voyage_rerank_results_fn=runtime.voyage_rerank_results,
        voyage_rerank_model=runtime.VOYAGE_RERANK_MODEL,
        voyage_pharma_vector_index_name=runtime.VOYAGE_PHARMA_VECTOR_INDEX_NAME,
        serialize_doc_fn=runtime.serialize_doc,
        safe_float_fn=runtime.safe_float,
        normalize_pharma_text_fn=runtime.normalize_pharma_text,
        logger=runtime.LOGGER,
    )
    agent_status = _build_agent_status(
        ready=True,
        severity="info",
        title="External clinical-trial matching agent response",
        message="The standalone clinical-trial matching agent evaluated this patient against the external trial catalog.",
    )
    pipeline_visibility = dict(result.get("pipelineVisibility") or {})
    pipeline_visibility["externalAgent"] = {
        "agentId": "clinical-trial-matching-agent",
        "baseUrl": "/api/v1/agents/clinical-trial-matching/invoke",
        "status": agent_status,
    }
    return {
        "success": True,
        **result,
        "pipelineVisibility": pipeline_visibility,
        "agentStatus": agent_status,
        "agentInvocation": {
            "mode": "external",
            "agentId": "clinical-trial-matching-agent",
            "appointmentId": appointment_id,
            "patientId": patient_id,
            "topK": top_k,
        },
    }
