from __future__ import annotations

import re

from app.services.helpers import normalize_space, normalize_token
from app.services.normalizer import normalize_criterion_text


SECTION_HEADERS = {
    "inclusion": re.compile(r"^\s*inclusion criteria\s*:?\s*$", flags=re.IGNORECASE),
    "exclusion": re.compile(r"^\s*exclusion criteria\s*:?\s*$", flags=re.IGNORECASE),
}

BULLET_PATTERN = re.compile(r"^(?P<indent>\s*)(?:[-*•]|\d+[.)])\s+(?P<text>.+)$")


def _section_code(section: str) -> str:
    return "EXC" if section == "exclusion" else "INC"


def _group_allows_same_level_children(header_text: str) -> bool:
    normalized = normalize_token(header_text)
    return (
        normalized.endswith("with:")
        or "all the following" in normalized
        or "following criteria" in normalized
        or normalized.endswith("including:")
    )


def _same_level_child_matches(header_text: str, child_text: str) -> bool:
    normalized_header = normalize_token(header_text)
    stripped_child = str(child_text or "").lstrip()
    if "all the following" in normalized_header or "following criteria" in normalized_header:
        return True
    if normalized_header.endswith(("with:", "including:")):
        if not stripped_child:
            return False
        first_character = stripped_child[0]
        first_token = normalize_token(stripped_child.split()[0].strip("(["))
        if first_token in {
            "active",
            "inadequate",
            "medical",
            "participant",
            "participants",
            "patient",
            "patients",
            "pregnant",
            "significant",
            "symptomatic",
            "women",
        }:
            return False
        return first_character.islower() or first_character.isupper() or first_character in {"<", ">", "=", "(", "[", "≤", "≥"}
    return False


def _build_node(
    *,
    version_id: str,
    trial_id: str,
    section: str,
    ordinal: int,
    node_type: str,
    source_kind: str,
    text: str,
    parent_node: dict | None,
    source_path: str,
) -> dict:
    ordinal_token = f"{ordinal:03d}"
    code = _section_code(section)
    node_id = f"{version_id}::{code}::{ordinal_token}"
    parent_path = list(parent_node.get("path") or []) if isinstance(parent_node, dict) else []
    path = parent_path + [ordinal_token] if parent_path else [code, ordinal_token]
    depth = max(1, len(path) - 1)
    ancestor_headers = list(parent_node.get("ancestor_headers") or []) if isinstance(parent_node, dict) else []
    if isinstance(parent_node, dict) and parent_node.get("node_type") == "group_header":
        ancestor_headers = ancestor_headers + [str(parent_node.get("raw", {}).get("text") or "")]
    return {
        "_id": node_id,
        "trial_id": trial_id,
        "version_id": version_id,
        "section": section,
        "node_type": node_type,
        "source_kind": source_kind,
        "ordinal": ordinal,
        "depth": depth,
        "path": path,
        "parent_node_id": parent_node.get("_id") if isinstance(parent_node, dict) else None,
        "ancestor_headers": [header for header in ancestor_headers if header],
        "raw": {
            "text": normalize_space(text),
            "source_path": source_path,
            "lineage_version": 1,
        },
        "normalized": {},
        "compiler": {},
        "review": {},
        "audit": {},
    }


def parse_markdown_eligibility_tree(*, version_id: str, trial_id: str, eligibility_text: str) -> list[dict]:
    lines = [line.rstrip() for line in str(eligibility_text or "").splitlines()]
    section = "inclusion"
    section_ordinals = {"inclusion": 0, "exclusion": 0}
    nodes: list[dict] = []
    group_stack: list[dict] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        next_section = None
        for section_name, pattern in SECTION_HEADERS.items():
            if pattern.match(stripped):
                next_section = section_name
                break
        if next_section:
            section = next_section
            group_stack = []
            continue

        bullet_match = BULLET_PATTERN.match(line)
        if not bullet_match:
            if nodes and nodes[-1]["section"] == section:
                nodes[-1]["raw"]["text"] = normalize_space(f"{nodes[-1]['raw']['text']} {stripped}")
            else:
                section_ordinals[section] += 1
                nodes.append(
                    _build_node(
                        version_id=version_id,
                        trial_id=trial_id,
                        section=section,
                        ordinal=section_ordinals[section],
                        node_type="note",
                        source_kind="markdown_note",
                        text=stripped,
                        parent_node=None,
                        source_path="protocolSection.eligibilityModule.eligibilityCriteria",
                    )
                )
            continue

        indent = len(bullet_match.group("indent") or "")
        text = normalize_space(bullet_match.group("text"))

        while group_stack:
            parent_candidate = group_stack[-1]
            parent_indent = int(parent_candidate.get("_indent", 0))
            if indent > parent_indent:
                break
            if indent == parent_indent and parent_candidate.get("_same_level_mode") and _same_level_child_matches(
                parent_candidate.get("raw", {}).get("text"),
                text,
            ):
                break
            group_stack.pop()

        parent_node = group_stack[-1] if group_stack else None
        node_type = "group_header" if text.endswith(":") else "leaf"
        source_kind = "markdown_group_header" if node_type == "group_header" else "markdown_leaf"

        section_ordinals[section] += 1
        node = _build_node(
            version_id=version_id,
            trial_id=trial_id,
            section=section,
            ordinal=section_ordinals[section],
            node_type=node_type,
            source_kind=source_kind,
            text=text,
            parent_node=parent_node,
            source_path="protocolSection.eligibilityModule.eligibilityCriteria",
        )
        nodes.append(node)

        if node_type == "group_header":
            node["_indent"] = indent
            node["_same_level_mode"] = _group_allows_same_level_children(text)
            group_stack.append(node)

    for node in nodes:
        node.pop("_indent", None)
        node.pop("_same_level_mode", None)
        if node["node_type"] != "note":
            node["normalized"] = normalize_criterion_text(node["raw"]["text"])
    return nodes


def build_synthetic_structured_criteria(*, version_id: str, trial_id: str, structured_eligibility: dict) -> list[dict]:
    synthetic_specs: list[tuple[str, str, str, str]] = []
    if structured_eligibility.get("minimum_age"):
        synthetic_specs.append(
            ("AGE_MIN", "Minimum age", "protocolSection.eligibilityModule.minimumAge", "Minimum age {value} {unit}")
        )
    if structured_eligibility.get("maximum_age"):
        synthetic_specs.append(
            ("AGE_MAX", "Maximum age", "protocolSection.eligibilityModule.maximumAge", "Maximum age {value} {unit}")
        )
    if structured_eligibility.get("sex"):
        synthetic_specs.append(
            ("SEX", "Sex", "protocolSection.eligibilityModule.sex", "Sex {value}")
        )
    if structured_eligibility.get("healthy_volunteers") is not None:
        synthetic_specs.append(
            (
                "HEALTHY_VOLUNTEERS",
                "Healthy volunteers",
                "protocolSection.eligibilityModule.healthyVolunteers",
                "Healthy volunteers accepted {value}",
            )
        )

    nodes: list[dict] = []
    for ordinal, (token, label, source_path, template) in enumerate(synthetic_specs, start=1):
        value = None
        if token == "AGE_MIN":
            age_payload = structured_eligibility.get("minimum_age") or {}
            value = template.format(value=age_payload.get("value"), unit=age_payload.get("unit"))
        elif token == "AGE_MAX":
            age_payload = structured_eligibility.get("maximum_age") or {}
            value = template.format(value=age_payload.get("value"), unit=age_payload.get("unit"))
        elif token == "SEX":
            value = template.format(value=structured_eligibility.get("sex"))
        else:
            value = template.format(value=str(structured_eligibility.get("healthy_volunteers")).lower())
        node = {
            "_id": f"{version_id}::STRUCT::{token}",
            "trial_id": trial_id,
            "version_id": version_id,
            "section": "inclusion",
            "node_type": "synthetic_structured",
            "source_kind": "structured_field",
            "ordinal": ordinal,
            "depth": 0,
            "path": ["STRUCT", token],
            "parent_node_id": None,
            "ancestor_headers": [],
            "raw": {
                "text": normalize_space(value),
                "source_path": source_path,
                "lineage_version": 1,
            },
            "normalized": normalize_criterion_text(value),
            "compiler": {},
            "review": {},
            "audit": {},
            "display_label": label,
        }
        nodes.append(node)
    return nodes
