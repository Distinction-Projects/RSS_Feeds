from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .failure_taxonomy import ScrapeFailureClassification, classification_from_scrape_audit
from .normalization import canonical_source_id, compact_whitespace

DEFAULT_SOURCE_SCRAPE_FALLBACK_POLICIES: dict[str, dict[str, Any]] = {
    "skynews": {
        "policy_id": "skynews-rss-only-on-403",
        "accepted_failure_codes": ["source_blocked_403"],
        "mode": "rss_summary",
        "reason": "source_blocks_article_fetch",
        "message": (
            "SkyNews article pages return HTTP 403 to automated fetches; "
            "use the RSS summary as an accepted fallback."
        ),
        "source_action": "monitor_rss_feed_or_add_source_adapter",
        "policy_source": "default",
    }
}


def normalize_scrape_fallback_policy(
    policy: Mapping[str, Any] | None,
    *,
    policy_source: str,
) -> dict[str, Any] | None:
    if not policy:
        return None

    accepted_codes = policy.get("accepted_failure_codes") or policy.get("failure_codes")
    code_values = accepted_codes if isinstance(accepted_codes, list) else [accepted_codes]
    normalized_codes = [compact_whitespace(code) for code in code_values]
    normalized_codes = [code for code in normalized_codes if code]
    if not normalized_codes:
        return None

    policy_id = compact_whitespace(policy.get("policy_id")) or "source-rss-only-fallback"
    mode = compact_whitespace(policy.get("mode")) or "rss_summary"
    reason = compact_whitespace(policy.get("reason")) or "source_scrape_policy"
    message = compact_whitespace(policy.get("message")) or (
        "Article fetch failed for a known source policy; use RSS content as fallback."
    )
    source_action = compact_whitespace(policy.get("source_action")) or "monitor_source"
    return {
        "policy_id": policy_id,
        "accepted_failure_codes": normalized_codes,
        "mode": mode,
        "reason": reason,
        "message": message,
        "source_action": source_action,
        "policy_source": compact_whitespace(policy.get("policy_source")) or policy_source,
    }


def scrape_fallback_policy_for_source(
    *,
    source_id: Any,
    source_name: Any,
    configured_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    normalized_configured = normalize_scrape_fallback_policy(
        configured_policy,
        policy_source="catalog",
    )
    if normalized_configured is not None:
        return normalized_configured

    source_keys = {
        canonical_source_id(source_id),
        canonical_source_id(source_name),
    }
    for source_key in source_keys:
        default_policy = DEFAULT_SOURCE_SCRAPE_FALLBACK_POLICIES.get(source_key)
        normalized_default = normalize_scrape_fallback_policy(
            default_policy,
            policy_source="default",
        )
        if normalized_default is not None:
            return normalized_default
    return None


def configured_scrape_fallback_policy_from_audit(audit: Any) -> dict[str, Any] | None:
    if not isinstance(audit, dict):
        return None
    source_policy = audit.get("source_policy")
    if not isinstance(source_policy, dict):
        return None
    raw_policy = source_policy.get("scrape_fallback") or source_policy.get("scrape")
    return raw_policy if isinstance(raw_policy, dict) else None


def accepted_scrape_fallback_for_source(
    *,
    source_id: Any,
    source_name: Any,
    summary: Any,
    classification: ScrapeFailureClassification,
    configured_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    policy = scrape_fallback_policy_for_source(
        source_id=source_id,
        source_name=source_name,
        configured_policy=configured_policy,
    )
    if policy is None:
        return None
    if classification.code not in set(policy["accepted_failure_codes"]):
        return None

    rss_summary = compact_whitespace(summary)
    if not rss_summary:
        return None

    return {
        "status": "accepted",
        "code": "rss_only_fallback_accepted",
        "mode": policy["mode"],
        "reason": policy["reason"],
        "message": policy["message"],
        "failure_code": classification.code,
        "policy_id": policy["policy_id"],
        "policy_source": policy["policy_source"],
        "source_action": policy["source_action"],
        "rss_summary_chars": len(rss_summary),
    }


def accepted_scrape_fallback_for_digest_item(
    item: Any,
    *,
    classification: ScrapeFailureClassification,
) -> dict[str, Any] | None:
    source = getattr(item, "source", None)
    source_id = getattr(source, "id", "")
    source_name = getattr(source, "name", "")
    return accepted_scrape_fallback_for_source(
        source_id=source_id,
        source_name=source_name,
        summary=getattr(item, "summary", ""),
        classification=classification,
        configured_policy=configured_scrape_fallback_policy_from_audit(getattr(item, "audit", {})),
    )


def accepted_scrape_fallback_for_payload(
    item: dict[str, Any],
    scrape_audit: dict[str, Any],
    *,
    fallback_error: Any = None,
    classification: ScrapeFailureClassification | None = None,
) -> dict[str, Any] | None:
    existing = accepted_scrape_fallback_from_audit(scrape_audit)
    if existing is not None:
        return existing

    source = item.get("source")
    source_payload = source if isinstance(source, dict) else {}
    classification = classification or classification_from_scrape_audit(
        scrape_audit,
        fallback_error=fallback_error,
    )
    return accepted_scrape_fallback_for_source(
        source_id=source_payload.get("id") or item.get("source_id"),
        source_name=source_payload.get("name") or item.get("source_name"),
        summary=item.get("summary"),
        classification=classification,
        configured_policy=configured_scrape_fallback_policy_from_audit(item.get("audit")),
    )


def accepted_scrape_fallback_from_audit(scrape_audit: Any) -> dict[str, Any] | None:
    if not isinstance(scrape_audit, dict):
        return None
    accepted_fallback = scrape_audit.get("accepted_fallback")
    if not isinstance(accepted_fallback, dict):
        return None
    if accepted_fallback.get("status") != "accepted":
        return None
    return accepted_fallback
