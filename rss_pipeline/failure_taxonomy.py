from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError

from .normalization import compact_whitespace

_HTTP_STATUS_PATTERN = re.compile(
    r"\b(?:http(?:\s+error)?\s*)?(401|403|404|408|429|500|502|503|504)\b"
)

_CLASSIFICATION_TEMPLATES: dict[str, dict[str, Any]] = {
    "missing_link": {
        "category": "input_error",
        "message": "Article link is missing; scraper cannot fetch the article.",
        "http_status": None,
        "retryable": False,
        "source_action": "fix_feed_mapping",
    },
    "source_blocked_401": {
        "category": "source_blocked",
        "message": "Article fetch required authorization with HTTP 401.",
        "http_status": 401,
        "retryable": False,
        "source_action": "source_adapter_or_rss_fallback",
    },
    "source_blocked_403": {
        "category": "source_blocked",
        "message": "Article fetch was blocked with HTTP 403; use RSS fallback or a source-specific adapter.",
        "http_status": 403,
        "retryable": False,
        "source_action": "source_adapter_or_rss_fallback",
    },
    "article_not_found_404": {
        "category": "source_content_gone",
        "message": "Article URL returned HTTP 404.",
        "http_status": 404,
        "retryable": False,
        "source_action": "drop_or_refresh_source_url",
    },
    "rate_limited_429": {
        "category": "rate_limited",
        "message": "Article fetch was rate limited.",
        "http_status": 429,
        "retryable": True,
        "source_action": "retry_with_backoff",
    },
    "source_server_error": {
        "category": "source_unavailable",
        "message": "Article source returned a server error.",
        "http_status": None,
        "retryable": True,
        "source_action": "retry_later",
    },
    "fetch_timeout": {
        "category": "transient_network",
        "message": "Article fetch timed out.",
        "http_status": None,
        "retryable": True,
        "source_action": "retry_later",
    },
    "network_error": {
        "category": "transient_network",
        "message": "Article fetch failed due to a network error.",
        "http_status": None,
        "retryable": True,
        "source_action": "retry_later",
    },
    "unsupported_article_content_type": {
        "category": "unsupported_content",
        "message": "Article response was not HTML.",
        "http_status": None,
        "retryable": False,
        "source_action": "exclude_or_adapter",
    },
    "scrape_failed": {
        "category": "unknown_scrape_error",
        "message": "Article scrape failed.",
        "http_status": None,
        "retryable": False,
        "source_action": "review_source_adapter",
    },
}


@dataclass(frozen=True, slots=True)
class ScrapeFailureClassification:
    code: str
    category: str
    message: str
    raw_error: str
    http_status: int | None = None
    retryable: bool = False
    source_action: str = "review_source_adapter"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
            "raw_error": self.raw_error,
            "http_status": self.http_status,
            "retryable": self.retryable,
            "source_action": self.source_action,
        }


def classification_from_scrape_audit(
    scrape_audit: dict[str, Any],
    *,
    fallback_error: BaseException | str | None = None,
) -> ScrapeFailureClassification:
    taxonomy = scrape_audit.get("failure_taxonomy")
    taxonomy_payload = taxonomy if isinstance(taxonomy, dict) else {}
    raw_error = (
        compact_whitespace(taxonomy_payload.get("raw_error"))
        or compact_whitespace(scrape_audit.get("error"))
        or compact_whitespace(fallback_error)
    )
    code = compact_whitespace(taxonomy_payload.get("code")) or compact_whitespace(
        scrape_audit.get("reason")
    )
    if code and code != "scrape_failed":
        return _classification_from_template(
            code,
            raw_error=raw_error or code,
            overrides=taxonomy_payload | scrape_audit,
        )
    return classify_scrape_failure(raw_error or code or "unknown scrape failure")


def classify_scrape_failure(error: BaseException | str) -> ScrapeFailureClassification:
    raw_error = compact_whitespace(error)
    message = raw_error.casefold()
    http_status = _http_status(error, message)

    if http_status == 401:
        return _classification_from_template("source_blocked_401", raw_error=raw_error)
    if http_status == 403:
        return _classification_from_template("source_blocked_403", raw_error=raw_error)
    if http_status == 404:
        return _classification_from_template("article_not_found_404", raw_error=raw_error)
    if http_status == 408:
        return _classification_from_template(
            "fetch_timeout",
            raw_error=raw_error,
            overrides={"http_status": 408},
        )
    if http_status == 429:
        return _classification_from_template("rate_limited_429", raw_error=raw_error)
    if http_status is not None and http_status >= 500:
        return _classification_from_template(
            "source_server_error",
            raw_error=raw_error,
            overrides={"http_status": http_status},
        )
    if message in {"missing link", "missing_link"}:
        return _classification_from_template("missing_link", raw_error=raw_error)
    if isinstance(error, TimeoutError) or "timed out" in message or "timeout" in message:
        return _classification_from_template("fetch_timeout", raw_error=raw_error)
    if isinstance(error, URLError) or "urlopen error" in message:
        return _classification_from_template("network_error", raw_error=raw_error)
    if "unsupported content type" in message:
        return _classification_from_template(
            "unsupported_article_content_type",
            raw_error=raw_error,
        )

    return _classification_from_template("scrape_failed", raw_error=raw_error)


def _classification_from_template(
    code: str,
    *,
    raw_error: str,
    overrides: dict[str, Any] | None = None,
) -> ScrapeFailureClassification:
    template = _CLASSIFICATION_TEMPLATES.get(code) or _CLASSIFICATION_TEMPLATES["scrape_failed"]
    overrides = overrides or {}
    http_status = _int_or_none(overrides.get("http_status"))
    if http_status is None:
        http_status = _int_or_none(template.get("http_status"))

    retryable = overrides.get("retryable")
    return ScrapeFailureClassification(
        code=code if code in _CLASSIFICATION_TEMPLATES else "scrape_failed",
        category=compact_whitespace(overrides.get("category")) or str(template["category"]),
        message=compact_whitespace(overrides.get("message")) or str(template["message"]),
        raw_error=raw_error,
        http_status=http_status,
        retryable=retryable if isinstance(retryable, bool) else bool(template["retryable"]),
        source_action=compact_whitespace(overrides.get("source_action"))
        or str(template["source_action"]),
    )


def _http_status(error: BaseException | str, message: str) -> int | None:
    if isinstance(error, HTTPError):
        return int(error.code)
    match = _HTTP_STATUS_PATTERN.search(message)
    if match:
        return int(match.group(1))
    return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
