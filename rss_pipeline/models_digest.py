from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
    audit: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
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
            "audit": self.audit,
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
        }
