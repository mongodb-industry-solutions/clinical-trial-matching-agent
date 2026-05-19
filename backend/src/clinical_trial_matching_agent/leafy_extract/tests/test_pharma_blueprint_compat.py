import unittest

from clinical_trial_matching_agent.leafy_extract.services.pharma_canonical_compat import (
    build_legacy_structured_eval_from_canonical,
    canonical_overall_to_legacy_assessment,
)


class PharmaBlueprintCanonicalCompatTests(unittest.TestCase):
    def test_canonical_overall_maps_to_legacy_assessment(self):
        self.assertEqual(canonical_overall_to_legacy_assessment("candidate"), "likely_eligible")
        self.assertEqual(canonical_overall_to_legacy_assessment("excluded"), "likely_ineligible")
        self.assertEqual(canonical_overall_to_legacy_assessment("needs_more_data"), "needs_review")
        self.assertEqual(canonical_overall_to_legacy_assessment("manual_review_required"), "needs_review")

    def test_build_legacy_eval_maps_inclusion_and_exclusion_criteria(self):
        criteria_lookup = {
            "trial::INC::001": {
                "section": "inclusion",
                "text": "ECOG performance status must be 0 or 1",
                "familyId": "performance.ecog.max",
                "operator": "<=",
                "requiredFeatures": ["ECOG"],
            },
            "trial::EXC::002": {
                "section": "exclusion",
                "text": "Active CNS metastases",
                "familyId": "metastases.cns.active",
                "operator": "!=",
                "requiredFeatures": ["cnsMetastasesActive"],
            },
        }
        compat = build_legacy_structured_eval_from_canonical(
            criteria_rows=[
                {
                    "criterionId": "trial::INC::001",
                    "section": "inclusion",
                    "status": "met",
                    "reason": "Compared curated ECOG status to trial threshold.",
                    "patientValue": 1,
                    "requiredValue": 1,
                    "evidencePaths": ["patientEvidenceProfile.resolvedFeatures.ECOG"],
                    "sourceOfTruth": "test",
                },
                {
                    "criterionId": "trial::EXC::002",
                    "section": "exclusion",
                    "status": "not_met",
                    "reason": "Active CNS metastases are present.",
                    "patientValue": True,
                    "requiredValue": False,
                    "evidencePaths": ["patientEvidenceProfile.resolvedFeatures.cnsMetastasesActive"],
                    "sourceOfTruth": "test",
                },
            ],
            criteria_lookup=criteria_lookup,
            overall="excluded",
        )

        self.assertEqual(compat["overallAssessment"], "likely_ineligible")
        self.assertFalse(compat["isEligibleStructured"])
        self.assertEqual(compat["ruleStatusCounts"]["pass"], 1)
        self.assertEqual(compat["ruleStatusCounts"]["fail"], 1)
        self.assertEqual(compat["decisionSummary"]["exclusionHits"], 1)

        first_rule, second_rule = compat["ruleEvaluations"]
        self.assertEqual(first_rule["status"], "PASS")
        self.assertEqual(first_rule["criterionState"], "satisfied")
        self.assertEqual(second_rule["status"], "FAIL")
        self.assertEqual(second_rule["criterionState"], "triggered")

    def test_build_legacy_eval_surfaces_questionnaire_and_manual_review_as_unknown(self):
        criteria_lookup = {
            "trial::EXC::003": {
                "section": "exclusion",
                "text": "Not pregnant or breastfeeding",
                "familyId": "context.pregnancy_breastfeeding",
                "operator": "=",
                "requiredFeatures": ["pregnancy", "breastfeeding"],
            },
            "trial::INC::004": {
                "section": "inclusion",
                "text": "Willing to provide additional sample",
                "familyId": "sample_submission.willingness",
                "operator": "=",
                "requiredFeatures": ["sampleSubmissionWilling"],
            },
        }
        compat = build_legacy_structured_eval_from_canonical(
            criteria_rows=[
                {
                    "criterionId": "trial::EXC::003",
                    "section": "exclusion",
                    "status": "fillable_by_questionnaire",
                    "reason": "Pregnancy or breastfeeding context needs questionnaire confirmation.",
                    "gap": {"feature": "pregnancy_breastfeeding"},
                },
                {
                    "criterionId": "trial::INC::004",
                    "section": "inclusion",
                    "status": "manual_review_required",
                    "reason": "Criterion is not yet deterministic for automated matching.",
                    "gap": {"feature": "manualReview"},
                },
            ],
            criteria_lookup=criteria_lookup,
            overall="needs_more_data",
        )

        self.assertEqual(compat["overallAssessment"], "needs_review")
        self.assertEqual(compat["ruleStatusCounts"]["unknown"], 2)
        self.assertIn("pregnancy_breastfeeding", compat["missingCriteria"])
        self.assertIn("manualReview", compat["missingCriteria"])
        self.assertEqual(
            {row["evaluationMode"] for row in compat["ruleEvaluations"]},
            {"questionnaire_fillable", "manual_review_required"},
        )


if __name__ == "__main__":
    unittest.main()
