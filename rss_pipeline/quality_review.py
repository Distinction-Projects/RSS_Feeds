from __future__ import annotations

from collections import Counter
from typing import Any

from .cleanliness import build_cleanliness_summary
from .content_classifier import (
    NEWSLENS_INELIGIBLE_CONTENT_TYPES,
    classify_item_payload_content_type,
)
from .failure_taxonomy import classification_from_scrape_audit
from .llm_readiness import (
    llm_quality_flags_from_readiness,
    llm_readiness_from_payload,
)
from .normalization import compact_whitespace
from .scrape_policy import accepted_scrape_fallback_for_payload


def _item_source(item: dict[str, Any]) -> str:
    source = item.get("source")
    if isinstance(source, dict):
        source_name = compact_whitespace(source.get("name"))
        source_id = compact_whitespace(source.get("id"))
        return source_name or source_id or "unknown"
    return (
        compact_whitespace(item.get("source_name"))
        or compact_whitespace(item.get("source_id"))
        or "unknown"
    )


def _item_feed(item: dict[str, Any]) -> str:
    feed = item.get("feed")
    if isinstance(feed, dict):
        return (
            compact_whitespace(feed.get("name")) or compact_whitespace(feed.get("url")) or "unknown"
        )
    return (
        compact_whitespace(item.get("feed_name"))
        or compact_whitespace(item.get("feed_url"))
        or "unknown"
    )


def _item_content_type(item: dict[str, Any]) -> str:
    explicit = compact_whitespace(item.get("content_type"))
    if explicit:
        return explicit
    return classify_item_payload_content_type(item).content_type


def _audit_object(item: dict[str, Any], key: str) -> dict[str, Any]:
    audit = item.get("audit")
    if not isinstance(audit, dict):
        return {}
    value = audit.get(key)
    return value if isinstance(value, dict) else {}


def _flag(code: str, severity: str, message: str, detail: str | None = None) -> dict[str, str]:
    payload = {"code": code, "severity": severity, "message": message}
    if detail:
        payload["detail"] = detail
    return payload


def _normalized_existing_flags(item: dict[str, Any]) -> list[dict[str, str]]:
    flags = item.get("quality_flags")
    if not isinstance(flags, list):
        return []

    normalized: list[dict[str, str]] = []
    for flag in flags:
        if not isinstance(flag, dict):
            continue
        code = compact_whitespace(flag.get("code"))
        if not code:
            continue
        normalized.append(
            _flag(
                code,
                compact_whitespace(flag.get("severity")) or "warn",
                compact_whitespace(flag.get("message")) or code,
                compact_whitespace(flag.get("detail")) or None,
            )
        )
    return normalized


def _scrape_failure_flag(
    item: dict[str, Any],
    scrape_audit: dict[str, Any],
    scrape_error: str,
) -> dict[str, str]:
    classification = classification_from_scrape_audit(
        scrape_audit,
        fallback_error=scrape_error,
    )
    accepted_fallback = accepted_scrape_fallback_for_payload(
        item,
        scrape_audit,
        fallback_error=scrape_error,
        classification=classification,
    )
    if accepted_fallback is not None:
        return _flag(
            "rss_only_fallback_accepted",
            "info",
            accepted_fallback["message"],
            classification.code,
        )
    return _flag(
        classification.code,
        "warn",
        classification.message,
        classification.raw_error or classification.source_action,
    )


def _upgrade_existing_scrape_flags(
    item: dict[str, Any],
    flags: list[dict[str, str]],
) -> list[dict[str, str]]:
    scrape_audit = _audit_object(item, "scrape")
    scrape_error = compact_whitespace(scrape_audit.get("error")) or compact_whitespace(
        item.get("scrape_error")
    )
    scrape_status = compact_whitespace(scrape_audit.get("status"))
    if scrape_status != "failed" and not scrape_error:
        return flags

    classification = classification_from_scrape_audit(
        scrape_audit,
        fallback_error=scrape_error,
    )
    replacement = _scrape_failure_flag(item, scrape_audit, scrape_error)
    if replacement["code"] == "scrape_failed":
        return flags

    upgraded: list[dict[str, str]] = []
    replaced = False
    for flag in flags:
        if flag.get("code") in {"scrape_failed", classification.code}:
            upgraded.append(replacement)
            replaced = True
        else:
            upgraded.append(flag)

    if not replaced and replacement["code"] not in {flag.get("code") for flag in upgraded}:
        upgraded.append(replacement)
    return upgraded


def _upgrade_existing_content_filter_flags(
    item: dict[str, Any],
    flags: list[dict[str, str]],
) -> list[dict[str, str]]:
    exclusion_reason = _effective_newsfeed_exclusion_reason(item)
    if not exclusion_reason or not exclusion_reason.startswith("unsupported_content_type:"):
        return flags

    content_type = exclusion_reason.split(":", 1)[1]
    replacement = _flag(
        "content_type_filter_accepted",
        "info",
        "Story content type is not eligible for NewsLens and was excluded from normal output.",
        content_type,
    )
    upgraded: list[dict[str, str]] = []
    replaced = False
    for flag in flags:
        if flag.get("code") == "unsupported_content_type":
            upgraded.append(replacement)
            replaced = True
        else:
            upgraded.append(flag)
    if not replaced and replacement["code"] not in {flag.get("code") for flag in upgraded}:
        upgraded.append(replacement)
    return upgraded


def _append_llm_readiness_flags(
    item: dict[str, Any],
    flags: list[dict[str, str]],
) -> list[dict[str, str]]:
    readiness = llm_readiness_from_payload(item)
    additional_flags = llm_quality_flags_from_readiness(readiness)
    if not additional_flags:
        return flags

    existing_codes = {flag.get("code") for flag in flags}
    updated = list(flags)
    for flag in additional_flags:
        code = compact_whitespace(flag.get("code"))
        if not code or code in existing_codes:
            continue
        if code == "llm_input_rss_only_fallback" and "rss_only_fallback_accepted" in existing_codes:
            continue
        updated.append(flag)
        existing_codes.add(code)
    return updated


def _effective_newsfeed_exclusion_reason(item: dict[str, Any]) -> str | None:
    reason = compact_whitespace(item.get("newsfeed_exclusion_reason"))
    if item.get("include_in_newsfeed") is False:
        return reason or "newsfeed_excluded"

    content_audit = _audit_object(item, "content")
    if content_audit.get("exclude_from_newsfeed") is True:
        audit_reason = compact_whitespace(content_audit.get("reason"))
        return audit_reason or reason or "newsfeed_excluded"

    content_type = _item_content_type(item)
    if content_type == "missing_content":
        return "missing_rss_content"
    if content_type in NEWSLENS_INELIGIBLE_CONTENT_TYPES:
        return f"unsupported_content_type:{content_type}"

    return None


def quality_flags_from_item_payload(item: dict[str, Any]) -> list[dict[str, str]]:
    existing_flags = _normalized_existing_flags(item)
    if existing_flags:
        return _append_llm_readiness_flags(
            item,
            _upgrade_existing_scrape_flags(
                item,
                _upgrade_existing_content_filter_flags(item, existing_flags),
            ),
        )

    flags: list[dict[str, str]] = []
    if not compact_whitespace(item.get("title")):
        flags.append(_flag("missing_title", "error", "Story title is missing."))
    if not compact_whitespace(item.get("link")):
        flags.append(_flag("missing_link", "error", "Story link is missing."))
    if _item_source(item) == "unknown":
        flags.append(_flag("missing_source", "error", "Story source is missing."))
    if not compact_whitespace(item.get("published")):
        flags.append(_flag("missing_published", "warn", "Published timestamp is missing."))

    include_in_newsfeed = item.get("include_in_newsfeed")
    exclusion_reason = _effective_newsfeed_exclusion_reason(item)
    if include_in_newsfeed is False or exclusion_reason:
        reason = exclusion_reason or "newsfeed_excluded"
        if reason == "missing_rss_content":
            flags.append(
                _flag(
                    "missing_rss_content",
                    "warn",
                    "RSS entry has no summary, description, or content text.",
                )
            )
        elif reason.startswith("unsupported_content_type:"):
            content_type = reason.split(":", 1)[1]
            flags.append(
                _flag(
                    "content_type_filter_accepted",
                    "info",
                    "Story content type is not eligible for NewsLens and was excluded from normal output.",
                    content_type,
                )
            )
        else:
            flags.append(
                _flag(
                    "excluded_from_newsfeed",
                    "warn",
                    "Story was excluded from normal NewsLens output.",
                    reason,
                )
            )
    elif "summary" in item and not compact_whitespace(item.get("summary")):
        flags.append(
            _flag(
                "missing_summary",
                "warn",
                "Story has no RSS summary text.",
            )
        )

    scrape_audit = _audit_object(item, "scrape")
    scrape_status = compact_whitespace(scrape_audit.get("status"))
    scrape_error = compact_whitespace(scrape_audit.get("error")) or compact_whitespace(
        item.get("scrape_error")
    )
    if scrape_status == "failed" or scrape_error:
        flags.append(_scrape_failure_flag(item, scrape_audit, scrape_error))

    openai_audit = _audit_object(item, "openai")
    openai_status = compact_whitespace(openai_audit.get("status"))
    if openai_status in {"failed", "missing_result"}:
        flags.append(
            _flag(
                "openai_digest_failed",
                "warn",
                "OpenAI digest output is missing or failed for this story.",
                compact_whitespace(openai_audit.get("error")) or openai_status,
            )
        )
    elif (
        "ai_summary" in item
        and include_in_newsfeed is not False
        and not exclusion_reason
        and not compact_whitespace(item.get("ai_summary"))
    ):
        flags.append(
            _flag(
                "missing_ai_summary",
                "warn",
                "Story has no AI digest summary.",
            )
        )

    return _append_llm_readiness_flags(item, flags)


def _status_for_flags(flags: list[dict[str, str]]) -> str:
    if any(flag.get("severity") == "error" for flag in flags):
        return "fail"
    if any(flag.get("severity") == "warn" for flag in flags):
        return "warn"
    return "clean"


def _counter_rows(counter: Counter[str], key_name: str, limit: int) -> list[dict[str, Any]]:
    return [{key_name: key, "count": count} for key, count in counter.most_common(limit)]


def _pair_counter_rows(
    counter: Counter[tuple[str, str]],
    first_key: str,
    second_key: str,
    limit: int,
) -> list[dict[str, Any]]:
    return [
        {first_key: first, second_key: second, "count": count}
        for (first, second), count in counter.most_common(limit)
    ]


def evaluate_quality_gates(
    review: dict[str, Any],
    *,
    max_unknown_content_types: int | None = None,
    max_unsupported_content_types: int | None = None,
    max_accepted_content_type_filters: int | None = None,
    max_source_blocked: int | None = None,
    max_accepted_rss_only_fallback: int | None = None,
    max_llm_review_items: int | None = None,
    max_empty_scraped_text: int | None = None,
    max_short_scraped_text: int | None = None,
) -> dict[str, Any]:
    metrics = review.get("quality_gate_metrics")
    metric_values = metrics if isinstance(metrics, dict) else {}
    checks = {
        "unknown_content_type_items": max_unknown_content_types,
        "unsupported_content_type_items": max_unsupported_content_types,
        "accepted_content_type_filter_items": max_accepted_content_type_filters,
        "source_blocked_items": max_source_blocked,
        "accepted_rss_only_fallback_items": max_accepted_rss_only_fallback,
        "llm_review_items": max_llm_review_items,
        "empty_scraped_text_items": max_empty_scraped_text,
        "short_scraped_text_items": max_short_scraped_text,
    }
    thresholds = {
        key: value for key, value in checks.items() if isinstance(value, int) and value >= 0
    }
    violations: list[dict[str, Any]] = []
    for metric, threshold in thresholds.items():
        actual = _safe_int(metric_values.get(metric))
        if actual > threshold:
            violations.append(
                {
                    "metric": metric,
                    "actual": actual,
                    "threshold": threshold,
                    "message": f"{metric}={actual} exceeds threshold {threshold}",
                }
            )

    return {
        "status": "fail" if violations else "pass",
        "thresholds": thresholds,
        "metrics": {
            "unknown_content_type_items": _safe_int(
                metric_values.get("unknown_content_type_items")
            ),
            "unsupported_content_type_items": _safe_int(
                metric_values.get("unsupported_content_type_items")
            ),
            "accepted_content_type_filter_items": _safe_int(
                metric_values.get("accepted_content_type_filter_items")
            ),
            "source_blocked_items": _safe_int(metric_values.get("source_blocked_items")),
            "accepted_rss_only_fallback_items": _safe_int(
                metric_values.get("accepted_rss_only_fallback_items")
            ),
            "llm_review_items": _safe_int(metric_values.get("llm_review_items")),
            "empty_scraped_text_items": _safe_int(metric_values.get("empty_scraped_text_items")),
            "short_scraped_text_items": _safe_int(metric_values.get("short_scraped_text_items")),
        },
        "violations": violations,
    }


def build_digest_quality_review(payload: dict[str, Any], *, limit: int = 10) -> dict[str, Any]:
    raw_items = payload.get("items")
    items = (
        [item for item in raw_items if isinstance(item, dict)]
        if isinstance(raw_items, list)
        else []
    )

    status_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    feed_counts: Counter[str] = Counter()
    content_type_counts: Counter[str] = Counter()
    exclusion_reason_counts: Counter[str] = Counter()
    source_issue_counts: Counter[tuple[str, str]] = Counter()
    feed_issue_counts: Counter[tuple[str, str]] = Counter()
    content_type_issue_counts: Counter[tuple[str, str]] = Counter()
    llm_status_counts: Counter[str] = Counter()
    llm_reason_counts: Counter[str] = Counter()
    llm_flag_counts: Counter[str] = Counter()
    llm_source_status_counts: Counter[tuple[str, str]] = Counter()
    examples: list[dict[str, Any]] = []
    cleanliness_rows: list[dict[str, Any]] = []

    for item in items:
        flags = quality_flags_from_item_payload(item)
        status = _status_for_flags(flags)
        status_counts[status] += 1

        source = _item_source(item)
        feed = _item_feed(item)
        content_type = _item_content_type(item)
        content_type_counts[content_type] += 1
        llm_readiness = llm_readiness_from_payload(item)
        llm_status_counts[llm_readiness.status] += 1
        llm_reason_counts[llm_readiness.reason] += 1
        llm_source_status_counts[(source, llm_readiness.status)] += 1
        for llm_flag in llm_readiness.flags:
            llm_flag_counts[llm_flag.code] += 1

        exclusion_reason = _effective_newsfeed_exclusion_reason(item)
        if exclusion_reason:
            exclusion_reason_counts[exclusion_reason] += 1

        include_in_newsfeed = False if exclusion_reason else item.get("include_in_newsfeed", True)
        cleanliness_rows.append(
            {
                "id": compact_whitespace(item.get("id")),
                "title": compact_whitespace(item.get("title")),
                "source": source,
                "feed": feed,
                "content_type": content_type,
                "include_in_newsfeed": include_in_newsfeed,
                "newsfeed_exclusion_reason": exclusion_reason or None,
                "quality_status": status,
                "flags": flags,
            }
        )

        for flag in flags:
            code = compact_whitespace(flag.get("code")) or "unknown_quality_issue"
            severity = compact_whitespace(flag.get("severity")) or "warn"
            severity_counts[severity] += 1
            issue_counts[code] += 1
            source_counts[source] += 1
            feed_counts[feed] += 1
            source_issue_counts[(source, code)] += 1
            feed_issue_counts[(feed, code)] += 1
            content_type_issue_counts[(content_type, code)] += 1

        if flags and len(examples) < limit:
            examples.append(
                {
                    "id": compact_whitespace(item.get("id")),
                    "title": compact_whitespace(item.get("title")),
                    "source": source,
                    "feed": feed,
                    "content_type": content_type,
                    "include_in_newsfeed": include_in_newsfeed,
                    "newsfeed_exclusion_reason": exclusion_reason or None,
                    "llm_input_status": llm_readiness.status,
                    "llm_input_reason": llm_readiness.reason,
                    "ready_for_llm_judge": llm_readiness.ready_for_llm_judge,
                    "scraped_text_chars": llm_readiness.scraped_text_chars,
                    "flags": flags,
                }
            )

    status = (
        "fail"
        if status_counts.get("fail", 0)
        else "warn"
        if status_counts.get("warn", 0)
        else "pass"
    )
    source_blocked_items = sum(
        count for issue, count in issue_counts.items() if issue.startswith("source_blocked_")
    )
    quality_gate_metrics = {
        "unknown_content_type_items": content_type_counts.get("unknown", 0),
        "unsupported_content_type_items": issue_counts.get("unsupported_content_type", 0),
        "accepted_content_type_filter_items": issue_counts.get(
            "content_type_filter_accepted",
            0,
        ),
        "source_blocked_items": source_blocked_items,
        "accepted_rss_only_fallback_items": issue_counts.get(
            "rss_only_fallback_accepted",
            0,
        ),
        "llm_ready_items": llm_status_counts.get("ready", 0),
        "llm_review_items": llm_status_counts.get("review", 0),
        "llm_excluded_items": llm_status_counts.get("exclude", 0),
        "llm_rss_fallback_items": llm_status_counts.get("rss_fallback", 0),
        "empty_scraped_text_items": llm_flag_counts.get("empty_scraped_text", 0),
        "short_scraped_text_items": llm_flag_counts.get("short_scraped_text", 0),
    }
    return {
        "status": status,
        "generated_at": payload.get("generated_at")
        or (payload.get("run") or {}).get("generated_at"),
        "run_id": (payload.get("run") or {}).get("id"),
        "total_items": len(items),
        "issue_item_count": sum(count for key, count in status_counts.items() if key != "clean"),
        "status_counts": dict(status_counts),
        "severity_counts": dict(severity_counts),
        "quality_gate_metrics": quality_gate_metrics,
        "cleanliness": build_cleanliness_summary(cleanliness_rows, limit=limit),
        "llm_input_status_counts": _counter_rows(llm_status_counts, "status", limit),
        "llm_input_reason_counts": _counter_rows(llm_reason_counts, "reason", limit),
        "llm_input_flag_counts": _counter_rows(llm_flag_counts, "flag", limit),
        "issue_counts": _counter_rows(issue_counts, "issue", limit),
        "content_type_counts": _counter_rows(content_type_counts, "content_type", limit),
        "exclusion_reason_counts": _counter_rows(
            exclusion_reason_counts,
            "reason",
            limit,
        ),
        "top_sources": _counter_rows(source_counts, "source", limit),
        "top_feeds": _counter_rows(feed_counts, "feed", limit),
        "top_source_issues": _pair_counter_rows(source_issue_counts, "source", "issue", limit),
        "top_feed_issues": _pair_counter_rows(feed_issue_counts, "feed", "issue", limit),
        "top_content_type_issues": _pair_counter_rows(
            content_type_issue_counts,
            "content_type",
            "issue",
            limit,
        ),
        "top_llm_input_source_statuses": _pair_counter_rows(
            llm_source_status_counts,
            "source",
            "status",
            limit,
        ),
        "examples": examples,
    }


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0
