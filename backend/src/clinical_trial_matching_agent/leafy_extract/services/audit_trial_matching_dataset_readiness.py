import argparse
import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import MongoClient


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


SEARCH_FIELDS = [
    "narrative.summaryText",
    "searchIndex.string.value",
    "searchIndex.token.display",
    "searchIndex.token.code",
    "resource.code.text",
    "resource.code.coding.display",
    "resource.type.text",
    "resource.type.coding.display",
    "resource.medicationCodeableConcept.text",
    "resource.medicationCodeableConcept.coding.display",
    "resource.valueCodeableConcept.text",
    "resource.valueCodeableConcept.coding.display",
    "resource.conclusion",
    "resource.description",
    "resource.note.text",
]

SIGNAL_FAMILIES = {
    "breast_oncology": ["breast", "mastectomy", "lumpectomy"],
    "breast_imaging": ["mammogram", "mammography", "breast ultrasound", "breast mri"],
    "breast_biopsy": ["breast biopsy", "biopsy"],
    "receptor_status": ["her2", "estrogen receptor", "progesterone receptor", "er positive", "pr positive"],
    "performance_status": ["ecog"],
    "stage_or_extent": ["stage", "tnm", "metast", "metastatic"],
    "oncology_treatment": [
        "trastuzumab",
        "tamoxifen",
        "anastrozole",
        "letrozole",
        "paclitaxel",
        "doxorubicin",
        "chemotherapy",
        "radiation therapy",
    ],
    "core_labs": [
        "bilirubin",
        "creatinine",
        "aspartate aminotransferase",
        "alanine aminotransferase",
        "platelets",
        "neutrophil",
        "hemoglobin",
    ],
}


def env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and (value is None or str(value).strip() == ""):
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def parse_args():
    parser = argparse.ArgumentParser(
        description="Audit whether the configured FHIR dataset is suitable for deterministic breast-oncology trial matching."
    )
    parser.add_argument(
        "--mongo-uri",
        default=os.getenv("MONGODB_CONNECTION_STRING", "").strip(),
        help="MongoDB connection string. Defaults to MONGODB_CONNECTION_STRING from externals/webservices/.env.",
    )
    parser.add_argument(
        "--db",
        default=env("SYNTHEA_FHIR_DB", "hc_fhir_10pat_test"),
        help="FHIR database name. Defaults to SYNTHEA_FHIR_DB.",
    )
    parser.add_argument(
        "--collection",
        default=env("SYNTHEA_RESOURCES_COLLECTION", "fhir_resources"),
        help="FHIR collection name. Defaults to SYNTHEA_RESOURCES_COLLECTION.",
    )
    parser.add_argument(
        "--min-patients",
        type=int,
        default=25,
        help="Minimum patient count to consider the cohort minimally useful.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 when the dataset is not ready.",
    )
    return parser.parse_args()


def build_regex_clause(term):
    return {
        "$or": [
            {field: {"$regex": term, "$options": "i"}}
            for field in SEARCH_FIELDS
        ]
    }


def count_signal_family(collection, terms):
    unique_ids = set()
    for term in terms:
        for doc in collection.find(build_regex_clause(term), {"_id": 1}):
            unique_ids.add(doc["_id"])
    return len(unique_ids)


def resource_type_counts(collection):
    pipeline = [
        {
            "$group": {
                "_id": {"$ifNull": ["$resourceType", "$resource.resourceType"]},
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"count": -1, "_id": 1}},
    ]
    rows = list(collection.aggregate(pipeline))
    return {str(row.get("_id") or "unknown"): int(row.get("count") or 0) for row in rows}


def build_checks(patient_count, family_counts, min_patients):
    checks = [
        {
            "id": "min_patient_count",
            "label": f"At least {min_patients} patients",
            "passed": patient_count >= min_patients,
            "observed": patient_count,
            "expected": min_patients,
        },
        {
            "id": "breast_oncology_signal",
            "label": "Breast-oncology signal present",
            "passed": family_counts.get("breast_oncology", 0) > 0,
            "observed": family_counts.get("breast_oncology", 0),
            "expected": "> 0",
        },
        {
            "id": "breast_imaging_signal",
            "label": "Breast imaging signal present",
            "passed": family_counts.get("breast_imaging", 0) > 0,
            "observed": family_counts.get("breast_imaging", 0),
            "expected": "> 0",
        },
        {
            "id": "receptor_or_stage_signal",
            "label": "Receptor-status or stage signal present",
            "passed": (
                family_counts.get("receptor_status", 0) > 0
                or family_counts.get("stage_or_extent", 0) > 0
            ),
            "observed": {
                "receptor_status": family_counts.get("receptor_status", 0),
                "stage_or_extent": family_counts.get("stage_or_extent", 0),
            },
            "expected": "one family > 0",
        },
        {
            "id": "oncology_treatment_signal",
            "label": "Oncology treatment signal present",
            "passed": family_counts.get("oncology_treatment", 0) > 0,
            "observed": family_counts.get("oncology_treatment", 0),
            "expected": "> 0",
        },
        {
            "id": "core_labs_signal",
            "label": "Core lab signal present",
            "passed": family_counts.get("core_labs", 0) > 0,
            "observed": family_counts.get("core_labs", 0),
            "expected": "> 0",
        },
    ]
    return checks


def build_dataset_readiness_summary(collection, *, db_name, collection_name, min_patients=25):
    patient_count = collection.count_documents(
        {
            "$or": [
                {"resourceType": "Patient"},
                {"resource.resourceType": "Patient"},
            ]
        }
    )
    counts_by_type = resource_type_counts(collection)
    family_counts = {
        family: count_signal_family(collection, terms)
        for family, terms in SIGNAL_FAMILIES.items()
    }
    checks = build_checks(patient_count, family_counts, min_patients)
    ready = all(bool(item.get("passed")) for item in checks)
    return {
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "db": db_name,
        "collection": collection_name,
        "ready": ready,
        "patientCount": patient_count,
        "resourceTypeCounts": counts_by_type,
        "signalFamilyCounts": family_counts,
        "checks": checks,
    }


def main():
    args = parse_args()
    if not args.mongo_uri:
        raise RuntimeError("Missing MongoDB URI. Provide --mongo-uri or set MONGODB_CONNECTION_STRING.")

    client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=10000)
    collection = client[args.db][args.collection]

    summary = build_dataset_readiness_summary(
        collection,
        db_name=args.db,
        collection_name=args.collection,
        min_patients=args.min_patients,
    )
    print(json.dumps(summary, indent=2))
    client.close()

    if args.strict and not summary.get("ready"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
