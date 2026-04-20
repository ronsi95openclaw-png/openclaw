import html
import re
from typing import Optional

# Patterns that indicate prompt injection attempts in user-sourced content
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|context)",
    r"disregard\s+(the\s+)?(above|previous|prior|all)",
    r"you\s+are\s+now\s+(a|an)",
    r"act\s+as\s+(if\s+you\s+are|a|an)",
    r"new\s+instructions?\s*:",
    r"system\s*prompt\s*:",
    r"jailbreak",
    r"<\|.*?\|>",
    r"\[INST\]",
    r"###\s*instruction",
    r"\{.*?(prompt|instruction|override).*?\}",
    r"forget\s+(everything|what\s+you)",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

_PHONE_RE = re.compile(r"(\+?1?\s?[\(]?\d{3}[\)]?[\s.\-]?\d{3}[\s.\-]?\d{4})")
_FB_URL_RE = re.compile(r"https://(www\.)?facebook\.com/marketplace/")


def sanitize_text(text: str, max_length: int = 2000) -> str:
    if not isinstance(text, str):
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_length]


def is_prompt_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text))


def sanitize_lead_field(value: str, field_name: str, max_length: int = 500) -> str:
    if value is None:
        return ""
    value = sanitize_text(str(value), max_length=max_length)
    if is_prompt_injection(value):
        raise ValueError(f"Prompt injection detected in field '{field_name}'")
    return value


def extract_phone(text: str) -> Optional[str]:
    match = _PHONE_RE.search(text)
    if match:
        digits = re.sub(r"[^\d]", "", match.group(1))
        return digits if len(digits) >= 10 else None
    return None


def validate_fb_url(url: str) -> bool:
    return bool(_FB_URL_RE.match(url))
