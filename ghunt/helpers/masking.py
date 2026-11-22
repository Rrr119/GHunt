import re
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


MASK_NONE = "none"
MASK_PARTIAL = "partial"
MASK_FULL = "full"

MASKING_LEVELS = (MASK_NONE, MASK_PARTIAL, MASK_FULL)

REDACTED = "***REDACTED***"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Attribute / key name -> masking rule. The same rules apply to live GHunt
# objects (Person, ...) and to the JSON-ready dicts produced by GHuntEncoder.
FIELD_RULES: Dict[str, str] = {
    "personId": "gaia_id",
    "gaia_id": "gaia_id",
    "customerId": "gaia_id",
    "url": "url",
    "flathash": "full",
    "email": "email",
    "email_address": "email",
}


def mask_email(value: str, level: str) -> str:
    if level == MASK_FULL or not isinstance(value, str) or "@" not in value:
        return REDACTED if level == MASK_FULL else mask_string(value, level)
    local, domain = value.rsplit("@", 1)
    if not local:
        return f"@{domain}"
    return f"{local[0]}***@{domain}"


def mask_gaia_id(value: str, level: str) -> str:
    if level == MASK_FULL:
        return REDACTED
    value = str(value)
    if len(value) <= 4:
        return "*" * len(value)
    return f"***{value[-4:]}"


def mask_url(value: str, level: str) -> str:
    if level == MASK_FULL:
        return REDACTED
    if not isinstance(value, str) or "://" not in value:
        return mask_string(value, level)
    scheme, rest = value.split("://", 1)
    host = rest.split("/", 1)[0]
    return f"{scheme}://{host}/***"


def mask_string(value: str, level: str) -> str:
    if level == MASK_FULL:
        return REDACTED
    value = str(value)
    if not value:
        return value
    keep = 1 if len(value) <= 4 else 2
    return f"{value[:keep]}***"


def _rule_for(key: str, value: Any, path: Tuple[str, ...], extra_fields: Set[str]) -> Optional[str]:
    dotted = ".".join(path)
    if key in extra_fields or dotted in extra_fields or any(dotted.endswith(field) for field in extra_fields if "." in field):
        return "full"
    if key == "value" and "emails" in path:
        return "email"
    if isinstance(value, str) and _EMAIL_RE.match(value):
        return "email"
    return FIELD_RULES.get(key)


def _apply_rule(rule: str, value: Any, level: str) -> Any:
    if rule == "email":
        return mask_email(value, level)
    if rule == "gaia_id":
        return mask_gaia_id(value, level)
    if rule == "url":
        return mask_url(value, level)
    if rule == "full":
        return REDACTED
    if isinstance(value, str):
        return mask_string(value, level)
    return REDACTED


def _walk_mask(obj: Any, level: str, extra_fields: Set[str], path: Tuple[str, ...], masked: List[str]) -> Any:
    if isinstance(obj, dict):
        for key, value in list(obj.items()):
            child_path = path + (str(key),)
            child_dotted = ".".join(child_path)
            if child_dotted in extra_fields:
                obj[key] = REDACTED
                masked.append(child_dotted)
            else:
                obj[key] = _walk_mask(value, level, extra_fields, child_path, masked)
        return obj
    if isinstance(obj, list):
        for index, value in enumerate(obj):
            obj[index] = _walk_mask(value, level, extra_fields, path + (str(index),), masked)
        return obj
    if hasattr(obj, "__dict__") and vars(obj):
        for key, value in list(vars(obj).items()):
            child_path = path + (key,)
            child_dotted = ".".join(child_path)
            if child_dotted in extra_fields:
                setattr(obj, key, REDACTED)
                masked.append(child_dotted)
            else:
                setattr(obj, key, _walk_mask(value, level, extra_fields, child_path, masked))
        return obj

    key = path[-1] if path else ""
    rule = _rule_for(key, obj, path, extra_fields)
    if rule:
        masked.append(".".join(path))
        return _apply_rule(rule, obj, level)
    return obj


def mask_person(person: Any, level: str = MASK_PARTIAL, extra_fields: Optional[Iterable[str]] = None) -> List[str]:
    """
    Masks sensitive fields of a live GHunt Person object in place.

    Returns the list of dotted field paths that were masked, useful for audits.
    """
    if level == MASK_NONE:
        return []
    if level not in MASKING_LEVELS:
        raise ValueError(f"Unknown masking level {level!r}, expected one of {MASKING_LEVELS}.")
    masked: List[str] = []
    _walk_mask(person, level, set(extra_fields or []), (), masked)
    return sorted(set(masked))


def mask_dict(
    data: Dict[str, Any],
    level: str = MASK_PARTIAL,
    extra_fields: Optional[Iterable[str]] = None,
    in_place: bool = False,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Masks sensitive fields of a JSON-ready dict (e.g. the --json export).

    Returns a (masked_copy, masked_field_paths) tuple; the input dict is left
    untouched unless in_place is True.
    """
    if level == MASK_NONE:
        return data, []
    if level not in MASKING_LEVELS:
        raise ValueError(f"Unknown masking level {level!r}, expected one of {MASKING_LEVELS}.")
    target = data if in_place else deepcopy(data)
    masked: List[str] = []
    _walk_mask(target, level, set(extra_fields or []), (), masked)
    return target, sorted(set(masked))
