#!/usr/bin/env python3

import argparse
import json

from app import (
    normalize_authoritative_linkage_policy,
    rebuild_breast_oncology_cohort,
    rebuild_pharma_patient_facts,
    rebuild_pharma_patient_profiles,
)


def main():
    parser = argparse.ArgumentParser(
        description="Normalize screening-to-FHIR linkage to the authoritative-only policy."
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=5000,
        help="Maximum number of linkage/report/appointment records to normalize per collection.",
    )
    parser.add_argument(
        "--skip-rebuild",
        action="store_true",
        help="Normalize linkage state without rebuilding derived patient artifacts.",
    )
    args = parser.parse_args()

    result = {
        "normalization": normalize_authoritative_linkage_policy(
            max_records=args.max_records,
        )
    }
    if not args.skip_rebuild:
        result["rebuildPatientFacts"] = rebuild_pharma_patient_facts()
        result["rebuildPatientProfiles"] = rebuild_pharma_patient_profiles()
        result["rebuildBreastOncologyCohort"] = rebuild_breast_oncology_cohort()

    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
