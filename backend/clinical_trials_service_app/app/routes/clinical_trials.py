from __future__ import annotations

import json
import os
import time

import requests
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.database import collections
from app.schemas import (
    BindingValidateRequest,
    BulkIngestRequest,
    IngestTrialRequest,
    PatientToTrialsMatchRequest,
    PlaygroundCompileRequest,
    RecompileFamilyRequest,
    RecompileTrialRequest,
    ReviewApplyRequest,
    RemoteSearchRequest,
    TerminologyResolveRequest,
    TrialToPatientsMatchRequest,
)
from app.services.ctgov_client import ClinicalTrialsGovClient, build_search_result_row
from app.services.helpers import serialize_document
from app.services.ingest_service import TERMINAL_RUN_STATUSES, ingest_service
from app.services.micro_benchmark_service import (
    load_latest_micro_benchmark_bundle,
    load_latest_micro_benchmark_review_sheet,
)
from app.services.matcher import MatchNotFoundError, MatchRuntimeGateError, matcher_service
from app.services.terminology import TerminologyProxyService
from clinical_trial_matching_agent.standalone.service_materializer import (
    materialize_service_collections_from_pharma,
)


router = APIRouter()
client = ClinicalTrialsGovClient()
terminology_service = TerminologyProxyService()


def _bootstrap_limit() -> int:
    try:
        return max(1, int(os.getenv("CLINICAL_TRIALS_SERVICE_BOOTSTRAP_LIMIT", "80") or "80"))
    except Exception:
        return 80


def _ensure_trials_bootstrapped() -> None:
    if collections.trials.estimated_document_count() > 0:
        return
    materialize_service_collections_from_pharma(max_trials=_bootstrap_limit())


def _ensure_trial_materialized(trial_id: str) -> None:
    target_id = str(trial_id or "").strip()
    if not target_id:
        return
    trial = collections.trials.find_one({"_id": target_id}, {"current_version_id": 1})
    if trial and collections.trial_criteria.count_documents(
        {"trial_id": target_id, "version_id": trial.get("current_version_id")},
        limit=1,
    ):
        return
    materialize_service_collections_from_pharma(trial_ids=[target_id], max_trials=1)


@router.get("/health")
def healthcheck() -> dict:
    return {
        "ok": True,
        "service": "clinical_trials_service",
    }


@router.get("/clinical-trials/health")
def clinical_trials_healthcheck() -> dict:
    return healthcheck()


@router.post("/clinical-trials/search")
def search_remote_trials(request: RemoteSearchRequest) -> dict:
    result = client.search_studies(
        condition=request.condition,
        phase=request.phase,
        sponsor=request.sponsor,
        nct_id=request.nct_id,
        page_size=request.page_size,
        max_pages=request.max_pages,
        statuses=request.statuses,
    )
    return {
        "success": True,
        "search_mode": result.get("searchMode"),
        "pages_fetched": result.get("pagesFetched"),
        "results": [build_search_result_row(study) for study in (result.get("studies") or [])],
    }


@router.post("/clinical-trials/ingest", status_code=202)
def ingest_trial(request: IngestTrialRequest) -> dict:
    result = ingest_service.start_ingest_run(request.model_dump())
    return {
        "success": True,
        **result,
    }


@router.post("/clinical-trials/ingest/bulk", status_code=202)
def bulk_ingest_trials(request: BulkIngestRequest) -> dict:
    result = ingest_service.start_bulk_ingest_run(request.model_dump())
    return {
        "success": True,
        **result,
    }


@router.post("/clinical-trials/playground/compile")
def compile_playground_rule(request: PlaygroundCompileRequest) -> dict:
    return {
        "success": True,
        "result": ingest_service.compile_playground_rule(request.model_dump()),
    }


@router.post("/clinical-trials/recompile/trial/{trial_id}", status_code=202)
def recompile_trial(trial_id: str, request: RecompileTrialRequest) -> dict:
    result = ingest_service.start_recompile_trial_run(trial_id, request.model_dump())
    return {
        "success": True,
        **result,
    }


@router.post("/clinical-trials/recompile/family/{family_id}", status_code=202)
def recompile_family(family_id: str, request: RecompileFamilyRequest) -> dict:
    result = ingest_service.start_recompile_family_run(family_id, request.model_dump())
    return {
        "success": True,
        **result,
    }


@router.get("/clinical-trials/runs")
def list_ingest_runs(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    return {
        "success": True,
        "runs": ingest_service.list_runs(limit=limit),
    }


@router.get("/clinical-trials/runs/{run_id}")
def get_ingest_run(run_id: str) -> dict:
    run = ingest_service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        "success": True,
        "run": run,
    }


def _run_event_stream(run_id: str):
    last_signature = None
    while True:
        run = collections.ingest_runs.find_one({"_id": run_id})
        if not run:
            yield "event: error\ndata: {\"message\": \"Run not found\"}\n\n"
            break
        serialized_run = serialize_document(run)
        signature = json.dumps(serialized_run, sort_keys=True)
        if signature != last_signature:
            yield f"event: run\ndata: {json.dumps(serialized_run)}\n\n"
            last_signature = signature
        if (serialized_run.get("status") or "") in TERMINAL_RUN_STATUSES:
            break
        time.sleep(1.0)


@router.get("/clinical-trials/runs/{run_id}/stream")
def stream_ingest_run(run_id: str) -> StreamingResponse:
    return StreamingResponse(_run_event_stream(run_id), media_type="text/event-stream")


@router.get("/clinical-trials/trials")
def list_trials(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    compile_status: str | None = None,
    phase: str | None = None,
    status: str | None = None,
    disease_area: str | None = None,
    search: str | None = None,
    runnable: bool | None = None,
    stage_target_window: str | None = None,
) -> dict:
    _ensure_trials_bootstrapped()
    trials = ingest_service.list_trials(
        limit=limit,
        offset=offset,
        compile_status=compile_status,
        phase=phase,
        overall_status=status,
        disease_area=disease_area,
        search=search,
        runnable=runnable,
        stage_target_window=stage_target_window,
    )
    return {
        "success": True,
        "trials": trials,
        "totalCount": ingest_service.count_trials(
            compile_status=compile_status,
            phase=phase,
            overall_status=status,
            disease_area=disease_area,
            search=search,
            runnable=runnable,
            stage_target_window=stage_target_window,
        ),
        "limit": limit,
        "offset": offset,
    }


@router.get("/clinical-trials/trials/{trial_id}")
def get_trial(trial_id: str) -> dict:
    _ensure_trial_materialized(trial_id)
    trial = ingest_service.get_trial(trial_id)
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    return {
        "success": True,
        "trial": trial,
    }


@router.get("/clinical-trials/trials/{trial_id}/criteria")
def get_trial_criteria(trial_id: str, version_id: str | None = None) -> dict:
    _ensure_trial_materialized(trial_id)
    return {
        "success": True,
        "criteria": ingest_service.get_trial_criteria(trial_id, version_id=version_id),
    }


@router.get("/clinical-trials/trials/{trial_id}/canonical-tree")
def get_trial_canonical_tree(trial_id: str, version_id: str | None = None) -> dict:
    _ensure_trial_materialized(trial_id)
    payload = ingest_service.get_trial_canonical_tree(trial_id, version_id=version_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Trial not found")
    return {
        "success": True,
        "canonical_tree": payload,
    }


@router.get("/clinical-trials/criteria")
def list_criteria(
    trial_id: str | None = None,
    version_id: str | None = None,
    parse_status: str | None = None,
    family_id: str | None = None,
    fingerprint: str | None = None,
    section: str | None = None,
    review_status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    return {
        "success": True,
        "criteria": ingest_service.list_criteria(
            trial_id=trial_id,
            version_id=version_id,
            parse_status=parse_status,
            family_id=family_id,
            fingerprint=fingerprint,
            section=section,
            review_status=review_status,
            limit=limit,
        ),
    }


@router.get("/clinical-trials/review-queue")
def review_queue(
    trial_id: str | None = None,
    family_id: str | None = None,
    fingerprint: str | None = None,
    section: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    return {
        "success": True,
        "criteria": ingest_service.get_review_queue(
            trial_id=trial_id,
            family_id=family_id,
            fingerprint=fingerprint,
            section=section,
            limit=limit,
        ),
    }


@router.get("/clinical-trials/rule-families")
def list_rule_families() -> dict:
    return {
        "success": True,
        "rule_families": ingest_service.list_rule_families(),
    }


@router.post("/clinical-trials/match/patient/{patient_ref:path}")
def match_patient_to_trials(patient_ref: str, request: PatientToTrialsMatchRequest) -> dict:
    payload = request.model_dump()
    requested_trial_ids = list(payload.get("trial_ids") or payload.get("trialIds") or [])
    if requested_trial_ids:
        materialize_service_collections_from_pharma(
            trial_ids=[str(item).strip() for item in requested_trial_ids if str(item).strip()],
            max_trials=max(1, len(requested_trial_ids)),
        )
    else:
        _ensure_trials_bootstrapped()
    try:
        result = matcher_service.match_patient_to_trials(patient_ref, payload)
    except MatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MatchRuntimeGateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "success": True,
        **result,
    }


@router.post("/clinical-trials/match/trial/{trial_id}")
def match_trial_to_patients(trial_id: str, request: TrialToPatientsMatchRequest) -> dict:
    _ensure_trial_materialized(trial_id)
    try:
        result = matcher_service.match_trial_to_patients(trial_id, request.model_dump())
    except MatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MatchRuntimeGateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "success": True,
        **result,
    }


@router.post("/clinical-trials/match/patient/{patient_ref:path}/stream")
def match_patient_to_trials_stream(patient_ref: str, request: PatientToTrialsMatchRequest) -> StreamingResponse:
    payload = request.model_dump()
    requested_trial_ids = list(payload.get("trial_ids") or payload.get("trialIds") or [])
    if requested_trial_ids:
        materialize_service_collections_from_pharma(
            trial_ids=[str(item).strip() for item in requested_trial_ids if str(item).strip()],
            max_trials=max(1, len(requested_trial_ids)),
        )
    else:
        _ensure_trials_bootstrapped()

    def generate():
        try:
            for event in matcher_service.match_patient_to_trials_stream(patient_ref, payload):
                yield f"data: {json.dumps(event)}\n\n"
        except MatchNotFoundError as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        except MatchRuntimeGateError as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/clinical-trials/match/trial/{trial_id}/stream")
def match_trial_to_patients_stream(trial_id: str, request: TrialToPatientsMatchRequest) -> StreamingResponse:
    _ensure_trial_materialized(trial_id)

    def generate():
        try:
            for event in matcher_service.match_trial_to_patients_stream(trial_id, request.model_dump()):
                yield f"data: {json.dumps(event)}\n\n"
        except MatchNotFoundError as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        except MatchRuntimeGateError as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/clinical-trials/match-runs")
def list_match_runs(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    return {
        "success": True,
        "runs": matcher_service.list_match_runs(limit=limit),
    }


@router.get("/clinical-trials/match-runs/{run_id}")
def get_match_run(run_id: str) -> dict:
    run = matcher_service.get_match_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Match run not found")
    return {
        "success": True,
        "run": run,
    }


@router.get("/clinical-trials/benchmarks/micro/latest")
def get_latest_micro_benchmark() -> dict:
    try:
        bundle = load_latest_micro_benchmark_bundle()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Benchmark artifact not found: {exc}") from exc
    return {
        "success": True,
        "benchmark": bundle,
    }


@router.get("/clinical-trials/benchmarks/micro/latest/review-sheet", response_class=PlainTextResponse)
def get_latest_micro_benchmark_review_sheet() -> PlainTextResponse:
    try:
        sheet = load_latest_micro_benchmark_review_sheet()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Benchmark review sheet not found: {exc}") from exc
    return PlainTextResponse(content=sheet, media_type="text/csv")


@router.get("/clinical-trials/analytics/unresolved-top")
def unresolved_top(limit: int = Query(default=25, ge=1, le=100)) -> dict:
    return {
        "success": True,
        "rows": ingest_service.get_unresolved_top(limit=limit),
    }


@router.get("/clinical-trials/analytics/family-coverage")
def family_coverage() -> dict:
    return {
        "success": True,
        "rows": ingest_service.get_family_coverage(),
    }


@router.post("/clinical-trials/reviews/apply", status_code=202)
def apply_review(request: ReviewApplyRequest) -> dict:
    result = ingest_service.start_review_apply_run(request.model_dump())
    return {
        "success": True,
        **result,
    }


@router.get("/clinical-trials/terminology/status")
def terminology_status() -> dict:
    return {
        "success": True,
        "status": terminology_service.get_status(),
    }


@router.post("/clinical-trials/terminology/snomed/resolve")
def resolve_snomed_terms(request: TerminologyResolveRequest) -> dict:
    try:
        result = terminology_service.resolve_snomed(request.model_dump())
        return {"success": True, "result": result}
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/clinical-trials/terminology/bindings/{binding_id}/validate")
def validate_binding(binding_id: str, request: BindingValidateRequest) -> dict:
    try:
        result = terminology_service.validate_binding(binding_id, request.model_dump())
        return {"success": True, "result": result}
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
