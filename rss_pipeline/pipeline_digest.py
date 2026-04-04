from __future__ import annotations

import hashlib
import html
import json
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser

import scrape_experiment_links as scrape_links
from rss_pipeline.prompt_builder import build_digest_messages

from .artifact_store import archive_json, export_prompt_audit_rows, write_json
from .cache_sqlite import SQLiteOpenAICache
from .config import DigestBuildConfig
from .env import resolve_env_value
from .errors import ConfigError
from .logging import get_logger
from .models_digest import (
    CacheMeta,
    DigestDocument,
    DigestItem,
    FeedRef,
    OpenAIMeta,
    RunMeta,
    SourceRef,
)
from .openai_client import OpenAIService
from .workflow_runtime import RunContext, command_line

ENV_OPENAI_KEY = "OPENAI_API_KEY"
ENV_OPENAI_MODEL = "OPENAI_MODEL"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
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
VOLUME_WARNING_THRESHOLD = 60
SCRAPE_SUCCESS_WARNING_RATE = 0.5

logger = get_logger(__name__)


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
            feeds.append(
                {
                    "source_id": source_id,
                    "source_name": str(source.get("name") or source_id or "Unknown Source"),
                    "feed_name": str(feed.get("name") or feed_url),
                    "feed_url": feed_url,
                    "topic_tags": feed.get("topic_tags") or [],
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
    value = link.strip()
    if not value:
        return ""

    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return value

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


def dedupe_keys_for_item(item: DigestItem) -> set[str]:
    keys: set[str] = set()

    item_id_value = item.id.strip()
    if item_id_value:
        keys.add(f"id:{item_id_value}")

    normalized_link = normalize_link_for_dedupe(item.link)
    if normalized_link:
        keys.add(f"link:{normalized_link}")

    title = " ".join(item.title.lower().split())
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

    title = " ".join(str(payload.get("title") or "").lower().split())
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
        title = compact_text(str(entry.get("title") or "").strip(), 200)
        link = str(entry.get("link") or "").strip()
        summary = compact_text(
            strip_html(str(entry.get("summary") or entry.get("description") or "")),
            500,
        )
        published = str(entry.get("published") or entry.get("updated") or "").strip()

        source = SourceRef(id=feed["source_id"], name=feed["source_name"])
        feed_ref = FeedRef(name=feed["feed_name"], url=feed["feed_url"])
        item = DigestItem(
            id=item_id(feed["source_id"], link, title),
            title=title,
            link=link,
            summary=summary,
            published=published,
            source=source,
            feed=feed_ref,
            topic_tags=list(feed.get("topic_tags") or []),
            audit={
                "provenance": {
                    "source_id": source.id,
                    "source_name": source.name,
                    "feed_name": feed_ref.name,
                    "feed_url": feed_ref.url,
                }
            },
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
) -> dict[str, Any]:
    attempts = 0
    success = 0
    failed = 0
    skipped = 0

    for item in items:
        item.scraped = None
        item.scrape_error = None

    for item in items:
        if limit is not None and attempts >= limit:
            skipped += 1
            continue

        link = item.link.strip()
        if not link:
            item.scrape_error = "missing link"
            failed += 1
            attempts += 1
            continue

        try:
            scraped = scrape_links.scrape_article(
                link,
                timeout_seconds=timeout_seconds,
                user_agent=user_agent,
            )
            item.scraped = scraped.to_dict()
            success += 1
        except Exception as exc:  # noqa: BLE001
            item.scrape_error = str(exc)
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

    catalog = load_catalog(catalog_path)
    feeds = select_feeds(catalog, config.max_sources, config.feeds_per_source, config.source_ids)
    if not feeds:
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
    user_agent = "RSS_Feeds/2.0 (+https://github.com)"

    for feed in feeds:
        try:
            feed_items = fetch_feed_items(
                feed=feed,
                max_items=config.max_items_per_feed,
                timeout_seconds=config.timeout_seconds,
                user_agent=user_agent,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {
                    "stage": "feed_fetch",
                    "feed_url": feed["feed_url"],
                    "source_id": feed["source_id"],
                    "error": str(exc),
                }
            )
            continue

        raw_fetched_items += len(feed_items)
        for item in feed_items:
            dedupe_key = item.link or item.title or item.id
            if dedupe_key in seen_in_run:
                duplicate_in_run += 1
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
        )
    else:
        for item in items:
            item.scraped = None
            item.scrape_error = None
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

    if config.openai_enabled and items:
        api_key = resolve_env_value(ENV_OPENAI_KEY, base_dir=repo_root, env_paths=(".env",))
        if not api_key:
            raise ConfigError(f"{ENV_OPENAI_KEY} is missing. Add it to env or .env.")

        cache = SQLiteOpenAICache(cache_path)
        service = OpenAIService(
            api_key=api_key,
            timeout_seconds=config.openai_timeout_seconds,
            cache=cache,
        )
        model = _openai_model(config, repo_root)
        openai_batches = _chunk_items(items, config.openai_batch_size)
        openai_batch_stats["executed_batches"] = len(openai_batches)
        aggregated_usage: dict[str, Any] | None = None
        last_response_id: str | None = None
        logger.info(
            "OpenAI digest stage: %s items across %s batches (batch_size=%s timeout=%ss retries=%s).",
            len(items),
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
                item.audit.setdefault("openai", {})
                item.audit["openai"].update(
                    {
                        "batch_index": batch_index,
                        "batch_size": len(batch_items),
                        "attempts": attempt_count,
                        "status": "succeeded",
                        "cache_key": result.cache_key,
                        "prompt_hash": result.user_prompt_hash,
                        "prompt_ref": f"sqlite://prompt_audit/{context.run_id}/{result.cache_key}",
                        "response_hash": result.response_hash,
                    }
                )

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

    summary = {
        "selected_sources": len(selected_source_ids),
        "selected_feeds": len(feeds),
        "raw_fetched_items": raw_fetched_items,
        "in_run_duplicates": duplicate_in_run,
        "skipped_seen": int(seen_filter_stats.get("skipped_seen") or 0),
        "new_items": len(items),
        "new_items_before_scrape": new_items_before_scrape,
        "scrape_attempts": int(scrape_stats.get("attempts") or 0),
        "scrape_success": int(scrape_stats.get("success") or 0),
        "scrape_failed": int(scrape_stats.get("failed") or 0),
        "scrape_skipped": int(scrape_stats.get("skipped") or 0),
        "openai_batches_executed": int(openai_batch_stats.get("executed_batches") or 0),
        "openai_batches_succeeded": int(openai_batch_stats.get("succeeded_batches") or 0),
        "openai_batches_failed": int(openai_batch_stats.get("failed_batches") or 0),
        "openai_retry_attempts": int(openai_batch_stats.get("retry_attempts") or 0),
    }
    warnings = _warning_messages(
        new_items=len(items),
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
            "openai_timeout_seconds": config.openai_timeout_seconds,
            "openai_batch_size": config.openai_batch_size,
            "openai_max_retries": config.openai_max_retries,
            "openai_retry_backoff_seconds": config.openai_retry_backoff_seconds,
        },
        sources={
            "selected_count": len(feeds),
            "selected": feeds,
        },
        openai=openai_meta,
        cache=cache_meta,
        items=items,
        errors=errors,
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
            "warnings": warnings,
            "prompt_export": prompt_export_path,
            "catalog_path": str(catalog_path),
        },
    )

    payload = digest.to_dict()
    write_json(output_path, payload)

    archive_path: Path | None = None
    if config.archive_enabled:
        archive_path = archive_json(payload, output_path, archive_dir)

    return {
        "run_id": context.run_id,
        "output": str(output_path),
        "archive": str(archive_path) if archive_path else None,
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
