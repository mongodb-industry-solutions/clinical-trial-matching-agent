from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any

from app.config import get_settings
from app.database import collections, list_active_rule_families
from app.services.compiler import (
    compile_criterion_node,
    get_effective_compiled_rule,
    get_effective_computability,
    materialize_match_contract,
    summarize_criteria,
)
from app.services.ctgov_client import ClinicalTrialsGovClient
from app.services.helpers import normalize_token, parse_age_string, serialize_document, stable_json_hash, utcnow
from app.services.normalizer import normalize_criterion_text
from app.services.parser import build_synthetic_structured_criteria, parse_markdown_eligibility_tree


RUN_STAGE_NAMES = {
    "ingest_trial": [
        "fetch_source",
        "parse_structure",
        "compile_criteria",
        "materialize_match_contract",
    ],
    "bulk_ingest_query": [
        "search_remote",
        "ingest_trials",
        "summarize_corpus",
    ],
    "recompile_trial": [
        "load_version",
        "parse_structure",
        "compile_criteria",
        "materialize_match_contract",
    ],
    "recompile_family": [
        "select_trials",
        "recompile_trials",
        "summarize_coverage",
    ],
    "review_apply": [
        "apply_review",
        "trigger_recompile",
    ],
}
TERMINAL_RUN_STATUSES = {"completed", "completed_with_review", "failed", "skipped_unchanged"}


def _infer_disease_area(title: str, conditions: list[str]) -> str:
    combined = normalize_token(" ".join([title, *conditions]))
    if "breast" in combined:
        return "breast_cancer"
    primary = normalize_token(conditions[0]) if conditions else ""
    return primary.replace(" ", "_") if primary else "unknown"


def _structured_eligibility_from_study(study_doc: dict) -> dict:
    eligibility_module = ((study_doc.get("protocolSection") or {}).get("eligibilityModule") or {})
    sex_value = str(eligibility_module.get("sex") or "").strip().upper() or "UNKNOWN"
    if sex_value == "FEMALE":
        normalized_sex = "FEMALE"
    elif sex_value == "MALE":
        normalized_sex = "MALE"
    elif sex_value == "ALL":
        normalized_sex = "ALL"
    else:
        normalized_sex = "UNKNOWN"
    return {
        "sex": normalized_sex,
        "minimum_age": parse_age_string(eligibility_module.get("minimumAge")),
        "maximum_age": parse_age_string(eligibility_module.get("maximumAge")),
        "healthy_volunteers": eligibility_module.get("healthyVolunteers"),
    }


def _build_stage_relevance(trial_summary: dict, compiled_nodes: list[dict], match_contract: dict | None = None) -> dict:
    family_ids = []
    normalized_texts = []
    for node in compiled_nodes or []:
        family_id = (
            ((node.get("review") or {}).get("final_family_id"))
            or ((get_effective_compiled_rule(node) or {}).get("family"))
            or ((node.get("compiler") or {}).get("selected_family_id"))
            or ""
        )
        family_ids.append(str(family_id or "").strip())
        text_value = normalize_token(((node.get("raw") or {}).get("text")) or "")
        if text_value:
            normalized_texts.append(text_value)
    trial_level_texts = []
    for value in [
        trial_summary.get("title"),
        trial_summary.get("brief_title"),
        *(trial_summary.get("conditions") or []),
        *(trial_summary.get("keywords") or []),
    ]:
        normalized_value = normalize_token(value)
        if normalized_value:
            trial_level_texts.append(normalized_value)
    all_stage_texts = normalized_texts + trial_level_texts

    stage_context_count = sum(1 for family_id in family_ids if family_id == "manual.stage_extent_context")
    complex_setting_count = sum(1 for family_id in family_ids if family_id == "manual.complex_disease_setting")
    advanced_metastatic_count = sum(1 for family_id in family_ids if family_id == "oncology.advanced_or_metastatic.required")
    hard_prefilter = deepcopy(((match_contract or {}).get("hard_prefilter")) or {})
    hard_extent_values = [normalize_token(item) for item in (hard_prefilter.get("oncology.disease_extent.any_of") or []) if normalize_token(item)]
    stage_ii_iii_signal = bool(
        stage_context_count > 0
        or any(
            phrase in text
            for text in all_stage_texts
            for phrase in [
                "stage i-iii",
                "stage ii",
                "stage iii",
                "stage 2",
                "stage 3",
                "early stage",
                "locally advanced",
                "neoadjuvant",
                "adjuvant",
                "resected invasive",
                "residual invasive disease",
                "without evidence of distant metastases",
                "node positive",
                "node-positive",
            ]
        )
    )
    advanced_or_metastatic_signal = bool(
        advanced_metastatic_count > 0
        or any(value in {"advanced", "metastatic", "recurrent"} for value in hard_extent_values)
        or any(
            phrase in text
            for text in all_stage_texts
            for phrase in [
                "advanced/metastatic",
                "advanced metastatic",
                "metastatic",
                "recurrent",
                "stage iv",
                "stage 4",
                "unresectable",
                "locoregional recurrent",
            ]
        )
    )
    if stage_ii_iii_signal and not advanced_or_metastatic_signal:
        target_window = "stage_ii_iii_or_locally_advanced"
    elif advanced_or_metastatic_signal and not stage_ii_iii_signal:
        target_window = "advanced_or_metastatic"
    elif stage_ii_iii_signal or advanced_or_metastatic_signal or complex_setting_count > 0:
        target_window = "mixed_or_manual"
    else:
        target_window = "not_stage_specific"
    return {
        "targetWindow": target_window,
        "manualReviewRequired": bool(stage_context_count > 0 or complex_setting_count > 0),
        "manualStageContextCount": stage_context_count,
        "manualComplexSettingCount": complex_setting_count,
        "advancedOrMetastaticCriterionCount": advanced_metastatic_count,
        "hardPrefilterExtentValues": hard_extent_values,
        "diseaseArea": trial_summary.get("disease_area"),
    }


def _normalize_stage_target_window(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {
        "stage_ii_iii_or_locally_advanced",
        "advanced_or_metastatic",
        "mixed_or_manual",
        "not_stage_specific",
    }:
        return normalized
    return None


def _effective_stage_target_windows(stage_target_window: str | None) -> list[str]:
    normalized = _normalize_stage_target_window(stage_target_window)
    if not normalized:
        return []
    if normalized in {"stage_ii_iii_or_locally_advanced", "advanced_or_metastatic"}:
        return [normalized, "mixed_or_manual"]
    return [normalized]


def _trial_version_id(nct_id: str, source_hash: str) -> str:
    return f"{nct_id}::{source_hash[:16]}"


def _run_scope_from_payload(payload: dict) -> dict:
    query = {
        "condition": payload.get("condition"),
        "phase": payload.get("phase"),
        "sponsor": payload.get("sponsor"),
        "nct_id": payload.get("nct_id"),
    }
    trial_ids = list(payload.get("trial_ids") or [])
    if payload.get("nct_id") and payload.get("nct_id") not in trial_ids:
        trial_ids.append(payload.get("nct_id"))
    return {
        "nct_id": payload.get("nct_id"),
        "query": query,
        "condition": payload.get("condition"),
        "phase": payload.get("phase"),
        "trial_ids": trial_ids,
        "family_id": payload.get("family_id"),
        "criterion_id": payload.get("criterion_id"),
    }


def _build_trial_version_document(study_doc: dict, *, nct_id: str, source_hash: str, run_id: str) -> dict:
    protocol_section = study_doc.get("protocolSection") or {}
    status_module = protocol_section.get("statusModule") or {}
    document_section = study_doc.get("documentSection") or {}
    now = utcnow()
    large_docs = document_section.get("largeDocumentModule") or {}
    large_doc_list = large_docs.get("largeDocs") or large_docs.get("largeDocumentList") or []
    return {
        "_id": _trial_version_id(nct_id, source_hash),
        "nct_id": nct_id,
        "source": "clinicaltrials_gov_api_v2",
        "source_hash": source_hash,
        "source_study_last_updated": ((status_module.get("lastUpdatePostDateStruct") or {}).get("date")),
        "fetched_at": now,
        "ingested_by_run_id": run_id,
        "payload_meta": {
            "has_results": bool(study_doc.get("resultsSection")),
            "has_large_docs": bool(large_doc_list),
            "large_doc_count": len(large_doc_list),
        },
        "source_payload": deepcopy(study_doc),
    }


def _corpus_membership_entry(payload: dict) -> dict | None:
    condition = payload.get("condition")
    phase = payload.get("phase")
    sponsor = payload.get("sponsor")
    if not any([condition, phase, sponsor]):
        return None
    now = utcnow()
    query_key = "|".join(part for part in [condition or "", phase or "", sponsor or ""] if part)
    return {
        "query_key": query_key or "manual",
        "condition": condition,
        "phase": phase,
        "sponsor": sponsor,
        "first_seen_at": now,
        "last_seen_at": now,
    }


def _merge_corpus_membership(existing_membership: list[dict] | None, payload: dict) -> list[dict]:
    merged = [deepcopy(entry) for entry in (existing_membership or [])]
    incoming = _corpus_membership_entry(payload)
    if not incoming:
        return merged

    for entry in merged:
        if entry.get("query_key") != incoming.get("query_key"):
            continue
        entry["condition"] = incoming.get("condition")
        entry["phase"] = incoming.get("phase")
        entry["sponsor"] = incoming.get("sponsor")
        entry["last_seen_at"] = incoming.get("last_seen_at")
        entry.setdefault("first_seen_at", incoming.get("first_seen_at"))
        return merged

    merged.append(incoming)
    merged.sort(key=lambda row: str(row.get("query_key") or ""))
    return merged


def _build_trial_summary(study_doc: dict, *, version_id: str, run_id: str, existing_trial: dict | None, payload: dict) -> dict:
    protocol_section = study_doc.get("protocolSection") or {}
    identification_module = protocol_section.get("identificationModule") or {}
    design_module = protocol_section.get("designModule") or {}
    status_module = protocol_section.get("statusModule") or {}
    sponsor_module = protocol_section.get("sponsorCollaboratorsModule") or {}
    conditions_module = protocol_section.get("conditionsModule") or {}
    contacts_module = protocol_section.get("contactsLocationsModule") or {}

    nct_id = identification_module.get("nctId")
    title = identification_module.get("officialTitle") or identification_module.get("briefTitle") or ""
    conditions = list(conditions_module.get("conditions") or [])
    keywords = list(conditions_module.get("keywords") or [])
    structured_eligibility = _structured_eligibility_from_study(study_doc)
    disease_area = _infer_disease_area(title, conditions)
    now = utcnow()

    existing_compile = (existing_trial or {}).get("compile") or {}
    return {
        "_id": nct_id,
        "nct_id": nct_id,
        "current_version_id": version_id,
        "title": title,
        "brief_title": identification_module.get("briefTitle"),
        "acronym": identification_module.get("acronym"),
        "phase": (design_module.get("phases") or [None])[0],
        "study_type": design_module.get("studyType"),
        "status": status_module.get("overallStatus"),
        "sponsor": {
            "lead": ((sponsor_module.get("leadSponsor") or {}).get("name")),
            "class": ((sponsor_module.get("leadSponsor") or {}).get("class")),
        },
        "conditions": conditions[:20],
        "keywords": keywords[:20],
        "disease_area": disease_area,
        "structured_eligibility": structured_eligibility,
        "criteria_summary": {
            "total_nodes": 0,
            "leaf_nodes": 0,
            "group_headers": 0,
            "patient_rules": 0,
            "deterministic_rules": 0,
            "manual_review_rules": 0,
            "not_patient_rules": 0,
        },
        "compile": {
            "status": existing_compile.get("status") or "parsing",
            "last_run_id": run_id,
            "parser_version": get_settings().parser_version,
            "family_registry_version": get_settings().family_registry_version,
            "review_pending_count": int(existing_compile.get("review_pending_count") or 0),
        },
        "match_contract": {
            "hard_prefilter": {},
            "deterministic_criterion_ids": [],
            "manual_criterion_ids": [],
        },
        "stage_relevance": deepcopy((existing_trial or {}).get("stage_relevance") or {
            "targetWindow": "not_stage_specific",
            "manualReviewRequired": False,
            "manualStageContextCount": 0,
            "manualComplexSettingCount": 0,
            "advancedOrMetastaticCriterionCount": 0,
            "hardPrefilterExtentValues": [],
            "diseaseArea": disease_area,
        }),
        "coverage_snapshot": deepcopy((existing_trial or {}).get("coverage_snapshot") or {}),
        "review_snapshot": deepcopy((existing_trial or {}).get("review_snapshot") or {}),
        "corpus_membership": _merge_corpus_membership((existing_trial or {}).get("corpus_membership"), payload),
        "runtimeReadiness": deepcopy((existing_trial or {}).get("runtimeReadiness") or {
            "runnable": False,
            "reasonIfBlocked": "trial_not_materialized",
            "deterministicPatientImpactingCount": 0,
            "manualPatientImpactingCount": 0,
            "unsupportedPatientImpactingCount": 0,
            "coveragePct": 0.0,
            "hasExclusionParseGap": False,
        }),
        "display": {
            "locations_count": len(contacts_module.get("locations") or []),
            "updated_on_source": ((status_module.get("lastUpdatePostDateStruct") or {}).get("date")),
            "last_ingested_at": now,
        },
    }


def _criterion_signature(node: dict) -> dict:
    review = node.get("review") or {}
    return {
        "parse_status": ((node.get("compiler") or {}).get("parse_status")),
        "selected_family_id": ((node.get("compiler") or {}).get("selected_family_id")),
        "effective_computability": get_effective_computability(node),
        "effective_compiled_rule": get_effective_compiled_rule(node),
        "review_status": review.get("status"),
        "review_decision": review.get("decision"),
        "review_final_family_id": review.get("final_family_id"),
    }


def _count_changed_criteria(compiled_nodes: list[dict], previous_by_id: dict[str, dict]) -> int:
    changed = 0
    for node in compiled_nodes:
        previous = previous_by_id.get(node.get("_id"))
        if previous is None:
            changed += 1
            continue
        if _criterion_signature(previous) != _criterion_signature(node):
            changed += 1
    return changed


def _latest_rule_family_map() -> dict[str, dict]:
    family_map: dict[str, dict] = {}
    cursor = collections.rule_families.find().sort([("family_id", 1), ("version", -1)])
    for family in cursor:
        family_id = str(family.get("family_id") or "").strip()
        if not family_id or family_id in family_map:
            continue
        family_map[family_id] = family
    if family_map:
        return family_map
    return {
        str(family.get("family_id")): family
        for family in list_active_rule_families()
        if str(family.get("family_id") or "").strip()
    }


def _build_runtime_readiness(compiled_nodes: list[dict]) -> dict:
    family_map = _latest_rule_family_map()
    patient_nodes = [
        node
        for node in compiled_nodes
        if get_effective_computability(node) != "not_patient_rule"
        and str(node.get("node_type") or "") != "group_header"
    ]
    total_patient_impacting = len(patient_nodes)
    deterministic_supported = 0
    manual_patient_impacting = 0
    unsupported_patient_impacting = 0
    unresolved_patient_impacting = 0
    has_exclusion_parse_gap = False

    for node in patient_nodes:
        computability = get_effective_computability(node)
        family_id = (
            ((get_effective_compiled_rule(node) or {}).get("family"))
            or ((node.get("review") or {}).get("final_family_id"))
            or ((node.get("compiler") or {}).get("selected_family_id"))
        )
        family_doc = family_map.get(str(family_id or "").strip()) or {}
        family_support = (family_doc.get("patientSupport") or {})
        family_contract = (family_doc.get("compiler_contract") or {})
        supported = bool(family_support.get("supported"))
        family_contract_computability = str(family_contract.get("computability") or "").strip() or str(computability or "").strip()
        is_unassigned = not str(family_id or "").strip() or str(family_id or "").strip() in {"unassigned", "unassigned.miscellaneous"}
        is_named_manual = family_contract_computability == "manual_review_clinical" and not is_unassigned

        if computability == "deterministic":
            if supported:
                deterministic_supported += 1
            else:
                unsupported_patient_impacting += 1
        else:
            manual_patient_impacting += 1
            if is_unassigned:
                unresolved_patient_impacting += 1

        if (
            str(node.get("section") or "").strip().lower() == "exclusion"
            and computability != "deterministic"
            and not is_named_manual
        ):
            has_exclusion_parse_gap = True

    coverage_pct = round((deterministic_supported / total_patient_impacting) * 100.0, 1) if total_patient_impacting else 0.0
    runtime_state = "blocked_unsupported"
    runnable = False
    reason_if_blocked = None
    if not total_patient_impacting:
        reason_if_blocked = "no_patient_impacting_criteria_compiled"
    elif unresolved_patient_impacting:
        reason_if_blocked = f"{unresolved_patient_impacting} unassigned patient-impacting criteria remain"
    elif unsupported_patient_impacting:
        reason_if_blocked = f"{unsupported_patient_impacting} unsupported patient-impacting criteria remain"
    elif has_exclusion_parse_gap:
        reason_if_blocked = "exclusion criteria still contain unresolved deterministic gaps"
    elif manual_patient_impacting:
        runtime_state = "runtime_ready_with_manual"
        runnable = True
    else:
        runtime_state = "runtime_ready_deterministic"
        runnable = True

    return {
        "runnable": runnable,
        "runtimeState": runtime_state,
        "reasonIfBlocked": reason_if_blocked,
        "deterministicPatientImpactingCount": deterministic_supported,
        "manualPatientImpactingCount": manual_patient_impacting,
        "unsupportedPatientImpactingCount": unsupported_patient_impacting,
        "unresolvedPatientImpactingCount": unresolved_patient_impacting,
        "coveragePct": coverage_pct,
        "hasExclusionParseGap": has_exclusion_parse_gap,
    }


def _build_projection_snapshots(compiled_nodes: list[dict]) -> tuple[dict, dict, int, str]:
    now = utcnow()
    summary = summarize_criteria(compiled_nodes)
    leaf_nodes = max(1, int(summary.get("leaf_nodes") or 0))
    pending_count = sum(1 for node in compiled_nodes if ((node.get("review") or {}).get("status") == "pending"))
    reviewed_count = sum(1 for node in compiled_nodes if ((node.get("review") or {}).get("status") == "reviewed"))
    not_needed_count = sum(1 for node in compiled_nodes if ((node.get("review") or {}).get("status") == "not_needed"))
    auto_compile_count = sum(1 for node in compiled_nodes if ((node.get("compiler") or {}).get("parse_status") == "auto_compiled"))
    coverage_snapshot = {
        "total_leaf_nodes": int(summary.get("leaf_nodes") or 0),
        "deterministic_rules": int(summary.get("deterministic_rules") or 0),
        "pending_review_count": pending_count,
        "reviewed_count": reviewed_count,
        "auto_compile_count": auto_compile_count,
        "auto_compile_rate": round(auto_compile_count / leaf_nodes, 4),
        "updated_at": now,
    }
    review_snapshot = {
        "pending_count": pending_count,
        "reviewed_count": reviewed_count,
        "not_needed_count": not_needed_count,
        "updated_at": now,
    }
    return coverage_snapshot, review_snapshot, pending_count, ("ready_with_review" if pending_count else "ready_auto")


SECTION_ROOT_OPERATORS = {
    "inclusion": "ALL OF",
    "exclusion": "NONE OF",
}


def _source_ref(node: dict) -> str:
    source_kind = str(node.get("source_kind") or "")
    if source_kind == "structured_field":
        token = str((node.get("path") or [None, None])[-1] or "").strip()
        return f"STRUCT-{token}" if token else "STRUCT"
    section_code = "EXC" if str(node.get("section") or "").strip().lower() == "exclusion" else "INC"
    ordinal = int(node.get("ordinal") or 0)
    return f"{section_code}-{ordinal:03d}" if ordinal else section_code


def _projection_compiler_state(node: dict) -> str:
    review = node.get("review") or {}
    compiler = node.get("compiler") or {}
    parse_status = str(compiler.get("parse_status") or "").strip().lower()
    if str(review.get("status") or "").strip().lower() == "reviewed":
        return "reviewed"
    if parse_status == "auto_compiled":
        return "auto_compiled"
    if parse_status == "needs_review":
        has_candidates = bool((compiler.get("candidate_families") or []) or compiler.get("selected_family_id"))
        return "partial_parse" if has_candidates else "source_only"
    if parse_status == "context_only":
        return "source_only"
    return "source_only"


def _projection_node_type(node: dict) -> str:
    node_type = str(node.get("node_type") or "").strip()
    if node_type == "group_header":
        return "group"
    if node_type == "synthetic_structured":
        return "synthetic"
    if node_type == "note":
        return "note"
    if get_effective_computability(node) == "not_patient_rule":
        return "context"
    return "predicate"


def _projection_logical_operator(node: dict) -> str:
    projection_type = _projection_node_type(node)
    if projection_type == "group":
        return "CONTEXT"
    if projection_type == "note":
        return "SOURCE ONLY"
    if projection_type == "context":
        return "CONTEXT"
    return "PREDICATE"


def _matchability_class(node: dict, family_doc: dict | None) -> str:
    computability = get_effective_computability(node)
    patient_support = (family_doc or {}).get("patientSupport") or {}
    if computability == "not_patient_rule":
        return "not_patient_rule"
    if computability == "deterministic":
        return "deterministic" if bool(patient_support.get("supported")) else "unsupported"
    return "manual"


def _canonical_required_patient_features(node: dict, family_doc: dict | None, compiled_rule: dict | None) -> list[str]:
    compiled_features = list((compiled_rule or {}).get("required_patient_features") or [])
    if compiled_features:
        return compiled_features
    compiler_contract = (family_doc or {}).get("compiler_contract") or {}
    return list(compiler_contract.get("required_patient_features") or [])


def build_trial_canonical_tree_payload(
    trial_doc: dict,
    criteria_rows: list[dict],
    *,
    family_map: dict[str, dict] | None = None,
) -> dict:
    active_family_map = family_map or {}
    section_roots = {
        section: {
            "nodeId": f"root::{section}",
            "parentNodeId": None,
            "section": section,
            "nodeType": "section_root",
            "sourceKind": "section_root",
            "sourceOrdinal": 0,
            "sourceRef": section.upper(),
            "depth": 0,
            "path": [section.upper()],
            "label": "Inclusion" if section == "inclusion" else "Exclusion",
            "source": {
                "text": "Inclusion Criteria" if section == "inclusion" else "Exclusion Criteria",
                "normalizedText": None,
                "sourcePath": None,
                "ancestorHeaders": [],
            },
            "logic": {
                "logicalOperator": SECTION_ROOT_OPERATORS.get(section),
                "predicate": None,
                "comparator": None,
                "value": None,
            },
            "compiler": {
                "state": "source_only",
                "parseStatus": None,
                "computability": None,
                "familyId": None,
                "familyVersion": None,
                "confidence": None,
            },
            "matchability": {
                "class": "section_root",
                "patientImpacting": False,
                "questionnaireFillable": False,
                "prefilterEligible": False,
                "supported": False,
                "runtimeBlocker": False,
            },
            "patientSupport": {
                "evidencePaths": [],
                "requiredPatientFeatures": [],
                "blockedByLinkageConflict": False,
                "evaluator": None,
            },
            "review": {
                "status": None,
                "decision": None,
            },
            "children": [],
        }
        for section in ["inclusion", "exclusion"]
    }

    source_summary = {
        "sourceBullets": 0,
        "groupHeaders": 0,
        "notes": 0,
        "structuredCriteria": 0,
    }
    canonical_summary = {
        "executablePredicates": 0,
        "deterministic": 0,
        "manualReview": 0,
        "unsupported": 0,
        "notPatientRules": 0,
        "prefilterEligible": 0,
        "runtimeReady": bool(((trial_doc or {}).get("runtimeReadiness") or {}).get("runnable")),
    }

    ordered_rows = sorted(
        list(criteria_rows or []),
        key=lambda row: (
            0 if str(row.get("section") or "").strip().lower() == "inclusion" else 1,
            int(row.get("ordinal") or 0),
            str(row.get("_id") or ""),
        ),
    )
    projected_by_id: dict[str, dict] = {}
    projected_order: list[dict] = []

    for row in ordered_rows:
        source_kind = str(row.get("source_kind") or "").strip()
        node_type = str(row.get("node_type") or "").strip()
        section = str(row.get("section") or "inclusion").strip().lower() or "inclusion"

        if source_kind == "structured_field":
            source_summary["structuredCriteria"] += 1
        elif node_type == "group_header":
            source_summary["groupHeaders"] += 1
        elif node_type == "note":
            source_summary["notes"] += 1
        else:
            source_summary["sourceBullets"] += 1

        compiled_rule = get_effective_compiled_rule(row) or {}
        family_id = str(
            compiled_rule.get("family")
            or ((row.get("review") or {}).get("final_family_id"))
            or ((row.get("compiler") or {}).get("selected_family_id"))
            or ""
        ).strip()
        family_doc = active_family_map.get(family_id) if family_id else None
        patient_support = (family_doc or {}).get("patientSupport") or {}
        compiler = row.get("compiler") or {}
        review = row.get("review") or {}
        matchability_class = _matchability_class(row, family_doc)
        patient_impacting = (
            _projection_node_type(row) in {"predicate", "synthetic", "context"}
            and matchability_class != "not_patient_rule"
        )
        prefilter_eligible = bool(
            (compiled_rule.get("prefilter_eligible"))
            or (((family_doc or {}).get("compiler_contract") or {}).get("prefilter_eligible"))
        )
        required_features = _canonical_required_patient_features(row, family_doc, compiled_rule)

        if _projection_node_type(row) in {"predicate", "synthetic"} and matchability_class != "not_patient_rule":
            canonical_summary["executablePredicates"] += 1
        if matchability_class == "deterministic":
            canonical_summary["deterministic"] += 1
        elif matchability_class == "manual":
            canonical_summary["manualReview"] += 1
        elif matchability_class == "unsupported":
            canonical_summary["unsupported"] += 1
        elif matchability_class == "not_patient_rule":
            canonical_summary["notPatientRules"] += 1
        if prefilter_eligible:
            canonical_summary["prefilterEligible"] += 1

        logic = compiled_rule.get("logic") or {}
        projected = {
            "nodeId": row.get("_id"),
            "parentNodeId": row.get("parent_node_id"),
            "section": section,
            "nodeType": _projection_node_type(row),
            "sourceKind": source_kind,
            "sourceOrdinal": int(row.get("ordinal") or 0),
            "sourceRef": _source_ref(row),
            "depth": int(row.get("depth") or 0),
            "path": list(row.get("path") or []),
            "label": (row.get("display_label") or ((row.get("raw") or {}).get("text")) or ""),
            "source": {
                "text": ((row.get("raw") or {}).get("text")),
                "normalizedText": ((row.get("normalized") or {}).get("text")),
                "templateText": ((row.get("normalized") or {}).get("template_text")),
                "sourcePath": ((row.get("raw") or {}).get("source_path")),
                "ancestorHeaders": list(row.get("ancestor_headers") or []),
            },
            "logic": {
                "logicalOperator": _projection_logical_operator(row),
                "predicate": logic.get("predicate"),
                "comparator": logic.get("operator"),
                "value": logic.get("value"),
            },
            "compiler": {
                "state": _projection_compiler_state(row),
                "parseStatus": compiler.get("parse_status"),
                "computability": get_effective_computability(row),
                "familyId": family_id or None,
                "familyVersion": compiler.get("selected_family_version"),
                "confidence": compiler.get("confidence"),
            },
            "matchability": {
                "class": matchability_class,
                "patientImpacting": bool(patient_impacting),
                "questionnaireFillable": bool(patient_support.get("questionnaireFillable")),
                "prefilterEligible": prefilter_eligible,
                "supported": bool(patient_support.get("supported")),
                "runtimeBlocker": matchability_class in {"manual", "unsupported"},
            },
            "patientSupport": {
                "evidencePaths": list(patient_support.get("evidencePaths") or []),
                "requiredPatientFeatures": required_features,
                "blockedByLinkageConflict": bool(patient_support.get("blockOnLinkageConflict")),
                "evaluator": patient_support.get("evaluator"),
            },
            "review": {
                "status": review.get("status"),
                "decision": review.get("decision"),
                "reviewer": review.get("reviewer"),
            },
            "children": [],
        }
        projected_by_id[str(projected["nodeId"])] = projected
        projected_order.append(projected)

    for node in projected_order:
        parent_id = str(node.get("parentNodeId") or "").strip()
        if parent_id and parent_id in projected_by_id:
            projected_by_id[parent_id]["children"].append(node)
        else:
            section_roots[node["section"]]["children"].append(node)

    roots = [section_roots["inclusion"], section_roots["exclusion"]]
    return serialize_document(
        {
            "trialId": (trial_doc or {}).get("_id") or (trial_doc or {}).get("nct_id"),
            "versionId": (trial_doc or {}).get("current_version_id"),
            "runtimeReadiness": (trial_doc or {}).get("runtimeReadiness") or {},
            "sourceSummary": source_summary,
            "canonicalSummary": canonical_summary,
            "roots": roots,
        }
    )


def _initial_run_document(run_id: str, payload: dict, *, run_type: str) -> dict:
    now = utcnow()
    return {
        "_id": run_id,
        "run_type": run_type,
        "trigger": payload.get("trigger") or "manual_demo",
        "scope": _run_scope_from_payload(payload),
        "status": "queued",
        "started_at": now,
        "finished_at": None,
        "stages": [
            {
                "name": stage_name,
                "status": "pending",
                "started_at": None,
                "finished_at": None,
                "metrics": {},
            }
            for stage_name in RUN_STAGE_NAMES.get(run_type, RUN_STAGE_NAMES["ingest_trial"])
        ],
        "artifacts": {
            "trial_id": payload.get("nct_id"),
            "version_id": None,
            "trial_ids": list(payload.get("trial_ids") or []),
        },
        "metrics": {
            "changed_criteria_count": 0,
        },
        "recompile_reason": payload.get("recompile_reason") or payload.get("reason"),
        "events_tail": [],
        "error": None,
    }


class ClinicalTrialsIngestService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = ClinicalTrialsGovClient()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="clinical-trials-ingest")

    def _build_trial_list_query(
        self,
        *,
        compile_status: str | None = None,
        phase: str | None = None,
        overall_status: str | None = None,
        disease_area: str | None = None,
        search: str | None = None,
        runnable: bool | None = None,
        stage_target_window: str | None = None,
    ) -> dict:
        query: dict[str, Any] = {}
        if compile_status:
            query["compile.status"] = compile_status
        if phase:
            query["phase"] = phase
        if overall_status:
            query["status"] = overall_status
        if disease_area:
            query["disease_area"] = disease_area
        if runnable is not None:
            query["runtimeReadiness.runnable"] = bool(runnable)
        effective_stage_windows = _effective_stage_target_windows(stage_target_window)
        if effective_stage_windows:
            query["stage_relevance.targetWindow"] = {"$in": effective_stage_windows}
        if search:
            search_text = str(search).strip()
            if search_text:
                query["$or"] = [
                    {"_id": {"$regex": search_text, "$options": "i"}},
                    {"nct_id": {"$regex": search_text, "$options": "i"}},
                    {"title": {"$regex": search_text, "$options": "i"}},
                    {"brief_title": {"$regex": search_text, "$options": "i"}},
                    {"conditions": {"$elemMatch": {"$regex": search_text, "$options": "i"}}},
                ]
        return query

    def count_trials(
        self,
        *,
        compile_status: str | None = None,
        phase: str | None = None,
        overall_status: str | None = None,
        disease_area: str | None = None,
        search: str | None = None,
        runnable: bool | None = None,
        stage_target_window: str | None = None,
    ) -> int:
        query = self._build_trial_list_query(
            compile_status=compile_status,
            phase=phase,
            overall_status=overall_status,
            disease_area=disease_area,
            search=search,
            runnable=runnable,
            stage_target_window=stage_target_window,
        )
        return collections.trials.count_documents(query)

    def list_trials(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        compile_status: str | None = None,
        phase: str | None = None,
        overall_status: str | None = None,
        disease_area: str | None = None,
        search: str | None = None,
        runnable: bool | None = None,
        stage_target_window: str | None = None,
    ) -> list[dict]:
        query = self._build_trial_list_query(
            compile_status=compile_status,
            phase=phase,
            overall_status=overall_status,
            disease_area=disease_area,
            search=search,
            runnable=runnable,
            stage_target_window=stage_target_window,
        )
        cursor = collections.trials.find(query).sort([("display.last_ingested_at", -1)]).skip(max(0, int(offset or 0))).limit(limit)
        return [serialize_document(row) for row in cursor]

    def get_trial(self, trial_id: str) -> dict | None:
        row = collections.trials.find_one({"_id": trial_id})
        return serialize_document(row) if row else None

    def get_trial_criteria(self, trial_id: str, *, version_id: str | None = None) -> list[dict]:
        trial = collections.trials.find_one({"_id": trial_id}, {"current_version_id": 1})
        resolved_version_id = version_id or ((trial or {}).get("current_version_id"))
        if not resolved_version_id:
            return []
        cursor = collections.trial_criteria.find(
            {"trial_id": trial_id, "version_id": resolved_version_id}
        ).sort([("section", 1), ("ordinal", 1)])
        return [serialize_document(row) for row in cursor]

    def get_trial_canonical_tree(self, trial_id: str, *, version_id: str | None = None) -> dict | None:
        trial = self.get_trial(trial_id)
        if not trial:
            return None
        criteria = self.get_trial_criteria(trial_id, version_id=version_id)
        family_map = _latest_rule_family_map()
        return build_trial_canonical_tree_payload(trial, criteria, family_map=family_map)

    def compile_playground_rule(self, payload: dict) -> dict:
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValueError("text is required")

        section = str(payload.get("section") or "inclusion").strip().lower() or "inclusion"
        if section not in {"inclusion", "exclusion"}:
            section = "inclusion"

        ancestor_headers = [
            str(item).strip()
            for item in (payload.get("ancestor_headers") or payload.get("ancestorHeaders") or [])
            if str(item).strip()
        ]
        version_id = "PLAYGROUND::v1"
        criterion_id = f"PLAYGROUND::{stable_json_hash({'text': text, 'section': section, 'ancestor_headers': ancestor_headers})[:12]}"
        node = {
            "_id": criterion_id,
            "trial_id": "PLAYGROUND",
            "version_id": version_id,
            "section": section,
            "node_type": "leaf",
            "source_kind": "markdown_leaf",
            "ordinal": 1,
            "depth": 1,
            "path": ["EXC" if section == "exclusion" else "INC", "001"],
            "parent_node_id": None,
            "ancestor_headers": ancestor_headers,
            "raw": {
                "text": text,
                "source_path": "playground.text",
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text(text),
            "compiler": {},
            "review": {},
            "audit": {},
        }

        active_families = list_active_rule_families()
        family_map = _latest_rule_family_map()
        compiled_node = compile_criterion_node(node, active_families)
        compiled_rule = get_effective_compiled_rule(compiled_node) or {}
        family_id = str(
            compiled_rule.get("family")
            or ((compiled_node.get("review") or {}).get("final_family_id"))
            or ((compiled_node.get("compiler") or {}).get("selected_family_id"))
            or ""
        ).strip()
        family_doc = family_map.get(family_id) or {}
        patient_support = (family_doc.get("patientSupport") or {})
        runtime_readiness = _build_runtime_readiness([compiled_node])
        canonical_tree = build_trial_canonical_tree_payload(
            {
                "_id": "PLAYGROUND",
                "current_version_id": version_id,
                "runtimeReadiness": runtime_readiness,
            },
            [compiled_node],
            family_map=family_map,
        )
        logic = compiled_rule.get("logic") or {}
        family_shortlist = [
            {
                "familyId": candidate.get("family_id"),
                "score": candidate.get("score"),
                "validatorPassed": candidate.get("validator_passed"),
                "leftoverTokens": list(candidate.get("leftover_tokens") or []),
            }
            for candidate in ((compiled_node.get("compiler") or {}).get("candidate_families") or [])
        ]

        return serialize_document(
            {
                "mode": "playground",
                "noDatabaseWrite": True,
                "rawText": text,
                "section": section,
                "normalizedText": ((compiled_node.get("normalized") or {}).get("text")) or normalize_token(text),
                "familyShortlist": family_shortlist,
                "selectedFamily": family_id or None,
                "confidence": ((compiled_node.get("compiler") or {}).get("confidence")),
                "compilerState": _projection_compiler_state(compiled_node),
                "parseStatus": ((compiled_node.get("compiler") or {}).get("parse_status")),
                "computability": get_effective_computability(compiled_node),
                "matchability": _matchability_class(compiled_node, family_doc),
                "patientImpacting": _projection_node_type(compiled_node) in {"predicate", "synthetic"},
                "prefilterEligible": bool(compiled_rule.get("prefilter_eligible")),
                "selectedEvaluator": patient_support.get("evaluator"),
                "evidencePaths": list(patient_support.get("evidencePaths") or []),
                "requiredPatientFeatures": _canonical_required_patient_features(compiled_node, family_doc, compiled_rule),
                "reviewStatus": ((compiled_node.get("review") or {}).get("status")),
                "predicate": logic.get("predicate"),
                "comparator": logic.get("operator"),
                "value": logic.get("value"),
                "compiledRule": compiled_rule or None,
                "canonicalTree": canonical_tree,
                "compiledNode": compiled_node,
            }
        )

    def get_run(self, run_id: str) -> dict | None:
        row = collections.ingest_runs.find_one({"_id": run_id})
        return serialize_document(row) if row else None

    def list_runs(self, *, limit: int = 20) -> list[dict]:
        cursor = collections.ingest_runs.find().sort([("started_at", -1)]).limit(limit)
        return [serialize_document(row) for row in cursor]

    def list_rule_families(self) -> list[dict]:
        cursor = collections.rule_families.find().sort([("status", 1), ("family_id", 1), ("version", -1)])
        return [serialize_document(row) for row in cursor]

    def list_criteria(
        self,
        *,
        trial_id: str | None = None,
        version_id: str | None = None,
        parse_status: str | None = None,
        family_id: str | None = None,
        fingerprint: str | None = None,
        section: str | None = None,
        review_status: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        query: dict[str, Any] = {}
        if trial_id:
            query["trial_id"] = trial_id
        if version_id:
            query["version_id"] = version_id
        if parse_status:
            query["compiler.parse_status"] = parse_status
        if family_id:
            query["$or"] = [
                {"compiler.selected_family_id": family_id},
                {"compiler.candidate_families.family_id": family_id},
                {"review.final_family_id": family_id},
            ]
        if fingerprint:
            query["normalized.criterion_fingerprint"] = fingerprint
        if section:
            query["section"] = section
        if review_status:
            query["review.status"] = review_status
        cursor = collections.trial_criteria.find(query).sort([("analytics.patient_relevance_weight", -1), ("audit.updated_at", -1), ("ordinal", 1)]).limit(limit)
        return [serialize_document(row) for row in cursor]

    def get_review_queue(
        self,
        *,
        trial_id: str | None = None,
        family_id: str | None = None,
        fingerprint: str | None = None,
        section: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        return self.list_criteria(
            trial_id=trial_id,
            family_id=family_id,
            fingerprint=fingerprint,
            section=section,
            review_status="pending",
            limit=limit,
        )

    def get_unresolved_top(self, *, limit: int = 25) -> list[dict]:
        pipeline = [
            {
                "$match": {
                    "node_type": "leaf",
                    "review.status": "pending",
                }
            },
            {
                "$group": {
                    "_id": "$analytics.review_cluster_key",
                    "count": {"$sum": 1},
                    "sample_text": {"$first": "$raw.text"},
                    "section": {"$first": "$section"},
                    "candidate_family_ids": {"$addToSet": "$compiler.selected_family_id"},
                    "trial_ids": {"$addToSet": "$trial_id"},
                    "patient_relevance_weight": {"$max": "$analytics.patient_relevance_weight"},
                }
            },
            {"$sort": {"patient_relevance_weight": -1, "count": -1, "_id": 1}},
            {"$limit": max(1, min(int(limit), 100))},
        ]
        rows = list(collections.trial_criteria.aggregate(pipeline))
        payload = []
        for row in rows:
            payload.append(
                {
                    "fingerprint": row.get("_id"),
                    "count": int(row.get("count") or 0),
                    "sample_text": row.get("sample_text"),
                    "section": row.get("section"),
                    "candidate_family_ids": [item for item in (row.get("candidate_family_ids") or []) if item],
                    "trial_ids": row.get("trial_ids") or [],
                    "trial_count": len(row.get("trial_ids") or []),
                    "patient_relevance_weight": int(row.get("patient_relevance_weight") or 0),
                }
            )
        return serialize_document(payload)

    def get_family_coverage(self) -> list[dict]:
        cursor = collections.trial_criteria.find(
            {"node_type": {"$in": ["leaf", "synthetic_structured"]}},
            {
                "compiler.selected_family_id": 1,
                "compiler.parse_status": 1,
                "review.status": 1,
                "review.final_family_id": 1,
            },
        )
        families: dict[str, dict[str, Any]] = {}
        for row in cursor:
            serialized = serialize_document(row)
            review = serialized.get("review") or {}
            compiler = serialized.get("compiler") or {}
            family_id = review.get("final_family_id") or compiler.get("selected_family_id") or "unassigned"
            parse_status = "reviewed" if review.get("status") == "reviewed" else (compiler.get("parse_status") or "unknown")
            family_payload = families.setdefault(
                family_id,
                {
                    "family_id": family_id,
                    "total": 0,
                    "statuses": {},
                },
            )
            family_payload["total"] += 1
            family_payload["statuses"][parse_status] = int(family_payload["statuses"].get(parse_status) or 0) + 1
        return sorted(families.values(), key=lambda item: (-int(item.get("total") or 0), str(item.get("family_id") or "")))

    def start_ingest_run(self, payload: dict) -> dict:
        run_id = self._create_run(payload, run_type="ingest_trial")
        self.executor.submit(self._execute_ingest_run, run_id, payload)
        return {"run_id": run_id, "status": "queued"}

    def start_bulk_ingest_run(self, payload: dict) -> dict:
        run_id = self._create_run(payload, run_type="bulk_ingest_query")
        self.executor.submit(self._execute_bulk_ingest_run, run_id, payload)
        return {"run_id": run_id, "status": "queued"}

    def run_bulk_ingest_sync(self, payload: dict) -> dict:
        run_id = self._create_run(payload, run_type="bulk_ingest_query")
        self._execute_bulk_ingest_run(run_id, payload)
        return {"run_id": run_id, "run": self.get_run(run_id)}

    def start_recompile_trial_run(self, trial_id: str, payload: dict | None = None) -> dict:
        resolved_payload = {
            "nct_id": trial_id,
            "trigger": (payload or {}).get("trigger") or "review_recompile",
            "recompile_reason": (payload or {}).get("reason"),
        }
        run_id = self._create_run(resolved_payload, run_type="recompile_trial")
        self.executor.submit(self._execute_recompile_trial_run, run_id, trial_id, resolved_payload)
        return {"run_id": run_id, "status": "queued"}

    def run_recompile_trial_sync(self, trial_id: str, payload: dict | None = None) -> dict:
        resolved_payload = {
            "nct_id": trial_id,
            "trigger": (payload or {}).get("trigger") or "sync_recompile",
            "recompile_reason": (payload or {}).get("reason"),
        }
        run_id = self._create_run(resolved_payload, run_type="recompile_trial")
        self._execute_recompile_trial_run(run_id, trial_id, resolved_payload)
        return {"run_id": run_id, "run": self.get_run(run_id)}

    def start_recompile_family_run(self, family_id: str, payload: dict | None = None) -> dict:
        resolved_payload = {
            "family_id": family_id,
            "trigger": (payload or {}).get("trigger") or "family_recompile",
            "recompile_reason": (payload or {}).get("reason"),
        }
        run_id = self._create_run(resolved_payload, run_type="recompile_family")
        self.executor.submit(self._execute_recompile_family_run, run_id, family_id, resolved_payload)
        return {"run_id": run_id, "status": "queued"}

    def start_review_apply_run(self, payload: dict) -> dict:
        criterion = collections.trial_criteria.find_one({"_id": payload.get("criterion_id")}, {"trial_id": 1})
        resolved_payload = dict(payload)
        if criterion:
            resolved_payload["nct_id"] = criterion.get("trial_id")
        run_id = self._create_run(resolved_payload, run_type="review_apply")
        self.executor.submit(self._execute_review_apply_run, run_id, resolved_payload)
        return {"run_id": run_id, "status": "queued"}

    def _create_run(self, payload: dict, *, run_type: str) -> str:
        now = utcnow()
        run_id = f"run_{now.strftime('%Y%m%d_%H%M%S_%f')}"
        collections.ingest_runs.insert_one(_initial_run_document(run_id, payload, run_type=run_type))
        return run_id

    def _append_event(self, run_id: str, level: str, message: str) -> None:
        collections.ingest_runs.update_one(
            {"_id": run_id},
            {
                "$push": {
                    "events_tail": {
                        "$each": [
                            {
                                "ts": utcnow(),
                                "level": level,
                                "message": message,
                            }
                        ],
                        "$slice": -80,
                    }
                }
            },
        )

    def _update_stage(self, run_id: str, stage_name: str, *, status: str, metrics: dict | None = None) -> None:
        run_document = collections.ingest_runs.find_one({"_id": run_id})
        if not run_document:
            return
        stages = list(run_document.get("stages") or [])
        now = utcnow()
        for stage in stages:
            if stage.get("name") != stage_name:
                continue
            if status == "running" and not stage.get("started_at"):
                stage["started_at"] = now
            stage["status"] = status
            if metrics:
                stage["metrics"] = {**(stage.get("metrics") or {}), **metrics}
            if status in {"completed", "failed", "skipped"}:
                stage["finished_at"] = now
            break
        collections.ingest_runs.update_one({"_id": run_id}, {"$set": {"stages": stages}})

    def _set_run_scope(self, run_id: str, scope_updates: dict) -> None:
        update_payload = {f"scope.{key}": value for key, value in scope_updates.items()}
        if update_payload:
            collections.ingest_runs.update_one({"_id": run_id}, {"$set": update_payload})

    def _merge_run_metrics(self, run_id: str, metrics: dict) -> None:
        run_document = collections.ingest_runs.find_one({"_id": run_id}, {"metrics": 1})
        existing_metrics = (run_document or {}).get("metrics") or {}
        collections.ingest_runs.update_one({"_id": run_id}, {"$set": {"metrics": {**existing_metrics, **metrics}}})

    def _set_run_status(self, run_id: str, status: str, *, error: dict | None = None, artifacts: dict | None = None) -> None:
        update_payload: dict[str, Any] = {
            "status": status,
            "finished_at": utcnow() if status in TERMINAL_RUN_STATUSES else None,
        }
        if error is not None:
            update_payload["error"] = error
        if artifacts is not None:
            update_payload["artifacts"] = artifacts
        collections.ingest_runs.update_one({"_id": run_id}, {"$set": update_payload})

    def _mark_unstarted_stages_skipped(self, run_id: str) -> None:
        run_document = collections.ingest_runs.find_one({"_id": run_id})
        if not run_document:
            return
        stages = list(run_document.get("stages") or [])
        now = utcnow()
        for stage in stages:
            if stage.get("status") == "pending":
                stage["status"] = "skipped"
                stage["finished_at"] = now
        collections.ingest_runs.update_one({"_id": run_id}, {"$set": {"stages": stages}})

    def _fetch_and_version_trial(self, run_id: str, nct_id: str) -> dict:
        study_doc = self.client.fetch_study(nct_id)
        source_hash = stable_json_hash(study_doc)
        version_id = _trial_version_id(nct_id, source_hash)
        existing_version = collections.trial_versions.find_one({"nct_id": nct_id, "source_hash": source_hash})
        existing_trial = collections.trials.find_one({"_id": nct_id}, {"current_version_id": 1})
        criteria_count = collections.trial_criteria.count_documents({"trial_id": nct_id, "version_id": version_id})
        is_unchanged = bool(existing_version and ((existing_trial or {}).get("current_version_id") == version_id) and criteria_count > 0)

        if not existing_version:
            version_document = _build_trial_version_document(
                study_doc,
                nct_id=nct_id,
                source_hash=source_hash,
                run_id=run_id,
            )
            collections.trial_versions.insert_one(version_document)
            version_created = True
        else:
            version_created = False

        return {
            "study_doc": study_doc,
            "source_hash": source_hash,
            "version_id": version_id,
            "version_created": version_created,
            "is_unchanged": is_unchanged,
        }

    def _load_current_trial_source(self, trial_id: str) -> tuple[dict, dict]:
        trial = collections.trials.find_one({"_id": trial_id})
        if not trial:
            raise ValueError(f"Trial {trial_id} not found")
        version_id = trial.get("current_version_id")
        if not version_id:
            raise ValueError(f"Trial {trial_id} has no current version")
        version_doc = collections.trial_versions.find_one({"_id": version_id})
        if not version_doc:
            raise ValueError(f"Version {version_id} not found for trial {trial_id}")
        return trial, version_doc

    def _parse_trial_nodes(self, *, trial_id: str, version_id: str, study_doc: dict, trial_summary: dict) -> tuple[list[dict], dict]:
        markdown_nodes = parse_markdown_eligibility_tree(
            version_id=version_id,
            trial_id=trial_id,
            eligibility_text=(((study_doc.get("protocolSection") or {}).get("eligibilityModule") or {}).get("eligibilityCriteria")) or "",
        )
        synthetic_nodes = build_synthetic_structured_criteria(
            version_id=version_id,
            trial_id=trial_id,
            structured_eligibility=trial_summary["structured_eligibility"],
        )
        all_nodes = markdown_nodes + synthetic_nodes
        metrics = {
            "total_nodes": len(all_nodes),
            "leaf_nodes": sum(1 for node in all_nodes if node.get("node_type") in {"leaf", "synthetic_structured"}),
            "markdown_nodes": len(markdown_nodes),
            "structured_nodes": len(synthetic_nodes),
        }
        return all_nodes, metrics

    def _compile_trial_nodes(self, *, trial_id: str, version_id: str, all_nodes: list[dict]) -> tuple[list[dict], dict]:
        existing_rows = list(collections.trial_criteria.find({"trial_id": trial_id, "version_id": version_id}))
        existing_by_id = {row.get("_id"): row for row in existing_rows}
        active_families = list_active_rule_families()
        compiled_nodes = []
        now = utcnow()
        for node in all_nodes:
            previous_node = existing_by_id.get(node.get("_id"))
            compiled_node = compile_criterion_node(node, active_families, previous_node=previous_node)
            previous_audit = (previous_node or {}).get("audit") or {}
            compiled_node["audit"] = {
                "created_at": previous_audit.get("created_at") or now,
                "updated_at": now,
            }
            compiled_nodes.append(compiled_node)

        changed_criteria_count = _count_changed_criteria(compiled_nodes, existing_by_id)
        collections.trial_criteria.delete_many({"trial_id": trial_id, "version_id": version_id})
        if compiled_nodes:
            collections.trial_criteria.insert_many(compiled_nodes, ordered=True)

        compile_metrics = {
            "auto_compiled": sum(1 for node in compiled_nodes if (node.get("compiler") or {}).get("parse_status") == "auto_compiled"),
            "needs_review": sum(1 for node in compiled_nodes if (node.get("review") or {}).get("status") == "pending"),
            "reviewed": sum(1 for node in compiled_nodes if (node.get("review") or {}).get("status") == "reviewed"),
            "context_only": sum(1 for node in compiled_nodes if (node.get("compiler") or {}).get("parse_status") == "context_only"),
            "changed_criteria_count": changed_criteria_count,
        }
        return compiled_nodes, compile_metrics

    def _materialize_trial_projection(self, *, trial_id: str, trial_summary: dict, compiled_nodes: list[dict], run_id: str) -> dict:
        criteria_summary = summarize_criteria(compiled_nodes)
        match_contract = materialize_match_contract(trial_summary, compiled_nodes)
        stage_relevance = _build_stage_relevance(trial_summary, compiled_nodes, match_contract)
        coverage_snapshot, review_snapshot, review_pending_count, compile_status = _build_projection_snapshots(compiled_nodes)
        runtime_readiness = _build_runtime_readiness(compiled_nodes)
        materialized_trial = deepcopy(trial_summary)
        materialized_trial["criteria_summary"] = criteria_summary
        materialized_trial["match_contract"] = match_contract
        materialized_trial["stage_relevance"] = stage_relevance
        materialized_trial["coverage_snapshot"] = coverage_snapshot
        materialized_trial["review_snapshot"] = review_snapshot
        materialized_trial["runtimeReadiness"] = runtime_readiness
        materialized_trial.setdefault("compile", {})
        materialized_trial["compile"]["status"] = compile_status
        materialized_trial["compile"]["last_run_id"] = run_id
        materialized_trial["compile"]["review_pending_count"] = review_pending_count
        materialized_trial.setdefault("display", {})
        materialized_trial["display"]["last_ingested_at"] = utcnow()
        collections.trials.update_one(
            {"_id": trial_id},
            {"$set": materialized_trial},
            upsert=True,
        )
        return {
            "criteria_summary": criteria_summary,
            "match_contract": match_contract,
            "stage_relevance": stage_relevance,
            "coverage_snapshot": coverage_snapshot,
            "review_snapshot": review_snapshot,
            "runtime_readiness": runtime_readiness,
            "review_pending_count": review_pending_count,
            "compile_status": compile_status,
        }

    def _refresh_trial_projection(self, trial_id: str, run_id: str) -> dict:
        trial, version_doc = self._load_current_trial_source(trial_id)
        trial_summary = _build_trial_summary(
            version_doc.get("source_payload") or {},
            version_id=version_doc.get("_id"),
            run_id=run_id,
            existing_trial=trial,
            payload=(trial.get("corpus_membership") or [{}])[-1] if (trial.get("corpus_membership") or []) else {},
        )
        compiled_nodes = list(
            collections.trial_criteria.find({"trial_id": trial_id, "version_id": version_doc.get("_id")}).sort([("section", 1), ("ordinal", 1)])
        )
        return self._materialize_trial_projection(
            trial_id=trial_id,
            trial_summary=trial_summary,
            compiled_nodes=compiled_nodes,
            run_id=run_id,
        )

    def _touch_trial_on_skip(self, trial_id: str, payload: dict, run_id: str) -> None:
        trial = collections.trials.find_one({"_id": trial_id})
        if not trial:
            return
        collections.trials.update_one(
            {"_id": trial_id},
            {
                "$set": {
                    "compile.last_run_id": run_id,
                    "compile.family_registry_version": self.settings.family_registry_version,
                    "corpus_membership": _merge_corpus_membership(trial.get("corpus_membership"), payload),
                    "display.last_ingested_at": utcnow(),
                }
            },
        )

    def _execute_ingest_run(self, run_id: str, payload: dict) -> None:
        nct_id = str(payload.get("nct_id") or "").strip()
        if not nct_id:
            self._set_run_status(run_id, "failed", error={"message": "nct_id is required"})
            return

        try:
            self._set_run_status(run_id, "running")
            self._update_stage(run_id, "fetch_source", status="running")
            self._append_event(run_id, "info", f"Fetching ClinicalTrials.gov study {nct_id}")

            fetch_result = self._fetch_and_version_trial(run_id, nct_id)
            version_id = fetch_result["version_id"]
            source_hash = fetch_result["source_hash"]
            study_doc = fetch_result["study_doc"]
            self._merge_run_metrics(run_id, {"changed_criteria_count": 0})

            if fetch_result["is_unchanged"]:
                self._append_event(run_id, "info", f"Skipped unchanged study version {version_id}")
                self._update_stage(run_id, "fetch_source", status="skipped", metrics={"source_hash": source_hash})
                self._mark_unstarted_stages_skipped(run_id)
                self._touch_trial_on_skip(nct_id, payload, run_id)
                self._set_run_status(
                    run_id,
                    "skipped_unchanged",
                    artifacts={"trial_id": nct_id, "version_id": version_id},
                )
                return

            self._append_event(
                run_id,
                "info",
                f"{'Created' if fetch_result['version_created'] else 'Reused'} immutable source version {version_id}",
            )
            self._update_stage(run_id, "fetch_source", status="completed", metrics={"source_hash": source_hash})

            existing_trial = collections.trials.find_one({"_id": nct_id})
            trial_summary = _build_trial_summary(
                study_doc,
                version_id=version_id,
                run_id=run_id,
                existing_trial=existing_trial,
                payload=payload,
            )
            collections.trials.update_one({"_id": nct_id}, {"$set": trial_summary}, upsert=True)

            self._update_stage(run_id, "parse_structure", status="running")
            all_nodes, parse_metrics = self._parse_trial_nodes(
                trial_id=nct_id,
                version_id=version_id,
                study_doc=study_doc,
                trial_summary=trial_summary,
            )
            self._append_event(
                run_id,
                "info",
                f"Parsed {parse_metrics['markdown_nodes']} markdown nodes and {parse_metrics['structured_nodes']} structured nodes",
            )
            self._update_stage(run_id, "parse_structure", status="completed", metrics=parse_metrics)

            self._update_stage(run_id, "compile_criteria", status="running")
            compiled_nodes, compile_metrics = self._compile_trial_nodes(
                trial_id=nct_id,
                version_id=version_id,
                all_nodes=all_nodes,
            )
            self._merge_run_metrics(run_id, {"changed_criteria_count": compile_metrics["changed_criteria_count"]})
            self._append_event(
                run_id,
                "info",
                f"Compiled criteria: {compile_metrics['auto_compiled']} auto, {compile_metrics['needs_review']} pending review, {compile_metrics['reviewed']} reviewed carry-forward",
            )
            self._update_stage(run_id, "compile_criteria", status="completed", metrics=compile_metrics)

            self._update_stage(run_id, "materialize_match_contract", status="running")
            projection = self._materialize_trial_projection(
                trial_id=nct_id,
                trial_summary=trial_summary,
                compiled_nodes=compiled_nodes,
                run_id=run_id,
            )
            self._append_event(
                run_id,
                "info",
                f"Materialized match contract for {nct_id} with {len(projection['match_contract']['deterministic_criterion_ids'])} deterministic rules",
            )
            self._update_stage(
                run_id,
                "materialize_match_contract",
                status="completed",
                metrics={
                    "deterministic_rules": projection["criteria_summary"]["deterministic_rules"],
                    "review_pending_count": projection["review_pending_count"],
                },
            )
            self._set_run_status(
                run_id,
                "completed_with_review" if projection["review_pending_count"] else "completed",
                artifacts={"trial_id": nct_id, "version_id": version_id},
            )
        except Exception as exc:
            self._append_event(run_id, "error", f"Ingest failed: {exc}")
            self._mark_unstarted_stages_skipped(run_id)
            self._set_run_status(run_id, "failed", error={"message": str(exc)})

    def _execute_bulk_ingest_run(self, run_id: str, payload: dict) -> None:
        try:
            self._set_run_status(run_id, "running")
            self._update_stage(run_id, "search_remote", status="running")
            search_result = self.client.search_studies(
                condition=payload.get("condition"),
                phase=payload.get("phase"),
                sponsor=payload.get("sponsor"),
                nct_id=None,
                page_size=payload.get("page_size") or 25,
                max_pages=payload.get("max_pages") or 5,
                statuses=payload.get("statuses"),
            )
            studies = list(search_result.get("studies") or [])
            limit = payload.get("limit")
            if isinstance(limit, int):
                studies = studies[:limit]
            trial_ids = []
            for study in studies:
                nct_id = (((study.get("protocolSection") or {}).get("identificationModule") or {}).get("nctId"))
                if nct_id and nct_id not in trial_ids:
                    trial_ids.append(nct_id)

            self._set_run_scope(run_id, {"trial_ids": trial_ids, "query": _run_scope_from_payload(payload)["query"]})
            self._update_stage(
                run_id,
                "search_remote",
                status="completed",
                metrics={"trial_count": len(trial_ids), "pages_fetched": search_result.get("pagesFetched")},
            )

            self._update_stage(run_id, "ingest_trials", status="running")
            processed = 0
            skipped = 0
            failed = 0
            review_pending_trials = 0
            total_changed_criteria = 0
            processed_ids: list[str] = []

            for index, trial_id in enumerate(trial_ids, start=1):
                self._append_event(run_id, "info", f"[{index}/{len(trial_ids)}] Ingesting {trial_id}")
                try:
                    fetch_result = self._fetch_and_version_trial(run_id, trial_id)
                    version_id = fetch_result["version_id"]
                    if fetch_result["is_unchanged"]:
                        skipped += 1
                        self._touch_trial_on_skip(trial_id, payload, run_id)
                        self._append_event(run_id, "info", f"{trial_id} unchanged at {version_id}")
                    else:
                        existing_trial = collections.trials.find_one({"_id": trial_id})
                        trial_summary = _build_trial_summary(
                            fetch_result["study_doc"],
                            version_id=version_id,
                            run_id=run_id,
                            existing_trial=existing_trial,
                            payload=payload,
                        )
                        collections.trials.update_one({"_id": trial_id}, {"$set": trial_summary}, upsert=True)
                        all_nodes, _ = self._parse_trial_nodes(
                            trial_id=trial_id,
                            version_id=version_id,
                            study_doc=fetch_result["study_doc"],
                            trial_summary=trial_summary,
                        )
                        compiled_nodes, compile_metrics = self._compile_trial_nodes(
                            trial_id=trial_id,
                            version_id=version_id,
                            all_nodes=all_nodes,
                        )
                        projection = self._materialize_trial_projection(
                            trial_id=trial_id,
                            trial_summary=trial_summary,
                            compiled_nodes=compiled_nodes,
                            run_id=run_id,
                        )
                        total_changed_criteria += int(compile_metrics.get("changed_criteria_count") or 0)
                        if projection["review_pending_count"]:
                            review_pending_trials += 1
                        self._append_event(
                            run_id,
                            "info",
                            f"{trial_id} compiled with {projection['criteria_summary']['deterministic_rules']} deterministic rules and {projection['review_pending_count']} pending review",
                        )
                    processed += 1
                    processed_ids.append(trial_id)
                except Exception as exc:
                    failed += 1
                    self._append_event(run_id, "warning", f"{trial_id} failed during bulk ingest: {exc}")

                self._update_stage(
                    run_id,
                    "ingest_trials",
                    status="running",
                    metrics={
                        "processed_trials": processed,
                        "skipped_trials": skipped,
                        "failed_trials": failed,
                        "review_pending_trials": review_pending_trials,
                    },
                )

            self._merge_run_metrics(run_id, {"changed_criteria_count": total_changed_criteria})
            self._update_stage(
                run_id,
                "ingest_trials",
                status="completed",
                metrics={
                    "processed_trials": processed,
                    "skipped_trials": skipped,
                    "failed_trials": failed,
                    "review_pending_trials": review_pending_trials,
                    "changed_criteria_count": total_changed_criteria,
                },
            )

            self._update_stage(run_id, "summarize_corpus", status="running")
            ready_trials = collections.trials.count_documents({"compile.status": {"$in": ["ready_auto", "ready_with_review"]}})
            pending_reviews = collections.trial_criteria.count_documents({"review.status": "pending"})
            self._update_stage(
                run_id,
                "summarize_corpus",
                status="completed",
                metrics={
                    "ready_trials": ready_trials,
                    "pending_reviews": pending_reviews,
                },
            )
            self._set_run_status(
                run_id,
                "completed_with_review" if review_pending_trials else "completed",
                artifacts={"trial_ids": processed_ids},
            )
        except Exception as exc:
            self._append_event(run_id, "error", f"Bulk ingest failed: {exc}")
            self._mark_unstarted_stages_skipped(run_id)
            self._set_run_status(run_id, "failed", error={"message": str(exc)})

    def _execute_recompile_trial_run(self, run_id: str, trial_id: str, payload: dict) -> None:
        try:
            self._set_run_status(run_id, "running")
            self._update_stage(run_id, "load_version", status="running")
            trial, version_doc = self._load_current_trial_source(trial_id)
            version_id = version_doc.get("_id")
            study_doc = version_doc.get("source_payload") or {}
            self._append_event(run_id, "info", f"Loaded stored source version {version_id} for {trial_id}")
            self._update_stage(run_id, "load_version", status="completed", metrics={"version_id": version_id})

            trial_summary = _build_trial_summary(
                study_doc,
                version_id=version_id,
                run_id=run_id,
                existing_trial=trial,
                payload=payload,
            )

            self._update_stage(run_id, "parse_structure", status="running")
            all_nodes, parse_metrics = self._parse_trial_nodes(
                trial_id=trial_id,
                version_id=version_id,
                study_doc=study_doc,
                trial_summary=trial_summary,
            )
            self._update_stage(run_id, "parse_structure", status="completed", metrics=parse_metrics)

            self._update_stage(run_id, "compile_criteria", status="running")
            compiled_nodes, compile_metrics = self._compile_trial_nodes(
                trial_id=trial_id,
                version_id=version_id,
                all_nodes=all_nodes,
            )
            self._merge_run_metrics(run_id, {"changed_criteria_count": compile_metrics["changed_criteria_count"]})
            self._update_stage(run_id, "compile_criteria", status="completed", metrics=compile_metrics)

            self._update_stage(run_id, "materialize_match_contract", status="running")
            projection = self._materialize_trial_projection(
                trial_id=trial_id,
                trial_summary=trial_summary,
                compiled_nodes=compiled_nodes,
                run_id=run_id,
            )
            self._update_stage(
                run_id,
                "materialize_match_contract",
                status="completed",
                metrics={
                    "deterministic_rules": projection["criteria_summary"]["deterministic_rules"],
                    "review_pending_count": projection["review_pending_count"],
                },
            )
            self._set_run_status(
                run_id,
                "completed_with_review" if projection["review_pending_count"] else "completed",
                artifacts={"trial_id": trial_id, "version_id": version_id},
            )
        except Exception as exc:
            self._append_event(run_id, "error", f"Trial recompile failed: {exc}")
            self._mark_unstarted_stages_skipped(run_id)
            self._set_run_status(run_id, "failed", error={"message": str(exc)})

    def _execute_recompile_family_run(self, run_id: str, family_id: str, payload: dict) -> None:
        try:
            self._set_run_status(run_id, "running")
            self._update_stage(run_id, "select_trials", status="running")
            candidate_rows = collections.trial_criteria.find(
                {
                    "$or": [
                        {"compiler.selected_family_id": family_id},
                        {"compiler.candidate_families.family_id": family_id},
                        {"review.final_family_id": family_id},
                    ]
                },
                {"trial_id": 1, "version_id": 1},
            )
            current_versions = {
                row.get("_id"): row.get("current_version_id")
                for row in collections.trials.find({}, {"current_version_id": 1})
            }
            trial_ids = sorted(
                {
                    row.get("trial_id")
                    for row in candidate_rows
                    if row.get("trial_id") and current_versions.get(row.get("trial_id")) == row.get("version_id")
                }
            )
            self._set_run_scope(run_id, {"trial_ids": trial_ids, "family_id": family_id})
            self._update_stage(run_id, "select_trials", status="completed", metrics={"trial_count": len(trial_ids)})

            self._update_stage(run_id, "recompile_trials", status="running")
            total_changed = 0
            completed = 0
            failures = 0
            review_pending_trials = 0

            for index, trial_id in enumerate(trial_ids, start=1):
                try:
                    self._append_event(run_id, "info", f"[{index}/{len(trial_ids)}] Recompiling {trial_id} for family {family_id}")
                    trial, version_doc = self._load_current_trial_source(trial_id)
                    version_id = version_doc.get("_id")
                    trial_summary = _build_trial_summary(
                        version_doc.get("source_payload") or {},
                        version_id=version_id,
                        run_id=run_id,
                        existing_trial=trial,
                        payload=payload,
                    )
                    all_nodes, _ = self._parse_trial_nodes(
                        trial_id=trial_id,
                        version_id=version_id,
                        study_doc=version_doc.get("source_payload") or {},
                        trial_summary=trial_summary,
                    )
                    compiled_nodes, compile_metrics = self._compile_trial_nodes(
                        trial_id=trial_id,
                        version_id=version_id,
                        all_nodes=all_nodes,
                    )
                    projection = self._materialize_trial_projection(
                        trial_id=trial_id,
                        trial_summary=trial_summary,
                        compiled_nodes=compiled_nodes,
                        run_id=run_id,
                    )
                    total_changed += int(compile_metrics.get("changed_criteria_count") or 0)
                    completed += 1
                    if projection["review_pending_count"]:
                        review_pending_trials += 1
                except Exception as exc:
                    failures += 1
                    self._append_event(run_id, "warning", f"{trial_id} recompile failed: {exc}")
                self._update_stage(
                    run_id,
                    "recompile_trials",
                    status="running",
                    metrics={
                        "completed_trials": completed,
                        "failed_trials": failures,
                        "changed_criteria_count": total_changed,
                    },
                )

            self._merge_run_metrics(run_id, {"changed_criteria_count": total_changed})
            self._update_stage(
                run_id,
                "recompile_trials",
                status="completed",
                metrics={
                    "completed_trials": completed,
                    "failed_trials": failures,
                    "changed_criteria_count": total_changed,
                },
            )
            self._update_stage(run_id, "summarize_coverage", status="running")
            self._update_stage(
                run_id,
                "summarize_coverage",
                status="completed",
                metrics={
                    "family_id": family_id,
                    "trial_count": len(trial_ids),
                    "review_pending_trials": review_pending_trials,
                },
            )
            self._set_run_status(
                run_id,
                "completed_with_review" if review_pending_trials else "completed",
                artifacts={"trial_ids": trial_ids},
            )
        except Exception as exc:
            self._append_event(run_id, "error", f"Family recompile failed: {exc}")
            self._mark_unstarted_stages_skipped(run_id)
            self._set_run_status(run_id, "failed", error={"message": str(exc)})

    def _execute_review_apply_run(self, run_id: str, payload: dict) -> None:
        try:
            criterion_id = str(payload.get("criterion_id") or "").strip()
            if not criterion_id:
                raise ValueError("criterion_id is required")

            self._set_run_status(run_id, "running")
            self._update_stage(run_id, "apply_review", status="running")
            criterion = collections.trial_criteria.find_one({"_id": criterion_id})
            if not criterion:
                raise ValueError(f"Criterion {criterion_id} not found")

            decision = payload.get("decision")
            final_family_id = payload.get("final_family_id") or ((criterion.get("compiler") or {}).get("selected_family_id"))
            final_compiled_rule = payload.get("final_compiled_rule")
            if decision != "not_patient_rule" and not final_compiled_rule:
                final_compiled_rule = get_effective_compiled_rule(criterion)

            review_payload = {
                "status": "reviewed",
                "decision": decision,
                "final_family_id": final_family_id,
                "slot_overrides": payload.get("slot_overrides") or {},
                "final_compiled_rule": None if decision == "not_patient_rule" else final_compiled_rule,
                "reviewer": payload.get("reviewer"),
                "reviewed_at": utcnow(),
                "notes": payload.get("notes"),
                "resolved_at": utcnow(),
            }
            compiler_history = list((((criterion.get("compiler") or {}).get("history")) or []))
            compiler_history.append(
                {
                    "ts": utcnow(),
                    "source": "review_apply",
                    "parser_version": self.settings.parser_version,
                    "family_registry_version": self.settings.family_registry_version,
                    "parse_status": (criterion.get("compiler") or {}).get("parse_status"),
                    "computability": decision,
                    "selected_family_id": final_family_id,
                    "selected_family_version": (criterion.get("compiler") or {}).get("selected_family_version"),
                    "confidence": (criterion.get("compiler") or {}).get("confidence"),
                    "compiled_rule": deepcopy(review_payload["final_compiled_rule"]),
                    "validator_report": deepcopy((criterion.get("compiler") or {}).get("validator_report")),
                }
            )
            update_payload = {
                "review": review_payload,
                "compiler.history": compiler_history,
                "analytics.patient_relevance_weight": 0 if decision == "not_patient_rule" else ((criterion.get("analytics") or {}).get("patient_relevance_weight") or 3),
            }
            if final_family_id:
                update_payload["compiler.selected_family_id"] = final_family_id
            collections.trial_criteria.update_one({"_id": criterion_id}, {"$set": update_payload})

            self._append_event(run_id, "info", f"Applied review decision {decision} to {criterion_id}")
            self._update_stage(
                run_id,
                "apply_review",
                status="completed",
                metrics={"criterion_id": criterion_id, "decision": decision},
            )

            recompile_target = payload.get("recompile_target") or "trial"
            if recompile_target == "none":
                self._update_stage(run_id, "trigger_recompile", status="skipped")
                projection = self._refresh_trial_projection(criterion.get("trial_id"), run_id)
                self._set_run_status(
                    run_id,
                    "completed_with_review" if projection["review_pending_count"] else "completed",
                    artifacts={"trial_id": criterion.get("trial_id"), "criterion_id": criterion_id},
                )
                return

            self._update_stage(run_id, "trigger_recompile", status="running")
            if recompile_target == "trial":
                self._append_event(run_id, "info", f"Recompiling trial {criterion.get('trial_id')} after review apply")
                trial, version_doc = self._load_current_trial_source(criterion.get("trial_id"))
                version_id = version_doc.get("_id")
                trial_summary = _build_trial_summary(
                    version_doc.get("source_payload") or {},
                    version_id=version_id,
                    run_id=run_id,
                    existing_trial=trial,
                    payload={"nct_id": criterion.get("trial_id"), "recompile_reason": payload.get("recompile_reason")},
                )
                all_nodes, _ = self._parse_trial_nodes(
                    trial_id=criterion.get("trial_id"),
                    version_id=version_id,
                    study_doc=version_doc.get("source_payload") or {},
                    trial_summary=trial_summary,
                )
                compiled_nodes, compile_metrics = self._compile_trial_nodes(
                    trial_id=criterion.get("trial_id"),
                    version_id=version_id,
                    all_nodes=all_nodes,
                )
                projection = self._materialize_trial_projection(
                    trial_id=criterion.get("trial_id"),
                    trial_summary=trial_summary,
                    compiled_nodes=compiled_nodes,
                    run_id=run_id,
                )
                self._merge_run_metrics(run_id, {"changed_criteria_count": compile_metrics["changed_criteria_count"]})
                self._update_stage(
                    run_id,
                    "trigger_recompile",
                    status="completed",
                    metrics={
                        "trial_id": criterion.get("trial_id"),
                        "changed_criteria_count": compile_metrics["changed_criteria_count"],
                    },
                )
                self._set_run_status(
                    run_id,
                    "completed_with_review" if projection["review_pending_count"] else "completed",
                    artifacts={"trial_id": criterion.get("trial_id"), "criterion_id": criterion_id},
                )
                return

            family_id = final_family_id or ((criterion.get("compiler") or {}).get("selected_family_id"))
            if not family_id:
                raise ValueError("A family_id is required to trigger family recompile")

            self._append_event(run_id, "info", f"Recompiling family {family_id} after review apply")
            candidate_rows = collections.trial_criteria.find(
                {
                    "$or": [
                        {"compiler.selected_family_id": family_id},
                        {"compiler.candidate_families.family_id": family_id},
                        {"review.final_family_id": family_id},
                    ]
                },
                {"trial_id": 1, "version_id": 1},
            )
            current_versions = {
                row.get("_id"): row.get("current_version_id")
                for row in collections.trials.find({}, {"current_version_id": 1})
            }
            trial_ids = sorted(
                {
                    row.get("trial_id")
                    for row in candidate_rows
                    if row.get("trial_id") and current_versions.get(row.get("trial_id")) == row.get("version_id")
                }
            )
            total_changed = 0
            review_pending_trials = 0
            for trial_id in trial_ids:
                trial, version_doc = self._load_current_trial_source(trial_id)
                version_id = version_doc.get("_id")
                trial_summary = _build_trial_summary(
                    version_doc.get("source_payload") or {},
                    version_id=version_id,
                    run_id=run_id,
                    existing_trial=trial,
                    payload={"family_id": family_id, "recompile_reason": payload.get("recompile_reason")},
                )
                all_nodes, _ = self._parse_trial_nodes(
                    trial_id=trial_id,
                    version_id=version_id,
                    study_doc=version_doc.get("source_payload") or {},
                    trial_summary=trial_summary,
                )
                compiled_nodes, compile_metrics = self._compile_trial_nodes(
                    trial_id=trial_id,
                    version_id=version_id,
                    all_nodes=all_nodes,
                )
                projection = self._materialize_trial_projection(
                    trial_id=trial_id,
                    trial_summary=trial_summary,
                    compiled_nodes=compiled_nodes,
                    run_id=run_id,
                )
                total_changed += int(compile_metrics.get("changed_criteria_count") or 0)
                if projection["review_pending_count"]:
                    review_pending_trials += 1
            self._merge_run_metrics(run_id, {"changed_criteria_count": total_changed})
            self._update_stage(
                run_id,
                "trigger_recompile",
                status="completed",
                metrics={"family_id": family_id, "trial_count": len(trial_ids), "changed_criteria_count": total_changed},
            )
            self._set_run_status(
                run_id,
                "completed_with_review" if review_pending_trials else "completed",
                artifacts={"criterion_id": criterion_id, "trial_ids": trial_ids},
            )
        except Exception as exc:
            self._append_event(run_id, "error", f"Review apply failed: {exc}")
            self._mark_unstarted_stages_skipped(run_id)
            self._set_run_status(run_id, "failed", error={"message": str(exc)})


ingest_service = ClinicalTrialsIngestService()
