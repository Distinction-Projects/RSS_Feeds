from __future__ import annotations

import html
import re
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "ref",
}
TRACKING_QUERY_PREFIXES = ("utm_",)
_WHITESPACE_PATTERN = re.compile(r"\s+")
_SOURCE_SUFFIX_SEPARATORS = (" - ", " | ", " — ", " – ")


def compact_whitespace(value: Any) -> str:
    return _WHITESPACE_PATTERN.sub(" ", html.unescape(str(value or ""))).strip()


def normalize_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""

    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw

    filtered_query = [
        (key, item_value)
        for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
        and not any(key.lower().startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES)
    ]
    normalized_path = parsed.path
    if normalized_path.endswith("/") and normalized_path != "/":
        normalized_path = normalized_path.rstrip("/")

    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=normalized_path,
        query=urlencode(filtered_query, doseq=True),
        fragment="",
    )
    canonical = urlunsplit(normalized)
    if canonical.endswith("/") and normalized.query == "":
        return canonical[:-1]
    return canonical


def normalize_title(value: Any, *, source_name: str | None = None) -> str:
    title = compact_whitespace(value)
    source = compact_whitespace(source_name)
    if not title or not source:
        return title

    title_casefold = title.casefold()
    source_casefold = source.casefold()
    for separator in _SOURCE_SUFFIX_SEPARATORS:
        suffix = f"{separator}{source_casefold}"
        if title_casefold.endswith(suffix):
            return title[: -len(suffix)].rstrip()
    return title


def normalize_tags(values: Any) -> list[str]:
    if values is None:
        return []
    raw_values = values if isinstance(values, list) else [values]
    seen: set[str] = set()
    normalized: list[str] = []
    for value in raw_values:
        tag = compact_whitespace(value)
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(tag)
    return normalized


def canonical_source_id(value: Any) -> str:
    text = compact_whitespace(value).casefold()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def normalize_datetime_text(value: Any) -> str:
    raw = compact_whitespace(value)
    if not raw:
        return ""
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return raw
    if parsed is None:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
