#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient, ReplaceOne


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"

load_dotenv(BACKEND_ROOT / ".env")
load_dotenv(BACKEND_ROOT / "clinical_trials_service_app" / ".env")


def env(name: str, *fallbacks: str, default: str = "") -> str:
    for key in (name, *fallbacks):
        value = str(os.getenv(key, "") or "").strip()
        if value:
            return value
    return default


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy trial-owned collections from the old Leafy-side Mongo database into the"
            " standalone clinical-trials database."
        )
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the migration plan without writing data.")
    parser.add_argument(
        "--drop-destination",
        action="store_true",
        help="Drop each destination collection before copying documents.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=250,
        help="Bulk upsert batch size. Defaults to 250.",
    )
    return parser.parse_args()


def iter_collection_specs() -> list[dict[str, str | bool]]:
    return [
        {"label": "pharmaTrials", "name": env("PHARMA_TRIALS_COLLECTION", default="pharma_clinical_trials"), "required": True},
        {
            "label": "patientProfiles",
            "name": env("PHARMA_PATIENT_PROFILES_COLLECTION", default="pharma_patient_profiles"),
            "required": True,
        },
        {
            "label": "patientFacts",
            "name": env("PHARMA_PATIENT_FACTS_COLLECTION", default="pharma_patient_facts"),
            "required": True,
        },
        {
            "label": "evaluationRuns",
            "name": env("PHARMA_EVALUATION_RUNS_COLLECTION", default="pharma_evaluation_runs"),
            "required": True,
        },
        {
            "label": "improvementRuns",
            "name": env("PHARMA_IMPROVEMENT_RUNS_COLLECTION", default="pharma_improvement_runs"),
            "required": True,
        },
        {
            "label": "serviceTrials",
            "name": env("CLINICAL_TRIALS_TRIALS_COLLECTION", default="clinical_trial_trials"),
            "required": True,
        },
        {
            "label": "serviceVersions",
            "name": env("CLINICAL_TRIALS_VERSIONS_COLLECTION", default="clinical_trial_versions"),
            "required": True,
        },
        {
            "label": "serviceCriteria",
            "name": env("CLINICAL_TRIALS_CRITERIA_COLLECTION", default="clinical_trial_criteria"),
            "required": True,
        },
        {
            "label": "patientCurationOverlays",
            "name": env("PHARMA_PATIENT_CURATION_OVERLAYS_COLLECTION"),
            "required": False,
        },
        {
            "label": "oncologyCohort",
            "name": env("PHARMA_ONCOLOGY_COHORT_COLLECTION"),
            "required": False,
        },
        {
            "label": "featureEdges",
            "name": env("PHARMA_FEATURE_EDGES_COLLECTION"),
            "required": False,
        },
    ]


def bulk_upsert_collection(*, source_collection, destination_collection, batch_size: int) -> int:
    written = 0
    operations: list[ReplaceOne] = []
    for document in source_collection.find({}):
        operations.append(ReplaceOne({"_id": document["_id"]}, document, upsert=True))
        if len(operations) >= batch_size:
            destination_collection.bulk_write(operations, ordered=False)
            written += len(operations)
            operations = []
    if operations:
        destination_collection.bulk_write(operations, ordered=False)
        written += len(operations)
    return written


def main() -> int:
    args = parse_args()

    source_uri = env(
        "MIGRATION_SOURCE_MONGODB_URI",
        "LEAFY_HOSPITAL_MONGODB_URI",
        "MONGODB_URI",
    )
    source_database_name = env(
        "MIGRATION_SOURCE_DATABASE_NAME",
        "LEAFY_HOSPITAL_DATABASE_NAME",
        "DATABASE_NAME",
        default="demo_hc_virtual_hospital",
    )
    destination_uri = env(
        "CLINICAL_TRIALS_MONGODB_URI",
        "MONGODB_CONNECTION_STRING",
        "MONGODB_URI",
    )
    destination_database_name = env(
        "CLINICAL_TRIALS_DATABASE_NAME",
        "CLINICAL_TRIALS_DB",
        "DATABASE_NAME",
        default="demo_hc_virtual_hospital",
    )

    if not source_uri:
        raise SystemExit("Missing source Mongo URI. Set MIGRATION_SOURCE_MONGODB_URI or LEAFY_HOSPITAL_MONGODB_URI.")
    if not destination_uri:
        raise SystemExit(
            "Missing destination Mongo URI. Set CLINICAL_TRIALS_MONGODB_URI or MONGODB_CONNECTION_STRING."
        )

    if source_uri == destination_uri and source_database_name == destination_database_name:
        raise SystemExit(
            "Source and destination point to the same Mongo database. "
            "Set MIGRATION_SOURCE_* and CLINICAL_TRIALS_* to distinct targets before running the migration."
        )

    source_client = MongoClient(source_uri)
    destination_client = MongoClient(destination_uri)
    source_db = source_client[source_database_name]
    destination_db = destination_client[destination_database_name]

    summary = {
        "source": {"uriPreview": f"{source_uri[:12]}...", "database": source_database_name},
        "destination": {"uriPreview": f"{destination_uri[:12]}...", "database": destination_database_name},
        "dryRun": bool(args.dry_run),
        "dropDestination": bool(args.drop_destination),
        "collections": [],
    }

    for spec in iter_collection_specs():
        collection_name = str(spec["name"] or "").strip()
        if not collection_name:
            if spec["required"]:
                raise SystemExit(f"Missing required collection name for {spec['label']}.")
            continue

        source_collection = source_db[collection_name]
        destination_collection = destination_db[collection_name]
        source_count = int(source_collection.estimated_document_count())
        destination_count_before = int(destination_collection.estimated_document_count())

        row = {
            "label": spec["label"],
            "collection": collection_name,
            "sourceCount": source_count,
            "destinationCountBefore": destination_count_before,
            "destinationCountAfter": destination_count_before,
            "written": 0,
        }

        if args.dry_run:
            summary["collections"].append(row)
            continue

        if args.drop_destination:
            destination_collection.drop()
            row["destinationCountBefore"] = 0

        row["written"] = bulk_upsert_collection(
            source_collection=source_collection,
            destination_collection=destination_collection,
            batch_size=max(1, int(args.batch_size)),
        )
        row["destinationCountAfter"] = int(destination_collection.estimated_document_count())
        summary["collections"].append(row)

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
