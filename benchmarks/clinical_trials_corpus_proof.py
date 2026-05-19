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
WEBservices_DIR = REPO_ROOT / "externals" / "webservices"
CLINICAL_TRIALS_SERVICE_DIR = REPO_ROOT / "externals" / "clinical_trials_service"
OUTPUT_DIR = REPO_ROOT / "externals" / "benchmarks" / "output" / "clinical_trials_corpus_proof"
RUNTIME_TAG = "clinical-trials-canonical-runtime"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the clinical-trials corpus proof pass: rebuild curated patients, refresh the breast-cancer trial corpus, and write a machine-readable proof artifact.",
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
    sys.path.insert(0, str(WEBservices_DIR))
    load_dotenv(WEBservices_DIR / ".env")
    os.environ.setdefault("TRIAL_MATCHING_DATASET_SKIP_STARTUP_AUDIT", "true")
    os.environ["VOYAGE_API_KEY"] = ""
    module_path = WEBservices_DIR / "app.py"
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


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _delta_row(*, before: int | float, after: int | float) -> dict:
    return {
        "before": before,
        "after": after,
        "delta": after - before,
    }


def _family_readiness_map(report: dict | None) -> dict[str, dict]:
    payload = {}
    for row in (report or {}).get("familyReadiness") or []:
        family_id = str(row.get("familyId") or "").strip()
        if family_id:
            payload[family_id] = row
    return payload


def _unresolved_family_map(report: dict | None) -> dict[str, dict]:
    payload = {}
    for row in ((report or {}).get("blockers") or {}).get("unresolvedTrialFamilies") or []:
        family_id = str(row.get("familyId") or "").strip()
        if family_id:
            payload[family_id] = row
    return payload


def _reason_counter_map(rows: list[dict] | None) -> dict[str, int]:
    payload: dict[str, int] = {}
    for row in rows or []:
        reason = str(row.get("reason") or row.get("disposition") or "").strip()
        if not reason:
            continue
        payload[reason] = int(row.get("count") or 0)
    return payload


def _build_delta_report(previous: dict | None, current: dict) -> dict:
    previous = previous or {}
    previous_trial_summary = previous.get("trialSummary") or {}
    current_trial_summary = current.get("trialSummary") or {}
    previous_family_rows = _family_readiness_map(previous)
    current_family_rows = _family_readiness_map(current)
    previous_unresolved = _unresolved_family_map(previous)
    current_unresolved = _unresolved_family_map(current)
    previous_trial_blockers = _reason_counter_map(((previous or {}).get("blockers") or {}).get("trialRuntimeBlockers"))
    current_trial_blockers = _reason_counter_map((current.get("blockers") or {}).get("trialRuntimeBlockers"))
    previous_triage_summary = _reason_counter_map(((previous or {}).get("unassignedTriage") or {}).get("summaryByDisposition"))
    current_triage_summary = _reason_counter_map((current.get("unassignedTriage") or {}).get("summaryByDisposition"))

    focus_families = [
        "demographics.sex.allowed",
        "performance.ecog.max",
        "oncology.primary_cancer.required",
        "oncology.advanced_or_metastatic.required",
        "biomarkers.ER.status.required",
        "biomarkers.HER2.status.required",
        "organ_function.lab_panel",
        "therapy.prior_exposure.prohibited_history",
        "context.pregnancy_breastfeeding",
        "context.infection_recent_systemic_therapy",
        "concomitant_illness.significant",
        "manual.stage_extent_context",
        "manual.safety_contraindication_history",
        "manual.complex_disease_setting",
        "manual.prior_exposure_contextual",
        "manual.inclusion_context_other",
        "manual.exclusion_context_other",
        "protocol_workflow.not_patient_rule",
    ]

    return {
        "generatedAt": current.get("generatedAt"),
        "previousGeneratedAt": previous.get("generatedAt"),
        "currentGeneratedAt": current.get("generatedAt"),
        "gitCommit": current.get("gitCommit"),
        "trialRuntimeTag": current.get("trialRuntimeTag"),
        "query": current.get("query"),
        "trialSummaryDelta": {
            "runtimeReadyTrials": _delta_row(
                before=int(previous_trial_summary.get("runtimeReadyTrials") or 0),
                after=int(current_trial_summary.get("runtimeReadyTrials") or 0),
            ),
            "runtimeReadyDeterministicTrials": _delta_row(
                before=int(previous_trial_summary.get("runtimeReadyDeterministicTrials") or 0),
                after=int(current_trial_summary.get("runtimeReadyDeterministicTrials") or 0),
            ),
            "runtimeReadyWithManualTrials": _delta_row(
                before=int(previous_trial_summary.get("runtimeReadyWithManualTrials") or 0),
                after=int(current_trial_summary.get("runtimeReadyWithManualTrials") or 0),
            ),
            "blockedTrials": _delta_row(
                before=int(previous_trial_summary.get("blockedTrials") or 0),
                after=int(current_trial_summary.get("blockedTrials") or 0),
            ),
            "averageDeterministicPatientImpactingCount": _delta_row(
                before=float(previous_trial_summary.get("averageDeterministicPatientImpactingCount") or 0.0),
                after=float(current_trial_summary.get("averageDeterministicPatientImpactingCount") or 0.0),
            ),
            "averageManualPatientImpactingCount": _delta_row(
                before=float(previous_trial_summary.get("averageManualPatientImpactingCount") or 0.0),
                after=float(current_trial_summary.get("averageManualPatientImpactingCount") or 0.0),
            ),
            "averageUnresolvedPatientImpactingCount": _delta_row(
                before=float(previous_trial_summary.get("averageUnresolvedPatientImpactingCount") or 0.0),
                after=float(current_trial_summary.get("averageUnresolvedPatientImpactingCount") or 0.0),
            ),
            "runtimeReadyTrialIdsBefore": list(previous_trial_summary.get("runtimeReadyTrialIds") or []),
            "runtimeReadyTrialIdsAfter": list(current_trial_summary.get("runtimeReadyTrialIds") or []),
            "newlyRuntimeReadyTrialIds": sorted(
                set(current_trial_summary.get("runtimeReadyTrialIds") or []) - set(previous_trial_summary.get("runtimeReadyTrialIds") or [])
            ),
        },
        "focusFamilyDelta": [
            {
                "familyId": family_id,
                "trialUsageCount": _delta_row(
                    before=int((previous_family_rows.get(family_id) or {}).get("trialUsageCount") or 0),
                    after=int((current_family_rows.get(family_id) or {}).get("trialUsageCount") or 0),
                ),
                "trialDeterministicUsageCount": _delta_row(
                    before=int((previous_family_rows.get(family_id) or {}).get("trialDeterministicUsageCount") or 0),
                    after=int((current_family_rows.get(family_id) or {}).get("trialDeterministicUsageCount") or 0),
                ),
                "trialPendingUsageCount": _delta_row(
                    before=int((previous_family_rows.get(family_id) or {}).get("trialPendingUsageCount") or 0),
                    after=int((current_family_rows.get(family_id) or {}).get("trialPendingUsageCount") or 0),
                ),
                "unresolvedCount": _delta_row(
                    before=int((previous_unresolved.get(family_id) or {}).get("count") or 0),
                    after=int((current_unresolved.get(family_id) or {}).get("count") or 0),
                ),
            }
            for family_id in focus_families
        ],
        "unassignedDelta": _delta_row(
            before=int((previous_unresolved.get("unassigned") or {}).get("count") or 0),
            after=int((current_unresolved.get("unassigned") or {}).get("count") or 0),
        ),
        "exclusionParseGapDelta": _delta_row(
            before=int(previous_trial_blockers.get("exclusion criteria still contain unresolved deterministic gaps") or 0),
            after=int(current_trial_blockers.get("exclusion criteria still contain unresolved deterministic gaps") or 0),
        ),
        "unassignedDispositionDelta": [
            {
                "disposition": disposition,
                "count": _delta_row(
                    before=int(previous_triage_summary.get(disposition) or 0),
                    after=int(current_triage_summary.get(disposition) or 0),
                ),
            }
            for disposition in sorted(set(previous_triage_summary.keys()) | set(current_triage_summary.keys()))
        ],
    }


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
        "trigger": "corpus_proof_pass",
    }
    print(json.dumps({"stage": "bulk_ingest_trials", "queryProfile": query_profile, "query": bulk_payload}), file=sys.stderr, flush=True)
    bulk_result = ingest_service.run_bulk_ingest_sync(bulk_payload)
    bulk_run = (bulk_result or {}).get("run") or {}
    trial_ids = list((((bulk_run.get("artifacts") or {}).get("trial_ids")) or ((bulk_run.get("scope") or {}).get("trial_ids")) or []))

    recompile_summaries = []
    if trial_ids:
        print(json.dumps({"stage": "recompile_current_trials", "trialCount": len(trial_ids)}), file=sys.stderr, flush=True)
        for index, trial_id in enumerate(trial_ids, start=1):
            print(json.dumps({"stage": "recompile_trial", "trialId": trial_id, "index": index, "total": len(trial_ids)}), file=sys.stderr, flush=True)
            recompile_result = ingest_service.run_recompile_trial_sync(
                trial_id,
                {
                    "trigger": "corpus_proof_recompile",
                    "reason": "refresh stored trials against the current compiler and rule registry",
                },
            )
            recompile_run = (recompile_result or {}).get("run") or {}
            recompile_summaries.append(
                {
                    "trialId": trial_id,
                    "runId": recompile_result.get("run_id"),
                    "status": recompile_run.get("status"),
                    "metrics": recompile_run.get("metrics") or {},
                    "artifacts": recompile_run.get("artifacts") or {},
                }
            )

    report = proof_service.build_report(
        trial_ids=trial_ids,
        query={
            "condition": resolved_query["condition"],
            "phase": resolved_query["phase"],
            "sponsor": resolved_query["sponsor"],
            "statuses": resolved_query["statuses"],
            "pageSize": resolved_query["page_size"],
            "maxPages": resolved_query["max_pages"],
            "limit": resolved_query["limit"],
            "queryProfile": query_profile,
        },
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
            "recompileRuns": recompile_summaries,
        },
    )

    output_dir = Path(args.output_dir).resolve()
    latest_path = output_dir / "clinical_trials_corpus_proof_latest.json"
    previous_report = _load_json(latest_path)
    timestamp = str(report.get("generatedAt") or "").replace(":", "").replace("-", "").replace("T", "_").replace("Z", "")
    output_path = output_dir / f"clinical_trials_corpus_proof_{timestamp}.json"
    delta_report = _build_delta_report(previous_report, report)
    delta_output_path = output_dir / f"clinical_trials_corpus_proof_delta_{timestamp}.json"
    delta_latest_path = output_dir / "clinical_trials_corpus_proof_delta_latest.json"
    triage_output_path = output_dir / f"clinical_trials_unassigned_triage_{timestamp}.json"
    triage_latest_path = output_dir / "clinical_trials_unassigned_triage_latest.json"
    _write_json(output_path, report)
    _write_json(latest_path, report)
    _write_json(delta_output_path, delta_report)
    _write_json(delta_latest_path, delta_report)
    _write_json(triage_output_path, report.get("unassignedTriage") or {})
    _write_json(triage_latest_path, report.get("unassignedTriage") or {})

    summary = {
        "outputPath": str(output_path),
        "latestPath": str(latest_path),
        "deltaOutputPath": str(delta_output_path),
        "deltaLatestPath": str(delta_latest_path),
        "triageOutputPath": str(triage_output_path),
        "triageLatestPath": str(triage_latest_path),
        "populationSummary": report.get("populationSummary"),
        "workflowSummary": report.get("workflowSummary"),
        "query": report.get("query"),
        "matchReadySubset": {
            "count": ((report.get("matchReadySubset") or {}).get("count")),
        },
        "retailOutreachSubset": {
            "count": ((report.get("retailOutreachSubset") or {}).get("count")),
        },
        "trialSummary": report.get("trialSummary"),
        "trialSummaryDelta": delta_report.get("trialSummaryDelta"),
        "unassignedTriage": {
            "totalRows": ((report.get("unassignedTriage") or {}).get("totalRows")),
            "summaryByDisposition": ((report.get("unassignedTriage") or {}).get("summaryByDisposition") or []),
        },
        "benchmarkGate": report.get("benchmarkGate"),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
