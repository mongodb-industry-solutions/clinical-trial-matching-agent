from __future__ import annotations

import unittest

from app.services.matcher import (
    POPULATION_MODE_AUTHORITATIVE,
    POPULATION_MODE_PHARMACY,
    _effective_stage_target_windows,
    _normalize_population_mode,
    _patient_in_population_mode,
    _patient_stage_target_window,
    evaluate_trial_match,
)
from app.services.rule_families import build_seed_rule_families


def _family_map() -> dict[str, dict]:
    family_map = {}
    for family in build_seed_rule_families("2026-04-03"):
        family_map[family["family_id"]] = family
    return family_map


def _patient_bundle(
    *,
    linkage_status: str = "linked",
    age_years: int | None = 52,
    sex: str = "female",
    ecog: int | None = 1,
    authoritative: bool | None = None,
    retail_outreach_ready: bool | None = None,
    pharmacy_prescreen: bool | None = None,
) -> dict:
    authoritative_value = linkage_status == "linked" if authoritative is None else authoritative
    retail_outreach_value = linkage_status == "linked" and authoritative_value if retail_outreach_ready is None else retail_outreach_ready
    pharmacy_value = (
        linkage_status == "linked" and not authoritative_value
        if pharmacy_prescreen is None
        else pharmacy_prescreen
    )
    return {
        "patientRef": "Patient/test-patient",
        "patientName": "Test Patient",
        "linkageStatus": linkage_status,
        "patientEvidenceProfile": {
            "schemaVersion": "patient_evidence_profile_v2",
            "identity": {
                "linkageStatus": linkage_status,
            },
            "demographics": {
                "ageYears": age_years,
                "sex": sex,
            },
            "oncology": {
                "primaryCancer": {
                    "status": "present",
                    "text": "breast cancer",
                },
                "performanceStatus": {
                    "value": ecog,
                },
                "tumorMarkers": {
                    "ER": {"status": "positive"},
                    "HER2": {"status": "negative"},
                },
                "metastatic": {
                    "status": "present",
                    "value": True,
                },
                "stageGroup": {
                    "value": "stage iv",
                },
                "metastaticSites": [],
            },
            "treatments": {
                "conditionHistory": ["metastatic breast cancer"],
                "medicationHistory": ["fulvestrant"],
            },
            "context": {},
            "resolvedFeatures": {
                "age": {"resolvedValue": age_years, "status": "present", "conflict": False, "evidenceRefs": [], "sourceOfTruth": "test"},
                "sex": {"resolvedValue": sex, "status": "present", "conflict": False, "evidenceRefs": [], "sourceOfTruth": "test"},
                "primaryCancer": {
                    "resolvedValue": {"code": "breast_carcinoma", "text": "breast cancer"},
                    "status": "present",
                    "conflict": False,
                    "evidenceRefs": [],
                    "sourceOfTruth": "test",
                },
                "ECOG": {"resolvedValue": ecog, "status": "present" if ecog is not None else "unknown", "conflict": False, "evidenceRefs": [], "sourceOfTruth": "test"},
                "ER": {"resolvedValue": "positive", "status": "positive", "conflict": False, "evidenceRefs": [], "sourceOfTruth": "test"},
                "HER2": {"resolvedValue": "negative", "status": "negative", "conflict": False, "evidenceRefs": [], "sourceOfTruth": "test"},
                "metastaticFlag": {"resolvedValue": True, "status": "present", "conflict": False, "evidenceRefs": [], "sourceOfTruth": "test"},
                "stageGroup": {"resolvedValue": "stage iv", "status": "present", "conflict": False, "evidenceRefs": [], "sourceOfTruth": "test"},
                "diseaseExtent": {"resolvedValue": ["advanced", "metastatic"], "status": "present", "conflict": False, "evidenceRefs": [], "sourceOfTruth": "test"},
                "treatmentHistory": {"resolvedValue": ["fulvestrant"], "status": "present", "conflict": False, "evidenceRefs": [], "sourceOfTruth": "test"},
                "keyLabs": {
                    "resolvedValue": {
                        "hemoglobin": {"value": 10.8, "unit": "g/dL"},
                        "platelet": {"value": 210, "unit": "10^3/uL"},
                        "ast": {"value": 22, "unit": "U/L"},
                        "alt": {"value": 19, "unit": "U/L"},
                        "bilirubin": {"value": 0.8, "unit": "mg/dL"},
                    },
                    "status": "present",
                    "conflict": False,
                    "evidenceRefs": [],
                    "sourceOfTruth": "test",
                },
                "pregnancy": {"resolvedValue": None, "status": "unknown", "conflict": False, "evidenceRefs": [], "sourceOfTruth": "test"},
                "breastfeeding": {"resolvedValue": None, "status": "unknown", "conflict": False, "evidenceRefs": [], "sourceOfTruth": "test"},
                "activeInfection": {"resolvedValue": None, "status": "unknown", "conflict": False, "evidenceRefs": [], "sourceOfTruth": "test"},
                "cnsMetastasesActive": {"resolvedValue": False, "status": "absent", "conflict": False, "evidenceRefs": [], "sourceOfTruth": "test"},
                "healthyVolunteer": {"resolvedValue": False, "status": "absent", "conflict": False, "evidenceRefs": [], "sourceOfTruth": "test"},
                "sampleSubmissionWilling": {"resolvedValue": None, "status": "unknown", "conflict": False, "evidenceRefs": [], "sourceOfTruth": "test"},
            },
        },
        "patientMatchProfile": {
            "schemaVersion": "patient_match_profile_v2",
            "identity": {
                "linkageStatus": linkage_status,
                "identityTrust": "linked_composite" if linkage_status == "linked" else "unresolved_identity",
            },
            "demographics": {
                "ageYears": age_years,
                "sex": sex,
            },
            "runtimeReadiness": {
                "mode": "authoritative_oncology" if authoritative_value else ("pharmacy_prescreen" if pharmacy_value else "linkage_required"),
                "runnable": authoritative_value or pharmacy_value,
                "linkageStatus": linkage_status,
                "criticalConflictCount": 0 if linkage_status != "conflict" else 1,
                "benchmarkRunnable": authoritative_value,
                "outreachRunnable": retail_outreach_value,
            },
            "cohortMembership": {
                "authoritativeBreastOncology": authoritative_value,
                "retailOutreachReady": retail_outreach_value,
                "pharmacyPrescreen": pharmacy_value,
                "screeningOnly": False,
            },
            "matchFilters": {
                "primaryCancerCode": "breast_carcinoma",
                "erStatus": "positive",
                "her2Status": "negative",
                "metastaticStatus": "present",
                "performanceECOG": ecog,
                "cnsMetastasesStatus": "absent",
            },
            "oncologySummary": {
                "performanceStatus": ecog,
            },
        },
    }


class ClinicalTrialMatcherTests(unittest.TestCase):
    def test_population_mode_defaults_to_authoritative(self) -> None:
        self.assertEqual(_normalize_population_mode(None), POPULATION_MODE_AUTHORITATIVE)
        self.assertEqual(_normalize_population_mode("unknown"), POPULATION_MODE_AUTHORITATIVE)
        self.assertEqual(_normalize_population_mode("pharmacy_prescreen"), POPULATION_MODE_PHARMACY)

    def test_stage_target_windows_include_manual_mixed_scope_for_demo(self) -> None:
        self.assertEqual(
            _effective_stage_target_windows("stage_ii_iii_or_locally_advanced"),
            ["stage_ii_iii_or_locally_advanced", "mixed_or_manual"],
        )
        self.assertEqual(
            _effective_stage_target_windows("advanced_or_metastatic"),
            ["advanced_or_metastatic", "mixed_or_manual"],
        )

    def test_patient_stage_target_window_comes_from_match_profile_posture(self) -> None:
        patient_bundle = _patient_bundle(authoritative=True, retail_outreach_ready=True, pharmacy_prescreen=False)
        patient_bundle["patientMatchProfile"]["cohortMembership"]["breastCancerStageIiIii"] = True
        patient_bundle["patientMatchProfile"]["cohortMembership"]["metastaticBreastCancer"] = False
        patient_bundle["patientMatchProfile"]["matchFilters"]["diseaseWindow"] = "stage_ii_iii_or_locally_advanced"

        self.assertEqual(_patient_stage_target_window(patient_bundle), "stage_ii_iii_or_locally_advanced")

    def test_seed_rule_families_include_patient_support_matrix(self) -> None:
        family = _family_map()["performance.ecog.max"]
        patient_support = family.get("patientSupport") or {}
        self.assertTrue(patient_support.get("supported"))
        self.assertEqual(patient_support.get("evaluator"), "eval_performance_ecog_max_v1")
        self.assertEqual(
            patient_support.get("evidencePaths"),
            ["patientEvidenceProfile.resolvedFeatures.ECOG"],
        )

    def test_candidate_patient_matches_supported_prefilters_and_criteria(self) -> None:
        family_map = _family_map()
        patient_bundle = _patient_bundle()
        trial_doc = {
            "_id": "NCT0001",
            "title": "Test trial",
            "current_version_id": "NCT0001::v1",
            "compile": {"status": "ready_auto"},
            "stage_relevance": {"targetWindow": "advanced_or_metastatic"},
            "match_contract": {
                "hard_prefilter": {
                    "demographics.age_years.min": 18,
                    "demographics.sex.any_of": ["female"],
                    "performance.ecog.max": 1,
                    "oncology.primary_cancer.any_of": ["breast_carcinoma"],
                }
            },
        }
        criteria_nodes = [
            {
                "_id": "NCT0001::INC::001",
                "section": "inclusion",
                "compiler": {
                    "computability": "deterministic",
                    "parse_status": "auto_compiled",
                    "selected_family_id": "performance.ecog.max",
                    "compiled_rule": {
                        "family": "performance.ecog.max",
                        "logic": {
                            "predicate": "performance.ecog",
                            "operator": "<=",
                            "value": 1,
                        },
                    },
                },
                "review": {"status": "not_needed"},
            }
        ]

        result = evaluate_trial_match(
            trial_doc=trial_doc,
            criteria_nodes=criteria_nodes,
            patient_bundle=patient_bundle,
            family_map=family_map,
        )

        self.assertEqual(result["overall"], "candidate")
        self.assertTrue(result["prefilter"]["candidate"])
        self.assertEqual(result["criteria"][0]["status"], "met")
        self.assertEqual(result["ruleTraversal"]["prefilterEvaluatedCount"], 4)
        self.assertEqual(result["ruleTraversal"]["criteriaEvaluatedCount"], 1)
        self.assertEqual(result["ruleTraversal"]["totalEvaluatedCount"], 5)
        self.assertEqual(result["contractMetadata"]["trialVersionId"], "NCT0001::v1")
        self.assertEqual(result["contractMetadata"]["stageRelevance"]["targetWindow"], "advanced_or_metastatic")
        self.assertEqual(result["contractMetadata"]["hardPrefilterKeyCount"], 4)

    def test_linkage_conflict_blocks_automatic_matching(self) -> None:
        family_map = _family_map()
        patient_bundle = _patient_bundle(linkage_status="conflict", authoritative=False, retail_outreach_ready=False, pharmacy_prescreen=True)
        trial_doc = {
            "_id": "NCT0002",
            "title": "Conflict trial",
            "compile": {"status": "ready_auto"},
            "match_contract": {
                "hard_prefilter": {
                    "demographics.age_years.min": 18,
                }
            },
        }

        result = evaluate_trial_match(
            trial_doc=trial_doc,
            criteria_nodes=[],
            patient_bundle=patient_bundle,
            family_map=family_map,
        )

        self.assertEqual(result["overall"], "needs_identity_resolution")
        self.assertEqual(result["prefilter"]["evaluations"][0]["status"], "blocked_by_linkage_conflict")

    def test_population_mode_accepts_conflict_patient_for_pharmacy_prescreen_only(self) -> None:
        conflict_patient = _patient_bundle(linkage_status="conflict", authoritative=False, retail_outreach_ready=False, pharmacy_prescreen=True)
        self.assertFalse(_patient_in_population_mode(conflict_patient, POPULATION_MODE_AUTHORITATIVE))
        self.assertFalse(_patient_in_population_mode(conflict_patient, POPULATION_MODE_PHARMACY))

    def test_unlinked_trusted_oncology_patient_is_authoritative(self) -> None:
        patient_bundle = _patient_bundle(linkage_status="unlinked", authoritative=True, retail_outreach_ready=False, pharmacy_prescreen=False)
        self.assertFalse(_patient_in_population_mode(patient_bundle, POPULATION_MODE_AUTHORITATIVE))
        self.assertFalse(_patient_in_population_mode(patient_bundle, POPULATION_MODE_PHARMACY))

    def test_questionnaire_fillable_rule_surfaces_structured_gap(self) -> None:
        family_map = _family_map()
        patient_bundle = _patient_bundle()
        trial_doc = {
            "_id": "NCT0003",
            "title": "Questionnaire trial",
            "compile": {"status": "ready_auto"},
            "match_contract": {"hard_prefilter": {}},
        }
        criteria_nodes = [
            {
                "_id": "NCT0003::INC::005",
                "section": "inclusion",
                "compiler": {
                    "computability": "deterministic",
                    "parse_status": "auto_compiled",
                    "selected_family_id": "sample_submission.willingness",
                    "compiled_rule": {
                        "family": "sample_submission.willingness",
                        "logic": {
                            "predicate": "study_participation.sample_submission_willing",
                            "operator": "=",
                            "value": True,
                        },
                    },
                },
                "review": {"status": "not_needed"},
            }
        ]

        result = evaluate_trial_match(
            trial_doc=trial_doc,
            criteria_nodes=criteria_nodes,
            patient_bundle=patient_bundle,
            family_map=family_map,
        )

        self.assertEqual(result["overall"], "needs_more_data")
        self.assertEqual(result["criteria"][0]["status"], "fillable_by_questionnaire")
        self.assertEqual(result["gaps"][0]["fillableBy"], "questionnaire")

    def test_lab_panel_and_prior_exposure_rules_evaluate_deterministically(self) -> None:
        family_map = _family_map()
        patient_bundle = _patient_bundle()
        trial_doc = {
            "_id": "NCT0004",
            "title": "Lab and therapy trial",
            "compile": {"status": "ready_auto"},
            "match_contract": {"hard_prefilter": {}},
        }
        criteria_nodes = [
            {
                "_id": "NCT0004::INC::001",
                "section": "inclusion",
                "compiler": {
                    "computability": "deterministic",
                    "parse_status": "auto_compiled",
                    "selected_family_id": "organ_function.lab_panel",
                    "compiled_rule": {
                        "family": "organ_function.lab_panel",
                        "logic": {
                            "predicate": "organ_function.lab_panel",
                            "operator": "panel_satisfies",
                            "clauses": [
                                {"labKey": "hemoglobin", "operator": ">=", "value": 9.0, "unit": "g/dL", "usesUln": False},
                                {"labKey": "platelet", "operator": ">=", "value": 100.0, "unit": "10^3/uL", "usesUln": False},
                            ],
                        },
                    },
                },
                "review": {"status": "not_needed"},
            },
            {
                "_id": "NCT0004::EXC::002",
                "section": "exclusion",
                "compiler": {
                    "computability": "deterministic",
                    "parse_status": "auto_compiled",
                    "selected_family_id": "therapy.prior_exposure.prohibited_history",
                    "compiled_rule": {
                        "family": "therapy.prior_exposure.prohibited_history",
                        "logic": {
                            "predicate": "therapy.prior_exposure_history",
                            "operator": "not_intersects",
                            "value": ["fulvestrant"],
                        },
                    },
                },
                "review": {"status": "not_needed"},
            },
        ]

        result = evaluate_trial_match(
            trial_doc=trial_doc,
            criteria_nodes=criteria_nodes,
            patient_bundle=patient_bundle,
            family_map=family_map,
        )

        self.assertEqual(result["criteria"][0]["status"], "met")
        self.assertEqual(result["criteria"][1]["status"], "not_met")
        self.assertEqual(result["overall"], "excluded")

    def test_pregnancy_context_can_be_questionnaire_fillable(self) -> None:
        family_map = _family_map()
        patient_bundle = _patient_bundle()
        trial_doc = {
            "_id": "NCT0005",
            "title": "Pregnancy context trial",
            "compile": {"status": "ready_auto"},
            "match_contract": {"hard_prefilter": {}},
        }
        criteria_nodes = [
            {
                "_id": "NCT0005::EXC::001",
                "section": "exclusion",
                "compiler": {
                    "computability": "deterministic",
                    "parse_status": "auto_compiled",
                    "selected_family_id": "context.pregnancy_breastfeeding",
                    "compiled_rule": {
                        "family": "context.pregnancy_breastfeeding",
                        "logic": {
                            "predicate": "context.pregnancy_breastfeeding",
                            "operator": "requirements_satisfied",
                            "requirements": [
                                {"feature": "pregnancy", "expected": "absent"},
                                {"feature": "breastfeeding", "expected": "absent"},
                            ],
                        },
                    },
                },
                "review": {"status": "not_needed"},
            }
        ]

        result = evaluate_trial_match(
            trial_doc=trial_doc,
            criteria_nodes=criteria_nodes,
            patient_bundle=patient_bundle,
            family_map=family_map,
        )

        self.assertEqual(result["overall"], "needs_more_data")
        self.assertEqual(result["criteria"][0]["status"], "fillable_by_questionnaire")

    def test_non_questionnaire_missing_feature_is_normalized_to_ehr_only_gap(self) -> None:
        family_map = _family_map()
        patient_bundle = _patient_bundle()
        patient_bundle["patientEvidenceProfile"]["resolvedFeatures"]["ER"] = {
            "resolvedValue": None,
            "status": "unknown",
            "conflict": False,
            "evidenceRefs": [],
            "sourceOfTruth": "test",
        }
        trial_doc = {
            "_id": "NCT0006",
            "title": "EHR-only gap trial",
            "compile": {"status": "ready_auto"},
            "match_contract": {"hard_prefilter": {}},
        }
        criteria_nodes = [
            {
                "_id": "NCT0006::INC::001",
                "section": "inclusion",
                "compiler": {
                    "computability": "deterministic",
                    "parse_status": "auto_compiled",
                    "selected_family_id": "biomarkers.ER.status.required",
                    "compiled_rule": {
                        "family": "biomarkers.ER.status.required",
                        "logic": {
                            "predicate": "oncology.biomarker.ER_status",
                            "operator": "=",
                            "value": "positive",
                        },
                    },
                },
                "review": {"status": "not_needed"},
            }
        ]

        result = evaluate_trial_match(
            trial_doc=trial_doc,
            criteria_nodes=criteria_nodes,
            patient_bundle=patient_bundle,
            family_map=family_map,
        )

        self.assertEqual(result["overall"], "needs_more_data")
        self.assertEqual(result["criteria"][0]["status"], "unknown")
        self.assertEqual(result["gaps"][0]["fillableBy"], "ehr_only")


if __name__ == "__main__":
    unittest.main()
