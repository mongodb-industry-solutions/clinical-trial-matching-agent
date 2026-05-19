from __future__ import annotations

from typing import Any

import requests

from app.config import get_settings
from app.services.helpers import normalize_token


settings = get_settings()


class ClinicalTrialsGovClient:
    def __init__(self) -> None:
        self.base_url = settings.clinicaltrials_api_base.rstrip("/")
        self.timeout = settings.clinicaltrials_timeout_sec

    def fetch_study(self, nct_id: str) -> dict:
        response = requests.get(
            f"{self.base_url}/studies/{str(nct_id).strip()}",
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def search_studies(
        self,
        *,
        condition: str,
        phase: str | None = None,
        sponsor: str | None = None,
        nct_id: str | None = None,
        page_size: int | None = None,
        max_pages: int | None = None,
        statuses: list[str] | None = None,
    ) -> dict:
        if nct_id:
            study = self.fetch_study(nct_id)
            return {
                "studies": [study],
                "pagesFetched": 1,
                "searchMode": "nct_id",
            }

        resolved_page_size = min(max(int(page_size or settings.search_page_size), 1), 100)
        resolved_max_pages = min(max(int(max_pages or settings.search_max_pages), 1), 20)
        requested_statuses = [str(item).strip() for item in (statuses or []) if str(item).strip()]

        next_page_token = None
        pages_fetched = 0
        filtered_studies: list[dict] = []

        while pages_fetched < resolved_max_pages and len(filtered_studies) < resolved_page_size:
            params: dict[str, Any] = {
                "query.cond": condition,
                "pageSize": min(100, resolved_page_size),
            }
            if requested_statuses:
                params["filter.overallStatus"] = ",".join(requested_statuses)
            if next_page_token:
                params["pageToken"] = next_page_token
            response = requests.get(f"{self.base_url}/studies", params=params, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            pages_fetched += 1

            for study in payload.get("studies") or []:
                if self._study_matches_filters(study, phase=phase, sponsor=sponsor):
                    filtered_studies.append(study)
                    if len(filtered_studies) >= resolved_page_size:
                        break

            next_page_token = payload.get("nextPageToken")
            if not next_page_token:
                break

        return {
            "studies": filtered_studies[:resolved_page_size],
            "pagesFetched": pages_fetched,
            "searchMode": "condition_search",
        }

    @staticmethod
    def _study_matches_filters(study: dict, *, phase: str | None = None, sponsor: str | None = None) -> bool:
        phase_filter = normalize_token(phase)
        sponsor_filter = normalize_token(sponsor)

        protocol_section = study.get("protocolSection") or {}
        design_module = protocol_section.get("designModule") or {}
        sponsor_module = protocol_section.get("sponsorCollaboratorsModule") or {}
        study_phases = [normalize_token(item) for item in (design_module.get("phases") or []) if item]
        lead_sponsor = normalize_token(((sponsor_module.get("leadSponsor") or {}).get("name")))

        if phase_filter and phase_filter not in study_phases:
            return False
        if sponsor_filter and sponsor_filter not in lead_sponsor:
            return False
        return True


def build_search_result_row(study: dict) -> dict:
    protocol_section = study.get("protocolSection") or {}
    identification_module = protocol_section.get("identificationModule") or {}
    status_module = protocol_section.get("statusModule") or {}
    sponsor_module = protocol_section.get("sponsorCollaboratorsModule") or {}
    conditions_module = protocol_section.get("conditionsModule") or {}
    design_module = protocol_section.get("designModule") or {}
    return {
        "nct_id": identification_module.get("nctId"),
        "title": identification_module.get("officialTitle") or identification_module.get("briefTitle"),
        "brief_title": identification_module.get("briefTitle"),
        "phase": (design_module.get("phases") or [None])[0],
        "status": status_module.get("overallStatus"),
        "sponsor": ((sponsor_module.get("leadSponsor") or {}).get("name")),
        "conditions": (conditions_module.get("conditions") or [])[:10],
        "source_last_updated": ((status_module.get("lastUpdatePostDateStruct") or {}).get("date")),
    }
