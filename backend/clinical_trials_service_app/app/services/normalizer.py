from __future__ import annotations

import re
import unicodedata

from app.services.helpers import stable_json_hash


PROTECTED_TOKEN_PATTERNS = [
    r"\bCDK4/6\b",
    r"\bHER2[+-]\b",
    r"\bHER-?2\b",
    r"\bER[+-]\b",
    r"\bPR[+-]\b",
    r"\bHR[+-]\b",
    r"\bARV-\d+\b",
    r"\bRECIST\s*v?\.?\s*\d(?:\.\d+)?\b",
    r"\bECOG\s*\d(?:\s*-\s*\d)?\b",
]


COMPARATOR_REPLACEMENTS = {
    "≤": "<=",
    "≥": ">=",
    "≦": "<=",
    "≧": ">=",
    "–": "-",
    "—": "-",
    "−": "-",
}


def extract_protected_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for pattern in PROTECTED_TOKEN_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            token = match.group(0)
            if token not in tokens:
                tokens.append(token)
    return tokens


def normalize_criterion_text(text: str) -> dict:
    raw_text = str(text or "")
    normalized_text = unicodedata.normalize("NFKC", raw_text)
    for source, target in COMPARATOR_REPLACEMENTS.items():
        normalized_text = normalized_text.replace(source, target)
    normalized_text = re.sub(r"\byrs?\b", "years", normalized_text, flags=re.IGNORECASE)
    normalized_text = re.sub(r"\bmos?\b", "months", normalized_text, flags=re.IGNORECASE)
    normalized_text = re.sub(r"\bwks?\b", "weeks", normalized_text, flags=re.IGNORECASE)
    normalized_text = re.sub(r"\bdays?\b", "days", normalized_text, flags=re.IGNORECASE)
    normalized_text = re.sub(r"\s+", " ", normalized_text).strip()
    protected_tokens = extract_protected_tokens(normalized_text)
    lowered_text = normalized_text.lower()
    template_text = lowered_text
    template_text = re.sub(r"\b\d+\s*-\s*\d+\b", "{RANGE}", template_text)
    template_text = re.sub(r"(?<![a-z0-9])(?:<=|>=|<|>|=)\s*\d+\b", "{COMPARATOR_VALUE}", template_text)
    template_text = re.sub(r"\b\d+\b", "{NUMBER}", template_text)
    if "ecog" in lowered_text and "performance status" in lowered_text:
        template_text = "eastern cooperative oncology group {PERFORMANCE_SCALE} performance status {RANGE}"
    fingerprint = f"fp_{stable_json_hash({'normalized_text': lowered_text})[:12]}"
    return {
        "text": lowered_text,
        "template_text": template_text,
        "protected_tokens": protected_tokens,
        "criterion_fingerprint": fingerprint,
    }
