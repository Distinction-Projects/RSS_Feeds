from __future__ import annotations

from collections import Counter
from typing import Any

from .normalization import compact_whitespace

_SEVERITY_RANK = {"info": 0, "warn": 1, "error": 2}


def issue_metadata(code: str, severity: str | None = None) -> dict[str, str]:
    issue = compact_whitespace(code) or "unknown_quality_issue"
    normalized_severity = compact_whitespace(severity) or _default_severity(issue)

    if issue == "missing_rss_content":
        return _metadata(issue, normalized_severity, "rss_content", "review_feed_content")
    if issue == "missing_summary":
        return _metadata(issue, normalized_severity, "rss_content", "review_feed_content")
    if issue == "content_type_filter_accepted":
        return _metadata(issue, "info", "content_type_filter", "review_source_mix")
    if issue == "excluded_from_newsfeed":
        return _metadata(issue, normalized_severity, "content_type_filter", "review_item_policy")
    if issue in {"missing_title", "missing_link", "missing_source", "missing_published"}:
        return _metadata(issue, normalized_severity, "required_fields", "review_feed_mapping")
    if issue == "feed_fetch_failed":
        return _metadata(issue, normalized_severity, "feed_fetch", "review_feed_availability")
    if issue.startswith("source_blocked_"):
        return _metadata(
            issue, normalized_severity, "article_fetch", "source_adapter_or_rss_fallback"
        )
    if issue in {"fetch_timeout", "network_error", "scrape_failed", "missing_link"}:
        return _metadata(issue, normalized_severity, "article_fetch", "review_scrape_reliability")
    if issue == "rss_only_fallback_accepted":
        return _metadata(issue, "info", "article_fetch", "monitor_rss_fallback")
    if issue in {"missing_ai_summary", "openai_digest_failed"}:
        return _metadata(issue, normalized_severity, "openai_digest", "review_digest_generation")
    if issue.startswith("llm_input_") or issue in {
        "empty_scraped_text",
        "short_scraped_text",
        "excluded_from_llm_judge",
    }:
        return _metadata(issue, normalized_severity, "llm_input", "review_pre_llm_input")
    return _metadata(issue, normalized_severity, "quality_audit", "review_quality_flags")


def build_cleanliness_summary(rows: list[dict[str, Any]], *, limit: int = 10) -> dict[str, Any]:
    total_items = len(rows)
    clean_newsfeed_items = 0
    observable_issue_items = 0
    warning_or_failure_items = 0
    info_only_items = 0
    newsfeed_excluded = 0

    reason_counts: Counter[str] = Counter()
    stage_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    source_rows: dict[str, dict[str, Any]] = {}
    feed_rows: dict[str, dict[str, Any]] = {}

    for row in rows:
        source = compact_whitespace(row.get("source")) or "unknown"
        feed = compact_whitespace(row.get("feed")) or "unknown"
        include_in_newsfeed = row.get("include_in_newsfeed") is not False
        flags = _flags_for_row(row)
        has_flags = bool(flags)
        has_warn_or_error = any(
            _SEVERITY_RANK.get(flag["severity"], 1) >= _SEVERITY_RANK["warn"] for flag in flags
        )

        if include_in_newsfeed and not has_flags and _row_status(row) == "clean":
            clean_newsfeed_items += 1
        if has_flags or not include_in_newsfeed or _row_status(row) != "clean":
            observable_issue_items += 1
        if has_warn_or_error or _row_status(row) in {"warn", "fail"}:
            warning_or_failure_items += 1
        elif has_flags or not include_in_newsfeed:
            info_only_items += 1
        if not include_in_newsfeed:
            newsfeed_excluded += 1

        source_row = _entity_row(source_rows, source, "source")
        feed_row = _entity_row(feed_rows, feed, "feed")
        for entity_row in (source_row, feed_row):
            entity_row["total_items"] += 1
            if include_in_newsfeed and not has_flags and _row_status(row) == "clean":
                entity_row["clean_newsfeed_items"] += 1
            if has_flags or not include_in_newsfeed or _row_status(row) != "clean":
                entity_row["observable_issue_items"] += 1
            if has_warn_or_error or _row_status(row) in {"warn", "fail"}:
                entity_row["warning_or_failure_items"] += 1
            if not include_in_newsfeed:
                entity_row["newsfeed_excluded"] += 1

        for flag in flags:
            code = flag["code"]
            metadata = issue_metadata(code, flag.get("severity"))
            reason_counts[code] += 1
            stage_counts[metadata["stage"]] += 1
            action_counts[metadata["recommended_action"]] += 1
            severity_counts[metadata["severity"]] += 1
            source_row["reason_counts"][code] += 1
            feed_row["reason_counts"][code] += 1

    return {
        "total_observations": total_items,
        "total_items": total_items,
        "clean_newsfeed_items": clean_newsfeed_items,
        "clean_newsfeed_rate": _rate(clean_newsfeed_items, total_items),
        "observable_issue_items": observable_issue_items,
        "observable_issue_rate": _rate(observable_issue_items, total_items),
        "warning_or_failure_items": warning_or_failure_items,
        "warning_or_failure_rate": _rate(warning_or_failure_items, total_items),
        "info_only_items": info_only_items,
        "newsfeed_excluded": newsfeed_excluded,
        "newsfeed_exclusion_rate": _rate(newsfeed_excluded, total_items),
        "reason_counts": _reason_rows(reason_counts, limit=limit),
        "stage_counts": _counter_rows(stage_counts, "stage", limit),
        "severity_counts": _counter_rows(severity_counts, "severity", limit),
        "recommended_action_counts": _counter_rows(
            action_counts,
            "recommended_action",
            limit,
        ),
        "top_sources": _entity_rows(source_rows, "source", limit),
        "top_feeds": _entity_rows(feed_rows, "feed", limit),
    }


def digest_item_cleanliness_row(item: Any) -> dict[str, Any]:
    return {
        "id": getattr(item, "id", ""),
        "title": getattr(item, "title", ""),
        "source": _nested_label(getattr(item, "source", None), "name", "id"),
        "feed": _nested_label(getattr(item, "feed", None), "name", "url"),
        "content_type": getattr(item, "content_type", "") or "unknown",
        "include_in_newsfeed": getattr(item, "include_in_newsfeed", True),
        "newsfeed_exclusion_reason": getattr(item, "newsfeed_exclusion_reason", None),
        "quality_status": getattr(item, "quality_status", "clean") or "clean",
        "flags": list(getattr(item, "quality_flags", []) or []),
    }


def feed_error_cleanliness_row(error: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "",
        "title": compact_whitespace(error.get("error")) or "Feed fetch failed",
        "source": compact_whitespace(error.get("source_name"))
        or compact_whitespace(error.get("source_id"))
        or "unknown",
        "feed": compact_whitespace(error.get("feed_name"))
        or compact_whitespace(error.get("feed_url"))
        or "unknown",
        "content_type": "feed_error",
        "include_in_newsfeed": True,
        "newsfeed_exclusion_reason": None,
        "quality_status": "warn",
        "flags": [
            {
                "code": "feed_fetch_failed",
                "severity": "warn",
            }
        ],
    }


def _metadata(
    issue: str,
    severity: str,
    stage: str,
    recommended_action: str,
) -> dict[str, str]:
    return {
        "issue": issue,
        "severity": severity if severity in _SEVERITY_RANK else "warn",
        "stage": stage,
        "recommended_action": recommended_action,
    }


def _default_severity(issue: str) -> str:
    if issue in {"content_type_filter_accepted", "rss_only_fallback_accepted"}:
        return "info"
    if issue in {"missing_title", "missing_link", "missing_source"}:
        return "error"
    return "warn"


def _row_status(row: dict[str, Any]) -> str:
    status = compact_whitespace(row.get("quality_status")) or compact_whitespace(row.get("status"))
    return status or "clean"


def _flags_for_row(row: dict[str, Any]) -> list[dict[str, str]]:
    flags = _normalized_flags(row.get("flags"))
    if flags:
        return flags

    if row.get("include_in_newsfeed") is False:
        reason = compact_whitespace(row.get("newsfeed_exclusion_reason"))
        if reason == "missing_rss_content":
            return [{"code": "missing_rss_content", "severity": "warn"}]
        if reason.startswith("unsupported_content_type:"):
            return [{"code": "content_type_filter_accepted", "severity": "info"}]
        return [{"code": "excluded_from_newsfeed", "severity": "warn"}]

    return []


def _normalized_flags(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    flags: list[dict[str, str]] = []
    for flag in value:
        if not isinstance(flag, dict):
            continue
        code = compact_whitespace(flag.get("code"))
        if not code:
            continue
        flags.append(
            {
                "code": code,
                "severity": compact_whitespace(flag.get("severity")) or _default_severity(code),
            }
        )
    return flags


def _entity_row(rows: dict[str, dict[str, Any]], label: str, label_key: str) -> dict[str, Any]:
    if label not in rows:
        rows[label] = {
            label_key: label,
            "total_items": 0,
            "clean_newsfeed_items": 0,
            "observable_issue_items": 0,
            "warning_or_failure_items": 0,
            "newsfeed_excluded": 0,
            "reason_counts": Counter(),
        }
    return rows[label]


def _entity_rows(
    rows: dict[str, dict[str, Any]], label_key: str, limit: int
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows.values():
        total_items = int(row["total_items"])
        reason_counts = row["reason_counts"]
        result.append(
            {
                label_key: row[label_key],
                "total_items": total_items,
                "clean_newsfeed_items": int(row["clean_newsfeed_items"]),
                "observable_issue_items": int(row["observable_issue_items"]),
                "observable_issue_rate": _rate(int(row["observable_issue_items"]), total_items),
                "warning_or_failure_items": int(row["warning_or_failure_items"]),
                "warning_or_failure_rate": _rate(
                    int(row["warning_or_failure_items"]),
                    total_items,
                ),
                "newsfeed_excluded": int(row["newsfeed_excluded"]),
                "top_reasons": _reason_rows(reason_counts, limit=5),
            }
        )
    result.sort(
        key=lambda row: (
            -int(row["warning_or_failure_items"]),
            -int(row["observable_issue_items"]),
            -int(row["newsfeed_excluded"]),
            str(row[label_key]),
        )
    )
    return result[:limit]


def _reason_rows(counter: Counter[str], *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for issue, count in counter.most_common(limit):
        metadata = issue_metadata(issue)
        rows.append(
            {
                "reason": issue,
                "count": count,
                "stage": metadata["stage"],
                "severity": metadata["severity"],
                "recommended_action": metadata["recommended_action"],
            }
        )
    return rows


def _counter_rows(counter: Counter[str], key_name: str, limit: int) -> list[dict[str, Any]]:
    return [{key_name: key, "count": count} for key, count in counter.most_common(limit)]


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _nested_label(value: Any, primary: str, fallback: str) -> str:
    primary_value = compact_whitespace(getattr(value, primary, None))
    fallback_value = compact_whitespace(getattr(value, fallback, None))
    return primary_value or fallback_value or "unknown"
