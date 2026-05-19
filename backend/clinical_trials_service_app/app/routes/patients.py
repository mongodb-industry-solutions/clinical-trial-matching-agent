from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services.patient_product import (
    PatientProductError,
    ScreenedPatientConflictError,
    ScreenedPatientNotFoundError,
    build_screened_patient_evidence,
    build_screened_patient_match_profile,
    get_mongo_storage_overview,
    get_patient_coverage_summary,
    get_patient_workspace_detail,
    get_patient_linkage,
    list_patient_catalog,
    select_random_patient_workspace,
    run_demo_screening_enrichment,
    select_random_screened_patient,
)


router = APIRouter()


def _raise_patient_error(exc: Exception) -> None:
    if isinstance(exc, ScreenedPatientConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, ScreenedPatientNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, PatientProductError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


@router.get("/api/v1/patients/screened/random")
def get_random_screened_patient() -> dict:
    try:
        payload = select_random_screened_patient()
        return {"success": True, **payload}
    except Exception as exc:
        _raise_patient_error(exc)


@router.get("/api/v1/patients/random")
def get_random_patient_workspace() -> dict:
    try:
        payload = select_random_patient_workspace()
        return {"success": True, **payload}
    except Exception as exc:
        _raise_patient_error(exc)


@router.get("/api/v1/patients/catalog")
def get_patient_catalog(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    focus: str = Query(default="breast_cancer"),
    search: str | None = Query(default=None),
    linked_only: bool = Query(default=False, alias="linkedOnly"),
    runnable_only: bool = Query(default=False, alias="runnableOnly"),
) -> dict:
    try:
        payload = list_patient_catalog(
            limit=limit,
            offset=offset,
            focus=focus,
            search=search,
            linked_only=linked_only,
            runnable_only=runnable_only,
        )
        return {"success": True, **payload}
    except Exception as exc:
        _raise_patient_error(exc)


@router.get("/api/v1/patients/{patient_ref:path}/evidence")
def get_patient_evidence(patient_ref: str, include_raw: bool = Query(default=False, alias="includeRaw")) -> dict:
    try:
        payload = build_screened_patient_evidence(patient_ref)
        response = {
            "success": True,
            "patientRef": payload["patientRef"],
            "patientEvidenceProfile": payload["patientEvidenceProfile"],
            "patientLink": payload["patientLink"],
            "screeningSummary": payload["screeningSummary"],
        }
        if include_raw:
            response["factsDocument"] = payload["factsDocument"]
        return response
    except Exception as exc:
        _raise_patient_error(exc)


@router.get("/api/v1/patients/{patient_ref:path}/match-profile")
def get_patient_match_profile(patient_ref: str, include_raw: bool = Query(default=False, alias="includeRaw")) -> dict:
    try:
        payload = build_screened_patient_match_profile(patient_ref)
        response = {
            "success": True,
            "patientRef": payload["patientRef"],
            "patientEvidenceProfile": payload["patientEvidenceProfile"],
            "patientMatchProfile": payload["patientMatchProfile"],
            "patientLink": payload["patientLink"],
            "screeningSummary": payload["screeningSummary"],
            "oncologyCohort": payload["oncologyCohort"],
        }
        if include_raw:
            response["factsDocument"] = payload["factsDocument"]
            response["profileDocument"] = payload["profileDocument"]
        return response
    except Exception as exc:
        _raise_patient_error(exc)


@router.get("/api/v1/patients/{patient_ref:path}/workspace")
def get_patient_workspace(patient_ref: str, include_raw: bool = Query(default=False, alias="includeRaw")) -> dict:
    try:
        payload = get_patient_workspace_detail(patient_ref)
        response = {
            "success": True,
            "patientRef": payload["patientRef"],
            "patientSummary": payload["patientSummary"],
            "patientEvidenceProfile": payload["patientEvidenceProfile"],
            "patientMatchProfile": payload["patientMatchProfile"],
            "screeningSummary": payload["screeningSummary"],
            "sourceCollections": payload["sourceCollections"],
        }
        if include_raw:
            response["factsDocument"] = payload["factsDocument"]
            response["profileDocument"] = payload["profileDocument"]
        return response
    except Exception as exc:
        _raise_patient_error(exc)


@router.get("/api/v1/patients/{patient_ref:path}/linkage")
def get_patient_linkage_status(patient_ref: str) -> dict:
    try:
        payload = get_patient_linkage(patient_ref)
        return {"success": True, **payload}
    except Exception as exc:
        _raise_patient_error(exc)


@router.get("/api/v1/patient-coverage")
def get_patient_coverage() -> dict:
    try:
        return {"success": True, "summary": get_patient_coverage_summary()}
    except Exception as exc:
        _raise_patient_error(exc)


@router.get("/api/v1/workbench/storage")
def get_workbench_storage() -> dict:
    try:
        return {"success": True, "storage": get_mongo_storage_overview()}
    except Exception as exc:
        _raise_patient_error(exc)


@router.post("/api/v1/patients/enrichment/demo-screened")
def enrich_demo_screened_patients(
    max_links: int = Query(default=250, ge=1, le=2000, alias="maxLinks"),
    dry_run: bool = Query(default=False, alias="dryRun"),
) -> dict:
    try:
        payload = run_demo_screening_enrichment(
            max_links=max_links,
            dry_run=dry_run,
        )
        return {"success": True, **payload}
    except Exception as exc:
        _raise_patient_error(exc)
