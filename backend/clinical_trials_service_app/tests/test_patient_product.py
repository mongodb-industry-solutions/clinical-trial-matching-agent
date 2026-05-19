from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("MONGODB_CONNECTION_STRING", "mongodb://localhost:27017")

from app.services.patient_product import ScreenedPatientNotFoundError, select_random_patient_workspace


class ClinicalTrialsPatientProductTests(unittest.TestCase):
    @patch("app.services.patient_product.random.choice", side_effect=lambda rows: rows[0])
    @patch("app.services.patient_product.get_patient_workspace_detail")
    @patch("app.services.patient_product.list_patient_catalog")
    def test_random_workspace_prefers_authoritative_runnable_patient(
        self,
        list_patient_catalog_mock,
        get_patient_workspace_detail_mock,
        _random_choice_mock,
    ) -> None:
        list_patient_catalog_mock.return_value = {
            "patients": [
                {
                    "patientRef": "Patient/authoritative",
                    "cohortMembership": {"authoritativeBreastOncology": True},
                    "runtimeReadiness": {"runnable": True},
                    "mergeSummary": {"authoritativeScreeningLinked": True},
                    "identity": {"linkageStatus": "linked", "identityTrust": "authoritative"},
                    "screeningSummary": {"appointmentId": "A-1"},
                    "displayName": "Patient authoritative",
                },
                {
                    "patientRef": "Patient/fallback",
                    "cohortMembership": {"authoritativeBreastOncology": False},
                    "runtimeReadiness": {"runnable": True},
                    "mergeSummary": {"authoritativeScreeningLinked": True},
                },
            ]
        }
        get_patient_workspace_detail_mock.return_value = {
            "patientRef": "Patient/authoritative",
            "patientSummary": {
                "patientRef": "Patient/authoritative",
                "displayName": "Patient authoritative",
                "identity": {"linkageStatus": "linked", "identityTrust": "authoritative"},
                "mergeSummary": {"authoritativeScreeningLinked": True},
                "screeningSummary": {"appointmentId": "A-1"},
            },
            "patientEvidenceProfile": {"identity": {"linkageStatus": "linked"}},
            "patientMatchProfile": {"runtimeReadiness": {"runnable": True}},
            "screeningSummary": {"appointmentId": "A-1", "leafyPatientId": "P-1"},
            "sourceCollections": {"profiles": "pharma_patient_profiles"},
        }

        payload = select_random_patient_workspace()

        self.assertEqual(payload["patientRef"], "Patient/authoritative")
        self.assertEqual(payload["selectionMode"], "random_authoritative_runnable_workspace_patient")
        self.assertEqual(payload["appointmentId"], "A-1")
        self.assertEqual(payload["patientLink"]["syntheaPatientRef"], "Patient/authoritative")
        get_patient_workspace_detail_mock.assert_called_once_with("Patient/authoritative")

    @patch("app.services.patient_product.random.choice", side_effect=lambda rows: rows[0])
    @patch("app.services.patient_product.get_patient_workspace_detail")
    @patch("app.services.patient_product.list_patient_catalog")
    def test_random_workspace_falls_back_to_any_workspace_row_when_needed(
        self,
        list_patient_catalog_mock,
        get_patient_workspace_detail_mock,
        _random_choice_mock,
    ) -> None:
        list_patient_catalog_mock.return_value = {
            "patients": [
                {
                    "patientRef": "Patient/catalog-only",
                    "cohortMembership": {"authoritativeBreastOncology": False},
                    "runtimeReadiness": {"runnable": False},
                    "mergeSummary": {"authoritativeScreeningLinked": False},
                    "identity": {"linkageStatus": "candidate_only", "identityTrust": "screening_only"},
                    "displayName": "Patient catalog-only",
                }
            ]
        }
        get_patient_workspace_detail_mock.return_value = {
            "patientRef": "Patient/catalog-only",
            "patientSummary": {
                "patientRef": "Patient/catalog-only",
                "displayName": "Patient catalog-only",
                "identity": {"linkageStatus": "candidate_only", "identityTrust": "screening_only"},
                "mergeSummary": {"authoritativeScreeningLinked": False},
            },
            "patientEvidenceProfile": {},
            "patientMatchProfile": {},
            "screeningSummary": {},
            "sourceCollections": {},
        }

        payload = select_random_patient_workspace()

        self.assertEqual(payload["patientRef"], "Patient/catalog-only")
        self.assertEqual(payload["selectionMode"], "random_workspace_patient")
        self.assertEqual(payload["patientContext"]["patientName"], "Patient catalog-only")

    @patch("app.services.patient_product.select_random_screened_patient")
    @patch("app.services.patient_product.list_patient_catalog")
    def test_random_workspace_uses_screened_fallback_when_catalog_is_empty(
        self,
        list_patient_catalog_mock,
        select_random_screened_patient_mock,
    ) -> None:
        list_patient_catalog_mock.return_value = {"patients": []}
        select_random_screened_patient_mock.return_value = {
            "patientRef": "Patient/screened",
            "patientLink": {"syntheaPatientRef": "Patient/screened", "leafyPatientId": "P-9"},
            "screeningSummary": {"appointmentId": "A-9"},
            "selectionMode": "random_authoritative_screened_synthea_patient",
        }

        payload = select_random_patient_workspace()

        self.assertEqual(payload["patientRef"], "Patient/screened")
        self.assertEqual(
            payload["selectionMode"],
            "random_authoritative_screened_synthea_patient_fallback",
        )
        self.assertEqual(payload["appointmentId"], "A-9")

    @patch("app.services.patient_product.select_random_screened_patient", side_effect=ScreenedPatientNotFoundError("none"))
    @patch("app.services.patient_product.list_patient_catalog")
    def test_random_workspace_raises_when_no_catalog_or_screened_patient_exists(
        self,
        list_patient_catalog_mock,
        _select_random_screened_patient_mock,
    ) -> None:
        list_patient_catalog_mock.return_value = {"patients": []}

        with self.assertRaises(ScreenedPatientNotFoundError):
            select_random_patient_workspace()


if __name__ == "__main__":
    unittest.main()
