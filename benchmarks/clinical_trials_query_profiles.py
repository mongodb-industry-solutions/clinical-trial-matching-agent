from __future__ import annotations

from copy import deepcopy


DEFAULT_QUERY_PROFILE = "broad_breast_phase3"

QUERY_PROFILES = {
    "broad_breast_phase3": {
        "label": "Broad Breast Cancer Phase 3",
        "demoFocus": "broad_breast_cancer",
        "description": "Covers a broad breast-cancer trial slice for canonical runtime verification and general demo health checks.",
        "targetWindowGoal": "not_stage_specific",
        "condition": "breast cancer",
        "phase": "PHASE3",
        "sponsor": None,
        "statuses": None,
        "page_size": 50,
        "max_pages": 4,
        "limit": 60,
    },
    "stage_ii_iii_breast_phase3": {
        "label": "Stage II/III Breast Cancer Phase 3",
        "demoFocus": "stage_ii_iii_breast_cancer",
        "description": "Prioritizes stage II, stage III, and locally advanced breast-cancer studies so the demonstrator can freeze a clearer pre-metastatic lane.",
        "targetWindowGoal": "stage_ii_iii_or_locally_advanced",
        "condition": "breast cancer stage ii stage iii locally advanced",
        "phase": "PHASE3",
        "sponsor": None,
        "statuses": None,
        "page_size": 50,
        "max_pages": 4,
        "limit": 60,
    },
}


def available_query_profiles() -> list[str]:
    return sorted(QUERY_PROFILES.keys())


def _normalized_statuses(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    return cleaned or None


def resolve_query_profile(
    *,
    profile_name: str | None = None,
    condition: str | None = None,
    phase: str | None = None,
    sponsor: str | None = None,
    statuses: list[str] | None = None,
    page_size: int | None = None,
    max_pages: int | None = None,
    limit: int | None = None,
) -> dict:
    resolved_profile_name = str(profile_name or DEFAULT_QUERY_PROFILE).strip() or DEFAULT_QUERY_PROFILE
    if resolved_profile_name not in QUERY_PROFILES:
        raise ValueError(f"Unknown query profile: {resolved_profile_name}")

    base = deepcopy(QUERY_PROFILES[resolved_profile_name])
    resolved_query = {
        "condition": str(condition).strip() if condition is not None and str(condition).strip() else base["condition"],
        "phase": str(phase).strip() if phase is not None and str(phase).strip() else base["phase"],
        "sponsor": str(sponsor).strip() if sponsor is not None and str(sponsor).strip() else base["sponsor"],
        "statuses": _normalized_statuses(statuses) if statuses is not None else _normalized_statuses(base.get("statuses")),
        "page_size": int(page_size) if page_size is not None else int(base["page_size"]),
        "max_pages": int(max_pages) if max_pages is not None else int(base["max_pages"]),
        "limit": int(limit) if limit is not None else int(base["limit"]),
    }
    query_profile = {
        "name": resolved_profile_name,
        "label": base["label"],
        "demoFocus": base["demoFocus"],
        "description": base["description"],
        "targetWindowGoal": base["targetWindowGoal"],
    }
    return {
        "query": resolved_query,
        "queryProfile": query_profile,
    }

