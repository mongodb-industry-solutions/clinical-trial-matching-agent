from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
CLINICAL_TRIALS_SERVICE_DIR = REPO_ROOT / "externals" / "clinical_trials_service"
OUTPUT_DIR = REPO_ROOT / "externals" / "benchmarks" / "output" / "clinical_trials_micro_benchmark"
DEFAULT_EXTERNAL_REVIEW_PACK_PATH = OUTPUT_DIR / "clinical_trials_micro_benchmark_external_review_pack_latest.json"
DEFAULT_REVIEW_SHEET_PATH = OUTPUT_DIR / "clinical_trials_micro_benchmark_review_sheet_latest.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import an SME-completed micro-benchmark review sheet and score it against the frozen canonical runtime.",
    )
    parser.add_argument("--external-review-pack-path", default=str(DEFAULT_EXTERNAL_REVIEW_PACK_PATH))
    parser.add_argument("--review-sheet-path", default=str(DEFAULT_REVIEW_SHEET_PATH))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    return parser.parse_args()


def _load_clinical_trials_modules():
    sys.path.insert(0, str(CLINICAL_TRIALS_SERVICE_DIR))
    load_dotenv(CLINICAL_TRIALS_SERVICE_DIR / "app" / ".env")
    from app.services.micro_benchmark_service import (
        build_micro_benchmark_sme_review_results,
        parse_micro_benchmark_review_sheet,
    )

    return (
        build_micro_benchmark_sme_review_results,
        parse_micro_benchmark_review_sheet,
    )


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _git_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def _git_status_short() -> list[str]:
    output = subprocess.check_output(["git", "status", "--short"], cwd=REPO_ROOT, text=True).strip()
    if not output:
        return []
    return [line for line in output.splitlines() if line.strip()]


def main() -> int:
    args = parse_args()
    external_review_pack_path = Path(args.external_review_pack_path).expanduser().resolve()
    review_sheet_path = Path(args.review_sheet_path).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()

    build_micro_benchmark_sme_review_results, parse_micro_benchmark_review_sheet = _load_clinical_trials_modules()

    external_review_pack = _load_json(external_review_pack_path)
    review_sheet_text = review_sheet_path.read_text(encoding="utf-8")
    review_sheet_rows = parse_micro_benchmark_review_sheet(review_sheet_text)
    sme_review_results = build_micro_benchmark_sme_review_results(
        external_review_pack=external_review_pack,
        review_sheet_rows=review_sheet_rows,
    )
    sme_review_results["workspaceVersion"] = {
        "gitCommit": _git_commit(),
        "gitDirty": bool(_git_status_short()),
        "gitStatusShort": _git_status_short(),
    }
    sme_review_results["sourcePaths"] = {
        "externalReviewPack": str(external_review_pack_path),
        "reviewSheet": str(review_sheet_path),
    }

    benchmark_pack_id = str(sme_review_results.get("benchmarkPackId") or "latest").strip() or "latest"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"clinical_trials_micro_benchmark_sme_review_results_{benchmark_pack_id}.json"
    latest_path = output_dir / "clinical_trials_micro_benchmark_sme_review_results_latest.json"
    _write_json(output_path, sme_review_results)
    _write_json(latest_path, sme_review_results)

    print(
        json.dumps(
            {
                "benchmarkPackId": benchmark_pack_id,
                "outputPath": str(output_path),
                "latestPath": str(latest_path),
                "summary": sme_review_results.get("summary"),
                "disagreementTaxonomy": sme_review_results.get("disagreementTaxonomy"),
                "reviewStatus": sme_review_results.get("reviewStatus"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
