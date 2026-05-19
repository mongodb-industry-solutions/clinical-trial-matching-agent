from __future__ import annotations

import copy
import re
from typing import Callable

from app.config import get_settings
from app.services.helpers import normalize_token
from app.services.helpers import utcnow


GLOBAL_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "for",
    "have",
    "in",
    "is",
    "of",
    "or",
    "participants",
    "patient",
    "patients",
    "should",
    "the",
    "to",
    "with",
}
TRIAL_SEARCH_CONTRACT_VERSION = "trial_search_contract_v1"
PRIMARY_CANCER_SEARCH_TERMS = {
    "breast_carcinoma": ["breast cancer", "breast carcinoma", "mammary carcinoma"],
}


def _extract_tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9/+.-]+", str(text or "").lower()) if token not in GLOBAL_STOPWORDS]


def _contains_word(text: str, word: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", str(text or "")))


def _contains_any_word(text: str, words: list[str] | tuple[str, ...]) -> bool:
    return any(_contains_word(text, word) for word in words)


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _append_unique_search_terms(target: list[str], raw_values, *, limit: int = 48) -> list[str]:
    seen = {str(item).strip() for item in target if str(item).strip()}
    for raw_value in raw_values or []:
        normalized = normalize_token(raw_value)
        if not normalized:
            continue
        term = normalized.replace("_", " ").strip()
        if not term or term in seen:
            continue
        seen.add(term)
        target.append(term)
        if len(target) >= limit:
            break
    return target


def _search_terms_for_primary_cancer(values: list[str]) -> list[str]:
    terms: list[str] = []
    for value in values or []:
        aliases = PRIMARY_CANCER_SEARCH_TERMS.get(str(value).strip(), [str(value).strip().replace("_", " ")])
        _append_unique_search_terms(terms, aliases, limit=12)
    return terms


def _search_terms_for_compiled_rule(family_id: str, compiled_rule: dict) -> list[str]:
    logic = (compiled_rule or {}).get("logic") or {}
    value = logic.get("value")
    values = value if isinstance(value, list) else [value]
    normalized_values = [normalize_token(item) for item in values if normalize_token(item)]

    if family_id == "oncology.primary_cancer.required":
        return _search_terms_for_primary_cancer(normalized_values)
    if family_id == "oncology.advanced_or_metastatic.required":
        if any(item in {"advanced", "metastatic"} for item in normalized_values):
            return ["advanced breast cancer", "metastatic breast cancer", "locally advanced breast cancer"]
    if family_id == "biomarkers.ER.status.required":
        if normalized_values:
            marker_value = normalized_values[0]
            return [f"er {marker_value}", f"estrogen receptor {marker_value}"]
    if family_id == "biomarkers.HER2.status.required":
        if normalized_values:
            marker_value = normalized_values[0]
            return [f"her2 {marker_value}", f"her2 {marker_value} breast cancer"]
    if family_id == "performance.ecog.max":
        ecog_max = _safe_int(value)
        if ecog_max is not None and ecog_max >= 0:
            return [f"ecog {score}" for score in range(0, min(ecog_max, 4) + 1)]
    return []


def _candidate_signals_for_match_contract(hard_prefilter: dict, compiled_nodes: list[dict]) -> list[str]:
    signals: list[str] = []
    primary_cancers = [normalize_token(item) for item in (hard_prefilter.get("oncology.primary_cancer.any_of") or []) if normalize_token(item)]
    if primary_cancers:
        signals.append("primary_cancer")

    extent_values = [normalize_token(item) for item in (hard_prefilter.get("oncology.disease_extent.any_of") or []) if normalize_token(item)]
    if any(item in {"advanced", "metastatic"} for item in extent_values):
        for signal in ["stage_iv_or_metastatic", "disease_window_advanced_or_metastatic"]:
            if signal not in signals:
                signals.append(signal)

    ecog_max = _safe_int(hard_prefilter.get("performance.ecog.max"))
    if ecog_max is not None:
        for score in range(0, min(ecog_max, 4) + 1):
            signal = f"ecog_{score}"
            if signal not in signals:
                signals.append(signal)

    for node in compiled_nodes:
        compiled_rule = get_effective_compiled_rule(node) or {}
        family_id = str(compiled_rule.get("family") or "").strip()
        logic = compiled_rule.get("logic") or {}
        value = normalize_token(logic.get("value"))
        if family_id == "biomarkers.ER.status.required" and value:
            signals.append(f"er_{value}")
        elif family_id == "biomarkers.HER2.status.required" and value:
            signals.append(f"her2_{value}")
    return list(dict.fromkeys([str(item).strip() for item in signals if str(item).strip()]))[:16]


def _build_trial_search_contract(*, trial_summary: dict, compiled_nodes: list[dict], hard_prefilter: dict) -> dict:
    family_ids: list[str] = []
    required_features: list[str] = []
    keyword_terms: list[str] = []
    semantic_fragments: list[str] = []
    match_filters: dict[str, object] = {}
    mongo_profile_query: dict[str, object] = {}
    mongo_vector_filter: dict[str, object] = {}

    age_min = _safe_int(hard_prefilter.get("demographics.age_years.min"))
    age_max = _safe_int(hard_prefilter.get("demographics.age_years.max"))
    sex_any_of = [normalize_token(item) for item in (hard_prefilter.get("demographics.sex.any_of") or []) if normalize_token(item)]
    primary_cancers = [normalize_token(item) for item in (hard_prefilter.get("oncology.primary_cancer.any_of") or []) if normalize_token(item)]
    extent_values = [normalize_token(item) for item in (hard_prefilter.get("oncology.disease_extent.any_of") or []) if normalize_token(item)]
    ecog_max = _safe_int(hard_prefilter.get("performance.ecog.max"))

    if age_min is not None or age_max is not None:
        age_clause = {}
        if age_min is not None:
            age_clause["$gte"] = max(age_min, 0)
        if age_max is not None:
            age_clause["$lte"] = max(age_max, 0)
        if age_clause:
            mongo_profile_query["age"] = age_clause
            mongo_vector_filter["age"] = dict(age_clause)
            match_filters["ageYears"] = {
                "min": age_clause.get("$gte"),
                "max": age_clause.get("$lte"),
            }

    normalized_sex_any_of = sorted(list(dict.fromkeys(sex_any_of)))
    if normalized_sex_any_of:
        mongo_profile_query["gender"] = {"$in": normalized_sex_any_of}
        if len(normalized_sex_any_of) == 1:
            mongo_vector_filter["gender"] = normalized_sex_any_of[0]
        else:
            mongo_vector_filter["gender"] = {"$in": normalized_sex_any_of}
        match_filters["sex"] = normalized_sex_any_of

    normalized_primary_cancers = sorted(list(dict.fromkeys(primary_cancers)))
    if normalized_primary_cancers:
        mongo_profile_query["patientMatchProfile.matchFilters.primaryCancerCode"] = {"$in": normalized_primary_cancers}
        match_filters["primaryCancerCode"] = normalized_primary_cancers

    normalized_extent_values = sorted(list(dict.fromkeys(extent_values)))
    if normalized_extent_values:
        match_filters["diseaseExtent"] = normalized_extent_values
        if any(item in {"advanced", "metastatic"} for item in normalized_extent_values):
            match_filters["diseaseWindow"] = ["advanced_or_metastatic"]

    if ecog_max is not None:
        match_filters["performanceECOGMax"] = ecog_max

    _append_unique_search_terms(
        semantic_fragments,
        [
            trial_summary.get("title"),
            trial_summary.get("brief_title"),
            *(trial_summary.get("conditions") or []),
            *(trial_summary.get("keywords") or []),
            *(trial_summary.get("interventions") or []),
        ],
        limit=24,
    )
    _append_unique_search_terms(keyword_terms, semantic_fragments, limit=32)

    for node in compiled_nodes:
        compiled_rule = get_effective_compiled_rule(node) or {}
        family_id = str(compiled_rule.get("family") or "").strip()
        if family_id and family_id not in family_ids:
            family_ids.append(family_id)
        for feature in compiled_rule.get("required_patient_features") or []:
            normalized_feature = str(feature or "").strip()
            if normalized_feature and normalized_feature not in required_features:
                required_features.append(normalized_feature)
        derived_terms = _search_terms_for_compiled_rule(family_id, compiled_rule)
        _append_unique_search_terms(keyword_terms, derived_terms, limit=40)
        _append_unique_search_terms(semantic_fragments, derived_terms, limit=32)

    candidate_signals_any = _candidate_signals_for_match_contract(hard_prefilter, compiled_nodes)
    semantic_query_text = " | ".join(
        [
            f"title: {trial_summary.get('title') or trial_summary.get('brief_title') or ''}",
            f"conditions: {', '.join(keyword_terms[:12])}",
            f"candidate signals: {', '.join(candidate_signals_any[:8])}",
            f"required features: {', '.join(required_features[:12])}",
        ]
    ).strip()

    return {
        "version": TRIAL_SEARCH_CONTRACT_VERSION,
        "policy": {
            "shortlistRole": "candidate_retrieval_only",
            "finalDecisionRole": "compiled_criteria_only",
            "exclusionCriteriaPrefiltered": False,
            "vectorOptional": True,
            "recallSafeFilters": ["ageYears", "sex", "primaryCancerCode"],
            "rankingSignals": ["keywordTerms", "candidateSignalsAny", "matchFilters"],
        },
        "mongoProfileQuery": mongo_profile_query,
        "mongoVectorFilter": mongo_vector_filter,
        "signals": {
            "familyIds": family_ids[:24],
            "requiredPatientFeatures": required_features[:24],
            "keywordTerms": keyword_terms[:40],
            "candidateSignalsAny": candidate_signals_any[:16],
            "matchFilters": match_filters,
        },
        "semantic": {
            "queryText": semantic_query_text[:900],
            "queryTerms": keyword_terms[:24],
        },
    }


def _term_in_text(text: str, term: str) -> bool:
    normalized_text = str(text or "")
    normalized_term = str(term or "")
    if not normalized_term:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])", normalized_text))


def _criterion_source_text(node: dict, *, include_ancestor_headers: bool = False) -> str:
    raw_text = str((node.get("raw") or {}).get("text") or "")
    if not include_ancestor_headers:
        return raw_text
    headers = [str(item).strip() for item in (node.get("ancestor_headers") or []) if str(item).strip()]
    if not headers:
        return raw_text
    return " ".join(headers + [raw_text]).strip()


def _criterion_normalized_text(node: dict, *, include_ancestor_headers: bool = False) -> str:
    text = _criterion_source_text(node, include_ancestor_headers=include_ancestor_headers)
    normalized_text = normalize_token(text)
    if normalized_text:
        return normalized_text
    return normalize_token((node.get("normalized") or {}).get("text") or text)


def _build_candidate_entry(family: dict, score: float, validator_passed: bool | None, leftover_tokens: list[str]) -> dict:
    return {
        "family_id": family.get("family_id"),
        "score": round(score, 4),
        "validator_passed": validator_passed,
        "leftover_tokens": leftover_tokens[:8],
    }


def _mark_candidate_validated(candidate_entries: list[dict], family_id: str) -> list[dict]:
    normalized_family_id = str(family_id or "").strip()
    updated_entries = []
    for entry in candidate_entries:
        copied = copy.deepcopy(entry)
        if str(copied.get("family_id") or "").strip() == normalized_family_id:
            copied["validator_passed"] = True
        updated_entries.append(copied)
    return updated_entries


def _default_review_state(status: str, *, notes: str | None = None) -> dict:
    return {
        "status": status,
        "decision": None,
        "final_family_id": None,
        "slot_overrides": {},
        "final_compiled_rule": None,
        "reviewer": None,
        "reviewed_at": None,
        "notes": notes,
        "resolved_at": None,
    }


def _build_history_entry(compiler: dict, *, source: str) -> dict:
    try:
        settings = get_settings()
        parser_version = settings.parser_version
        family_registry_version = settings.family_registry_version
    except Exception:
        parser_version = "ctgov_det_ingest_v1"
        family_registry_version = "test"
    return {
        "ts": utcnow(),
        "source": source,
        "parser_version": parser_version,
        "family_registry_version": family_registry_version,
        "parse_status": compiler.get("parse_status"),
        "computability": compiler.get("computability"),
        "selected_family_id": compiler.get("selected_family_id"),
        "selected_family_version": compiler.get("selected_family_version"),
        "confidence": compiler.get("confidence"),
        "compiled_rule": copy.deepcopy(compiler.get("compiled_rule")),
        "validator_report": copy.deepcopy(compiler.get("validator_report")),
    }


def _estimate_patient_relevance_weight(node: dict) -> int:
    node_type = str(node.get("node_type") or "")
    if node_type not in {"leaf", "synthetic_structured"}:
        return 0

    family_id = str(((node.get("compiler") or {}).get("selected_family_id")) or "")
    normalized_text = normalize_token((node.get("normalized") or {}).get("text") or (node.get("raw") or {}).get("text"))

    if family_id.startswith(("oncology.", "biomarkers.", "therapy.", "disease.", "metastases.", "performance.")):
        return 5
    if family_id.startswith(("demographics.", "organ_function.")):
        return 4
    if family_id.startswith(("sample_submission.", "concomitant_illness.", "eligibility.")):
        return 2
    if any(token in normalized_text for token in {"metastatic", "breast cancer", "her2", "er+", "ecog", "progression", "therapy"}):
        return 4
    return 3


def _review_cluster_key(node: dict) -> str:
    fingerprint = str(((node.get("normalized") or {}).get("criterion_fingerprint")) or "").strip()
    family_id = str(((node.get("compiler") or {}).get("selected_family_id")) or "unassigned").strip()
    if fingerprint:
        return fingerprint
    return f"{family_id}::{normalize_token((node.get('raw') or {}).get('text') or '')[:80]}"


def _family_contract_computability(family: dict | None) -> str | None:
    return normalize_token((((family or {}).get("compiler_contract") or {}).get("computability")) or None)


def _generic_manual_compiled_rule(node: dict, family: dict) -> dict:
    contract = (family.get("compiler_contract") or {})
    return {
        "kind": node.get("section"),
        "family": family.get("family_id"),
        "logic": {
            "predicate": family.get("family_id"),
            "operator": "manual_review_required",
            "value": True,
        },
        "required_patient_features": list(contract.get("required_patient_features") or []),
        "prefilter_eligible": False,
    }


def _generic_not_patient_rule(node: dict, family: dict) -> dict:
    return {
        "kind": node.get("section"),
        "family": family.get("family_id"),
        "logic": {
            "predicate": family.get("family_id"),
            "operator": "not_patient_rule",
            "value": True,
        },
        "required_patient_features": [],
        "prefilter_eligible": False,
    }


def _finalize_compiled_node(
    compiled: dict,
    *,
    review_state: dict,
    previous_node: dict | None = None,
    history_source: str = "compile",
) -> dict:
    previous_history = list((((previous_node or {}).get("compiler") or {}).get("history")) or [])
    compiled.setdefault("compiler", {})
    compiled["compiler"]["history"] = previous_history + [_build_history_entry(compiled.get("compiler") or {}, source=history_source)]

    previous_review = ((previous_node or {}).get("review")) or {}
    if previous_review.get("status") == "reviewed":
        merged_review = copy.deepcopy(previous_review)
        merged_review.setdefault("slot_overrides", {})
        compiled["review"] = merged_review
    else:
        compiled["review"] = review_state

    compiled["analytics"] = {
        "patient_relevance_weight": _estimate_patient_relevance_weight(compiled),
        "review_cluster_key": _review_cluster_key(compiled),
    }
    return compiled


def get_effective_compiled_rule(node: dict) -> dict | None:
    review = node.get("review") or {}
    if review.get("decision") in {"confirm_family", "manual_review_clinical"}:
        if review.get("final_compiled_rule"):
            return copy.deepcopy(review.get("final_compiled_rule"))
        compiler_rule = (node.get("compiler") or {}).get("compiled_rule")
        if compiler_rule:
            return copy.deepcopy(compiler_rule)
    compiler_rule = (node.get("compiler") or {}).get("compiled_rule")
    return copy.deepcopy(compiler_rule) if compiler_rule else None


def get_effective_computability(node: dict) -> str | None:
    review = node.get("review") or {}
    if review.get("decision") == "not_patient_rule":
        return "not_patient_rule"
    if review.get("decision") == "manual_review_clinical":
        return "manual_review_clinical"
    if review.get("decision") == "confirm_family":
        return "deterministic"
    return (node.get("compiler") or {}).get("computability")


def _shortlist_families(node: dict, active_families: list[dict]) -> list[tuple[dict, float, list[str]]]:
    normalized_text = _criterion_normalized_text(node, include_ancestor_headers=True)
    section = normalize_token(node.get("section"))
    candidates: list[tuple[dict, float, list[str]]] = []
    for family in active_families:
        allowed_sections = [normalize_token(item) for item in (family.get("allowed_sections") or []) if normalize_token(item)]
        if allowed_sections and section and section not in allowed_sections:
            continue
        trigger_payload = family.get("triggers") or {}
        required_terms = [normalize_token(item) for item in (trigger_payload.get("must_contain_any") or []) if normalize_token(item)]
        blocked_terms = [normalize_token(item) for item in (trigger_payload.get("must_not_contain_any") or []) if normalize_token(item)]
        if blocked_terms and any(_term_in_text(normalized_text, term) for term in blocked_terms):
            continue
        matched_terms = [term for term in required_terms if _term_in_text(normalized_text, term)]
        if not matched_terms:
            continue
        coverage = len(matched_terms) / max(1, len(required_terms))
        phrase_bonus = 0.1 if any(" " in term for term in matched_terms) else 0.0
        score = min(0.99, 0.68 + (0.18 * coverage) + phrase_bonus)
        candidates.append((family, score, matched_terms))
    candidates.sort(key=lambda item: item[1], reverse=True)
    return candidates[:3]


def _compile_age_rule(node: dict, *, comparator: str, family_id: str) -> dict | None:
    text = normalize_token(node.get("raw", {}).get("text"))
    match = re.search(r"(?P<value>\d+)\s+years", text)
    if not match:
        return None
    value = int(match.group("value"))
    prefilter_key = "demographics.age_years"
    return {
        "confidence": 1.0,
        "compiled_rule": {
            "kind": node.get("section"),
            "family": family_id,
            "logic": {
                "predicate": prefilter_key,
                "operator": comparator,
                "value": value,
            },
            "required_patient_features": [prefilter_key],
            "prefilter_eligible": True,
        },
        "validator_report": {
            "numeric_leakage_detected": False,
            "range_interpreted_correctly": True,
            "section_compatible": True,
        },
    }


def _compile_sex_allowed_rule(node: dict) -> dict | None:
    text = _criterion_normalized_text(node)
    if node.get("source_kind") == "structured_field":
        if "female" in text and "male" in text:
            values = ["female", "male"]
        elif "female" in text:
            values = ["female"]
        elif "male" in text:
            values = ["male"]
        elif "all" in text:
            values = ["female", "male"]
        else:
            return None
    else:
        if any(
            token in text
            for token in {
                "childbearing",
                "pregnan",
                "pregnancy",
                "breast feeding",
                "breastfeeding",
                "contracept",
                "wocbp",
                "postmenop",
                "perimenop",
                "amenorr",
                "oophorectomy",
                "ovarian",
                "vasectomy",
                "oral contracept",
                "hormonal therapy",
            }
        ):
            return None
        if not (
            re.match(r"^(female|male|women|men|woman|man)\b", text)
            or re.search(r"\b(female|male)\s+and\s+(female|male)\s+(patients|participants|subjects)\b", text)
            or re.search(r"\b(women|men)\s+(aged|patients|participants|subjects)\b", text)
            or re.search(r"\bconsenting\s+women\b", text)
        ):
            return None

        has_female = _contains_any_word(text, ("female", "women", "woman"))
        has_male = _contains_any_word(text, ("male", "men", "man"))
        if has_female and has_male:
            values = ["female", "male"]
        elif has_female:
            values = ["female"]
        elif has_male:
            values = ["male"]
        else:
            return None
    return {
        "confidence": 0.97,
        "compiled_rule": {
            "kind": node.get("section"),
            "family": "demographics.sex.allowed",
            "logic": {
                "predicate": "demographics.sex",
                "operator": "in",
                "value": values,
            },
            "required_patient_features": ["demographics.sex"],
            "prefilter_eligible": True,
        },
        "validator_report": {
            "numeric_leakage_detected": False,
            "range_interpreted_correctly": True,
            "section_compatible": True,
        },
    }


def _compile_healthy_volunteers_rule(node: dict) -> dict | None:
    text = normalize_token(node.get("raw", {}).get("text"))
    if "true" in text:
        accepted = True
    elif "false" in text:
        accepted = False
    else:
        return None
    return {
        "confidence": 1.0,
        "compiled_rule": {
            "kind": node.get("section"),
            "family": "eligibility.healthy_volunteers.allowed",
            "logic": {
                "predicate": "patient.is_healthy_volunteer",
                "operator": "=",
                "value": accepted,
            },
            "required_patient_features": ["patient.is_healthy_volunteer"],
            "prefilter_eligible": False,
        },
        "validator_report": {
            "numeric_leakage_detected": False,
            "range_interpreted_correctly": True,
            "section_compatible": True,
        },
    }


def _compile_ecog_rule(node: dict) -> dict | None:
    normalized_text = _criterion_normalized_text(node, include_ancestor_headers=True)
    if "not specified" in normalized_text and "performance status" in normalized_text:
        return None
    if (
        "ecog" not in normalized_text
        and "performance status" not in normalized_text
        and "who performance status" not in normalized_text
        and "world health organization" not in normalized_text
    ):
        return None

    comparator_match = re.search(r"(?:<=|=<|≤|less than or equal to)\s*(?P<value>[0-5])", normalized_text)
    if comparator_match:
        value = int(comparator_match.group("value"))
    else:
        range_match = re.search(r"(?P<low>[0-5])\s*(?:-|to|or)\s*(?P<high>[0-5])", normalized_text)
        if not range_match:
            single_value_match = re.search(
                r"(?:\becog\b|who performance status|performance status|eastern cooperative oncology group).*?(?P<value>[0-5])"
                r"(?!\s*(?:-|to|or)\s*[0-5])",
                normalized_text,
            )
            if not single_value_match:
                return None
            value = int(single_value_match.group("value"))
        else:
            value = max(int(range_match.group("low")), int(range_match.group("high")))

    if value < 0 or value > 5:
        return None

    return {
        "confidence": 0.99,
        "compiled_rule": {
            "kind": node.get("section"),
            "family": "performance.ecog.max",
            "logic": {
                "predicate": "performance.ecog",
                "operator": "<=",
                "value": value,
            },
            "required_patient_features": ["performance.ecog"],
            "prefilter_eligible": True,
        },
        "validator_report": {
            "range_interpreted_correctly": True,
            "numeric_leakage_detected": False,
            "section_compatible": True,
            "or_logic_preserved": True,
        },
    }


def _compile_primary_cancer_rule(node: dict) -> dict | None:
    if normalize_token(node.get("section")) != "inclusion":
        return None
    text = _criterion_normalized_text(node, include_ancestor_headers=True)
    if not any(term in text for term in {"breast cancer", "breast carcinoma", "adenocarcinoma of the breast", "invasive breast carcinoma"}):
        return None
    if any(
        phrase in text
        for phrase in {
            "history of previous breast cancer",
            "breast cancer prevention",
            "prevention of breast cancer",
            "breast cancer study",
            "breast cancer surgery",
            "for breast cancer prevention",
            "concurrent enrollment on any other",
            "remained disease-free from the time of initial breast cancer diagnosis",
        }
    ):
        return None
    if re.search(r"\bbreast cancer\s+or\s+[a-z ]+cancer\b", text):
        return None
    if not any(
        phrase in text
        for phrase in {
            "pathologically confirmed",
            "histologically confirmed",
            "cytologically confirmed",
            "confirmed diagnosis",
            "confirmed hr+",
            "confirmed tnbc",
            "triple negative breast cancer",
            "diagnosis of",
            "primary adenocarcinoma of the breast",
            "invasive breast carcinoma",
            "patients with breast cancer",
            "participant has confirmed",
            "breast cancer with evidence of",
            "planned for surgical resection",
        }
    ):
        return None

    return {
        "confidence": 0.96,
        "compiled_rule": {
            "kind": node.get("section"),
            "family": "oncology.primary_cancer.required",
            "logic": {
                "predicate": "oncology.primary_cancer",
                "operator": "in",
                "value": ["breast_carcinoma"],
            },
            "required_patient_features": ["oncology.primary_cancer"],
            "prefilter_eligible": True,
        },
        "validator_report": {
            "section_compatible": True,
            "disease_site_confirmed": True,
            "numeric_leakage_detected": False,
        },
    }


def _compile_er_status_rule(node: dict) -> dict | None:
    if normalize_token(node.get("section")) != "inclusion":
        return None
    text = _criterion_normalized_text(node, include_ancestor_headers=True)
    if "triple negative" in text:
        value = "negative"
    else:
        if "estrogen receptor" not in text and not re.search(r"(?<![a-z0-9])er(?![a-z0-9])", text):
            return None
        positive = _contains_any_word(
            text,
            (
                "er+",
                "er-positive",
                "er positive",
                "estrogen receptor positive",
                "estrogen receptor (er)-positive",
            ),
        )
        negative = _contains_any_word(
            text,
            (
                "er-",
                "er-negative",
                "er negative",
                "estrogen receptor negative",
                "estrogen receptor (er)-negative",
                "<1% expression for estrogen receptor",
            ),
        )
        if positive == negative:
            return None
        value = "positive" if positive else "negative"

    return {
        "confidence": 0.96,
        "compiled_rule": {
            "kind": node.get("section"),
            "family": "biomarkers.ER.status.required",
            "logic": {
                "predicate": "biomarkers.ER.status",
                "operator": "=",
                "value": value,
            },
            "required_patient_features": ["biomarkers.ER.status"],
            "prefilter_eligible": True,
        },
        "validator_report": {
            "section_compatible": True,
            "biomarker_status_detected": True,
            "numeric_leakage_detected": False,
        },
    }


def _compile_her2_status_rule(node: dict) -> dict | None:
    if normalize_token(node.get("section")) != "inclusion":
        return None
    text = _criterion_normalized_text(node, include_ancestor_headers=True)
    if "triple negative" in text:
        value = "negative"
    else:
        if "her2" not in text and "her-2" not in text and "her2-neu" not in text:
            return None
        positive = _contains_any_word(
            text,
            (
                "her2+",
                "her2-positive",
                "her2 positive",
                "her-2 positive",
                "expressing her2-neu",
                "her2-neu",
            ),
        )
        negative = _contains_any_word(
            text,
            (
                "her2-",
                "her2-negative",
                "her2 negative",
                "her-2-negative",
                "negative for human epidermal growth factor receptor 2",
            ),
        )
        if positive == negative:
            return None
        value = "positive" if positive else "negative"

    return {
        "confidence": 0.96,
        "compiled_rule": {
            "kind": node.get("section"),
            "family": "biomarkers.HER2.status.required",
            "logic": {
                "predicate": "biomarkers.HER2.status",
                "operator": "=",
                "value": value,
            },
            "required_patient_features": ["biomarkers.HER2.status"],
            "prefilter_eligible": True,
        },
        "validator_report": {
            "section_compatible": True,
            "biomarker_status_detected": True,
            "numeric_leakage_detected": False,
        },
    }


def _compile_advanced_or_metastatic_rule(node: dict) -> dict | None:
    normalized_section = normalize_token(node.get("section"))
    text = _criterion_normalized_text(node, include_ancestor_headers=True)
    if any(
        phrase in text
        for phrase in {
            "no tumor recurrence or metastasis",
            "without evidence of distant metastases",
            "remained disease-free",
            "no findings suggestive of recurrent breast cancer",
        }
    ):
        return None

    target_values: list[str] = []
    if any(phrase in text for phrase in {"metastatic", "stage iv", "stage 4"}):
        target_values.append("metastatic")
    if any(phrase in text for phrase in {"advanced", "locally advanced", "unresectable"}):
        target_values.append("advanced")
    if any(phrase in text for phrase in {"locoregional recurrent", "locally recurrent", "recurrent disease"}):
        target_values.append("recurrent")
    target_values = [item for item in ["advanced", "metastatic", "recurrent"] if item in target_values]
    if not target_values:
        return None

    operator = "intersects"
    if normalized_section == "exclusion":
        operator = "not_intersects"

    return {
        "confidence": 0.95,
        "compiled_rule": {
            "kind": node.get("section"),
            "family": "oncology.advanced_or_metastatic.required",
            "logic": {
                "predicate": "oncology.disease_extent",
                "operator": operator,
                "value": target_values,
            },
            "required_patient_features": ["oncology.disease_extent"],
            "prefilter_eligible": True,
        },
        "validator_report": {
            "section_compatible": True,
            "disease_extent_detected": True,
            "numeric_leakage_detected": False,
        },
    }


def _compile_sample_submission_rule(node: dict) -> dict | None:
    normalized_text = normalize_token(node.get("raw", {}).get("text"))
    if "willing" not in normalized_text or ("blood" not in normalized_text and "tissue" not in normalized_text):
        return None
    return {
        "confidence": 0.95,
        "compiled_rule": {
            "kind": node.get("section"),
            "family": "sample_submission.willingness",
            "logic": {
                "predicate": "study_participation.sample_submission_willing",
                "operator": "=",
                "value": True,
            },
            "required_patient_features": ["study_participation.sample_submission_willing"],
            "prefilter_eligible": False,
        },
        "validator_report": {
            "numeric_leakage_detected": False,
            "section_compatible": True,
            "or_logic_preserved": True,
        },
    }


def _compile_cns_metastases_rule(node: dict) -> dict | None:
    normalized_text = normalize_token(node.get("raw", {}).get("text"))
    if "brain metastases" not in normalized_text and "cns metastases" not in normalized_text:
        return None
    operator = "!=" if node.get("section") == "exclusion" else "="
    return {
        "confidence": 0.95,
        "compiled_rule": {
            "kind": node.get("section"),
            "family": "metastases.cns.active",
            "logic": {
                "predicate": "metastases.cns.active",
                "operator": operator,
                "value": True,
            },
            "required_patient_features": ["metastases.cns.active"],
            "prefilter_eligible": True,
        },
        "validator_report": {
            "numeric_leakage_detected": False,
            "section_compatible": True,
            "or_logic_preserved": True,
        },
    }


LAB_CANONICAL_UNITS = {
    "anc": "10^3/uL",
    "platelet": "10^3/uL",
    "hemoglobin": "g/dL",
    "bilirubin": "mg/dL",
    "ast": "U/L",
    "alt": "U/L",
    "creatinine": "mg/dL",
    "creatinine_clearance": "mL/min",
    "egfr": "mL/min",
}

LAB_ALIAS_PATTERNS = {
    "anc": [r"absolute neutrophil count", r"absolute neutrophils?", r"\banc\b", r"neutrophil count"],
    "platelet": [r"platelets?", r"platelet count"],
    "hemoglobin": [r"ha?emoglobin"],
    "bilirubin": [r"total bilirubin", r"\bbilirubin\b", r"\btbil\b"],
    "ast": [r"aspartate aminotransferase", r"\bast\b"],
    "alt": [r"alanine aminotransferase", r"\balt\b"],
    "creatinine": [r"serum creatinine", r"\bcreatinine\b"],
    "creatinine_clearance": [r"creatinine clearance", r"\bcrcl\b"],
    "egfr": [r"\begfr\b", r"estimated glomerular filtration rate"],
}

PRIOR_EXPOSURE_TOKEN_MAP = {
    "perjeta": ["perjeta", "pertuzumab"],
    "fulvestrant": ["fulvestrant"],
    "lapatinib": ["lapatinib", "lapatinib tosylate"],
    "elacestrant": ["elacestrant"],
    "arv_471": ["arv-471", "arv 471"],
    "parp_inhibitor": ["parp inhibitor", "parp inhibitors"],
    "cdk4_6_inhibitor": ["cdk4/6 inhibitor", "cdk46 inhibitor", "cdk 4/6 inhibitor"],
    "topoisomerase_i_inhibitor": ["topoisomerase i inhibitor", "topoisomerase 1 inhibitor"],
    "trop2_targeted_therapy": ["trop-2-targeted therapy", "trop 2 targeted therapy", "trop-2 targeted therapy"],
    "chemotherapy": ["chemotherapy"],
    "endocrine_therapy": ["endocrine therapy", "hormonal therapy"],
    "biologic_therapy": ["biologic therapy", "biological therapy"],
    "radiation": ["radiation", "radiation therapy"],
    "surgery": ["surgery"],
    "investigational_agent": ["investigational agent", "investigational agents", "investigational treatment"],
    "mtor_inhibitor": ["mtor inhibitor", "mtor inhibitors"],
    "pi3k_inhibitor": ["pi3k inhibitor", "pi3k inhibitors"],
    "akt_inhibitor": ["akt pathway inhibitor", "akt pathway inhibitors", "akt inhibitor", "akt inhibitors"],
}

COMPARATOR_TOKEN_PATTERN = r"(?P<op>>=|=>|<=|=<|>|<|=|≥|≤|greater than or equal to|less than or equal to|greater than|less than)"
NUMERIC_TOKEN_PATTERN = r"(?P<value>\d[\d,]*(?:\.\d+)?)"
UNIT_TOKEN_PATTERN = r"(?P<unit>(?:x\s*)?10\^?\d+\/[a-z]+|10\*\d+\/[a-z]+|g\/dl|g\/l|mg\/dl|u\/l|iu\/l|ml\/min|ml\/min\/1\.73m2|[x×]?\s*uln|upper limit of normal|/ul|cells\/ul|cell\/ul|[a-z0-9^*/.%+-]+(?:\s*/\s*[a-z0-9^*/.%+-]+)?)?"


def _normalize_comparator(raw_value: str | None) -> str | None:
    normalized = normalize_token(raw_value)
    mapping = {
        ">=": ">=",
        "=>": ">=",
        "greater than or equal to": ">=",
        "≤": "<=",
        "<=": "<=",
        "=<": "<=",
        "less than or equal to": "<=",
        ">": ">",
        "greater than": ">",
        "<": "<",
        "less than": "<",
        "=": "=",
    }
    return mapping.get(normalized)


def _invert_comparator(raw_value: str | None) -> str | None:
    normalized = _normalize_comparator(raw_value)
    return {
        ">": "<=",
        ">=": "<",
        "<": ">=",
        "<=": ">",
        "=": "!=",
    }.get(normalized)


def _parse_float(raw_value: str | None) -> float | None:
    if raw_value in {None, ""}:
        return None
    try:
        return float(str(raw_value).replace(",", ""))
    except Exception:
        return None


def _normalize_lab_unit(lab_key: str, raw_unit: str | None) -> tuple[str | None, bool]:
    normalized_unit = normalize_token(raw_unit).replace("μ", "u").replace("micro", "u")
    uses_uln = "uln" in normalized_unit or "upper limit of normal" in normalized_unit
    if uses_uln:
        return "xULN", True
    if lab_key in {"anc", "platelet"}:
        if any(token in normalized_unit for token in {"10^3/ul", "x10^3/ul", "10^9/l", "x10^9/l", "k/ul", "10*3/ul"}):
            return LAB_CANONICAL_UNITS[lab_key], False
        if any(token in normalized_unit for token in {"/ul", "cells/ul", "cell/ul", "/mm3"}):
            return normalized_unit or LAB_CANONICAL_UNITS[lab_key], False
    if lab_key == "hemoglobin" and normalized_unit in {"g/l", "gm/l"}:
        return "g/L", False
    return LAB_CANONICAL_UNITS.get(lab_key) or (raw_unit.strip() if isinstance(raw_unit, str) and raw_unit.strip() else None), uses_uln


def _extract_lab_panel_clauses(text: str, *, section: str) -> list[dict]:
    clauses: list[dict] = []
    seen = set()
    normalized_text = normalize_token(text)
    section_normalized = normalize_token(section)

    for lab_key, aliases in LAB_ALIAS_PATTERNS.items():
        alias_pattern = "|".join(f"(?:{alias})" for alias in aliases)
        pattern = re.compile(
            rf"(?:{alias_pattern})[^.;\n]{{0,80}}?{COMPARATOR_TOKEN_PATTERN}\s*{NUMERIC_TOKEN_PATTERN}\s*{UNIT_TOKEN_PATTERN}",
            flags=re.IGNORECASE,
        )
        for match in pattern.finditer(normalized_text):
            comparator = _normalize_comparator(match.group("op"))
            value = _parse_float(match.group("value"))
            if comparator is None or value is None:
                continue
            stored_operator = comparator
            if section_normalized == "exclusion":
                stored_operator = _invert_comparator(comparator) or comparator
            canonical_unit, uses_uln = _normalize_lab_unit(lab_key, match.group("unit"))
            signature = (lab_key, stored_operator, value, canonical_unit, uses_uln)
            if signature in seen:
                continue
            seen.add(signature)
            clauses.append(
                {
                    "labKey": lab_key,
                    "operator": stored_operator,
                    "value": value,
                    "unit": canonical_unit,
                    "usesUln": uses_uln,
                    "sourceText": match.group(0).strip(),
                }
            )

    return clauses


def _compile_organ_function_lab_panel_rule(node: dict) -> dict | None:
    text = _criterion_normalized_text(node, include_ancestor_headers=True)
    if not any(term in text for term in {"hemoglobin", "platelet", "bilirubin", "ast", "alt", "creatinine", "anc", "neutrophil", "bone marrow function", "organ function"}):
        return None
    clauses = _extract_lab_panel_clauses(text, section=str(node.get("section") or ""))
    if not clauses:
        return None
    return {
        "confidence": 0.96,
        "compiled_rule": {
            "kind": node.get("section"),
            "family": "organ_function.lab_panel",
            "logic": {
                "predicate": "organ_function.lab_panel",
                "operator": "panel_satisfies",
                "clauses": clauses,
            },
            "required_patient_features": ["organ_function.key_labs"],
            "prefilter_eligible": False,
        },
        "validator_report": {
            "section_compatible": True,
            "lab_clause_count": len(clauses),
            "numeric_leakage_detected": False,
        },
    }


def _extract_prior_exposure_targets(text: str) -> list[str]:
    normalized_text = normalize_token(text)
    targets = []
    for target, aliases in PRIOR_EXPOSURE_TOKEN_MAP.items():
        if any(alias in normalized_text for alias in aliases):
            targets.append(target)
    return sorted(set(targets))


def _compile_prior_exposure_prohibited_history_rule(node: dict) -> dict | None:
    text = _criterion_normalized_text(node, include_ancestor_headers=True)
    negative_context = any(
        phrase in text
        for phrase in {
            "prior treatment with",
            "previous treatment with",
            "prior therapy with",
            "must not have received",
            "no prior",
            "received prior",
            "prior exposure",
        }
    )
    if not negative_context:
        return None
    targets = _extract_prior_exposure_targets(text)
    if not targets:
        return None
    return {
        "confidence": 0.95,
        "compiled_rule": {
            "kind": node.get("section"),
            "family": "therapy.prior_exposure.prohibited_history",
            "logic": {
                "predicate": "therapy.prior_exposure_history",
                "operator": "not_intersects",
                "value": targets,
            },
            "required_patient_features": ["therapy.prior_exposure_history"],
            "prefilter_eligible": False,
        },
        "validator_report": {
            "section_compatible": True,
            "recognized_prohibited_targets": targets,
            "numeric_leakage_detected": False,
        },
    }


def _compile_pregnancy_breastfeeding_context_rule(node: dict) -> dict | None:
    text = _criterion_normalized_text(node, include_ancestor_headers=True)
    if not any(term in text for term in {"pregnant", "pregnancy", "breastfeeding", "breast feeding", "nursing", "lactating", "childbearing potential", "wocbp", "contraception", "pregnancy test"}):
        return None
    requirements: list[dict] = []
    if any(term in text for term in {"pregnant", "pregnancy", "pregnancy test"}):
        requirements.append({"feature": "pregnancy", "expected": "absent"})
    if any(term in text for term in {"breastfeeding", "breast feeding", "nursing", "lactating"}):
        requirements.append({"feature": "breastfeeding", "expected": "absent"})
    if any(term in text for term in {"contraception", "childbearing potential", "wocbp"}):
        requirements.append({"feature": "contraceptionAcknowledged", "expected": "required"})
    if not requirements:
        return None
    return {
        "confidence": 0.94,
        "compiled_rule": {
            "kind": node.get("section"),
            "family": "context.pregnancy_breastfeeding",
            "logic": {
                "predicate": "context.pregnancy_breastfeeding",
                "operator": "requirements_satisfied",
                "requirements": requirements,
            },
            "required_patient_features": ["context.pregnancy_breastfeeding"],
            "prefilter_eligible": False,
        },
        "validator_report": {
            "section_compatible": True,
            "requirements": requirements,
            "numeric_leakage_detected": False,
        },
    }


def _compile_infection_recent_systemic_therapy_rule(node: dict) -> dict | None:
    text = _criterion_normalized_text(node, include_ancestor_headers=True)
    requirements: list[dict] = []
    if any(term in text for term in {"infection", "hepatitis b", "hepatitis c", "hiv", "hiv infection", "uncontrolled infection"}):
        requirements.append({"type": "active_infection_absent"})

    window_match = re.search(r"within\s+(?P<value>\d+)\s*(?P<unit>days?|weeks?)", text)
    if any(term in text for term in {"anti-tumor treatment", "anti tumor treatment", "investigational treatment", "investigational agent", "investigational agents", "anti-cancer therapy", "other anti-tumor treatments"}) and window_match:
        window_value = int(window_match.group("value"))
        unit = normalize_token(window_match.group("unit"))
        days = window_value * 7 if unit.startswith("week") else window_value
        requirements.append({"type": "recent_systemic_therapy_window", "windowDays": days})

    if not requirements:
        return None
    return {
        "confidence": 0.92,
        "compiled_rule": {
            "kind": node.get("section"),
            "family": "context.infection_recent_systemic_therapy",
            "logic": {
                "predicate": "context.infection_recent_systemic_therapy",
                "operator": "requirements_satisfied",
                "requirements": requirements,
            },
            "required_patient_features": ["context.infection_recent_systemic_therapy"],
            "prefilter_eligible": False,
        },
        "validator_report": {
            "section_compatible": True,
            "requirements": requirements,
            "numeric_leakage_detected": False,
        },
    }


def _compile_concomitant_illness_significant_rule(node: dict) -> dict | None:
    text = _criterion_normalized_text(node, include_ancestor_headers=True)
    if not any(
        phrase in text
        for phrase in {
            "significant concomitant illness",
            "clinically significant illness",
            "other serious illness",
            "serious physical or mental disease",
            "medical disorder or condition that",
            "investigator considers",
            "unsuitable for study",
        }
    ):
        return None
    return {
        "confidence": 0.82,
        "compiled_rule": {
            "kind": node.get("section"),
            "family": "concomitant_illness.significant",
            "logic": {
                "predicate": "concomitant_illness.significant",
                "operator": "manual_review_required",
                "value": True,
            },
            "required_patient_features": ["concomitant_illness.significant"],
            "prefilter_eligible": False,
        },
        "validator_report": {
            "section_compatible": True,
            "subjective_medical_judgment": True,
        },
    }


STRUCTURED_COMPILERS: dict[str, Callable[[dict], dict | None]] = {
    "AGE_MIN": lambda node: _compile_age_rule(node, comparator=">=", family_id="demographics.age.min"),
    "AGE_MAX": lambda node: _compile_age_rule(node, comparator="<=", family_id="demographics.age.max"),
    "SEX": _compile_sex_allowed_rule,
    "HEALTHY_VOLUNTEERS": _compile_healthy_volunteers_rule,
}


TEXT_COMPILERS: dict[str, Callable[[dict], dict | None]] = {
    "demographics.sex.allowed": _compile_sex_allowed_rule,
    "oncology.primary_cancer.required": _compile_primary_cancer_rule,
    "oncology.advanced_or_metastatic.required": _compile_advanced_or_metastatic_rule,
    "biomarkers.ER.status.required": _compile_er_status_rule,
    "biomarkers.HER2.status.required": _compile_her2_status_rule,
    "performance.ecog.max": _compile_ecog_rule,
    "organ_function.lab_panel": _compile_organ_function_lab_panel_rule,
    "therapy.prior_exposure.prohibited_history": _compile_prior_exposure_prohibited_history_rule,
    "context.pregnancy_breastfeeding": _compile_pregnancy_breastfeeding_context_rule,
    "context.infection_recent_systemic_therapy": _compile_infection_recent_systemic_therapy_rule,
    "concomitant_illness.significant": _compile_concomitant_illness_significant_rule,
    "sample_submission.willingness": _compile_sample_submission_rule,
    "metastases.cns.active": _compile_cns_metastases_rule,
}

STRUCTURED_COMPILER_FAMILY_IDS = {
    "demographics.age.min",
    "demographics.age.max",
    "demographics.sex.allowed",
    "eligibility.healthy_volunteers.allowed",
}
CONTRACT_CLASSIFIED_FAMILY_IDS = {
    "manual.stage_extent_context",
    "manual.safety_contraindication_history",
    "manual.complex_disease_setting",
    "manual.prior_exposure_contextual",
    "manual.inclusion_context_other",
    "manual.exclusion_context_other",
    "protocol_workflow.not_patient_rule",
}
TRIAL_COMPILER_SUPPORTED_FAMILY_IDS = STRUCTURED_COMPILER_FAMILY_IDS.union(set(TEXT_COMPILERS.keys())).union(CONTRACT_CLASSIFIED_FAMILY_IDS)


def compile_criterion_node(node: dict, active_families: list[dict], previous_node: dict | None = None) -> dict:
    compiled = copy.deepcopy(node)
    node_type = compiled.get("node_type")
    active_family_map = {
        str(family.get("family_id") or "").strip(): family
        for family in active_families
        if str(family.get("family_id") or "").strip()
    }

    if node_type in {"group_header", "note"}:
        compiled["compiler"] = {
            "parse_status": "context_only",
            "computability": "not_patient_rule",
            "selected_family_id": None,
            "selected_family_version": None,
            "confidence": 1.0,
            "candidate_families": [],
            "compiled_rule": None,
            "validator_report": {
                "section_compatible": True,
            },
        }
        return _finalize_compiled_node(compiled, review_state=_default_review_state("not_needed"), previous_node=previous_node)

    if compiled.get("source_kind") == "structured_field":
        token = str((compiled.get("path") or [None, None])[-1])
        compiler_fn = STRUCTURED_COMPILERS.get(token)
        result = compiler_fn(compiled) if compiler_fn else None
        if result:
            family_id = result["compiled_rule"]["family"]
            compiled["compiler"] = {
                "parse_status": "auto_compiled",
                "computability": "deterministic",
                "selected_family_id": family_id,
                "selected_family_version": 1,
                "confidence": result["confidence"],
                "candidate_families": [_build_candidate_entry({"family_id": family_id}, 1.0, True, [])],
                "compiled_rule": result["compiled_rule"],
                "validator_report": result["validator_report"],
            }
            return _finalize_compiled_node(compiled, review_state=_default_review_state("not_needed"), previous_node=previous_node)

    shortlisted = _shortlist_families(compiled, active_families)
    candidate_entries: list[dict] = []
    successful_matches: list[tuple[dict, float, dict]] = []
    raw_tokens = _extract_tokens(_criterion_source_text(compiled, include_ancestor_headers=True))

    def auto_classify_family(selected_family: dict, *, score: float, result: dict | None = None) -> dict | None:
        contract_computability = _family_contract_computability(selected_family)
        if contract_computability not in {"manual_review_clinical", "not_patient_rule"}:
            return None

        family_id = str(selected_family.get("family_id") or "").strip()
        compiled_rule = copy.deepcopy((result or {}).get("compiled_rule"))
        if compiled_rule is None and contract_computability == "manual_review_clinical":
            compiled_rule = _generic_manual_compiled_rule(compiled, selected_family)
        elif compiled_rule is None and contract_computability == "not_patient_rule":
            compiled_rule = _generic_not_patient_rule(compiled, selected_family)

        validator_report = copy.deepcopy((result or {}).get("validator_report") or {})
        validator_report.setdefault("section_compatible", True)
        if contract_computability == "manual_review_clinical":
            validator_report["manual_family_assigned"] = True
        else:
            validator_report["not_patient_rule_assigned"] = True

        compiled["compiler"] = {
            "parse_status": "manual_classified" if contract_computability == "manual_review_clinical" else "context_only",
            "computability": contract_computability,
            "selected_family_id": family_id,
            "selected_family_version": int(selected_family.get("version") or 1),
            "confidence": round(max(score, float((result or {}).get("confidence") or 0.0)), 4),
            "candidate_families": _mark_candidate_validated(candidate_entries, family_id),
            "compiled_rule": compiled_rule,
            "validator_report": validator_report,
        }
        review_note = (
            "Automatically classified into explicit manual family."
            if contract_computability == "manual_review_clinical"
            else "Automatically classified as a protocol/workflow-only criterion."
        )
        return _finalize_compiled_node(
            compiled,
            review_state=_default_review_state("not_needed", notes=review_note),
            previous_node=previous_node,
        )

    for family, score, matched_terms in shortlisted:
        compiler_fn = TEXT_COMPILERS.get(family.get("family_id"))
        result = compiler_fn(compiled) if compiler_fn else None
        leftover_tokens = [token for token in raw_tokens if all(term not in token for term in matched_terms)]
        validator_passed = result is not None
        candidate_entries.append(_build_candidate_entry(family, score, validator_passed, leftover_tokens))
        if result is not None:
            successful_matches.append((family, score, result))

    if len(successful_matches) > 1 and max(successful_matches[1][1], float(successful_matches[1][2].get("confidence") or 0.0)) >= 0.9:
        compiled["compiler"] = {
            "parse_status": "needs_review",
            "computability": "manual_review_clinical",
            "selected_family_id": successful_matches[0][0].get("family_id"),
            "selected_family_version": 1,
            "confidence": round(successful_matches[0][1], 4),
            "candidate_families": candidate_entries,
            "compiled_rule": None,
            "validator_report": {
                "composite_criterion_detected": True,
                "section_compatible": True,
            },
        }
        return _finalize_compiled_node(
            compiled,
            review_state=_default_review_state(
                "pending",
                notes="Multiple deterministic families matched the same criterion. Review instead of guessing.",
            ),
            previous_node=previous_node,
        )

    if successful_matches:
        selected_family, score, result = successful_matches[0]
        contract_classification = auto_classify_family(selected_family, score=score, result=result)
        if contract_classification is not None:
            return contract_classification
        parse_status = "auto_compiled" if max(score, float(result.get("confidence") or 0.0)) >= 0.9 else "needs_review"
        computability = "deterministic" if parse_status == "auto_compiled" else "manual_review_clinical"
        compiled["compiler"] = {
            "parse_status": parse_status,
            "computability": computability,
            "selected_family_id": selected_family.get("family_id"),
            "selected_family_version": int(selected_family.get("version") or 1),
            "confidence": round(result["confidence"], 4),
            "candidate_families": candidate_entries,
                "compiled_rule": result["compiled_rule"] if parse_status == "auto_compiled" else None,
                "validator_report": result["validator_report"],
        }
        return _finalize_compiled_node(
            compiled,
            review_state=_default_review_state(
                "not_needed" if parse_status == "auto_compiled" else "pending",
                notes=None if parse_status == "auto_compiled" else "Confidence below auto-compile threshold.",
            ),
            previous_node=previous_node,
        )

    for selected_family, score, _matched_terms in shortlisted:
        contract_classification = auto_classify_family(selected_family, score=score, result=None)
        if contract_classification is not None:
            return contract_classification

    section = normalize_token(compiled.get("section"))
    fallback_family_id = "manual.exclusion_context_other" if section == "exclusion" else "manual.inclusion_context_other"
    fallback_family = active_family_map.get(fallback_family_id)
    if fallback_family is not None:
        candidate_entries.append(_build_candidate_entry(fallback_family, 0.51, True, raw_tokens[:8]))
        contract_classification = auto_classify_family(fallback_family, score=0.51, result=None)
        if contract_classification is not None:
            return contract_classification

    compiled["compiler"] = {
        "parse_status": "needs_review",
        "computability": "manual_review_clinical",
        "selected_family_id": candidate_entries[0]["family_id"] if candidate_entries else None,
        "selected_family_version": 1 if candidate_entries else None,
        "confidence": round(candidate_entries[0]["score"], 4) if candidate_entries else 0.0,
        "candidate_families": candidate_entries,
        "compiled_rule": None,
        "validator_report": {
            "leftover_clinical_tokens": raw_tokens[:8],
            "section_compatible": True,
        },
    }
    return _finalize_compiled_node(
        compiled,
        review_state=_default_review_state("pending", notes="No deterministic family passed validation."),
        previous_node=previous_node,
    )


def summarize_criteria(compiled_nodes: list[dict]) -> dict:
    total_nodes = len(compiled_nodes)
    leaf_nodes = sum(1 for node in compiled_nodes if node.get("node_type") in {"leaf", "synthetic_structured"})
    group_headers = sum(1 for node in compiled_nodes if node.get("node_type") == "group_header")
    patient_rules = sum(
        1
        for node in compiled_nodes
        if get_effective_computability(node) in {
            "deterministic",
            "deterministic_after_protocol_enrichment",
            "manual_review_clinical",
        }
    )
    deterministic_rules = sum(1 for node in compiled_nodes if get_effective_computability(node) == "deterministic")
    manual_review_rules = sum(1 for node in compiled_nodes if (node.get("review") or {}).get("status") == "pending")
    not_patient_rules = sum(1 for node in compiled_nodes if get_effective_computability(node) == "not_patient_rule")
    return {
        "total_nodes": total_nodes,
        "leaf_nodes": leaf_nodes,
        "group_headers": group_headers,
        "patient_rules": patient_rules,
        "deterministic_rules": deterministic_rules,
        "manual_review_rules": manual_review_rules,
        "not_patient_rules": not_patient_rules,
    }


def materialize_match_contract(trial_summary: dict, compiled_nodes: list[dict]) -> dict:
    hard_prefilter: dict = {}
    deterministic_ids: list[str] = []
    manual_ids: list[str] = []

    for node in compiled_nodes:
        compiler = node.get("compiler") or {}
        compiled_rule = get_effective_compiled_rule(node) or {}
        effective_computability = get_effective_computability(node)
        if effective_computability == "deterministic":
            deterministic_ids.append(node["_id"])
            if compiled_rule.get("prefilter_eligible"):
                logic = compiled_rule.get("logic") or {}
                predicate = logic.get("predicate")
                operator = logic.get("operator")
                value = logic.get("value")
                if predicate == "demographics.age_years" and operator == ">=":
                    hard_prefilter["demographics.age_years.min"] = value
                elif predicate == "demographics.age_years" and operator == "<=":
                    hard_prefilter["demographics.age_years.max"] = value
                elif predicate == "demographics.sex" and operator == "in":
                    hard_prefilter["demographics.sex.any_of"] = value
                elif predicate == "performance.ecog" and operator == "<=":
                    hard_prefilter["performance.ecog.max"] = value
                elif predicate == "metastases.cns.active" and operator == "!=":
                    hard_prefilter["metastases.cns.active"] = False
        elif effective_computability == "manual_review_clinical" or (node.get("review") or {}).get("status") == "pending":
            manual_ids.append(node["_id"])

    disease_area = str(trial_summary.get("disease_area") or "")
    metadata_text = normalize_token(
        " ".join(
            [
                str(trial_summary.get("title") or ""),
                str(trial_summary.get("brief_title") or ""),
                " ".join(trial_summary.get("conditions") or []),
                " ".join(trial_summary.get("keywords") or []),
            ]
        )
    )
    if disease_area == "breast_cancer":
        hard_prefilter.setdefault("oncology.primary_cancer.any_of", ["breast_carcinoma"])
    if "metastatic" in metadata_text or "advanced" in metadata_text or "locoregional recurrent" in metadata_text:
        hard_prefilter.setdefault("oncology.disease_extent.any_of", ["advanced", "metastatic"])

    search_contract = _build_trial_search_contract(
        trial_summary=trial_summary,
        compiled_nodes=compiled_nodes,
        hard_prefilter=hard_prefilter,
    )

    return {
        "hard_prefilter": hard_prefilter,
        "deterministic_criterion_ids": deterministic_ids,
        "manual_criterion_ids": manual_ids,
        "search_contract": search_contract,
    }
