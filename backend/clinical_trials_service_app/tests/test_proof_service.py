from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("MONGODB_CONNECTION_STRING", "mongodb://localhost:27017")

from app.services.proof_service import build_calibration_diagnostic_report, build_corpus_proof_report, proof_service


class ClinicalTrialsProofServiceTests(unittest.TestCase):
    @patch("app.services.proof_service.collections.rule_families.find")
    @patch("app.services.proof_service.collections.trial_criteria.find")
    @patch("app.services.proof_service.collections.trials.find")
    @patch("app.services.proof_service.patient_collections.profiles.find")
    @patch("app.services.proof_service.patient_collections.facts.find")
    def test_runtime_document_loader_requests_stage_relevance_projection(
        self,
        mock_facts_find,
        mock_profiles_find,
        mock_trials_find,
        mock_criteria_find,
        mock_rule_families_find,
    ) -> None:
        class _Cursor(list):
            def sort(self, *_args, **_kwargs):
                return self

        mock_facts_find.return_value = []
        mock_profiles_find.return_value = []
        mock_trials_find.return_value = []
        mock_criteria_find.return_value = []
        mock_rule_families_find.return_value = _Cursor([])

        proof_service._load_runtime_documents(trial_ids=[], query={"phase": "PHASE3"})

        _, projection = mock_trials_find.call_args.args
        self.assertEqual(projection["stage_relevance"], 1)

    def test_corpus_proof_report_computes_population_trial_and_gate_summary(self) -> None:
        patient_facts_docs = [
            {
                "patientRef": "Patient/1",
                "patientEvidenceProfile": {
                    "resolvedFeatures": {
                        "primaryCancer": {"status": "present"},
                        "ER": {"status": "positive"},
                        "PR": {"status": "positive"},
                        "HER2": {"status": "negative"},
                        "stageGroup": {"status": "present"},
                        "ECOG": {"status": "present"},
                        "treatmentHistory": {"status": "present"},
                        "keyLabs": {"status": "present"},
                        "diseaseStatus": {"status": "unknown"},
                    }
                },
            },
            {
                "patientRef": "Patient/2",
                "patientEvidenceProfile": {
                    "resolvedFeatures": {
                        "primaryCancer": {"status": "present"},
                        "ER": {"status": "unknown"},
                        "HER2": {"status": "unknown"},
                        "treatmentHistory": {"status": "unknown"},
                        "keyLabs": {"status": "present"},
                    }
                },
            },
        ]
        patient_profile_docs = [
            {
                "patientRef": "Patient/1",
                "patientMatchProfile": {
                    "identity": {"linkageStatus": "linked", "conflictCount": 0, "identityTrust": "linked_composite"},
                    "matchFilters": {"diseaseWindow": "stage_ii_iii_or_locally_advanced"},
                    "cohortMembership": {
                        "authoritativeBreastOncology": True,
                        "retailOutreachReady": True,
                        "pharmacyPrescreen": False,
                        "screeningOnly": False,
                    },
                    "runtimeReadiness": {
                        "mode": "authoritative_oncology",
                        "runnable": True,
                        "benchmarkRunnable": True,
                        "outreachRunnable": True,
                        "linkageStatus": "linked",
                        "criticalConflictCount": 0,
                        "targetDiseaseWindow": "stage_ii_iii_or_locally_advanced",
                    },
                    "coverageSnapshot": {"coveragePct": 85.0},
                    "oncologyCohort": {
                        "candidateCategory": "confirmed_breast_oncology",
                        "matchingReadinessTier": "ready",
                    },
                },
            },
            {
                "patientRef": "Patient/2",
                "patientMatchProfile": {
                    "identity": {"linkageStatus": "linked", "conflictCount": 0, "identityTrust": "unresolved_identity"},
                    "matchFilters": {"diseaseWindow": "advanced_or_metastatic"},
                    "cohortMembership": {
                        "authoritativeBreastOncology": False,
                        "retailOutreachReady": False,
                        "pharmacyPrescreen": True,
                        "screeningOnly": False,
                    },
                    "runtimeReadiness": {
                        "mode": "pharmacy_prescreen",
                        "runnable": True,
                        "benchmarkRunnable": False,
                        "outreachRunnable": False,
                        "reasonIfBenchmarkBlocked": "readiness_tier_not_authoritative::partial",
                        "reasonIfOutreachBlocked": "not_authoritative_breast_oncology",
                        "linkageStatus": "linked",
                        "criticalConflictCount": 0,
                        "targetDiseaseWindow": "advanced_or_metastatic",
                    },
                    "coverageSnapshot": {"coveragePct": 40.0},
                    "oncologyCohort": {
                        "candidateCategory": "confirmed_breast_oncology",
                        "matchingReadinessTier": "partial",
                    },
                },
            },
        ]
        trial_docs = [
            {
                "_id": "NCT1",
                "current_version_id": "NCT1::v1",
                "compile": {"status": "ready_auto"},
                "runtimeReadiness": {
                    "runnable": True,
                    "runtimeState": "runtime_ready_deterministic",
                    "deterministicPatientImpactingCount": 4,
                    "manualPatientImpactingCount": 1,
                    "unsupportedPatientImpactingCount": 0,
                },
                "stage_relevance": {
                    "targetWindow": "stage_ii_iii_or_locally_advanced",
                    "manualReviewRequired": True,
                    "manualStageContextCount": 1,
                    "manualComplexSettingCount": 0,
                },
                "display": {"last_ingested_at": "2026-04-04T00:00:00Z"},
            },
            {
                "_id": "NCT2",
                "current_version_id": "NCT2::v1",
                "compile": {"status": "ready_with_review"},
                "runtimeReadiness": {
                    "runnable": False,
                    "reasonIfBlocked": "unsupported_patient_impacting_criteria",
                    "deterministicPatientImpactingCount": 1,
                    "manualPatientImpactingCount": 3,
                    "unsupportedPatientImpactingCount": 2,
                },
                "stage_relevance": {
                    "targetWindow": "advanced_or_metastatic",
                    "manualReviewRequired": False,
                    "manualStageContextCount": 0,
                    "manualComplexSettingCount": 0,
                },
                "display": {"last_ingested_at": "2026-04-04T00:00:00Z"},
            },
        ]
        current_criteria_docs = [
            {
                "trial_id": "NCT1",
                "version_id": "NCT1::v1",
                "node_type": "leaf",
                "section": "inclusion",
                "raw": {"text": "Minimum age 18 years"},
                "normalized": {"text": "minimum age 18 years"},
                "ancestor_headers": [],
                "compiler": {"selected_family_id": "demographics.age.min", "computability": "deterministic"},
                "review": {"status": "not_needed"},
            },
            {
                "trial_id": "NCT2",
                "version_id": "NCT2::v1",
                "node_type": "leaf",
                "section": "inclusion",
                "raw": {"text": "Adequate bone marrow function with hemoglobin >= 9 g/dL"},
                "normalized": {"text": "adequate bone marrow function with hemoglobin >= 9 g/dl"},
                "ancestor_headers": [],
                "compiler": {"selected_family_id": "biomarkers.ER.status.required", "computability": "manual_review_clinical"},
                "review": {"status": "pending"},
            },
            {
                "trial_id": "NCT2",
                "version_id": "NCT2::v1",
                "node_type": "leaf",
                "section": "exclusion",
                "raw": {"text": "Pregnant or breastfeeding women are excluded"},
                "normalized": {"text": "pregnant or breastfeeding women are excluded"},
                "ancestor_headers": [],
                "compiler": {"selected_family_id": None, "computability": "manual_review_clinical"},
                "review": {"status": "pending"},
            },
        ]
        rule_family_docs = [
            {
                "family_id": "demographics.age.min",
                "patientSupport": {
                    "supported": True,
                    "evidencePaths": ["patientEvidenceProfile.resolvedFeatures.age"],
                    "prefilterPath": "patientMatchProfile.demographics.ageYears",
                },
            },
            {
                "family_id": "biomarkers.ER.status.required",
                "patientSupport": {
                    "supported": True,
                    "evidencePaths": ["patientEvidenceProfile.resolvedFeatures.ER"],
                    "prefilterPath": "patientMatchProfile.matchFilters.erStatus",
                },
            },
        ]

        report = build_corpus_proof_report(
            patient_facts_docs=patient_facts_docs,
            patient_profile_docs=patient_profile_docs,
            trial_docs=trial_docs,
            current_criteria_docs=current_criteria_docs,
            rule_family_docs=rule_family_docs,
            query={
                "condition": "breast cancer",
                "phase": "PHASE3",
                "queryProfile": {
                    "name": "broad_breast_phase3",
                    "label": "Broad Breast Cancer Phase 3",
                },
            },
            git_commit="426e448",
            trial_runtime_tag="clinical-trials-canonical-runtime",
            patient_rebuild_summary={"ok": True},
            trial_refresh_summary={"ok": True},
        )

        self.assertEqual(report["populationSummary"]["totalPatients"], 2)
        self.assertEqual(report["populationSummary"]["authoritativeBreastOncology"], 1)
        self.assertEqual(report["populationSummary"]["retailOutreachReady"], 1)
        self.assertEqual(report["populationSummary"]["pharmacyPrescreen"], 1)
        self.assertEqual(report["query"]["queryProfile"]["name"], "broad_breast_phase3")
        self.assertEqual(report["matchReadySubset"]["count"], 1)
        self.assertEqual(report["retailOutreachSubset"]["count"], 1)
        self.assertEqual(report["trialSummary"]["totalBreastCancerTrials"], 2)
        self.assertEqual(report["trialSummary"]["runtimeReadyTrials"], 1)
        self.assertEqual(report["trialSummary"]["runtimeReadyWithManualTrials"], 0)
        self.assertEqual(report["trialSummary"]["stageSpecificTrials"], 2)
        self.assertEqual(report["trialSummary"]["stageSpecificRuntimeReadyTrials"], 1)
        self.assertEqual(report["trialSummary"]["manualStageContextTrials"], 1)
        self.assertEqual(report["blockers"]["trialRuntimeBlockers"][0]["reason"], "unsupported_patient_impacting_criteria")
        self.assertEqual(report["benchmarkCandidates"]["trialIds"], ["NCT1"])
        self.assertEqual(report["benchmarkCandidates"]["matchReadyPatientRefs"], ["Patient/1"])
        self.assertEqual(report["benchmarkCandidates"]["retailOutreachPatientRefs"], ["Patient/1"])
        self.assertEqual(report["unassignedTriage"]["totalRows"], 1)
        self.assertTrue(report["unassignedTriage"]["clusters"])
        self.assertTrue(report["unassignedTriage"]["clusters"][0]["likelyFamilyId"])
        self.assertEqual(report["benchmarkGate"]["recommendedNextPass"], "coverage_expansion")
        self.assertEqual(report["stageScopeSummary"]["recommendedTargetWindow"], "stage_ii_iii_or_locally_advanced")
        self.assertEqual(
            report["stageScopeSummary"]["recommendedTargetWindowSummary"]["runtimeReadyDeterministicTrials"],
            1,
        )
        self.assertEqual(
            report["stageScopeSummary"]["patientTargetWindows"][0]["authoritativeCount"],
            1,
        )
        self.assertEqual(
            report["stageScopeSummary"]["trialTargetWindows"][0]["runtimeReadyCount"],
            1,
        )
        self.assertEqual(
            report["stageScopeSummary"]["effectiveOverlapWindows"][0]["runtimeReadyTrials"],
            1,
        )

    def test_corpus_proof_report_uses_mixed_manual_trials_as_effective_lane_support(self) -> None:
        patient_facts_docs = [
            {
                "patientRef": "Patient/1",
                "patientEvidenceProfile": {
                    "resolvedFeatures": {
                        "primaryCancer": {"status": "present"},
                        "ER": {"status": "positive"},
                        "HER2": {"status": "negative"},
                        "treatmentHistory": {"status": "present"},
                        "keyLabs": {"status": "present"},
                    }
                },
            },
        ]
        patient_profile_docs = [
            {
                "patientRef": "Patient/1",
                "patientMatchProfile": {
                    "identity": {"linkageStatus": "linked", "conflictCount": 0, "identityTrust": "linked_composite"},
                    "matchFilters": {"diseaseWindow": "stage_ii_iii_or_locally_advanced"},
                    "cohortMembership": {
                        "authoritativeBreastOncology": True,
                        "retailOutreachReady": False,
                        "pharmacyPrescreen": False,
                        "screeningOnly": False,
                    },
                    "runtimeReadiness": {
                        "mode": "authoritative_oncology",
                        "runnable": True,
                        "benchmarkRunnable": True,
                        "outreachRunnable": False,
                        "linkageStatus": "linked",
                        "criticalConflictCount": 0,
                        "targetDiseaseWindow": "stage_ii_iii_or_locally_advanced",
                    },
                    "coverageSnapshot": {"coveragePct": 90.0},
                    "oncologyCohort": {
                        "candidateCategory": "confirmed_breast_oncology",
                        "matchingReadinessTier": "ready",
                    },
                },
            },
        ]
        trial_docs = [
            {
                "_id": "NCT-MIXED",
                "current_version_id": "NCT-MIXED::v1",
                "compile": {"status": "ready_auto"},
                "runtimeReadiness": {
                    "runnable": True,
                    "runtimeState": "runtime_ready_with_manual",
                    "deterministicPatientImpactingCount": 2,
                    "manualPatientImpactingCount": 2,
                    "unsupportedPatientImpactingCount": 0,
                },
                "stage_relevance": {
                    "targetWindow": "mixed_or_manual",
                    "manualReviewRequired": True,
                    "manualStageContextCount": 2,
                    "manualComplexSettingCount": 0,
                },
                "display": {"last_ingested_at": "2026-04-04T00:00:00Z"},
            },
        ]
        current_criteria_docs = []
        rule_family_docs = []

        report = build_corpus_proof_report(
            patient_facts_docs=patient_facts_docs,
            patient_profile_docs=patient_profile_docs,
            trial_docs=trial_docs,
            current_criteria_docs=current_criteria_docs,
            rule_family_docs=rule_family_docs,
            query={
                "condition": "breast cancer",
                "phase": "PHASE3",
                "queryProfile": {
                    "name": "stage_ii_iii_breast_phase3",
                    "label": "Stage II/III Breast Cancer Phase 3",
                    "targetWindowGoal": "stage_ii_iii_or_locally_advanced",
                },
            },
            git_commit="426e448",
            trial_runtime_tag="clinical-trials-canonical-runtime",
            patient_rebuild_summary={"ok": True},
            trial_refresh_summary={"ok": True},
        )

        exact_row = next(
            row
            for row in report["stageScopeSummary"]["overlapWindows"]
            if row["targetWindow"] == "stage_ii_iii_or_locally_advanced"
        )
        effective_row = next(
            row
            for row in report["stageScopeSummary"]["effectiveOverlapWindows"]
            if row["targetWindow"] == "stage_ii_iii_or_locally_advanced"
        )

        self.assertEqual(exact_row["trialCount"], 0)
        self.assertEqual(exact_row["runtimeReadyTrials"], 0)
        self.assertEqual(effective_row["trialCount"], 1)
        self.assertEqual(effective_row["runtimeReadyTrials"], 1)
        self.assertEqual(effective_row["mixedOrManualSupportingTrialCount"], 1)
        self.assertEqual(report["stageScopeSummary"]["recommendedTargetWindow"], "stage_ii_iii_or_locally_advanced")
        self.assertEqual(
            report["stageScopeSummary"]["recommendedTargetWindowSummary"]["supportingTrialWindows"],
            ["stage_ii_iii_or_locally_advanced", "mixed_or_manual"],
        )

    def test_calibration_diagnostic_report_surfaces_patient_and_trial_blockers(self) -> None:
        patient_facts_docs = [
            {
                "patientRef": "Patient/1",
                "patientEvidenceProfile": {
                    "resolvedFeatures": {
                        "primaryCancer": {"status": "present"},
                        "ER": {"status": "positive"},
                        "HER2": {"status": "negative"},
                        "treatmentHistory": {"status": "present"},
                    }
                },
            },
            {
                "patientRef": "Patient/2",
                "patientEvidenceProfile": {
                    "resolvedFeatures": {
                        "primaryCancer": {"status": "present"},
                        "ER": {"status": "unknown"},
                    }
                },
            },
        ]
        patient_profile_docs = [
            {
                "patientRef": "Patient/1",
                "patientMatchProfile": {
                    "identity": {"linkageStatus": "linked", "conflictCount": 0, "identityTrust": "linked_composite"},
                    "cohortMembership": {
                        "authoritativeBreastOncology": False,
                        "retailOutreachReady": False,
                        "pharmacyPrescreen": True,
                        "screeningOnly": False,
                    },
                    "runtimeReadiness": {
                        "mode": "pharmacy_prescreen",
                        "runnable": True,
                        "benchmarkRunnable": False,
                        "outreachRunnable": False,
                        "linkageStatus": "linked",
                        "criticalConflictCount": 0,
                    },
                    "coverageSnapshot": {"coveragePct": 72.0},
                    "oncologyCohort": {
                        "candidateCategory": "confirmed_breast_oncology",
                        "matchingReadinessTier": "ready",
                        "criticalMissingDomains": ["receptor_status"],
                    },
                },
            },
            {
                "patientRef": "Patient/2",
                "patientMatchProfile": {
                    "identity": {"linkageStatus": "linked", "conflictCount": 0, "identityTrust": "linked_composite"},
                    "cohortMembership": {
                        "authoritativeBreastOncology": False,
                        "retailOutreachReady": False,
                        "pharmacyPrescreen": True,
                        "screeningOnly": False,
                    },
                    "runtimeReadiness": {
                        "mode": "pharmacy_prescreen",
                        "runnable": True,
                        "benchmarkRunnable": False,
                        "outreachRunnable": False,
                        "linkageStatus": "linked",
                        "criticalConflictCount": 0,
                    },
                    "coverageSnapshot": {"coveragePct": 55.0},
                    "oncologyCohort": {
                        "candidateCategory": "confirmed_breast_oncology",
                        "matchingReadinessTier": "partial",
                        "criticalMissingDomains": ["receptor_status", "stage_or_extent"],
                    },
                },
            },
        ]
        trial_docs = [
            {
                "_id": "NCT1",
                "current_version_id": "NCT1::v1",
                "compile": {"status": "ready_with_review"},
                "runtimeReadiness": {
                    "runnable": False,
                    "reasonIfBlocked": "exclusion criteria still contain unresolved deterministic gaps",
                    "deterministicPatientImpactingCount": 2,
                    "manualPatientImpactingCount": 3,
                    "unsupportedPatientImpactingCount": 0,
                    "hasExclusionParseGap": True,
                },
                "display": {"last_ingested_at": "2026-04-04T00:00:00Z"},
            }
        ]
        current_criteria_docs = [
            {
                "trial_id": "NCT1",
                "version_id": "NCT1::v1",
                "node_type": "leaf",
                "section": "inclusion",
                "raw": {"text": "Confirmed breast cancer"},
                "normalized": {"text": "confirmed breast cancer"},
                "ancestor_headers": [],
                "compiler": {"selected_family_id": "oncology.primary_cancer.required", "computability": "manual_review_clinical"},
                "review": {"status": "pending"},
            },
            {
                "trial_id": "NCT1",
                "version_id": "NCT1::v1",
                "node_type": "leaf",
                "section": "exclusion",
                "raw": {"text": "ER-positive disease is excluded"},
                "normalized": {"text": "er-positive disease is excluded"},
                "ancestor_headers": [],
                "compiler": {"selected_family_id": "biomarkers.ER.status.required", "computability": "manual_review_clinical"},
                "review": {"status": "pending"},
            },
        ]
        rule_family_docs = [
            {
                "family_id": "oncology.primary_cancer.required",
                "patientSupport": {"supported": True},
            },
            {
                "family_id": "biomarkers.ER.status.required",
                "patientSupport": {"supported": True},
            },
        ]

        report = build_calibration_diagnostic_report(
            patient_facts_docs=patient_facts_docs,
            patient_profile_docs=patient_profile_docs,
            trial_docs=trial_docs,
            current_criteria_docs=current_criteria_docs,
            rule_family_docs=rule_family_docs,
            query={"condition": "breast cancer", "phase": "PHASE3"},
            git_commit="426e448",
            trial_runtime_tag="clinical-trials-canonical-runtime",
            patient_rebuild_summary={"ok": True},
            trial_refresh_summary={"ok": True},
        )

        self.assertEqual(report["authoritativeCalibration"]["linkedPatients"], 2)
        self.assertEqual(report["authoritativeCalibration"]["confirmedBreastOncologyPatients"], 2)
        self.assertEqual(report["authoritativeCalibration"]["readyTierPatients"], 1)
        self.assertEqual(report["authoritativeCalibration"]["authoritativeBreastOncologyPatients"], 0)
        self.assertEqual(report["authoritativeCalibration"]["retailOutreachReadyPatients"], 0)
        self.assertTrue(
            any(
                "cohort_rule_mismatch" in (row.get("authoritativeRejectedReasons") or [])
                for row in report["authoritativeCalibration"]["patients"]
            )
        )
        self.assertTrue(
            any(
                "not_retail_outreach_ready" in (row.get("outreachRejectedReasons") or [])
                for row in report["authoritativeCalibration"]["patients"]
            )
        )
        self.assertEqual(report["trialRuntimeDiagnostics"]["blockedTrials"], 1)
        first_trial = report["trialRuntimeDiagnostics"]["trials"][0]
        self.assertIn("runtime_gate::exclusion criteria still contain unresolved deterministic gaps", first_trial["blockedBy"])
        self.assertTrue(any(item.startswith("manual_family:") for item in first_trial["blockedBy"]))
