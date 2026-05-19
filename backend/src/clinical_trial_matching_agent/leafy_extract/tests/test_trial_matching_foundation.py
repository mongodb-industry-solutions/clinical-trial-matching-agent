import re
import unittest
import logging
from datetime import datetime

from audit_trial_matching_dataset_readiness import build_dataset_readiness_summary
from routes.pharma_blueprint import compute_trial_match_ranking, run_patient_trial_match_pipeline
from services.pharma_services import (
    create_pharma_services,
    DETERMINISTIC_SCREENING_BACKFILL_LINK_STRATEGY,
    PHARMA_LINKAGE_CONTRACT_VERSION,
    PHARMA_TRIAL_DEFINITION_CANONICAL_VERSION,
    PHARMA_TRIAL_PREFILTER_CONTRACT_VERSION,
    SCREENING_CAPTURE_LINK_STRATEGY,
    build_trial_matching_evaluation_run_document,
    build_trial_matching_improvement_run_document,
    build_trial_definition_canonical,
    build_profile_query_from_trial_prefilter_contract,
    build_linkage_control_plane_summary_from_rows,
    build_linkage_control_plane_summary_from_screening_sources,
    build_sparse_oncology_domain_summary_from_inputs,
    score_trial_matching_control_plane_summary,
    build_trial_prefilter_contract,
    build_trial_query_for_patient_context,
    build_trial_control_plane_summary_from_inputs,
    classify_link_record,
    derive_synthetic_ecog_payload_from_facts,
    evaluate_patient_context_against_trial_prefilter,
    evaluate_profile_against_trial_prefilter,
    extract_embedded_synthea_link_strategy,
    extract_embedded_synthea_patient_ref,
    resolve_trial_definition_canonical,
    resolve_trial_prefilter_contract,
)
from services.pharma_evidence_tools import create_pharma_evidence_tools


def normalize_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def parse_int_value(value, default=None, minimum=None):
    try:
        parsed = int(value)
    except Exception:
        return default
    if minimum is not None and parsed < minimum:
        return minimum
    return parsed


def extract_resource_event_datetime(resource):
    for field in [
        resource.get("effectiveDateTime"),
        resource.get("recordedDate"),
        resource.get("authoredOn"),
        resource.get("date"),
    ]:
        if not field:
            continue
        try:
            return datetime.fromisoformat(str(field).replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            continue
    return None


def extract_codes_and_terms(resource):
    codes = []
    terms = []
    for key in ["code", "type", "medicationCodeableConcept", "valueCodeableConcept"]:
        codeable = resource.get(key) or {}
        text_value = normalize_text(codeable.get("text"))
        if text_value:
            terms.append(text_value)
        for coding in codeable.get("coding") or []:
            display_value = normalize_text((coding or {}).get("display"))
            code_value = normalize_text((coding or {}).get("code"))
            if display_value:
                terms.append(display_value)
            if code_value:
                codes.append(code_value)
    return codes, terms


def serialize_doc(value):
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value


def parse_iso_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def evaluate_numeric_window(operator, value, value_max, patient_value):
    if patient_value is None:
        return None
    if operator == "between":
        if value is None or value_max is None:
            return None
        return float(value) <= float(patient_value) <= float(value_max)
    if operator in {">=", ">"} and value is not None:
        return float(patient_value) >= float(value) if operator == ">=" else float(patient_value) > float(value)
    if operator in {"<=", "<"} and value is not None:
        return float(patient_value) <= float(value) if operator == "<=" else float(patient_value) < float(value)
    if operator == "=" and value is not None:
        return float(patient_value) == float(value)
    return None


class FakeCursor(list):
    def limit(self, count):
        return FakeCursor(self[:count])

    def sort(self, key, direction):
        reverse = int(direction or 1) < 0
        key_parts = str(key or "").split(".")

        def value_for(row):
            current = row
            for part in key_parts:
                if not isinstance(current, dict):
                    return None
                current = current.get(part)
            return current

        return FakeCursor(sorted(self, key=value_for, reverse=reverse))


class FakeSignalCollection:
    def __init__(self, *, patient_count, aggregate_rows, signal_hits):
        self.patient_count = patient_count
        self.aggregate_rows = list(aggregate_rows or [])
        self.signal_hits = {str(key).lower(): list(value) for key, value in (signal_hits or {}).items()}

    def count_documents(self, query):
        if query == {"resource.resourceType": "Patient"}:
            return self.patient_count
        if query == {"$or": [{"resourceType": "Patient"}, {"resource.resourceType": "Patient"}]}:
            return self.patient_count
        return 0

    def aggregate(self, _pipeline):
        return list(self.aggregate_rows)

    def find(self, query=None, projection=None):
        del projection
        regex_value = ""
        for clause in query.get("$or") or []:
            for predicate in clause.values():
                regex_value = normalize_text((predicate or {}).get("$regex"))
                if regex_value:
                    break
            if regex_value:
                break
        return list(self.signal_hits.get(regex_value, []))


class FakeFactsCollection:
    def __init__(self, document=None):
        self.document = document

    def find_one(self, query, sort=None):
        del sort
        if not isinstance(self.document, dict):
            return None
        if query.get("patientRef") != self.document.get("patientRef"):
            return None
        return self.document


class FakeSyntheaCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find(self, query=None, projection=None):
        del query, projection
        return FakeCursor(list(self.rows))


class FakeTrialCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def count_documents(self, _query):
        return len(self.rows)

    def list_search_indexes(self, *args, **kwargs):
        del args, kwargs
        return []

    def find(self, query=None, projection=None):
        del query, projection
        return list(self.rows)

    def aggregate(self, pipeline):
        del pipeline
        return list(self.rows)


class FakeUpdateResult:
    def __init__(self, matched_count=1, modified_count=1):
        self.matched_count = matched_count
        self.modified_count = modified_count


class FakeMutableCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.update_calls = []

    def _set_path(self, row, dotted_key, value):
        parts = str(dotted_key or "").split(".")
        current = row
        for part in parts[:-1]:
            next_value = current.get(part)
            if not isinstance(next_value, dict):
                next_value = {}
                current[part] = next_value
            current = next_value
        current[parts[-1]] = value

    def _matches(self, row, query):
        if not query:
            return True
        if "$or" in query:
            return any(self._matches(row, clause) for clause in (query.get("$or") or []))
        for key, value in (query or {}).items():
            current = row
            for part in str(key).split("."):
                if not isinstance(current, dict) or part not in current:
                    return False
                current = current.get(part)
            if current != value:
                return False
        return True

    def find_one(self, query=None, sort=None):
        del sort
        for row in self.rows:
            if self._matches(row, query):
                return dict(row)
        return None

    def find(self, query=None, projection=None):
        del projection
        return FakeCursor([dict(row) for row in self.rows if self._matches(row, query)])

    def update_one(self, query, update, upsert=False):
        self.update_calls.append({"query": query, "update": update, "upsert": upsert})
        matched = False
        for index, row in enumerate(self.rows):
            if self._matches(row, query):
                matched = True
                if "$set" in update:
                    updated = dict(row)
                    for key, value in (update["$set"] or {}).items():
                        self._set_path(updated, key, value)
                    self.rows[index] = updated
                break
        if not matched and upsert:
            inserted = {}
            for key, value in (((update or {}).get("$set")) or {}).items():
                self._set_path(inserted, key, value)
            self.rows.append(inserted)
            matched = True
        return FakeUpdateResult(matched_count=1 if matched else 0, modified_count=1 if matched else 0)

    def update_many(self, query, update):
        self.update_calls.append({"query": query, "update": update, "upsert": False, "many": True})
        return FakeUpdateResult(matched_count=0, modified_count=0)

    def aggregate(self, pipeline, allowDiskUse=False):
        del pipeline, allowDiskUse
        return [dict(row) for row in self.rows]

    def distinct(self, key):
        values = []
        for row in self.rows:
            value = row.get(key)
            if value not in values and value not in {None, ""}:
                values.append(value)
        return values

    def estimated_document_count(self):
        return len(self.rows)


class FakeSyntheaExistenceCollection(FakeMutableCollection):
    def find_one(self, query=None, projection=None):
        del projection
        for row in self.rows:
            patient_id = ((row.get("resource") or {}).get("id")) or row.get("resourceId")
            if not patient_id:
                continue
            for clause in (query or {}).get("$or") or []:
                if clause == {"resourceType": "Patient", "resourceId": patient_id}:
                    return dict(row)
                if clause == {"resource.resourceType": "Patient", "resource.id": patient_id}:
                    return dict(row)
        return None


class TrialMatchingFoundationTests(unittest.TestCase):
    def build_evidence_tools(self, *, patient_facts_doc=None, synthea_rows=None):
        return create_pharma_evidence_tools(
            normalize_pharma_text_fn=normalize_text,
            parse_int_value_fn=parse_int_value,
            extract_resource_event_datetime_fn=extract_resource_event_datetime,
            extract_codes_and_terms_fn=extract_codes_and_terms,
            serialize_doc_fn=serialize_doc,
            build_pharma_trial_rules_fn=lambda **kwargs: [],
            parse_birads_values_from_text_fn=lambda _text: [],
            evaluate_numeric_window_fn=evaluate_numeric_window,
            tokenize_rule_text_fn=lambda text: [token for token in re.findall(r"[a-z0-9]+", normalize_text(text)) if token],
            parse_iso_datetime_fn=parse_iso_datetime,
            days_since_fn=lambda _value: None,
            normalize_patient_ref_fn=lambda value: value if str(value or "").startswith("Patient/") else f"Patient/{value}",
            db_pharma_patient_facts_collection=FakeFactsCollection(patient_facts_doc),
            db_synthea_resources_collection=FakeSyntheaCollection(synthea_rows),
            pharma_rule_risk_paths={"oncology": ["breast", "mammogram", "tamoxifen"]},
            pharma_rule_medication_tokens={"tamoxifen", "letrozole"},
            pharma_rule_condition_tokens={"carcinoma", "diabetes"},
        )

    def build_pharma_services_for_linkage(self, *, profile_rows=None, synthea_rows=None, report_rows=None, appointment_rows=None, link_rows=None):
        appointment_collection = FakeMutableCollection()
        if appointment_rows is not None:
            appointment_collection = FakeMutableCollection(appointment_rows)
        report_collection = FakeMutableCollection(report_rows or [])
        link_collection = FakeMutableCollection(link_rows or [])
        profile_collection = FakeMutableCollection(profile_rows or [])
        synthea_collection = FakeSyntheaExistenceCollection(
            synthea_rows
            or [{"resource": {"resourceType": "Patient", "id": "alpha"}}]
        )
        services = create_pharma_services(
            deps={
                "clinicaltrials_api_base": "https://example.org",
                "clinicaltrials_timeout_sec": 5,
                "pharma_resource_types": ["Patient", "Condition", "Observation"],
                "pharma_trail_keywords": ["breast", "mammogram", "biopsy"],
                "synthea_patient_ref_cache": {},
                "collection": appointment_collection,
                "db_clinical_reports_collection": report_collection,
                "db_leafy_synthea_links_collection": link_collection,
                "db_pharma_evaluation_runs_collection": FakeMutableCollection(),
                "db_pharma_feature_edges_collection": FakeMutableCollection(),
                "db_pharma_improvement_runs_collection": FakeMutableCollection(),
                "db_pharma_patient_facts_collection": FakeFactsCollection(),
                "db_pharma_oncology_cohort_collection": FakeMutableCollection(),
                "db_pharma_profiles_collection": profile_collection,
                "db_pharma_trials_collection": FakeTrialCollection(),
                "db_synthea_resources_collection": synthea_collection,
                "synthea_fhir_db_name": "synthea_runtime",
                "synthea_resources_collection_name": "fhir_resources",
                "parse_int_value_fn": parse_int_value,
                "safe_float_fn": lambda value, default=0.0: float(value) if value is not None else float(default),
                "serialize_doc_fn": serialize_doc,
                "voyage_api_key": None,
                "voyage_embedding_model": "voyage-3.5",
                "voyage_embed_text_fn": None,
                "voyage_embed_texts_fn": None,
            }
        )
        return services, link_collection, report_collection, appointment_collection

    def test_dataset_readiness_summary_requires_breast_specific_signals(self):
        collection = FakeSignalCollection(
            patient_count=30,
            aggregate_rows=[
                {"_id": "Patient", "count": 30},
                {"_id": "Observation", "count": 200},
            ],
            signal_hits={
                "breast": [{"_id": 1}],
                "mammogram": [{"_id": 2}],
                "her2": [{"_id": 3}],
                "trastuzumab": [{"_id": 4}],
                "bilirubin": [{"_id": 5}],
            },
        )

        summary = build_dataset_readiness_summary(
            collection,
            db_name="synthea_breast",
            collection_name="fhir_resources",
            min_patients=25,
        )

        self.assertTrue(summary["ready"])
        self.assertEqual(summary["patientCount"], 30)
        self.assertTrue(all(check["passed"] for check in summary["checks"]))

    def test_dataset_readiness_summary_accepts_canonical_search_index_signals(self):
        collection = FakeSignalCollection(
            patient_count=40,
            aggregate_rows=[
                {"_id": "Patient", "count": 40},
                {"_id": "Condition", "count": 80},
            ],
            signal_hits={
                "breast": [{"_id": 11}],
                "mammogram": [{"_id": 12}],
                "tnm": [{"_id": 13}],
                "tamoxifen": [{"_id": 14}],
                "creatinine": [{"_id": 15}],
            },
        )

        summary = build_dataset_readiness_summary(
            collection,
            db_name="synthea_runtime",
            collection_name="fhir_resources",
            min_patients=25,
        )

        self.assertTrue(summary["ready"])
        self.assertEqual(summary["resourceTypeCounts"]["Patient"], 40)

    def test_patient_facts_fill_missing_runtime_evidence_without_overwriting_leafy_core_fields(self):
        patient_facts_doc = {
            "patientRef": "Patient/1",
            "factVersion": "oncology_facts_v1",
            "facts": {
                "age": {"value": 67, "source": "synthea-facts"},
                "sex": {"value": "male", "source": "synthea-facts"},
                "procedureHistory": {"value": ["lumpectomy"], "source": "synthea-facts"},
                "oncologySignals": {
                    "value": {"breast_oncology": {"present": True, "matches": ["breast"], "supportRefs": ["Condition/1"]}},
                    "source": "synthea-facts",
                },
                "keyLabs": {
                    "value": {"bilirubin": {"value": 0.7, "unit": "mg/dL", "date": "2026-01-01 12:00:00"}},
                    "source": "synthea-facts",
                },
            },
        }
        tools = self.build_evidence_tools(patient_facts_doc=patient_facts_doc)

        evidence_pack = tools["build_patient_evidence_pack"](
            {"age": 52, "sex": "female"},
            synthea_patient_ref="Patient/1",
        )

        self.assertEqual(evidence_pack["facts"]["age"]["value"], 52)
        self.assertEqual(evidence_pack["facts"]["sex"]["value"], "female")
        self.assertEqual(evidence_pack["facts"]["procedureHistory"]["value"], ["lumpectomy"])
        self.assertIn("breast_oncology", evidence_pack["facts"]["oncologySignals"]["value"])
        self.assertIn("bilirubin", evidence_pack["facts"]["keyLabs"]["value"])
        self.assertEqual(evidence_pack["source_provenance"]["patientFactsVersion"], "oncology_facts_v1")
        self.assertGreaterEqual(evidence_pack["source_breakdown"]["syntheaFacts"], 3)
        self.assertEqual(evidence_pack["patientEvidenceProfile"]["schemaVersion"], "patient_evidence_profile_v2")
        self.assertEqual(evidence_pack["patientEvidenceProfile"]["demographics"]["sex"], "male")
        self.assertIsNone(evidence_pack["patientEvidenceProfile"]["demographics"]["screeningSnapshot"])

    def test_patient_evidence_pack_surfaces_linkage_conflicts_in_v2_profile(self):
        patient_facts_doc = {
            "patientRef": "Patient/1",
            "factVersion": "oncology_facts_v1",
            "generatedAt": "2026-04-03T10:00:00Z",
            "facts": {
                "age": {"value": 49, "source": "synthea-facts"},
                "sex": {"value": "male", "source": "synthea-facts"},
                "birthDate": {"value": "1976-06-28", "source": "synthea-facts"},
            },
        }
        tools = self.build_evidence_tools(patient_facts_doc=patient_facts_doc)

        evidence_pack = tools["build_patient_evidence_pack"](
            {
                "appointmentId": "appt-1",
                "reportId": "report-1",
                "age": 65,
                "sex": "female",
                "biradsValues": [5],
                "conclusions": "Suspicious right breast finding.",
            },
            report_doc={
                "_id": "report-1",
                "appointmentID": "appt-1",
                "series": [
                    {"imageMetadata": {"Laterality": "R", "biRads": {"BIRAD": 5}}},
                ],
            },
            synthea_patient_ref="Patient/1",
            linkage_payload={"linkageStatus": "linked", "linkageConfidence": 1.0},
        )

        self.assertEqual(evidence_pack["patientEvidenceProfile"]["identity"]["linkageStatus"], "conflict")
        self.assertGreaterEqual(
            len(evidence_pack["patientEvidenceProfile"]["identity"]["linkageConflicts"]),
            2,
        )

    def test_completed_medications_remain_history_not_active(self):
        tools = self.build_evidence_tools(
            synthea_rows=[
                {
                    "resource": {
                        "resourceType": "MedicationRequest",
                        "status": "completed",
                        "medicationCodeableConcept": {"text": "Tamoxifen"},
                    }
                },
                {
                    "resource": {
                        "resourceType": "MedicationRequest",
                        "status": "active",
                        "medicationCodeableConcept": {"text": "Letrozole"},
                    }
                },
            ]
        )

        evidence_pack = tools["build_patient_evidence_pack"]({}, synthea_patient_ref="Patient/1")
        active_meds = evidence_pack["facts"]["activeMedications"]["value"]
        med_history = evidence_pack["facts"]["medicationHistory"]["value"]

        self.assertIn("tamoxifen", med_history)
        self.assertIn("letrozole", med_history)
        self.assertNotIn("tamoxifen", active_meds)
        self.assertIn("letrozole", active_meds)

    def test_patient_facts_merge_explicit_oncology_domains(self):
        patient_facts_doc = {
            "patientRef": "Patient/1",
            "factVersion": "oncology_facts_v1",
            "facts": {
                "receptorStatus": {
                    "value": {"hr": "negative", "er": "negative", "pr": "negative", "her2": "negative", "matches": ["triple_negative"]},
                    "source": "synthea-facts",
                },
                "performanceStatus": {
                    "value": {"scale": "ECOG", "value": 1, "matches": ["ecog:1"]},
                    "source": "synthea-facts",
                },
                "stageOrExtent": {
                    "value": {"stage": "stage iv", "metastatic": True, "matches": ["stage iv", "metastatic"]},
                    "source": "synthea-facts",
                },
                "oncologyTreatmentHistory": {
                    "value": {"terms": ["paclitaxel", "trastuzumab"]},
                    "source": "synthea-facts",
                },
            },
        }
        tools = self.build_evidence_tools(patient_facts_doc=patient_facts_doc)

        evidence_pack = tools["build_patient_evidence_pack"]({}, synthea_patient_ref="Patient/1")

        self.assertEqual(evidence_pack["facts"]["receptorStatus"]["value"]["her2"], "negative")
        self.assertEqual(evidence_pack["facts"]["receptorStatus"]["value"]["hr"], "negative")
        self.assertEqual(evidence_pack["facts"]["performanceStatus"]["value"]["value"], 1)
        self.assertTrue(evidence_pack["facts"]["stageOrExtent"]["value"]["metastatic"])
        self.assertIn("paclitaxel", evidence_pack["facts"]["oncologyTreatmentHistory"]["value"]["terms"])

    def test_raw_synthea_observation_can_extract_ecog_into_evidence_pack(self):
        tools = self.build_evidence_tools(
            synthea_rows=[
                {
                    "resource": {
                        "resourceType": "Observation",
                        "code": {"text": "ECOG performance status"},
                        "valueInteger": 1,
                        "effectiveDateTime": "2026-01-10T10:00:00Z",
                    }
                }
            ]
        )

        evidence_pack = tools["build_patient_evidence_pack"]({}, synthea_patient_ref="Patient/1")

        self.assertEqual(evidence_pack["facts"]["performanceStatus"]["value"]["value"], 1)
        self.assertIn("ecog:1", evidence_pack["facts"]["performanceStatus"]["value"]["matches"])

    def test_synthetic_ecog_observation_can_extract_numeric_status_into_evidence_pack(self):
        tools = self.build_evidence_tools(
            synthea_rows=[
                {
                    "resource": {
                        "resourceType": "Observation",
                        "code": {
                            "coding": [
                                {
                                    "system": "http://loinc.org",
                                    "code": "89247-1",
                                    "display": "ECOG Performance Status score",
                                }
                            ],
                            "text": "ECOG performance status",
                        },
                        "method": {"text": "Synthetic demo enrichment"},
                        "note": [
                            {
                                "text": "Synthetic demo ECOG observation derived from normalized oncology facts."
                            }
                        ],
                        "valueInteger": 2,
                        "effectiveDateTime": "2026-01-10T10:00:00Z",
                    }
                }
            ]
        )

        evidence_pack = tools["build_patient_evidence_pack"]({}, synthea_patient_ref="Patient/1")

        self.assertEqual(evidence_pack["facts"]["performanceStatus"]["value"]["value"], 2)
        self.assertIn("ecog:2", evidence_pack["facts"]["performanceStatus"]["value"]["matches"])
        self.assertIn("ecog_observation", evidence_pack["facts"]["performanceStatus"]["value"]["matches"])

    def test_raw_synthea_receptor_assays_can_extract_status_into_evidence_pack(self):
        tools = self.build_evidence_tools(
            synthea_rows=[
                {
                    "resource": {
                        "resourceType": "Observation",
                        "code": {"text": "Estrogen+Progesterone receptor Ag [Presence] in Tissue by Immune stain"},
                        "valueCodeableConcept": {"text": "Positive"},
                        "effectiveDateTime": "2026-01-10T10:00:00Z",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "code": {"text": "HER2 [Presence] in Breast cancer specimen by FISH"},
                        "valueString": "Negative",
                        "effectiveDateTime": "2026-01-11T10:00:00Z",
                    }
                },
            ]
        )

        evidence_pack = tools["build_patient_evidence_pack"]({}, synthea_patient_ref="Patient/1")

        self.assertEqual(evidence_pack["facts"]["receptorStatus"]["value"]["er"], "positive")
        self.assertEqual(evidence_pack["facts"]["receptorStatus"]["value"]["pr"], "positive")
        self.assertEqual(evidence_pack["facts"]["receptorStatus"]["value"]["hr"], "positive")
        self.assertEqual(evidence_pack["facts"]["receptorStatus"]["value"]["her2"], "negative")

    def test_leafy_narrative_can_extract_hr_her2_and_ecog_into_evidence_pack(self):
        tools = self.build_evidence_tools()

        evidence_pack = tools["build_patient_evidence_pack"](
            {
                "clinicalNotes": "Patient has hormone receptor positive (HR+) and HER2- metastatic disease with ECOG performance status 1.",
                "conclusions": "First-line systemic therapy planned.",
                "keywords": ["metastatic", "systemic therapy"],
            },
            synthea_patient_ref="Patient/1",
        )

        self.assertEqual(evidence_pack["facts"]["receptorStatus"]["value"]["hr"], "positive")
        self.assertEqual(evidence_pack["facts"]["receptorStatus"]["value"]["her2"], "negative")
        self.assertEqual(evidence_pack["facts"]["performanceStatus"]["value"]["value"], 1)
        self.assertTrue(evidence_pack["facts"]["stageOrExtent"]["value"]["metastatic"])

    def test_negated_exclusion_condition_is_downgraded_to_unknown_for_manual_review(self):
        tools = self.build_evidence_tools()
        result = tools["evaluate_rule_with_evidence"](
            {
                "rule_id": "rule-001",
                "type": "exclusion",
                "text": "No history of diabetes",
                "concept_tags": ["conditions"],
                "data_needed": ["conditionHistory"],
                "rule_kind": "patient_eligibility",
                "primary_criterion_group": "conditions",
            },
            {
                "facts": {
                    "conditionHistory": {"value": ["diabetes mellitus"], "source": "synthea"},
                }
            },
        )

        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["evaluationMode"], "parser_ambiguous_negation")
        self.assertEqual(result["criterionState"], "unknown")
        self.assertEqual(result["eligibilityImpact"], "unknown")
        self.assertTrue(result["requiresManualReview"])
        self.assertIn("ruleNegationInterpretation", result["missingData"])

    def test_evaluate_rule_with_performance_status_uses_explicit_fact(self):
        tools = self.build_evidence_tools()
        result = tools["evaluate_rule_with_evidence"](
            {
                "rule_id": "rule-ecog-1",
                "type": "inclusion",
                "text": "Eastern Cooperative Oncology Group (ECOG) performance status of 0 or 1",
                "concept_tags": ["performance_status"],
                "data_needed": ["performanceStatus"],
                "rule_kind": "patient_eligibility",
                "primary_criterion_group": "performance_status",
            },
            {
                "facts": {
                    "performanceStatus": {
                        "value": {"scale": "ECOG", "value": 1, "matches": ["ecog:1"]},
                        "source": "synthea-facts",
                    }
                }
            },
        )

        self.assertEqual(result["status"], "PASS")
        self.assertIn("facts.performanceStatus", result["evidencePointers"])

    def test_evaluate_rule_with_receptor_status_uses_explicit_fact(self):
        tools = self.build_evidence_tools()
        result = tools["evaluate_rule_with_evidence"](
            {
                "rule_id": "rule-rec-1",
                "type": "inclusion",
                "text": "Histologically confirmed metastatic breast cancer with negative status for ER, PR, and HER-2",
                "concept_tags": ["receptor_status"],
                "data_needed": ["receptorStatus"],
                "rule_kind": "patient_eligibility",
                "primary_criterion_group": "receptor_status",
            },
            {
                "facts": {
                    "receptorStatus": {
                        "value": {"hr": "negative", "er": "negative", "pr": "negative", "her2": "negative", "matches": ["triple_negative"]},
                        "source": "synthea-facts",
                    }
                }
            },
        )

        self.assertEqual(result["status"], "PASS")
        self.assertIn("facts.receptorStatus", result["evidencePointers"])

    def test_evaluate_rule_with_hr_positive_her2_negative_uses_explicit_fact(self):
        tools = self.build_evidence_tools()
        result = tools["evaluate_rule_with_evidence"](
            {
                "rule_id": "rule-rec-hr-1",
                "type": "inclusion",
                "text": "Has a diagnosis of hormone receptor positive (HR+)/human epidermal growth factor receptor 2 (HER2)- invasive breast carcinoma",
                "concept_tags": ["receptor_status"],
                "data_needed": ["receptorStatus"],
                "rule_kind": "patient_eligibility",
                "primary_criterion_group": "receptor_status",
            },
            {
                "facts": {
                    "receptorStatus": {
                        "value": {"hr": "positive", "her2": "negative", "matches": ["hr:positive", "her2:negative"]},
                        "source": "synthea-facts",
                    }
                }
            },
        )

        self.assertEqual(result["status"], "PASS")
        self.assertIn("facts.receptorStatus", result["evidencePointers"])

    def test_evaluate_rule_with_stage_or_extent_uses_explicit_fact(self):
        tools = self.build_evidence_tools()
        result = tools["evaluate_rule_with_evidence"](
            {
                "rule_id": "rule-stage-1",
                "type": "inclusion",
                "text": "Metastatic or inoperable locally advanced breast cancer",
                "concept_tags": ["stage_or_extent"],
                "data_needed": ["stageOrExtent"],
                "rule_kind": "patient_eligibility",
                "primary_criterion_group": "stage_or_extent",
            },
            {
                "facts": {
                    "stageOrExtent": {
                        "value": {"stage": "stage iv", "metastatic": True, "locallyAdvanced": False, "matches": ["stage iv", "metastatic"]},
                        "source": "synthea-facts",
                    }
                }
            },
        )

        self.assertEqual(result["status"], "PASS")
        self.assertIn("facts.stageOrExtent", result["evidencePointers"])

    def test_evaluate_rule_with_oncology_treatment_history_uses_explicit_fact(self):
        tools = self.build_evidence_tools()
        result = tools["evaluate_rule_with_evidence"](
            {
                "rule_id": "rule-tx-1",
                "type": "inclusion",
                "text": "Subjects who have not received prior systemic therapy for advanced breast cancer are eligible for the study",
                "concept_tags": ["oncology_treatment"],
                "data_needed": ["oncologyTreatmentHistory"],
                "rule_kind": "patient_eligibility",
                "primary_criterion_group": "oncology_treatment",
            },
            {
                "facts": {
                    "oncologyTreatmentHistory": {
                        "value": {"terms": ["surgery"]},
                        "source": "synthea-facts",
                    }
                }
            },
        )

        self.assertEqual(result["status"], "PASS")
        self.assertIn("facts.oncologyTreatmentHistory", result["evidencePointers"])

    def test_structured_match_returns_explicit_contract_and_retrieval_sections(self):
        tools = self.build_evidence_tools()
        structured = tools["evaluate_trial_structured_match"](
            {
                "trialId": "NCT123",
                "atomicRules": [
                    {
                        "rule_id": "rule-001",
                        "type": "inclusion",
                        "text": "Age at least 18 years",
                        "concept_tags": ["age"],
                        "data_needed": ["age"],
                        "rule_kind": "patient_eligibility",
                        "primary_criterion_group": "age",
                        "operator": ">=",
                        "value": 18,
                        "value_max": None,
                        "rule_tokens": ["age", "18", "years"],
                    }
                ],
            },
            {
                "keywords": ["breast"],
                "evidencePack": {
                    "facts": {
                        "age": {"value": 55, "source": "leafy"},
                    }
                },
            },
            semantic_query="breast age trial",
        )

        self.assertEqual(structured["matchingContractVersion"], "trial_matching_contract_v2")
        self.assertIn("criterionEvaluations", structured)
        self.assertEqual(len(structured["criterionEvaluations"]), 1)
        self.assertIn("retrievalSignals", structured)
        self.assertFalse(structured["retrievalSignals"]["retrievalAffectsEligibility"])
        self.assertIn("contractVersion", structured["decisionSummary"])
        self.assertEqual(structured["decisionSummary"]["contractVersion"], "trial_matching_contract_v2")
        self.assertEqual(structured["ruleEvaluations"][0]["evidenceSources"], ["leafy_context"])
        self.assertIn("evidenceSourceCounts", structured["decisionSummary"])
        self.assertEqual(structured["decisionSummary"]["evidenceSourceCounts"]["leafy_context"], 1)

    def test_trial_definition_canonical_builds_cdisc_aligned_criteria(self):
        canonical = build_trial_definition_canonical(
            trial_doc={
                "trialId": "NCT-CDISC-1",
                "nctId": "NCT-CDISC-1",
                "title": "Breast Trial",
                "source": "ClinicalTrials.gov API v2",
                "pharma_trial_rules": [
                    {
                        "rule_id": "rule-001",
                        "type": "inclusion",
                        "text": "Age at least 18 years",
                        "concept_tags": ["age"],
                        "data_needed": ["age"],
                        "rule_kind": "patient_eligibility",
                        "primary_criterion_group": "age",
                        "operator": ">=",
                        "value": 18,
                        "value_max": None,
                        "deterministic_candidate": True,
                        "llm_candidate": False,
                    },
                    {
                        "rule_id": "rule-002",
                        "type": "inclusion",
                        "text": "Eastern Cooperative Oncology Group (ECOG) performance status of 0 or 1",
                        "concept_tags": ["conditions"],
                        "data_needed": ["conditionHistory"],
                        "rule_kind": "patient_eligibility",
                        "primary_criterion_group": "conditions",
                        "deterministic_candidate": False,
                        "llm_candidate": True,
                    },
                ],
            }
        )

        self.assertEqual(canonical["contractVersion"], PHARMA_TRIAL_DEFINITION_CANONICAL_VERSION)
        self.assertEqual(canonical["summary"]["totalCriteria"], 2)
        self.assertEqual(canonical["criteria"][0]["criterionId"], "IE-INC-001")
        self.assertEqual(canonical["criteria"][0]["criterionCategory"], "demographics")
        self.assertEqual(canonical["criteria"][0]["cdisc"]["sdtmTi"]["tirl"], "INCLUSION")
        self.assertEqual(canonical["criteria"][1]["criterionCategory"], "performance_status")
        self.assertEqual(canonical["criteria"][1]["computability"], "narrative_adjudication")

    def test_resolve_trial_definition_canonical_reuses_existing_definition(self):
        canonical = {"contractVersion": PHARMA_TRIAL_DEFINITION_CANONICAL_VERSION, "summary": {"totalCriteria": 7}}
        resolved = resolve_trial_definition_canonical({"trialDefinitionCanonical": canonical})
        self.assertIs(resolved, canonical)

    def test_structured_match_can_run_from_canonical_trial_definition(self):
        tools = self.build_evidence_tools()
        canonical = build_trial_definition_canonical(
            trial_doc={
                "trialId": "NCT-CANON-1",
                "pharma_trial_rules": [
                    {
                        "rule_id": "rule-001",
                        "type": "inclusion",
                        "text": "Age at least 18 years",
                        "concept_tags": ["age"],
                        "data_needed": ["age"],
                        "rule_kind": "patient_eligibility",
                        "primary_criterion_group": "age",
                        "operator": ">=",
                        "value": 18,
                        "value_max": None,
                        "rule_tokens": ["age", "18", "years"],
                        "deterministic_candidate": True,
                        "llm_candidate": False,
                    }
                ],
            }
        )

        structured = tools["evaluate_trial_structured_match"](
            {
                "trialId": "NCT-CANON-1",
                "trialDefinitionCanonical": canonical,
            },
            {
                "evidencePack": {
                    "facts": {
                        "age": {"value": 55, "source": "leafy"},
                    }
                },
            },
            semantic_query="age trial",
        )

        self.assertEqual(structured["trialDefinitionCanonicalSummary"]["totalCriteria"], 1)
        self.assertEqual(structured["ruleEvaluations"][0]["canonicalCriterionId"], "IE-INC-001")
        self.assertTrue(structured["isEligibleStructured"])

    def test_unknown_rule_includes_manual_review_reason_and_mode_counts(self):
        tools = self.build_evidence_tools()
        structured = tools["evaluate_trial_structured_match"](
            {
                "trialId": "NCT-UNKNOWN-1",
                "atomicRules": [
                    {
                        "rule_id": "rule-ecog-001",
                        "type": "inclusion",
                        "text": "Eastern Cooperative Oncology Group (ECOG) performance status of 0 or 1",
                        "concept_tags": ["performance_status"],
                        "data_needed": ["performanceStatus"],
                        "rule_kind": "patient_eligibility",
                        "primary_criterion_group": "performance_status",
                    }
                ],
            },
            {"evidencePack": {"facts": {"age": {"value": 55, "source": "leafy"}}}},
            semantic_query="ecog performance status",
        )

        self.assertEqual(structured["ruleEvaluations"][0]["status"], "UNKNOWN")
        self.assertTrue(structured["ruleEvaluations"][0]["requiresManualReview"])
        self.assertTrue(structured["ruleEvaluations"][0]["manualReviewReason"])
        self.assertIn("manualReviewReasons", structured["decisionSummary"])
        self.assertIn("evaluationModeCounts", structured["decisionSummary"])

    def test_ranking_helper_reduces_retrieval_influence_when_evidence_is_sparse(self):
        low_completeness = compute_trial_match_ranking(
            structured_score=0.6,
            completeness_score=0.2,
            semantic_score=0.95,
            retrieval_score=1.0,
            hybrid_search_used=True,
            overall_assessment="needs_review",
        )
        high_completeness = compute_trial_match_ranking(
            structured_score=0.6,
            completeness_score=0.9,
            semantic_score=0.95,
            retrieval_score=1.0,
            hybrid_search_used=True,
            overall_assessment="needs_review",
        )

        self.assertLess(
            low_completeness["weights"]["retrievalSignals"],
            high_completeness["weights"]["retrievalSignals"],
        )
        self.assertLess(
            low_completeness["weights"]["vectorSearch"],
            high_completeness["weights"]["vectorSearch"],
        )
        self.assertLess(low_completeness["finalScore"], high_completeness["finalScore"])

    def test_shared_trial_match_pipeline_returns_navigator_matcher_metadata(self):
        trial_collection = FakeTrialCollection(
            rows=[
                {
                    "trialId": "NCT-1",
                    "nctId": "NCT0001",
                    "title": "HER2-directed breast oncology study",
                    "status": "RECRUITING",
                    "phase": "Phase 2",
                    "sponsor": "Leafy Research",
                    "summary": "Evaluates eligibility with deterministic contract rules.",
                    "criteriaChunks": ["HER2 positive breast cancer"],
                }
            ]
        )

        result = run_patient_trial_match_pipeline(
            question="Which active clinical trials best match this case and why?",
            appointment_id="appt-1",
            patient_id="patient-1",
            top_k=5,
            rerank_enabled=True,
            trial_filters={},
            build_leafy_patient_context_fn=lambda appointment_id, patient_id=None: (
                {
                    "appointmentId": appointment_id,
                    "patientId": patient_id,
                    "patientName": "Test Patient",
                    "age": 55,
                    "sex": "female",
                    "biradsValues": [4],
                    "semanticQuery": "female age 55 breast oncology",
                },
                {"appointmentID": appointment_id, "patientID": patient_id},
                {"_id": appointment_id},
            ),
            infer_synthea_analog_patients_fn=lambda patient_context, limit=5: (
                [{"patientRef": "Patient/123", "sharedKeywordCount": 2}],
                [{"step": "analog", "status": "done"}],
            ),
            resolve_linked_synthea_patient_ref_fn=lambda **kwargs: {
                "linkageStatus": "linked",
                "syntheaPatientRef": "Patient/123",
            },
            build_patient_evidence_pack_fn=lambda **kwargs: {
                "missing_fields": [],
                "facts": {"age": {"value": 55, "source": "leafy"}},
            },
            build_trial_query_for_patient_context_fn=lambda patient_context: {"sex": "female"},
            evaluate_patient_context_against_trial_prefilter_fn=lambda **kwargs: {"passesHardFilters": True},
            resolve_trial_prefilter_contract_fn=lambda trial: {"contractVersion": "trial_prefilter_contract_v1"},
            resolve_trial_definition_canonical_fn=lambda trial: {"criteria": [{"id": "c-1"}]},
            evaluate_trial_structured_match_fn=lambda trial, patient_context, semantic_query=None: {
                "matchingContractVersion": "trial_matching_contract_v2",
                "isEligibleStructured": True,
                "overallAssessment": "needs_review",
                "structuredScore": 0.82,
                "eligibilityScore": 0.82,
                "evidenceCompletenessScore": 0.9,
                "matchReasons": ["Strong deterministic alignment"],
                "missingCriteria": [],
                "decisionSummary": {"contractVersion": "trial_matching_contract_v2"},
                "criterionBreakdown": [],
                "coverageMeter": {"covered": 1.0},
                "riskEvidence": {},
                "ruleEvaluations": [],
                "llmAdjudicationQueue": [],
                "criteriaLevelMatches": [{"ruleId": "c-1", "text": "HER2 positive breast cancer", "score": 0.7}],
                "parserWarnings": [],
                "validatorWarnings": [],
                "evidencePack": {"missing_fields": [], "facts": {"age": {"value": 55}}},
            },
            build_trial_match_text_fn=lambda trial: str(trial.get("title") or ""),
            estimate_trial_coverage_with_synthea_fn=lambda row: {"estimatedEligiblePatients": 4},
            db_pharma_trials_collection=trial_collection,
            active_trial_statuses_all=["RECRUITING"],
            voyage_api_key=None,
            voyage_embed_text_fn=None,
            voyage_embedding_model="voyage-3.5",
            voyage_rerank_results_fn=None,
            voyage_rerank_model="rerank-2.5",
            voyage_pharma_vector_index_name="vectorSearch-pharma-trials",
            serialize_doc_fn=lambda value: value,
            safe_float_fn=lambda value, default=0.0: float(value) if value is not None else float(default),
            normalize_pharma_text_fn=normalize_text,
            logger=logging.getLogger("trial-matching-test"),
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["matches"][0]["trialId"], "NCT-1")
        self.assertEqual(result["pipelineVisibility"]["matcher"], "shared_pharma_navigator_trial_match")
        self.assertEqual(result["pipelineVisibility"]["candidateSearchMode"], "structured_trial_prefilter")
        self.assertEqual(result["pipelineVisibility"]["patientContextStatus"]["status"], "ready")
        self.assertEqual(result["patientEvidencePack"]["facts"]["age"]["value"], 55)

    def test_shared_trial_match_pipeline_does_not_reinject_raw_synthea_ref_from_provided_context(self):
        result = run_patient_trial_match_pipeline(
            question="Match this patient to breast cancer trials.",
            patient_context={
                "appointmentId": "appt-raw",
                "patientId": "patient-raw",
                "syntheaPatientRef": "Patient/legacy",
                "patientLink": {
                    "syntheaPatientRef": "Patient/legacy",
                    "linkStrategy": DETERMINISTIC_SCREENING_BACKFILL_LINK_STRATEGY,
                    "linkageStatus": "candidate_only",
                },
            },
            appointment_id="appt-raw",
            patient_id="patient-raw",
            top_k=3,
            rerank_enabled=False,
            trial_filters={},
            build_leafy_patient_context_fn=lambda appointment_id, patient_id=None: (
                {
                    "appointmentId": appointment_id,
                    "patientId": patient_id,
                    "patientName": "Strict Policy Patient",
                    "age": 61,
                    "sex": "female",
                    "biradsValues": [4],
                },
                {"appointmentID": appointment_id, "patientID": patient_id},
                {"_id": appointment_id},
            ),
            infer_synthea_analog_patients_fn=lambda patient_context, limit=5: ([], []),
            resolve_linked_synthea_patient_ref_fn=lambda **kwargs: {
                "linkageStatus": "unlinked",
                "syntheaPatientRef": "",
            },
            build_patient_evidence_pack_fn=lambda **kwargs: {"missing_fields": []},
            build_trial_query_for_patient_context_fn=lambda patient_context: {"sex": "female"},
            evaluate_patient_context_against_trial_prefilter_fn=lambda **kwargs: {"passesHardFilters": True},
            resolve_trial_prefilter_contract_fn=lambda trial: {"contractVersion": "trial_prefilter_contract_v1"},
            resolve_trial_definition_canonical_fn=lambda trial: {"criteria": []},
            evaluate_trial_structured_match_fn=lambda **kwargs: {
                "matchingContractVersion": "trial_matching_contract_v2",
                "isEligibleStructured": True,
                "overallAssessment": "needs_review",
                "structuredScore": 0.5,
                "eligibilityScore": 0.5,
                "evidenceCompletenessScore": 1.0,
                "matchReasons": [],
                "missingCriteria": [],
                "decisionSummary": {},
                "criterionBreakdown": [],
                "coverageMeter": {"covered": 1.0},
                "riskEvidence": {},
                "ruleEvaluations": [],
                "llmAdjudicationQueue": [],
                "criteriaLevelMatches": [],
                "parserWarnings": [],
                "validatorWarnings": [],
                "evidencePack": {"missing_fields": []},
            },
            build_trial_match_text_fn=lambda trial: str(trial.get("title") or ""),
            estimate_trial_coverage_with_synthea_fn=lambda row: {"estimatedEligiblePatients": 0},
            db_pharma_trials_collection=FakeTrialCollection(rows=[]),
            active_trial_statuses_all=["RECRUITING"],
            voyage_api_key=None,
            voyage_embed_text_fn=None,
            voyage_embedding_model="voyage-3.5",
            voyage_rerank_results_fn=None,
            voyage_rerank_model="rerank-2.5",
            voyage_pharma_vector_index_name="vectorSearch-pharma-trials",
            serialize_doc_fn=lambda value: value,
            safe_float_fn=lambda value, default=0.0: float(value) if value is not None else float(default),
            normalize_pharma_text_fn=normalize_text,
            logger=logging.getLogger("trial-matching-test"),
        )

        self.assertNotIn("syntheaPatientRef", result["patientContext"])
        self.assertFalse(result["patientContext"].get("patientLink"))
        self.assertEqual(result["patientLink"]["linkageStatus"], "unlinked")

    def test_runtime_linkage_keeps_analog_candidates_non_authoritative_by_default(self):
        services, link_collection, _, _ = self.build_pharma_services_for_linkage(
            profile_rows=[{"patientRef": "Patient/alpha", "age": 56, "gender": "female", "featureTerms": ["breast"], "resourceEventCount": 12}],
            synthea_rows=[{"resource": {"resourceType": "Patient", "id": "alpha"}}],
        )

        resolved = services["_resolve_linked_synthea_patient_ref"](
            {
                "appointmentId": "appt-1",
                "patientId": "leafy-1",
                "patientName": "Leafy Example",
                "age": 55,
                "sex": "female",
                "keywords": ["breast"],
            },
            allow_analog=True,
        )

        self.assertEqual(resolved["linkageStatus"], "candidate_only")
        self.assertEqual(resolved["syntheaPatientRef"], "")
        self.assertEqual(resolved["suggestedSyntheaPatientRef"], "Patient/alpha")
        stored = link_collection.rows[0]
        self.assertEqual(stored["linkageStatus"], "candidate_only")
        self.assertIsNone(stored["syntheaPatientRef"])

    def test_runtime_linkage_treats_embedded_retired_backfill_as_candidate_only(self):
        services, link_collection, _, _ = self.build_pharma_services_for_linkage(
            synthea_rows=[{"resource": {"resourceType": "Patient", "id": "alpha"}}],
        )

        resolved = services["_resolve_linked_synthea_patient_ref"](
            {
                "appointmentId": "appt-2",
                "patientId": "leafy-2",
                "patientName": "Leafy Example",
                "age": 55,
                "sex": "female",
                "keywords": ["breast"],
            },
            report_doc={
                "_id": "report-2",
                "appointmentID": "appt-2",
                "syntheaPatientRef": "Patient/alpha",
                "syntheaLinkStrategy": DETERMINISTIC_SCREENING_BACKFILL_LINK_STRATEGY,
            },
            allow_analog=True,
        )

        self.assertEqual(resolved["linkageStatus"], "candidate_only")
        self.assertEqual(resolved["syntheaPatientRef"], "")
        self.assertEqual(resolved["suggestedSyntheaPatientRef"], "Patient/alpha")
        stored = link_collection.rows[0]
        self.assertEqual(stored["linkageStatus"], "candidate_only")
        self.assertIsNone(stored["syntheaPatientRef"])

    def test_normalize_authoritative_linkage_policy_downgrades_retired_links_and_clears_embedded_screening_refs(self):
        services, link_collection, report_collection, appointment_collection = self.build_pharma_services_for_linkage(
            synthea_rows=[{"resource": {"resourceType": "Patient", "id": "alpha"}}],
            link_rows=[
                {
                    "_id": "link-1",
                    "linkKey": "leafy::appt-legacy",
                    "leafyAppointmentId": "appt-legacy",
                    "leafyReportId": "report-legacy",
                    "leafyPatientId": "leafy-legacy",
                    "syntheaPatientRef": "Patient/alpha",
                    "linkStrategy": DETERMINISTIC_SCREENING_BACKFILL_LINK_STRATEGY,
                    "linkageStatus": "linked",
                    "linkageVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
                    "resolvedAt": "2026-04-18T10:00:00Z",
                }
            ],
            report_rows=[
                {
                    "_id": "report-legacy",
                    "appointmentID": "appt-legacy",
                    "patientID": "leafy-legacy",
                    "syntheaPatientRef": "Patient/alpha",
                    "syntheaLinkStrategy": DETERMINISTIC_SCREENING_BACKFILL_LINK_STRATEGY,
                }
            ],
            appointment_rows=[
                {
                    "_id": "appt-legacy",
                    "syntheaPatientRef": "Patient/alpha",
                    "syntheaLinkStrategy": DETERMINISTIC_SCREENING_BACKFILL_LINK_STRATEGY,
                    "metadata": {
                        "syntheaPatientRef": "Patient/alpha",
                        "syntheaLinkStrategy": DETERMINISTIC_SCREENING_BACKFILL_LINK_STRATEGY,
                        "extraSearchParams": {
                            "subject": {
                                "syntheaPatientRef": "Patient/alpha",
                                "syntheaLinkStrategy": DETERMINISTIC_SCREENING_BACKFILL_LINK_STRATEGY,
                            }
                        },
                    },
                }
            ],
        )

        summary = services["normalize_authoritative_linkage_policy"](max_records=20)

        self.assertEqual(summary["downgradedLinkRecords"], 1)
        self.assertEqual(summary["normalizedAppointments"], 1)
        self.assertEqual(summary["normalizedReports"], 1)
        self.assertEqual(link_collection.rows[0]["linkageStatus"], "candidate_only")
        self.assertIsNone(link_collection.rows[0]["syntheaPatientRef"])
        self.assertEqual(link_collection.rows[0]["suggestedSyntheaPatientRef"], "Patient/alpha")
        self.assertEqual(report_collection.rows[0]["syntheaPatientRef"], "")
        self.assertEqual(report_collection.rows[0]["syntheaLinkStrategy"], "")
        self.assertEqual(appointment_collection.rows[0]["syntheaPatientRef"], "")
        self.assertEqual(appointment_collection.rows[0]["syntheaLinkStrategy"], "")

    def test_classify_link_record_only_trusts_authoritative_v2_links(self):
        authoritative = classify_link_record(
            {
                "syntheaPatientRef": "Patient/123",
                "linkStrategy": "manual_review",
                "linkageVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
            }
        )
        legacy = classify_link_record(
            {
                "syntheaPatientRef": "Patient/123",
                "linkStrategy": "analog-high-confidence",
                "linkageVersion": "v1",
            }
        )

        self.assertTrue(authoritative["authoritative"])
        self.assertEqual(authoritative["linkageStatus"], "linked")
        self.assertTrue(legacy["legacyHeuristic"])
        self.assertEqual(legacy["linkageStatus"], "legacy_heuristic_link")

    def test_screening_capture_link_strategy_is_authoritative(self):
        classification = classify_link_record(
            {
                "syntheaPatientRef": "Patient/123",
                "linkStrategy": SCREENING_CAPTURE_LINK_STRATEGY,
                "linkageVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
            }
        )

        self.assertTrue(classification["authoritative"])
        self.assertEqual(classification["linkageStatus"], "linked")

    def test_deterministic_screening_backfill_link_strategy_is_legacy(self):
        classification = classify_link_record(
            {
                "syntheaPatientRef": "Patient/123",
                "linkStrategy": DETERMINISTIC_SCREENING_BACKFILL_LINK_STRATEGY,
                "linkageVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
            }
        )

        self.assertFalse(classification["authoritative"])
        self.assertTrue(classification["legacyHeuristic"])
        self.assertEqual(classification["linkageStatus"], "legacy_heuristic_link")

    def test_extract_embedded_synthea_link_strategy_requires_explicit_authoritative_strategy(self):
        strategy = extract_embedded_synthea_link_strategy(
            report_doc={"syntheaPatientRef": "Patient/alpha"},
        )

        self.assertEqual(strategy, "")

    def test_extract_embedded_synthea_patient_ref_prefers_screening_documents(self):
        extracted = extract_embedded_synthea_patient_ref(
            report_doc={"syntheaPatientRef": "Patient/alpha"},
            appointment_doc={
                "metadata": {
                    "extraSearchParams": {
                        "subject": {
                            "syntheaPatientRef": "Patient/beta",
                        }
                    }
                }
            },
        )
        fallback = extract_embedded_synthea_patient_ref(
            appointment_doc={
                "metadata": {
                    "extraSearchParams": {
                        "subject": {
                            "syntheaPatientRef": "beta",
                        }
                    }
                }
            }
        )

        self.assertEqual(extracted, "Patient/alpha")
        self.assertEqual(fallback, "Patient/beta")

    def test_extract_embedded_synthea_link_strategy_prefers_explicit_strategy(self):
        strategy = extract_embedded_synthea_link_strategy(
            report_doc={"syntheaLinkStrategy": DETERMINISTIC_SCREENING_BACKFILL_LINK_STRATEGY},
            appointment_doc={
                "metadata": {
                    "syntheaLinkStrategy": SCREENING_CAPTURE_LINK_STRATEGY,
                }
            },
        )

        self.assertEqual(strategy, DETERMINISTIC_SCREENING_BACKFILL_LINK_STRATEGY)

    def test_extract_embedded_synthea_patient_ref_ignores_non_authoritative_screening_ids(self):
        from_report_patient_id = extract_embedded_synthea_patient_ref(
            report_doc={"patientID": "Patient/report-123"},
        )
        from_appointment_subject = extract_embedded_synthea_patient_ref(
            appointment_doc={"resource": {"subject": {"reference": "Patient/appt-456"}}},
        )

        self.assertEqual(from_report_patient_id, "")
        self.assertEqual(from_appointment_subject, "")

    def test_synthetic_ecog_payload_is_reproducible_for_oncology_patients(self):
        payload = derive_synthetic_ecog_payload_from_facts(
            {
                "facts": {
                    "age": {"value": 83},
                    "oncologySignals": {"value": {"breast_oncology": {"present": True}}},
                    "stageOrExtent": {"value": {"metastatic": True}},
                    "oncologyTreatmentHistory": {"value": {"terms": ["chemotherapy"]}},
                }
            }
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["scale"], "ECOG")
        self.assertEqual(payload["value"], 2)
        self.assertTrue(payload["rationale"])

    def test_linkage_control_plane_summary_highlights_candidate_only_and_legacy_links(self):
        summary = build_linkage_control_plane_summary_from_rows(
            [
                {
                    "leafyPatientId": "p1",
                    "syntheaPatientRef": "Patient/1",
                    "linkStrategy": "manual_review",
                    "linkageVersion": PHARMA_LINKAGE_CONTRACT_VERSION,
                },
                {
                    "leafyPatientId": "p2",
                    "syntheaPatientRef": "Patient/2",
                    "linkStrategy": "analog-high-confidence",
                    "linkageVersion": "v1",
                    "candidateOptions": [{"patientRef": "Patient/2"}],
                },
                {
                    "leafyPatientId": "p3",
                    "linkageStatus": "candidate_only",
                    "candidateOptions": [{"patientRef": "Patient/3"}],
                },
            ]
        )

        self.assertEqual(summary["authoritativeLinked"], 1)
        self.assertEqual(summary["legacyHeuristicLinks"], 1)
        self.assertEqual(summary["candidateOnly"], 1)
        self.assertEqual(summary["statusCounts"]["linked"], 1)
        self.assertEqual(summary["statusCounts"]["legacy_heuristic_link"], 1)
        self.assertEqual(summary["statusCounts"]["candidate_only"], 1)

    def test_linkage_control_plane_can_derive_from_embedded_screening_refs(self):
        summary = build_linkage_control_plane_summary_from_screening_sources(
            appointment_total=12,
            appointment_with_ref=9,
            report_total=5,
            report_with_ref=4,
        )

        self.assertEqual(summary["summaryMode"], "embedded_screening_refs")
        self.assertEqual(summary["authoritativeLinked"], 4)
        self.assertEqual(summary["legacyHeuristicLinks"], 0)
        self.assertEqual(summary["candidateOnly"], 0)
        self.assertEqual(summary["appointmentsWithSyntheaRef"], 9)
        self.assertEqual(summary["reportsWithSyntheaRef"], 4)

    def test_trial_control_plane_summary_reports_runtime_posture_and_backlog(self):
        summary = build_trial_control_plane_summary_from_inputs(
            readiness_summary={"ready": True},
            oncology_quality_summary={
                "topEnrichmentBacklog": [
                    {"domain": "receptor_status"},
                    {"domain": "performance_status"},
                    {"domain": "oncology_treatment"},
                ]
            },
            linkage_summary={
                "legacyHeuristicLinks": 2,
                "candidateOnly": 3,
            },
            trial_rows=[
                {
                    "trialDefinitionCanonical": {
                        "contractVersion": PHARMA_TRIAL_DEFINITION_CANONICAL_VERSION,
                        "summary": {"totalCriteria": 2},
                    },
                    "parserWarnings": [{"code": "low_rule_coverage"}],
                    "trialDefinitionCanonical": {
                        "contractVersion": PHARMA_TRIAL_DEFINITION_CANONICAL_VERSION,
                        "summary": {"totalCriteria": 2},
                        "criteria": [
                            {
                                "criterionType": "inclusion",
                                "computability": "manual_review",
                                "ruleKind": "patient_eligibility",
                                "ontologyBindings": {"primaryCriterionGroup": "receptor_status"},
                            },
                            {
                                "criterionType": "exclusion",
                                "computability": "deterministic",
                                "ruleKind": "patient_eligibility",
                                "ontologyBindings": {"primaryCriterionGroup": "age"},
                            },
                        ],
                    },
                    "pharma_trial_rules": [
                        {"rule_kind": "patient_eligibility", "deterministic_candidate": True, "llm_candidate": False},
                        {"rule_kind": "patient_eligibility", "deterministic_candidate": False, "llm_candidate": True},
                    ],
                }
            ],
            patient_profile_rows=[
                {
                    "patientMatchProfile": {
                        "gaps": [
                            {"feature": "receptorStatus", "fillableBy": "pathology_or_site_ehr"},
                            {"feature": "performanceStatus", "fillableBy": "questionnaire_or_site_ehr"},
                            {"feature": "identityLinkage", "fillableBy": "manual_review"},
                        ]
                    }
                }
            ],
        )

        self.assertEqual(summary["runtimePosture"]["patientLinkagePolicy"], "authoritative_link_required")
        self.assertFalse(summary["runtimePosture"]["heuristicAutoLinkingEnabled"])
        self.assertEqual(summary["trialContracts"]["patientEligibilityRuleCount"], 2)
        self.assertEqual(summary["trialContracts"]["canonicalDefinitionCount"], 1)
        self.assertEqual(summary["trialContracts"]["deterministicRuleCount"], 1)
        self.assertEqual(summary["trialContracts"]["parserWarningCounts"]["low_rule_coverage"], 1)
        self.assertEqual(summary["gapTaxonomy"]["counts"]["ehr_only"], 1)
        self.assertEqual(summary["gapTaxonomy"]["counts"]["questionnaire"], 1)
        self.assertEqual(summary["trialContracts"]["criterionGroupCount"], 2)
        self.assertEqual(summary["trialContracts"]["criterionGroupHotspots"][0]["criterionGroup"], "receptor_status")
        self.assertTrue(summary["priorityBacklog"])

    def test_trial_matching_evaluation_run_document_scores_dimensions_and_hotspots(self):
        summary = build_trial_control_plane_summary_from_inputs(
            readiness_summary={
                "ready": False,
                "patientCount": 18,
                "checks": [
                    {"label": "Min patients", "passed": False},
                    {"label": "Breast oncology signals", "passed": True},
                ],
                "runtimeCounts": {"patientProfileEmbeddings": 0},
            },
            oncology_quality_summary={"candidateAverageCoveragePct": 42.0},
            linkage_summary={
                "authoritativeLinkedPct": 50.0,
                "legacyHeuristicLinks": 1,
                "candidateOnly": 2,
            },
            sparse_domain_summary={"deterministicDomainPct": 25.0},
            trial_rows=[
                {
                    "trialDefinitionCanonical": {
                        "contractVersion": PHARMA_TRIAL_DEFINITION_CANONICAL_VERSION,
                        "summary": {"totalCriteria": 4},
                    },
                    "trialPrefilterContract": {"hardFilters": {"age": {"min": 18}, "sex": {"anyOf": ["female"]}}},
                    "parserWarnings": [{"code": "low_rule_coverage"}],
                    "pharma_trial_rules": [
                        {"rule_kind": "patient_eligibility", "deterministic_candidate": True, "llm_candidate": False},
                        {"rule_kind": "patient_eligibility", "deterministic_candidate": False, "llm_candidate": True},
                    ],
                }
            ],
        )

        score_payload = score_trial_matching_control_plane_summary(summary, mode="runtime_snapshot")
        run_document = build_trial_matching_evaluation_run_document(summary=summary, mode="runtime_snapshot")

        self.assertEqual(score_payload["overview"]["dimensionsTotal"], 7)
        self.assertEqual(run_document["runType"], "clinical_trial_matching_evaluation")
        self.assertEqual(run_document["overview"]["mode"], "runtime_snapshot")
        self.assertTrue(run_document["dimensions"])
        self.assertTrue(run_document["hotspots"])
        self.assertIn("summarySnapshot", run_document)

    def test_trial_matching_improvement_run_document_captures_drift(self):
        base_summary = build_trial_control_plane_summary_from_inputs(
            readiness_summary={"ready": True, "patientCount": 40, "checks": [{"label": "Min patients", "passed": True}], "runtimeCounts": {"patientProfileEmbeddings": 12}},
            oncology_quality_summary={"candidateAverageCoveragePct": 80.0},
            linkage_summary={"authoritativeLinkedPct": 95.0, "legacyHeuristicLinks": 0, "candidateOnly": 0},
            sparse_domain_summary={"deterministicDomainPct": 75.0},
            trial_rows=[
                {
                    "trialDefinitionCanonical": {"contractVersion": PHARMA_TRIAL_DEFINITION_CANONICAL_VERSION, "summary": {"totalCriteria": 2}},
                    "trialPrefilterContract": {"hardFilters": {"age": {"min": 18}, "sex": {"anyOf": ["female"]}}},
                    "parserWarnings": [],
                    "pharma_trial_rules": [
                        {"rule_kind": "patient_eligibility", "deterministic_candidate": True, "llm_candidate": False},
                    ],
                }
            ],
        )
        improved_summary = build_trial_control_plane_summary_from_inputs(
            readiness_summary={"ready": True, "patientCount": 60, "checks": [{"label": "Min patients", "passed": True}], "runtimeCounts": {"patientProfileEmbeddings": 30}},
            oncology_quality_summary={"candidateAverageCoveragePct": 90.0},
            linkage_summary={"authoritativeLinkedPct": 100.0, "legacyHeuristicLinks": 0, "candidateOnly": 0},
            sparse_domain_summary={"deterministicDomainPct": 100.0},
            trial_rows=[
                {
                    "trialDefinitionCanonical": {"contractVersion": PHARMA_TRIAL_DEFINITION_CANONICAL_VERSION, "summary": {"totalCriteria": 4}},
                    "trialPrefilterContract": {"hardFilters": {"age": {"min": 18}, "sex": {"anyOf": ["female"]}, "softFilters": {"birads": {"anyOf": [4]}}}},
                    "parserWarnings": [],
                    "pharma_trial_rules": [
                        {"rule_kind": "patient_eligibility", "deterministic_candidate": True, "llm_candidate": False},
                        {"rule_kind": "patient_eligibility", "deterministic_candidate": True, "llm_candidate": False},
                    ],
                }
            ],
        )

        previous_run = build_trial_matching_evaluation_run_document(summary=base_summary, mode="runtime_snapshot")
        current_run = build_trial_matching_evaluation_run_document(
            summary=improved_summary,
            mode="full_audit",
            previous_run=previous_run,
        )
        improvement = build_trial_matching_improvement_run_document(
            evaluation_run=current_run,
            previous_run=previous_run,
        )

        self.assertEqual(improvement["runType"], "clinical_trial_matching_improvement")
        self.assertEqual(improvement["evaluationRunId"], current_run["runId"])
        self.assertTrue(isinstance(improvement["improvements"], list))
        self.assertIn("scoreDelta", improvement["overview"])

    def test_sparse_oncology_domain_summary_distinguishes_materialized_and_missing_paths(self):
        summary = build_sparse_oncology_domain_summary_from_inputs(
            patient_fact_rows=[
                {
                    "facts": {
                        "receptorStatus": {"value": {"hr": "positive", "her2": "negative"}},
                    }
                }
            ],
            profile_rows=[
                {"observationTerms": ["ecog", "cbc"]},
            ],
            report_rows=[
                {"clinicalNotes": "Metastatic breast cancer, ECOG performance status 1."},
            ],
        )

        domains = {item["key"]: item for item in summary["domains"]}
        self.assertEqual(domains["receptor_status"]["status"], "deterministic_in_cohort")
        self.assertEqual(domains["receptor_status"]["patientFactCount"], 1)
        self.assertEqual(domains["performance_status"]["status"], "synthea_signal_needs_materialization")
        self.assertEqual(domains["stage_or_extent"]["status"], "leafy_narrative_only")
        self.assertEqual(domains["oncology_treatment"]["status"], "unsupported_in_current_demo_data")
        self.assertIn("Performance status", summary["sourcePresentButUnmaterialized"])
        self.assertIn("Stage or extent", summary["leafyNarrativeOnly"])
        self.assertIn("Oncology treatment", summary["unsupportedInCurrentDemoData"])

    def test_trial_prefilter_contract_extracts_safe_hard_filters(self):
        contract = build_trial_prefilter_contract(
            eligibility={
                "sex": ["female"],
                "ageMin": 18,
                "ageMax": 75,
                "biradsAny": [4, 5],
            },
            trial_rules=[
                {"primary_criterion_group": "age", "concept_tags": ["age"]},
                {"primary_criterion_group": "sex", "concept_tags": ["sex"]},
                {"primary_criterion_group": "pathology", "concept_tags": ["pathology"]},
                {"primary_criterion_group": "imaging", "concept_tags": ["birads"]},
            ],
        )

        self.assertEqual(contract["contractVersion"], PHARMA_TRIAL_PREFILTER_CONTRACT_VERSION)
        self.assertEqual(contract["hardFilters"]["sex"]["anyOf"], ["female"])
        self.assertEqual(contract["hardFilters"]["age"]["min"], 18)
        self.assertEqual(contract["hardFilters"]["age"]["max"], 75)
        self.assertEqual(contract["softFilters"]["birads"]["anyOf"], [4, 5])
        self.assertIn("pathology", contract["unsupportedCriterionGroups"])

    def test_trial_prefilter_query_for_patient_context_uses_age_and_sex(self):
        query = build_trial_query_for_patient_context({"age": 43, "sex": "female"})

        self.assertEqual(
            query,
            {
                "$and": [
                    {
                        "$or": [
                            {"trialPrefilterContract.hardFilters.sex": {"$exists": False}},
                            {"trialPrefilterContract.hardFilters.sex.anyOf": "female"},
                        ]
                    },
                    {
                        "$or": [
                            {"trialPrefilterContract.hardFilters.age.min": {"$exists": False}},
                            {"trialPrefilterContract.hardFilters.age.min": {"$lte": 43}},
                        ]
                    },
                    {
                        "$or": [
                            {"trialPrefilterContract.hardFilters.age.max": {"$exists": False}},
                            {"trialPrefilterContract.hardFilters.age.max": {"$gte": 43}},
                        ]
                    },
                ]
            },
        )

    def test_patient_and_profile_prefilter_evaluation_blocks_only_safe_mismatches(self):
        contract = {
            "contractVersion": PHARMA_TRIAL_PREFILTER_CONTRACT_VERSION,
            "hardFilters": {
                "sex": {"anyOf": ["female"]},
                "age": {"min": 18, "max": 65},
            },
            "softFilters": {
                "birads": {"anyOf": [4], "supportLevel": "semi_structured"},
            },
        }

        patient_eval = evaluate_patient_context_against_trial_prefilter(
            patient_context={"age": 70, "sex": "male", "biradsValues": [4]},
            trial_prefilter_contract=contract,
        )
        profile_eval = evaluate_profile_against_trial_prefilter(
            profile_doc={"age": 52, "gender": "female"},
            trial_prefilter_contract=contract,
        )

        self.assertFalse(patient_eval["passesHardFilters"])
        self.assertEqual(set(patient_eval["blockedBy"]), {"age_max", "sex"})
        self.assertIn("birads", patient_eval["advisoryMatches"])
        self.assertTrue(profile_eval["passesHardFilters"])
        self.assertEqual(profile_eval["blockedBy"], [])

    def test_profile_query_and_contract_resolution_fall_back_from_trial_doc(self):
        trial_doc = {
            "eligibility": {"sex": ["female"], "ageMin": 21, "ageMax": 70},
            "pharma_trial_rules": [{"primary_criterion_group": "age", "concept_tags": ["age"]}],
        }

        contract = resolve_trial_prefilter_contract(trial_doc)
        profile_query = build_profile_query_from_trial_prefilter_contract(contract)

        self.assertEqual(contract["contractVersion"], PHARMA_TRIAL_PREFILTER_CONTRACT_VERSION)
        self.assertEqual(profile_query["gender"], {"$in": ["female"]})
        self.assertEqual(profile_query["age"], {"$gte": 21, "$lte": 70})


if __name__ == "__main__":
    unittest.main()
