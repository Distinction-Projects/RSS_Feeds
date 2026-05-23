from __future__ import annotations

import hashlib
import html
import json
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import feedparser

import scrape_experiment_links as scrape_links
from rss_pipeline.prompt_builder import build_digest_messages

from .artifact_store import archive_json, export_prompt_audit_rows, write_json
from .cache_sqlite import SQLiteOpenAICache
from .config import DigestBuildConfig
from .content_classifier import classify_story_content_type
from .env import resolve_env_value
from .errors import ConfigError
from .failure_taxonomy import classify_scrape_failure
from .llm_readiness import apply_items_llm_readiness
from .logging import StructuredRunLogger, get_logger
from .models_digest import (
    CacheMeta,
    DigestDocument,
    DigestItem,
    FeedRef,
    OpenAIMeta,
    RunMeta,
    SourceRef,
)
from .normalization import normalize_tags, normalize_title, normalize_url
from .openai_client import OpenAIService
from .quality_diagnostics import apply_items_quality_audit
from .quality_report import build_digest_quality_report
from .schema_validation import validate_digest_payload, validation_summary
from .scrape_policy import (
    accepted_scrape_fallback_for_digest_item,
    scrape_fallback_policy_for_source,
)
from .workflow_runtime import RunContext, command_line

ENV_OPENAI_KEY = "OPENAI_API_KEY"
ENV_OPENAI_MODEL = "OPENAI_MODEL"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
VOLUME_WARNING_THRESHOLD = 60
SCRAPE_SUCCESS_WARNING_RATE = 0.5

logger = get_logger(__name__)


def _article_event_payload(item: DigestItem) -> dict[str, Any]:
    return {
        "article_id": item.id,
        "article_title": item.title,
        "article_url": item.link,
        "content_type": item.content_type,
        "include_in_newsfeed": item.include_in_newsfeed,
        "newsfeed_exclusion_reason": item.newsfeed_exclusion_reason,
        "source_id": item.source.id,
        "source_name": item.source.name,
        "feed_name": item.feed.name,
        "feed_url": item.feed.url,
    }


def _feed_event_payload(feed: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": feed.get("source_id"),
        "source_name": feed.get("source_name"),
        "feed_name": feed.get("feed_name"),
        "feed_url": feed.get("feed_url"),
    }


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    parser = _HTMLStripper()
    parser.feed(value)
    return html.unescape(parser.get_text()).strip()


def compact_text(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(limit - 3, 0)] + "..."


def rss_entry_content_text(entry: Any) -> str:
    parts: list[str] = []
    for key in ("summary", "description"):
        value = entry.get(key)
        if value:
            parts.append(strip_html(str(value)))

    content_values = entry.get("content") or []
    if isinstance(content_values, list):
        for content_value in content_values:
            if isinstance(content_value, dict):
                value = content_value.get("value")
                if value:
                    parts.append(strip_html(str(value)))

    deduped_parts: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = " ".join(part.split())
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped_parts.append(cleaned)
    return " ".join(deduped_parts)


def load_catalog(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Catalog must be an object: {path}")
    return payload


def select_feeds(
    catalog: dict[str, Any],
    max_sources: int,
    feeds_per_source: int,
    source_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    feeds: list[dict[str, Any]] = []
    selected_sources = 0
    for source in catalog.get("sources") or []:
        if not isinstance(source, dict):
            continue
        if source.get("enabled") is False:
            continue

        source_id = str(source.get("id") or "").strip()
        if source_ids and source_id not in source_ids:
            continue

        feed_count = 0
        for feed in source.get("feeds") or []:
            if not isinstance(feed, dict):
                continue
            if feed.get("enabled") is False:
                continue
            if feed_count >= feeds_per_source:
                break

            feed_url = str(feed.get("url") or "").strip()
            if not feed_url:
                continue
            topic_tags = normalize_tags(feed.get("topic_tags") or [])
            scrape_fallback_policy = scrape_fallback_policy_for_source(
                source_id=source_id,
                source_name=source.get("name") or source_id,
                configured_policy=source.get("scrape_policy")
                if isinstance(source.get("scrape_policy"), dict)
                else None,
            )
            feeds.append(
                {
                    "source_id": source_id,
                    "source_name": str(source.get("name") or source_id or "Unknown Source"),
                    "feed_name": str(feed.get("name") or feed_url),
                    "feed_url": feed_url,
                    "topic_tags": topic_tags,
                    "scrape_fallback_policy": scrape_fallback_policy,
                }
            )
            feed_count += 1

        if feed_count == 0:
            continue

        selected_sources += 1
        if selected_sources >= max_sources:
            break

    return feeds


def item_id(source_id: str, link: str, title: str) -> str:
    digest = hashlib.sha1(f"{source_id}:{link or title}".encode()).hexdigest()
    return digest[:12]


def normalize_link_for_dedupe(link: str) -> str:
    return normalize_url(link)


def dedupe_keys_for_item(item: DigestItem) -> set[str]:
    keys: set[str] = set()

    item_id_value = item.id.strip()
    if item_id_value:
        keys.add(f"id:{item_id_value}")

    normalized_link = normalize_link_for_dedupe(item.link)
    if normalized_link:
        keys.add(f"link:{normalized_link}")

    title = normalize_title(item.title).casefold()
    source_id = item.source.id.strip()
    if title:
        keys.add(f"title:{source_id}:{title}")

    return keys


def dedupe_keys_from_item_payload(payload: dict[str, Any]) -> set[str]:
    keys: set[str] = set()

    item_id_value = str(payload.get("id") or "").strip()
    if item_id_value:
        keys.add(f"id:{item_id_value}")

    normalized_link = normalize_link_for_dedupe(str(payload.get("link") or ""))
    if normalized_link:
        keys.add(f"link:{normalized_link}")

    source: dict[str, Any] = {}
    source_raw = payload.get("source")
    if isinstance(source_raw, dict):
        source = source_raw
    source_id = str(source.get("id") or payload.get("source_id") or "").strip()

    title = normalize_title(payload.get("title")).casefold()
    if title:
        keys.add(f"title:{source_id}:{title}")

    return keys


def _load_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def collect_seen_keys(
    *,
    output_path: Path,
    history_dir: Path,
) -> tuple[set[str], dict[str, int]]:
    seen: set[str] = set()
    current_items = 0
    history_items = 0
    history_files = 0

    current_payload = _load_json_object(output_path)
    if isinstance(current_payload, dict):
        raw_items = current_payload.get("items")
        if isinstance(raw_items, list):
            for item in raw_items:
                if isinstance(item, dict):
                    seen.update(dedupe_keys_from_item_payload(item))
                    current_items += 1

    for path in sorted(history_dir.glob("rss_openai_daily_*.json")):
        payload = _load_json_object(path)
        if not isinstance(payload, dict):
            continue
        history_files += 1
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if isinstance(item, dict):
                seen.update(dedupe_keys_from_item_payload(item))
                history_items += 1

    return seen, {
        "loaded_keys": len(seen),
        "current_items": current_items,
        "history_items": history_items,
        "history_files": history_files,
    }


def fetch_feed_items(
    feed: dict[str, Any],
    max_items: int,
    timeout_seconds: int,
    user_agent: str,
) -> list[DigestItem]:
    request = urllib.request.Request(feed["feed_url"], headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        content = response.read()

    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"Feed parse error: {parsed.bozo_exception}")

    items: list[DigestItem] = []
    for entry in parsed.entries[:max_items]:
        title = compact_text(
            normalize_title(entry.get("title"), source_name=feed["source_name"]),
            200,
        )
        link = normalize_url(entry.get("link"))
        rss_content = rss_entry_content_text(entry)
        summary = compact_text(rss_content, 500)
        published = str(entry.get("published") or entry.get("updated") or "").strip()
        topic_tags = normalize_tags(feed.get("topic_tags") or [])
        content_classification = classify_story_content_type(
            title=title,
            link=link,
            feed_name=feed["feed_name"],
            source_name=feed["source_name"],
            topic_tags=topic_tags,
            rss_content=rss_content,
        )
        has_rss_content = content_classification.content_type != "missing_content"
        include_in_newsfeed = content_classification.newslens_eligible
        if content_classification.content_type == "missing_content":
            exclusion_reason = "missing_rss_content"
        elif not content_classification.newslens_eligible:
            exclusion_reason = f"unsupported_content_type:{content_classification.content_type}"
        else:
            exclusion_reason = None

        source = SourceRef(id=feed["source_id"], name=feed["source_name"])
        feed_ref = FeedRef(name=feed["feed_name"], url=feed["feed_url"])
        audit: dict[str, Any] = {
            "provenance": {
                "source_id": source.id,
                "source_name": source.name,
                "feed_name": feed_ref.name,
                "feed_url": feed_ref.url,
            },
            "content": {
                "status": "present" if has_rss_content else "missing",
                "source": "rss_feed",
                "has_rss_content": has_rss_content,
                "rss_content_chars": len(rss_content.strip()),
                "content_type": content_classification.content_type,
                "content_type_confidence": content_classification.confidence,
                "content_type_reason": content_classification.reason,
                "content_type_signals": content_classification.matched_signals,
                "newslens_eligible": content_classification.newslens_eligible,
                "exclude_from_newsfeed": not include_in_newsfeed,
                "reason": exclusion_reason,
                "message": None
                if include_in_newsfeed
                else (
                    "RSS entry is not eligible for normal NewsLens output; "
                    f"reason={exclusion_reason}."
                ),
            },
        }
        scrape_fallback_policy = feed.get("scrape_fallback_policy")
        if isinstance(scrape_fallback_policy, dict):
            audit["source_policy"] = {"scrape_fallback": scrape_fallback_policy}

        item = DigestItem(
            id=item_id(feed["source_id"], link, title),
            title=title,
            link=link,
            summary=summary,
            published=published,
            source=source,
            feed=feed_ref,
            topic_tags=topic_tags,
            content_type=content_classification.content_type,
            content_type_confidence=content_classification.confidence,
            content_type_reason=content_classification.reason,
            include_in_newsfeed=include_in_newsfeed,
            newsfeed_exclusion_reason=exclusion_reason,
            audit=audit,
        )
        items.append(item)
    return items


def enrich_items_with_scrape(
    items: list[DigestItem],
    *,
    limit: int | None,
    timeout_seconds: float,
    sleep_seconds: float,
    user_agent: str,
    audit_logger: StructuredRunLogger | None = None,
) -> dict[str, Any]:
    attempts = 0
    success = 0
    failed = 0
    skipped = 0
    accepted_fallback = 0

    for item in items:
        item.scraped = None
        item.scrape_error = None

    for item in items:
        if limit is not None and attempts >= limit:
            skipped += 1
            item.audit.setdefault("scrape", {})
            item.audit["scrape"].update(
                {
                    "status": "skipped",
                    "reason": "scrape_limit",
                    "attempted": False,
                }
            )
            if audit_logger is not None:
                audit_logger.event(
                    "article_fetch_skipped",
                    **_article_event_payload(item),
                    reason="scrape_limit",
                    outcome_state="included_partial",
                )
            continue

        link = item.link.strip()
        if not link:
            item.scrape_error = "missing link"
            classification = classify_scrape_failure(item.scrape_error)
            failed += 1
            attempts += 1
            item.audit.setdefault("scrape", {})
            item.audit["scrape"].update(
                {
                    "status": "failed",
                    "reason": classification.code,
                    "category": classification.category,
                    "attempted": True,
                    "error": item.scrape_error,
                    "retryable": classification.retryable,
                    "source_action": classification.source_action,
                    "failure_taxonomy": classification.to_dict(),
                }
            )
            if audit_logger is not None:
                audit_logger.event(
                    "article_fetch_failed",
                    **_article_event_payload(item),
                    reason=classification.code,
                    category=classification.category,
                    error=item.scrape_error,
                    retryable=classification.retryable,
                    source_action=classification.source_action,
                    outcome_state="scrape_failed",
                )
            continue

        if audit_logger is not None:
            audit_logger.event(
                "article_fetch_started",
                **_article_event_payload(item),
                timeout_seconds=timeout_seconds,
            )
        article_started_at = time.monotonic()
        try:
            scraped = scrape_links.scrape_article(
                link,
                timeout_seconds=timeout_seconds,
                user_agent=user_agent,
            )
            item.scraped = scraped.to_dict()
            duration_seconds = round(time.monotonic() - article_started_at, 3)
            item.audit.setdefault("scrape", {})
            item.audit["scrape"].update(
                {
                    "status": "succeeded",
                    "attempted": True,
                    "duration_seconds": duration_seconds,
                    "status_code": item.scraped.get("status_code"),
                    "final_url": item.scraped.get("final_url"),
                }
            )
            if audit_logger is not None:
                audit_logger.event(
                    "article_fetch_succeeded",
                    **_article_event_payload(item),
                    duration_seconds=duration_seconds,
                    status_code=item.scraped.get("status_code"),
                    final_url=item.scraped.get("final_url"),
                    outcome_state="included_clean",
                )
            success += 1
        except Exception as exc:  # noqa: BLE001
            duration_seconds = round(time.monotonic() - article_started_at, 3)
            item.scrape_error = str(exc)
            classification = classify_scrape_failure(exc)
            fallback_audit = accepted_scrape_fallback_for_digest_item(
                item,
                classification=classification,
            )
            if fallback_audit is not None:
                accepted_fallback += 1
            item.audit.setdefault("scrape", {})
            item.audit["scrape"].update(
                {
                    "status": "failed",
                    "reason": classification.code,
                    "category": classification.category,
                    "attempted": True,
                    "duration_seconds": duration_seconds,
                    "exception_type": type(exc).__name__,
                    "error": item.scrape_error,
                    "http_status": classification.http_status,
                    "retryable": classification.retryable,
                    "source_action": classification.source_action,
                    "failure_taxonomy": classification.to_dict(),
                }
            )
            if fallback_audit is not None:
                item.audit["scrape"]["accepted_fallback"] = fallback_audit
            if audit_logger is not None:
                audit_logger.event(
                    "article_fetch_failed",
                    **_article_event_payload(item),
                    duration_seconds=duration_seconds,
                    reason=classification.code,
                    category=classification.category,
                    exception_type=type(exc).__name__,
                    http_status=classification.http_status,
                    error=item.scrape_error,
                    retryable=classification.retryable,
                    source_action=classification.source_action,
                    accepted_fallback=fallback_audit is not None,
                    fallback_reason=fallback_audit.get("reason") if fallback_audit else None,
                    outcome_state="included_rss_only_fallback"
                    if fallback_audit is not None
                    else "scrape_failed",
                )
                if fallback_audit is not None:
                    audit_logger.event(
                        "article_fetch_fallback_accepted",
                        **_article_event_payload(item),
                        reason=fallback_audit["reason"],
                        failure_code=fallback_audit["failure_code"],
                        policy_id=fallback_audit["policy_id"],
                        policy_source=fallback_audit["policy_source"],
                        mode=fallback_audit["mode"],
                        outcome_state="included_rss_only_fallback",
                    )
            failed += 1

        attempts += 1
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return {
        "enabled": True,
        "attempts": attempts,
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "accepted_fallback": accepted_fallback,
        "limit": limit,
        "timeout_seconds": timeout_seconds,
        "sleep_seconds": sleep_seconds,
    }


def _openai_model(config: DigestBuildConfig, repo_root: Path) -> str:
    if config.openai_model:
        return config.openai_model
    return (
        resolve_env_value(ENV_OPENAI_MODEL, base_dir=repo_root, env_paths=(".env",))
        or DEFAULT_OPENAI_MODEL
    )


def _chunk_items(items: list[DigestItem], batch_size: int) -> list[list[DigestItem]]:
    if batch_size <= 0:
        return [items]
    return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]


def _merge_usage_totals(
    current: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not incoming:
        return current

    merged: dict[str, Any] = dict(current or {})
    for key, value in incoming.items():
        if isinstance(value, dict):
            nested_current = merged.get(key)
            merged[key] = _merge_usage_totals(
                nested_current if isinstance(nested_current, dict) else {},
                value,
            )
            continue

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            previous = merged.get(key, 0)
            merged[key] = previous + value if isinstance(previous, (int, float)) else value
            continue

        if key not in merged:
            merged[key] = value
    return merged


def _classify_openai_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "timeout" in message or "timed out" in message or "readtimeout" in message:
        return "timeout"
    return "openai_error"


def _openai_backoff_seconds(base_seconds: float, retry_number: int) -> float:
    return max(base_seconds * retry_number, 0.0)


def _warning_messages(
    *,
    new_items: int,
    rss_missing_content: int,
    unsupported_content_type: int,
    scrape_stats: dict[str, Any],
    openai_batch_stats: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []

    if new_items == 0:
        warnings.append("digest produced zero new items")
    if new_items > VOLUME_WARNING_THRESHOLD:
        warnings.append(
            f"digest produced {new_items} new items, above warning threshold {VOLUME_WARNING_THRESHOLD}"
        )
    if rss_missing_content > 0:
        warnings.append(
            f"{rss_missing_content} article(s) missing RSS content were excluded from typical newsfeed output"
        )
    if unsupported_content_type > 0:
        warnings.append(
            f"{unsupported_content_type} article(s) with unsupported content type were excluded from NewsLens output"
        )

    scrape_attempts = int(scrape_stats.get("attempts") or 0)
    scrape_success = int(scrape_stats.get("success") or 0)
    if scrape_attempts > 0:
        scrape_success_rate = scrape_success / scrape_attempts
        if scrape_success_rate < SCRAPE_SUCCESS_WARNING_RATE:
            warnings.append(
                f"scrape success rate {scrape_success_rate:.0%} is below warning threshold "
                f"{SCRAPE_SUCCESS_WARNING_RATE:.0%}"
            )

    retry_attempts = int(openai_batch_stats.get("retry_attempts") or 0)
    executed_batches = int(openai_batch_stats.get("executed_batches") or 0)
    if retry_attempts > max(2, executed_batches):
        warnings.append(
            f"OpenAI retry volume is elevated ({retry_attempts} retries across {executed_batches} batches)"
        )
    if int(openai_batch_stats.get("failed_batches") or 0) > 0:
        warnings.append(
            f"{int(openai_batch_stats.get('failed_batches') or 0)} OpenAI digest batch(es) failed permanently"
        )

    return warnings


def build_digest(config: DigestBuildConfig, *, repo_root: Path) -> dict[str, Any]:
    context = RunContext.start("digest")

    catalog_path = config.catalog if config.catalog.is_absolute() else repo_root / config.catalog
    output_path = config.output if config.output.is_absolute() else repo_root / config.output
    archive_dir = (
        config.archive_dir if config.archive_dir.is_absolute() else repo_root / config.archive_dir
    )
    cache_path = (
        config.cache_path if config.cache_path.is_absolute() else repo_root / config.cache_path
    )
    prompt_audit_dir = (
        config.prompt_audit_dir
        if config.prompt_audit_dir.is_absolute()
        else repo_root / config.prompt_audit_dir
    )
    run_log_dir = (
        config.run_log_dir if config.run_log_dir.is_absolute() else repo_root / config.run_log_dir
    )
    run_log_path = run_log_dir / f"{context.run_id}.jsonl"
    audit_logger = StructuredRunLogger(run_log_path, run_id=context.run_id)
    audit_logger.event(
        "run_started",
        catalog_path=str(catalog_path),
        output_path=str(output_path),
        archive_dir=str(archive_dir),
        max_sources=config.max_sources,
        feeds_per_source=config.feeds_per_source,
        max_items_per_feed=config.max_items_per_feed,
        skip_seen_items=config.skip_seen_items,
        scrape_enabled=config.scrape_enabled,
        openai_enabled=config.openai_enabled,
        feed_user_agent=config.feed_user_agent,
    )

    catalog = load_catalog(catalog_path)
    feeds = select_feeds(catalog, config.max_sources, config.feeds_per_source, config.source_ids)
    if not feeds:
        audit_logger.event(
            "run_failed",
            error="No feeds selected; check catalog filters and enabled flags.",
            duration_seconds=context.duration_seconds,
        )
        audit_logger.close()
        raise ConfigError("No feeds selected; check catalog filters and enabled flags.")
    logger.info(
        "Digest selected %s sources across %s feeds (max_sources=%s feeds_per_source=%s max_items_per_feed=%s).",
        len({feed["source_id"] for feed in feeds}),
        len(feeds),
        config.max_sources,
        config.feeds_per_source,
        config.max_items_per_feed,
    )

    items: list[DigestItem] = []
    errors: list[dict[str, Any]] = []
    seen_in_run: set[str] = set()
    selected_source_ids = {feed["source_id"] for feed in feeds}
    raw_fetched_items = 0
    duplicate_in_run = 0
    for feed in feeds:
        feed_started_at = time.monotonic()
        audit_logger.event("feed_fetch_started", **_feed_event_payload(feed))
        try:
            feed_items = fetch_feed_items(
                feed=feed,
                max_items=config.max_items_per_feed,
                timeout_seconds=config.timeout_seconds,
                user_agent=config.feed_user_agent,
            )
        except Exception as exc:  # noqa: BLE001
            duration_seconds = round(time.monotonic() - feed_started_at, 3)
            errors.append(
                {
                    "stage": "feed_fetch",
                    "feed_url": feed["feed_url"],
                    "source_id": feed["source_id"],
                    "type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            audit_logger.event(
                "feed_fetch_failed",
                **_feed_event_payload(feed),
                duration_seconds=duration_seconds,
                exception_type=type(exc).__name__,
                error=str(exc),
            )
            continue

        audit_logger.event(
            "feed_fetch_succeeded",
            **_feed_event_payload(feed),
            duration_seconds=round(time.monotonic() - feed_started_at, 3),
            item_count=len(feed_items),
        )
        raw_fetched_items += len(feed_items)
        for item in feed_items:
            audit_logger.event("article_seen", **_article_event_payload(item))
            dedupe_key = item.link or item.title or item.id
            if dedupe_key in seen_in_run:
                duplicate_in_run += 1
                audit_logger.event(
                    "article_deduped",
                    **_article_event_payload(item),
                    reason="in_run_duplicate",
                    dedupe_key=dedupe_key,
                    outcome_state="duplicate",
                )
                continue
            seen_in_run.add(dedupe_key)
            item.fetched_at = context.generated_at
            items.append(item)

    seen_filter_stats: dict[str, Any] = {
        "enabled": config.skip_seen_items,
        "loaded_keys": 0,
        "current_items": 0,
        "history_items": 0,
        "history_files": 0,
        "skipped_seen": 0,
    }
    if config.skip_seen_items:
        seen_keys, loaded_stats = collect_seen_keys(
            output_path=output_path, history_dir=archive_dir
        )
        seen_filter_stats.update(loaded_stats)
        seen_filter_stats["loaded_keys"] = len(seen_keys)

        filtered_items: list[DigestItem] = []
        skipped_seen = 0
        for item in items:
            keys = dedupe_keys_for_item(item)
            if keys and any(key in seen_keys for key in keys):
                skipped_seen += 1
                audit_logger.event(
                    "article_deduped",
                    **_article_event_payload(item),
                    reason="previously_seen",
                    dedupe_keys=sorted(keys),
                    outcome_state="duplicate",
                )
                continue
            seen_keys.update(keys)
            filtered_items.append(item)
        items = filtered_items
        seen_filter_stats["skipped_seen"] = skipped_seen

    new_items_before_scrape = len(items)
    logger.info(
        "Digest fetched %s raw items, removed %s in-run duplicates, skipped %s seen items, %s new items remain.",
        raw_fetched_items,
        duplicate_in_run,
        seen_filter_stats["skipped_seen"],
        new_items_before_scrape,
    )

    if config.scrape_enabled and items:
        scrape_stats = enrich_items_with_scrape(
            items,
            limit=config.scrape_limit,
            timeout_seconds=config.scrape_timeout_seconds,
            sleep_seconds=config.scrape_sleep_seconds,
            user_agent=config.scrape_user_agent,
            audit_logger=audit_logger,
        )
    else:
        for item in items:
            item.scraped = None
            item.scrape_error = None
            item.audit.setdefault("scrape", {})
            item.audit["scrape"].update(
                {
                    "status": "skipped",
                    "reason": "scrape_disabled" if not config.scrape_enabled else "no_items",
                    "attempted": False,
                }
            )
            audit_logger.event(
                "article_fetch_skipped",
                **_article_event_payload(item),
                reason="scrape_disabled" if not config.scrape_enabled else "no_items",
                outcome_state="included_partial",
            )
        scrape_stats = {
            "enabled": False,
            "attempts": 0,
            "success": 0,
            "failed": 0,
            "skipped": len(items),
            "limit": config.scrape_limit,
            "timeout_seconds": config.scrape_timeout_seconds,
            "sleep_seconds": config.scrape_sleep_seconds,
        }

    logger.info(
        "Scrape stage: attempts=%s success=%s failed=%s skipped=%s.",
        scrape_stats["attempts"],
        scrape_stats["success"],
        scrape_stats["failed"],
        scrape_stats["skipped"],
    )

    llm_input = apply_items_llm_readiness(items)
    for item in items:
        audit_logger.event(
            "article_llm_input_assessed",
            **_article_event_payload(item),
            llm_input_status=item.llm_input_status,
            llm_input_reason=item.llm_input_reason,
            llm_input_source=item.llm_input_source,
            ready_for_llm_judge=item.ready_for_llm_judge,
            scraped_text_chars=item.scraped_text_chars,
            llm_input_flags=[flag.get("code") for flag in item.llm_input_flags],
        )
    audit_logger.event("llm_input_summary", **llm_input)

    newsfeed_excluded_items = [item for item in items if not item.include_in_newsfeed]
    newsfeed_items = [item for item in items if item.include_in_newsfeed]
    for item in newsfeed_excluded_items:
        audit_logger.event(
            "article_newsfeed_excluded",
            **_article_event_payload(item),
            reason=item.newsfeed_exclusion_reason or "newsfeed_excluded",
            outcome_state="excluded_from_newsfeed",
        )

    openai_meta = OpenAIMeta(enabled=False)
    cache_meta = CacheMeta(enabled=config.openai_enabled, path=str(cache_path))
    prompt_export_path: str | None = None
    openai_batch_stats: dict[str, Any] = {
        "configured_batch_size": config.openai_batch_size,
        "configured_timeout_seconds": config.openai_timeout_seconds,
        "configured_max_retries": config.openai_max_retries,
        "configured_retry_backoff_seconds": config.openai_retry_backoff_seconds,
        "executed_batches": 0,
        "succeeded_batches": 0,
        "failed_batches": 0,
        "retry_attempts": 0,
    }

    if config.openai_enabled:
        for item in newsfeed_excluded_items:
            item.audit.setdefault("openai", {})
            item.audit["openai"].update(
                {
                    "status": "skipped",
                    "reason": "excluded_from_newsfeed",
                    "newsfeed_exclusion_reason": item.newsfeed_exclusion_reason,
                }
            )
            audit_logger.event(
                "article_scoring_skipped",
                **_article_event_payload(item),
                stage="openai_digest_batch",
                reason="excluded_from_newsfeed",
                outcome_state="excluded_from_newsfeed",
            )

    if config.openai_enabled and newsfeed_items:
        api_key = resolve_env_value(ENV_OPENAI_KEY, base_dir=repo_root, env_paths=(".env",))
        if not api_key:
            audit_logger.event(
                "run_failed",
                error=f"{ENV_OPENAI_KEY} is missing. Add it to env or .env.",
                duration_seconds=context.duration_seconds,
            )
            audit_logger.close()
            raise ConfigError(f"{ENV_OPENAI_KEY} is missing. Add it to env or .env.")

        cache = SQLiteOpenAICache(cache_path)
        service = OpenAIService(
            api_key=api_key,
            timeout_seconds=config.openai_timeout_seconds,
            cache=cache,
        )
        model = _openai_model(config, repo_root)
        openai_batches = _chunk_items(newsfeed_items, config.openai_batch_size)
        openai_batch_stats["executed_batches"] = len(openai_batches)
        aggregated_usage: dict[str, Any] | None = None
        last_response_id: str | None = None
        logger.info(
            "OpenAI digest stage: %s items across %s batches (batch_size=%s timeout=%ss retries=%s).",
            len(newsfeed_items),
            len(openai_batches),
            config.openai_batch_size,
            config.openai_timeout_seconds,
            config.openai_max_retries,
        )

        for batch_index, batch_items in enumerate(openai_batches, start=1):
            item_payloads = [item.to_dict() for item in batch_items]
            messages = build_digest_messages(item_payloads)
            result = None
            last_error: Exception | None = None
            attempt_count = 0

            for attempt_index in range(config.openai_max_retries + 1):
                attempt_count = attempt_index + 1
                try:
                    result = service.chat_json(
                        run_id=context.run_id,
                        purpose="digest_summarize",
                        model=model,
                        messages=messages,
                        temperature=0.2,
                        metadata={
                            "article_id": f"batch:{batch_index}",
                            "batch_index": batch_index,
                            "batch_size": len(batch_items),
                        },
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    if attempt_index >= config.openai_max_retries:
                        break
                    openai_batch_stats["retry_attempts"] += 1
                    backoff_seconds = _openai_backoff_seconds(
                        config.openai_retry_backoff_seconds,
                        attempt_index + 1,
                    )
                    logger.warning(
                        "Digest OpenAI batch %s/%s failed on attempt %s/%s: %s. Retrying in %.1fs.",
                        batch_index,
                        len(openai_batches),
                        attempt_index + 1,
                        config.openai_max_retries + 1,
                        exc,
                        backoff_seconds,
                    )
                    if backoff_seconds > 0:
                        time.sleep(backoff_seconds)

            if result is None:
                openai_batch_stats["failed_batches"] += 1
                error_type = _classify_openai_error(last_error or Exception("unknown OpenAI error"))
                logger.error(
                    "Digest OpenAI batch %s/%s failed permanently after %s attempt(s): %s",
                    batch_index,
                    len(openai_batches),
                    attempt_count,
                    last_error,
                )
                errors.append(
                    {
                        "stage": "openai_digest_batch",
                        "type": error_type,
                        "batch_index": batch_index,
                        "batch_size": len(batch_items),
                        "attempts": attempt_count,
                        "error": str(last_error or "unknown OpenAI error"),
                    }
                )
                for item in batch_items:
                    item.audit.setdefault("openai", {})
                    item.audit["openai"].update(
                        {
                            "batch_index": batch_index,
                            "batch_size": len(batch_items),
                            "attempts": attempt_count,
                            "status": "failed",
                            "error": str(last_error or "unknown OpenAI error"),
                        }
                    )
                    audit_logger.event(
                        "article_scoring_failed",
                        **_article_event_payload(item),
                        stage="openai_digest_batch",
                        batch_index=batch_index,
                        attempts=attempt_count,
                        error_type=error_type,
                        error=str(last_error or "unknown OpenAI error"),
                        outcome_state="score_failed",
                    )
                continue

            openai_batch_stats["succeeded_batches"] += 1
            aggregated_usage = _merge_usage_totals(aggregated_usage, result.usage)
            last_response_id = result.response_id or last_response_id

            mapping: dict[str, dict[str, Any]] = {}
            for result_item in result.parsed.get("items") or []:
                if isinstance(result_item, dict):
                    item_id_value = str(result_item.get("id") or "").strip()
                    if item_id_value:
                        mapping[item_id_value] = result_item

            for item in batch_items:
                ai = mapping.get(item.id)
                if ai:
                    item.ai_summary = str(ai.get("summary") or "")
                    raw_tags = ai.get("tags")
                    if isinstance(raw_tags, list):
                        item.ai_tags = [str(tag).strip() for tag in raw_tags if str(tag).strip()]
                    else:
                        item.ai_tags = []
                    scoring_event = "article_scoring_succeeded"
                    scoring_payload = {
                        "stage": "openai_digest_batch",
                        "batch_index": batch_index,
                        "attempts": attempt_count,
                        "tag_count": len(item.ai_tags),
                    }
                    openai_status = "succeeded"
                else:
                    scoring_event = "article_scoring_failed"
                    scoring_payload = {
                        "stage": "openai_digest_batch",
                        "batch_index": batch_index,
                        "attempts": attempt_count,
                        "error_type": "missing_openai_result",
                        "error": "OpenAI response did not include this article id.",
                        "outcome_state": "score_failed",
                    }
                    openai_status = "missing_result"
                item.audit.setdefault("openai", {})
                item.audit["openai"].update(
                    {
                        "batch_index": batch_index,
                        "batch_size": len(batch_items),
                        "attempts": attempt_count,
                        "status": openai_status,
                        "cache_key": result.cache_key,
                        "prompt_hash": result.user_prompt_hash,
                        "prompt_ref": f"sqlite://prompt_audit/{context.run_id}/{result.cache_key}",
                        "response_hash": result.response_hash,
                    }
                )
                audit_logger.event(scoring_event, **_article_event_payload(item), **scoring_payload)

        stats = cache.run_cache_stats(context.run_id)
        cache_meta.hits = stats["hits"]
        cache_meta.misses = stats["misses"]
        cache_meta.calls = stats["calls"]
        openai_meta = OpenAIMeta(
            enabled=True,
            model=model,
            response_id=last_response_id,
            usage=aggregated_usage,
            calls=stats["calls"],
            cache_hits=stats["hits"],
            cache_misses=stats["misses"],
        )

        prompt_rows = cache.prompt_audit_rows(context.run_id)
        prompt_export = export_prompt_audit_rows(prompt_rows, prompt_audit_dir, context.run_id)
        prompt_export_path = str(prompt_export)
    else:
        reason = "openai_disabled" if not config.openai_enabled else "no_newsfeed_items"
        skipped_items = items if not config.openai_enabled else newsfeed_items
        for item in skipped_items:
            item.audit.setdefault("openai", {})
            item.audit["openai"].update({"status": "skipped", "reason": reason})
            audit_logger.event(
                "article_scoring_skipped",
                **_article_event_payload(item),
                stage="openai_digest_batch",
                reason=reason,
                outcome_state="included_partial",
            )

    item_quality = apply_items_quality_audit(items)
    for item in items:
        audit_logger.event(
            "article_quality_assessed",
            **_article_event_payload(item),
            quality_status=item.quality_status,
            quality_flags=[flag.get("code") for flag in item.quality_flags],
            quality_flag_count=len(item.quality_flags),
        )
    audit_logger.event("quality_summary", **item_quality)

    summary = {
        "selected_sources": len(selected_source_ids),
        "selected_feeds": len(feeds),
        "raw_fetched_items": raw_fetched_items,
        "in_run_duplicates": duplicate_in_run,
        "skipped_seen": int(seen_filter_stats.get("skipped_seen") or 0),
        "new_items": len(items),
        "new_items_before_scrape": new_items_before_scrape,
        "typical_newsfeed_items": len(newsfeed_items),
        "newsfeed_excluded": len(newsfeed_excluded_items),
        "rss_missing_content": sum(
            1
            for item in newsfeed_excluded_items
            if item.newsfeed_exclusion_reason == "missing_rss_content"
        ),
        "unsupported_content_type": sum(
            1
            for item in newsfeed_excluded_items
            if (item.newsfeed_exclusion_reason or "").startswith("unsupported_content_type:")
        ),
        "accepted_content_type_filter": sum(
            1
            for item in newsfeed_excluded_items
            if (item.newsfeed_exclusion_reason or "").startswith("unsupported_content_type:")
        ),
        "quality_clean_items": int(item_quality.get("status_counts", {}).get("clean") or 0),
        "quality_warn_items": int(item_quality.get("status_counts", {}).get("warn") or 0),
        "quality_fail_items": int(item_quality.get("status_counts", {}).get("fail") or 0),
        "llm_ready_items": int(llm_input.get("status_counts", {}).get("ready") or 0),
        "llm_review_items": int(llm_input.get("status_counts", {}).get("review") or 0),
        "llm_excluded_items": int(llm_input.get("status_counts", {}).get("exclude") or 0),
        "llm_rss_fallback_items": int(llm_input.get("status_counts", {}).get("rss_fallback") or 0),
        "scrape_attempts": int(scrape_stats.get("attempts") or 0),
        "scrape_success": int(scrape_stats.get("success") or 0),
        "scrape_failed": int(scrape_stats.get("failed") or 0),
        "scrape_accepted_fallback": int(scrape_stats.get("accepted_fallback") or 0),
        "scrape_skipped": int(scrape_stats.get("skipped") or 0),
        "openai_batches_executed": int(openai_batch_stats.get("executed_batches") or 0),
        "openai_batches_succeeded": int(openai_batch_stats.get("succeeded_batches") or 0),
        "openai_batches_failed": int(openai_batch_stats.get("failed_batches") or 0),
        "openai_retry_attempts": int(openai_batch_stats.get("retry_attempts") or 0),
    }
    warnings = _warning_messages(
        new_items=len(items),
        rss_missing_content=int(summary.get("rss_missing_content") or 0),
        unsupported_content_type=int(summary.get("unsupported_content_type") or 0),
        scrape_stats=scrape_stats,
        openai_batch_stats=openai_batch_stats,
    )
    logger.info(
        "Digest summary: raw=%s new=%s scrape_success=%s/%s openai_batches=%s/%s retries=%s warnings=%s.",
        summary["raw_fetched_items"],
        summary["new_items"],
        summary["scrape_success"],
        summary["scrape_attempts"],
        summary["openai_batches_succeeded"],
        summary["openai_batches_executed"],
        summary["openai_retry_attempts"],
        len(warnings),
    )
    quality_report = build_digest_quality_report(
        run_id=context.run_id,
        generated_at=context.generated_at,
        items=items,
        errors=errors,
        summary=summary,
        warnings=warnings,
    )

    digest = DigestDocument(
        run=RunMeta(
            id=context.run_id,
            generated_at=context.generated_at,
            duration_seconds=context.duration_seconds,
            command=command_line(),
        ),
        request={
            "catalog_path": str(config.catalog),
            "skip_seen_items": config.skip_seen_items,
            "max_sources": config.max_sources,
            "feeds_per_source": config.feeds_per_source,
            "max_items_per_feed": config.max_items_per_feed,
            "source_ids": list(config.source_ids),
            "timeout_seconds": config.timeout_seconds,
            "feed_user_agent": config.feed_user_agent,
            "openai_timeout_seconds": config.openai_timeout_seconds,
            "openai_batch_size": config.openai_batch_size,
            "openai_max_retries": config.openai_max_retries,
            "openai_retry_backoff_seconds": config.openai_retry_backoff_seconds,
            "run_log_dir": str(config.run_log_dir),
        },
        sources={
            "selected_count": len(feeds),
            "selected": feeds,
        },
        openai=openai_meta,
        cache=cache_meta,
        items=items,
        errors=errors,
        quality_report=quality_report,
        audit={
            "summary": summary,
            "scrape": scrape_stats,
            "openai_batches": openai_batch_stats,
            "dedupe": {
                "raw_fetched_items": raw_fetched_items,
                "in_run_duplicates": duplicate_in_run,
                "new_items_before_scrape": new_items_before_scrape,
                "seen_filter": seen_filter_stats,
            },
            "item_quality": item_quality,
            "llm_input": llm_input,
            "warnings": warnings,
            "prompt_export": prompt_export_path,
            "run_log": str(run_log_path),
            "catalog_path": str(catalog_path),
        },
    )

    payload = digest.to_dict()
    schema_issues = validate_digest_payload(payload)
    schema_validation = validation_summary(schema_issues)
    payload.setdefault("audit", {})["schema_validation"] = schema_validation
    payload["quality_report"]["schema_validation"] = schema_validation
    if schema_issues:
        payload["quality_report"]["status"] = "fail"
        payload["quality_report"]["publishable"] = False
        blocking_issues = payload["quality_report"].setdefault("blocking_issues", [])
        if isinstance(blocking_issues, list):
            blocking_issues.append(f"schema validation failed with {len(schema_issues)} issue(s)")
    audit_logger.event(
        "json_validation_succeeded" if not schema_issues else "json_validation_failed",
        issue_count=len(schema_issues),
        issues=schema_validation["issues"],
    )
    write_json(output_path, payload)

    archive_path: Path | None = None
    if config.archive_enabled:
        archive_path = archive_json(payload, output_path, archive_dir)

    audit_logger.event(
        "run_completed",
        output_path=str(output_path),
        archive_path=str(archive_path) if archive_path else None,
        item_count=len(items),
        error_count=len(errors),
        warning_count=len(warnings),
        duration_seconds=context.duration_seconds,
    )
    audit_logger.close()

    return {
        "run_id": context.run_id,
        "output": str(output_path),
        "archive": str(archive_path) if archive_path else None,
        "run_log": str(run_log_path),
        "items": len(items),
        "errors": len(errors),
        "cache": payload.get("cache"),
        "openai": payload.get("openai"),
        "summary": summary,
        "warnings": warnings,
    }


def archive_existing_digest(
    *,
    repo_root: Path,
    output: Path,
    archive_dir: Path,
) -> Path:
    output_path = output if output.is_absolute() else repo_root / output
    archive_path = archive_dir if archive_dir.is_absolute() else repo_root / archive_dir
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Digest at {output_path} must be a JSON object.")
    return archive_json(payload, output_path, archive_path)
