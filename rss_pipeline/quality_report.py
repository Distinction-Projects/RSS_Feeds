from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from .failure_taxonomy import classification_from_scrape_audit
from .llm_readiness import summarize_llm_readiness
from .models_digest import DigestItem
from .quality_diagnostics import summarize_item_quality
from .scrape_policy import accepted_scrape_fallback_for_digest_item


def _populated(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    if isinstance(value, dict):
        return bool(value)
    return value is not None


def _field_coverage(
    items: list[DigestItem],
    fields: list[tuple[str, Callable[[DigestItem], Any]]],
) -> list[dict[str, Any]]:
    total = len(items)
    rows: list[dict[str, Any]] = []
    for field_name, getter in fields:
        present = sum(1 for item in items if _populated(getter(item)))
        missing = max(total - present, 0)
        rows.append(
            {
                "field": field_name,
                "present": present,
                "missing": missing,
                "coverage_percent": (present / total * 100.0) if total else 0.0,
            }
        )
    return rows


def _coverage_by_field(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("field")): row for row in rows}


def _included_duplicate_canonical_urls(items: list[DigestItem]) -> int:
    counts: Counter[str] = Counter()
    for item in items:
        url = item.canonical_url().strip().lower()
        if url:
            counts[url] += 1
    return sum(count - 1 for count in counts.values() if count > 1)


def _top_counter_rows(
    counter: Counter[str], *, key_name: str, limit: int = 10
) -> list[dict[str, Any]]:
    return [{key_name: key, "count": count} for key, count in counter.most_common(limit)]


def _append_warning_once(warnings: list[str], warning: str) -> None:
    if warning not in warnings:
        warnings.append(warning)


def _row_count(rows: list[dict[str, Any]], key_name: str, key_value: str) -> int:
    for row in rows:
        if row.get(key_name) == key_value:
            value = row.get("count")
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return 0


def _scrape_failure_classification(
    item: DigestItem,
    scrape_audit: dict[str, Any],
) -> Any | None:
    if scrape_audit.get("status") != "failed" and not item.scrape_error:
        return None
    return classification_from_scrape_audit(
        scrape_audit,
        fallback_error=item.scrape_error,
    )


def build_digest_quality_report(
    *,
    run_id: str,
    generated_at: str,
    items: list[DigestItem],
    errors: list[dict[str, Any]],
    summary: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    included_articles = len(items)
    field_coverage = _field_coverage(
        items,
        [
            ("title", lambda item: item.title),
            ("canonical_url", lambda item: item.link),
            ("source", lambda item: item.source.name or item.source.id),
            ("published_at", lambda item: item.published),
            ("ai_summary", lambda item: item.ai_summary),
            ("tags", lambda item: item.ai_tags or item.topic_tags),
        ],
    )
    coverage_lookup = _coverage_by_field(field_coverage)

    included_clean = 0
    score_failed = 0
    newsfeed_excluded = 0
    rss_missing_content = 0
    unsupported_content_type = 0
    accepted_content_type_filter = 0
    failing_sources: Counter[str] = Counter()
    error_reasons: Counter[str] = Counter()
    scrape_failure_reasons: Counter[str] = Counter()
    accepted_fallback_reasons: Counter[str] = Counter()
    accepted_fallback_sources: Counter[str] = Counter()
    content_type_counts: Counter[str] = Counter()
    excluded_content_type_counts: Counter[str] = Counter()
    rss_only_fallback = 0
    accepted_rss_only_fallback = 0
    unresolved_scrape_failed = 0
    for item in items:
        content_type = item.content_type or "unknown"
        content_type_counts[content_type] += 1
        raw_scrape_audit = item.audit.get("scrape")
        scrape_audit = raw_scrape_audit if isinstance(raw_scrape_audit, dict) else {}
        raw_openai_audit = item.audit.get("openai")
        openai_audit = raw_openai_audit if isinstance(raw_openai_audit, dict) else {}
        if scrape_audit.get("status") == "succeeded" and not item.scrape_error:
            included_clean += 1
        if openai_audit.get("status") in {"failed", "missing_result"}:
            score_failed += 1
        if not item.include_in_newsfeed:
            newsfeed_excluded += 1
            excluded_content_type_counts[content_type] += 1
            if item.newsfeed_exclusion_reason == "missing_rss_content":
                rss_missing_content += 1
            if (item.newsfeed_exclusion_reason or "").startswith("unsupported_content_type:"):
                unsupported_content_type += 1
                accepted_content_type_filter += 1
        scrape_classification = _scrape_failure_classification(item, scrape_audit)
        if scrape_classification is not None:
            source_name = item.source.name or item.source.id or "unknown"
            accepted_fallback = accepted_scrape_fallback_for_digest_item(
                item,
                classification=scrape_classification,
            )
            if accepted_fallback is not None:
                accepted_rss_only_fallback += 1
                accepted_fallback_reasons[scrape_classification.code] += 1
                accepted_fallback_sources[source_name] += 1
            else:
                unresolved_scrape_failed += 1
                failing_sources[source_name] += 1
                scrape_failure_reasons[scrape_classification.code] += 1
                error_reasons[scrape_classification.code] += 1
            if item.summary:
                rss_only_fallback += 1

    for error in errors:
        source_name = str(error.get("source_name") or error.get("source_id") or "unknown").strip()
        if source_name:
            failing_sources[source_name] += 1
        reason = str(error.get("type") or error.get("error") or "unknown_error").strip()
        if reason:
            error_reasons[reason] += 1

    included_duplicate_urls = _included_duplicate_canonical_urls(items)
    duplicate_count = int(summary.get("in_run_duplicates") or 0) + int(
        summary.get("skipped_seen") or 0
    )
    scrape_failed = int(summary.get("scrape_failed") or 0)
    rss_missing_content = max(rss_missing_content, int(summary.get("rss_missing_content") or 0))
    newsfeed_excluded = max(newsfeed_excluded, int(summary.get("newsfeed_excluded") or 0))
    unsupported_content_type = max(
        unsupported_content_type,
        int(summary.get("unsupported_content_type") or 0),
    )
    accepted_content_type_filter = max(
        accepted_content_type_filter,
        int(summary.get("accepted_content_type_filter") or 0),
        unsupported_content_type,
    )
    item_quality = summarize_item_quality(items)
    llm_input = summarize_llm_readiness(items)
    llm_status_counts = llm_input.get("status_counts", {})
    llm_flag_counts = llm_input.get("flag_counts", [])
    llm_ready_items = max(
        int(summary.get("llm_ready_items") or 0),
        int(llm_status_counts.get("ready") or 0),
    )
    llm_review_items = max(
        int(summary.get("llm_review_items") or 0),
        int(llm_status_counts.get("review") or 0),
    )
    llm_excluded_items = max(
        int(summary.get("llm_excluded_items") or 0),
        int(llm_status_counts.get("exclude") or 0),
    )
    llm_rss_fallback_items = max(
        int(summary.get("llm_rss_fallback_items") or 0),
        int(llm_status_counts.get("rss_fallback") or 0),
    )
    llm_short_scraped_text = _row_count(llm_flag_counts, "flag", "short_scraped_text")
    llm_empty_scraped_text = _row_count(llm_flag_counts, "flag", "empty_scraped_text")

    blocking_issues: list[str] = []
    report_warnings = list(warnings)
    if included_articles == 0:
        blocking_issues.append("digest produced zero included articles")

    for field_name in ("title", "canonical_url", "source"):
        coverage = coverage_lookup.get(field_name, {})
        if float(coverage.get("coverage_percent") or 0.0) < 100.0:
            blocking_issues.append(f"{field_name} coverage is below 100%")

    published_coverage = float(
        coverage_lookup.get("published_at", {}).get("coverage_percent") or 0.0
    )
    if included_articles and published_coverage < 99.0:
        blocking_issues.append("published_at coverage is below 99%")

    if included_duplicate_urls > 0:
        blocking_issues.append("included articles contain duplicate canonical URLs")
    if duplicate_count > 0:
        _append_warning_once(
            report_warnings,
            f"{duplicate_count} duplicate article(s) were filtered before output",
        )
    if unresolved_scrape_failed > 0:
        top_scrape_reason = scrape_failure_reasons.most_common(1)
        suffix = f" (top reason: {top_scrape_reason[0][0]})" if top_scrape_reason else ""
        _append_warning_once(
            report_warnings,
            f"{unresolved_scrape_failed} article scrape(s) failed{suffix}",
        )
    if accepted_rss_only_fallback > 0:
        top_accepted_reason = accepted_fallback_reasons.most_common(1)
        suffix = f" (top reason: {top_accepted_reason[0][0]})" if top_accepted_reason else ""
        _append_warning_once(
            report_warnings,
            f"{accepted_rss_only_fallback} article scrape(s) used accepted RSS-only fallback{suffix}",
        )
    if llm_review_items > 0:
        _append_warning_once(
            report_warnings,
            f"{llm_review_items} article(s) need review before LLM judge input",
        )
    if llm_short_scraped_text > 0:
        _append_warning_once(
            report_warnings,
            f"{llm_short_scraped_text} article(s) have scraped text below the LLM judge threshold",
        )
    if llm_empty_scraped_text > 0:
        _append_warning_once(
            report_warnings,
            f"{llm_empty_scraped_text} article(s) scraped successfully but produced no usable text",
        )
    if score_failed > 0:
        _append_warning_once(
            report_warnings,
            f"{score_failed} article digest/scoring step(s) failed",
        )
    if rss_missing_content > 0:
        _append_warning_once(
            report_warnings,
            f"{rss_missing_content} article(s) missing RSS content were excluded from typical newsfeed output",
        )
    status = "fail" if blocking_issues else "warn" if report_warnings else "pass"
    return {
        "status": status,
        "publishable": status != "fail",
        "run_id": run_id,
        "generated_at": generated_at,
        "total_feed_items": int(summary.get("raw_fetched_items") or 0),
        "included_articles": included_articles,
        "typical_newsfeed_articles": max(included_articles - newsfeed_excluded, 0),
        "newsfeed_excluded": newsfeed_excluded,
        "rss_missing_content": rss_missing_content,
        "unsupported_content_type": unsupported_content_type,
        "accepted_content_type_filter": accepted_content_type_filter,
        "included_clean": included_clean,
        "included_partial": max(included_articles - included_clean, 0),
        "llm_ready_items": llm_ready_items,
        "llm_review_items": llm_review_items,
        "llm_excluded_items": llm_excluded_items,
        "llm_rss_fallback_items": llm_rss_fallback_items,
        "llm_short_scraped_text": llm_short_scraped_text,
        "llm_empty_scraped_text": llm_empty_scraped_text,
        "llm_input": llm_input,
        "rss_only_fallback": rss_only_fallback,
        "accepted_rss_only_fallback": accepted_rss_only_fallback,
        "duplicates": duplicate_count,
        "included_duplicate_canonical_urls": included_duplicate_urls,
        "scrape_failed": scrape_failed,
        "unresolved_scrape_failed": unresolved_scrape_failed,
        "extract_failed": 0,
        "score_failed": score_failed,
        "rejected_invalid": 0,
        "item_quality": item_quality,
        "field_coverage": field_coverage,
        "content_type_counts": _top_counter_rows(content_type_counts, key_name="content_type"),
        "excluded_content_type_counts": _top_counter_rows(
            excluded_content_type_counts,
            key_name="content_type",
        ),
        "top_failing_sources": _top_counter_rows(failing_sources, key_name="source"),
        "top_accepted_fallback_sources": _top_counter_rows(
            accepted_fallback_sources,
            key_name="source",
        ),
        "top_error_reasons": _top_counter_rows(error_reasons, key_name="reason"),
        "scrape_failure_reasons": _top_counter_rows(
            scrape_failure_reasons,
            key_name="reason",
        ),
        "accepted_rss_only_fallback_reasons": _top_counter_rows(
            accepted_fallback_reasons,
            key_name="reason",
        ),
        "blocking_issues": blocking_issues,
        "warnings": report_warnings,
    }
