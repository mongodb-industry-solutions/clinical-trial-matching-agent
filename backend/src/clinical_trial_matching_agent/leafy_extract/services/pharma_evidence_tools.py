from collections import defaultdict
from datetime import datetime
import hashlib
import json
import re

from .patient_curation import build_patient_evidence_profile_v2

TRIAL_MATCHING_CONTRACT_VERSION = "trial_matching_contract_v2"


def create_pharma_evidence_tools(
    *,
    normalize_pharma_text_fn,
    parse_int_value_fn,
    extract_resource_event_datetime_fn,
    extract_codes_and_terms_fn,
    serialize_doc_fn,
    build_pharma_trial_rules_fn,
    parse_birads_values_from_text_fn,
    evaluate_numeric_window_fn,
    tokenize_rule_text_fn,
    parse_iso_datetime_fn,
    days_since_fn,
    normalize_patient_ref_fn,
    db_pharma_patient_facts_collection,
    db_synthea_resources_collection,
    pharma_rule_risk_paths,
    pharma_rule_medication_tokens,
    pharma_rule_condition_tokens,
):
    normalize_pharma_text = normalize_pharma_text_fn
    parse_int_value = parse_int_value_fn
    extract_resource_event_datetime = extract_resource_event_datetime_fn
    extract_codes_and_terms = extract_codes_and_terms_fn
    serialize_doc = serialize_doc_fn
    build_pharma_trial_rules = build_pharma_trial_rules_fn
    parse_birads_values_from_text = parse_birads_values_from_text_fn
    evaluate_numeric_window = evaluate_numeric_window_fn
    tokenize_rule_text = tokenize_rule_text_fn
    parse_iso_datetime = parse_iso_datetime_fn
    days_since = days_since_fn
    _normalize_patient_ref = normalize_patient_ref_fn
    db_patient_facts_collection = db_pharma_patient_facts_collection
    PHARMA_RULE_RISK_PATHS = pharma_rule_risk_paths
    PHARMA_RULE_MEDICATION_TOKENS = set(pharma_rule_medication_tokens or [])
    PHARMA_RULE_CONDITION_TOKENS = set(pharma_rule_condition_tokens or [])
    ONCOLOGY_SYSTEMIC_THERAPY_TOKENS = {
        "systemic therapy",
        "chemotherapy",
        "endocrine therapy",
        "hormonal therapy",
        "anti-her2",
        "immunotherapy",
        "cdk4/6",
        "trastuzumab",
        "tamoxifen",
        "anastrozole",
        "letrozole",
        "paclitaxel",
        "doxorubicin",
        "palbociclib",
        "ribociclib",
        "abemaciclib",
        "taxane",
        "anthracycline",
    }
    ONCOLOGY_LOCAL_THERAPY_TOKENS = {"surgery", "radiation", "radiation therapy"}
    LAB_CANONICAL_UNITS = {
        "bilirubin": "mg/dL",
        "creatinine": "mg/dL",
        "hemoglobin": "g/dL",
        "platelet": "10^3/uL",
        "anc": "10^3/uL",
        "ast": "U/L",
        "alt": "U/L",
        "viral_load": "copies/mL",
    }

    def to_nullable_float(value):
        try:
            return float(value)
        except Exception:
            return None

    def fact_payload_has_value(fact_payload):
        if not isinstance(fact_payload, dict):
            return False
        fact_value = fact_payload.get("value")
        if fact_value is None or fact_value == "":
            return False
        if isinstance(fact_value, (list, dict)) and len(fact_value) == 0:
            return False
        return True

    def _normalize_evidence_source_label(source_value):
        normalized = normalize_pharma_text(source_value or "")
        if normalized in {"synthea", "synthea-facts", "synthea facts"}:
            return "synthea_facts"
        if normalized in {"leafy", "leafy_context", "leafy context"}:
            return "leafy_context"
        if normalized in {"leafy_narrative", "leafy narrative"}:
            return "leafy_narrative"
        if normalized in {"synthetic_demo", "synthetic demo"}:
            return "synthetic_demo"
        if not normalized:
            return "unknown"
        return normalized.replace(" ", "_")

    def _resolve_evidence_sources(evidence_pointers, evidence_pack):
        facts_root = ((evidence_pack or {}).get("facts") or {})
        resolved = []
        for pointer in evidence_pointers or []:
            normalized_pointer = str(pointer or "").strip()
            if not normalized_pointer.startswith("facts."):
                continue
            pointer_parts = normalized_pointer.split(".")
            if len(pointer_parts) < 2:
                continue
            fact_key = pointer_parts[1]
            fact_payload = facts_root.get(fact_key) or {}
            source_value = _normalize_evidence_source_label(fact_payload.get("source"))
            if source_value == "unknown" and fact_key == "keyLabs":
                source_value = _normalize_evidence_source_label((facts_root.get("keyLabs") or {}).get("source"))
            resolved.append(source_value)
        resolved = sorted(list({item for item in resolved if item}))
        return resolved or ["unknown"]

    def _describe_manual_review_reason(status, evaluation_mode, missing_data):
        if status != "UNKNOWN":
            return ""
        normalized_mode = normalize_pharma_text(evaluation_mode or "")
        missing = [str(item).strip() for item in (missing_data or []) if str(item).strip()]
        if normalized_mode == "llm_adjudication_required":
            return "Narrative interpretation required"
        if normalized_mode == "llm_missing_citation":
            return "Narrative check missing citation"
        if normalized_mode == "parser_ambiguous_negation":
            return "Rule text is ambiguous"
        if normalized_mode == "parser_inconsistent":
            return "Equivalent rules conflict"
        if missing:
            return f"Missing {', '.join(missing[:3])}"
        return "Evidence incomplete"

    def _normalize_unit_token(unit_value):
        return normalize_pharma_text(str(unit_value or "")).replace("micro", "u").replace("μ", "u")

    def _normalize_lab_value(lab_key, numeric_value, unit_value):
        value = to_nullable_float(numeric_value)
        if value is None:
            return {"value": None, "unit": None}
        normalized_unit = _normalize_unit_token(unit_value)
        canonical_unit = LAB_CANONICAL_UNITS.get(lab_key)
        if lab_key == "bilirubin":
            if "umol" in normalized_unit:
                return {"value": round(value / 17.1, 4), "unit": canonical_unit}
            return {"value": value, "unit": canonical_unit}
        if lab_key == "creatinine":
            if "umol" in normalized_unit:
                return {"value": round(value / 88.4, 4), "unit": canonical_unit}
            return {"value": value, "unit": canonical_unit}
        if lab_key == "hemoglobin":
            if normalized_unit in {"g/l", "gm/l"}:
                return {"value": round(value / 10.0, 4), "unit": canonical_unit}
            return {"value": value, "unit": canonical_unit}
        if lab_key in {"platelet", "anc"}:
            if any(token in normalized_unit for token in ["10^3/ul", "x10^3/ul", "k/ul", "10^9/l", "x10^9/l"]):
                return {"value": value, "unit": canonical_unit}
            if any(token in normalized_unit for token in ["/ul", "/mm3", "cells/ul", "cell/ul"]):
                return {"value": round(value / 1000.0, 4), "unit": canonical_unit}
            if not normalized_unit and ((lab_key == "anc" and value >= 100) or (lab_key == "platelet" and value >= 1000)):
                return {"value": round(value / 1000.0, 4), "unit": canonical_unit}
            return {"value": value, "unit": canonical_unit}
        return {"value": value, "unit": canonical_unit or unit_value}

    def _extract_lab_target(normalized_text, rule):
        lab_aliases = {
            "bilirubin": ["bilirubin"],
            "ast": ["ast", "aspartate aminotransferase"],
            "alt": ["alt", "alanine aminotransferase"],
            "creatinine": ["creatinine"],
            "hemoglobin": ["hemoglobin", "haemoglobin"],
            "platelet": ["platelet"],
            "anc": ["absolute neutrophil", "anc", "neutrophil"],
            "viral_load": ["viral load", "hiv"],
        }
        target_lab_key = None
        for lab_key, aliases in lab_aliases.items():
            if any(alias in normalized_text for alias in aliases):
                target_lab_key = lab_key
                break
        uses_uln = bool(re.search(r"\b(?:x\s*)?uln\b|\bupper limit of normal\b", normalized_text))
        raw_value = rule.get("value")
        raw_value_max = rule.get("value_max")
        normalized_target = _normalize_lab_value(target_lab_key, raw_value, normalized_text) if target_lab_key else {"value": raw_value, "unit": None}
        normalized_target_max = _normalize_lab_value(target_lab_key, raw_value_max, normalized_text) if target_lab_key else {"value": raw_value_max, "unit": None}
        return {
            "labKey": target_lab_key,
            "operator": rule.get("operator"),
            "value": raw_value,
            "valueMax": raw_value_max,
            "normalizedValue": normalized_target.get("value"),
            "normalizedValueMax": normalized_target_max.get("value"),
            "canonicalUnit": normalized_target.get("unit") or normalized_target_max.get("unit"),
            "usesUln": uses_uln,
        }

    def _build_oncology_treatment_payload(signal_blob, normalized_terms):
        normalized_blob = normalize_pharma_text(signal_blob)
        terms = set()
        classes = set()
        line_hints = set()
        for token in sorted(ONCOLOGY_SYSTEMIC_THERAPY_TOKENS.union(ONCOLOGY_LOCAL_THERAPY_TOKENS)):
            if token in normalized_blob:
                terms.add(token)
        for term in normalized_terms:
            normalized_term = normalize_pharma_text(term)
            if not normalized_term:
                continue
            if any(token in normalized_term for token in ONCOLOGY_SYSTEMIC_THERAPY_TOKENS.union(ONCOLOGY_LOCAL_THERAPY_TOKENS)):
                terms.add(normalized_term)
        if any(token in normalized_blob for token in ["treatment naive", "treatment-naive", "untreated", "no prior systemic"]):
            line_hints.add("treatment_naive")
        if any(token in normalized_blob for token in ["first line", "1l", "frontline"]):
            line_hints.add("first_line")
        if any(token in normalized_blob for token in ["second line", "2l"]):
            line_hints.add("second_line")
        if any(token in normalized_blob for token in ["third line", "3l"]):
            line_hints.add("third_line")
        if any(token in normalized_blob for token in ["prior therapy", "prior systemic", "previously treated", "pretreated"]):
            line_hints.add("prior_therapy")
        for term in list(terms):
            if term in ONCOLOGY_SYSTEMIC_THERAPY_TOKENS or any(token in term for token in ONCOLOGY_SYSTEMIC_THERAPY_TOKENS):
                classes.add("systemic")
            if term in ONCOLOGY_LOCAL_THERAPY_TOKENS or any(token in term for token in ONCOLOGY_LOCAL_THERAPY_TOKENS):
                classes.add("local")
        prior_systemic = None
        if "treatment_naive" in line_hints and "systemic" not in classes:
            prior_systemic = False
        elif "systemic" in classes or "prior_therapy" in line_hints:
            prior_systemic = True
        return {
            "terms": sorted(list(terms))[:16],
            "classes": sorted(list(classes))[:6],
            "lineHints": sorted(list(line_hints))[:6],
            "priorSystemicTherapy": prior_systemic,
        }

    def _build_signal_blob(resource, normalized_terms):
        note_texts = []
        for note in resource.get("note") or []:
            if isinstance(note, dict) and note.get("text"):
                note_texts.append(str(note.get("text")))
        component_pairs = []
        for component in resource.get("component") or []:
            if not isinstance(component, dict):
                continue
            code_text = normalize_pharma_text(((component.get("code") or {}).get("text")) or "")
            value_text = normalize_pharma_text(
                ((component.get("valueCodeableConcept") or {}).get("text"))
                or component.get("valueString")
                or component.get("valueInteger")
                or component.get("valueBoolean")
                or ""
            )
            if code_text and value_text:
                component_pairs.append(f"{code_text} {value_text}")
        stage_parts = []
        stage_items = resource.get("stage") or []
        if isinstance(stage_items, dict):
            stage_items = [stage_items]
        for stage_item in stage_items if isinstance(stage_items, list) else []:
            if not isinstance(stage_item, dict):
                continue
            summary_text = ((stage_item.get("summary") or {}).get("text")) or ""
            type_text = ((stage_item.get("type") or {}).get("text")) or ""
            if summary_text:
                stage_parts.append(str(summary_text))
            if type_text:
                stage_parts.append(str(type_text))
        raw_parts = [
            " ".join(normalized_terms),
            re.sub(r"<[^>]+>", " ", str(((resource.get("text") or {}).get("div")) or "")),
            str(resource.get("conclusion") or ""),
            str(resource.get("description") or ""),
            str(resource.get("valueString") or ""),
            str(resource.get("valueInteger") if resource.get("valueInteger") is not None else ""),
            str(resource.get("valueBoolean") if resource.get("valueBoolean") is not None else ""),
            " ".join(component_pairs),
            " ".join(stage_parts),
            " ".join(note_texts),
        ]
        return normalize_pharma_text(" ".join([part for part in raw_parts if part]))

    def _extract_receptor_status_payload(signal_blob):
        payload = {"hr": None, "er": None, "pr": None, "her2": None, "matches": []}
        normalized_blob = normalize_pharma_text(signal_blob)
        if not normalized_blob:
            return payload
        receptor_patterns = {
            "hr": ["hr", "hormone receptor", "hormone-receptor", "estrogen+progesterone receptor"],
            "er": ["er", "estrogen receptor", "estrogen receptor ag", "estrogen+progesterone receptor"],
            "pr": ["pr", "progesterone receptor", "progesterone receptor ag", "estrogen+progesterone receptor"],
            "her2": ["her2", "her-2", "erbb2", "human epidermal growth factor receptor 2"],
        }
        status_tokens = ["positive", "negative", "low", "equivocal"]
        for receptor_key, receptor_aliases in receptor_patterns.items():
            for alias in receptor_aliases:
                alias_pattern = re.escape(alias)
                for status in status_tokens:
                    if (
                        f"{alias} {status}" in normalized_blob
                        or f"{status} {alias}" in normalized_blob
                        or re.search(rf"\b{alias_pattern}\b[^.;,\n]{{0,120}}\b{status}\b", normalized_blob)
                        or re.search(rf"\b{status}\b[^.;,\n]{{0,80}}\b{alias_pattern}\b", normalized_blob)
                    ):
                        payload[receptor_key] = status
                        payload["matches"].append(f"{receptor_key}:{status}")
                        break
                if payload[receptor_key]:
                    break
        grouped_match = re.search(
            r"\b(positive|negative|low|equivocal)\s+(?:status\s+)?for\s+([a-z0-9,\-\s]+)",
            normalized_blob,
        )
        if grouped_match:
            grouped_status = grouped_match.group(1)
            grouped_receptors = grouped_match.group(2)
            for receptor_key, receptor_aliases in receptor_patterns.items():
                if any(alias in grouped_receptors for alias in receptor_aliases):
                    payload[receptor_key] = payload[receptor_key] or grouped_status
                    payload["matches"].append(f"{receptor_key}:{grouped_status}")
        shorthand_patterns = [
            ("hr", r"\bhr\s*([+-])"),
            ("her2", r"\bher[\s-]?2\s*([+-])"),
            ("er", r"\ber\s*([+-])"),
            ("pr", r"\bpr\s*([+-])"),
        ]
        for receptor_key, pattern in shorthand_patterns:
            shorthand_match = re.search(pattern, normalized_blob)
            if shorthand_match:
                payload[receptor_key] = "positive" if shorthand_match.group(1) == "+" else "negative"
                payload["matches"].append(f"{receptor_key}:{payload[receptor_key]}")
        if any(marker in normalized_blob for marker in ["[presence]", "by immune stain", "by fish"]):
            global_statuses = [status for status in status_tokens if re.search(rf"\b{status}\b", normalized_blob)]
            if len(set(global_statuses)) == 1:
                global_status = global_statuses[0]
                for receptor_key, receptor_aliases in receptor_patterns.items():
                    if payload.get(receptor_key):
                        continue
                    if any(alias in normalized_blob for alias in receptor_aliases):
                        payload[receptor_key] = global_status
                        payload["matches"].append(f"{receptor_key}:{global_status}")
        if "triple negative" in normalized_blob:
            payload["hr"] = payload["hr"] or "negative"
            payload["er"] = payload["er"] or "negative"
            payload["pr"] = payload["pr"] or "negative"
            payload["her2"] = payload["her2"] or "negative"
            payload["matches"].append("triple_negative")
        if payload.get("hr") is None:
            if payload.get("er") == "positive" or payload.get("pr") == "positive":
                payload["hr"] = "positive"
                payload["matches"].append("hr:positive")
            elif payload.get("er") == "negative" and payload.get("pr") == "negative":
                payload["hr"] = "negative"
                payload["matches"].append("hr:negative")
        payload["matches"] = sorted(list(set(payload["matches"])))[:8]
        return payload

    def _extract_performance_status_payload(signal_blob, resource=None):
        normalized_blob = normalize_pharma_text(signal_blob)
        resource_payload = resource if isinstance(resource, dict) else {}
        if not normalized_blob:
            normalized_blob = ""
        match = re.search(r"ecog(?: performance status)?\s*(?:of|=|:)?\s*([0-4])", normalized_blob)
        if match:
            return {
                "scale": "ECOG",
                "value": int(match.group(1)),
                "matches": [f"ecog:{match.group(1)}"],
            }

        code_payload = resource_payload.get("code") or {}
        coding_values = []
        for coding in code_payload.get("coding") or []:
            if not isinstance(coding, dict):
                continue
            coding_values.extend(
                [
                    normalize_pharma_text(coding.get("code")),
                    normalize_pharma_text(coding.get("display")),
                    normalize_pharma_text(coding.get("system")),
                ]
            )
        code_blob = normalize_pharma_text(
            " ".join(
                [
                    normalized_blob,
                    code_payload.get("text") or "",
                    (resource_payload.get("method") or {}).get("text") or "",
                    " ".join([item for item in coding_values if item]),
                ]
            )
        )
        is_ecog_observation = (
            resource_payload.get("resourceType") == "Observation"
            and (
                "89247-1" in coding_values
                or "ecog" in code_blob
                or "performance status" in code_blob
            )
        )
        if not is_ecog_observation:
            return {"scale": "ECOG", "value": None, "matches": []}

        numeric_value = resource_payload.get("valueInteger")
        if numeric_value is None:
            numeric_value = ((resource_payload.get("valueQuantity") or {}).get("value"))
        if numeric_value is None and resource_payload.get("valueString"):
            numeric_match = re.search(r"\b([0-4])\b", str(resource_payload.get("valueString")))
            if numeric_match:
                numeric_value = int(numeric_match.group(1))
        try:
            numeric_value = int(numeric_value)
        except Exception:
            numeric_value = None
        if numeric_value is None or numeric_value < 0 or numeric_value > 4:
            return {"scale": "ECOG", "value": None, "matches": []}
        return {
            "scale": "ECOG",
            "value": numeric_value,
            "matches": [f"ecog:{numeric_value}", "ecog_observation"],
        }

    def _extract_stage_extent_payload(signal_blob):
        normalized_blob = normalize_pharma_text(signal_blob)
        payload = {
            "stage": None,
            "tnm": [],
            "metastatic": False,
            "locallyAdvanced": False,
            "matches": [],
        }
        if not normalized_blob:
            return payload
        stage_match = re.search(r"\bstage\s+([0-4ivx]+[a-c]?)\b", normalized_blob)
        if stage_match:
            payload["stage"] = f"stage {stage_match.group(1)}".lower()
            payload["matches"].append(payload["stage"])
        tnm_matches = sorted(list(set(re.findall(r"\b(?:t[0-4][a-c]?|n[0-3][a-c]?|m[0-1][a-c]?)\b", normalized_blob))))[:6]
        if tnm_matches:
            payload["tnm"] = [item.upper() for item in tnm_matches]
            payload["matches"].extend([item.lower() for item in tnm_matches])
        if any(token in normalized_blob for token in ["metastatic", "metastasis", "stage iv", "m1"]):
            payload["metastatic"] = True
            payload["matches"].append("metastatic")
        if any(token in normalized_blob for token in ["locally advanced", "inoperable", "unresectable"]):
            payload["locallyAdvanced"] = True
            payload["matches"].append("locally_advanced")
        payload["matches"] = sorted(list(set(payload["matches"])))[:8]
        return payload

    def _extract_oncology_treatment_terms(signal_blob, normalized_terms):
        normalized_blob = normalize_pharma_text(signal_blob)
        terms = []
        explicit_tokens = list(PHARMA_RULE_MEDICATION_TOKENS) + [
            "systemic therapy",
            "chemotherapy",
            "hormonal therapy",
            "radiation therapy",
            "taxane",
            "anthracycline",
            "line of therapy",
            "prior therapy",
            "treatment naive",
            "first line",
            "surgery",
        ]
        for token in explicit_tokens:
            if token in normalized_blob:
                terms.append(token)
        for term in normalized_terms:
            if any(token in term for token in explicit_tokens):
                terms.append(term)
        return sorted(list({normalize_pharma_text(item) for item in terms if normalize_pharma_text(item)}))[:16]

    def _extract_performance_status_target(normalized_text, rule):
        operator = rule.get("operator")
        value = rule.get("value")
        value_max = rule.get("value_max")
        if value is not None or value_max is not None or operator:
            return {
                "operator": operator or ("between" if value_max is not None else "<="),
                "value": value,
                "valueMax": value_max,
            }
        range_match = re.search(
            r"(?:ecog|performance status)[^0-4]{0,20}([0-4])\s*(?:or|/|-|to)\s*([0-4])",
            normalized_text,
        )
        if range_match:
            return {"operator": "between", "value": int(range_match.group(1)), "valueMax": int(range_match.group(2))}
        single_match = re.search(r"(?:ecog|performance status)[^0-4]{0,20}([0-4])", normalized_text)
        if single_match:
            return {"operator": "<=", "value": int(single_match.group(1)), "valueMax": None}
        return {"operator": None, "value": None, "valueMax": None}

    def _extract_receptor_targets(normalized_text):
        targets = {}
        receptor_patterns = {
            "hr": ["hr", "hormone receptor", "hormone-receptor", "estrogen+progesterone receptor"],
            "er": ["er", "estrogen receptor", "estrogen receptor ag", "estrogen+progesterone receptor"],
            "pr": ["pr", "progesterone receptor", "progesterone receptor ag", "estrogen+progesterone receptor"],
            "her2": ["her2", "her-2", "erbb2", "human epidermal growth factor receptor 2"],
        }
        status_tokens = ["positive", "negative", "low", "equivocal"]
        if "triple negative" in normalized_text:
            return {"hr": "negative", "er": "negative", "pr": "negative", "her2": "negative"}
        for receptor_key, receptor_aliases in receptor_patterns.items():
            for alias in receptor_aliases:
                for status in status_tokens:
                    if f"{alias} {status}" in normalized_text or f"{status} {alias}" in normalized_text:
                        targets[receptor_key] = status
                        break
                if targets.get(receptor_key):
                    break
        grouped_match = re.search(
            r"\b(positive|negative|low|equivocal)\s+(?:status\s+)?for\s+([a-z0-9,\-\s]+)",
            normalized_text,
        )
        if grouped_match:
            grouped_status = grouped_match.group(1)
            grouped_receptors = grouped_match.group(2)
            for receptor_key, receptor_aliases in receptor_patterns.items():
                if any(alias in grouped_receptors for alias in receptor_aliases):
                    targets[receptor_key] = targets.get(receptor_key) or grouped_status
        shorthand_patterns = [
            ("hr", r"\bhr\s*([+-])"),
            ("her2", r"\bher[\s-]?2\s*([+-])"),
            ("er", r"\ber\s*([+-])"),
            ("pr", r"\bpr\s*([+-])"),
        ]
        for receptor_key, pattern in shorthand_patterns:
            shorthand_match = re.search(pattern, normalized_text)
            if shorthand_match:
                targets[receptor_key] = targets.get(receptor_key) or (
                    "positive" if shorthand_match.group(1) == "+" else "negative"
                )
        return targets

    def _extract_stage_targets(normalized_text):
        stage_match = re.search(r"\bstage\s+([0-4ivx]+[a-c]?)\b", normalized_text)
        return {
            "stage": f"stage {stage_match.group(1)}".lower() if stage_match else None,
            "metastatic": any(token in normalized_text for token in ["metastatic", "metastasis", "stage iv", "m1"]),
            "locallyAdvanced": any(token in normalized_text for token in ["locally advanced", "inoperable", "unresectable"]),
        }

    def _extract_oncology_treatment_targets(normalized_text):
        explicit_tokens = list(PHARMA_RULE_MEDICATION_TOKENS) + [
            "systemic therapy",
            "chemotherapy",
            "hormonal therapy",
            "radiation therapy",
            "taxane",
            "anthracycline",
            "line of therapy",
            "prior therapy",
            "treatment naive",
            "first line",
            "surgery",
        ]
        return [token for token in explicit_tokens if token in normalized_text][:10]

    def build_text_overlap_score(query_tokens, candidate_tokens):
        if not query_tokens or not candidate_tokens:
            return 0.0
        left = set(query_tokens)
        right = set(candidate_tokens)
        if not left or not right:
            return 0.0
        shared = left.intersection(right)
        union = left.union(right)
        if not union:
            return 0.0
        return round(float(len(shared) / len(union)), 4)

    def build_leafy_note_snippets(note_text, source_label, date_value=None, max_snippets=8):
        snippets = []
        normalized_text = str(note_text or "").strip()
        if not normalized_text:
            return snippets
        key_terms = [
            "birads", "mass", "calcification", "density", "lcis", "dcis", "atypia",
            "family history", "genetic", "metformin", "tamoxifen", "mammogram",
        ]
        sentence_candidates = re.split(r"(?<=[\.\!\?])\s+", normalized_text)
        for sentence in sentence_candidates:
            clean_sentence = sentence.strip()
            if len(clean_sentence) < 20:
                continue
            normalized_sentence = normalize_pharma_text(clean_sentence)
            if not any(term in normalized_sentence for term in key_terms):
                continue
            snippets.append(
                {
                    "source": source_label,
                    "date": date_value,
                    "text": clean_sentence[:380],
                }
            )
            if len(snippets) >= max_snippets:
                break
        return snippets

    def build_patient_evidence_pack(
        patient_context,
        report_doc=None,
        appointment_doc=None,
        synthea_patient_ref=None,
        linkage_payload=None,
        synthea_resources_limit=240,
    ):
        context = patient_context if isinstance(patient_context, dict) else {}
        report = report_doc if isinstance(report_doc, dict) else {}
        appointment = appointment_doc if isinstance(appointment_doc, dict) else {}
        report_date = context.get("reportDate") or report.get("date")

        facts = {
            "age": {"value": context.get("age"), "source": "leafy"},
            "sex": {"value": normalize_pharma_text(context.get("sex") or ""), "source": "leafy"},
            "birthDate": {"value": context.get("birthDate"), "source": "leafy"},
            "biradsValues": {"value": sorted(list(set(context.get("biradsValues") or []))), "source": "leafy"},
            "imageCount": {"value": parse_int_value(context.get("numberImages"), 0, 0), "source": "leafy"},
            "keywords": {"value": sorted(list(set(context.get("keywords") or [])))[:20], "source": "leafy"},
            "latestBmi": {"value": None, "date": None, "source": "unknown"},
            "lastMammogramDate": {"value": report_date, "source": "leafy"},
            "activeMedications": {"value": [], "source": "unknown"},
            "medicationHistory": {"value": [], "source": "unknown"},
            "conditionHistory": {"value": [], "source": "unknown"},
            "procedureHistory": {"value": [], "source": "unknown"},
            "keyLabs": {"value": {}, "source": "unknown"},
            "riskSignals": {"value": [], "source": "unknown"},
            "oncologySignals": {"value": {}, "source": "unknown"},
            "receptorStatus": {"value": {}, "source": "unknown"},
            "performanceStatus": {"value": {}, "source": "unknown"},
            "stageOrExtent": {"value": {}, "source": "unknown"},
            "oncologyTreatmentHistory": {"value": {}, "source": "unknown"},
        }

        timeline = []
        snippets = []
        if report_date:
            timeline.append(
                {
                    "source": "leafy",
                    "type": "MammographyReport",
                    "date": report_date,
                    "label": "Leafy screening report",
                    "detail": context.get("conclusions") or report.get("conclusions") or "",
                }
            )
        snippets.extend(
            build_leafy_note_snippets(
                f"{context.get('clinicalNotes') or ''} {context.get('conclusions') or ''}",
                "leafy",
                report_date,
            )
        )

        leafy_signal_blob = normalize_pharma_text(
            " ".join(
                [
                    str(context.get("clinicalNotes") or ""),
                    str(context.get("conclusions") or ""),
                    str(report.get("conclusions") or ""),
                ]
            )
        )
        leafy_receptor_status = _extract_receptor_status_payload(leafy_signal_blob)
        if any(leafy_receptor_status.get(key) for key in ["hr", "er", "pr", "her2"]) or leafy_receptor_status.get("matches"):
            facts["receptorStatus"] = {
                "value": {
                    "hr": leafy_receptor_status.get("hr"),
                    "er": leafy_receptor_status.get("er"),
                    "pr": leafy_receptor_status.get("pr"),
                    "her2": leafy_receptor_status.get("her2"),
                    "matches": leafy_receptor_status.get("matches") or [],
                },
                "source": "leafy_narrative",
            }
        leafy_performance_status = _extract_performance_status_payload(leafy_signal_blob)
        if leafy_performance_status.get("value") is not None:
            facts["performanceStatus"] = {
                "value": leafy_performance_status,
                "source": "leafy_narrative",
            }
        leafy_stage_or_extent = _extract_stage_extent_payload(leafy_signal_blob)
        if (
            leafy_stage_or_extent.get("stage")
            or leafy_stage_or_extent.get("tnm")
            or leafy_stage_or_extent.get("metastatic")
            or leafy_stage_or_extent.get("locallyAdvanced")
        ):
            facts["stageOrExtent"] = {
                "value": leafy_stage_or_extent,
                "source": "leafy_narrative",
            }
        leafy_treatment_terms = _extract_oncology_treatment_terms(
            leafy_signal_blob,
            [normalize_pharma_text(item) for item in (context.get("keywords") or []) if normalize_pharma_text(item)],
        )
        if leafy_treatment_terms:
            facts["oncologyTreatmentHistory"] = {
                "value": {"terms": leafy_treatment_terms},
                "source": "leafy_narrative",
            }

        risk_signals = set()
        for snippet in snippets:
            normalized = normalize_pharma_text(snippet.get("text"))
            for path_keywords in PHARMA_RULE_RISK_PATHS.values():
                for term in path_keywords:
                    if term in normalized:
                        risk_signals.add(term)

        synthea_ref = _normalize_patient_ref(synthea_patient_ref)
        patient_facts_doc = None
        if synthea_ref:
            if db_patient_facts_collection is not None:
                try:
                    patient_facts_doc = db_patient_facts_collection.find_one(
                        {"patientRef": synthea_ref},
                        sort=[("generatedAt", -1)],
                    )
                except Exception:
                    patient_facts_doc = None

            fact_doc_facts = (patient_facts_doc or {}).get("facts") or {}
            for fact_key in [
                "age",
                "sex",
                "birthDate",
                "latestBmi",
                "lastMammogramDate",
                "activeMedications",
                "medicationHistory",
                "conditionHistory",
                "procedureHistory",
                "keyLabs",
                "riskSignals",
                "oncologySignals",
                "receptorStatus",
                "performanceStatus",
                "stageOrExtent",
                "oncologyTreatmentHistory",
            ]:
                incoming_payload = fact_doc_facts.get(fact_key)
                if not fact_payload_has_value(incoming_payload):
                    continue
                if fact_payload_has_value(facts.get(fact_key)):
                    continue
                facts[fact_key] = dict(incoming_payload)

            query = {
                "$or": [
                    {"compartments.patientRefs": synthea_ref},
                    {"resource.subject.reference": synthea_ref},
                    {"resource.patient.reference": synthea_ref},
                    {"resource.beneficiary.reference": synthea_ref},
                ]
            }
            projection = {
                "resource.resourceType": 1,
                "resource.status": 1,
                "resource.code": 1,
                "resource.type": 1,
                "resource.medicationCodeableConcept": 1,
                "resource.valueCodeableConcept": 1,
                "resource.valueQuantity": 1,
                "resource.valueString": 1,
                "resource.valueInteger": 1,
                "resource.valueBoolean": 1,
                "resource.text.div": 1,
                "resource.category": 1,
                "resource.interpretation": 1,
                "resource.bodySite": 1,
                "resource.method": 1,
                "resource.clinicalStatus": 1,
                "resource.verificationStatus": 1,
                "resource.specimen.type": 1,
                "resource.stage": 1,
                "resource.component": 1,
                "resource.effectiveDateTime": 1,
                "resource.recordedDate": 1,
                "resource.issued": 1,
                "resource.authoredOn": 1,
                "resource.onsetDateTime": 1,
                "resource.date": 1,
                "resource.performedPeriod": 1,
                "resource.period": 1,
                "resource.note": 1,
            }
            synthea_resources = list(
                db_synthea_resources_collection.find(query, projection).limit(max(1, min(int(synthea_resources_limit), 600)))
            )
            active_meds = set()
            med_history = set()
            condition_history = set()
            procedure_history = set()
            receptor_status_state = {"hr": None, "er": None, "pr": None, "her2": None, "matches": set()}
            performance_status_state = {"scale": "ECOG", "value": None, "date": None, "matches": set()}
            stage_or_extent_state = {
                "stage": None,
                "tnm": set(),
                "metastatic": False,
                "locallyAdvanced": False,
                "matches": set(),
            }
            oncology_treatment_terms = set()
            latest_bmi = {"value": None, "date": None}
            latest_mammo_date = parse_iso_datetime(report_date)
            labs = {}

            for row in synthea_resources:
                resource = row.get("resource") or {}
                resource_type = str(resource.get("resourceType") or "").strip()
                event_date = extract_resource_event_datetime(resource)
                event_date_serialized = serialize_doc(event_date) if event_date is not None else None
                extracted_codes, extracted_terms = extract_codes_and_terms(resource)
                normalized_terms = [normalize_pharma_text(item) for item in extracted_terms if normalize_pharma_text(item)]
                signal_blob = _build_signal_blob(resource, normalized_terms)

                if event_date is not None:
                    timeline.append(
                        {
                            "source": "synthea",
                            "type": resource_type,
                            "date": event_date_serialized,
                            "label": normalized_terms[0] if normalized_terms else resource_type,
                            "detail": ", ".join(normalized_terms[:4]),
                        }
                    )

                receptor_payload = _extract_receptor_status_payload(signal_blob)
                if any(receptor_payload.get(key) for key in ["hr", "er", "pr", "her2"]) or receptor_payload.get("matches"):
                    for key in ["hr", "er", "pr", "her2"]:
                        if receptor_payload.get(key) and not receptor_status_state.get(key):
                            receptor_status_state[key] = receptor_payload.get(key)
                    receptor_status_state["matches"].update(receptor_payload.get("matches") or [])

                performance_payload = _extract_performance_status_payload(signal_blob, resource=resource)
                if performance_payload.get("value") is not None:
                    existing_date = parse_iso_datetime(performance_status_state.get("date"))
                    should_replace = existing_date is None or (event_date is not None and event_date > existing_date)
                    if should_replace:
                        performance_status_state["value"] = performance_payload.get("value")
                        performance_status_state["date"] = event_date_serialized
                    performance_status_state["matches"].update(performance_payload.get("matches") or [])

                stage_payload = _extract_stage_extent_payload(signal_blob)
                if (
                    stage_payload.get("stage")
                    or stage_payload.get("tnm")
                    or stage_payload.get("metastatic")
                    or stage_payload.get("locallyAdvanced")
                    or stage_payload.get("matches")
                ):
                    if stage_payload.get("stage"):
                        stage_or_extent_state["stage"] = stage_payload.get("stage")
                    stage_or_extent_state["tnm"].update(stage_payload.get("tnm") or [])
                    stage_or_extent_state["metastatic"] = bool(
                        stage_or_extent_state["metastatic"] or stage_payload.get("metastatic")
                    )
                    stage_or_extent_state["locallyAdvanced"] = bool(
                        stage_or_extent_state["locallyAdvanced"] or stage_payload.get("locallyAdvanced")
                    )
                    stage_or_extent_state["matches"].update(stage_payload.get("matches") or [])

                oncology_treatment_terms.update(_extract_oncology_treatment_terms(signal_blob, normalized_terms))

                note_items = resource.get("note") or []
                for note in note_items[:2] if isinstance(note_items, list) else []:
                    snippet_text = (note or {}).get("text")
                    if snippet_text:
                        snippets.append(
                            {
                                "source": "synthea",
                                "date": event_date_serialized,
                                "text": str(snippet_text)[:380],
                            }
                        )

                for path_keywords in PHARMA_RULE_RISK_PATHS.values():
                    for term in path_keywords:
                        if any(term in term_value for term_value in normalized_terms):
                            risk_signals.add(term)

                if resource_type == "Condition":
                    condition_history.update(normalized_terms[:8])
                elif resource_type == "MedicationRequest":
                    med_history.update(normalized_terms[:8])
                    med_status = normalize_pharma_text(resource.get("status"))
                    if med_status in {"active", "on-hold"}:
                        active_meds.update(normalized_terms[:8])
                elif resource_type == "Procedure":
                    procedure_history.update(normalized_terms[:8])
                elif resource_type in {"Procedure", "DiagnosticReport", "Observation"}:
                    if any("mammogram" in term for term in normalized_terms):
                        if latest_mammo_date is None or (event_date is not None and event_date > latest_mammo_date):
                            latest_mammo_date = event_date

                if resource_type == "Observation":
                    obs_text = " ".join(normalized_terms + extracted_codes).strip()
                    value_quantity = resource.get("valueQuantity") or {}
                    numeric_value = None
                    if isinstance(value_quantity, dict) and value_quantity.get("value") is not None:
                        numeric_value = to_nullable_float(value_quantity.get("value"))
                    if numeric_value is None and resource.get("valueInteger") is not None:
                        numeric_value = to_nullable_float(resource.get("valueInteger"))
                    if numeric_value is None and resource.get("valueString"):
                        numeric_match = re.search(r"(\d+(?:\.\d+)?)", str(resource.get("valueString")))
                        if numeric_match:
                            numeric_value = to_nullable_float(numeric_match.group(1))

                    if numeric_value is not None and (
                        "body mass index" in obs_text or "bmi" in obs_text or "39156-5" in obs_text
                    ):
                        latest_seen = parse_iso_datetime(latest_bmi.get("date"))
                        if latest_seen is None or (event_date is not None and event_date > latest_seen):
                            latest_bmi = {
                                "value": numeric_value,
                                "date": event_date_serialized,
                            }

                    lab_aliases = {
                        "bilirubin": ["bilirubin"],
                        "ast": ["ast", "aspartate aminotransferase"],
                        "alt": ["alt", "alanine aminotransferase"],
                        "creatinine": ["creatinine"],
                        "hemoglobin": ["hemoglobin", "haemoglobin"],
                        "platelet": ["platelet"],
                        "viral_load": ["viral load", "hiv rna"],
                    }
                    for lab_key, aliases in lab_aliases.items():
                        if any(alias in obs_text for alias in aliases):
                            labs[lab_key] = {
                                "value": numeric_value,
                                "unit": value_quantity.get("unit") if isinstance(value_quantity, dict) else None,
                                "date": event_date_serialized,
                                "label": normalized_terms[0] if normalized_terms else lab_key,
                            }

            if active_meds:
                facts["activeMedications"] = {"value": sorted(list(active_meds))[:80], "source": "synthea"}
            if med_history:
                facts["medicationHistory"] = {"value": sorted(list(med_history))[:120], "source": "synthea"}
            if condition_history:
                facts["conditionHistory"] = {"value": sorted(list(condition_history))[:120], "source": "synthea"}
            if procedure_history:
                facts["procedureHistory"] = {"value": sorted(list(procedure_history))[:120], "source": "synthea"}
            if latest_bmi.get("value") is not None:
                facts["latestBmi"] = {
                    "value": latest_bmi.get("value"),
                    "date": latest_bmi.get("date"),
                    "source": "synthea",
                }
            if latest_mammo_date is not None:
                facts["lastMammogramDate"] = {
                    "value": serialize_doc(latest_mammo_date),
                    "source": "synthea" if synthea_resources else "leafy",
                }
            if labs:
                facts["keyLabs"] = {"value": labs, "source": "synthea"}
            if risk_signals:
                facts["riskSignals"] = {"value": sorted(list(risk_signals))[:80], "source": "synthea"}
            elif snippets:
                facts["riskSignals"] = {"value": sorted(list(risk_signals))[:80], "source": "leafy"}
            if (
                receptor_status_state.get("hr")
                or receptor_status_state.get("er")
                or receptor_status_state.get("pr")
                or receptor_status_state.get("her2")
                or receptor_status_state.get("matches")
            ):
                facts["receptorStatus"] = {
                    "value": {
                        "hr": receptor_status_state.get("hr"),
                        "er": receptor_status_state.get("er"),
                        "pr": receptor_status_state.get("pr"),
                        "her2": receptor_status_state.get("her2"),
                        "matches": sorted(list(receptor_status_state.get("matches") or []))[:8],
                    },
                    "source": "synthea",
                }
            if performance_status_state.get("value") is not None:
                facts["performanceStatus"] = {
                    "value": {
                        "scale": "ECOG",
                        "value": performance_status_state.get("value"),
                        "date": performance_status_state.get("date"),
                        "matches": sorted(list(performance_status_state.get("matches") or []))[:8],
                    },
                    "source": "synthea",
                }
            if (
                stage_or_extent_state.get("stage")
                or stage_or_extent_state.get("tnm")
                or stage_or_extent_state.get("metastatic")
                or stage_or_extent_state.get("locallyAdvanced")
            ):
                facts["stageOrExtent"] = {
                    "value": {
                        "stage": stage_or_extent_state.get("stage"),
                        "tnm": sorted(list(stage_or_extent_state.get("tnm") or []))[:6],
                        "metastatic": bool(stage_or_extent_state.get("metastatic")),
                        "locallyAdvanced": bool(stage_or_extent_state.get("locallyAdvanced")),
                        "matches": sorted(list(stage_or_extent_state.get("matches") or []))[:8],
                    },
                    "source": "synthea",
                }
            if oncology_treatment_terms:
                facts["oncologyTreatmentHistory"] = {
                    "value": {"terms": sorted(list(oncology_treatment_terms))[:16]},
                    "source": "synthea",
                }

        missing_fields = []
        required_fact_paths = [
            ("age", "age"),
            ("sex", "sex"),
            ("latestBmi", "latestBmi"),
            ("lastMammogramDate", "lastMammogramDate"),
            ("activeMedications", "activeMedications"),
            ("conditionHistory", "conditionHistory"),
            ("keyLabs", "keyLabs"),
        ]
        for fact_key, field_label in required_fact_paths:
            fact_value = ((facts.get(fact_key) or {}).get("value"))
            if (
                fact_value is None
                or fact_value == ""
                or (isinstance(fact_value, list) and len(fact_value) == 0)
                or (isinstance(fact_value, dict) and len(fact_value) == 0)
            ):
                missing_fields.append(field_label)

        source_breakdown = {"leafyFacts": 0, "syntheaFacts": 0, "unknownFacts": 0}
        for fact_payload in facts.values():
            source_name = normalize_pharma_text((fact_payload or {}).get("source") or "")
            if source_name == "leafy":
                source_breakdown["leafyFacts"] += 1
            elif source_name in {"synthea", "synthea-facts"}:
                source_breakdown["syntheaFacts"] += 1
            else:
                source_breakdown["unknownFacts"] += 1

        screening_summary = {
            "reportId": str(report.get("_id") or context.get("reportId") or ""),
            "appointmentId": context.get("appointmentId") or str(report.get("appointmentID") or "").strip(),
            "leafyPatientId": context.get("patientId") or str(report.get("patientID") or report.get("patient-id") or "").strip(),
            "patientName": context.get("patientName") or str(report.get("user") or "").strip(),
            "reportDate": report_date,
            "sex": normalize_pharma_text(context.get("sex") or ""),
            "ageYears": context.get("age"),
            "birthDate": context.get("birthDate"),
            "numberImages": parse_int_value(context.get("numberImages"), 0, 0),
            "biradsValues": sorted(list(set(context.get("biradsValues") or []))),
            "biradsLeft": None,
            "biradsRight": None,
            "biradsMax": max(context.get("biradsValues") or []) if (context.get("biradsValues") or []) else None,
            "suspiciousLaterality": None,
            "reportConclusion": context.get("conclusions") or report.get("conclusions") or "",
            "clinicalNotes": context.get("clinicalNotes") or report.get("clinicalNotes") or "",
            "conclusionsSNOMED": report.get("conclusionsSNOMED") or "",
            "sourceRefs": [f"clinicalreports/{str(report.get('_id') or context.get('reportId') or '')}"] if (report.get("_id") or context.get("reportId")) else [],
        }

        report_series = report.get("series") or []
        birads_by_laterality = {}
        for image_entry in report_series:
            image_meta = (image_entry or {}).get("imageMetadata") or {}
            laterality = normalize_pharma_text(image_meta.get("Laterality") or "")
            laterality = laterality[:1].upper() if laterality else ""
            birads_payload = image_meta.get("biRads")
            birads_value = birads_payload.get("BIRAD") if isinstance(birads_payload, dict) else birads_payload
            birads_int = parse_int_value(birads_value, None, 0)
            if laterality and birads_int is not None:
                birads_by_laterality[laterality] = max(birads_int, parse_int_value(birads_by_laterality.get(laterality), birads_int, 0))
        screening_summary["biradsLeft"] = birads_by_laterality.get("L")
        screening_summary["biradsRight"] = birads_by_laterality.get("R")
        if birads_by_laterality:
            suspicious_code = sorted(
                birads_by_laterality.items(),
                key=lambda item: (-1 * parse_int_value(item[1], 0, 0), item[0]),
            )[0][0].lower()
            screening_summary["suspiciousLaterality"] = "left" if suspicious_code == "l" else ("right" if suspicious_code == "r" else suspicious_code)

        profile_facts = dict(facts)
        canonical_fact_doc_facts = (patient_facts_doc or {}).get("facts") or {}
        for fact_key in ["age", "sex", "birthDate"]:
            incoming_payload = canonical_fact_doc_facts.get(fact_key)
            if fact_payload_has_value(incoming_payload):
                profile_facts[fact_key] = incoming_payload

        evidence_profile = build_patient_evidence_profile_v2(
            patient_ref=synthea_ref,
            facts_doc={
                "patientRef": synthea_ref,
                "generatedAt": (patient_facts_doc or {}).get("generatedAt"),
                "facts": profile_facts,
            },
            screening_summary=screening_summary,
            linkage_payload=linkage_payload,
            normalize_text_fn=normalize_pharma_text,
        )

        timeline_sorted = sorted(
            timeline,
            key=lambda item: parse_iso_datetime(item.get("date")) or datetime.min,
            reverse=True
        )[:160]
        return {
            "patientRef": synthea_ref or None,
            "leafyAppointmentId": context.get("appointmentId"),
            "facts": facts,
            "patientEvidenceProfile": evidence_profile,
            "timeline": timeline_sorted,
            "snippets": snippets[:40],
            "missing_fields": sorted(list(set(missing_fields))),
            "source_breakdown": source_breakdown,
            "source_provenance": {
                "leafyReportId": str(report.get("_id") or context.get("reportId") or ""),
                "leafyAppointmentId": context.get("appointmentId") or "",
                "leafyPatientId": context.get("patientId") or "",
                "syntheaPatientRef": synthea_ref or "",
                "patientFactsVersion": (patient_facts_doc or {}).get("factVersion"),
                "center": context.get("center") or ((appointment.get("resource") or {}).get("location") or {}).get("display"),
            },
        }

    def build_risk_evidence(evidence_pack):
        facts = (evidence_pack or {}).get("facts") or {}
        snippets = (evidence_pack or {}).get("snippets") or []
        risk_signal_values = ((facts.get("riskSignals") or {}).get("value")) or []
        snippet_text = " ".join(
            [normalize_pharma_text(item.get("text") or "") for item in snippets[:20] if isinstance(item, dict)]
        )
        signal_text = normalize_pharma_text(" ".join([str(item) for item in risk_signal_values])) + " " + snippet_text

        paths = []
        pass_count = 0
        unknown_count = 0
        for path_name, keywords in PHARMA_RULE_RISK_PATHS.items():
            matches = [kw for kw in keywords if kw in signal_text]
            if matches:
                status = "PASS"
                pass_count += 1
            elif not signal_text.strip():
                status = "UNKNOWN"
                unknown_count += 1
            else:
                status = "FAIL"
            paths.append(
                {
                    "path": path_name,
                    "status": status,
                    "evidence": matches[:4],
                    "missingData": [] if status != "UNKNOWN" else ["riskSignals"],
                }
            )

        if pass_count > 0:
            overall = "PASS"
        elif unknown_count == len(paths):
            overall = "UNKNOWN"
        else:
            overall = "FAIL"
        return {
            "overallStatus": overall,
            "paths": paths,
        }

    def evaluate_rule_with_evidence(rule, evidence_pack):
        rule_type = normalize_pharma_text(rule.get("type") or "inclusion")
        text_value = str(rule.get("text") or "").strip()
        normalized_text = normalize_pharma_text(text_value)
        tags = set(rule.get("concept_tags") or [])
        rule_kind = normalize_pharma_text(rule.get("rule_kind") or "patient_eligibility")
        primary_group = normalize_pharma_text(rule.get("primary_criterion_group") or "")
        facts = (evidence_pack or {}).get("facts") or {}
        missing_data = []
        evidence = []
        condition_met = None
        evaluation_mode = "deterministic"
        patient_value = None
        trial_value = None
        negative_language = bool(re.search(r"\b(no|not|without|free of|absence|off)\b", normalized_text))
        ambiguous_negated_exclusion = bool(
            rule_type == "exclusion"
            and negative_language
            and tags.intersection({"medications", "conditions", "pathology"})
        )

        age_fact = (facts.get("age") or {}).get("value")
        sex_fact = normalize_pharma_text((facts.get("sex") or {}).get("value") or "")
        birads_fact = set((facts.get("biradsValues") or {}).get("value") or [])
        bmi_payload = facts.get("latestBmi") or {}
        mammogram_payload = facts.get("lastMammogramDate") or {}
        meds_active = [normalize_pharma_text(item) for item in ((facts.get("activeMedications") or {}).get("value") or [])]
        meds_history = [normalize_pharma_text(item) for item in ((facts.get("medicationHistory") or {}).get("value") or [])]
        condition_history = [normalize_pharma_text(item) for item in ((facts.get("conditionHistory") or {}).get("value") or [])]
        labs_payload = (facts.get("keyLabs") or {}).get("value") or {}
        receptor_status_payload = (facts.get("receptorStatus") or {}).get("value") or {}
        performance_status_payload = (facts.get("performanceStatus") or {}).get("value") or {}
        stage_or_extent_payload = (facts.get("stageOrExtent") or {}).get("value") or {}
        oncology_treatment_payload = (facts.get("oncologyTreatmentHistory") or {}).get("value") or {}

        if "age" in tags:
            if age_fact is None:
                missing_data.append("age")
            else:
                check_result = evaluate_numeric_window(
                    rule.get("operator"),
                    rule.get("value"),
                    rule.get("value_max"),
                    age_fact,
                )
                if check_result is None:
                    check_result = True
                condition_met = bool(check_result)
                evidence.append("facts.age")
                patient_value = age_fact
                trial_value = {"operator": rule.get("operator"), "value": rule.get("value"), "valueMax": rule.get("value_max")}
        elif "sex" in tags:
            target_sex = "female" if "female" in normalized_text else "male" if "male" in normalized_text else ""
            if not sex_fact:
                missing_data.append("sex")
            elif not target_sex:
                condition_met = True
                evidence.append("facts.sex")
                patient_value = sex_fact
            else:
                condition_met = (sex_fact == target_sex)
                evidence.append("facts.sex")
                patient_value = sex_fact
                trial_value = target_sex
        elif "birads" in tags:
            birads_targets = set(parse_birads_values_from_text(normalized_text))
            if not birads_fact:
                missing_data.append("biradsValues")
            elif not birads_targets:
                condition_met = True
                evidence.append("facts.biradsValues")
                patient_value = sorted(list(birads_fact))
            else:
                condition_met = bool(birads_fact.intersection(birads_targets))
                evidence.append("facts.biradsValues")
                patient_value = sorted(list(birads_fact))
                trial_value = sorted(list(birads_targets))
        elif "bmi" in tags:
            bmi_value = bmi_payload.get("value")
            bmi_date = bmi_payload.get("date")
            if bmi_value is None:
                missing_data.append("latestBmi")
            else:
                check_result = evaluate_numeric_window(
                    rule.get("operator") or ">=",
                    rule.get("value"),
                    rule.get("value_max"),
                    bmi_value,
                )
                condition_met = bool(check_result) if check_result is not None else True
                evidence.append("facts.latestBmi")
                patient_value = bmi_value
                trial_value = {"operator": rule.get("operator") or ">=", "value": rule.get("value"), "valueMax": rule.get("value_max")}
                window_days = ((rule.get("time_window") or {}).get("days"))
                if window_days and bmi_date:
                    delta_days = days_since(bmi_date)
                    if delta_days is None:
                        missing_data.append("bmiDate")
                        condition_met = None
                    elif delta_days > int(window_days):
                        condition_met = False
                elif window_days and not bmi_date:
                    missing_data.append("bmiDate")
                    condition_met = None
        elif "mammogram" in tags:
            mammogram_date = mammogram_payload.get("value")
            if not mammogram_date:
                missing_data.append("lastMammogramDate")
            else:
                condition_met = True
                evidence.append("facts.lastMammogramDate")
                patient_value = mammogram_date
                window_days = ((rule.get("time_window") or {}).get("days")) or (
                    365 if ("month" in normalized_text and "12" in normalized_text) else None
                )
                if window_days:
                    delta_days = days_since(mammogram_date)
                    if delta_days is None:
                        condition_met = None
                        missing_data.append("lastMammogramDate")
                    else:
                        condition_met = bool(delta_days <= int(window_days))
        elif "performance_status" in tags or "ecog" in normalized_text or "performance status" in normalized_text:
            performance_value = performance_status_payload.get("value")
            performance_target = _extract_performance_status_target(normalized_text, rule)
            if performance_value is None:
                missing_data.append("performanceStatus")
            elif performance_target.get("value") is None and performance_target.get("valueMax") is None:
                evaluation_mode = "llm_adjudication_required"
                missing_data.append("performanceStatusThreshold")
            else:
                check_result = evaluate_numeric_window(
                    performance_target.get("operator"),
                    performance_target.get("value"),
                    performance_target.get("valueMax"),
                    performance_value,
                )
                condition_met = bool(check_result) if check_result is not None else True
                evidence.append("facts.performanceStatus")
                patient_value = {
                    "scale": performance_status_payload.get("scale") or "ECOG",
                    "value": performance_value,
                }
                trial_value = performance_target
        elif "receptor_status" in tags or any(token in normalized_text for token in ["her2", "estrogen receptor", "progesterone receptor", "triple negative", "hormone receptor", "hr+"]):
            receptor_targets = _extract_receptor_targets(normalized_text)
            patient_receptor_values = {
                "hr": normalize_pharma_text(receptor_status_payload.get("hr") or ""),
                "er": normalize_pharma_text(receptor_status_payload.get("er") or ""),
                "pr": normalize_pharma_text(receptor_status_payload.get("pr") or ""),
                "her2": normalize_pharma_text(receptor_status_payload.get("her2") or ""),
            }
            if not patient_receptor_values.get("hr"):
                if patient_receptor_values.get("er") == "positive" or patient_receptor_values.get("pr") == "positive":
                    patient_receptor_values["hr"] = "positive"
                elif patient_receptor_values.get("er") == "negative" and patient_receptor_values.get("pr") == "negative":
                    patient_receptor_values["hr"] = "negative"
            if not any(patient_receptor_values.values()):
                missing_data.append("receptorStatus")
            elif not receptor_targets:
                evaluation_mode = "llm_adjudication_required"
                missing_data.append("receptorStatusTarget")
            else:
                comparisons = []
                for receptor_key, expected_value in receptor_targets.items():
                    actual_value = patient_receptor_values.get(receptor_key) or ""
                    if not actual_value:
                        missing_data.append(f"receptorStatus.{receptor_key}")
                        comparisons.append(None)
                    else:
                        comparisons.append(actual_value == expected_value)
                if comparisons and all(item is not None for item in comparisons):
                    condition_met = all(comparisons)
                evidence.append("facts.receptorStatus")
                patient_value = patient_receptor_values
                trial_value = receptor_targets
        elif "stage_or_extent" in tags or any(token in normalized_text for token in ["metastatic", "stage", "tnm", "locally advanced", "inoperable", "unresectable"]):
            stage_targets = _extract_stage_targets(normalized_text)
            has_stage_payload = bool(
                stage_or_extent_payload.get("stage")
                or stage_or_extent_payload.get("tnm")
                or stage_or_extent_payload.get("metastatic")
                or stage_or_extent_payload.get("locallyAdvanced")
            )
            if not has_stage_payload:
                missing_data.append("stageOrExtent")
            elif not any([stage_targets.get("stage"), stage_targets.get("metastatic"), stage_targets.get("locallyAdvanced")]):
                evaluation_mode = "llm_adjudication_required"
                missing_data.append("stageOrExtentTarget")
            else:
                stage_matches = []
                if stage_targets.get("stage"):
                    stage_matches.append(stage_targets.get("stage") == normalize_pharma_text(stage_or_extent_payload.get("stage") or ""))
                if stage_targets.get("metastatic"):
                    stage_matches.append(bool(stage_or_extent_payload.get("metastatic")))
                if stage_targets.get("locallyAdvanced"):
                    stage_matches.append(bool(stage_or_extent_payload.get("locallyAdvanced")))
                if stage_matches:
                    if stage_targets.get("metastatic") and stage_targets.get("locallyAdvanced") and " or " in normalized_text:
                        condition_met = any(stage_matches)
                    else:
                        condition_met = all(stage_matches)
                evidence.append("facts.stageOrExtent")
                patient_value = stage_or_extent_payload
                trial_value = stage_targets
        elif "oncology_treatment" in tags or any(token in normalized_text for token in ["systemic therapy", "chemotherapy", "hormonal therapy", "treatment naive", "first line", "prior therapy", "line of therapy", "radiation therapy"]):
            oncology_treatment_terms = [
                normalize_pharma_text(item)
                for item in (oncology_treatment_payload.get("terms") or oncology_treatment_payload or [])
                if normalize_pharma_text(item)
            ] if isinstance(oncology_treatment_payload, (dict, list)) else []
            treatment_targets = _extract_oncology_treatment_targets(normalized_text)
            if not oncology_treatment_terms:
                missing_data.append("oncologyTreatmentHistory")
            else:
                if treatment_targets:
                    has_match = any(
                        any(target in term or term in target for term in oncology_treatment_terms)
                        for target in treatment_targets
                    )
                else:
                    has_match = bool(oncology_treatment_terms)
                condition_met = not has_match if negative_language else has_match
                evidence.append("facts.oncologyTreatmentHistory")
                patient_value = oncology_treatment_terms[:16]
                trial_value = treatment_targets[:8] if treatment_targets else ["treatment_history_present"]
        elif "medications" in tags:
            med_tokens = [token for token in PHARMA_RULE_MEDICATION_TOKENS if token in normalized_text]
            if not med_tokens:
                med_tokens = [token for token in tokenize_rule_text(text_value) if len(token) >= 5][:6]
            all_meds = meds_active + meds_history
            if not all_meds:
                missing_data.append("medicationHistory")
            else:
                has_match = any(any(token in med for med in all_meds) for token in med_tokens)
                condition_met = not has_match if negative_language else has_match
                evidence.append("facts.activeMedications")
                patient_value = all_meds[:16]
                trial_value = med_tokens[:8]
        elif "conditions" in tags or "pathology" in tags:
            condition_tokens = [token for token in PHARMA_RULE_CONDITION_TOKENS if token in normalized_text]
            if not condition_tokens:
                condition_tokens = [token for token in tokenize_rule_text(text_value) if len(token) >= 4][:8]
            if not condition_history:
                missing_data.append("conditionHistory")
            else:
                has_match = any(any(token in item for item in condition_history) for token in condition_tokens)
                condition_met = not has_match if negative_language else has_match
                evidence.append("facts.conditionHistory")
                patient_value = condition_history[:16]
                trial_value = condition_tokens[:8]
        elif "labs" in tags:
            lab_aliases = {
                "bilirubin": ["bilirubin"],
                "ast": ["ast"],
                "alt": ["alt"],
                "creatinine": ["creatinine"],
                "hemoglobin": ["hemoglobin"],
                "platelet": ["platelet"],
                "viral_load": ["viral load", "hiv"],
            }
            target_lab_key = None
            for lab_key, aliases in lab_aliases.items():
                if any(alias in normalized_text for alias in aliases):
                    target_lab_key = lab_key
                    break
            if not target_lab_key:
                evaluation_mode = "llm_adjudication_required"
                missing_data.append("labResults")
            else:
                lab_value_payload = labs_payload.get(target_lab_key) or {}
                lab_value = lab_value_payload.get("value")
                if lab_value is None:
                    missing_data.append(target_lab_key)
                else:
                    check_result = evaluate_numeric_window(
                        rule.get("operator"),
                        rule.get("value"),
                        rule.get("value_max"),
                        lab_value,
                    )
                    condition_met = bool(check_result) if check_result is not None else True
                    evidence.append(f"facts.keyLabs.{target_lab_key}")
                    patient_value = lab_value
                    trial_value = {"operator": rule.get("operator"), "value": rule.get("value"), "valueMax": rule.get("value_max")}
                    window_days = ((rule.get("time_window") or {}).get("days"))
                    lab_date = lab_value_payload.get("date")
                    if window_days:
                        if not lab_date:
                            condition_met = None
                            missing_data.append(f"{target_lab_key}Date")
                        else:
                            age_days = days_since(lab_date)
                            if age_days is None:
                                condition_met = None
                                missing_data.append(f"{target_lab_key}Date")
                            else:
                                condition_met = bool(age_days <= int(window_days)) and bool(condition_met)
        else:
            evaluation_mode = "llm_adjudication_required"

        if condition_met is None:
            status = "UNKNOWN"
        elif rule_type == "exclusion":
            status = "FAIL" if condition_met else "PASS"
        else:
            status = "PASS" if condition_met else "FAIL"

        if status in {"PASS", "FAIL"} and (patient_value is None or len(evidence) == 0):
            status = "UNKNOWN"
            evaluation_mode = "deterministic_missing_data"
            if patient_value is None:
                missing_data.append("patientValue")
            if len(evidence) == 0:
                missing_data.append("evidencePointers")

        if ambiguous_negated_exclusion and status in {"PASS", "FAIL"}:
            status = "UNKNOWN"
            evaluation_mode = "parser_ambiguous_negation"
            missing_data.append("ruleNegationInterpretation")

        if status == "UNKNOWN" and evaluation_mode == "deterministic":
            evaluation_mode = "deterministic_missing_data"

        if status == "PASS":
            eligibility_impact = "supports_eligibility"
        elif status == "FAIL":
            eligibility_impact = "blocks_eligibility"
        else:
            eligibility_impact = "unknown"

        if status == "UNKNOWN":
            criterion_state = "unknown"
        elif rule_type == "exclusion":
            criterion_state = "triggered" if status == "FAIL" else "not_triggered"
        else:
            criterion_state = "satisfied" if status == "PASS" else "not_satisfied"

        evidence_sources = _resolve_evidence_sources(evidence, evidence_pack)
        manual_review_reason = _describe_manual_review_reason(status, evaluation_mode, missing_data)

        return {
            "ruleId": rule.get("rule_id"),
            "canonicalCriterionId": rule.get("canonicalCriterionId"),
            "type": rule.get("type"),
            "text": rule.get("text"),
            "ruleKind": rule_kind or "patient_eligibility",
            "primaryCriterionGroup": primary_group or "general",
            "conceptTags": rule.get("concept_tags") or [],
            "dataNeeded": rule.get("data_needed") or [],
            "status": status,
            "criterionState": criterion_state,
            "eligibilityImpact": eligibility_impact,
            "evaluationMode": evaluation_mode,
            "requiresManualReview": bool(status == "UNKNOWN"),
            "manualReviewReason": manual_review_reason,
            "evidencePointers": evidence[:6],
            "evidenceSources": evidence_sources[:4],
            "patientValue": patient_value,
            "trialValue": trial_value,
            "missingData": sorted(list(set(missing_data)))[:8],
            "timeWindow": rule.get("time_window"),
            "operator": rule.get("operator"),
        }

    def evaluate_trial_structured_match(trial_doc, patient_context, semantic_query=None, llm_adjudications=None):
        patient_payload = patient_context if isinstance(patient_context, dict) else {}
        evidence_pack = patient_payload.get("evidencePack")
        if not isinstance(evidence_pack, dict):
            evidence_pack = build_patient_evidence_pack(patient_payload)
        adjudication_map = llm_adjudications if isinstance(llm_adjudications, dict) else {}

        canonical_definition = trial_doc.get("trialDefinitionCanonical") or {}
        canonical_criteria = list(canonical_definition.get("criteria") or [])
        canonical_rules = []
        for criterion in canonical_criteria:
            if not isinstance(criterion, dict):
                continue
            matcher_binding = criterion.get("matcherBinding") or {}
            if not isinstance(matcher_binding, dict):
                continue
            canonical_rules.append(matcher_binding)

        trial_rules = canonical_rules or trial_doc.get("pharma_trial_rules") or trial_doc.get("atomicRules") or []
        if not trial_rules:
            eligibility = trial_doc.get("eligibility") or {}
            trial_rules = build_pharma_trial_rules(
                eligibility_module={
                    "sex": (eligibility.get("sex") or [None])[0] if isinstance(eligibility.get("sex"), list) else eligibility.get("sex"),
                    "minimumAge": eligibility.get("ageMin"),
                    "maximumAge": eligibility.get("ageMax"),
                    "eligibilityCriteria": eligibility.get("rawCriteria") or "",
                },
                aggregate_text=f"{trial_doc.get('title') or ''} {trial_doc.get('summary') or ''} {eligibility.get('rawCriteria') or ''}",
            )

        parser_warnings = list(trial_doc.get("parserWarnings") or [])
        patient_rules = []
        non_patient_rules = []
        for rule in trial_rules:
            if not isinstance(rule, dict):
                continue
            rule_kind = normalize_pharma_text(rule.get("rule_kind") or "patient_eligibility")
            if rule_kind in {"", "patient_eligibility"}:
                patient_rules.append(rule)
            else:
                non_patient_rules.append(rule)

        rule_results = []
        for rule in patient_rules:
            rule_results.append(evaluate_rule_with_evidence(rule, evidence_pack))

        if adjudication_map:
            for result in rule_results:
                rule_id = str(result.get("ruleId") or "").strip()
                if not rule_id:
                    continue
                adjudication = adjudication_map.get(rule_id)
                if not isinstance(adjudication, dict):
                    continue
                requested_status = str(adjudication.get("status") or "").strip().upper()
                evidence_pointers = [str(item).strip() for item in (adjudication.get("evidencePointers") or []) if str(item).strip()]
                missing_data = [str(item).strip() for item in (adjudication.get("missingData") or []) if str(item).strip()]
                reasoning_text = str(adjudication.get("reasoning") or adjudication.get("message") or "").strip()
                llm_meta = adjudication.get("llmMeta") if isinstance(adjudication.get("llmMeta"), dict) else {}
                if requested_status in {"PASS", "FAIL"} and evidence_pointers:
                    result["status"] = requested_status
                    result["evaluationMode"] = "llm_cited"
                    result["evidencePointers"] = evidence_pointers[:6]
                    result["missingData"] = missing_data[:8]
                    if result.get("patientValue") is None:
                        result["patientValue"] = adjudication.get("patientValue") or "narrative_evidence"
                else:
                    result["status"] = "UNKNOWN"
                    result["evaluationMode"] = "llm_missing_citation"
                    result["evidencePointers"] = evidence_pointers[:6]
                    merged_missing = sorted(list(set((result.get("missingData") or []) + missing_data)))
                    result["missingData"] = merged_missing[:8]
                if reasoning_text:
                    result["llmReasoning"] = reasoning_text[:320]
                if llm_meta:
                    result["llmMeta"] = llm_meta

        # Guardrail: if equivalent constraints disagree (both PASS and FAIL), mark as parser-inconsistent.
        consistency_conflicts = []
        consistency_map = defaultdict(lambda: {"statuses": set(), "indexes": []})
        for idx, result in enumerate(rule_results):
            status_value = str(result.get("status") or "").upper()
            if status_value not in {"PASS", "FAIL"}:
                continue
            trial_value = result.get("trialValue") if isinstance(result.get("trialValue"), dict) else {}
            signature_payload = {
                "type": normalize_pharma_text(result.get("type") or ""),
                "ruleKind": normalize_pharma_text(result.get("ruleKind") or ""),
                "conceptTags": sorted([normalize_pharma_text(tag) for tag in (result.get("conceptTags") or []) if normalize_pharma_text(tag)]),
                "operator": result.get("operator") or trial_value.get("operator"),
                "value": trial_value.get("value"),
                "valueMax": trial_value.get("valueMax"),
                "text": normalize_pharma_text(result.get("text") or ""),
            }
            signature_key = hashlib.sha256(json.dumps(signature_payload, sort_keys=True).encode("utf-8")).hexdigest()[:20]
            consistency_map[signature_key]["statuses"].add(status_value)
            consistency_map[signature_key]["indexes"].append(idx)

        for payload in consistency_map.values():
            if payload.get("statuses") != {"PASS", "FAIL"}:
                continue
            conflict_rule_ids = []
            for idx in payload.get("indexes") or []:
                row = rule_results[idx]
                if row.get("ruleId"):
                    conflict_rule_ids.append(row.get("ruleId"))
                existing_missing = [str(item).strip() for item in (row.get("missingData") or []) if str(item).strip()]
                row["status"] = "UNKNOWN"
                row["evaluationMode"] = "parser_inconsistent"
                row["missingData"] = sorted(list(set(existing_missing + ["parser_consistency"])))[:8]
            consistency_conflicts.append(
                {
                    "ruleIds": sorted(list(set(conflict_rule_ids)))[:8]
                }
            )

        status_counts = defaultdict(int)
        for item in rule_results:
            status_counts[item.get("status") or "UNKNOWN"] += 1
        total_rules = len(rule_results)
        denominator = max(total_rules, 1)
        pass_count = status_counts.get("PASS", 0)
        fail_count = status_counts.get("FAIL", 0)
        unknown_count = status_counts.get("UNKNOWN", 0)
        evaluated_count = pass_count + fail_count

        deterministic_candidates = [
            rule for rule in patient_rules
            if bool(rule.get("deterministic_candidate"))
        ]
        deterministic_ids = set([rule.get("rule_id") for rule in deterministic_candidates if rule.get("rule_id")])
        deterministic_evaluated = len(
            [result for result in rule_results if result.get("ruleId") in deterministic_ids and result.get("status") in {"PASS", "FAIL"}]
        )
        llm_with_citations = len(
            [
                result for result in rule_results
                if result.get("evaluationMode") == "llm_cited" and result.get("status") in {"PASS", "FAIL"}
            ]
        )
        llm_queue = [
            result for result in rule_results
            if result.get("status") == "UNKNOWN"
            and result.get("evaluationMode") in {"llm_adjudication_required", "llm_missing_citation"}
            and normalize_pharma_text(result.get("ruleKind")) == "patient_eligibility"
        ]

        coverage_meter = {
            "hasRules": bool(total_rules > 0),
            "totalRules": total_rules,
            "evaluatedRules": evaluated_count,
            "deterministicRules": len(deterministic_ids),
            "deterministicEvaluated": deterministic_evaluated,
            "llmWithCitations": llm_with_citations,
            "unknownRules": unknown_count,
            "deterministicCoveragePct": round(
                (deterministic_evaluated / max(len(deterministic_ids), 1)) * 100.0, 1
            ),
            "llmCoveragePct": round((llm_with_citations / denominator) * 100.0, 1),
            "unknownPct": round((unknown_count / denominator) * 100.0, 1),
            "overallCoveragePct": round((evaluated_count / denominator) * 100.0, 1),
        }

        criterion_group = defaultdict(list)
        for result in rule_results:
            primary_group = normalize_pharma_text(result.get("primaryCriterionGroup") or "")
            criterion_group[primary_group or "general"].append(result)

        criterion_breakdown = []
        for tag_name, rows in criterion_group.items():
            pass_count_for_tag = sum(1 for row in rows if row.get("status") == "PASS")
            fail_count_for_tag = sum(1 for row in rows if row.get("status") == "FAIL")
            unknown_count_for_tag = sum(1 for row in rows if row.get("status") == "UNKNOWN")
            if fail_count_for_tag > 0 and (pass_count_for_tag > 0 or unknown_count_for_tag > 0):
                status_label = "mixed"
            elif fail_count_for_tag > 0:
                status_label = "fail"
            elif pass_count_for_tag > 0:
                status_label = "pass"
            else:
                status_label = "unknown"
            passed = status_label == "pass"
            detail = (
                f"{pass_count_for_tag} PASS, "
                f"{fail_count_for_tag} FAIL, "
                f"{unknown_count_for_tag} UNKNOWN"
            )
            criterion_breakdown.append(
                {
                    "criterion": tag_name,
                    "passed": passed,
                    "status": status_label,
                    "weight": round(len(rows) / denominator, 4),
                    "detail": detail,
                    "patientValue": None,
                    "trialValue": None,
                    "passCount": pass_count_for_tag,
                    "failCount": fail_count_for_tag,
                    "unknownCount": unknown_count_for_tag,
                }
            )
        criterion_breakdown = sorted(criterion_breakdown, key=lambda item: item.get("weight", 0), reverse=True)[:12]
        total_weight = sum(float(item.get("weight") or 0) for item in criterion_breakdown)
        if total_weight > 0:
            normalized_sum = 0.0
            for idx, item in enumerate(criterion_breakdown):
                if idx == len(criterion_breakdown) - 1:
                    item["weight"] = round(max(0.0, 1.0 - normalized_sum), 4)
                else:
                    normalized_weight = round(float(item.get("weight") or 0) / total_weight, 4)
                    item["weight"] = normalized_weight
                    normalized_sum += normalized_weight

        semantic_matches = []
        query_tokens = tokenize_rule_text(
            f"{semantic_query or ''} {' '.join(patient_payload.get('keywords') or [])}"
        )
        if query_tokens:
            for rule in patient_rules:
                candidate_tokens = rule.get("rule_tokens") or tokenize_rule_text(rule.get("text") or "")
                score = build_text_overlap_score(query_tokens, candidate_tokens)
                if score <= 0:
                    continue
                semantic_matches.append(
                    {
                        "ruleId": rule.get("rule_id"),
                        "type": rule.get("type"),
                        "text": rule.get("text"),
                        "score": score,
                        "conceptTags": rule.get("concept_tags") or [],
                    }
                )
            semantic_matches = sorted(semantic_matches, key=lambda item: item.get("score", 0), reverse=True)[:8]

        validator_warnings = []
        for result in rule_results:
            if result.get("status") in {"PASS", "FAIL"}:
                if result.get("patientValue") is None:
                    validator_warnings.append(
                        {
                            "code": "missing_patient_value",
                            "ruleId": result.get("ruleId"),
                            "message": "PASS/FAIL requires patientValue.",
                        }
                    )
                if not (result.get("evidencePointers") or []):
                    validator_warnings.append(
                        {
                            "code": "missing_evidence_pointers",
                            "ruleId": result.get("ruleId"),
                            "message": "PASS/FAIL requires evidencePointers.",
                        }
                    )

        for conflict in consistency_conflicts:
            validator_warnings.append(
                {
                    "code": "inconsistent_rule_outcomes",
                    "ruleIds": conflict.get("ruleIds") or [],
                    "message": "Equivalent rule constraints produced both PASS and FAIL and were downgraded to UNKNOWN.",
                }
            )

        # Catch obvious dataNeeded mismatches (example: AST requested for non-lab rules).
        lab_missing_tokens = {"ast", "alt", "bilirubin", "platelet", "hemoglobin", "creatinine", "viral_load", "labresults"}
        for result in rule_results:
            tags = set([normalize_pharma_text(tag) for tag in (result.get("conceptTags") or []) if normalize_pharma_text(tag)])
            missing_tokens = set(
                [normalize_pharma_text(item) for item in (result.get("missingData") or []) if normalize_pharma_text(item)]
            )
            if "labs" not in tags and missing_tokens.intersection(lab_missing_tokens):
                validator_warnings.append(
                    {
                        "code": "mismatched_missing_data",
                        "ruleId": result.get("ruleId"),
                        "message": "Rule missing-data fields include lab markers but rule is not tagged as labs.",
                    }
                )
        if len(patient_rules) == 0:
            parser_warnings.append(
                {
                    "code": "no_patient_eligibility_rules",
                    "severity": "high",
                    "message": "All parsed rules are non-patient rules or no rules were parsed.",
                }
            )

        risk_evidence = build_risk_evidence(evidence_pack)

        inclusion_fail_count = sum(
            1 for item in rule_results
            if item.get("status") == "FAIL" and normalize_pharma_text(item.get("type")) == "inclusion"
        )
        exclusion_hit_count = sum(
            1 for item in rule_results
            if item.get("status") == "FAIL" and normalize_pharma_text(item.get("type")) == "exclusion"
        )
        has_inclusion_fail = inclusion_fail_count > 0
        has_exclusion_hit = exclusion_hit_count > 0

        if total_rules == 0:
            overall_assessment = "needs_review"
        elif has_inclusion_fail or has_exclusion_hit:
            overall_assessment = "likely_ineligible"
        elif unknown_count > 0:
            overall_assessment = "needs_review"
        else:
            overall_assessment = "likely_eligible"

        is_eligible = overall_assessment == "likely_eligible"
        if total_rules == 0:
            eligibility_score = 0.0
            evidence_completeness_score = 0.0
            structured_score = 0.0
        else:
            if has_inclusion_fail or has_exclusion_hit:
                eligibility_score = 0.02
            else:
                eligibility_score = min(1.0, max(0.0, pass_count / float(denominator)))
            evidence_completeness_score = min(1.0, max(0.0, evaluated_count / float(denominator)))
            structured_score = round(float(eligibility_score * evidence_completeness_score), 4)
            if has_inclusion_fail or has_exclusion_hit:
                structured_score = min(structured_score, 0.05)
        match_reasons = [
            f"{row.get('type')} rule matched: {row.get('text')[:140]}"
            for row in rule_results
            if row.get("status") == "PASS"
        ][:8]
        missing_criteria = sorted(
            list(
                {
                    missing_item
                    for row in rule_results if row.get("status") == "UNKNOWN"
                    for missing_item in (row.get("missingData") or [])
                }
            )
        )[:10]
        failed_rule_examples = [
            str(row.get("text") or "").strip()
            for row in rule_results
            if row.get("status") == "FAIL" and row.get("text")
        ][:5]
        evidence_source_counts = defaultdict(int)
        evaluation_mode_counts = defaultdict(int)
        criterion_state_counts = defaultdict(int)
        manual_review_reasons = defaultdict(int)
        for row in rule_results:
            evaluation_mode_counts[str(row.get("evaluationMode") or "unknown")] += 1
            criterion_state_counts[str(row.get("criterionState") or "unknown")] += 1
            unique_sources = list({str(item).strip() for item in (row.get("evidenceSources") or []) if str(item).strip()})
            if not unique_sources:
                unique_sources = ["unknown"]
            for source_name in unique_sources:
                evidence_source_counts[source_name] += 1
            if row.get("status") == "UNKNOWN":
                reason_label = str(row.get("manualReviewReason") or "Evidence incomplete").strip()
                manual_review_reasons[reason_label] += 1

        if total_rules == 0:
            decision_reason = "No atomic trial rules were parsed; eligibility cannot be determined."
        elif has_inclusion_fail or has_exclusion_hit:
            decision_reason = (
                f"{inclusion_fail_count} inclusion rule(s) failed and "
                f"{exclusion_hit_count} exclusion rule(s) were triggered."
            )
        elif unknown_count > 0:
            decision_reason = (
                f"{unknown_count} of {total_rules} rules are unknown due to missing evidence; manual review is required."
            )
        else:
            decision_reason = f"All {total_rules} atomic rules passed with available evidence."

        decision_summary = {
            "assessment": overall_assessment,
            "reason": decision_reason,
            "contractVersion": TRIAL_MATCHING_CONTRACT_VERSION,
            "totalRules": total_rules,
            "evaluatedRules": evaluated_count,
            "passRules": pass_count,
            "failRules": fail_count,
            "unknownRules": unknown_count,
            "inclusionFails": inclusion_fail_count,
            "exclusionHits": exclusion_hit_count,
            "eligibilityScore": round(float(eligibility_score), 4),
            "evidenceCompletenessScore": round(float(evidence_completeness_score), 4),
            "hardFail": bool(has_inclusion_fail or has_exclusion_hit),
            "manualReviewRequired": bool(overall_assessment == "needs_review"),
            "manualReviewRuleCount": unknown_count,
            "retrievalAffectsEligibility": False,
            "failedRuleExamples": failed_rule_examples,
            "missingDataFields": missing_criteria,
            "evidenceSourceCounts": dict(sorted(evidence_source_counts.items(), key=lambda item: (-item[1], item[0]))),
            "evaluationModeCounts": dict(sorted(evaluation_mode_counts.items(), key=lambda item: (-item[1], item[0]))),
            "criterionStateCounts": dict(sorted(criterion_state_counts.items(), key=lambda item: (-item[1], item[0]))),
            "manualReviewReasons": dict(sorted(manual_review_reasons.items(), key=lambda item: (-item[1], item[0]))),
        }

        patient_version = ""
        try:
            patient_version = hashlib.sha256(
                json.dumps(evidence_pack or {}, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
        except Exception:
            patient_version = ""

        return {
            "matchingContractVersion": TRIAL_MATCHING_CONTRACT_VERSION,
            "isEligibleStructured": bool(is_eligible),
            "overallAssessment": overall_assessment,
            "structuredScore": structured_score,
            "eligibilityScore": round(float(eligibility_score), 4),
            "evidenceCompletenessScore": round(float(evidence_completeness_score), 4),
            "matchReasons": match_reasons,
            "missingCriteria": missing_criteria,
            "decisionSummary": decision_summary,
            "trialDefinitionCanonicalSummary": canonical_definition.get("summary") or {},
            "trialDefinitionStandards": canonical_definition.get("standards") or {},
            "trialVersion": trial_doc.get("trialVersion") or {},
            "sourcePayloadHash": trial_doc.get("sourcePayloadHash"),
            "patientVersion": patient_version,
            "parserWarnings": parser_warnings[:12],
            "validatorWarnings": validator_warnings[:20],
            "nonPatientRulesExcluded": len(non_patient_rules),
            "totalParsedRules": len(trial_rules),
            "ruleStatusCounts": {
                "pass": pass_count,
                "fail": fail_count,
                "unknown": unknown_count,
                "total": total_rules,
            },
            "criterionBreakdown": criterion_breakdown,
            "criterionEvaluations": rule_results,
            "ruleEvaluations": rule_results,
            "llmAdjudicationQueue": llm_queue,
            "riskEvidence": risk_evidence,
            "coverageMeter": coverage_meter,
            "semanticRetrievalMatches": semantic_matches,
            "retrievalSignals": {
                "semanticRuleMatches": semantic_matches,
                "retrievalAffectsEligibility": False,
                "rankingOnly": True,
            },
            "criteriaLevelMatches": semantic_matches,
            "evidencePack": evidence_pack,
        }

    return {
        "build_leafy_note_snippets": build_leafy_note_snippets,
        "build_patient_evidence_pack": build_patient_evidence_pack,
        "build_risk_evidence": build_risk_evidence,
        "evaluate_rule_with_evidence": evaluate_rule_with_evidence,
        "evaluate_trial_structured_match": evaluate_trial_structured_match,
    }
