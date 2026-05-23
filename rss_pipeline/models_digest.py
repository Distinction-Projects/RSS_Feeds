from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .normalization import normalize_url


@dataclass(slots=True)
class RunMeta:
    id: str
    generated_at: str
    duration_seconds: float | None = None
    command: str | None = None
    tool_version: str = "rss_pipeline/2.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "generated_at": self.generated_at,
            "duration_seconds": self.duration_seconds,
            "command": self.command,
            "tool_version": self.tool_version,
        }


@dataclass(slots=True)
class SourceRef:
    id: str
    name: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name}


@dataclass(slots=True)
class FeedRef:
    name: str
    url: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "url": self.url}


@dataclass(slots=True)
class DigestItem:
    id: str
    title: str
    link: str
    summary: str
    published: str
    source: SourceRef
    feed: FeedRef
    topic_tags: list[str] = field(default_factory=list)
    scraped: dict[str, Any] | None = None
    scrape_error: str | None = None
    ai_summary: str = ""
    ai_tags: list[str] = field(default_factory=list)
    fetched_at: str | None = None
    content_type: str = "news_article"
    content_type_confidence: str = "medium"
    content_type_reason: str | None = None
    quality_status: str = "clean"
    quality_flags: list[dict[str, Any]] = field(default_factory=list)
    include_in_newsfeed: bool = True
    newsfeed_exclusion_reason: str | None = None
    scraped_text_chars: int = 0
    llm_input_status: str = "not_evaluated"
    llm_input_reason: str | None = None
    llm_input_source: str | None = None
    llm_input_flags: list[dict[str, Any]] = field(default_factory=list)
    ready_for_llm_judge: bool = False
    audit: dict[str, Any] = field(default_factory=dict)

    def canonical_url(self) -> str:
        if isinstance(self.scraped, dict):
            for key in ("canonical_url", "final_url"):
                value = self.scraped.get(key)
                if isinstance(value, str) and value.strip():
                    return normalize_url(value)
        return normalize_url(self.link)

    def to_dict(self) -> dict[str, Any]:
        canonical = {
            "id": self.id,
            "url": self.canonical_url(),
            "source_id": self.source.id,
            "source_name": self.source.name,
            "published_at": self.published,
            "title": self.title,
        }
        data = {
            "id": self.id,
            "title": self.title,
            "link": self.link,
            "summary": self.summary,
            "published": self.published,
            "source": self.source.to_dict(),
            "feed": self.feed.to_dict(),
            "topic_tags": list(self.topic_tags),
            "scraped": self.scraped,
            "scrape_error": self.scrape_error,
            "ai_summary": self.ai_summary,
            "ai_tags": list(self.ai_tags),
            "fetched_at": self.fetched_at,
            "content_type": self.content_type,
            "content_type_confidence": self.content_type_confidence,
            "content_type_reason": self.content_type_reason,
            "quality_status": self.quality_status,
            "quality_flags": list(self.quality_flags),
            "include_in_newsfeed": self.include_in_newsfeed,
            "newsfeed_exclusion_reason": self.newsfeed_exclusion_reason,
            "scraped_text_chars": self.scraped_text_chars,
            "llm_input_status": self.llm_input_status,
            "llm_input_reason": self.llm_input_reason,
            "llm_input_source": self.llm_input_source,
            "llm_input_flags": list(self.llm_input_flags),
            "ready_for_llm_judge": self.ready_for_llm_judge,
            "audit": self.audit,
            "canonical": canonical,
            # Backward-compatible flat aliases consumed by existing tooling.
            "source_id": self.source.id,
            "source_name": self.source.name,
            "feed_name": self.feed.name,
            "feed_url": self.feed.url,
        }
        return data


@dataclass(slots=True)
class CacheMeta:
    enabled: bool
    path: str | None
    hits: int = 0
    misses: int = 0
    calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "path": self.path,
            "hits": self.hits,
            "misses": self.misses,
            "calls": self.calls,
        }


@dataclass(slots=True)
class OpenAIMeta:
    enabled: bool
    model: str | None = None
    response_id: str | None = None
    usage: dict[str, Any] | None = None
    calls: int = 0
    cache_hits: int = 0
    cache_misses: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "model": self.model,
            "response_id": self.response_id,
            "usage": self.usage,
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
        }


@dataclass(slots=True)
class DigestDocument:
    run: RunMeta
    request: dict[str, Any]
    sources: dict[str, Any]
    openai: OpenAIMeta
    cache: CacheMeta
    items: list[DigestItem]
    errors: list[dict[str, Any]]
    audit: dict[str, Any]
    quality_report: dict[str, Any] | None = None
    schema_version: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run": self.run.to_dict(),
            "generated_at": self.run.generated_at,
            "request": self.request,
            "sources": self.sources,
            "openai": self.openai.to_dict(),
            "cache": self.cache.to_dict(),
            "items": [item.to_dict() for item in self.items],
            "errors": self.errors,
            "audit": self.audit,
            "quality_report": self.quality_report or {},
        }
