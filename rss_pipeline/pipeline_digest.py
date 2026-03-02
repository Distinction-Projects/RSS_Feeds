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
from .env import resolve_env_value
from .errors import ConfigError
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

    items: list[DigestItem] = []
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
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

        for item in feed_items:
            dedupe_key = item.link or item.title or item.id
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            item.fetched_at = context.generated_at
            items.append(item)

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

    openai_meta = OpenAIMeta(enabled=False)
    cache_meta = CacheMeta(enabled=config.openai_enabled, path=str(cache_path))
    prompt_export_path: str | None = None

    if config.openai_enabled and items:
        api_key = resolve_env_value(ENV_OPENAI_KEY, base_dir=repo_root, env_paths=(".env",))
        if not api_key:
            raise ConfigError(f"{ENV_OPENAI_KEY} is missing. Add it to env or .env.")

        cache = SQLiteOpenAICache(cache_path)
        service = OpenAIService(
            api_key=api_key, timeout_seconds=config.timeout_seconds, cache=cache
        )
        model = _openai_model(config, repo_root)

        item_payloads = [item.to_dict() for item in items]
        messages = build_digest_messages(item_payloads)
        result = service.chat_json(
            run_id=context.run_id,
            purpose="digest_summarize",
            model=model,
            messages=messages,
            temperature=0.2,
            metadata={"article_id": "batch"},
        )

        mapping: dict[str, dict[str, Any]] = {}
        for result_item in result.parsed.get("items") or []:
            if isinstance(result_item, dict):
                item_id_value = str(result_item.get("id") or "").strip()
                if item_id_value:
                    mapping[item_id_value] = result_item

        for item in items:
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
            response_id=result.response_id,
            usage=result.usage,
            calls=stats["calls"],
            cache_hits=stats["hits"],
            cache_misses=stats["misses"],
        )

        prompt_rows = cache.prompt_audit_rows(context.run_id)
        prompt_export = export_prompt_audit_rows(prompt_rows, prompt_audit_dir, context.run_id)
        prompt_export_path = str(prompt_export)

    digest = DigestDocument(
        run=RunMeta(
            id=context.run_id,
            generated_at=context.generated_at,
            duration_seconds=context.duration_seconds,
            command=command_line(),
        ),
        request={
            "catalog_path": str(config.catalog),
            "max_sources": config.max_sources,
            "feeds_per_source": config.feeds_per_source,
            "max_items_per_feed": config.max_items_per_feed,
            "source_ids": list(config.source_ids),
            "timeout_seconds": config.timeout_seconds,
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
            "scrape": scrape_stats,
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
