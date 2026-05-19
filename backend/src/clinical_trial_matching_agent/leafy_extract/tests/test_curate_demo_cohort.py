import unittest

from curate_demo_cohort import (
    _candidate_refs_from_link_row,
    _derive_target_rows,
    _derive_pending_link_rows_from_screening,
    _overlay_payload_from_entry,
    _select_target_patients,
)
from services.pharma_services import apply_patient_curation_overlay_to_fact_doc


class _FakeCollection:
    def __init__(self, rows):
        self.rows = list(rows or [])

    def find(self, *_args, **_kwargs):
        return _FakeCursor(self.rows)


class _FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows or [])

    def sort(self, field_name, direction):
        reverse = int(direction or 1) < 0
        self.rows = sorted(self.rows, key=lambda row: row.get(field_name) or "", reverse=reverse)
        return self

    def limit(self, count):
        self.rows = self.rows[: int(count or 0)]
        return self

    def __iter__(self):
        return iter(self.rows)


class CurateDemoCohortTests(unittest.TestCase):
    def test_select_target_patients_prefers_confirmed_ready_rows(self):
        selected = _select_target_patients(
            [
                {
                    "patientRef": "Patient/limited",
                    "candidateCategory": "confirmed_breast_oncology",
                    "matchingReadinessTier": "limited",
                    "enrichmentPriorityScore": 100,
                    "coveragePct": 45.0,
                },
                {
                    "patientRef": "Patient/ready",
                    "candidateCategory": "confirmed_breast_oncology",
                    "matchingReadinessTier": "ready",
                    "enrichmentPriorityScore": 80,
                    "coveragePct": 70.0,
                },
                {
                    "patientRef": "Patient/probable",
                    "candidateCategory": "probable_breast_oncology",
                    "matchingReadinessTier": "partial",
                    "enrichmentPriorityScore": 95,
                    "coveragePct": 60.0,
                },
            ],
            limit=2,
        )

        self.assertEqual([row["patientRef"] for row in selected], ["Patient/ready", "Patient/limited"])

    def test_candidate_refs_include_suggested_and_candidate_options(self):
        refs = _candidate_refs_from_link_row(
            {
                "suggestedSyntheaPatientRef": "alpha",
                "syntheaPatientRef": "",
                "candidateOptions": [
                    {"patientRef": "Patient/beta"},
                    {"patientRef": "gamma"},
                    {"patientRef": "Patient/beta"},
                ],
            }
        )

        self.assertEqual(refs, ["Patient/alpha", "Patient/beta", "Patient/gamma"])

    def test_overlay_payload_from_entry_returns_none_when_empty(self):
        payload = _overlay_payload_from_entry(
            {
                "curatedEvidence": {
                    "apply": True,
                    "receptorStatus": {},
                    "stageOrExtent": {},
                    "performanceStatus": {"scale": "ECOG", "value": None},
                    "oncologyTreatmentHistory": {"terms": []},
                    "oncologySignals": {},
                }
            }
        )

        self.assertIsNone(payload)

    def test_derive_target_rows_builds_runtime_cohort_without_materialized_collection(self):
        facts_collection = _FakeCollection(
            [
                {
                    "patientRef": "Patient/alpha",
                    "patientEvidenceProfile": {"identity": {"linkageStatus": "linked"}},
                    "facts": {},
                },
                {
                    "patientRef": "Patient/background",
                    "patientEvidenceProfile": {"identity": {"linkageStatus": "linked"}},
                    "facts": {},
                },
            ]
        )
        profiles_collection = _FakeCollection(
            [
                {"patientRef": "Patient/alpha", "profileVersion": "v1"},
                {"patientRef": "Patient/background", "profileVersion": "v1"},
            ]
        )

        def build_assessment_input(*, patient_ref, facts_doc=None, profile_doc=None):
            return {
                "patientRef": patient_ref,
                "hasProfile": bool(profile_doc),
                "linkageStatus": ((facts_doc or {}).get("patientEvidenceProfile") or {}).get("identity", {}).get("linkageStatus"),
            }

        def build_cohort_document(*, patient_ref, assessment_input):
            if patient_ref == "Patient/background":
                return {"patientRef": patient_ref, "candidateCategory": "background_population"}
            return {
                "patientRef": patient_ref,
                "candidateCategory": "confirmed_breast_oncology",
                "matchingReadinessTier": "ready",
                "coveragePct": 88.0,
                "criticalMissingDomains": [],
                "candidateReasons": ["runtime-derived"],
                "resolvedFeatureSummary": {"linkageStatus": assessment_input.get("linkageStatus")},
            }

        rows = _derive_target_rows(
            facts_collection=facts_collection,
            profiles_collection=profiles_collection,
            build_assessment_input=build_assessment_input,
            build_cohort_document=build_cohort_document,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["patientRef"], "Patient/alpha")
        self.assertEqual(rows[0]["candidateCategory"], "confirmed_breast_oncology")

    def test_derive_pending_link_rows_from_screening_filters_to_target_refs(self):
        reports_collection = _FakeCollection(
            [
                {"_id": "report-1", "appointmentID": "appt-1", "patientID": "leafy-1", "user": "Alice", "date": "2026-04-18"},
                {"_id": "report-2", "appointmentID": "appt-2", "patientID": "leafy-2", "user": "Beth", "date": "2026-04-17"},
            ]
        )

        def build_patient_context(*, appointment_id, patient_id=None):
            if appointment_id == "appt-1":
                return (
                    {"appointmentId": "appt-1", "patientId": patient_id, "patientName": "Alice", "reportId": "report-1", "syntheaPatientRef": ""},
                    {"date": "2026-04-18"},
                    {},
                )
            return (
                {"appointmentId": "appt-2", "patientId": patient_id, "patientName": "Beth", "reportId": "report-2", "syntheaPatientRef": ""},
                {"date": "2026-04-17"},
                {},
            )

        def infer_analog_patients(patient_context, limit=4):
            if patient_context.get("appointmentId") == "appt-1":
                return ([{"patientRef": "Patient/alpha", "sharedKeywordCount": 4, "resourceEventCount": 9}], [])
            return ([{"patientRef": "Patient/other", "sharedKeywordCount": 2, "resourceEventCount": 3}], [])

        rows = _derive_pending_link_rows_from_screening(
            reports_collection=reports_collection,
            build_patient_context=build_patient_context,
            infer_analog_patients=infer_analog_patients,
            target_refs={"Patient/alpha"},
            max_pending=5,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["leafyAppointmentId"], "appt-1")
        self.assertEqual(rows[0]["suggestedSyntheaPatientRef"], "Patient/alpha")
        self.assertEqual(rows[0]["candidateOptions"][0]["sharedKeywordCount"], 4)

    def test_apply_patient_curation_overlay_to_fact_doc_merges_curated_oncology_domains(self):
        base_fact_doc = {
            "patientRef": "Patient/alpha",
            "facts": {
                "conditionHistory": {"value": ["breast cancer"], "source": "synthea-facts"},
                "procedureHistory": {"value": [], "source": "synthea-facts"},
                "oncologySignals": {
                    "value": {
                        "breast_oncology": {"present": True, "matches": ["breast"], "supportRefs": ["Condition/1"]},
                        "breast_biopsy": {"present": False, "matches": [], "supportRefs": []},
                    },
                    "source": "synthea-facts",
                },
                "receptorStatus": {"value": {"er": None, "pr": None, "her2": None, "matches": [], "supportRefs": []}, "source": "synthea-facts"},
                "performanceStatus": {"value": {"scale": "ECOG", "value": None, "matches": [], "supportRefs": []}, "source": "synthea-facts"},
                "stageOrExtent": {"value": {"stage": None, "tnm": [], "metastatic": False, "locallyAdvanced": False, "matches": [], "supportRefs": []}, "source": "synthea-facts"},
                "oncologyTreatmentHistory": {"value": {"terms": [], "supportRefs": []}, "source": "synthea-facts"},
            },
        }
        overlay_doc = {
            "overlayVersion": "patient_curation_overlay_v1",
            "updatedAt": "2026-04-19T10:00:00Z",
            "curationSource": "demo_manual_review",
            "reviewer": "oncology-demo",
            "note": "Approved for showcase cohort.",
            "factsOverlay": {
                "supportRefs": ["Report/demo-1"],
                "procedureHistoryTerms": ["breast biopsy"],
                "pathologySummary": "Invasive ductal carcinoma, ER positive, HER2 negative.",
                "receptorStatus": {
                    "er": "positive",
                    "pr": "positive",
                    "her2": "negative",
                    "supportRefs": ["Observation/receptor-1"],
                },
                "stageOrExtent": {
                    "stage": "stage iii",
                    "metastatic": False,
                    "locallyAdvanced": True,
                    "supportRefs": ["Condition/stage-1"],
                },
                "performanceStatus": {
                    "scale": "ECOG",
                    "value": 1,
                    "supportRefs": ["Observation/ecog-1"],
                },
                "oncologyTreatmentHistory": {
                    "terms": ["neoadjuvant chemotherapy"],
                    "supportRefs": ["MedicationRequest/tx-1"],
                },
            },
        }

        merged = apply_patient_curation_overlay_to_fact_doc(base_fact_doc, overlay_doc)
        facts = merged["facts"]

        self.assertEqual(facts["receptorStatus"]["value"]["er"], "positive")
        self.assertEqual(facts["receptorStatus"]["value"]["pr"], "positive")
        self.assertEqual(facts["receptorStatus"]["value"]["her2"], "negative")
        self.assertEqual(facts["stageOrExtent"]["value"]["stage"], "stage iii")
        self.assertFalse(facts["stageOrExtent"]["value"]["metastatic"])
        self.assertTrue(facts["stageOrExtent"]["value"]["locallyAdvanced"])
        self.assertEqual(facts["performanceStatus"]["value"]["value"], 1)
        self.assertIn("neoadjuvant chemotherapy", facts["oncologyTreatmentHistory"]["value"]["terms"])
        self.assertIn("breast biopsy", facts["procedureHistory"]["value"])
        self.assertTrue(facts["oncologySignals"]["value"]["breast_biopsy"]["present"])
        self.assertTrue(facts["oncologySignals"]["value"]["receptor_status"]["present"])
        self.assertTrue(merged["curationOverlay"]["applied"])


if __name__ == "__main__":
    unittest.main()
