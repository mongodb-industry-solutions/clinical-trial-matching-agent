import unittest

from services.patient_curation import (
    PATIENT_CURATION_COVERAGE_SUMMARY_VERSION,
    PATIENT_EVIDENCE_PROFILE_VERSION,
    PATIENT_MATCH_PROFILE_VERSION,
    build_patient_curation_coverage_summary,
    build_patient_evidence_profile_v2,
    build_patient_match_profile_v2,
    build_screening_report_summary,
    normalize_gap_fillability,
)
from services.pharma_services import (
    build_breast_oncology_cohort_document,
    build_oncology_cohort_assessment_input,
    choose_screening_backfill_resolution,
)


def normalize_text(value):
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


def parse_age_token(value):
    raw = str(value or "").strip().upper()
    if raw.endswith("Y") and raw[:-1].isdigit():
        return int(raw[:-1])
    try:
        return int(raw)
    except Exception:
        return None


def serialize_doc(value):
    return value


def parse_iso_datetime(value):
    if not value:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


class PatientCurationTests(unittest.TestCase):
    def test_gap_fillability_normalizes_legacy_aliases(self):
        self.assertEqual(normalize_gap_fillability("pathology_or_site_ehr"), "ehr_only")
        self.assertEqual(normalize_gap_fillability("site_ehr_or_manual"), "ehr_only")
        self.assertEqual(normalize_gap_fillability("questionnaire_or_site_ehr"), "questionnaire")
        self.assertEqual(normalize_gap_fillability("manual_review"), "manual_review")

    def test_screening_summary_extracts_birads_and_laterality(self):
        summary = build_screening_report_summary(
            {
                "_id": "report-1",
                "appointmentID": "appt-1",
                "patientID": "leafy-1",
                "date": "2025-02-03 14:43:20",
                "conclusions": "Suspicious right-sided mammogram.",
                "series": [
                    {"imageMetadata": {"PatientName": "Jane Doe", "PatientSex": "F", "PatientAge": "065Y", "Laterality": "L", "biRads": {"BIRAD": 4}}},
                    {"imageMetadata": {"PatientName": "Jane Doe", "PatientSex": "F", "PatientAge": "065Y", "Laterality": "R", "biRads": {"BIRAD": 5}}},
                ],
            },
            parse_age_token_fn=parse_age_token,
            serialize_doc_fn=serialize_doc,
            normalize_text_fn=normalize_text,
            parse_iso_datetime_fn=parse_iso_datetime,
        )

        self.assertEqual(summary["sex"], "female")
        self.assertEqual(summary["ageYears"], 65)
        self.assertEqual(summary["biradsMax"], 5)
        self.assertEqual(summary["suspiciousLaterality"], "right")

    def test_evidence_profile_flags_linkage_conflict_when_screening_disagrees_with_longitudinal_record(self):
        profile = build_patient_evidence_profile_v2(
            patient_ref="Patient/1",
            facts_doc={
                "patientRef": "Patient/1",
                "generatedAt": "2026-04-03T10:00:00Z",
                "facts": {
                    "age": {"value": 49, "source": "synthea-facts"},
                    "sex": {"value": "male", "source": "synthea-facts"},
                    "birthDate": {"value": "1976-06-28", "source": "synthea-facts"},
                    "conditionHistory": {"value": ["metastatic breast carcinoma"], "source": "synthea-facts"},
                    "receptorStatus": {"value": {"er": "positive", "her2": "negative", "supportRefs": ["Observation/1"]}, "source": "synthea-facts"},
                    "oncologySignals": {"value": {"breast_oncology": {"present": True, "supportRefs": ["Condition/1"]}}, "source": "synthea-facts"},
                    "keyLabs": {"value": {"hemoglobin": {"value": 11.2}}, "source": "synthea-facts"},
                },
            },
            screening_summary={
                "reportId": "report-1",
                "reportDate": "2025-02-03 14:43:20",
                "sex": "female",
                "ageYears": 65,
                "biradsValues": [3, 4, 5],
                "biradsMax": 5,
                "suspiciousLaterality": "right",
                "sourceRefs": ["clinicalreports/report-1"],
            },
            linkage_payload={"linkageStatus": "linked", "linkageConfidence": 1.0},
            normalize_text_fn=normalize_text,
        )

        self.assertEqual(profile["schemaVersion"], PATIENT_EVIDENCE_PROFILE_VERSION)
        self.assertEqual(profile["identity"]["linkageStatus"], "conflict")
        self.assertGreaterEqual(len(profile["identity"]["linkageConflicts"]), 2)
        self.assertEqual(profile["oncology"]["tumorMarkers"]["ER"]["status"], "positive")
        self.assertEqual(profile["screeningImaging"]["biradsMax"], 5)
        self.assertIn("identityLinkage", [gap["feature"] for gap in profile["gaps"]])

    def test_evidence_profile_infers_primary_breast_cancer_from_biopsy_and_receptor_support(self):
        profile = build_patient_evidence_profile_v2(
            patient_ref="Patient/42",
            facts_doc={
                "patientRef": "Patient/42",
                "generatedAt": "2026-04-05T10:00:00Z",
                "facts": {
                    "procedureHistory": {"value": ["ultrasound-guided breast biopsy"], "source": "synthea-facts"},
                    "oncologyTreatmentHistory": {"value": {"terms": ["trastuzumab", "endocrine therapy"]}, "source": "synthea-facts"},
                    "receptorStatus": {
                        "value": {
                            "er": "positive",
                            "her2": "negative",
                            "matches": ["hr positive", "her2 negative breast cancer"],
                            "supportRefs": ["Observation/ER", "Observation/HER2"],
                        },
                        "source": "synthea-facts",
                    },
                    "oncologySignals": {
                        "value": {
                            "breast_biopsy": {"present": True, "matches": ["breast biopsy"], "supportRefs": ["Procedure/1"]},
                            "oncology_treatment": {"present": True, "matches": ["trastuzumab"], "supportRefs": ["MedicationRequest/1"]},
                        },
                        "source": "synthea-facts",
                    },
                },
            },
            screening_summary=None,
            linkage_payload={"linkageStatus": "linked", "linkageConfidence": 1.0},
            normalize_text_fn=normalize_text,
        )

        self.assertEqual(profile["resolvedFeatures"]["primaryCancer"]["status"], "present")
        self.assertEqual(profile["oncology"]["primaryCancer"]["code"], "breast_carcinoma")
        self.assertIn("Procedure/1", profile["resolvedFeatures"]["primaryCancer"]["evidenceRefs"])
        self.assertEqual(profile["oncology"]["tumorMarkers"]["ER"]["status"], "positive")
        self.assertEqual(profile["oncology"]["tumorMarkers"]["HER2"]["status"], "negative")

    def test_choose_screening_backfill_resolution_only_returns_candidate_suggestion(self):
        resolution = choose_screening_backfill_resolution(
            identity_snapshot={
                "leafyAppointmentId": "69bf380f453840f4025fe9e7",
                "leafyPatientName": "hc-provider",
            },
            candidate_options=[
                {"patientRef": "Patient/alpha", "score": 0.88},
                {"patientRef": "Patient/beta", "score": 0.67},
            ],
            deterministic_ref="Patient/fallback",
        )

        self.assertIsNotNone(resolution)
        self.assertEqual(resolution["suggestedSyntheaPatientRef"], "Patient/alpha")
        self.assertEqual(resolution["linkageStatus"], "candidate_only")
        self.assertEqual(resolution["linkStrategy"], "candidate_only")

    def test_evidence_profile_defaults_to_unlinked_when_screening_exists_without_linkage_payload(self):
        profile = build_patient_evidence_profile_v2(
            patient_ref="Patient/24",
            facts_doc={
                "patientRef": "Patient/24",
                "generatedAt": "2026-04-05T10:00:00Z",
                "facts": {
                    "age": {"value": 58, "source": "synthea-facts"},
                    "sex": {"value": "female", "source": "synthea-facts"},
                },
            },
            screening_summary={
                "reportId": "report-24",
                "reportDate": "2026-04-05T10:00:00Z",
                "biradsValues": [4],
                "biradsMax": 4,
                "sourceRefs": ["clinicalreports/report-24"],
            },
            linkage_payload=None,
            normalize_text_fn=normalize_text,
        )

        self.assertEqual(profile["identity"]["linkageStatus"], "unlinked")

    def test_match_profile_materializes_fast_filters_from_evidence_profile(self):
        match_profile = build_patient_match_profile_v2(
            profile_doc={"patientRef": "Patient/1", "gender": "female", "age": 65},
            evidence_profile={
                "schemaVersion": PATIENT_EVIDENCE_PROFILE_VERSION,
                "generatedAt": "2026-04-03T10:00:00Z",
                "identity": {"canonicalPatientKey": "pat_1", "linkageStatus": "linked", "linkageConfidence": 0.98, "linkageConflicts": []},
                "demographics": {"sex": "female", "ageYears": 65, "birthDate": "1960-01-02"},
                "oncology": {
                    "primaryCancer": {"status": "present"},
                    "stageGroup": {"value": "stage iv"},
                    "laterality": "right",
                    "performanceStatus": {"value": 1},
                    "tumorMarkers": {"ER": {"status": "positive"}, "HER2": {"status": "negative"}},
                    "metastatic": {"status": "present"},
                },
                "screeningImaging": {"present": True, "biradsValues": [4, 5], "biradsMax": 5, "suspiciousLaterality": "right"},
                "completeness": {"coveragePct": 75.0, "domains": {"demographics": True, "receptorStatus": True}},
                "gaps": [{"feature": "stageOrExtent"}],
            },
            oncology_cohort={
                "candidateCategory": "confirmed_breast_oncology",
                "matchingReadinessTier": "ready",
                "coveragePct": 82.0,
                "criticalMissingDomains": [],
            },
            normalize_text_fn=normalize_text,
        )

        self.assertEqual(match_profile["schemaVersion"], PATIENT_MATCH_PROFILE_VERSION)
        self.assertEqual(match_profile["matchFilters"]["sex"], "female")
        self.assertEqual(match_profile["matchFilters"]["biradsMax"], 5)
        self.assertEqual(match_profile["oncologySummary"]["tumorMarkers"]["ER"], "positive")
        self.assertEqual(match_profile["oncologySummary"]["tumorMarkers"]["PR"], "unknown")
        self.assertTrue(match_profile["cohortMembership"]["authoritativeBreastOncology"])
        self.assertTrue(match_profile["cohortMembership"]["retailOutreachReady"])
        self.assertFalse(match_profile["cohortMembership"]["pharmacyPrescreen"])
        self.assertEqual(match_profile["runtimeReadiness"]["mode"], "authoritative_oncology")
        self.assertTrue(match_profile["runtimeReadiness"]["benchmarkRunnable"])
        self.assertTrue(match_profile["runtimeReadiness"]["outreachRunnable"])
        self.assertEqual(match_profile["matchFilters"]["diseaseWindow"], "advanced_or_metastatic")
        self.assertEqual(match_profile["oncologySummary"]["stageTreatmentPosture"]["treatmentSetting"], "metastatic_systemic")
        self.assertFalse(match_profile["cohortMembership"]["breastCancerStageIiIii"])
        self.assertTrue(match_profile["cohortMembership"]["metastaticBreastCancer"])
        self.assertIn("primary_cancer", match_profile["candidateSignals"])
        self.assertTrue(match_profile["oncologyCohortAssessment"]["confirmedBreastOncology"])
        self.assertEqual(match_profile["oncologyCohortAssessment"]["authoritativeRejectedReasons"], [])
        self.assertEqual(match_profile["oncologyCohortAssessment"]["outreachRejectedReasons"], [])

    def test_stage_iii_nonmetastatic_patient_surfaces_stage_ii_iii_posture(self):
        match_profile = build_patient_match_profile_v2(
            profile_doc={"patientRef": "Patient/12", "gender": "female", "age": 54},
            evidence_profile={
                "schemaVersion": PATIENT_EVIDENCE_PROFILE_VERSION,
                "generatedAt": "2026-04-05T10:00:00Z",
                "identity": {"canonicalPatientKey": "pat_12", "linkageStatus": "linked", "linkageConfidence": 0.98, "linkageConflicts": []},
                "demographics": {"sex": "female", "ageYears": 54, "birthDate": "1972-01-01"},
                "oncology": {
                    "primaryCancer": {"status": "present"},
                    "stageGroup": {"value": "stage iiib"},
                    "laterality": "left",
                    "performanceStatus": {"value": 1},
                    "tumorMarkers": {"ER": {"status": "positive"}, "PR": {"status": "positive"}, "HER2": {"status": "negative"}},
                    "metastatic": {"status": "absent"},
                },
                "resolvedFeatures": {
                    "primaryCancer": {"resolvedValue": {"code": "breast_carcinoma"}, "status": "present"},
                    "age": {"resolvedValue": 54, "status": "present"},
                    "sex": {"resolvedValue": "female", "status": "present"},
                    "ER": {"resolvedValue": "positive", "status": "positive"},
                    "PR": {"resolvedValue": "positive", "status": "positive"},
                    "HER2": {"resolvedValue": "negative", "status": "negative"},
                    "ECOG": {"resolvedValue": 1, "status": "present"},
                    "stageGroup": {"resolvedValue": "stage iiib", "status": "present"},
                    "metastaticFlag": {"resolvedValue": False, "status": "absent"},
                    "diseaseExtent": {"resolvedValue": ["regional"], "status": "present"},
                    "treatmentHistory": {"resolvedValue": ["paclitaxel"], "status": "present"},
                },
                "treatments": {"priorSystemicTherapy": {"status": "present", "terms": ["paclitaxel"]}},
                "screeningImaging": {"present": False},
                "completeness": {"coveragePct": 84.0, "domains": {"breastOncology": True, "receptorStatus": True}},
                "gaps": [],
            },
            oncology_cohort={
                "candidateCategory": "confirmed_breast_oncology",
                "matchingReadinessTier": "ready",
                "coveragePct": 84.0,
                "criticalMissingDomains": [],
            },
            normalize_text_fn=normalize_text,
        )

        self.assertTrue(match_profile["matchFilters"]["stageIiIii"])
        self.assertEqual(match_profile["matchFilters"]["diseaseWindow"], "stage_ii_iii_or_locally_advanced")
        self.assertEqual(match_profile["matchFilters"]["treatmentSetting"], "curative_or_locally_advanced")
        self.assertTrue(match_profile["oncologySummary"]["stageTreatmentPosture"]["stageIiIii"])
        self.assertTrue(match_profile["cohortMembership"]["breastCancerStageIiIii"])
        self.assertFalse(match_profile["cohortMembership"]["metastaticBreastCancer"])
        self.assertIn("stage_ii_iii", match_profile["candidateSignals"])

    def test_partial_oncology_cohort_falls_back_to_pharmacy_prescreen_mode(self):
        match_profile = build_patient_match_profile_v2(
            profile_doc={"patientRef": "Patient/2", "gender": "female", "age": 65},
            evidence_profile={
                "schemaVersion": PATIENT_EVIDENCE_PROFILE_VERSION,
                "generatedAt": "2026-04-03T10:00:00Z",
                "identity": {"canonicalPatientKey": "pat_2", "linkageStatus": "linked", "linkageConfidence": 0.98, "linkageConflicts": []},
                "demographics": {"sex": "female", "ageYears": 65, "birthDate": "1960-01-02"},
                "oncology": {
                    "primaryCancer": {"status": "present"},
                    "stageGroup": {"value": None},
                    "laterality": None,
                    "performanceStatus": {"value": None},
                    "tumorMarkers": {"ER": {"status": "unknown"}, "PR": {"status": "unknown"}, "HER2": {"status": "unknown"}},
                    "metastatic": {"status": "unknown"},
                },
                "screeningImaging": {"present": False},
                "completeness": {"coveragePct": 35.0, "domains": {"demographics": True, "receptorStatus": False}},
                "resolvedFeatures": {
                    "primaryCancer": {"resolvedValue": {"code": "breast_carcinoma"}, "status": "present"},
                    "age": {"resolvedValue": 65, "status": "present"},
                    "sex": {"resolvedValue": "female", "status": "present"},
                    "ER": {"resolvedValue": None, "status": "unknown"},
                    "PR": {"resolvedValue": None, "status": "unknown"},
                    "HER2": {"resolvedValue": None, "status": "unknown"},
                    "ECOG": {"resolvedValue": None, "status": "unknown"},
                    "stageGroup": {"resolvedValue": None, "status": "unknown"},
                    "metastaticFlag": {"resolvedValue": None, "status": "unknown"},
                    "treatmentHistory": {"resolvedValue": [], "status": "unknown"},
                },
                "gaps": [{"feature": "receptorStatus"}],
            },
            oncology_cohort={
                "candidateCategory": "confirmed_breast_oncology",
                "matchingReadinessTier": "partial",
                "coveragePct": 41.0,
                "criticalMissingDomains": ["receptor_status", "stage_or_extent"],
            },
            normalize_text_fn=normalize_text,
        )

        self.assertFalse(match_profile["cohortMembership"]["authoritativeBreastOncology"])
        self.assertFalse(match_profile["cohortMembership"]["retailOutreachReady"])
        self.assertTrue(match_profile["cohortMembership"]["pharmacyPrescreen"])
        self.assertEqual(match_profile["runtimeReadiness"]["mode"], "pharmacy_prescreen")
        self.assertIn(
            "readiness_tier_not_authoritative::partial",
            match_profile["oncologyCohortAssessment"]["authoritativeRejectedReasons"],
        )
        self.assertIn(
            "not_authoritative_breast_oncology",
            match_profile["oncologyCohortAssessment"]["outreachRejectedReasons"],
        )

    def test_runtime_profile_and_cohort_builder_share_same_assessment_input(self):
        facts_doc = {
            "patientRef": "Patient/9",
            "factVersion": "oncology_facts_v1",
            "patientEvidenceProfile": {
                "schemaVersion": PATIENT_EVIDENCE_PROFILE_VERSION,
                "generatedAt": "2026-04-04T10:00:00Z",
                "identity": {
                    "canonicalPatientKey": "pat_9",
                    "linkageStatus": "linked",
                    "linkageConfidence": 0.99,
                    "linkageConflicts": [],
                },
                "demographics": {"sex": "female", "ageYears": 58, "birthDate": "1968-01-02"},
                "oncology": {
                    "primaryCancer": {"status": "present", "text": "breast carcinoma", "code": "breast_carcinoma", "provenance": ["Condition/1"]},
                    "stageGroup": {"status": "present", "value": "stage iv", "provenance": ["Condition/2"]},
                    "metastatic": {"status": "present", "value": True, "provenance": ["Condition/2"]},
                    "tumorMarkers": {
                        "ER": {"status": "positive", "supportRefs": ["Observation/1"]},
                        "PR": {"status": "positive", "supportRefs": ["Observation/2"]},
                        "HER2": {"status": "negative", "supportRefs": ["Observation/3"]},
                    },
                    "performanceStatus": {"status": "present", "value": 1, "provenance": ["Observation/4"]},
                },
                "treatments": {
                    "priorSystemicTherapy": {
                        "status": "present",
                        "terms": ["tamoxifen"],
                        "provenance": ["MedicationRequest/1"],
                    }
                },
                "screeningImaging": {"present": False},
                "resolvedFeatures": {
                    "primaryCancer": {"resolvedValue": {"code": "breast_carcinoma", "text": "breast carcinoma"}, "status": "present"},
                    "ER": {"resolvedValue": "positive", "status": "positive"},
                    "PR": {"resolvedValue": "positive", "status": "positive"},
                    "HER2": {"resolvedValue": "negative", "status": "negative"},
                    "ECOG": {"resolvedValue": 1, "status": "present"},
                    "stageGroup": {"resolvedValue": "stage iv", "status": "present"},
                    "metastaticFlag": {"resolvedValue": True, "status": "present"},
                    "treatmentHistory": {"resolvedValue": ["tamoxifen"], "status": "present"},
                },
                "completeness": {"coveragePct": 87.5, "domains": {"breastOncology": True}},
                "gaps": [],
            },
            "facts": {
                "age": {"value": 58},
                "sex": {"value": "female"},
                "keyLabs": {"value": {"hemoglobin": {"value": 12.1}}},
                "conditionHistory": {"value": ["metastatic breast carcinoma"]},
                "procedureHistory": {"value": ["breast biopsy"]},
                "medicationHistory": {"value": ["tamoxifen"]},
                "oncologySignals": {
                    "value": {
                        "breast_oncology": {"present": True, "matches": ["breast carcinoma"], "supportRefs": ["Condition/1"]},
                        "breast_biopsy": {"present": True, "matches": ["breast biopsy"], "supportRefs": ["Procedure/1"]},
                        "stage_or_extent": {"present": True, "matches": ["stage iv"], "supportRefs": ["Condition/2"]},
                        "oncology_treatment": {"present": True, "matches": ["tamoxifen"], "supportRefs": ["MedicationRequest/1"]},
                        "receptor_status": {"present": True, "matches": ["er positive", "her2 negative"], "supportRefs": ["Observation/1"]},
                    }
                },
                "receptorStatus": {"value": {"er": "positive", "pr": "positive", "her2": "negative", "supportRefs": ["Observation/1"]}},
                "performanceStatus": {"value": {"scale": "ECOG", "value": 1, "supportRefs": ["Observation/4"]}},
                "stageOrExtent": {"value": {"stage": "stage iv", "metastatic": True, "supportRefs": ["Condition/2"]}},
                "oncologyTreatmentHistory": {"value": {"terms": ["tamoxifen"], "supportRefs": ["MedicationRequest/1"]}},
            },
        }
        profile_doc = {
            "patientRef": "Patient/9",
            "gender": "female",
            "age": 58,
            "conditionTerms": ["breast carcinoma"],
            "featureTerms": ["breast cancer", "tamoxifen"],
            "resourceEventCount": 22,
        }

        assessment_input = build_oncology_cohort_assessment_input(
            patient_ref="Patient/9",
            facts_doc=facts_doc,
            profile_doc=profile_doc,
        )
        oncology_cohort = build_breast_oncology_cohort_document(
            patient_ref="Patient/9",
            assessment_input=assessment_input,
            source_dataset={"db": "hc_fhir", "collection": "fhir_resources"},
        )
        match_profile = build_patient_match_profile_v2(
            profile_doc=profile_doc,
            evidence_profile=facts_doc["patientEvidenceProfile"],
            oncology_cohort=oncology_cohort,
            normalize_text_fn=normalize_text,
        )

        self.assertEqual(match_profile["oncologyCohort"]["candidateCategory"], "confirmed_breast_oncology")
        self.assertEqual(match_profile["oncologyCohort"]["matchingReadinessTier"], "ready")
        self.assertTrue(match_profile["cohortMembership"]["authoritativeBreastOncology"])
        self.assertTrue(match_profile["cohortMembership"]["retailOutreachReady"])
        self.assertEqual(match_profile["identity"]["identityTrust"], "linked_composite")
        self.assertEqual(match_profile["runtimeReadiness"]["mode"], "authoritative_oncology")
        self.assertEqual(match_profile["oncologyCohortAssessment"]["authoritativeRejectedReasons"], [])
        self.assertEqual(match_profile["oncologyCohortAssessment"]["outreachRejectedReasons"], [])

    def test_unlinked_confirmed_ready_patient_is_blocked_until_authoritative_link_exists(self):
        match_profile = build_patient_match_profile_v2(
            profile_doc={"patientRef": "Patient/11", "gender": "female", "age": 61},
            evidence_profile={
                "schemaVersion": PATIENT_EVIDENCE_PROFILE_VERSION,
                "generatedAt": "2026-04-04T10:00:00Z",
                "identity": {"canonicalPatientKey": "pat_11", "linkageStatus": "unlinked", "linkageConfidence": 0.0, "linkageConflicts": []},
                "demographics": {"sex": "female", "ageYears": 61, "birthDate": "1965-01-01"},
                "oncology": {
                    "primaryCancer": {"status": "present"},
                    "stageGroup": {"value": "stage iv"},
                    "performanceStatus": {"value": 1},
                    "tumorMarkers": {"ER": {"status": "positive"}, "HER2": {"status": "negative"}},
                    "metastatic": {"status": "present"},
                },
                "screeningImaging": {"present": False},
                "completeness": {"coveragePct": 80.0, "domains": {"breastOncology": True, "receptorStatus": True}},
                "resolvedFeatures": {
                    "primaryCancer": {"resolvedValue": {"code": "breast_carcinoma"}, "status": "present"},
                    "age": {"resolvedValue": 61, "status": "present"},
                    "sex": {"resolvedValue": "female", "status": "present"},
                    "ER": {"resolvedValue": "positive", "status": "positive"},
                    "HER2": {"resolvedValue": "negative", "status": "negative"},
                    "ECOG": {"resolvedValue": 1, "status": "present"},
                    "stageGroup": {"resolvedValue": "stage iv", "status": "present"},
                    "metastaticFlag": {"resolvedValue": True, "status": "present"},
                    "treatmentHistory": {"resolvedValue": ["fulvestrant"], "status": "present"},
                    "keyLabs": {"resolvedValue": {"hemoglobin": {"value": 11.8}}, "status": "present"},
                },
                "treatments": {"priorSystemicTherapy": {"status": "present", "terms": ["fulvestrant"]}},
                "gaps": [],
            },
            oncology_cohort={
                "candidateCategory": "confirmed_breast_oncology",
                "matchingReadinessTier": "ready",
                "coveragePct": 82.0,
                "criticalMissingDomains": [],
            },
            normalize_text_fn=normalize_text,
        )

        self.assertFalse(match_profile["cohortMembership"]["authoritativeBreastOncology"])
        self.assertFalse(match_profile["cohortMembership"]["retailOutreachReady"])
        self.assertFalse(match_profile["cohortMembership"]["pharmacyPrescreen"])
        self.assertEqual(match_profile["identity"]["identityTrust"], "unresolved_identity")
        self.assertFalse(match_profile["runtimeReadiness"]["benchmarkRunnable"])
        self.assertFalse(match_profile["runtimeReadiness"]["outreachRunnable"])
        self.assertFalse(match_profile["runtimeReadiness"]["runnable"])
        self.assertEqual(match_profile["runtimeReadiness"]["mode"], "linkage_required")
        self.assertEqual(match_profile["runtimeReadiness"]["reasonIfBlocked"], "authoritative_fhir_link_required")
        self.assertEqual(match_profile["runtimeReadiness"]["reasonIfBenchmarkBlocked"], "linkage_status::unlinked")
        self.assertEqual(match_profile["runtimeReadiness"]["reasonIfOutreachBlocked"], "linkage_status::unlinked")
        self.assertIn("linkage_status::unlinked", match_profile["oncologyCohortAssessment"]["authoritativeRejectedReasons"])
        self.assertIn("linkage_status::unlinked", match_profile["oncologyCohortAssessment"]["outreachRejectedReasons"])

    def test_background_patient_persists_explicit_authoritative_rejection_reasons(self):
        match_profile = build_patient_match_profile_v2(
            profile_doc={"patientRef": "Patient/10", "gender": "female", "age": 48},
            evidence_profile={
                "schemaVersion": PATIENT_EVIDENCE_PROFILE_VERSION,
                "generatedAt": "2026-04-04T10:00:00Z",
                "identity": {"canonicalPatientKey": "pat_10", "linkageStatus": "linked", "linkageConfidence": 0.97, "linkageConflicts": []},
                "demographics": {"sex": "female", "ageYears": 48, "birthDate": "1978-06-01"},
                "oncology": {"primaryCancer": {"status": "unknown"}, "tumorMarkers": {"ER": {"status": "unknown"}, "PR": {"status": "unknown"}, "HER2": {"status": "unknown"}}},
                "screeningImaging": {"present": False},
                "completeness": {"coveragePct": 20.0, "domains": {"breastOncology": False}},
                "resolvedFeatures": {
                    "primaryCancer": {"resolvedValue": None, "status": "unknown"},
                    "ER": {"resolvedValue": None, "status": "unknown"},
                    "HER2": {"resolvedValue": None, "status": "unknown"},
                    "treatmentHistory": {"resolvedValue": [], "status": "unknown"},
                },
                "gaps": [{"feature": "primaryCancer"}],
            },
            oncology_cohort={
                "candidateCategory": "background_population",
                "matchingReadinessTier": "not_candidate",
                "coveragePct": 20.0,
                "criticalMissingDomains": [],
            },
            normalize_text_fn=normalize_text,
        )

        self.assertFalse(match_profile["cohortMembership"]["authoritativeBreastOncology"])
        self.assertIn(
            "primary_cancer_not_present",
            match_profile["oncologyCohortAssessment"]["authoritativeRejectedReasons"],
        )
        self.assertIn(
            "candidate_category_not_confirmed::background_population",
            match_profile["oncologyCohortAssessment"]["authoritativeRejectedReasons"],
        )
        self.assertIn(
            "not_authoritative_breast_oncology",
            match_profile["oncologyCohortAssessment"]["outreachRejectedReasons"],
        )

    def test_curation_coverage_summary_counts_conflicts_and_gaps(self):
        summary = build_patient_curation_coverage_summary(
            facts_documents=[
                {
                    "patientRef": "Patient/1",
                    "patientEvidenceProfile": {
                        "schemaVersion": PATIENT_EVIDENCE_PROFILE_VERSION,
                        "identity": {"linkageStatus": "conflict"},
                        "screeningImaging": {"present": True},
                        "completeness": {"domains": {"demographics": True, "receptorStatus": False}},
                        "gaps": [{"feature": "receptorStatus"}, {"feature": "identityLinkage"}],
                    },
                },
                {
                    "patientRef": "Patient/2",
                    "patientEvidenceProfile": {
                        "schemaVersion": PATIENT_EVIDENCE_PROFILE_VERSION,
                        "identity": {"linkageStatus": "linked"},
                        "screeningImaging": {"present": False},
                        "completeness": {"domains": {"demographics": True, "receptorStatus": True}},
                        "gaps": [],
                    },
                },
            ],
            profile_documents=[
                {
                    "patientRef": "Patient/1",
                    "patientMatchProfile": {
                        "schemaVersion": PATIENT_MATCH_PROFILE_VERSION,
                        "identity": {"identityTrust": "unresolved_identity"},
                        "cohortMembership": {"authoritativeBreastOncology": False, "retailOutreachReady": False, "pharmacyPrescreen": False, "screeningOnly": False},
                        "runtimeReadiness": {"mode": "linkage_required"},
                    },
                },
            ],
        )

        self.assertEqual(summary["summaryVersion"], PATIENT_CURATION_COVERAGE_SUMMARY_VERSION)
        self.assertEqual(summary["totalEvidenceProfiles"], 2)
        self.assertEqual(summary["totalMatchProfiles"], 1)
        self.assertEqual(summary["conflictPatients"], 1)
        self.assertEqual(summary["screeningLinkedPatients"], 1)
        self.assertEqual(summary["linkageStatusCounts"]["linked"], 1)
        self.assertEqual(summary["identityTrustCounts"]["unresolved_identity"], 1)
        self.assertEqual(summary["cohortMembershipCounts"]["authoritativeBreastOncology"], 0)
        self.assertEqual(summary["cohortMembershipCounts"]["retailOutreachReady"], 0)
        self.assertEqual(summary["domainCoveragePct"]["demographics"], 100.0)
        self.assertEqual(summary["domainCoveragePct"]["receptorStatus"], 50.0)
        self.assertEqual(summary["topGapBacklog"][0]["feature"], "identityLinkage")


if __name__ == "__main__":
    unittest.main()
