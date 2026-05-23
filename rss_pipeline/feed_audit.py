from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Any

from .artifact_store import archive_json, write_json
from .config import FeedAuditConfig
from .errors import ConfigError
from .logging import StructuredRunLogger, get_logger
from .models_digest import DigestItem
from .normalization import compact_whitespace
from .pipeline_digest import (
    _article_event_payload,
    _feed_event_payload,
    fetch_feed_items,
    load_catalog,
    select_feeds,
)
from .quality_diagnostics import apply_item_quality_audit
from .workflow_runtime import RunContext, command_line

logger = get_logger(__name__)


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


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _item_feed_label(item: DigestItem) -> str:
    return item.feed.name or item.feed.url or "unknown"


def _item_source_label(item: DigestItem) -> str:
    return item.source.name or item.source.id or "unknown"


def _feed_error_payload(
    feed: dict[str, Any], exc: Exception, duration_seconds: float
) -> dict[str, Any]:
    return {
        "stage": "feed_fetch",
        "source_id": feed.get("source_id"),
        "source_name": feed.get("source_name"),
        "feed_name": feed.get("feed_name"),
        "feed_url": feed.get("feed_url"),
        "type": type(exc).__name__,
        "error": str(exc),
        "duration_seconds": duration_seconds,
    }


def _quality_gate_metrics(
    *,
    summary: dict[str, Any],
    issue_counts: Counter[str],
    content_type_counts: Counter[str],
) -> dict[str, int]:
    return {
        "feed_fetch_failed": _safe_int(summary.get("feed_fetch_failed")),
        "missing_rss_content_items": _safe_int(summary.get("rss_missing_content")),
        "unknown_content_type_items": content_type_counts.get("unknown", 0),
        "unsupported_content_type_items": issue_counts.get("unsupported_content_type", 0),
        "accepted_content_type_filter_items": issue_counts.get(
            "content_type_filter_accepted",
            0,
        ),
    }


def evaluate_feed_audit_gates(
    report: dict[str, Any],
    *,
    max_feed_fetch_failures: int | None = None,
    max_missing_rss_content: int | None = None,
    max_unknown_content_types: int | None = None,
    max_unsupported_content_types: int | None = None,
    max_accepted_content_type_filters: int | None = None,
) -> dict[str, Any]:
    raw_metrics = report.get("quality_gate_metrics")
    metric_values = raw_metrics if isinstance(raw_metrics, dict) else {}
    checks = {
        "feed_fetch_failed": max_feed_fetch_failures,
        "missing_rss_content_items": max_missing_rss_content,
        "unknown_content_type_items": max_unknown_content_types,
        "unsupported_content_type_items": max_unsupported_content_types,
        "accepted_content_type_filter_items": max_accepted_content_type_filters,
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
            "feed_fetch_failed": _safe_int(metric_values.get("feed_fetch_failed")),
            "missing_rss_content_items": _safe_int(metric_values.get("missing_rss_content_items")),
            "unknown_content_type_items": _safe_int(
                metric_values.get("unknown_content_type_items")
            ),
            "unsupported_content_type_items": _safe_int(
                metric_values.get("unsupported_content_type_items")
            ),
            "accepted_content_type_filter_items": _safe_int(
                metric_values.get("accepted_content_type_filter_items")
            ),
        },
        "violations": violations,
    }


def run_feed_audit(
    config: FeedAuditConfig,
    *,
    repo_root: Path,
    limit: int = 10,
) -> dict[str, Any]:
    context = RunContext.start("feed-audit")
    catalog_path = config.catalog if config.catalog.is_absolute() else repo_root / config.catalog
    output_path = config.output if config.output.is_absolute() else repo_root / config.output
    run_log_dir = (
        config.run_log_dir if config.run_log_dir.is_absolute() else repo_root / config.run_log_dir
    )
    run_log_path = run_log_dir / f"{context.run_id}.jsonl"

    with StructuredRunLogger(run_log_path, run_id=context.run_id) as audit_logger:
        audit_logger.event(
            "run_started",
            catalog_path=str(catalog_path),
            output_path=str(output_path),
            max_sources=config.max_sources,
            feeds_per_source=config.feeds_per_source,
            max_items_per_feed=config.max_items_per_feed,
            timeout_seconds=config.timeout_seconds,
        )
        catalog = load_catalog(catalog_path)
        feeds = select_feeds(
            catalog,
            config.max_sources,
            config.feeds_per_source,
            config.source_ids,
        )
        if not feeds:
            audit_logger.event(
                "run_failed",
                error="No feeds selected; check catalog filters and enabled flags.",
                duration_seconds=context.duration_seconds,
            )
            raise ConfigError("No feeds selected; check catalog filters and enabled flags.")

        logger.info(
            "Feed audit selected %s sources across %s feeds.",
            len({feed["source_id"] for feed in feeds}),
            len(feeds),
        )

        items: list[DigestItem] = []
        feed_errors: list[dict[str, Any]] = []
        feed_success = 0
        raw_fetched_items = 0

        for feed in feeds:
            feed_started_at = time.monotonic()
            audit_logger.event("feed_fetch_started", **_feed_event_payload(feed))
            try:
                feed_items = fetch_feed_items(
                    feed=feed,
                    max_items=config.max_items_per_feed,
                    timeout_seconds=config.timeout_seconds,
                    user_agent=config.user_agent,
                )
            except Exception as exc:  # noqa: BLE001
                duration_seconds = round(time.monotonic() - feed_started_at, 3)
                error = _feed_error_payload(feed, exc, duration_seconds)
                feed_errors.append(error)
                audit_logger.event(
                    "feed_fetch_failed",
                    **_feed_event_payload(feed),
                    duration_seconds=duration_seconds,
                    exception_type=type(exc).__name__,
                    error=str(exc),
                )
                continue

            feed_success += 1
            raw_fetched_items += len(feed_items)
            audit_logger.event(
                "feed_fetch_succeeded",
                **_feed_event_payload(feed),
                duration_seconds=round(time.monotonic() - feed_started_at, 3),
                item_count=len(feed_items),
            )

            for item in feed_items:
                apply_item_quality_audit(item)
                items.append(item)
                audit_logger.event(
                    "feed_item_classified",
                    **_article_event_payload(item),
                    quality_status=item.quality_status,
                    quality_flags=[flag.get("code") for flag in item.quality_flags],
                    quality_flag_count=len(item.quality_flags),
                )

        report = build_feed_audit_report(
            run_id=context.run_id,
            generated_at=context.generated_at,
            duration_seconds=context.duration_seconds,
            catalog_path=config.catalog,
            resolved_catalog_path=catalog_path,
            output_path=output_path,
            feeds=feeds,
            items=items,
            feed_errors=feed_errors,
            feed_success=feed_success,
            raw_fetched_items=raw_fetched_items,
            request={
                "catalog_path": str(config.catalog),
                "max_sources": config.max_sources,
                "feeds_per_source": config.feeds_per_source,
                "max_items_per_feed": config.max_items_per_feed,
                "timeout_seconds": config.timeout_seconds,
                "source_ids": list(config.source_ids),
                "run_log_dir": str(config.run_log_dir),
            },
            run_log_path=run_log_path,
            limit=limit,
        )
        audit_logger.event(
            "feed_quality_summary",
            status=report["status"],
            **report["summary"],
        )
        audit_logger.event("run_completed", duration_seconds=context.duration_seconds)

    write_json(output_path, report)
    if config.archive_history_dir is not None:
        archive_dir = (
            config.archive_history_dir
            if config.archive_history_dir.is_absolute()
            else repo_root / config.archive_history_dir
        )
        archive_path = archive_json(report, output_path, archive_dir)
        report["history_archive"] = str(archive_path)
        write_json(output_path, report)
    return report


def build_feed_audit_report(
    *,
    run_id: str,
    generated_at: str,
    duration_seconds: float,
    catalog_path: Path,
    resolved_catalog_path: Path,
    output_path: Path,
    feeds: list[dict[str, Any]],
    items: list[DigestItem],
    feed_errors: list[dict[str, Any]],
    feed_success: int,
    raw_fetched_items: int,
    request: dict[str, Any],
    run_log_path: Path,
    limit: int = 10,
) -> dict[str, Any]:
    status_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    source_item_counts: Counter[str] = Counter()
    feed_item_counts: Counter[str] = Counter()
    source_issue_counts: Counter[tuple[str, str]] = Counter()
    feed_issue_counts: Counter[tuple[str, str]] = Counter()
    content_type_counts: Counter[str] = Counter()
    exclusion_reason_counts: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []

    typical_newsfeed_items = 0
    newsfeed_excluded = 0
    rss_missing_content = 0
    accepted_content_type_filter = 0

    for item in items:
        source = _item_source_label(item)
        feed = _item_feed_label(item)
        content_type = item.content_type or "unknown"
        status_counts[item.quality_status or "clean"] += 1
        source_item_counts[source] += 1
        feed_item_counts[feed] += 1
        content_type_counts[content_type] += 1

        if item.include_in_newsfeed:
            typical_newsfeed_items += 1
        else:
            newsfeed_excluded += 1
            reason = item.newsfeed_exclusion_reason or "newsfeed_excluded"
            exclusion_reason_counts[reason] += 1
            if reason == "missing_rss_content":
                rss_missing_content += 1
            if reason.startswith("unsupported_content_type:"):
                accepted_content_type_filter += 1

        for flag in item.quality_flags:
            code = compact_whitespace(flag.get("code")) or "unknown_quality_issue"
            severity = compact_whitespace(flag.get("severity")) or "warn"
            issue_counts[code] += 1
            severity_counts[severity] += 1
            source_issue_counts[(source, code)] += 1
            feed_issue_counts[(feed, code)] += 1

        if item.quality_flags and len(examples) < limit:
            examples.append(
                {
                    "id": item.id,
                    "title": item.title,
                    "source": source,
                    "feed": feed,
                    "content_type": content_type,
                    "include_in_newsfeed": item.include_in_newsfeed,
                    "newsfeed_exclusion_reason": item.newsfeed_exclusion_reason,
                    "quality_status": item.quality_status,
                    "flags": list(item.quality_flags),
                }
            )

    for error in feed_errors:
        source = compact_whitespace(error.get("source_name")) or compact_whitespace(
            error.get("source_id")
        )
        feed = compact_whitespace(error.get("feed_name")) or compact_whitespace(
            error.get("feed_url")
        )
        source_issue_counts[(source or "unknown", "feed_fetch_failed")] += 1
        feed_issue_counts[(feed or "unknown", "feed_fetch_failed")] += 1
        issue_counts["feed_fetch_failed"] += 1
        severity_counts["warn"] += 1

    summary = {
        "selected_sources": len({feed["source_id"] for feed in feeds}),
        "selected_feeds": len(feeds),
        "feed_fetch_attempts": len(feeds),
        "feed_fetch_succeeded": feed_success,
        "feed_fetch_failed": len(feed_errors),
        "raw_fetched_items": raw_fetched_items,
        "typical_newsfeed_items": typical_newsfeed_items,
        "newsfeed_excluded": newsfeed_excluded,
        "rss_missing_content": rss_missing_content,
        "accepted_content_type_filter": accepted_content_type_filter,
        "quality_clean_items": status_counts.get("clean", 0),
        "quality_warn_items": status_counts.get("warn", 0),
        "quality_fail_items": status_counts.get("fail", 0),
    }
    warnings: list[str] = []
    blocking_issues: list[str] = []
    if feed_success == 0:
        blocking_issues.append("no selected feeds fetched successfully")
    if raw_fetched_items == 0:
        blocking_issues.append("feed audit produced zero RSS items")
    if status_counts.get("fail", 0) > 0:
        blocking_issues.append("RSS items failed required field quality checks")
    if feed_errors:
        warnings.append(f"{len(feed_errors)} feed fetch(es) failed")
    if rss_missing_content:
        warnings.append(f"{rss_missing_content} RSS item(s) had no content text")
    if status_counts.get("warn", 0) > 0:
        warnings.append(f"{status_counts['warn']} RSS item(s) had warning-level quality flags")

    status = "fail" if blocking_issues else "warn" if warnings else "pass"
    return {
        "schema_version": "1.0",
        "status": status,
        "run": {
            "id": run_id,
            "generated_at": generated_at,
            "duration_seconds": duration_seconds,
            "command": command_line(),
        },
        "generated_at": generated_at,
        "request": request,
        "sources": {
            "selected_count": len(feeds),
            "selected": feeds,
        },
        "summary": summary,
        "quality_gate_metrics": _quality_gate_metrics(
            summary=summary,
            issue_counts=issue_counts,
            content_type_counts=content_type_counts,
        ),
        "status_counts": dict(status_counts),
        "severity_counts": dict(severity_counts),
        "issue_counts": _counter_rows(issue_counts, "issue", limit),
        "content_type_counts": _counter_rows(content_type_counts, "content_type", limit),
        "exclusion_reason_counts": _counter_rows(exclusion_reason_counts, "reason", limit),
        "top_sources": _counter_rows(source_item_counts, "source", limit),
        "top_feeds": _counter_rows(feed_item_counts, "feed", limit),
        "top_source_issues": _pair_counter_rows(source_issue_counts, "source", "issue", limit),
        "top_feed_issues": _pair_counter_rows(feed_issue_counts, "feed", "issue", limit),
        "examples": examples,
        "feed_fetch_errors": feed_errors[:limit],
        "blocking_issues": blocking_issues,
        "warnings": warnings,
        "audit": {
            "catalog_path": str(resolved_catalog_path),
            "output_path": str(output_path),
            "run_log": str(run_log_path),
            "requested_catalog_path": str(catalog_path),
        },
    }
