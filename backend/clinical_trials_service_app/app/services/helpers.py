from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_json_hash(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_token(value: Any) -> str:
    return normalize_space(value).lower()


def parse_age_string(value: Any) -> dict | None:
    text = normalize_space(value)
    if not text or text.upper() in {"N/A", "NA"}:
        return None
    match = re.search(r"(?P<number>\d+)\s*(?P<unit>year|years|month|months|week|weeks|day|days)", text, flags=re.IGNORECASE)
    if not match:
        return None
    unit = match.group("unit").lower()
    canonical_unit = "years"
    if unit.startswith("month"):
        canonical_unit = "months"
    elif unit.startswith("week"):
        canonical_unit = "weeks"
    elif unit.startswith("day"):
        canonical_unit = "days"
    return {
        "value": int(match.group("number")),
        "unit": canonical_unit,
    }


def serialize_document(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: serialize_document(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize_document(item) for item in value]
    if isinstance(value, datetime):
        return isoformat_z(value)
    if isinstance(value, ObjectId):
        return str(value)
    return value


def truncate_list(values: list[Any], limit: int) -> list[Any]:
    return list(values[: max(0, int(limit))])

