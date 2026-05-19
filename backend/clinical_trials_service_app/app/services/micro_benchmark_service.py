from __future__ import annotations

import csv
import io
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from statistics import mean
from typing import Any

from app.services.helpers import serialize_document, utcnow


MICRO_BENCHMARK_THRESHOLDS = {
    "minMatchReadyPatients": 4,
    "minRuntimeReadyTrials": 5,
}

SCALE_BENCHMARK_THRESHOLDS = {
    "minMatchReadyPatients": 25,
    "minRuntimeReadyTrials": 10,
}

REVIEW_OUTCOMES = [
    "candidate",
    "excluded",
    "needs_more_data",
    "manual_review_required",
]

CORE_EXPECTATION_FAMILIES = {
    "demographics.age.min",
    "demographics.age.max",
    "demographics.sex.allowed",
    "oncology.primary_cancer.required",
    "oncology.advanced_or_metastatic.required",
    "biomarkers.ER.status.required",
    "biomarkers.HER2.status.required",
    "performance.ecog.max",
    "organ_function.lab_panel",
    "therapy.prior_exposure.prohibited_history",
}

REVIEW_METHOD = "assistant_adjudicated_from_frozen_canonical_runtime_v1"
REVIEW_PACK_LABEL = "assistant_adjudicated_internal_gold_subset_v1"
SME_REVIEW_LABEL = "sme_reviewed_gold_subset_v1"

ALLOWED_OVERALL_OUTCOMES = {
    "candidate",
    "excluded",
    "needs_more_data",
    "manual_review_required",
    "needs_identity_resolution",
}

ALLOWED_CRITERION_OUTCOMES = {
    "met",
    "not_met",
    "unknown",
    "not_patient_rule",
    "blocked_by_linkage_conflict",
    "fillable_by_questionnaire",
    "manual_review_required",
}

REPO_ROOT = Path(__file__).resolve().parents[4]
MICRO_BENCHMARK_OUTPUT_DIR = REPO_ROOT / "benchmarks" / "output" / "clinical_trials_micro_benchmark"
MICRO_BENCHMARK_LATEST_PATHS = {
    "pack": MICRO_BENCHMARK_OUTPUT_DIR / "clinical_trials_micro_benchmark_pack_latest.json",
    "results": MICRO_BENCHMARK_OUTPUT_DIR / "clinical_trials_micro_benchmark_results_latest.json",
    "gold_subset": MICRO_BENCHMARK_OUTPUT_DIR / "clinical_trials_micro_benchmark_gold_subset_latest.json",
    "review_results": MICRO_BENCHMARK_OUTPUT_DIR / "clinical_trials_micro_benchmark_review_results_latest.json",
    "showcase": MICRO_BENCHMARK_OUTPUT_DIR / "clinical_trials_showcase_cases_latest.json",
    "external_review_pack": MICRO_BENCHMARK_OUTPUT_DIR / "clinical_trials_micro_benchmark_external_review_pack_latest.json",
    "review_sheet": MICRO_BENCHMARK_OUTPUT_DIR / "clinical_trials_micro_benchmark_review_sheet_latest.csv",
    "sme_review_results": MICRO_BENCHMARK_OUTPUT_DIR / "clinical_trials_micro_benchmark_sme_review_results_latest.json",
}

SME_DISAGREEMENT_TAXONOMY = {
    "benchmark_labeling_issue",
    "supported_family_evaluator_issue",
    "patient_evidence_extraction_issue",
    "manual_family_classification_issue",
    "source_ambiguity_or_not_enough_evidence",
    "reviewer_uncertainty",
}


def _threshold_gate(*, actual: dict[str, int], thresholds: dict[str, int]) -> dict:
    reasons = []
    if int(actual.get("matchReadyPatients") or 0) < int(thresholds.get("minMatchReadyPatients") or 0):
        reasons.append("insufficient_match_ready_patients")
    if int(actual.get("runtimeReadyTrials") or 0) < int(thresholds.get("minRuntimeReadyTrials") or 0):
        reasons.append("insufficient_runtime_ready_trials")
    return {
        "ready": len(reasons) == 0,
        "thresholds": deepcopy(thresholds),
        "actual": deepcopy(actual),
        "reasons": reasons,
    }


def build_micro_benchmark_pack(*, proof_report: dict, benchmark_pack_id: str, source_proof_path: str) -> dict:
    match_ready_patient_refs = [str(item).strip() for item in (proof_report.get("matchReadySubset") or {}).get("patientRefs") or [] if str(item).strip()]
    runtime_ready_trial_ids = [str(item).strip() for item in (proof_report.get("trialSummary") or {}).get("runtimeReadyTrialIds") or [] if str(item).strip()]

    actual = {
        "matchReadyPatients": len(match_ready_patient_refs),
        "runtimeReadyTrials": len(runtime_ready_trial_ids),
    }
    return {
        "benchmarkPackId": benchmark_pack_id,
        "packType": "micro_benchmark",
        "generatedAt": serialize_document(utcnow()),
        "gitCommit": proof_report.get("gitCommit"),
        "trialRuntimeTag": proof_report.get("trialRuntimeTag"),
        "query": deepcopy(proof_report.get("query") or {}),
        "queryProfile": deepcopy(((proof_report.get("query") or {}).get("queryProfile") or {})),
        "sourceProof": {
            "path": source_proof_path,
            "generatedAt": proof_report.get("generatedAt"),
            "queryProfile": deepcopy(((proof_report.get("query") or {}).get("queryProfile") or {})),
        },
        "populationSummary": deepcopy(proof_report.get("populationSummary") or {}),
        "stageScopeSummary": deepcopy(proof_report.get("stageScopeSummary") or {}),
        "trialSummary": deepcopy(proof_report.get("trialSummary") or {}),
        "microBenchmarkGate": _threshold_gate(actual=actual, thresholds=MICRO_BENCHMARK_THRESHOLDS),
        "scaleBenchmarkGate": _threshold_gate(actual=actual, thresholds=SCALE_BENCHMARK_THRESHOLDS),
        "patients": match_ready_patient_refs,
        "trials": runtime_ready_trial_ids,
        "pairCount": len(match_ready_patient_refs) * len(runtime_ready_trial_ids),
        "reviewPolicy": {
            "reviewStatus": "seeded_from_canonical_runtime",
            "targetPairs": 24,
            "targetPairsPerOutcome": 6,
            "outcomes": list(REVIEW_OUTCOMES),
            "coreExpectationFamilies": sorted(CORE_EXPECTATION_FAMILIES),
        },
    }


def _single_or_list(values: set[str]) -> str | list[str] | None:
    cleaned = sorted({str(value).strip() for value in values if str(value).strip()})
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return cleaned[0]
    return cleaned


def build_benchmark_runtime_metadata(*, matcher_version: str, patient_runs: list[dict], trial_runs: list[dict]) -> dict:
    all_runs = list(patient_runs or []) + list(trial_runs or [])
    runtime_modes: set[str] = set()
    population_modes: set[str] = set()
    legacy_inputs_used: set[str] = set()
    trial_contract_versions: set[str] = set()
    patient_evidence_versions: set[str] = set()
    patient_match_versions: set[str] = set()
    rule_family_registry_versions: set[str] = set()
    trial_version_ids: dict[str, str] = {}
    legacy_behavior_allowed = False

    for run in all_runs:
        runtime_modes.add(str(run.get("runtimeMode") or "").strip())
        population_modes.add(str(run.get("populationMode") or "").strip())
        legacy_behavior_allowed = legacy_behavior_allowed or bool(run.get("legacyBehaviorAllowed"))
        for item in run.get("legacyInputsUsed") or []:
            value = str(item).strip()
            if value:
                legacy_inputs_used.add(value)

        contract_versions = run.get("contractVersions") or {}
        trial_contract_versions.add(str(contract_versions.get("trialContractVersion") or "").strip())
        patient_evidence_versions.add(str(contract_versions.get("patientEvidenceProfileVersion") or "").strip())
        patient_match_versions.add(str(contract_versions.get("patientMatchProfileVersion") or "").strip())
        rule_family_registry_versions.add(str(contract_versions.get("ruleFamilyRegistryVersion") or "").strip())

        run_trial_id = str(run.get("trialId") or "").strip()
        direct_trial_version = str(contract_versions.get("trialVersionId") or "").strip()
        if run_trial_id and direct_trial_version:
            trial_version_ids[run_trial_id] = direct_trial_version
        for trial_id, version_id in (contract_versions.get("trialVersionIds") or {}).items():
            normalized_trial_id = str(trial_id or "").strip()
            normalized_version_id = str(version_id or "").strip()
            if normalized_trial_id and normalized_version_id:
                trial_version_ids[normalized_trial_id] = normalized_version_id

    return {
        "matcherVersion": matcher_version,
        "runtimeMode": _single_or_list(runtime_modes),
        "populationModes": sorted({value for value in population_modes if value}),
        "legacyBehaviorAllowed": legacy_behavior_allowed,
        "legacyInputsUsed": sorted(legacy_inputs_used),
        "trialContractVersion": _single_or_list(trial_contract_versions),
        "patientEvidenceProfileVersion": _single_or_list(patient_evidence_versions),
        "patientMatchProfileVersion": _single_or_list(patient_match_versions),
        "ruleFamilyRegistryVersion": _single_or_list(rule_family_registry_versions),
        "trialVersionIds": dict(sorted(trial_version_ids.items())),
    }


def _pair_sort_key(row: dict) -> tuple[Any, ...]:
    trace = row.get("traceSummary") or {}
    return (
        -1 * float(row.get("evidenceCompletenessPct") or 0.0),
        int(trace.get("manualReviewRequiredCount") or 0),
        int(trace.get("unknownCount") or 0) + int(trace.get("fillableByQuestionnaireCount") or 0),
        str(row.get("patientRef") or ""),
        str(row.get("trialId") or ""),
    )


def build_pair_expectations(pair_row: dict) -> list[dict]:
    expectations: list[dict] = []
    seen = set()

    for row in ((pair_row.get("prefilter") or {}).get("evaluations") or []):
        family_id = str(row.get("familyId") or "").strip()
        if family_id not in CORE_EXPECTATION_FAMILIES:
            continue
        signature = ("prefilter", family_id, str(row.get("key") or "").strip())
        if signature in seen:
            continue
        seen.add(signature)
        expectations.append(
            {
                "source": "prefilter",
                "key": row.get("key"),
                "familyId": family_id,
                "expected": row.get("status"),
            }
        )

    for row in pair_row.get("criteria") or []:
        family_id = str(row.get("familyId") or "").strip()
        if family_id not in CORE_EXPECTATION_FAMILIES:
            continue
        signature = ("criterion", family_id, str(row.get("criterionId") or "").strip())
        if signature in seen:
            continue
        seen.add(signature)
        expectations.append(
            {
                "source": "criterion",
                "criterionId": row.get("criterionId"),
                "familyId": family_id,
                "expected": row.get("status"),
            }
        )
    return expectations


def _build_review_subset(pair_rows: list[dict]) -> list[dict]:
    selected: list[dict] = []
    for outcome in REVIEW_OUTCOMES:
        candidates = [row for row in pair_rows if str(row.get("overall") or "") == outcome]
        candidates.sort(key=_pair_sort_key)
        for row in candidates[:6]:
            selected.append(
                {
                    "patientRef": row.get("patientRef"),
                    "trialId": row.get("trialId"),
                    "expectedOverall": row.get("overall"),
                    "expectedCriteria": build_pair_expectations(row),
                    "reviewStatus": "seeded_from_canonical_runtime",
                    "runtimeReadiness": row.get("runtimeReadiness") or {},
                    "evidenceCompletenessPct": row.get("evidenceCompletenessPct"),
                    "traceSummary": row.get("traceSummary") or {},
                    "notes": "Baseline seeded from canonical runtime for targeted human review.",
                }
            )
    return selected


def build_showcase_case(pair_row: dict) -> dict:
    return {
        "patientRef": pair_row.get("patientRef"),
        "trialId": pair_row.get("trialId"),
        "overall": pair_row.get("overall"),
        "evidenceCompletenessPct": pair_row.get("evidenceCompletenessPct"),
        "traceSummary": pair_row.get("traceSummary") or {},
        "criterionExpectations": build_pair_expectations(pair_row),
    }


def _build_showcase_cases(pair_rows: list[dict]) -> dict:
    payload: dict[str, dict] = {}
    for outcome in REVIEW_OUTCOMES:
        candidates = [row for row in pair_rows if str(row.get("overall") or "") == outcome]
        if not candidates:
            continue
        candidates.sort(key=_pair_sort_key)
        payload[outcome] = build_showcase_case(candidates[0])
    return payload


def _flatten_patient_runs(patient_runs: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for run in patient_runs:
        run_id = run.get("_id")
        patient_ref = run.get("patientRef")
        for result in run.get("results") or []:
            row = deepcopy(result)
            row["patientRef"] = patient_ref
            row["runId"] = run_id
            rows.append(row)
    return rows


def _flatten_trial_runs(trial_runs: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for run in trial_runs:
        trial_id = run.get("trialId")
        run_id = run.get("_id")
        for result in run.get("results") or []:
            row = deepcopy(result)
            row["trialId"] = trial_id
            row["runId"] = run_id
            rows.append(row)
    return rows


def build_micro_benchmark_report(*, pack: dict, patient_runs: list[dict], trial_runs: list[dict]) -> dict:
    patient_pair_rows = _flatten_patient_runs(patient_runs)
    trial_pair_rows = _flatten_trial_runs(trial_runs)
    trial_pair_map = {
        (str(row.get("patientRef") or "").strip(), str(row.get("trialId") or "").strip()): row
        for row in trial_pair_rows
        if str(row.get("patientRef") or "").strip() and str(row.get("trialId") or "").strip()
    }

    pair_rows: list[dict] = []
    directional_mismatches: list[dict] = []
    overall_counter = Counter()
    manual_burden: list[float] = []
    unknown_burden: list[float] = []
    completeness_values: list[float] = []

    for row in patient_pair_rows:
        key = (str(row.get("patientRef") or "").strip(), str(row.get("trialId") or "").strip())
        trial_direction = trial_pair_map.get(key)
        directional_consistent = bool(trial_direction) and str(trial_direction.get("overall") or "") == str(row.get("overall") or "")
        trace = row.get("traceSummary") or {}
        pair_row = {
            "patientRef": row.get("patientRef"),
            "patientName": (trial_direction or {}).get("patientName"),
            "linkageStatus": (trial_direction or {}).get("linkageStatus"),
            "patientCoveragePct": (trial_direction or {}).get("coveragePct"),
            "patientRuntimeReadiness": (trial_direction or {}).get("runtimeReadiness") or {},
            "patientCohortMembership": (trial_direction or {}).get("cohortMembership") or {},
            "trialId": row.get("trialId"),
            "title": row.get("title"),
            "overall": row.get("overall"),
            "compileStatus": row.get("compileStatus"),
            "runtimeReadiness": row.get("runtimeReadiness") or {},
            "evidenceCompletenessPct": row.get("evidenceCompletenessPct"),
            "traceSummary": trace,
            "prefilter": row.get("prefilter") or {},
            "criteria": row.get("criteria") or [],
            "gaps": row.get("gaps") or [],
            "directionalConsistency": directional_consistent,
            "trialDirectionOverall": (trial_direction or {}).get("overall"),
            "patientToTrialsRunId": row.get("runId"),
            "trialToPatientsRunId": (trial_direction or {}).get("runId"),
        }
        pair_rows.append(pair_row)
        overall_counter[str(pair_row.get("overall") or "unknown")] += 1
        manual_burden.append(float(trace.get("manualReviewRequiredCount") or 0))
        unknown_burden.append(float(trace.get("unknownCount") or 0) + float(trace.get("fillableByQuestionnaireCount") or 0))
        completeness_values.append(float(pair_row.get("evidenceCompletenessPct") or 0.0))
        if trial_direction is None or not directional_consistent:
            directional_mismatches.append(
                {
                    "patientRef": pair_row.get("patientRef"),
                    "trialId": pair_row.get("trialId"),
                    "patientToTrialsOverall": pair_row.get("overall"),
                    "trialToPatientsOverall": (trial_direction or {}).get("overall"),
                }
            )

    pair_rows.sort(key=lambda row: (str(row.get("overall") or ""), _pair_sort_key(row)))
    review_subset = _build_review_subset(pair_rows)
    showcase_cases = _build_showcase_cases(pair_rows)
    total_pairs = len(pair_rows)
    consistent_pairs = total_pairs - len(directional_mismatches)

    return {
        "benchmarkPackId": pack.get("benchmarkPackId"),
        "generatedAt": serialize_document(utcnow()),
        "gitCommit": pack.get("gitCommit"),
        "trialRuntimeTag": pack.get("trialRuntimeTag"),
        "pack": {
            "patientCount": len(pack.get("patients") or []),
            "trialCount": len(pack.get("trials") or []),
            "pairCount": int(pack.get("pairCount") or 0),
            "microBenchmarkGate": deepcopy(pack.get("microBenchmarkGate") or {}),
            "scaleBenchmarkGate": deepcopy(pack.get("scaleBenchmarkGate") or {}),
        },
        "execution": {
            "patientToTrialsRunCount": len(patient_runs),
            "trialToPatientsRunCount": len(trial_runs),
            "patientToTrialsRunIds": [str(run.get("_id") or "").strip() for run in patient_runs if str(run.get("_id") or "").strip()],
            "trialToPatientsRunIds": [str(run.get("_id") or "").strip() for run in trial_runs if str(run.get("_id") or "").strip()],
        },
        "summary": {
            "totalPairs": total_pairs,
            "overallCounts": dict(overall_counter),
            "averageEvidenceCompletenessPct": round(mean(completeness_values), 1) if completeness_values else 0.0,
            "averageManualReviewBurden": round(mean(manual_burden), 2) if manual_burden else 0.0,
            "averageUnknownBurden": round(mean(unknown_burden), 2) if unknown_burden else 0.0,
            "directionalConsistencyPct": round((consistent_pairs / total_pairs) * 100.0, 1) if total_pairs else 0.0,
            "directionalMismatchCount": len(directional_mismatches),
        },
        "reviewSubsetSeed": review_subset,
        "showcaseCases": showcase_cases,
        "pairRows": pair_rows,
        "directionalMismatches": directional_mismatches,
    }


def _pair_map(pair_rows: list[dict]) -> dict[tuple[str, str], dict]:
    return {
        (str(row.get("patientRef") or "").strip(), str(row.get("trialId") or "").strip()): row
        for row in pair_rows or []
        if str(row.get("patientRef") or "").strip() and str(row.get("trialId") or "").strip()
    }


def _evaluation_rows(pair_row: dict) -> list[dict]:
    evaluations: list[dict] = []
    for row in ((pair_row.get("prefilter") or {}).get("evaluations") or []):
        enriched = deepcopy(row)
        enriched["source"] = "prefilter"
        evaluations.append(enriched)
    for row in pair_row.get("criteria") or []:
        enriched = deepcopy(row)
        enriched["source"] = "criterion"
        evaluations.append(enriched)
    return evaluations


def _lookup_expected_evaluation(pair_row: dict, expected_row: dict) -> dict | None:
    source = str(expected_row.get("source") or "").strip()
    family_id = str(expected_row.get("familyId") or "").strip()
    criterion_id = str(expected_row.get("criterionId") or "").strip()
    key = str(expected_row.get("key") or "").strip()
    for row in _evaluation_rows(pair_row):
        if source and str(row.get("source") or "").strip() != source:
            continue
        if criterion_id and str(row.get("criterionId") or "").strip() == criterion_id:
            return row
        if key and str(row.get("key") or "").strip() == key:
            return row
        if family_id and str(row.get("familyId") or "").strip() == family_id and not criterion_id and not key:
            return row
    return None


def _collect_evidence_refs(rows: list[dict]) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        for ref in row.get("evidenceRefs") or []:
            normalized = str(ref or "").strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                refs.append(normalized)
    return refs


def _build_review_note(*, overall: str, trace_summary: dict) -> str:
    normalized = str(overall or "").strip().lower()
    if normalized == "candidate":
        return "Supported deterministic and manual checks align with a candidate posture in the frozen canonical run."
    if normalized == "excluded":
        return "At least one supported criterion excludes the patient in the frozen canonical run."
    if normalized == "manual_review_required":
        return (
            "Manual-family burden is accepted as the correct posture; the pair remains runnable but requires reviewer adjudication."
        )
    if normalized == "needs_more_data":
        return "The frozen canonical run surfaces a real evidence gap rather than forcing a pass/fail decision."
    manual_count = int((trace_summary or {}).get("manualReviewRequiredCount") or 0)
    return f"Reviewed against the frozen canonical run with manual burden count = {manual_count}."


def build_micro_benchmark_gold_subset(*, pack: dict, report: dict, runtime_metadata: dict) -> dict:
    pair_map = _pair_map(report.get("pairRows") or [])
    reviewed_pairs: list[dict] = []

    for seeded in report.get("reviewSubsetSeed") or []:
        patient_ref = str(seeded.get("patientRef") or "").strip()
        trial_id = str(seeded.get("trialId") or "").strip()
        pair_row = pair_map.get((patient_ref, trial_id))
        if not pair_row:
            continue

        enriched_expected_criteria = []
        matched_evaluations: list[dict] = []
        for expected_row in seeded.get("expectedCriteria") or []:
            matched = _lookup_expected_evaluation(pair_row, expected_row)
            if matched:
                matched_evaluations.append(matched)
            enriched_expected_criteria.append(
                {
                    "source": expected_row.get("source"),
                    "key": expected_row.get("key"),
                    "criterionId": expected_row.get("criterionId"),
                    "familyId": expected_row.get("familyId"),
                    "expected": expected_row.get("expected"),
                    "actualAtFreeze": (matched or {}).get("status"),
                    "reason": (matched or {}).get("reason"),
                    "evaluator": (matched or {}).get("evaluator"),
                    "evidenceRefs": (matched or {}).get("evidenceRefs") or [],
                }
            )

        overall = str(seeded.get("expectedOverall") or pair_row.get("overall") or "").strip()
        reviewed_pairs.append(
            {
                "patientRef": patient_ref,
                "trialId": trial_id,
                "reviewStatus": "assistant_adjudicated",
                "reviewMethod": REVIEW_METHOD,
                "reviewDisposition": overall,
                "expectedOverall": overall,
                "actualOverallAtFreeze": pair_row.get("overall"),
                "manualCriteriaAccepted": overall == "manual_review_required",
                "patientSummary": {
                    "patientRef": patient_ref,
                    "patientName": pair_row.get("patientName"),
                    "linkageStatus": pair_row.get("linkageStatus"),
                    "runtimeReadiness": pair_row.get("patientRuntimeReadiness") or {},
                    "cohortMembership": pair_row.get("patientCohortMembership") or {},
                },
                "trialSummary": {
                    "trialId": trial_id,
                    "title": pair_row.get("title"),
                    "compileStatus": pair_row.get("compileStatus"),
                    "runtimeReadiness": pair_row.get("runtimeReadiness") or {},
                },
                "expectedCriteria": enriched_expected_criteria,
                "reviewNotes": _build_review_note(overall=overall, trace_summary=pair_row.get("traceSummary") or {}),
                "evidenceRefs": _collect_evidence_refs(matched_evaluations),
                "traceSummary": deepcopy(pair_row.get("traceSummary") or {}),
                "evidenceCompletenessPct": pair_row.get("evidenceCompletenessPct"),
                "runtimeReadiness": deepcopy(pair_row.get("runtimeReadiness") or {}),
                "manualBurden": int(((pair_row.get("traceSummary") or {}).get("manualReviewRequiredCount") or 0)),
            }
        )

    outcome_counts = Counter(str(row.get("expectedOverall") or "").strip() for row in reviewed_pairs)
    return {
        "benchmarkPackId": pack.get("benchmarkPackId"),
        "generatedAt": serialize_document(utcnow()),
        "gitCommit": pack.get("gitCommit"),
        "trialRuntimeTag": pack.get("trialRuntimeTag"),
        "reviewMethod": REVIEW_METHOD,
        "reviewScope": "assistant_adjudicated_supported_families_and_overall_posture",
        "contractVersions": deepcopy(runtime_metadata or {}),
        "reviewCounts": {
            "reviewedPairs": len(reviewed_pairs),
            "outcomes": dict(outcome_counts),
        },
        "reviewedPairs": reviewed_pairs,
    }


def build_micro_benchmark_review_results(*, pack: dict, report: dict, gold_subset: dict) -> dict:
    pair_map = _pair_map(report.get("pairRows") or [])
    family_counts: dict[str, dict[str, int]] = {}
    mismatches: list[dict] = []
    reviewed_pair_summaries: list[dict] = []

    reviewed_pairs = gold_subset.get("reviewedPairs") or []
    overall_matches = 0
    false_candidate_count = 0
    false_exclusion_count = 0
    manual_review_matches = 0
    total_expected_criteria = 0
    matched_expected_criteria = 0
    evidence_ref_complete_pairs = 0
    trace_complete_pairs = 0
    decision_vocabulary_correct_pairs = 0

    for reviewed in reviewed_pairs:
        patient_ref = str(reviewed.get("patientRef") or "").strip()
        trial_id = str(reviewed.get("trialId") or "").strip()
        pair_row = pair_map.get((patient_ref, trial_id))
        if not pair_row:
            mismatches.append(
                {
                    "patientRef": patient_ref,
                    "trialId": trial_id,
                    "reason": "pair_missing_from_runtime_results",
                }
            )
            continue

        expected_overall = str(reviewed.get("expectedOverall") or "").strip()
        actual_overall = str(pair_row.get("overall") or "").strip()
        overall_agreement = expected_overall == actual_overall
        if overall_agreement:
            overall_matches += 1
        if actual_overall == "candidate" and expected_overall != "candidate":
            false_candidate_count += 1
        if actual_overall == "excluded" and expected_overall != "excluded":
            false_exclusion_count += 1
        if expected_overall == "manual_review_required" and actual_overall == "manual_review_required":
            manual_review_matches += 1

        criterion_rows = reviewed.get("expectedCriteria") or []
        criterion_agreement_count = 0
        for criterion in criterion_rows:
            total_expected_criteria += 1
            family_id = str(criterion.get("familyId") or "").strip() or "unknown"
            family_bucket = family_counts.setdefault(
                family_id,
                {
                    "expectedCount": 0,
                    "agreementCount": 0,
                },
            )
            family_bucket["expectedCount"] += 1
            actual = _lookup_expected_evaluation(pair_row, criterion) or {}
            actual_status = str(actual.get("status") or "").strip()
            expected_status = str(criterion.get("expected") or "").strip()
            if actual_status == expected_status and actual_status:
                matched_expected_criteria += 1
                criterion_agreement_count += 1
                family_bucket["agreementCount"] += 1

        if reviewed.get("evidenceRefs"):
            evidence_ref_complete_pairs += 1
        if _evaluation_rows(pair_row):
            trace_complete_pairs += 1

        statuses = {str(pair_row.get("overall") or "").strip()}
        statuses.update(str(row.get("status") or "").strip() for row in _evaluation_rows(pair_row))
        if (
            str(pair_row.get("overall") or "").strip() in ALLOWED_OVERALL_OUTCOMES
            and all(status in ALLOWED_CRITERION_OUTCOMES or status in ALLOWED_OVERALL_OUTCOMES for status in statuses if status)
        ):
            decision_vocabulary_correct_pairs += 1

        if not overall_agreement or criterion_agreement_count != len(criterion_rows):
            mismatches.append(
                {
                    "patientRef": patient_ref,
                    "trialId": trial_id,
                    "expectedOverall": expected_overall,
                    "actualOverall": actual_overall,
                    "criterionAgreementCount": criterion_agreement_count,
                    "criterionExpectedCount": len(criterion_rows),
                }
            )

        reviewed_pair_summaries.append(
            {
                "patientRef": patient_ref,
                "trialId": trial_id,
                "expectedOverall": expected_overall,
                "actualOverall": actual_overall,
                "overallAgreement": overall_agreement,
                "criterionAgreementCount": criterion_agreement_count,
                "criterionExpectedCount": len(criterion_rows),
            }
        )

    reviewed_count = len(reviewed_pairs)
    return {
        "benchmarkPackId": pack.get("benchmarkPackId"),
        "generatedAt": serialize_document(utcnow()),
        "gitCommit": pack.get("gitCommit"),
        "trialRuntimeTag": pack.get("trialRuntimeTag"),
        "reviewMethod": gold_subset.get("reviewMethod") or REVIEW_METHOD,
        "contractVersions": deepcopy(gold_subset.get("contractVersions") or {}),
        "overallMetrics": {
            "reviewedPairs": reviewed_count,
            "overallAgreementCount": overall_matches,
            "overallAgreementPct": round((overall_matches / reviewed_count) * 100.0, 1) if reviewed_count else 0.0,
            "falseCandidateCount": false_candidate_count,
            "falseExclusionCount": false_exclusion_count,
            "manualReviewAgreementCount": manual_review_matches,
            "manualReviewAgreementPct": round(
                (manual_review_matches / max(1, sum(1 for row in reviewed_pairs if row.get("expectedOverall") == "manual_review_required"))) * 100.0,
                1,
            )
            if reviewed_pairs
            else 0.0,
        },
        "criterionMetrics": {
            "expectedCriterionCount": total_expected_criteria,
            "criterionAgreementCount": matched_expected_criteria,
            "criterionAgreementPct": round((matched_expected_criteria / total_expected_criteria) * 100.0, 1)
            if total_expected_criteria
            else 0.0,
            "familyAgreement": [
                {
                    "familyId": family_id,
                    "expectedCount": values["expectedCount"],
                    "agreementCount": values["agreementCount"],
                    "agreementPct": round((values["agreementCount"] / values["expectedCount"]) * 100.0, 1)
                    if values["expectedCount"]
                    else 0.0,
                }
                for family_id, values in sorted(family_counts.items())
            ],
        },
        "explanationQuality": {
            "pairsWithEvidenceRefsPct": round((evidence_ref_complete_pairs / reviewed_count) * 100.0, 1) if reviewed_count else 0.0,
            "criterionTraceCompletenessPct": round((trace_complete_pairs / reviewed_count) * 100.0, 1) if reviewed_count else 0.0,
            "decisionVocabularyCorrectPct": round((decision_vocabulary_correct_pairs / reviewed_count) * 100.0, 1)
            if reviewed_count
            else 0.0,
        },
        "manualFamilyBurden": {
            "averageManualReviewBurden": round(
                mean([float((row.get("traceSummary") or {}).get("manualReviewRequiredCount") or 0) for row in reviewed_pairs]),
                2,
            )
            if reviewed_pairs
            else 0.0,
        },
        "reviewedPairs": reviewed_pair_summaries,
        "mismatches": mismatches,
    }


def _showcase_priority(row: dict) -> tuple[Any, ...]:
    trace = row.get("traceSummary") or {}
    return (
        -1 * float(row.get("evidenceCompletenessPct") or 0.0),
        int(trace.get("manualReviewRequiredCount") or 0),
        int(trace.get("unknownCount") or 0),
    )


def _showcase_explanation_rows(pair_row: dict, *, outcome: str, limit: int = 6) -> list[dict]:
    rows = _evaluation_rows(pair_row)

    def sort_key(row: dict) -> tuple[Any, ...]:
        status = str(row.get("status") or "").strip().lower()
        if outcome == "excluded":
            priority = 0 if status == "not_met" else 1 if status == "blocked_by_linkage_conflict" else 2 if status == "manual_review_required" else 3
        elif outcome == "manual_review_required":
            priority = 0 if status == "manual_review_required" else 1 if status == "unknown" else 2
        else:
            priority = 0 if status == "met" else 1 if status == "not_patient_rule" else 2
        return (priority, str(row.get("familyId") or ""), str(row.get("criterionId") or row.get("key") or ""))

    rows.sort(key=sort_key)
    return [
        {
            "source": row.get("source"),
            "familyId": row.get("familyId"),
            "criterionId": row.get("criterionId"),
            "key": row.get("key"),
            "status": row.get("status"),
            "reason": row.get("reason"),
            "evaluator": row.get("evaluator"),
            "evidenceRefs": (row.get("evidenceRefs") or [])[:5],
        }
        for row in rows[:limit]
    ]


def _showcase_note(outcome: str) -> str:
    normalized = str(outcome or "").strip().lower()
    if normalized == "candidate":
        return "Shows a clean canonical pass on currently supported core families."
    if normalized == "excluded":
        return "Shows a clear deterministic exclusion with evidence trace."
    if normalized == "manual_review_required":
        return "Shows that manual-family burden is surfaced explicitly rather than hidden behind a score."
    return "Representative canonical benchmark case."


def build_micro_benchmark_showcase(*, pack: dict, report: dict, gold_subset: dict) -> dict:
    pair_map = _pair_map(report.get("pairRows") or [])
    reviewed_pairs = gold_subset.get("reviewedPairs") or []
    cases: dict[str, dict] = {}

    for outcome in ("candidate", "excluded", "manual_review_required"):
        candidates = [row for row in reviewed_pairs if str(row.get("expectedOverall") or "").strip() == outcome]
        if not candidates:
            continue
        candidates.sort(key=lambda row: _showcase_priority(pair_map.get((str(row.get("patientRef") or "").strip(), str(row.get("trialId") or "").strip())) or {}))
        reviewed = candidates[0]
        pair_row = pair_map.get((str(reviewed.get("patientRef") or "").strip(), str(reviewed.get("trialId") or "").strip()))
        if not pair_row:
            continue
        cases[outcome] = {
            "showcaseCase": outcome,
            "patientRef": reviewed.get("patientRef"),
            "trialId": reviewed.get("trialId"),
            "direction": "patient_to_trials",
            "populationMode": "authoritative_oncology",
            "benchmarkPackId": pack.get("benchmarkPackId"),
            "patientSummary": deepcopy(reviewed.get("patientSummary") or {}),
            "trialSummary": deepcopy(reviewed.get("trialSummary") or {}),
            "overallDecision": reviewed.get("expectedOverall"),
            "whyThisShowcase": _showcase_note(outcome),
            "canonicalTreeExcerpt": _showcase_explanation_rows(pair_row, outcome=outcome, limit=4),
            "criterionLevelExplanation": _showcase_explanation_rows(pair_row, outcome=outcome, limit=8),
            "reviewNotes": reviewed.get("reviewNotes"),
            "evidenceCompletenessPct": pair_row.get("evidenceCompletenessPct"),
            "traceSummary": deepcopy(pair_row.get("traceSummary") or {}),
        }

    return {
        "benchmarkPackId": pack.get("benchmarkPackId"),
        "generatedAt": serialize_document(utcnow()),
        "gitCommit": pack.get("gitCommit"),
        "trialRuntimeTag": pack.get("trialRuntimeTag"),
        "reviewMethod": gold_subset.get("reviewMethod") or REVIEW_METHOD,
        "contractVersions": deepcopy(gold_subset.get("contractVersions") or {}),
        "cases": cases,
    }


def build_micro_benchmark_external_review_pack(
    *,
    pack: dict,
    report: dict,
    gold_subset: dict,
    review_results: dict,
    showcase_cases: dict,
) -> dict:
    pair_map = _pair_map(report.get("pairRows") or [])
    showcase_lookup = {}
    for outcome, case in (showcase_cases.get("cases") or {}).items():
        patient_ref = str(case.get("patientRef") or "").strip()
        trial_id = str(case.get("trialId") or "").strip()
        if patient_ref and trial_id:
            showcase_lookup[(patient_ref, trial_id)] = outcome

    review_pairs: list[dict] = []
    for reviewed in gold_subset.get("reviewedPairs") or []:
        patient_ref = str(reviewed.get("patientRef") or "").strip()
        trial_id = str(reviewed.get("trialId") or "").strip()
        pair_row = pair_map.get((patient_ref, trial_id)) or {}
        review_pairs.append(
            {
                "pairId": f"{patient_ref}::{trial_id}",
                "patientRef": patient_ref,
                "trialId": trial_id,
                "showcaseCase": showcase_lookup.get((patient_ref, trial_id)),
                "expectedOverall": reviewed.get("expectedOverall"),
                "actualOverallAtFreeze": reviewed.get("actualOverallAtFreeze"),
                "reviewDisposition": reviewed.get("reviewDisposition"),
                "reviewStatus": "assistant_adjudicated_internal",
                "reviewMethod": REVIEW_METHOD,
                "manualCriteriaAccepted": bool(reviewed.get("manualCriteriaAccepted")),
                "patientSummary": deepcopy(reviewed.get("patientSummary") or {}),
                "trialSummary": deepcopy(reviewed.get("trialSummary") or {}),
                "supportedCriteria": deepcopy(reviewed.get("expectedCriteria") or []),
                "manualFamilySummary": {
                    "manualBurden": int(reviewed.get("manualBurden") or 0),
                    "traceSummary": deepcopy(reviewed.get("traceSummary") or {}),
                    "runtimeState": ((reviewed.get("trialSummary") or {}).get("runtimeReadiness") or {}).get("runtimeState"),
                },
                "keyEvidenceRefs": list(reviewed.get("evidenceRefs") or []),
                "criterionLevelExplanation": _showcase_explanation_rows(pair_row, outcome=reviewed.get("expectedOverall"), limit=8),
                "reviewNotes": reviewed.get("reviewNotes"),
                "reviewerFields": {
                    "agree": "",
                    "reviewerLabel": "",
                    "reviewerNotes": "",
                    "supportedCriteriaDisagreements": "",
                    "manualFamilyComments": "",
                },
            }
        )

    return {
        "benchmarkPackId": pack.get("benchmarkPackId"),
        "generatedAt": serialize_document(utcnow()),
        "gitCommit": pack.get("gitCommit"),
        "trialRuntimeTag": pack.get("trialRuntimeTag"),
        "reviewPackLabel": REVIEW_PACK_LABEL,
        "futureReviewPackLabel": SME_REVIEW_LABEL,
        "caveat": "assistant_adjudicated_internal_gold_subset_v1 is internally derived from the frozen canonical runtime and is not independent SME clinical truth.",
        "workspaceVersion": deepcopy(pack.get("workspaceVersion") or {}),
        "contractVersions": deepcopy(gold_subset.get("contractVersions") or {}),
        "runtimeMetadata": deepcopy(pack.get("runtimeMetadata") or {}),
        "packSummary": {
            "patientCount": len(pack.get("patients") or []),
            "trialCount": len(pack.get("trials") or []),
            "pairCount": int(pack.get("pairCount") or 0),
            "reviewedPairCount": len(review_pairs),
        },
        "reviewResultsSummary": deepcopy(
            {
                "overallMetrics": review_results.get("overallMetrics") or {},
                "criterionMetrics": review_results.get("criterionMetrics") or {},
                "explanationQuality": review_results.get("explanationQuality") or {},
            }
        ),
        "showcaseCaseKeys": sorted((showcase_cases.get("cases") or {}).keys()),
        "reviewPairs": review_pairs,
    }


def build_micro_benchmark_review_sheet_rows(*, external_review_pack: dict) -> list[dict]:
    rows: list[dict] = []
    for pair in external_review_pack.get("reviewPairs") or []:
        supported_families = sorted(
            {
                str(row.get("familyId") or "").strip()
                for row in pair.get("supportedCriteria") or []
                if str(row.get("familyId") or "").strip()
            }
        )
        rows.append(
            {
                "pair_id": pair.get("pairId"),
                "benchmark_pack_id": external_review_pack.get("benchmarkPackId"),
                "patient_ref": pair.get("patientRef"),
                "trial_id": pair.get("trialId"),
                "showcase_case": pair.get("showcaseCase") or "",
                "expected_overall": pair.get("expectedOverall"),
                "actual_overall_at_freeze": pair.get("actualOverallAtFreeze"),
                "assistant_review_disposition": pair.get("reviewDisposition"),
                "assistant_review_method": pair.get("reviewMethod"),
                "supported_family_ids": " | ".join(supported_families),
                "manual_burden": pair.get("manualFamilySummary", {}).get("manualBurden"),
                "agree": "",
                "sme_overall": "",
                "manual_review_appropriate": "",
                "disagreement_taxonomy": "",
                "reviewer_label": "",
                "reviewer_notes": "",
                "supported_criteria_disagreements": "",
                "manual_family_comments": "",
            }
        )
    return rows


def render_review_sheet_csv(rows: list[dict]) -> str:
    if not rows:
        return ""
    output = io.StringIO()
    fieldnames = list(rows[0].keys())
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_bool_flag(value: Any) -> bool | None:
    normalized = _normalize_text(value).lower()
    if normalized in {"yes", "y", "true", "1", "agree"}:
        return True
    if normalized in {"no", "n", "false", "0", "disagree"}:
        return False
    return None


def _normalize_disagreement_taxonomy(value: Any) -> str:
    normalized = _normalize_text(value).lower().replace(" ", "_")
    if normalized in SME_DISAGREEMENT_TAXONOMY:
        return normalized
    return ""


def _infer_disagreement_taxonomy(row: dict) -> str:
    explicit = _normalize_disagreement_taxonomy(row.get("disagreement_taxonomy") or row.get("disagreementTaxonomy"))
    if explicit:
        return explicit

    reviewer_label = _normalize_text(row.get("reviewer_label") or row.get("reviewerLabel")).lower()
    reviewer_notes = _normalize_text(row.get("reviewer_notes") or row.get("reviewerNotes")).lower()
    supported = _normalize_text(row.get("supported_criteria_disagreements") or row.get("supportedCriteriaDisagreements")).lower()
    manual_comments = _normalize_text(row.get("manual_family_comments") or row.get("manualFamilyComments")).lower()
    combined = " ".join(filter(None, [reviewer_label, reviewer_notes, supported, manual_comments]))

    if supported:
        return "supported_family_evaluator_issue"
    if manual_comments:
        return "manual_family_classification_issue"
    if any(token in combined for token in ["extract", "evidence", "missing record", "patient fact"]):
        return "patient_evidence_extraction_issue"
    if any(token in combined for token in ["ambig", "uncertain", "unsure", "not sure"]):
        return "reviewer_uncertainty"
    if any(token in combined for token in ["source ambiguity", "insufficient evidence", "not enough evidence", "unclear source"]):
        return "source_ambiguity_or_not_enough_evidence"
    if any(token in combined for token in ["label", "benchmark", "expected overall"]):
        return "benchmark_labeling_issue"
    return "benchmark_labeling_issue"


def parse_micro_benchmark_review_sheet(csv_text: str) -> list[dict]:
    if not _normalize_text(csv_text):
        return []

    reader = csv.DictReader(io.StringIO(csv_text))
    rows: list[dict] = []
    for raw_row in reader:
        row = {str(key or ""): _normalize_text(value) for key, value in (raw_row or {}).items()}
        pair_id = _normalize_text(row.get("pair_id"))
        patient_ref = _normalize_text(row.get("patient_ref"))
        trial_id = _normalize_text(row.get("trial_id"))
        if not pair_id and patient_ref and trial_id:
            pair_id = f"{patient_ref}::{trial_id}"
        if not pair_id:
            continue

        agree = _normalize_bool_flag(row.get("agree"))
        manual_review_appropriate = _normalize_bool_flag(row.get("manual_review_appropriate"))
        reviewer_label = _normalize_text(row.get("reviewer_label"))
        reviewer_notes = _normalize_text(row.get("reviewer_notes"))
        supported_criteria_disagreements = _normalize_text(row.get("supported_criteria_disagreements"))
        manual_family_comments = _normalize_text(row.get("manual_family_comments"))
        sme_overall = _normalize_text(row.get("sme_overall"))

        completed = any(
            [
                agree is not None,
                bool(sme_overall),
                bool(reviewer_label),
                bool(reviewer_notes),
                bool(supported_criteria_disagreements),
                bool(manual_family_comments),
            ]
        )

        rows.append(
            {
                "pairId": pair_id,
                "benchmarkPackId": _normalize_text(row.get("benchmark_pack_id")),
                "patientRef": patient_ref,
                "trialId": trial_id,
                "showcaseCase": _normalize_text(row.get("showcase_case")),
                "expectedOverall": _normalize_text(row.get("expected_overall")),
                "actualOverallAtFreeze": _normalize_text(row.get("actual_overall_at_freeze")),
                "assistantReviewDisposition": _normalize_text(row.get("assistant_review_disposition")),
                "assistantReviewMethod": _normalize_text(row.get("assistant_review_method")),
                "supportedFamilyIds": [item.strip() for item in _normalize_text(row.get("supported_family_ids")).split("|") if item.strip()],
                "manualBurden": _normalize_text(row.get("manual_burden")),
                "agree": agree,
                "smeOverall": sme_overall,
                "manualReviewAppropriate": manual_review_appropriate,
                "reviewerLabel": reviewer_label,
                "reviewerNotes": reviewer_notes,
                "supportedCriteriaDisagreements": supported_criteria_disagreements,
                "manualFamilyComments": manual_family_comments,
                "disagreementTaxonomy": _normalize_disagreement_taxonomy(row.get("disagreement_taxonomy")),
                "completed": completed,
            }
        )
    return rows


def build_micro_benchmark_sme_review_results(*, external_review_pack: dict, review_sheet_rows: list[dict]) -> dict:
    review_pair_map = {
        _normalize_text(row.get("pairId")): row
        for row in (external_review_pack.get("reviewPairs") or [])
        if _normalize_text(row.get("pairId"))
    }
    review_sheet_map = {
        _normalize_text(row.get("pairId")): row
        for row in review_sheet_rows or []
        if _normalize_text(row.get("pairId"))
    }

    pair_results: list[dict] = []
    disagreement_counter = Counter()
    total_reviewable_pairs = len(review_pair_map)
    completed_reviews = 0
    overall_agreement_count = 0
    false_candidate_count = 0
    false_exclusion_count = 0
    manual_review_expected_count = 0
    manual_review_agreement_count = 0
    supported_disagreement_count = 0

    for pair_id, review_pair in review_pair_map.items():
        review_row = review_sheet_map.get(pair_id)
        completed = bool(review_row and review_row.get("completed"))
        expected_overall = _normalize_text(review_pair.get("expectedOverall"))
        runtime_overall = _normalize_text(review_pair.get("actualOverallAtFreeze"))
        if expected_overall == "manual_review_required":
            manual_review_expected_count += 1

        sme_overall = _normalize_text((review_row or {}).get("smeOverall"))
        agree = (review_row or {}).get("agree") if review_row else None
        if agree is None and completed and sme_overall:
            agree = sme_overall == runtime_overall

        taxonomy = ""
        if completed:
            completed_reviews += 1
            if agree is True:
                overall_agreement_count += 1
            else:
                taxonomy = _infer_disagreement_taxonomy(review_row or {})
                disagreement_counter[taxonomy] += 1
                if runtime_overall == "candidate":
                    false_candidate_count += 1
                if runtime_overall == "excluded":
                    false_exclusion_count += 1
            if expected_overall == "manual_review_required":
                manual_ok = (review_row or {}).get("manualReviewAppropriate")
                if manual_ok is True or agree is True:
                    manual_review_agreement_count += 1
            if _normalize_text((review_row or {}).get("supportedCriteriaDisagreements")):
                supported_disagreement_count += 1

        pair_results.append(
            {
                "pairId": pair_id,
                "patientRef": review_pair.get("patientRef"),
                "trialId": review_pair.get("trialId"),
                "showcaseCase": review_pair.get("showcaseCase"),
                "completed": completed,
                "status": "reviewed" if completed else "pending_sme_review",
                "runtimeOverallAtFreeze": runtime_overall,
                "assistantExpectedOverall": expected_overall,
                "smeOverall": sme_overall or None,
                "agree": agree,
                "manualReviewAppropriate": (review_row or {}).get("manualReviewAppropriate") if review_row else None,
                "disagreementTaxonomy": taxonomy or None,
                "reviewerLabel": (review_row or {}).get("reviewerLabel") if review_row else "",
                "reviewerNotes": (review_row or {}).get("reviewerNotes") if review_row else "",
                "supportedCriteriaDisagreements": (review_row or {}).get("supportedCriteriaDisagreements") if review_row else "",
                "manualFamilyComments": (review_row or {}).get("manualFamilyComments") if review_row else "",
                "manualBurden": review_pair.get("manualFamilySummary", {}).get("manualBurden"),
            }
        )

    pending_reviews = total_reviewable_pairs - completed_reviews

    return {
        "benchmarkPackId": external_review_pack.get("benchmarkPackId"),
        "generatedAt": serialize_document(utcnow()),
        "reviewPackLabel": SME_REVIEW_LABEL,
        "sourceReviewPackLabel": external_review_pack.get("reviewPackLabel"),
        "sourceBenchmarkPackId": external_review_pack.get("benchmarkPackId"),
        "reviewStatus": "completed" if pending_reviews == 0 and total_reviewable_pairs > 0 else "pending_external_review",
        "workspaceVersion": deepcopy(external_review_pack.get("workspaceVersion") or {}),
        "contractVersions": deepcopy(external_review_pack.get("contractVersions") or {}),
        "summary": {
            "totalReviewablePairs": total_reviewable_pairs,
            "completedReviews": completed_reviews,
            "pendingReviews": pending_reviews,
            "overallAgreementCount": overall_agreement_count,
            "overallAgreementPct": round((overall_agreement_count / completed_reviews) * 100.0, 1) if completed_reviews else 0.0,
            "falseCandidateCount": false_candidate_count,
            "falseExclusionCount": false_exclusion_count,
            "manualReviewAgreementCount": manual_review_agreement_count,
            "manualReviewAgreementPct": round((manual_review_agreement_count / manual_review_expected_count) * 100.0, 1) if manual_review_expected_count else 0.0,
            "supportedCriteriaDisagreementCount": supported_disagreement_count,
        },
        "disagreementTaxonomy": [
            {
                "taxonomy": taxonomy,
                "count": count,
            }
            for taxonomy, count in disagreement_counter.most_common()
        ],
        "pairResults": pair_results,
    }


def _load_json_artifact(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return serialize_document(json.loads(path.read_text(encoding="utf-8")))


def _load_text_artifact(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return path.read_text(encoding="utf-8")


def _try_load_json_artifact(path: Path) -> dict | None:
    if not path.exists():
        return None
    return _load_json_artifact(path)


def load_latest_micro_benchmark_bundle() -> dict:
    pack = _load_json_artifact(MICRO_BENCHMARK_LATEST_PATHS["pack"])
    review_results = _load_json_artifact(MICRO_BENCHMARK_LATEST_PATHS["review_results"])
    showcase = _load_json_artifact(MICRO_BENCHMARK_LATEST_PATHS["showcase"])
    external_review_pack = _load_json_artifact(MICRO_BENCHMARK_LATEST_PATHS["external_review_pack"])
    sme_review_results = _try_load_json_artifact(MICRO_BENCHMARK_LATEST_PATHS["sme_review_results"])

    return {
        "benchmarkPackId": pack.get("benchmarkPackId"),
        "generatedAt": serialize_document(utcnow()),
        "pack": pack,
        "reviewResults": review_results,
        "showcase": showcase,
        "externalReviewPack": external_review_pack,
        "smeReviewResults": sme_review_results,
        "artifactPaths": {
            key: str(path)
            for key, path in MICRO_BENCHMARK_LATEST_PATHS.items()
        },
        "artifactAvailability": {
            key: path.exists()
            for key, path in MICRO_BENCHMARK_LATEST_PATHS.items()
        },
        "currentCaveat": external_review_pack.get("caveat"),
    }


def load_latest_micro_benchmark_review_sheet() -> str:
    return _load_text_artifact(MICRO_BENCHMARK_LATEST_PATHS["review_sheet"])
