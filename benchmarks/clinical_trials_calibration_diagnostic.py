from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from clinical_trials_query_profiles import (
    DEFAULT_QUERY_PROFILE,
    available_query_profiles,
    resolve_query_profile,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
WEBSERVICES_DIR = REPO_ROOT / "externals" / "webservices"
CLINICAL_TRIALS_SERVICE_DIR = REPO_ROOT / "externals" / "clinical_trials_service"
OUTPUT_DIR = REPO_ROOT / "externals" / "benchmarks" / "output" / "clinical_trials_calibration_diagnostic"
RUNTIME_TAG = "clinical-trials-canonical-runtime"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the clinical-trials calibration diagnostic pass: rebuild curated patients, refresh the breast-cancer trial corpus, and write patient authoritative-gate and trial runtime blocker diagnostics.",
    )
    parser.add_argument("--query-profile", choices=available_query_profiles(), default=DEFAULT_QUERY_PROFILE)
    parser.add_argument("--condition", default=None)
    parser.add_argument("--phase", default=None)
    parser.add_argument("--sponsor", default=None)
    parser.add_argument("--page-size", type=int, default=None)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--statuses", nargs="*", default=None)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    return parser.parse_args()


def _load_webservices_app():
    sys.path.insert(0, str(WEBSERVICES_DIR))
    load_dotenv(WEBSERVICES_DIR / ".env")
    os.environ.setdefault("TRIAL_MATCHING_DATASET_SKIP_STARTUP_AUDIT", "true")
    os.environ["VOYAGE_API_KEY"] = ""
    module_path = WEBSERVICES_DIR / "app.py"
    spec = importlib.util.spec_from_file_location("leafy_webservices_app", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load webservices app from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["leafy_webservices_app"] = module
    spec.loader.exec_module(module)
    return module


def _load_clinical_trials_modules():
    sys.path.insert(0, str(CLINICAL_TRIALS_SERVICE_DIR))
    load_dotenv(CLINICAL_TRIALS_SERVICE_DIR / "app" / ".env")
    from app.database import ensure_indexes_and_seed_rule_families
    from app.services.ingest_service import ingest_service
    from app.services.proof_service import proof_service

    return ensure_indexes_and_seed_rule_families, ingest_service, proof_service


def _git_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    args = parse_args()
    web_app = _load_webservices_app()
    ensure_indexes_and_seed_rule_families, ingest_service, proof_service = _load_clinical_trials_modules()
    resolved_query_config = resolve_query_profile(
        profile_name=args.query_profile,
        condition=args.condition,
        phase=args.phase,
        sponsor=args.sponsor,
        statuses=args.statuses,
        page_size=args.page_size,
        max_pages=args.max_pages,
        limit=args.limit,
    )
    resolved_query = resolved_query_config["query"]
    query_profile = resolved_query_config["queryProfile"]

    ensure_indexes_and_seed_rule_families()

    print(json.dumps({"stage": "rebuild_patient_facts"}), file=sys.stderr, flush=True)
    patient_facts_summary = web_app.rebuild_pharma_patient_facts()
    print(json.dumps({"stage": "rebuild_patient_profiles"}), file=sys.stderr, flush=True)
    patient_profiles_summary = web_app.rebuild_pharma_patient_profiles()
    print(json.dumps({"stage": "rebuild_oncology_cohort"}), file=sys.stderr, flush=True)
    oncology_cohort_summary = web_app.rebuild_breast_oncology_cohort()

    bulk_payload = {
        "condition": resolved_query["condition"],
        "phase": resolved_query["phase"],
        "sponsor": resolved_query["sponsor"],
        "statuses": resolved_query["statuses"],
        "page_size": resolved_query["page_size"],
        "max_pages": resolved_query["max_pages"],
        "limit": resolved_query["limit"],
        "trigger": "calibration_diagnostic_pass",
    }
    print(json.dumps({"stage": "bulk_ingest_trials", "queryProfile": query_profile, "query": bulk_payload}), file=sys.stderr, flush=True)
    bulk_result = ingest_service.run_bulk_ingest_sync(bulk_payload)
    bulk_run = (bulk_result or {}).get("run") or {}
    trial_ids = list((((bulk_run.get("artifacts") or {}).get("trial_ids")) or ((bulk_run.get("scope") or {}).get("trial_ids")) or []))

    query = {
        "condition": resolved_query["condition"],
        "phase": resolved_query["phase"],
        "sponsor": resolved_query["sponsor"],
        "statuses": resolved_query["statuses"],
        "pageSize": resolved_query["page_size"],
        "maxPages": resolved_query["max_pages"],
        "limit": resolved_query["limit"],
        "queryProfile": query_profile,
    }
    report = proof_service.build_diagnostic_report(
        trial_ids=trial_ids,
        query=query,
        git_commit=_git_commit(),
        trial_runtime_tag=RUNTIME_TAG,
        patient_rebuild_summary={
            "patientFacts": patient_facts_summary,
            "patientProfiles": patient_profiles_summary,
            "oncologyCohort": oncology_cohort_summary,
        },
        trial_refresh_summary={
            "runId": bulk_result.get("run_id"),
            "runStatus": bulk_run.get("status"),
            "artifacts": bulk_run.get("artifacts") or {},
            "metrics": bulk_run.get("metrics") or {},
        },
    )

    output_dir = Path(args.output_dir).resolve()
    timestamp = str(report.get("generatedAt") or "").replace(":", "").replace("-", "").replace("T", "_").replace("Z", "")
    output_path = output_dir / f"clinical_trials_calibration_diagnostic_{timestamp}.json"
    latest_path = output_dir / "clinical_trials_calibration_diagnostic_latest.json"
    _write_json(output_path, report)
    _write_json(latest_path, report)

    summary = {
        "outputPath": str(output_path),
        "latestPath": str(latest_path),
        "authoritativeCalibration": {
            "linkedPatients": ((report.get("authoritativeCalibration") or {}).get("linkedPatients")),
            "confirmedBreastOncologyPatients": ((report.get("authoritativeCalibration") or {}).get("confirmedBreastOncologyPatients")),
            "readyTierPatients": ((report.get("authoritativeCalibration") or {}).get("readyTierPatients")),
            "authoritativeBreastOncologyPatients": ((report.get("authoritativeCalibration") or {}).get("authoritativeBreastOncologyPatients")),
            "retailOutreachReadyPatients": ((report.get("authoritativeCalibration") or {}).get("retailOutreachReadyPatients")),
            "selectedAuthoritativeRejectedPatients": ((report.get("authoritativeCalibration") or {}).get("selectedAuthoritativeRejectedPatients")),
            "selectedOutreachRejectedPatients": ((report.get("authoritativeCalibration") or {}).get("selectedOutreachRejectedPatients")),
        },
        "trialRuntimeDiagnostics": {
            "totalTrials": ((report.get("trialRuntimeDiagnostics") or {}).get("totalTrials")),
            "runtimeReadyTrials": ((report.get("trialRuntimeDiagnostics") or {}).get("runtimeReadyTrials")),
            "blockedTrials": ((report.get("trialRuntimeDiagnostics") or {}).get("blockedTrials")),
        },
        "recommendedNextPass": report.get("recommendedNextPass"),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
