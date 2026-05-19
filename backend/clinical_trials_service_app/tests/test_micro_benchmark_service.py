from __future__ import annotations

import unittest

from app.services.micro_benchmark_service import (
    build_benchmark_runtime_metadata,
    build_micro_benchmark_external_review_pack,
    build_micro_benchmark_gold_subset,
    build_micro_benchmark_pack,
    build_micro_benchmark_report,
    build_micro_benchmark_review_results,
    build_micro_benchmark_review_sheet_rows,
    build_micro_benchmark_sme_review_results,
    build_micro_benchmark_showcase,
    parse_micro_benchmark_review_sheet,
    render_review_sheet_csv,
)


class ClinicalTrialsMicroBenchmarkServiceTests(unittest.TestCase):
    def test_build_micro_benchmark_pack_uses_current_match_ready_patients_and_runtime_ready_trials(self) -> None:
        proof_report = {
            "generatedAt": "2026-04-04T18:00:00Z",
            "gitCommit": "abc123",
            "trialRuntimeTag": "clinical-trials-canonical-runtime",
            "query": {
                "condition": "breast cancer stage ii stage iii locally advanced",
                "phase": "PHASE3",
                "queryProfile": {
                    "name": "stage_ii_iii_breast_phase3",
                    "label": "Stage II/III Breast Cancer Phase 3",
                    "targetWindowGoal": "stage_ii_iii_or_locally_advanced",
                },
            },
            "matchReadySubset": {
                "patientRefs": ["Patient/1", "Patient/2", "Patient/3", "Patient/4"],
            },
            "trialSummary": {
                "runtimeReadyTrialIds": ["NCT1", "NCT2", "NCT3", "NCT4", "NCT5"],
            },
            "populationSummary": {"authoritativeBreastOncology": 4},
            "stageScopeSummary": {
                "recommendedTargetWindow": "stage_ii_iii_or_locally_advanced",
                "recommendedTargetWindowSummary": {
                    "authoritativePatients": 4,
                    "runtimeReadyTrials": 5,
                },
            },
        }

        pack = build_micro_benchmark_pack(
            proof_report=proof_report,
            benchmark_pack_id="breast_micro_benchmark_v1",
            source_proof_path="/tmp/proof.json",
        )

        self.assertEqual(pack["benchmarkPackId"], "breast_micro_benchmark_v1")
        self.assertEqual(pack["patients"], ["Patient/1", "Patient/2", "Patient/3", "Patient/4"])
        self.assertEqual(pack["trials"], ["NCT1", "NCT2", "NCT3", "NCT4", "NCT5"])
        self.assertEqual(pack["pairCount"], 20)
        self.assertEqual(pack["queryProfile"]["name"], "stage_ii_iii_breast_phase3")
        self.assertEqual(pack["sourceProof"]["queryProfile"]["targetWindowGoal"], "stage_ii_iii_or_locally_advanced")
        self.assertEqual(pack["stageScopeSummary"]["recommendedTargetWindow"], "stage_ii_iii_or_locally_advanced")
        self.assertTrue(pack["microBenchmarkGate"]["ready"])
        self.assertFalse(pack["scaleBenchmarkGate"]["ready"])

    def test_build_micro_benchmark_report_creates_review_subset_and_showcases(self) -> None:
        pack = {
            "benchmarkPackId": "breast_micro_benchmark_v1",
            "gitCommit": "abc123",
            "trialRuntimeTag": "clinical-trials-canonical-runtime",
            "patients": ["Patient/1"],
            "trials": ["NCT1", "NCT2", "NCT3", "NCT4"],
            "pairCount": 4,
            "microBenchmarkGate": {"ready": True},
            "scaleBenchmarkGate": {"ready": False},
        }
        patient_runs = [
            {
                "_id": "match_pat_1",
                "patientRef": "Patient/1",
                "results": [
                    {
                        "trialId": "NCT1",
                        "overall": "candidate",
                        "title": "Trial 1",
                        "compileStatus": "ready_auto",
                        "runtimeReadiness": {"runtimeState": "runtime_ready_deterministic"},
                        "evidenceCompletenessPct": 100.0,
                        "traceSummary": {
                            "manualReviewRequiredCount": 0,
                            "unknownCount": 0,
                            "fillableByQuestionnaireCount": 0,
                        },
                        "prefilter": {"evaluations": [{"key": "demographics.sex.any_of", "familyId": "demographics.sex.allowed", "status": "met"}]},
                        "criteria": [{"criterionId": "c1", "familyId": "oncology.primary_cancer.required", "status": "met"}],
                        "gaps": [],
                    },
                    {
                        "trialId": "NCT2",
                        "overall": "excluded",
                        "title": "Trial 2",
                        "compileStatus": "ready_with_review",
                        "runtimeReadiness": {"runtimeState": "runtime_ready_with_manual"},
                        "evidenceCompletenessPct": 95.0,
                        "traceSummary": {
                            "manualReviewRequiredCount": 0,
                            "unknownCount": 0,
                            "fillableByQuestionnaireCount": 0,
                        },
                        "prefilter": {"evaluations": []},
                        "criteria": [{"criterionId": "c2", "familyId": "biomarkers.HER2.status.required", "status": "not_met"}],
                        "gaps": [],
                    },
                    {
                        "trialId": "NCT3",
                        "overall": "needs_more_data",
                        "title": "Trial 3",
                        "compileStatus": "ready_with_review",
                        "runtimeReadiness": {"runtimeState": "runtime_ready_with_manual"},
                        "evidenceCompletenessPct": 50.0,
                        "traceSummary": {
                            "manualReviewRequiredCount": 0,
                            "unknownCount": 1,
                            "fillableByQuestionnaireCount": 0,
                        },
                        "prefilter": {"evaluations": []},
                        "criteria": [{"criterionId": "c3", "familyId": "performance.ecog.max", "status": "unknown"}],
                        "gaps": [{"feature": "ECOG"}],
                    },
                    {
                        "trialId": "NCT4",
                        "overall": "manual_review_required",
                        "title": "Trial 4",
                        "compileStatus": "ready_with_review",
                        "runtimeReadiness": {"runtimeState": "runtime_ready_with_manual"},
                        "evidenceCompletenessPct": 75.0,
                        "traceSummary": {
                            "manualReviewRequiredCount": 2,
                            "unknownCount": 0,
                            "fillableByQuestionnaireCount": 0,
                        },
                        "prefilter": {"evaluations": []},
                        "criteria": [{"criterionId": "c4", "familyId": "manual.stage_extent_context", "status": "manual_review_required"}],
                        "gaps": [],
                    },
                ],
            }
        ]
        trial_runs = [
            {
                "_id": "match_trial_1",
                "trialId": "NCT1",
                "results": [{"patientRef": "Patient/1", "overall": "candidate"}],
            },
            {
                "_id": "match_trial_2",
                "trialId": "NCT2",
                "results": [{"patientRef": "Patient/1", "overall": "excluded"}],
            },
            {
                "_id": "match_trial_3",
                "trialId": "NCT3",
                "results": [{"patientRef": "Patient/1", "overall": "needs_more_data"}],
            },
            {
                "_id": "match_trial_4",
                "trialId": "NCT4",
                "results": [{"patientRef": "Patient/1", "overall": "manual_review_required"}],
            },
        ]

        report = build_micro_benchmark_report(pack=pack, patient_runs=patient_runs, trial_runs=trial_runs)

        self.assertEqual(report["summary"]["totalPairs"], 4)
        self.assertEqual(report["summary"]["overallCounts"]["candidate"], 1)
        self.assertEqual(report["summary"]["directionalMismatchCount"], 0)
        self.assertEqual(len(report["reviewSubsetSeed"]), 4)
        self.assertIn("candidate", report["showcaseCases"])
        self.assertIn("manual_review_required", report["showcaseCases"])

    def test_gold_subset_review_results_and_showcase_artifacts_are_built_from_frozen_report(self) -> None:
        pack = {
            "benchmarkPackId": "breast_micro_benchmark_v1",
            "gitCommit": "abc123",
            "trialRuntimeTag": "clinical-trials-canonical-runtime",
            "patients": ["Patient/1"],
            "trials": ["NCT1", "NCT2", "NCT3"],
            "pairCount": 3,
            "microBenchmarkGate": {"ready": True},
            "scaleBenchmarkGate": {"ready": False},
        }
        patient_runs = [
            {
                "_id": "match_pat_1",
                "runtimeMode": "canonical_only",
                "populationMode": "authoritative_oncology",
                "legacyBehaviorAllowed": False,
                "legacyInputsUsed": [],
                "contractVersions": {
                    "trialContractVersion": "ctgov_det_ingest_v1",
                    "patientEvidenceProfileVersion": "patient_evidence_profile_v2",
                    "patientMatchProfileVersion": "patient_match_profile_v2",
                    "ruleFamilyRegistryVersion": "2026-04-03",
                    "trialVersionIds": {"NCT1": "tv1", "NCT2": "tv2", "NCT3": "tv3"},
                },
                "patientRef": "Patient/1",
                "results": [
                    {
                        "trialId": "NCT1",
                        "overall": "candidate",
                        "title": "Trial 1",
                        "compileStatus": "ready_auto",
                        "runtimeReadiness": {"runtimeState": "runtime_ready_deterministic"},
                        "evidenceCompletenessPct": 100.0,
                        "traceSummary": {
                            "manualReviewRequiredCount": 0,
                            "unknownCount": 0,
                            "fillableByQuestionnaireCount": 0,
                        },
                        "prefilter": {"evaluations": [{"key": "demographics.sex.any_of", "familyId": "demographics.sex.allowed", "status": "met", "reason": "ok", "evidenceRefs": ["Patient/1"], "evaluator": "eval"}]},
                        "criteria": [{"criterionId": "c1", "familyId": "oncology.primary_cancer.required", "status": "met", "reason": "ok", "evidenceRefs": ["Condition/1"], "evaluator": "eval"}],
                        "gaps": [],
                    },
                    {
                        "trialId": "NCT2",
                        "overall": "excluded",
                        "title": "Trial 2",
                        "compileStatus": "ready_with_review",
                        "runtimeReadiness": {"runtimeState": "runtime_ready_with_manual"},
                        "evidenceCompletenessPct": 95.0,
                        "traceSummary": {
                            "manualReviewRequiredCount": 0,
                            "unknownCount": 0,
                            "fillableByQuestionnaireCount": 0,
                        },
                        "prefilter": {"evaluations": []},
                        "criteria": [{"criterionId": "c2", "familyId": "biomarkers.HER2.status.required", "status": "not_met", "reason": "no", "evidenceRefs": ["Observation/1"], "evaluator": "eval"}],
                        "gaps": [],
                    },
                    {
                        "trialId": "NCT3",
                        "overall": "manual_review_required",
                        "title": "Trial 3",
                        "compileStatus": "ready_with_review",
                        "runtimeReadiness": {"runtimeState": "runtime_ready_with_manual"},
                        "evidenceCompletenessPct": 75.0,
                        "traceSummary": {
                            "manualReviewRequiredCount": 2,
                            "unknownCount": 0,
                            "fillableByQuestionnaireCount": 0,
                        },
                        "prefilter": {"evaluations": []},
                        "criteria": [{"criterionId": "c3", "familyId": "manual.stage_extent_context", "status": "manual_review_required", "reason": "manual", "evidenceRefs": ["Condition/2"], "evaluator": "manual"}],
                        "gaps": [],
                    },
                ],
            }
        ]
        trial_runs = [
            {
                "_id": "match_trial_1",
                "runtimeMode": "canonical_only",
                "populationMode": "authoritative_oncology",
                "legacyBehaviorAllowed": False,
                "legacyInputsUsed": [],
                "contractVersions": {
                    "trialVersionId": "tv1",
                    "trialContractVersion": "ctgov_det_ingest_v1",
                    "ruleFamilyRegistryVersion": "2026-04-03",
                },
                "trialId": "NCT1",
                "results": [{"patientRef": "Patient/1", "patientName": "Alice Example", "overall": "candidate", "linkageStatus": "linked", "runtimeReadiness": {"benchmarkRunnable": True}, "cohortMembership": {"authoritativeBreastOncology": True}}],
            },
            {
                "_id": "match_trial_2",
                "runtimeMode": "canonical_only",
                "populationMode": "authoritative_oncology",
                "legacyBehaviorAllowed": False,
                "legacyInputsUsed": [],
                "contractVersions": {
                    "trialVersionId": "tv2",
                    "trialContractVersion": "ctgov_det_ingest_v1",
                    "ruleFamilyRegistryVersion": "2026-04-03",
                },
                "trialId": "NCT2",
                "results": [{"patientRef": "Patient/1", "patientName": "Alice Example", "overall": "excluded", "linkageStatus": "linked", "runtimeReadiness": {"benchmarkRunnable": True}, "cohortMembership": {"authoritativeBreastOncology": True}}],
            },
            {
                "_id": "match_trial_3",
                "runtimeMode": "canonical_only",
                "populationMode": "authoritative_oncology",
                "legacyBehaviorAllowed": False,
                "legacyInputsUsed": [],
                "contractVersions": {
                    "trialVersionId": "tv3",
                    "trialContractVersion": "ctgov_det_ingest_v1",
                    "ruleFamilyRegistryVersion": "2026-04-03",
                },
                "trialId": "NCT3",
                "results": [{"patientRef": "Patient/1", "patientName": "Alice Example", "overall": "manual_review_required", "linkageStatus": "linked", "runtimeReadiness": {"benchmarkRunnable": True}, "cohortMembership": {"authoritativeBreastOncology": True}}],
            },
        ]

        report = build_micro_benchmark_report(pack=pack, patient_runs=patient_runs, trial_runs=trial_runs)
        runtime_metadata = build_benchmark_runtime_metadata(
            matcher_version="git:abc123",
            patient_runs=patient_runs,
            trial_runs=trial_runs,
        )
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
        showcase = build_micro_benchmark_showcase(
            pack=pack,
            report=report,
            gold_subset=gold_subset,
        )
        external_review_pack = build_micro_benchmark_external_review_pack(
            pack=pack,
            report=report,
            gold_subset=gold_subset,
            review_results=review_results,
            showcase_cases=showcase,
        )
        review_sheet_rows = build_micro_benchmark_review_sheet_rows(
            external_review_pack=external_review_pack,
        )
        review_sheet_csv = render_review_sheet_csv(review_sheet_rows)

        self.assertEqual(runtime_metadata["runtimeMode"], "canonical_only")
        self.assertEqual(len(gold_subset["reviewedPairs"]), 3)
        self.assertEqual(review_results["overallMetrics"]["overallAgreementPct"], 100.0)
        self.assertIn("candidate", showcase["cases"])
        self.assertIn("manual_review_required", showcase["cases"])
        self.assertEqual(external_review_pack["reviewPackLabel"], "assistant_adjudicated_internal_gold_subset_v1")
        self.assertEqual(len(external_review_pack["reviewPairs"]), 3)
        self.assertEqual(len(review_sheet_rows), 3)
        self.assertIn("pair_id,benchmark_pack_id,patient_ref,trial_id", review_sheet_csv)

    def test_sme_review_results_are_scored_and_classified_from_review_sheet(self) -> None:
        external_review_pack = {
            "benchmarkPackId": "breast_micro_benchmark_v1",
            "reviewPackLabel": "assistant_adjudicated_internal_gold_subset_v1",
            "workspaceVersion": {"gitCommit": "abc123"},
            "contractVersions": {"trialContractVersion": "ctgov_det_ingest_v1"},
            "reviewPairs": [
                {
                    "pairId": "Patient/1::NCT1",
                    "patientRef": "Patient/1",
                    "trialId": "NCT1",
                    "showcaseCase": "candidate",
                    "expectedOverall": "candidate",
                    "actualOverallAtFreeze": "candidate",
                    "manualFamilySummary": {"manualBurden": 0},
                },
                {
                    "pairId": "Patient/2::NCT2",
                    "patientRef": "Patient/2",
                    "trialId": "NCT2",
                    "showcaseCase": "manual_review_required",
                    "expectedOverall": "manual_review_required",
                    "actualOverallAtFreeze": "manual_review_required",
                    "manualFamilySummary": {"manualBurden": 2},
                },
            ],
        }

        csv_text = "\n".join(
            [
                "pair_id,benchmark_pack_id,patient_ref,trial_id,showcase_case,expected_overall,actual_overall_at_freeze,assistant_review_disposition,assistant_review_method,supported_family_ids,manual_burden,agree,sme_overall,manual_review_appropriate,disagreement_taxonomy,reviewer_label,reviewer_notes,supported_criteria_disagreements,manual_family_comments",
                "Patient/1::NCT1,breast_micro_benchmark_v1,Patient/1,NCT1,candidate,candidate,candidate,candidate,assistant,demographics.age.min,0,yes,candidate,,,oncologist,Looks right,,",
                "Patient/2::NCT2,breast_micro_benchmark_v1,Patient/2,NCT2,manual_review_required,manual_review_required,manual_review_required,manual_review_required,assistant,manual.stage_extent_context,2,no,excluded,no,,oncologist,\"Manual gate should not have fired\",\"stage extent mismatch\",\"Manual burden overstated\"",
            ]
        )

        parsed_rows = parse_micro_benchmark_review_sheet(csv_text)
        results = build_micro_benchmark_sme_review_results(
            external_review_pack=external_review_pack,
            review_sheet_rows=parsed_rows,
        )

        self.assertEqual(len(parsed_rows), 2)
        self.assertEqual(results["summary"]["completedReviews"], 2)
        self.assertEqual(results["summary"]["overallAgreementCount"], 1)
        self.assertEqual(results["summary"]["overallAgreementPct"], 50.0)
        self.assertEqual(results["summary"]["manualReviewAgreementCount"], 0)
        self.assertEqual(results["summary"]["supportedCriteriaDisagreementCount"], 1)
        self.assertEqual(results["disagreementTaxonomy"][0]["taxonomy"], "supported_family_evaluator_issue")


if __name__ == "__main__":
    unittest.main()
