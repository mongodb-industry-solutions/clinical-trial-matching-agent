import argparse
import json
import os
import sys

from dotenv import load_dotenv


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Bootstrap the configured canonical Synthea dataset into Leafy Hospital and rebuild deterministic trial-matching derivatives."
    )
    parser.add_argument(
        "--ndjson-path",
        default="",
        help="Override the canonical NDJSON path. Defaults to SYNTHEA_RESOURCES_NDJSON_PATH from app.py.",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Delete the configured FHIR collection before syncing the NDJSON.",
    )
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="Skip the canonical FHIR sync step and only rebuild downstream Leafy derivatives.",
    )
    parser.add_argument(
        "--skip-profiles",
        action="store_true",
        help="Skip rebuilding pharma patient profiles after the sync.",
    )
    parser.add_argument(
        "--skip-facts",
        action="store_true",
        help="Skip rebuilding deterministic patient facts after the sync.",
    )
    parser.add_argument(
        "--skip-trial-contracts",
        action="store_true",
        help="Skip rebuilding deterministic trial prefilter contracts.",
    )
    parser.add_argument(
        "--include-oncology-cohort",
        action="store_true",
        help="Also rebuild the optional downstream breast-oncology cohort projection.",
    )
    parser.add_argument(
        "--allow-embeddings",
        action="store_true",
        help="Allow semantic profile embeddings during rebuilds. Disabled by default for deterministic bootstrap runs.",
    )
    parser.add_argument(
        "--backfill-screening-refs",
        action="store_true",
        help="Copy authoritative Synthea refs from legacy link records onto appointment/report documents.",
    )
    parser.add_argument(
        "--seed-screening-refs",
        action="store_true",
        help="Assign deterministic canonical Synthea refs to screening patients that still lack a valid direct link.",
    )
    parser.add_argument(
        "--include-synthetic-ecog",
        action="store_true",
        help="Upsert synthetic demo ECOG Observation resources into canonical fhir_resources and rebuild facts afterward.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    os.environ["TRIAL_MATCHING_DATASET_SKIP_STARTUP_AUDIT"] = "true"
    if not args.allow_embeddings:
        os.environ["VOYAGE_API_KEY"] = ""

    from app import (
        SYNTHEA_FHIR_DB,
        SYNTHEA_RESOURCES_COLLECTION,
        SYNTHEA_RESOURCES_NDJSON_PATH,
        build_dataset_readiness_summary,
        audit_breast_oncology_cohort_quality,
        backfill_embedded_screening_synthea_refs,
        db_synthea_resources_collection,
        ensure_pharma_indexes,
        rebuild_breast_oncology_cohort,
        rebuild_pharma_patient_facts,
        rebuild_pharma_patient_profiles,
        rebuild_pharma_trial_canonical_definitions,
        rebuild_pharma_trial_prefilter_contracts,
        seed_missing_screening_synthea_refs,
        sync_synthea_resources_from_ndjson,
        upsert_synthetic_ecog_observations,
    )

    ensure_pharma_indexes()
    ndjson_path = args.ndjson_path or SYNTHEA_RESOURCES_NDJSON_PATH
    summary = {
        "db": SYNTHEA_FHIR_DB,
        "collection": SYNTHEA_RESOURCES_COLLECTION,
        "ndjsonPath": ndjson_path,
    }
    if args.skip_sync:
        summary["sync"] = {
            "loadedFromFile": False,
            "skipped": True,
            "existingCount": db_synthea_resources_collection.count_documents({}),
        }
    else:
        print(json.dumps({"stage": "sync", "ndjsonPath": ndjson_path}), file=sys.stderr, flush=True)
        summary["sync"] = sync_synthea_resources_from_ndjson(
            ndjson_path,
            replace_existing=bool(args.replace_existing),
            batch_size=800,
        )
    if args.backfill_screening_refs:
        print(json.dumps({"stage": "backfill_screening_refs"}), file=sys.stderr, flush=True)
        summary["screeningRefBackfill"] = backfill_embedded_screening_synthea_refs(authoritative_only=True)
    if args.seed_screening_refs:
        print(json.dumps({"stage": "seed_screening_refs"}), file=sys.stderr, flush=True)
        summary["screeningRefSeed"] = seed_missing_screening_synthea_refs(replace_invalid=True)
    if not args.skip_profiles:
        print(json.dumps({"stage": "rebuild_profiles"}), file=sys.stderr, flush=True)
        summary["patientProfiles"] = rebuild_pharma_patient_profiles()
    if not args.skip_facts:
        print(json.dumps({"stage": "rebuild_facts"}), file=sys.stderr, flush=True)
        summary["patientFacts"] = rebuild_pharma_patient_facts()
    if args.include_synthetic_ecog:
        print(json.dumps({"stage": "upsert_synthetic_ecog"}), file=sys.stderr, flush=True)
        summary["syntheticEcog"] = upsert_synthetic_ecog_observations()
        print(json.dumps({"stage": "rebuild_facts_post_synthetic_ecog"}), file=sys.stderr, flush=True)
        summary["patientFactsPostSyntheticEcog"] = rebuild_pharma_patient_facts()
    if not args.skip_trial_contracts:
        print(json.dumps({"stage": "rebuild_trial_canonical_definitions"}), file=sys.stderr, flush=True)
        summary["trialCanonicalDefinitions"] = rebuild_pharma_trial_canonical_definitions()
        print(json.dumps({"stage": "rebuild_trial_prefilter_contracts"}), file=sys.stderr, flush=True)
        summary["trialPrefilterContracts"] = rebuild_pharma_trial_prefilter_contracts()
    if args.include_oncology_cohort:
        print(json.dumps({"stage": "rebuild_oncology_cohort"}), file=sys.stderr, flush=True)
        summary["oncologyCohort"] = rebuild_breast_oncology_cohort()
    print(json.dumps({"stage": "readiness_audit"}), file=sys.stderr, flush=True)
    summary["readiness"] = build_dataset_readiness_summary(
        db_synthea_resources_collection,
        db_name=SYNTHEA_FHIR_DB,
        collection_name=SYNTHEA_RESOURCES_COLLECTION,
    )
    if args.include_oncology_cohort:
        summary["oncologyCohortQuality"] = audit_breast_oncology_cohort_quality()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
