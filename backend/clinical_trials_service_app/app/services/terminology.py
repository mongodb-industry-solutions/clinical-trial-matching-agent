from __future__ import annotations

import requests

from app.config import get_settings


settings = get_settings()


class TerminologyProxyService:
    def __init__(self) -> None:
        self.terminology_base_url = settings.terminology_base_url
        self.snomed_navigator_url = settings.snomed_navigator_url
        self.timeout = settings.terminology_timeout_sec

    def get_status(self) -> dict:
        return {
            "terminology_base_url_configured": bool(self.terminology_base_url),
            "snomed_navigator_url_configured": bool(self.snomed_navigator_url),
            "message": (
                "Endpoints are prepared. Wire a SNOMED or terminology service URL to activate proxy mode."
                if not self.terminology_base_url and not self.snomed_navigator_url
                else "Terminology proxy endpoints are configured."
            ),
        }

    def resolve_snomed(self, payload: dict) -> dict:
        if not self.snomed_navigator_url:
            raise RuntimeError("SNOMED navigator URL is not configured yet.")
        response = requests.post(
            f"{self.snomed_navigator_url.rstrip('/')}/resolve",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def validate_binding(self, binding_id: str, payload: dict) -> dict:
        if not self.terminology_base_url:
            raise RuntimeError("Terminology binding service URL is not configured yet.")
        response = requests.post(
            f"{self.terminology_base_url.rstrip('/')}/bindings/{binding_id}/validate",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()
