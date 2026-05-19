from __future__ import annotations

import json
import os
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.wsgi import WSGIMiddleware

import runtime_state as runtime
from agent_router import router as agent_router
from app.routes.patients import router as patient_router
from compat_factory import create_compat_app


def _load_manifest() -> dict:
    with runtime.MANIFEST_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _compat_summary() -> dict:
    files = sorted(
        str(path.relative_to(runtime.FRONTEND_COMPAT_ROOT))
        for path in runtime.FRONTEND_COMPAT_ROOT.rglob("*")
        if path.is_file()
    )
    return {
        "compatRoot": str(runtime.FRONTEND_COMPAT_ROOT),
        "fileCount": len(files),
        "topLevelAreas": sorted(path.name for path in runtime.FRONTEND_COMPAT_ROOT.iterdir()),
        "sampleFiles": files[:25],
    }


def _connection_topology() -> dict:
    return {
        "clinicalTrials": {
            "databaseName": runtime.CLINICAL_TRIALS_DATABASE_NAME,
            "usesDedicatedUri": bool(
                runtime.CLINICAL_TRIALS_MONGO_URI
                and runtime.CLINICAL_TRIALS_MONGO_URI != runtime.LEAFY_HOSPITAL_MONGO_URI
            ),
        },
        "leafyHospital": {
            "databaseName": runtime.LEAFY_HOSPITAL_DATABASE_NAME,
            "usesDedicatedUri": bool(
                runtime.LEAFY_HOSPITAL_MONGO_URI
                and runtime.LEAFY_HOSPITAL_MONGO_URI != runtime.CLINICAL_TRIALS_MONGO_URI
            ),
        },
        "sharedMongoRuntime": bool(
            runtime.CLINICAL_TRIALS_MONGO_URI
            and runtime.CLINICAL_TRIALS_MONGO_URI == runtime.LEAFY_HOSPITAL_MONGO_URI
        ),
    }


def _build_env_status(*, include_compat: bool) -> dict:
    env_rows = []
    for item in runtime.ENV_SPEC:
        raw_value = os.getenv(item["name"])
        value = str(raw_value).strip() if raw_value is not None else ""
        fallback = item.get("default", "")
        preview = value or fallback
        if preview and any(token in item["name"] for token in {"KEY", "SECRET", "TOKEN", "URI"}):
            preview = f"{preview[:6]}..." if len(preview) > 6 else "***"
        env_rows.append(
            {
                **item,
                "configured": bool(value),
                "valuePreview": preview,
            }
        )
    clinical_trials_ready = bool(
        runtime.CLINICAL_TRIALS_MONGO_URI and runtime.CLINICAL_TRIALS_DATABASE_NAME
    )
    leafy_hospital_ready = bool(
        runtime.LEAFY_HOSPITAL_MONGO_URI and runtime.LEAFY_HOSPITAL_DATABASE_NAME
    )
    service_health = bool(
        runtime.appointments_collection is not None and runtime.clinical_reports_collection is not None
    )
    return {
        "startupReady": clinical_trials_ready and leafy_hospital_ready and service_health,
        "featureReadiness": {
            "mongo": clinical_trials_ready,
            "clinicalTrialsMongo": clinical_trials_ready,
            "leafyHospitalMongo": leafy_hospital_ready,
            "clinicalTrialsDatabase": runtime.clinical_trials_db is not None,
            "leafyHospitalDatabase": runtime.leafy_hospital_db is not None,
            "appointments": runtime.appointments_collection is not None,
            "clinicalReports": runtime.clinical_reports_collection is not None,
            "clinicalTrialsRouter": True,
            "pharmaBlueprint": include_compat,
            "syntheaMirror": runtime.synthea_resources_collection is not None,
            "voyageHelpers": bool(runtime.VOYAGE_API_KEY),
        },
        "connectionTopology": _connection_topology(),
        "env": env_rows,
    }


def _collection_counts() -> dict:
    if runtime.clinical_trials_db is None and runtime.leafy_hospital_db is None:
        return {}
    rows = {}
    for label, collection_obj in [
        ("appointments", runtime.appointments_collection),
        ("clinicalReports", runtime.clinical_reports_collection),
        ("pharmaTrials", runtime.pharma_trials_collection),
        ("pharmaProfiles", runtime.pharma_profiles_collection),
        ("pharmaFacts", runtime.pharma_patient_facts_collection),
        ("syntheaResources", runtime.synthea_resources_collection),
        ("clinicalTrialsServiceTrials", runtime.clinical_trials_service_collections.trials),
        ("clinicalTrialsServiceVersions", runtime.clinical_trials_service_collections.trial_versions),
        ("clinicalTrialsServiceCriteria", runtime.clinical_trials_service_collections.trial_criteria),
    ]:
        if collection_obj is None:
            rows[label] = None
            continue
        try:
            rows[label] = int(collection_obj.estimated_document_count())
        except Exception:
            rows[label] = None
    return rows


def create_app(*, include_compat: bool = True) -> FastAPI:
    app = FastAPI(title="Clinical Trial Matching Agent")
    app.state.service_materialization_status = {
        "ok": False,
        "reason": "not_started",
    }
    app.add_middleware(
        CORSMiddleware,
        allow_origins=runtime.ORIGINS or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(agent_router)
    app.include_router(runtime.clinical_trials_router)
    app.include_router(patient_router)

    @app.on_event("startup")
    def _startup() -> None:
        runtime.ensure_indexes_and_seed_rule_families()
        runtime.ensure_pharma_indexes()
        if runtime.CLINICAL_TRIALS_SERVICE_SYNC_ON_STARTUP:
            app.state.service_materialization_status = runtime.materialize_service_collections_from_pharma(
                runtime.pharma_trials_collection,
                max_trials=runtime.CLINICAL_TRIALS_SERVICE_BOOTSTRAP_LIMIT,
            )
        else:
            app.state.service_materialization_status = {
                "ok": False,
                "reason": "disabled_by_env",
            }

    @app.get("/")
    async def read_root():
        manifest = _load_manifest()
        return {
            "message": "Clinical Trial Matching Agent backend is running",
            "libraryId": manifest.get("id"),
            "displayName": manifest.get("displayName"),
            "connectionTopology": _connection_topology(),
            "collections": _collection_counts(),
            "clinicalTrialsServiceMaterialization": app.state.service_materialization_status,
        }

    @app.get("/api/v1/manifest")
    async def get_manifest():
        return _load_manifest()

    @app.get("/api/v1/extract/summary")
    async def get_extract_summary():
        files = sorted(
            str(path.relative_to(runtime.EXTRACT_ROOT))
            for path in runtime.EXTRACT_ROOT.rglob("*")
            if path.is_file()
        )
        return {
            "extractRoot": str(runtime.EXTRACT_ROOT),
            "fileCount": len(files),
            "sampleFiles": files[:20],
        }

    @app.get("/api/v1/setup/env-spec")
    async def get_env_spec():
        return {"variables": runtime.ENV_SPEC}

    @app.get("/api/v1/setup/status")
    async def get_setup_status():
        payload = _build_env_status(include_compat=include_compat)
        payload["collectionCounts"] = _collection_counts()
        payload["clinicalTrialsServiceMaterialization"] = app.state.service_materialization_status
        return payload

    @app.get("/api/v1/assets/compat-summary")
    async def get_compat_summary():
        return _compat_summary()

    @app.get("/appointments")
    async def list_appointments(
        q: str = "",
        currentCenter: str = "",
        currentUser: str = "",
        limit: int = Query(default=24, ge=1, le=100),
        sortField: str = "start",
        sortOrder: str = "desc",
    ):
        if runtime.appointments_collection is None:
            return {
                "appointments": {
                    "data": [],
                    "metadata": {"totalCount": 0, "error": "Appointments collection is not configured."},
                }
            }

        query: dict[str, Any] = {"resource.status": "fulfilled"}
        center_hint = str(currentCenter or "").strip()
        if center_hint:
            query["resource.location.display"] = center_hint

        search_text = str(q or "").strip()
        if search_text:
            regex = {"$regex": search_text, "$options": "i"}
            query["$or"] = [
                {"metadata.extraSearchParams.subject.patientName": regex},
                {"resource.subject.reference": regex},
                {"resource.location.display": regex},
                {"metadata.extraSearchParams.actor.actorName": regex},
                {"resource.description": regex},
            ]

        sort_path = {
            "start": "resource.start",
            "status": "resource.status",
            "created": "resource.created",
        }.get(str(sortField or "").strip(), "resource.start")
        sort_direction = -1 if str(sortOrder or "").strip().lower() == "desc" else 1

        cursor = (
            runtime.appointments_collection.find(query)
            .sort(sort_path, sort_direction)
            .limit(int(limit))
        )
        rows = [runtime.serialize_doc(row) for row in cursor]
        try:
            total_count = int(runtime.appointments_collection.count_documents(query))
        except Exception:
            total_count = len(rows)

        return {
            "appointments": {
                "data": rows,
                "metadata": {
                    "totalCount": total_count,
                    "limit": int(limit),
                    "sortField": sortField,
                    "sortOrder": sortOrder,
                    "currentCenter": center_hint,
                    "currentUser": str(currentUser or "").strip(),
                },
            }
        }

    if include_compat:
        app.mount("/", WSGIMiddleware(create_compat_app()))

    return app
