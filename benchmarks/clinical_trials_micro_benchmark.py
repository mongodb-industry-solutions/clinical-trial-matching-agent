from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
CLINICAL_TRIALS_SERVICE_DIR = REPO_ROOT / "externals" / "clinical_trials_service"
DEFAULT_PROOF_PATH = REPO_ROOT / "externals" / "benchmarks" / "output" / "clinical_trials_corpus_proof" / "clinical_trials_corpus_proof_latest.json"
OUTPUT_DIR = REPO_ROOT / "externals" / "benchmarks" / "output" / "clinical_trials_micro_benchmark"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze and run the clinical-trials micro benchmark pack from the latest corpus proof artifact.",
    )
    parser.add_argument("--proof-path", default=str(DEFAULT_PROOF_PATH))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--benchmark-pack-id", default="")
    return parser.parse_args()


def _load_clinical_trials_modules():
    sys.path.insert(0, str(CLINICAL_TRIALS_SERVICE_DIR))
    load_dotenv(CLINICAL_TRIALS_SERVICE_DIR / "app" / ".env")
    from app.services.matcher import matcher_service
    from app.services.micro_benchmark_service import (
        REVIEW_OUTCOMES,
        build_benchmark_runtime_metadata,
        build_micro_benchmark_external_review_pack,
        build_micro_benchmark_gold_subset,
        build_micro_benchmark_pack,
        build_micro_benchmark_report,
        build_micro_benchmark_review_results,
        build_micro_benchmark_review_sheet_rows,
        build_micro_benchmark_showcase,
        build_showcase_case,
        render_review_sheet_csv,
    )

    return (
        matcher_service,
        build_benchmark_runtime_metadata,
        build_micro_benchmark_external_review_pack,
        build_micro_benchmark_gold_subset,
        build_micro_benchmark_pack,
        build_micro_benchmark_report,
        build_micro_benchmark_review_results,
        build_micro_benchmark_review_sheet_rows,
        build_micro_benchmark_showcase,
        build_showcase_case,
        render_review_sheet_csv,
        REVIEW_OUTCOMES,
    )


def _git_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def _git_status_short() -> list[str]:
    output = subprocess.check_output(["git", "status", "--short"], cwd=REPO_ROOT, text=True).strip()
    if not output:
        return []
    return [line for line in output.splitlines() if line.strip()]


def _workspace_version() -> dict:
    return {
        "gitCommit": _git_commit(),
        "gitDirty": bool(_git_status_short()),
        "gitStatusShort": _git_status_short(),
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _pair_sort_key(row: dict) -> tuple:
    trace = row.get("traceSummary") or {}
    return (
        -1 * float(row.get("evidenceCompletenessPct") or 0.0),
        int(trace.get("manualReviewRequiredCount") or 0),
        int(trace.get("unknownCount") or 0) + int(trace.get("fillableByQuestionnaireCount") or 0),
        str(row.get("patientRef") or ""),
        str(row.get("trialId") or ""),
    )


def _default_pack_id(proof_report: dict) -> str:
    generated_at = str(proof_report.get("generatedAt") or "").replace(":", "").replace("-", "").replace("T", "_")
    generated_at = generated_at.replace("Z", "").replace(".", "_")
    return f"breast_micro_benchmark_{generated_at[:24] or 'v1'}"


def main() -> int:
    args = parse_args()
    proof_path = Path(args.proof_path).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    proof_report = _load_json(proof_path)
    (
        matcher_service,
        build_benchmark_runtime_metadata,
        build_micro_benchmark_external_review_pack,
        build_micro_benchmark_gold_subset,
        build_micro_benchmark_pack,
        build_micro_benchmark_report,
        build_micro_benchmark_review_results,
        build_micro_benchmark_review_sheet_rows,
        build_micro_benchmark_showcase,
        build_showcase_case,
        render_review_sheet_csv,
        review_outcomes,
    ) = _load_clinical_trials_modules()

    benchmark_pack_id = str(args.benchmark_pack_id or "").strip() or _default_pack_id(proof_report)
    workspace_version = _workspace_version()
    pack = build_micro_benchmark_pack(
        proof_report=proof_report,
        benchmark_pack_id=benchmark_pack_id,
        source_proof_path=str(proof_path),
    )

    if not bool(((pack.get("microBenchmarkGate") or {}).get("ready"))):
        raise SystemExit(
            json.dumps(
                {
                    "error": "micro_benchmark_gate_failed",
                    "benchmarkPackId": benchmark_pack_id,
                    "gate": pack.get("microBenchmarkGate"),
                },
                indent=2,
            )
        )

    patient_refs = list(pack.get("patients") or [])
    trial_ids = list(pack.get("trials") or [])
    patient_runs = []
    for patient_ref in patient_refs:
        result = matcher_service.match_patient_to_trials(
            patient_ref,
            {
                "trial_ids": trial_ids,
                "limit": len(trial_ids),
                "population_mode": "authoritative_oncology",
                "run_type": "benchmark_micro",
                "benchmark_pack_id": benchmark_pack_id,
                "benchmark_label": "micro_patient_to_trials",
            },
        )
        patient_runs.append(result["run"])

    trial_runs = []
    for trial_id in trial_ids:
        result = matcher_service.match_trial_to_patients(
            trial_id,
            {
                "patient_refs": patient_refs,
                "limit": len(patient_refs),
                "include_conflict_patients": False,
                "population_mode": "authoritative_oncology",
                "run_type": "benchmark_micro",
                "benchmark_pack_id": benchmark_pack_id,
                "benchmark_label": "micro_trial_to_patients",
            },
        )
        trial_runs.append(result["run"])

    report = build_micro_benchmark_report(
        pack=pack,
        patient_runs=patient_runs,
        trial_runs=trial_runs,
    )
    runtime_metadata = build_benchmark_runtime_metadata(
        matcher_version=workspace_version["gitCommit"],
        patient_runs=patient_runs,
        trial_runs=trial_runs,
    )

    missing_showcase_outcomes = [outcome for outcome in review_outcomes if outcome not in (report.get("showcaseCases") or {})]
    supplemental_probe_runs = []
    if missing_showcase_outcomes:
        authoritative_refs = [
            str(item).strip()
            for item in ((proof_report.get("benchmarkCandidates") or {}).get("patientRefs") or [])
            if str(item).strip()
        ]
        supplemental_refs = [patient_ref for patient_ref in authoritative_refs if patient_ref not in set(patient_refs)]
        for patient_ref in supplemental_refs:
            if not missing_showcase_outcomes:
                break
            result = matcher_service.match_patient_to_trials(
                patient_ref,
                {
                    "trial_ids": trial_ids,
                    "limit": len(trial_ids),
                    "population_mode": "authoritative_oncology",
                    "run_type": "benchmark_micro_probe",
                    "benchmark_pack_id": benchmark_pack_id,
                    "benchmark_label": "micro_showcase_probe",
                },
            )
            run = result["run"]
            supplemental_probe_runs.append(run)
            pair_rows = []
            for item in run.get("results") or []:
                pair_row = dict(item)
                pair_row["patientRef"] = patient_ref
                pair_rows.append(pair_row)
            for outcome in list(missing_showcase_outcomes):
                candidates = [row for row in pair_rows if str(row.get("overall") or "") == outcome]
                if not candidates:
                    continue
                candidates.sort(key=_pair_sort_key)
                showcase = build_showcase_case(candidates[0])
                showcase["source"] = "supplemental_probe"
                (report.setdefault("showcaseCases", {}))[outcome] = showcase
                missing_showcase_outcomes.remove(outcome)

    pack["gitCommit"] = workspace_version["gitCommit"]
    pack["workspaceVersion"] = workspace_version
    pack["runtimeMetadata"] = runtime_metadata
    report["gitCommit"] = pack["gitCommit"]
    report["workspaceVersion"] = workspace_version
    report["runtimeMetadata"] = runtime_metadata
    report["supplementalProbeRunIds"] = [str(run.get("_id") or "").strip() for run in supplemental_probe_runs if str(run.get("_id") or "").strip()]

    gold_subset = build_micro_benchmark_gold_subset(
        pack=pack,
        report=report,
        runtime_metadata=runtime_metadata,
    )
    review_results = build_micro_benchmark_review_results(
        pack=pack,
        report=report,
        gold_subset=gold_subset,
    )
    showcase_cases = build_micro_benchmark_showcase(
        pack=pack,
        report=report,
        gold_subset=gold_subset,
    )
    external_review_pack = build_micro_benchmark_external_review_pack(
        pack=pack,
        report=report,
        gold_subset=gold_subset,
        review_results=review_results,
        showcase_cases=showcase_cases,
    )
    review_sheet_rows = build_micro_benchmark_review_sheet_rows(
        external_review_pack=external_review_pack,
    )
    review_sheet_csv = render_review_sheet_csv(review_sheet_rows)
    gold_subset["workspaceVersion"] = workspace_version
    review_results["workspaceVersion"] = workspace_version
    showcase_cases["workspaceVersion"] = workspace_version
    external_review_pack["workspaceVersion"] = workspace_version
    pack["reviewedPairs"] = list(gold_subset.get("reviewedPairs") or [])
    pack["showcaseCases"] = dict(showcase_cases.get("cases") or {})
    pack["runtimeMetadata"] = runtime_metadata
    pack["execution"] = {
        "generatedAt": report.get("generatedAt"),
        "patientToTrialsRunCount": len(patient_runs),
        "trialToPatientsRunCount": len(trial_runs),
        "supplementalProbeRunCount": len(supplemental_probe_runs),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    pack_output_path = output_dir / f"clinical_trials_micro_benchmark_pack_{benchmark_pack_id}.json"
    pack_latest_path = output_dir / "clinical_trials_micro_benchmark_pack_latest.json"
    report_output_path = output_dir / f"clinical_trials_micro_benchmark_results_{benchmark_pack_id}.json"
    report_latest_path = output_dir / "clinical_trials_micro_benchmark_results_latest.json"
    gold_output_path = output_dir / f"clinical_trials_micro_benchmark_gold_subset_{benchmark_pack_id}.json"
    gold_latest_path = output_dir / "clinical_trials_micro_benchmark_gold_subset_latest.json"
    review_output_path = output_dir / f"clinical_trials_micro_benchmark_review_results_{benchmark_pack_id}.json"
    review_latest_path = output_dir / "clinical_trials_micro_benchmark_review_results_latest.json"
    showcase_output_path = output_dir / f"clinical_trials_showcase_cases_{benchmark_pack_id}.json"
    showcase_latest_path = output_dir / "clinical_trials_showcase_cases_latest.json"
    external_review_output_path = output_dir / f"clinical_trials_micro_benchmark_external_review_pack_{benchmark_pack_id}.json"
    external_review_latest_path = output_dir / "clinical_trials_micro_benchmark_external_review_pack_latest.json"
    review_sheet_output_path = output_dir / f"clinical_trials_micro_benchmark_review_sheet_{benchmark_pack_id}.csv"
    review_sheet_latest_path = output_dir / "clinical_trials_micro_benchmark_review_sheet_latest.csv"

    _write_json(pack_output_path, pack)
    _write_json(pack_latest_path, pack)
    _write_json(report_output_path, report)
    _write_json(report_latest_path, report)
    _write_json(gold_output_path, gold_subset)
    _write_json(gold_latest_path, gold_subset)
    _write_json(review_output_path, review_results)
    _write_json(review_latest_path, review_results)
    _write_json(showcase_output_path, showcase_cases)
    _write_json(showcase_latest_path, showcase_cases)
    _write_json(external_review_output_path, external_review_pack)
    _write_json(external_review_latest_path, external_review_pack)
    _write_text(review_sheet_output_path, review_sheet_csv)
    _write_text(review_sheet_latest_path, review_sheet_csv)

    print(
        json.dumps(
            {
                "benchmarkPackId": benchmark_pack_id,
                "packOutputPath": str(pack_output_path),
                "packLatestPath": str(pack_latest_path),
                "resultsOutputPath": str(report_output_path),
                "resultsLatestPath": str(report_latest_path),
                "goldSubsetOutputPath": str(gold_output_path),
                "goldSubsetLatestPath": str(gold_latest_path),
                "reviewResultsOutputPath": str(review_output_path),
                "reviewResultsLatestPath": str(review_latest_path),
                "showcaseOutputPath": str(showcase_output_path),
                "showcaseLatestPath": str(showcase_latest_path),
                "externalReviewPackOutputPath": str(external_review_output_path),
                "externalReviewPackLatestPath": str(external_review_latest_path),
                "reviewSheetOutputPath": str(review_sheet_output_path),
                "reviewSheetLatestPath": str(review_sheet_latest_path),
                "microBenchmarkGate": pack.get("microBenchmarkGate"),
                "summary": report.get("summary"),
                "reviewResults": review_results.get("overallMetrics"),
                "showcaseCases": showcase_cases.get("cases"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
