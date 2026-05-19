from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("MONGODB_CONNECTION_STRING", "mongodb://localhost:27017")

from app.services.compiler import compile_criterion_node, materialize_match_contract
from app.services.ingest_service import _build_runtime_readiness, _build_stage_relevance, build_trial_canonical_tree_payload, ingest_service
from app.services.normalizer import normalize_criterion_text
from app.services.parser import build_synthetic_structured_criteria, parse_markdown_eligibility_tree
from app.services.rule_families import build_seed_rule_families


def _active_families() -> list[dict]:
    return [family for family in build_seed_rule_families("2026-04-03") if family.get("status") == "active"]


class ClinicalTrialsIngestionTests(unittest.TestCase):
    def test_match_contract_materializes_canonical_search_contract(self) -> None:
        compiled_nodes = [
            {
                "_id": "NCTSEARCH::INC::001",
                "compiler": {
                    "computability": "deterministic",
                    "selected_family_id": "demographics.age.min",
                    "compiled_rule": {
                        "family": "demographics.age.min",
                        "logic": {
                            "predicate": "demographics.age_years",
                            "operator": ">=",
                            "value": 18,
                        },
                        "required_patient_features": ["demographics.age_years"],
                        "prefilter_eligible": True,
                    },
                },
                "review": {"status": "not_needed"},
            },
            {
                "_id": "NCTSEARCH::INC::002",
                "compiler": {
                    "computability": "deterministic",
                    "selected_family_id": "demographics.sex.allowed",
                    "compiled_rule": {
                        "family": "demographics.sex.allowed",
                        "logic": {
                            "predicate": "demographics.sex",
                            "operator": "in",
                            "value": ["female"],
                        },
                        "required_patient_features": ["demographics.sex"],
                        "prefilter_eligible": True,
                    },
                },
                "review": {"status": "not_needed"},
            },
            {
                "_id": "NCTSEARCH::INC::003",
                "compiler": {
                    "computability": "deterministic",
                    "selected_family_id": "biomarkers.ER.status.required",
                    "compiled_rule": {
                        "family": "biomarkers.ER.status.required",
                        "logic": {
                            "predicate": "biomarkers.ER.status",
                            "operator": "=",
                            "value": "positive",
                        },
                        "required_patient_features": ["biomarkers.ER.status"],
                        "prefilter_eligible": True,
                    },
                },
                "review": {"status": "not_needed"},
            },
            {
                "_id": "NCTSEARCH::INC::004",
                "compiler": {
                    "computability": "deterministic",
                    "selected_family_id": "biomarkers.HER2.status.required",
                    "compiled_rule": {
                        "family": "biomarkers.HER2.status.required",
                        "logic": {
                            "predicate": "biomarkers.HER2.status",
                            "operator": "=",
                            "value": "negative",
                        },
                        "required_patient_features": ["biomarkers.HER2.status"],
                        "prefilter_eligible": True,
                    },
                },
                "review": {"status": "not_needed"},
            },
        ]

        match_contract = materialize_match_contract(
            {
                "disease_area": "breast_cancer",
                "title": "Metastatic HR-positive / HER2-negative breast cancer study",
                "conditions": ["Breast Cancer"],
                "keywords": ["metastatic", "HER2-negative", "ER-positive"],
            },
            compiled_nodes,
        )

        self.assertEqual(match_contract["hard_prefilter"]["demographics.age_years.min"], 18)
        self.assertEqual(match_contract["hard_prefilter"]["demographics.sex.any_of"], ["female"])
        self.assertEqual(match_contract["hard_prefilter"]["oncology.primary_cancer.any_of"], ["breast_carcinoma"])
        self.assertEqual(match_contract["search_contract"]["version"], "trial_search_contract_v1")
        self.assertEqual(
            match_contract["search_contract"]["mongoProfileQuery"]["patientMatchProfile.matchFilters.primaryCancerCode"],
            {"$in": ["breast_carcinoma"]},
        )
        self.assertEqual(
            match_contract["search_contract"]["signals"]["matchFilters"]["sex"],
            ["female"],
        )
        self.assertIn(
            "disease_window_advanced_or_metastatic",
            match_contract["search_contract"]["signals"]["candidateSignalsAny"],
        )
        self.assertIn("er_positive", match_contract["search_contract"]["signals"]["candidateSignalsAny"])
        self.assertIn("her2_negative", match_contract["search_contract"]["signals"]["candidateSignalsAny"])
        self.assertIn(
            "breast cancer",
            match_contract["search_contract"]["signals"]["keywordTerms"],
        )

    def test_stage_relevance_flags_stage_ii_iii_manual_context(self) -> None:
        stage_relevance = _build_stage_relevance(
            {"disease_area": "breast_cancer"},
            [
                {
                    "_id": "NCT-STAGE::INC::001",
                    "raw": {"text": "Patients with stage II-III breast cancer after surgery"},
                    "compiler": {"selected_family_id": "manual.stage_extent_context"},
                    "review": {},
                }
            ],
            {"hard_prefilter": {}},
        )

        self.assertEqual(stage_relevance["targetWindow"], "stage_ii_iii_or_locally_advanced")
        self.assertTrue(stage_relevance["manualReviewRequired"])
        self.assertEqual(stage_relevance["manualStageContextCount"], 1)

    def test_trial_list_query_can_filter_to_stage_ii_iii_scope(self) -> None:
        query = ingest_service._build_trial_list_query(
            disease_area="breast_cancer",
            runnable=True,
            stage_target_window="stage_ii_iii_or_locally_advanced",
        )

        self.assertEqual(query["disease_area"], "breast_cancer")
        self.assertTrue(query["runtimeReadiness.runnable"])
        self.assertEqual(
            query["stage_relevance.targetWindow"],
            {"$in": ["stage_ii_iii_or_locally_advanced", "mixed_or_manual"]},
        )

    def test_stage_relevance_uses_trial_title_and_conditions_as_fallback_signal(self) -> None:
        stage_relevance = _build_stage_relevance(
            {
                "disease_area": "breast_cancer",
                "title": "Neoadjuvant study for stage II/III HER2-positive breast cancer",
                "conditions": ["Breast Cancer"],
                "keywords": ["locally advanced"],
            },
            [],
            {"hard_prefilter": {}},
        )

        self.assertEqual(stage_relevance["targetWindow"], "stage_ii_iii_or_locally_advanced")
        self.assertFalse(stage_relevance["manualReviewRequired"])

    def test_parser_keeps_same_level_group_headers_and_children(self) -> None:
        eligibility_text = """
Inclusion Criteria:

* Adult participants with loco-regional recurrent or metastatic breast disease not amenable to surgical resection or radiation therapy
* Confirmed diagnosis of ER+/HER2- breast cancer
* Prior therapies for locoregional recurrent or metastatic disease must fulfill all the following criteria:
* One line of CDK4/6 inhibitor therapy in combination with endocrine therapy. Only one line of CDK4/6 inhibitor is allowed in any setting.
* ≤ 1 endocrine therapy in addition to CDK4/6 inhibitor with ET
* Most recent endocrine treatment duration must have been given for ≥6 months prior to disease progression.
* Radiological progression during or after the last line of therapy.

Exclusion Criteria:

* Participants with advanced, symptomatic visceral spread, that are at risk of life-threatening complications in the short term
* Prior treatment with:
* ARV-471, fulvestrant, elacestrant, mTOR, PI3K, AKT pathway inhibitors, PARP inhibitor for any setting
* other investigational agents (including novel endocrine therapy any SERDs, SERCAs, CERANs) for any setting
* prior chemotherapy for advanced/metastatic disease
* Inadequate liver, kidney and bone marrow function
""".strip()

        nodes = parse_markdown_eligibility_tree(
            version_id="NCT05654623::1234567890abcdef",
            trial_id="NCT05654623",
            eligibility_text=eligibility_text,
        )

        inclusion_header = next(
            node for node in nodes if node["raw"]["text"].startswith("Prior therapies for locoregional recurrent")
        )
        inclusion_children = [node for node in nodes if node.get("parent_node_id") == inclusion_header["_id"]]
        self.assertEqual(len(inclusion_children), 4)
        self.assertTrue(inclusion_children[0]["raw"]["text"].startswith("One line of CDK4/6 inhibitor therapy"))

        exclusion_header = next(node for node in nodes if node["raw"]["text"] == "Prior treatment with:")
        exclusion_children = [node for node in nodes if node.get("parent_node_id") == exclusion_header["_id"]]
        self.assertEqual(len(exclusion_children), 3)
        self.assertTrue(exclusion_children[0]["raw"]["text"].startswith("ARV-471"))
        self.assertNotIn(
            "Inadequate liver, kidney and bone marrow function",
            [child["raw"]["text"] for child in exclusion_children],
        )

    def test_structured_and_text_rules_compile_deterministically(self) -> None:
        synthetic_nodes = build_synthetic_structured_criteria(
            version_id="NCT05654623::1234567890abcdef",
            trial_id="NCT05654623",
            structured_eligibility={
                "sex": "ALL",
                "minimum_age": {"value": 18, "unit": "years"},
                "maximum_age": None,
                "healthy_volunteers": False,
            },
        )
        age_node = next(node for node in synthetic_nodes if node["_id"].endswith("AGE_MIN"))
        compiled_age = compile_criterion_node(age_node, _active_families())
        self.assertEqual(compiled_age["compiler"]["parse_status"], "auto_compiled")
        self.assertEqual(compiled_age["compiler"]["selected_family_id"], "demographics.age.min")
        self.assertEqual(compiled_age["compiler"]["compiled_rule"]["logic"]["value"], 18)
        self.assertEqual(compiled_age["review"]["status"], "not_needed")
        self.assertEqual(len(compiled_age["compiler"]["history"]), 1)

        ecog_node = {
            "_id": "NCT05654623::1234567890abcdef::INC::009",
            "trial_id": "NCT05654623",
            "version_id": "NCT05654623::1234567890abcdef",
            "section": "inclusion",
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 9,
            "depth": 1,
            "path": ["INC", "009"],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": "Eastern Cooperative Oncology Group (ECOG) performance status 0-1",
                "source_path": "protocolSection.eligibilityModule.eligibilityCriteria",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text("Eastern Cooperative Oncology Group (ECOG) performance status 0-1"),
            "compiler": {},
            "review": {},
            "audit": {},
        }
        compiled_ecog = compile_criterion_node(ecog_node, _active_families())
        self.assertEqual(compiled_ecog["compiler"]["parse_status"], "auto_compiled")
        self.assertEqual(compiled_ecog["compiler"]["selected_family_id"], "performance.ecog.max")
        self.assertEqual(compiled_ecog["compiler"]["compiled_rule"]["logic"]["value"], 1)
        self.assertEqual(compiled_ecog["analytics"]["patient_relevance_weight"], 5)

        sex_text_node = {
            "_id": "NCT05654623::1234567890abcdef::INC::010",
            "trial_id": "NCT05654623",
            "version_id": "NCT05654623::1234567890abcdef",
            "section": "inclusion",
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 10,
            "depth": 1,
            "path": ["INC", "010"],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": "Female and male patients >= 18 and <= 70 years of age.",
                "source_path": "protocolSection.eligibilityModule.eligibilityCriteria",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text("Female and male patients >= 18 and <= 70 years of age."),
            "compiler": {},
            "review": {},
            "audit": {},
        }
        compiled_sex = compile_criterion_node(sex_text_node, _active_families())
        self.assertEqual(compiled_sex["compiler"]["parse_status"], "auto_compiled")
        self.assertEqual(compiled_sex["compiler"]["selected_family_id"], "demographics.sex.allowed")
        self.assertEqual(compiled_sex["compiler"]["compiled_rule"]["logic"]["value"], ["female", "male"])

        primary_node = {
            "_id": "NCT05654623::1234567890abcdef::INC::011",
            "trial_id": "NCT05654623",
            "version_id": "NCT05654623::1234567890abcdef",
            "section": "inclusion",
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 11,
            "depth": 1,
            "path": ["INC", "011"],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": "Histologically confirmed primary adenocarcinoma of the breast",
                "source_path": "protocolSection.eligibilityModule.eligibilityCriteria",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text("Histologically confirmed primary adenocarcinoma of the breast"),
            "compiler": {},
            "review": {},
            "audit": {},
        }
        compiled_primary = compile_criterion_node(primary_node, _active_families())
        self.assertEqual(compiled_primary["compiler"]["parse_status"], "auto_compiled")
        self.assertEqual(compiled_primary["compiler"]["selected_family_id"], "oncology.primary_cancer.required")
        self.assertEqual(compiled_primary["compiler"]["compiled_rule"]["logic"]["value"], ["breast_carcinoma"])

        er_node = {
            "_id": "NCT05654623::1234567890abcdef::INC::012",
            "trial_id": "NCT05654623",
            "version_id": "NCT05654623::1234567890abcdef",
            "section": "inclusion",
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 12,
            "depth": 1,
            "path": ["INC", "012"],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": "The primary tumor must have been estrogen receptor (ER)-positive and/or progesterone receptor (PgR)-positive.",
                "source_path": "protocolSection.eligibilityModule.eligibilityCriteria",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text("The primary tumor must have been estrogen receptor (ER)-positive and/or progesterone receptor (PgR)-positive."),
            "compiler": {},
            "review": {},
            "audit": {},
        }
        compiled_er = compile_criterion_node(er_node, _active_families())
        self.assertEqual(compiled_er["compiler"]["parse_status"], "auto_compiled")
        self.assertEqual(compiled_er["compiler"]["selected_family_id"], "biomarkers.ER.status.required")
        self.assertEqual(compiled_er["compiler"]["compiled_rule"]["logic"]["value"], "positive")

        her2_node = {
            "_id": "NCT05654623::1234567890abcdef::INC::013",
            "trial_id": "NCT05654623",
            "version_id": "NCT05654623::1234567890abcdef",
            "section": "inclusion",
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 13,
            "depth": 1,
            "path": ["INC", "013"],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": "Tumor is HER2-positive by IHC 3+ or amplified by FISH or CISH",
                "source_path": "protocolSection.eligibilityModule.eligibilityCriteria",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text("Tumor is HER2-positive by IHC 3+ or amplified by FISH or CISH"),
            "compiler": {},
            "review": {},
            "audit": {},
        }
        compiled_her2 = compile_criterion_node(her2_node, _active_families())
        self.assertEqual(compiled_her2["compiler"]["parse_status"], "auto_compiled")
        self.assertEqual(compiled_her2["compiler"]["selected_family_id"], "biomarkers.HER2.status.required")
        self.assertEqual(compiled_her2["compiler"]["compiled_rule"]["logic"]["value"], "positive")

        organ_function_node = {
            "_id": "NCT05654623::1234567890abcdef::INC::014",
            "trial_id": "NCT05654623",
            "version_id": "NCT05654623::1234567890abcdef",
            "section": "inclusion",
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 14,
            "depth": 1,
            "path": ["INC", "014"],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": "Adequate bone marrow function, defined as neutrophil count >= 1,500/uL, hemoglobin >= 9 g/dL, and platelet count >= 100,000/uL.",
                "source_path": "protocolSection.eligibilityModule.eligibilityCriteria",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text("Adequate bone marrow function, defined as neutrophil count >= 1,500/uL, hemoglobin >= 9 g/dL, and platelet count >= 100,000/uL."),
            "compiler": {},
            "review": {},
            "audit": {},
        }

        compiled_organ_function = compile_criterion_node(organ_function_node, _active_families())
        self.assertEqual(compiled_organ_function["compiler"]["parse_status"], "auto_compiled")
        self.assertEqual(compiled_organ_function["compiler"]["selected_family_id"], "organ_function.lab_panel")
        self.assertEqual(len(compiled_organ_function["compiler"]["compiled_rule"]["logic"]["clauses"]), 3)

        prior_exposure_node = {
            "_id": "NCT05654623::1234567890abcdef::EXC::015",
            "trial_id": "NCT05654623",
            "version_id": "NCT05654623::1234567890abcdef",
            "section": "exclusion",
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 15,
            "depth": 1,
            "path": ["EXC", "015"],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": "Previous treatment with Perjeta.",
                "source_path": "protocolSection.eligibilityModule.eligibilityCriteria",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text("Previous treatment with Perjeta."),
            "compiler": {},
            "review": {},
            "audit": {},
        }
        compiled_prior_exposure = compile_criterion_node(prior_exposure_node, _active_families())
        self.assertEqual(compiled_prior_exposure["compiler"]["parse_status"], "auto_compiled")
        self.assertEqual(compiled_prior_exposure["compiler"]["selected_family_id"], "therapy.prior_exposure.prohibited_history")

        pregnancy_node = {
            "_id": "NCT05654623::1234567890abcdef::EXC::016",
            "trial_id": "NCT05654623",
            "version_id": "NCT05654623::1234567890abcdef",
            "section": "exclusion",
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 16,
            "depth": 1,
            "path": ["EXC", "016"],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": "Is pregnant, is breast feeding women, or women of childbearing age who cannot practice effective contraceptives.",
                "source_path": "protocolSection.eligibilityModule.eligibilityCriteria",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text("Is pregnant, is breast feeding women, or women of childbearing age who cannot practice effective contraceptives."),
            "compiler": {},
            "review": {},
            "audit": {},
        }
        compiled_pregnancy = compile_criterion_node(pregnancy_node, _active_families())
        self.assertEqual(compiled_pregnancy["compiler"]["parse_status"], "auto_compiled")
        self.assertEqual(compiled_pregnancy["compiler"]["selected_family_id"], "context.pregnancy_breastfeeding")

        infection_node = {
            "_id": "NCT05654623::1234567890abcdef::EXC::017",
            "trial_id": "NCT05654623",
            "version_id": "NCT05654623::1234567890abcdef",
            "section": "exclusion",
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 17,
            "depth": 1,
            "path": ["EXC", "017"],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": "Received other anti-tumor treatments within 4 weeks prior to the first dose.",
                "source_path": "protocolSection.eligibilityModule.eligibilityCriteria",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text("Received other anti-tumor treatments within 4 weeks prior to the first dose."),
            "compiler": {},
            "review": {},
            "audit": {},
        }
        compiled_infection = compile_criterion_node(infection_node, _active_families())
        self.assertEqual(compiled_infection["compiler"]["parse_status"], "auto_compiled")
        self.assertEqual(compiled_infection["compiler"]["selected_family_id"], "context.infection_recent_systemic_therapy")

        illness_node = {
            "_id": "NCT05654623::1234567890abcdef::EXC::018",
            "trial_id": "NCT05654623",
            "version_id": "NCT05654623::1234567890abcdef",
            "section": "exclusion",
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 18,
            "depth": 1,
            "path": ["EXC", "018"],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": "Other serious illness or medical disorder or condition that, in the opinion of the Investigator, would make the patient unsuitable.",
                "source_path": "protocolSection.eligibilityModule.eligibilityCriteria",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text("Other serious illness or medical disorder or condition that, in the opinion of the Investigator, would make the patient unsuitable."),
            "compiler": {},
            "review": {},
            "audit": {},
        }
        compiled_illness = compile_criterion_node(illness_node, _active_families())
        self.assertEqual(compiled_illness["compiler"]["parse_status"], "manual_classified")
        self.assertEqual(compiled_illness["compiler"]["selected_family_id"], "concomitant_illness.significant")
        self.assertEqual(compiled_illness["review"]["status"], "not_needed")

    def test_playground_compile_returns_ephemeral_canonical_tree(self) -> None:
        family_map = {family["family_id"]: family for family in _active_families()}
        with patch("app.services.ingest_service.list_active_rule_families", return_value=_active_families()):
            with patch("app.services.ingest_service._latest_rule_family_map", return_value=family_map):
                result = ingest_service.compile_playground_rule(
                    {
                        "text": "Histologically confirmed primary adenocarcinoma of the breast",
                        "section": "inclusion",
                    }
                )

        self.assertTrue(result["noDatabaseWrite"])
        self.assertEqual(result["selectedFamily"], "oncology.primary_cancer.required")
        self.assertEqual(result["compilerState"], "auto_compiled")
        self.assertEqual(result["matchability"], "deterministic")
        self.assertIn("canonicalTree", result)
        self.assertEqual(result["canonicalTree"]["trialId"], "PLAYGROUND")

    def test_composite_breast_biomarker_bullet_stays_in_review(self) -> None:
        composite_node = {
            "_id": "NCT05654623::1234567890abcdef::INC::020",
            "trial_id": "NCT05654623",
            "version_id": "NCT05654623::1234567890abcdef",
            "section": "inclusion",
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 20,
            "depth": 1,
            "path": ["INC", "020"],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": "The participant has confirmed HR+, HER2-, early stage resected invasive breast cancer without evidence of distant metastases.",
                "source_path": "protocolSection.eligibilityModule.eligibilityCriteria",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text("The participant has confirmed HR+, HER2-, early stage resected invasive breast cancer without evidence of distant metastases."),
            "compiler": {},
            "review": {},
            "audit": {},
        }

        compiled = compile_criterion_node(composite_node, _active_families())
        self.assertEqual(compiled["compiler"]["parse_status"], "needs_review")
        self.assertEqual(compiled["compiler"]["computability"], "manual_review_clinical")
        self.assertIsNone(compiled["compiler"]["compiled_rule"])
        self.assertEqual(compiled["review"]["status"], "pending")

    def test_contraception_guidance_does_not_compile_as_sex_rule(self) -> None:
        contraception_node = {
            "_id": "NCT05654623::1234567890abcdef::INC::021",
            "trial_id": "NCT05654623",
            "version_id": "NCT05654623::1234567890abcdef",
            "section": "inclusion",
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 21,
            "depth": 1,
            "path": ["INC", "021"],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": "For women of childbearing potential (WOCBP): WOCBP must have a negative serum pregnancy test at Screening and must use adequate birth control.",
                "source_path": "protocolSection.eligibilityModule.eligibilityCriteria",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text("For women of childbearing potential (WOCBP): WOCBP must have a negative serum pregnancy test at Screening and must use adequate birth control."),
            "compiler": {},
            "review": {},
            "audit": {},
        }

        compiled = compile_criterion_node(contraception_node, _active_families())
        self.assertNotEqual(compiled["compiler"]["selected_family_id"], "demographics.sex.allowed")

    def test_manual_stage_extent_cluster_is_auto_classified_into_named_manual_family(self) -> None:
        stage_node = {
            "_id": "NCT05654623::1234567890abcdef::INC::099",
            "trial_id": "NCT05654623",
            "version_id": "NCT05654623::1234567890abcdef",
            "section": "inclusion",
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 99,
            "depth": 1,
            "path": ["INC", "099"],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": "Stage I-III disease",
                "source_path": "protocolSection.eligibilityModule.eligibilityCriteria",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text("Stage I-III disease"),
            "compiler": {},
            "review": {},
            "audit": {},
        }

        compiled = compile_criterion_node(stage_node, _active_families())
        self.assertEqual(compiled["compiler"]["selected_family_id"], "manual.stage_extent_context")
        self.assertEqual(compiled["compiler"]["computability"], "manual_review_clinical")
        self.assertEqual(compiled["compiler"]["parse_status"], "manual_classified")
        self.assertEqual(compiled["review"]["status"], "not_needed")
        self.assertEqual(
            ((compiled["compiler"].get("compiled_rule") or {}).get("logic") or {}).get("operator"),
            "manual_review_required",
        )

    def test_protocol_workflow_row_is_auto_classified_as_not_patient_rule(self) -> None:
        consent_node = {
            "_id": "NCT05654623::1234567890abcdef::INC::100",
            "trial_id": "NCT05654623",
            "version_id": "NCT05654623::1234567890abcdef",
            "section": "inclusion",
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 100,
            "depth": 1,
            "path": ["INC", "100"],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": "Participants must provide informed consent and be willing and able to comply with study procedures.",
                "source_path": "protocolSection.eligibilityModule.eligibilityCriteria",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text("Participants must provide informed consent and be willing and able to comply with study procedures."),
            "compiler": {},
            "review": {},
            "audit": {},
        }

        compiled = compile_criterion_node(consent_node, _active_families())
        self.assertEqual(compiled["compiler"]["selected_family_id"], "protocol_workflow.not_patient_rule")
        self.assertEqual(compiled["compiler"]["computability"], "not_patient_rule")
        self.assertEqual(compiled["review"]["status"], "not_needed")

    def test_residual_inclusion_context_falls_back_to_named_manual_family(self) -> None:
        residual_node = {
            "_id": "NCT05654623::1234567890abcdef::INC::101",
            "trial_id": "NCT05654623",
            "version_id": "NCT05654623::1234567890abcdef",
            "section": "inclusion",
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 101,
            "depth": 1,
            "path": ["INC", "101"],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": "Must have undergone lumpectomy or total mastectomy for primary disease within the past 12 weeks, or have completed chemotherapy within the past 8 weeks.",
                "source_path": "protocolSection.eligibilityModule.eligibilityCriteria",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text("Must have undergone lumpectomy or total mastectomy for primary disease within the past 12 weeks, or have completed chemotherapy within the past 8 weeks."),
            "compiler": {},
            "review": {},
            "audit": {},
        }

        compiled = compile_criterion_node(residual_node, _active_families())
        self.assertEqual(compiled["compiler"]["selected_family_id"], "manual.inclusion_context_other")
        self.assertEqual(compiled["compiler"]["parse_status"], "manual_classified")
        self.assertEqual(compiled["review"]["status"], "not_needed")

    def test_runtime_readiness_allows_runnable_with_manual_when_only_named_manual_rules_remain(self) -> None:
        deterministic_node = {
            "node_type": "leaf",
            "section": "inclusion",
            "compiler": {
                "selected_family_id": "demographics.sex.allowed",
                "computability": "deterministic",
                "compiled_rule": {"family": "demographics.sex.allowed"},
            },
            "review": {"status": "not_needed"},
        }
        manual_node = {
            "node_type": "leaf",
            "section": "inclusion",
            "compiler": {
                "selected_family_id": "manual.stage_extent_context",
                "computability": "manual_review_clinical",
                "compiled_rule": {
                    "family": "manual.stage_extent_context",
                    "logic": {"operator": "manual_review_required", "value": True},
                },
            },
            "review": {"status": "not_needed"},
        }

        with patch(
            "app.services.ingest_service._latest_rule_family_map",
            return_value={
                "demographics.sex.allowed": {
                    "compiler_contract": {"computability": "deterministic"},
                    "patientSupport": {"supported": True},
                },
                "manual.stage_extent_context": {
                    "compiler_contract": {"computability": "manual_review_clinical"},
                    "patientSupport": {"supported": False},
                },
            },
        ):
            readiness = _build_runtime_readiness([deterministic_node, manual_node])
        self.assertTrue(readiness["runnable"])
        self.assertEqual(readiness["runtimeState"], "runtime_ready_with_manual")
        self.assertEqual(readiness["manualPatientImpactingCount"], 1)
        self.assertEqual(readiness["unresolvedPatientImpactingCount"], 0)

    def test_match_contract_materializes_prefilters_for_breast_cancer_trial(self) -> None:
        synthetic_nodes = build_synthetic_structured_criteria(
            version_id="NCT05654623::1234567890abcdef",
            trial_id="NCT05654623",
            structured_eligibility={
                "sex": "ALL",
                "minimum_age": {"value": 18, "unit": "years"},
                "maximum_age": None,
                "healthy_volunteers": False,
            },
        )
        compiled_nodes = [compile_criterion_node(node, _active_families()) for node in synthetic_nodes]
        ecog_node = {
            "_id": "NCT05654623::1234567890abcdef::INC::009",
            "trial_id": "NCT05654623",
            "version_id": "NCT05654623::1234567890abcdef",
            "section": "inclusion",
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 9,
            "depth": 1,
            "path": ["INC", "009"],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": "Eastern Cooperative Oncology Group (ECOG) performance status 0-1",
                "source_path": "protocolSection.eligibilityModule.eligibilityCriteria",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text("Eastern Cooperative Oncology Group (ECOG) performance status 0-1"),
            "compiler": {},
            "review": {},
            "audit": {},
        }
        compiled_nodes.append(compile_criterion_node(ecog_node, _active_families()))

        contract = materialize_match_contract(
            {
                "title": "A Study to Learn About a New Medicine in Advanced Metastatic Breast Cancer",
                "brief_title": "Advanced metastatic breast cancer study",
                "conditions": ["Advanced Breast Cancer"],
                "keywords": ["ER(+)/HER2(-) Advanced Breast Cancer"],
                "disease_area": "breast_cancer",
            },
            compiled_nodes,
        )

        self.assertEqual(contract["hard_prefilter"]["demographics.age_years.min"], 18)
        self.assertEqual(contract["hard_prefilter"]["demographics.sex.any_of"], ["female", "male"])
        self.assertEqual(contract["hard_prefilter"]["performance.ecog.max"], 1)
        self.assertEqual(contract["hard_prefilter"]["oncology.primary_cancer.any_of"], ["breast_carcinoma"])
        self.assertIn("advanced", contract["hard_prefilter"]["oncology.disease_extent.any_of"])

    def test_review_override_can_promote_manual_rule_into_deterministic_contract(self) -> None:
        node = {
            "_id": "NCT05654623::1234567890abcdef::INC::014",
            "trial_id": "NCT05654623",
            "version_id": "NCT05654623::1234567890abcdef",
            "section": "inclusion",
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 14,
            "depth": 1,
            "path": ["INC", "014"],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": "Eastern Cooperative Oncology Group (ECOG) performance status 0-1",
                "source_path": "protocolSection.eligibilityModule.eligibilityCriteria",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text("Eastern Cooperative Oncology Group (ECOG) performance status 0-1"),
            "compiler": {
                "parse_status": "needs_review",
                "computability": "manual_review_clinical",
                "selected_family_id": "performance.ecog.max",
                "selected_family_version": 1,
                "confidence": 0.84,
                "candidate_families": [],
                "compiled_rule": None,
                "validator_report": {},
                "history": [],
            },
            "review": {
                "status": "reviewed",
                "decision": "confirm_family",
                "final_family_id": "performance.ecog.max",
                "slot_overrides": {},
                "final_compiled_rule": {
                    "kind": "inclusion",
                    "family": "performance.ecog.max",
                    "logic": {
                        "predicate": "performance.ecog",
                        "operator": "<=",
                        "value": 1,
                    },
                    "required_patient_features": ["performance.ecog"],
                    "prefilter_eligible": True,
                },
                "reviewer": "analyst",
                "reviewed_at": "2026-04-03T12:00:00Z",
                "notes": "Confirmed after review",
                "resolved_at": "2026-04-03T12:00:00Z",
            },
            "analytics": {
                "patient_relevance_weight": 5,
                "review_cluster_key": "fp_test",
            },
            "audit": {},
        }

        contract = materialize_match_contract(
            {
                "title": "A Study to Learn About a New Medicine in Advanced Metastatic Breast Cancer",
                "brief_title": "Advanced metastatic breast cancer study",
                "conditions": ["Advanced Breast Cancer"],
                "keywords": ["ER(+)/HER2(-) Advanced Breast Cancer"],
                "disease_area": "breast_cancer",
            },
            [node],
        )

        self.assertEqual(contract["hard_prefilter"]["performance.ecog.max"], 1)
        self.assertEqual(contract["deterministic_criterion_ids"], ["NCT05654623::1234567890abcdef::INC::014"])

    def test_canonical_tree_projection_preserves_source_and_compiled_shape(self) -> None:
        eligibility_text = """
Inclusion Criteria:

* Participants must be at least 18 years old
* Eastern Cooperative Oncology Group (ECOG) performance status 0-1
* Prior treatment with:
* One line of endocrine therapy
* Radiological progression during or after the last line of therapy

Exclusion Criteria:

* Active brain metastases
""".strip()

        parsed_nodes = parse_markdown_eligibility_tree(
            version_id="NCTTREE::1234567890abcdef",
            trial_id="NCTTREE",
            eligibility_text=eligibility_text,
        )
        synthetic_nodes = build_synthetic_structured_criteria(
            version_id="NCTTREE::1234567890abcdef",
            trial_id="NCTTREE",
            structured_eligibility={
                "sex": "ALL",
                "minimum_age": {"value": 18, "unit": "years"},
                "maximum_age": None,
                "healthy_volunteers": False,
            },
        )
        compiled_nodes = [
            compile_criterion_node(node, _active_families())
            for node in [*synthetic_nodes, *parsed_nodes]
        ]
        payload = build_trial_canonical_tree_payload(
            {
                "_id": "NCTTREE",
                "current_version_id": "NCTTREE::1234567890abcdef",
                "runtimeReadiness": {
                    "runnable": False,
                    "reasonIfBlocked": "manual criteria remain",
                },
            },
            compiled_nodes,
            family_map={family["family_id"]: family for family in _active_families()},
        )

        self.assertEqual(payload["sourceSummary"]["groupHeaders"], 1)
        self.assertEqual(payload["sourceSummary"]["structuredCriteria"], 3)
        self.assertEqual(payload["canonicalSummary"]["runtimeReady"], False)
        self.assertGreaterEqual(payload["canonicalSummary"]["deterministic"], 3)

        inclusion_root = next(node for node in payload["roots"] if node["section"] == "inclusion")
        exclusion_root = next(node for node in payload["roots"] if node["section"] == "exclusion")
        self.assertEqual(inclusion_root["logic"]["logicalOperator"], "ALL OF")
        self.assertEqual(exclusion_root["logic"]["logicalOperator"], "NONE OF")

        ecog_node = next(node for node in inclusion_root["children"] if node["compiler"]["familyId"] == "performance.ecog.max")
        self.assertEqual(ecog_node["matchability"]["class"], "deterministic")
        self.assertTrue(ecog_node["matchability"]["prefilterEligible"])
        self.assertEqual(ecog_node["patientSupport"]["evaluator"], "eval_performance_ecog_max_v1")

        prior_treatment_group = next(node for node in inclusion_root["children"] if node["label"] == "Prior treatment with:")
        self.assertEqual(prior_treatment_group["nodeType"], "group")
        self.assertEqual(len(prior_treatment_group["children"]), 2)


if __name__ == "__main__":
    unittest.main()
