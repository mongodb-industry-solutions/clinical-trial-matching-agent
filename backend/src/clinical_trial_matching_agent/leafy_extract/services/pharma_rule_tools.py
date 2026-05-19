from datetime import datetime, timezone
import hashlib
import json
import re


def create_pharma_rule_tools(
    *,
    normalize_pharma_text_fn,
    parse_int_value_fn,
    parse_age_years_token_fn,
    parse_birads_values_from_text_fn,
    rule_stopwords,
    rule_medication_tokens,
    rule_condition_tokens,
):
    normalize_pharma_text = normalize_pharma_text_fn
    parse_int_value = parse_int_value_fn
    parse_age_years_token = parse_age_years_token_fn
    parse_birads_values_from_text = parse_birads_values_from_text_fn
    PHARMA_RULE_STOPWORDS = set(rule_stopwords or [])
    PHARMA_RULE_MEDICATION_TOKENS = set(rule_medication_tokens or [])
    PHARMA_RULE_CONDITION_TOKENS = set(rule_condition_tokens or [])

    def to_nullable_float(value):
        try:
            return float(value)
        except Exception:
            return None

    def build_source_hash(payload):
        try:
            payload_bytes = json.dumps(payload or {}, sort_keys=True, separators=(",", ":")).encode("utf-8")
            return hashlib.sha256(payload_bytes).hexdigest()
        except Exception:
            return hashlib.sha256(str(payload).encode("utf-8")).hexdigest()

    def parse_time_window_days(text_value):
        normalized_text = normalize_pharma_text(text_value)
        if not normalized_text:
            return None
        matches = re.findall(
            r"(within|during|past|last|lookback)\s+(\d{1,4})\s*(day|days|week|weeks|month|months|year|years)",
            normalized_text,
        )
        if not matches:
            return None
        _, amount_text, unit = matches[0]
        amount = parse_int_value(amount_text, None, 0)
        if amount is None:
            return None
        if unit.startswith("day"):
            return int(amount)
        if unit.startswith("week"):
            return int(amount) * 7
        if unit.startswith("month"):
            return int(amount) * 30
        if unit.startswith("year"):
            return int(amount) * 365
        return None

    def extract_numeric_bounds(text_value):
        normalized_text = normalize_pharma_text(text_value)
        if not normalized_text:
            return {"operator": None, "value": None, "valueMax": None}

        between_match = re.search(r"between\s+(\d+(?:\.\d+)?)\s+(?:and|to)\s+(\d+(?:\.\d+)?)", normalized_text)
        if between_match:
            return {
                "operator": "between",
                "value": to_nullable_float(between_match.group(1)),
                "valueMax": to_nullable_float(between_match.group(2)),
            }

        range_match = re.search(r"(\d+(?:\.\d+)?)\s*[-to]+\s*(\d+(?:\.\d+)?)", normalized_text)
        if range_match and ("age" in normalized_text or "year" in normalized_text):
            return {
                "operator": "between",
                "value": to_nullable_float(range_match.group(1)),
                "valueMax": to_nullable_float(range_match.group(2)),
            }

        or_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:or|/)\s*(\d+(?:\.\d+)?)", normalized_text)
        if or_match and any(token in normalized_text for token in ["ecog", "performance status", "grade", "status"]):
            return {
                "operator": "between",
                "value": to_nullable_float(or_match.group(1)),
                "valueMax": to_nullable_float(or_match.group(2)),
            }

        simple_patterns = [
            (r"(?:>=|at least|greater than or equal to|not less than)\s*(\d+(?:\.\d+)?)", ">="),
            (r"(?:<=|at most|less than or equal to|not greater than)\s*(\d+(?:\.\d+)?)", "<="),
            (r"(?:>\s*|greater than\s+)(\d+(?:\.\d+)?)", ">"),
            (r"(?:<\s*|less than\s+)(\d+(?:\.\d+)?)", "<"),
            (r"(?:=\s*|equal to\s+)(\d+(?:\.\d+)?)", "="),
        ]
        for pattern, operator in simple_patterns:
            match = re.search(pattern, normalized_text)
            if match:
                return {
                    "operator": operator,
                    "value": to_nullable_float(match.group(1)),
                    "valueMax": None,
                }

        token_match = re.search(r"\b(\d+(?:\.\d+)?)\b", normalized_text)
        if token_match:
            return {
                "operator": "=",
                "value": to_nullable_float(token_match.group(1)),
                "valueMax": None,
            }
        return {"operator": None, "value": None, "valueMax": None}

    def tokenize_rule_text(text_value):
        normalized_text = normalize_pharma_text(text_value)
        if not normalized_text:
            return []
        tokens = re.findall(r"[a-z0-9][a-z0-9\-/]{1,}", normalized_text)
        return [token for token in tokens if token not in PHARMA_RULE_STOPWORDS]

    def contains_term(normalized_text, term_value):
        text_value = normalize_pharma_text(normalized_text or "")
        term = normalize_pharma_text(term_value or "")
        if not text_value or not term:
            return False
        parts = [part for part in re.split(r"\s+", term) if part]
        if not parts:
            return False
        pattern = r"\b" + r"\s+".join(re.escape(part) for part in parts) + r"\b"
        return bool(re.search(pattern, text_value))

    def any_term(normalized_text, terms):
        return any(contains_term(normalized_text, term) for term in (terms or []))

    def infer_rule_type(section_type, sentence_text):
        normalized_text = normalize_pharma_text(sentence_text)
        if section_type in {"inclusion", "exclusion"}:
            return section_type
        if re.search(r"\b(exclusion|exclude|must not|cannot|no history|not eligible|disqualif)", normalized_text):
            return "exclusion"
        return "inclusion"

    def infer_concept_tags(sentence_text):
        normalized_text = normalize_pharma_text(sentence_text)
        tags = set()
        if not normalized_text:
            return []

        if "age" in normalized_text or "year old" in normalized_text or "years old" in normalized_text:
            tags.add("age")
        if "female" in normalized_text or "male" in normalized_text or "sex" in normalized_text:
            tags.add("sex")
        if "bmi" in normalized_text or "body mass index" in normalized_text:
            tags.add("bmi")
        if "mammogram" in normalized_text or "mammography" in normalized_text:
            tags.add("mammogram")
        if "bi-rads" in normalized_text or "birads" in normalized_text:
            tags.add("birads")
        if any_term(normalized_text, PHARMA_RULE_MEDICATION_TOKENS):
            tags.add("medications")
        if any_term(normalized_text, PHARMA_RULE_CONDITION_TOKENS):
            tags.add("conditions")
        if any_term(normalized_text, ["bilirubin", "ast", "alt", "creatinine", "viral load", "hemoglobin", "platelet", "lab"]):
            tags.add("labs")
        if any_term(normalized_text, ["ecog", "performance status", "eastern cooperative oncology group"]):
            tags.add("performance_status")
        if any_term(normalized_text, ["her2", "estrogen receptor", "progesterone receptor", "er positive", "pr positive", "triple negative"]):
            tags.add("receptor_status")
        if any_term(normalized_text, ["metastatic", "metastasis", "stage", "tnm", "locally advanced", "inoperable"]):
            tags.add("stage_or_extent")
        if any_term(normalized_text, ["systemic therapy", "chemotherapy", "hormonal therapy", "radiation therapy", "taxane", "anthracycline", "line of therapy", "prior therapy", "treatment line"]):
            tags.add("oncology_treatment")
        if any_term(normalized_text, ["postmenopausal", "menopause", "amenorrhea"]):
            tags.add("menopause")
        if any_term(normalized_text, ["family history", "mother", "sister", "genetic", "brca", "risk model", "gail", "tyrer"]):
            tags.add("risk")
        if any_term(normalized_text, ["lcis", "dcis", "atypia", "atypical", "hyperplasia"]):
            tags.add("pathology")
            tags.add("risk")
        if any_term(normalized_text, ["allergy", "allergic", "intolerance"]):
            tags.add("allergy")
        if any(token in normalized_text for token in ["window", "within", "last", "past", "days", "months", "years"]):
            tags.add("time_window")
        return sorted(list(tags))

    def infer_data_needed(concept_tags):
        tag_map = {
            "age": ["age"],
            "sex": ["sex"],
            "bmi": ["bmi", "bmiDate"],
            "mammogram": ["lastMammogramDate"],
            "birads": ["biradsValues"],
            "medications": ["activeMedications", "medicationHistory"],
            "conditions": ["conditionHistory"],
            "labs": ["labResults"],
            "performance_status": ["performanceStatus"],
            "receptor_status": ["receptorStatus"],
            "stage_or_extent": ["stageOrExtent"],
            "oncology_treatment": ["oncologyTreatmentHistory"],
            "menopause": ["menopauseStatus", "hormoneHistory"],
            "risk": ["pathologyHistory", "geneticFindings", "familyHistory", "riskScores", "mammographicDensity"],
            "allergy": ["allergyHistory"],
            "time_window": ["eventDates"],
            "pathology": ["pathologyHistory", "biopsyHistory"],
        }
        values = set()
        for tag in concept_tags or []:
            for needed in tag_map.get(tag, []):
                values.add(needed)
        return sorted(list(values))

    def infer_rule_kind(sentence_text, concept_tags):
        normalized_text = normalize_pharma_text(sentence_text)
        tags = set(concept_tags or [])
        if not normalized_text:
            return "informational"

        informational_tokens = [
            "for more information",
            "contact",
            "objective",
            "study aims",
            "study purpose",
            "background",
        ]
        if any_term(normalized_text, informational_tokens):
            return "informational"

        site_process_tokens = [
            "site",
            "location",
            "country",
            "institution",
            "center",
            "travel",
            "local lab",
            "shipping",
        ]
        if any_term(normalized_text, site_process_tokens):
            return "site_process"

        workflow_tokens = [
            "randomized",
            "randomisation",
            "double blind",
            "single blind",
            "cohort",
            "dose escalation",
            "study visit",
            "consent",
            "follow-up",
            "run-in",
            "dosing",
            "dose escalation",
            "adverse event",
            "endpoint",
            "end point",
            "efficacy",
            "pharmacokinetic",
            "pharmacodynamic",
            "study arm",
            "treatment arm",
        ]
        if any_term(normalized_text, workflow_tokens):
            return "study_workflow"

        patient_tags = {
            "age", "sex", "bmi", "mammogram", "birads",
            "medications", "conditions", "labs",
            "risk", "menopause", "allergy", "pathology", "time_window",
            "performance_status", "receptor_status", "stage_or_extent", "oncology_treatment",
        }
        if tags.intersection(patient_tags):
            return "patient_eligibility"
        return "informational"

    def infer_primary_criterion_group(concept_tags, sentence_text):
        tags = set(concept_tags or [])
        if "age" in tags:
            return "age"
        if "sex" in tags:
            return "sex"
        if "bmi" in tags:
            return "bmi"
        if "mammogram" in tags or "birads" in tags:
            return "imaging"
        if "medications" in tags:
            return "medications"
        if "conditions" in tags:
            return "conditions"
        if "labs" in tags:
            return "labs"
        if "performance_status" in tags:
            return "performance_status"
        if "receptor_status" in tags:
            return "receptor_status"
        if "stage_or_extent" in tags:
            return "stage_or_extent"
        if "oncology_treatment" in tags:
            return "oncology_treatment"
        if "risk" in tags or "pathology" in tags:
            return "risk"
        if "time_window" in tags:
            return "time_window"
        if "menopause" in tags:
            return "hormonal"
        if "allergy" in tags:
            return "allergy"
        if normalize_pharma_text(sentence_text or "").startswith("site"):
            return "site"
        return "general"

    def split_eligibility_sentences(raw_text):
        lines = []
        current_type = None
        for raw_line in re.split(r"[\n\r]+", str(raw_text or "")):
            line = re.sub(r"^[\-\*\d\.\)\(]+\s*", "", str(raw_line or "").strip())
            if not line:
                continue
            normalized_line = normalize_pharma_text(line)
            if normalized_line in {"inclusion criteria", "inclusion", "key inclusion criteria"}:
                current_type = "inclusion"
                continue
            if normalized_line in {"exclusion criteria", "exclusion", "key exclusion criteria"}:
                current_type = "exclusion"
                continue
            if normalized_line.startswith("inclusion criteria"):
                current_type = "inclusion"
                line = re.sub(r"^inclusion criteria[:\s-]*", "", line, flags=re.IGNORECASE).strip()
            if normalized_line.startswith("exclusion criteria"):
                current_type = "exclusion"
                line = re.sub(r"^exclusion criteria[:\s-]*", "", line, flags=re.IGNORECASE).strip()

            for sentence in re.split(r"(?<=[\.\;\:])\s+(?=[A-Z0-9])", line):
                clean_sentence = sentence.strip(" ;")
                if len(clean_sentence) < 8:
                    continue
                lines.append((current_type, clean_sentence[:1200]))
        return lines

    def build_pharma_trial_rules(*, eligibility_module, aggregate_text):
        rules = []
        rule_id_counter = 0

        def append_rule(rule_type, text_value, concept_tags=None, data_needed=None, source="raw_criteria"):
            nonlocal rule_id_counter
            normalized_text = str(text_value or "").strip()
            if not normalized_text:
                return
            rule_id_counter += 1
            computed_tags = concept_tags if concept_tags is not None else infer_concept_tags(normalized_text)
            operator_payload = extract_numeric_bounds(normalized_text)
            time_window_days = parse_time_window_days(normalized_text)
            needed = data_needed if data_needed is not None else infer_data_needed(computed_tags)
            rule_kind = infer_rule_kind(normalized_text, computed_tags)
            primary_group = infer_primary_criterion_group(computed_tags, normalized_text)
            rules.append(
                {
                    "rule_id": f"rule-{rule_id_counter:03d}",
                    "type": rule_type,
                    "text": normalized_text,
                    "source": source,
                    "concept_tags": computed_tags,
                    "data_needed": needed,
                    "time_window": {"days": time_window_days} if time_window_days else None,
                    "operator": operator_payload.get("operator"),
                    "value": operator_payload.get("value"),
                    "value_max": operator_payload.get("valueMax"),
                    "rule_kind": rule_kind,
                    "primary_criterion_group": primary_group,
                    "deterministic_candidate": bool(
                        set(computed_tags).intersection(
                            {
                                "age", "sex", "bmi", "mammogram", "birads", "medications", "conditions", "labs",
                                "performance_status", "receptor_status", "stage_or_extent", "oncology_treatment",
                            }
                        )
                    ),
                    "llm_candidate": bool(
                        set(computed_tags).intersection({"risk", "menopause", "allergy", "pathology"})
                        and not set(computed_tags).intersection(
                            {"performance_status", "receptor_status", "stage_or_extent", "oncology_treatment"}
                        )
                    ),
                    "rule_match_text": " | ".join(
                        [
                            rule_type,
                            ", ".join(computed_tags),
                            normalized_text,
                        ]
                    ).strip(),
                    "rule_tokens": tokenize_rule_text(normalized_text)[:80],
                }
            )

        sex_value = normalize_pharma_text(eligibility_module.get("sex") or "")
        if sex_value in {"female", "male"}:
            append_rule(
                "inclusion",
                f"Participant sex must be {sex_value}",
                concept_tags=["sex"],
                data_needed=["sex"],
                source="structured",
            )

        minimum_age = eligibility_module.get("minimumAge")
        maximum_age = eligibility_module.get("maximumAge")
        age_min = parse_age_years_token(minimum_age)
        age_max = parse_age_years_token(maximum_age)
        if age_min is not None or age_max is not None:
            if age_min is not None and age_max is not None:
                age_sentence = f"Age between {age_min} and {age_max} years"
            elif age_min is not None:
                age_sentence = f"Age at least {age_min} years"
            else:
                age_sentence = f"Age at most {age_max} years"
            append_rule(
                "inclusion",
                age_sentence,
                concept_tags=["age"],
                data_needed=["age"],
                source="structured",
            )

        for section_type, sentence in split_eligibility_sentences(
            eligibility_module.get("eligibilityCriteria") or aggregate_text
        ):
            append_rule(infer_rule_type(section_type, sentence), sentence)

        deduped = []
        seen = set()
        for rule in rules:
            dedupe_key = f"{rule.get('type')}::{normalize_pharma_text(rule.get('text'))}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            deduped.append(rule)
        return deduped[:120]

    def build_rule_parser_warnings(*, eligibility_module, aggregate_text, atomic_rules):
        warnings = []
        raw_text = str((eligibility_module or {}).get("eligibilityCriteria") or aggregate_text or "").strip()
        rules = atomic_rules if isinstance(atomic_rules, list) else []
        patient_rules = [rule for rule in rules if (rule or {}).get("rule_kind") == "patient_eligibility"]
        informational_rules = [rule for rule in rules if (rule or {}).get("rule_kind") == "informational"]

        if not raw_text:
            warnings.append(
                {
                    "code": "missing_eligibility_text",
                    "severity": "high",
                    "message": "Eligibility raw text is empty or unavailable.",
                }
            )
        if len(rules) == 0:
            warnings.append(
                {
                    "code": "no_atomic_rules",
                    "severity": "high",
                    "message": "No atomic rules were parsed from eligibility criteria.",
                }
            )
        if len(patient_rules) == 0:
            warnings.append(
                {
                    "code": "no_patient_eligibility_rules",
                    "severity": "high",
                    "message": "No patient_eligibility rules found; matching cannot adjudicate eligibility.",
                }
            )
        if raw_text and len(rules) > 0 and len(rules) <= 3:
            warnings.append(
                {
                    "code": "low_rule_coverage",
                    "severity": "medium",
                    "message": "Very few rules parsed from eligibility text; parser may be incomplete.",
                }
            )
        if len(informational_rules) > len(patient_rules) and len(patient_rules) > 0:
            warnings.append(
                {
                    "code": "non_patient_rule_dominance",
                    "severity": "low",
                    "message": "Most parsed rules are non-patient rules and excluded from eligibility scoring.",
                }
            )
        if len(rules) >= 120:
            warnings.append(
                {
                    "code": "rule_cap_reached",
                    "severity": "medium",
                    "message": "Atomic rule cap reached; additional rules may have been truncated.",
                }
            )
        return warnings

    def build_pharma_rule_artifacts(*, eligibility_module, aggregate_text):
        raw_text = str((eligibility_module or {}).get("eligibilityCriteria") or aggregate_text or "").strip()
        rules = build_pharma_trial_rules(
            eligibility_module=eligibility_module,
            aggregate_text=aggregate_text,
        )
        warnings = build_rule_parser_warnings(
            eligibility_module=eligibility_module,
            aggregate_text=aggregate_text,
            atomic_rules=rules,
        )
        return {
            "eligibilityRawText": raw_text,
            "atomicRules": rules,
            "parserWarnings": warnings,
        }

    def build_trial_version_payload(*, source_hash, source_label, sync_meta, source_study_last_updated):
        fetched_at = sync_meta.get("fetchedAt") if isinstance(sync_meta, dict) else None
        if fetched_at is None:
            fetched_at = datetime.utcnow()
        return {
            "versionId": f"{(sync_meta or {}).get('syncRunId') or 'manual'}::{source_hash[:16]}",
            "sourceHash": source_hash,
            "source": source_label,
            "sourceStudyLastUpdated": source_study_last_updated,
            "fetchedAt": fetched_at,
            "syncRunId": (sync_meta or {}).get("syncRunId"),
            "generatedAt": datetime.utcnow(),
        }

    def merge_trial_version_history(existing_doc, incoming_doc):
        existing = existing_doc or {}
        incoming = incoming_doc or {}
        existing_version = existing.get("trialVersion")
        existing_history = existing.get("trialVersionHistory") or []
        incoming_version = incoming.get("trialVersion") or {}

        if not existing_version:
            incoming["trialVersionHistory"] = existing_history[-30:]
            return incoming
        if existing_version.get("sourceHash") == incoming_version.get("sourceHash"):
            incoming["trialVersionHistory"] = existing_history[-30:]
            return incoming

        history_items = list(existing_history)
        history_items.append(existing_version)
        incoming["trialVersionHistory"] = history_items[-30:]
        return incoming

    def build_rule_chunks(trial_rules, chunk_size=6, max_chunks=8):
        chunks = []
        current = []
        for rule in trial_rules or []:
            if not isinstance(rule, dict):
                continue
            text_value = str(rule.get("text") or "").strip()
            if not text_value:
                continue
            current.append(f"{rule.get('type')}: {text_value}")
            if len(current) >= chunk_size:
                chunks.append(" ".join(current))
                current = []
                if len(chunks) >= max_chunks:
                    break
        if current and len(chunks) < max_chunks:
            chunks.append(" ".join(current))
        return chunks

    def evaluate_numeric_window(operator_value, target_value, target_max, observed_value):
        if observed_value is None:
            return None
        try:
            observed = float(observed_value)
        except Exception:
            return None
        if operator_value == "between":
            if target_value is None or target_max is None:
                return None
            return bool(observed >= float(target_value) and observed <= float(target_max))
        if operator_value == ">=":
            return bool(target_value is not None and observed >= float(target_value))
        if operator_value == "<=":
            return bool(target_value is not None and observed <= float(target_value))
        if operator_value == ">":
            return bool(target_value is not None and observed > float(target_value))
        if operator_value == "<":
            return bool(target_value is not None and observed < float(target_value))
        if operator_value == "=":
            if target_value is None:
                return None
            return bool(abs(observed - float(target_value)) < 0.0001)
        return None

    def parse_iso_datetime(value):
        if value is None:
            return None
        try:
            if isinstance(value, datetime):
                parsed = value
            else:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            # Normalize to UTC-naive to avoid aware/naive comparison failures downstream.
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except Exception:
            try:
                return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None

    def days_since(value):
        parsed = parse_iso_datetime(value)
        if parsed is None:
            return None
        delta = datetime.utcnow() - parsed.replace(tzinfo=None) if parsed.tzinfo else datetime.utcnow() - parsed
        return max(int(delta.total_seconds() // 86400), 0)

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

    return {
        "to_nullable_float": to_nullable_float,
        "build_source_hash": build_source_hash,
        "parse_time_window_days": parse_time_window_days,
        "extract_numeric_bounds": extract_numeric_bounds,
        "tokenize_rule_text": tokenize_rule_text,
        "infer_rule_type": infer_rule_type,
        "infer_concept_tags": infer_concept_tags,
        "infer_data_needed": infer_data_needed,
        "infer_rule_kind": infer_rule_kind,
        "infer_primary_criterion_group": infer_primary_criterion_group,
        "split_eligibility_sentences": split_eligibility_sentences,
        "build_pharma_trial_rules": build_pharma_trial_rules,
        "build_rule_parser_warnings": build_rule_parser_warnings,
        "build_pharma_rule_artifacts": build_pharma_rule_artifacts,
        "build_trial_version_payload": build_trial_version_payload,
        "merge_trial_version_history": merge_trial_version_history,
        "build_rule_chunks": build_rule_chunks,
        "evaluate_numeric_window": evaluate_numeric_window,
        "parse_iso_datetime": parse_iso_datetime,
        "days_since": days_since,
        "build_text_overlap_score": build_text_overlap_score,
    }
