from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from typing_extensions import TypedDict

from serialization_utils import dump_json, validate_json

SELF_TEST_FLAG = "--self-test"
HELP_FLAGS: tuple[str, ...] = ("-h", "--help")
DEFAULT_INPUT_PATTERNS: tuple[str, ...] = ("data/rss_openai_daily.json",)
DIRECTORY_JSON_GLOB = "*.json"

ITEM_CONTAINER_KEYS: tuple[str, ...] = (
    "items",
    "news_items",
    "articles",
    "entries",
    "results",
    "records",
)
GENERATED_AT_KEYS: tuple[str, ...] = (
    "generated_at",
    "generatedAt",
    "created_at",
    "timestamp",
    "run_at",
)
GENERATED_AT_GROUP_KEYS: tuple[str, ...] = ("summary", "metadata", "run")
ERROR_COUNT_KEYS: tuple[str, ...] = ("error_count", "errors_count", "total_errors")
ERROR_COUNT_GROUP_KEYS: tuple[str, ...] = ("summary", "metadata", "stats")

NEWS_TITLE_KEYS: tuple[str, ...] = ("title", "headline", "name")
NEWS_LINK_KEYS: tuple[str, ...] = ("link", "url", "permalink")
NEWS_ID_KEYS: tuple[str, ...] = ("id", "item_id", "guid", "uuid")
NEWS_SUMMARY_KEYS: tuple[str, ...] = ("summary", "description", "excerpt", "content")
NEWS_PUBLISHED_KEYS: tuple[str, ...] = (
    "published",
    "published_at",
    "pub_date",
    "published_date",
    "date",
    "created_at",
)
NEWS_SOURCE_ID_KEYS: tuple[str, ...] = ("source_id", "source", "source_slug")
NEWS_SOURCE_NAME_KEYS: tuple[str, ...] = ("source_name",)
NEWS_FEED_NAME_KEYS: tuple[str, ...] = ("feed_name",)
NEWS_FEED_URL_KEYS: tuple[str, ...] = ("feed_url",)
NEWS_TOPIC_TAG_KEYS: tuple[str, ...] = ("topic_tags", "tags", "topics", "categories")
NEWS_FETCHED_AT_KEYS: tuple[str, ...] = (
    "fetched_at",
    "fetchedAt",
    "retrieved_at",
    "ingested_at",
)
NEWS_AI_SUMMARY_KEYS: tuple[str, ...] = (
    "ai_summary",
    "summary_ai",
    "llm_summary",
    "model_summary",
)
NEWS_AI_TAG_KEYS: tuple[str, ...] = ("ai_tags", "llm_tags", "keywords")

NESTED_SOURCE_ID_KEYS: tuple[str, ...] = ("id", "slug", "code")
NESTED_SOURCE_NAME_KEYS: tuple[str, ...] = ("name", "title", "label")
NESTED_FEED_NAME_KEYS: tuple[str, ...] = ("name", "title", "label")
NESTED_FEED_URL_KEYS: tuple[str, ...] = ("url", "link")

SELF_TEST_CASES: tuple[dict[str, Any], ...] = (
    {
        "name": "canonical_schema",
        "payload": {
            "generated_at": "2026-02-10T12:00:00Z",
            "errors": [{"message": "one failure"}],
            "items": [
                {
                    "id": "a-1",
                    "title": "Alpha",
                    "link": "https://example.com/a1",
                    "summary": "Alpha summary",
                    "published": "Tue, 10 Feb 2026 11:00:00 GMT",
                    "source_id": "source-a",
                    "source_name": "Source A",
                    "feed_name": "Feed A",
                    "feed_url": "https://example.com/feed-a.xml",
                    "topic_tags": ["economy", "markets"],
                    "fetched_at": "2026-02-10T12:05:00Z",
                    "ai_summary": "AI summary A",
                    "ai_tags": ["finance"],
                    "scraped": {
                        "fetched_at": "2026-02-10T12:07:00Z",
                        "final_url": "https://example.com/a1",
                        "status_code": 200,
                        "content_type": "text/html",
                        "title": "Alpha Title",
                        "description": "Alpha description",
                        "author": "Reporter A",
                        "published_at": "2026-02-10T11:00:00Z",
                        "canonical_url": "https://example.com/a1",
                        "language": "en",
                        "h1": "Alpha H1",
                        "body_text": (
                            "Alpha lead paragraph text.\n\n"
                            "Alpha supporting paragraph one.\n\n"
                            "Alpha supporting paragraph two."
                        ),
                        "lead_paragraph": "Alpha lead paragraph text.",
                        "paragraph_count": 4,
                        "word_count": 120,
                        "top_keywords": ["alpha", "economy"],
                    },
                    "scrape_error": None,
                },
                {
                    "id": "b-1",
                    "title": "Beta",
                    "link": "https://example.com/b1",
                    "summary": "Beta summary",
                    "published": "Tue, 10 Feb 2026 10:00:00 GMT",
                    "source_id": "source-b",
                    "source_name": "Source B",
                    "feed_name": "Feed B",
                    "feed_url": "https://example.com/feed-b.xml",
                    "topic_tags": ["tech"],
                    "fetched_at": "2026-02-10T12:06:00Z",
                    "ai_summary": "AI summary B",
                    "ai_tags": ["ai"],
                },
            ],
        },
        "expect": {
            "items": 2,
            "sources": 2,
            "errors": 1,
            "first_id": "a-1",
            "first_scraped_lead": "Alpha lead paragraph text.",
        },
    },
    {
        "name": "alternate_schema",
        "payload": {
            "metadata": {"timestamp": "2026-02-11T00:00:00Z"},
            "summary": {"total_errors": 3},
            "articles": [
                {
                    "guid": "alt-1",
                    "headline": "Gamma",
                    "url": "https://example.com/gamma",
                    "description": "Gamma summary",
                    "published_at": "2026-02-11T00:30:00Z",
                    "source": {"id": "source-c", "name": "Source C"},
                    "feed": {"name": "Feed C", "url": "https://example.com/feed-c.xml"},
                    "tags": "policy, us",
                    "retrieved_at": "2026-02-11T00:40:00Z",
                    "summary_ai": "AI summary C",
                    "llm_tags": ["politics", "usa"],
                }
            ],
        },
        "expect": {
            "items": 1,
            "sources": 1,
            "errors": 3,
            "first_id": "alt-1",
            "first_ai_summary": "AI summary C",
        },
    },
    {
        "name": "list_root_schema",
        "payload": [
            {
                "uuid": "list-1",
                "name": "Delta",
                "permalink": "https://example.com/delta",
                "content": "Delta summary",
                "date": "2026-02-11T01:00:00Z",
                "source_slug": "source-d",
                "source_name": "Source D",
                "feed_name": "Feed D",
                "feed_url": "https://example.com/feed-d.xml",
                "categories": ["world"],
                "ingested_at": "2026-02-11T01:05:00Z",
                "model_summary": "AI summary D",
                "keywords": "conflict,breaking",
            },
            {
                "uuid": "list-2",
                "name": "Epsilon",
                "permalink": "https://example.com/epsilon",
                "content": "Epsilon summary",
                "date": "2026-02-11T02:00:00Z",
                "source_slug": "source-d",
                "source_name": "Source D",
                "feed_name": "Feed D",
                "feed_url": "https://example.com/feed-d.xml",
                "categories": "economy, europe",
                "ingested_at": "2026-02-11T02:05:00Z",
                "model_summary": "AI summary E",
                "keywords": ["growth"],
            },
        ],
        "expect": {"items": 2, "sources": 1, "errors": 0, "first_id": "list-1"},
    },
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _first_value(obj: dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    for key in keys:
        if key in obj and obj[key] is not None:
            return obj[key]
    return default


def _as_datetime_optional(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    text = _text(value)
    if not text:
        return None

    # JSON often uses trailing "Z", which fromisoformat does not parse directly.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass

    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None


def _as_datetime(value: Any, fallback: datetime | None = None) -> datetime:
    parsed = _as_datetime_optional(value)
    if parsed is not None:
        return parsed
    if fallback is not None:
        return fallback
    return _now_utc()


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in (piece.strip() for piece in value.split(",")) if part]
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            text = _optional_text(item)
            if text:
                result.append(text)
        return result
    fallback = _optional_text(value)
    return [fallback] if fallback else []


def _datetime_to_iso_optional(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _load_json_object(raw: str | bytes, *, context: str) -> dict[str, Any]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"{context}: expected JSON object root for compat parsing.")
    return payload


class ScrapedArticleJSON(TypedDict):
    fetched_at: str
    final_url: str
    status_code: int | None
    content_type: str | None
    title: str | None
    description: str | None
    author: str | None
    published_at: str | None
    canonical_url: str | None
    language: str | None
    h1: str | None
    body_text: str | None
    lead_paragraph: str | None
    paragraph_count: int
    word_count: int
    top_keywords: list[str]


class NewsItemJSON(TypedDict):
    id: str
    title: str
    link: str
    summary: str | None
    published: str
    source_id: str
    source_name: str
    feed_name: str
    feed_url: str
    topic_tags: list[str]
    fetched_at: str
    ai_summary: str
    ai_tags: list[str]
    scraped: ScrapedArticleJSON | None
    scrape_error: str | None


class NewsSummaryJSON(TypedDict):
    generated_at: str
    total_items: int
    total_sources: int
    total_errors: int


class ExperimentDataJSON(TypedDict):
    generated_at: str
    summary: NewsSummaryJSON
    items: list[NewsItemJSON]
    errors: int


@dataclass(slots=True)
class ScrapedArticle:
    fetched_at: datetime
    final_url: str
    status_code: int | None
    content_type: str | None
    title: str | None
    description: str | None
    author: str | None
    published_at: datetime | None
    canonical_url: str | None
    language: str | None
    h1: str | None
    body_text: str | None
    lead_paragraph: str | None
    paragraph_count: int
    word_count: int
    top_keywords: list[str]

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> ScrapedArticle:
        return cls(
            fetched_at=_as_datetime(_first_value(obj, ("fetched_at",), _now_utc())),
            final_url=_text(_first_value(obj, ("final_url", "url"))),
            status_code=(
                int(obj["status_code"])
                if _first_value(obj, ("status_code",)) is not None and _text(obj.get("status_code"))
                else None
            ),
            content_type=_optional_text(_first_value(obj, ("content_type",))),
            title=_optional_text(_first_value(obj, ("title",))),
            description=_optional_text(_first_value(obj, ("description",))),
            author=_optional_text(_first_value(obj, ("author",))),
            published_at=_as_datetime_optional(_first_value(obj, ("published_at",))),
            canonical_url=_optional_text(_first_value(obj, ("canonical_url",))),
            language=_optional_text(_first_value(obj, ("language",))),
            h1=_optional_text(_first_value(obj, ("h1",))),
            body_text=_optional_text(_first_value(obj, ("body_text", "article_body", "full_text"))),
            lead_paragraph=_optional_text(_first_value(obj, ("lead_paragraph",))),
            paragraph_count=int(_first_value(obj, ("paragraph_count",), 0)),
            word_count=int(_first_value(obj, ("word_count",), 0)),
            top_keywords=_as_text_list(_first_value(obj, ("top_keywords",), [])),
        )

    @classmethod
    def from_json(cls, raw: str | bytes, *, strict: bool = False) -> ScrapedArticle:
        if strict:
            payload = validate_json(ScrapedArticleJSON, raw, context="ScrapedArticle")
            return cls.from_dict(dict(payload))
        return cls.from_dict(_load_json_object(raw, context="ScrapedArticle"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetched_at": self.fetched_at.isoformat(),
            "final_url": self.final_url,
            "status_code": self.status_code,
            "content_type": self.content_type,
            "title": self.title,
            "description": self.description,
            "author": self.author,
            "published_at": _datetime_to_iso_optional(self.published_at),
            "canonical_url": self.canonical_url,
            "language": self.language,
            "h1": self.h1,
            "body_text": self.body_text,
            "lead_paragraph": self.lead_paragraph,
            "paragraph_count": self.paragraph_count,
            "word_count": self.word_count,
            "top_keywords": list(self.top_keywords),
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return dump_json(
            ScrapedArticleJSON,
            self.to_dict(),
            indent=indent,
            context="ScrapedArticle",
        )


@dataclass(slots=True)
class NewsItem:
    id: str
    title: str
    link: str
    summary: str | None
    published_raw: str
    published_at: datetime | None
    source_id: str
    source_name: str
    feed_name: str
    feed_url: str
    topic_tags: list[str]
    fetched_at: datetime
    ai_summary: str
    ai_tags: list[str]
    scraped: ScrapedArticle | None = None
    scrape_error: str | None = None

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> NewsItem:
        source = obj.get("source")
        source_obj = source if isinstance(source, dict) else {}
        feed = obj.get("feed")
        feed_obj = feed if isinstance(feed, dict) else {}

        title = _text(_first_value(obj, NEWS_TITLE_KEYS))
        link = _text(_first_value(obj, NEWS_LINK_KEYS))
        item_id = _text(_first_value(obj, NEWS_ID_KEYS))
        if not item_id:
            item_id = link or title or "unknown"

        published_value = _first_value(obj, NEWS_PUBLISHED_KEYS, "")
        published_raw = _text(published_value)
        published_at = _as_datetime_optional(published_value)

        source_id = _text(
            _first_value(
                obj,
                NEWS_SOURCE_ID_KEYS,
                _first_value(source_obj, NESTED_SOURCE_ID_KEYS),
            )
        )
        source_name = _text(
            _first_value(
                obj,
                NEWS_SOURCE_NAME_KEYS,
                _first_value(source_obj, NESTED_SOURCE_NAME_KEYS),
            )
        )
        feed_name = _text(
            _first_value(
                obj,
                NEWS_FEED_NAME_KEYS,
                _first_value(feed_obj, NESTED_FEED_NAME_KEYS),
            )
        )
        feed_url = _text(
            _first_value(
                obj,
                NEWS_FEED_URL_KEYS,
                _first_value(feed_obj, NESTED_FEED_URL_KEYS),
            )
        )
        scraped_raw = obj.get("scraped")
        scraped = ScrapedArticle.from_dict(scraped_raw) if isinstance(scraped_raw, dict) else None

        return cls(
            id=item_id,
            title=title or "(untitled)",
            link=link,
            summary=_optional_text(_first_value(obj, NEWS_SUMMARY_KEYS)),
            published_raw=published_raw,
            published_at=published_at,
            source_id=source_id or "unknown",
            source_name=source_name or "Unknown Source",
            feed_name=feed_name or "Unknown Feed",
            feed_url=feed_url,
            topic_tags=_as_text_list(_first_value(obj, NEWS_TOPIC_TAG_KEYS)),
            fetched_at=_as_datetime(_first_value(obj, NEWS_FETCHED_AT_KEYS)),
            ai_summary=_text(_first_value(obj, NEWS_AI_SUMMARY_KEYS)),
            ai_tags=_as_text_list(_first_value(obj, NEWS_AI_TAG_KEYS)),
            scraped=scraped,
            scrape_error=_optional_text(_first_value(obj, ("scrape_error",))),
        )

    @classmethod
    def from_json(cls, raw: str | bytes, *, strict: bool = False) -> NewsItem:
        if strict:
            payload = validate_json(NewsItemJSON, raw, context="NewsItem")
            return cls.from_dict(dict(payload))
        return cls.from_dict(_load_json_object(raw, context="NewsItem"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "link": self.link,
            "summary": self.summary,
            "published": self.published_raw,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "feed_name": self.feed_name,
            "feed_url": self.feed_url,
            "topic_tags": list(self.topic_tags),
            "fetched_at": self.fetched_at.isoformat(),
            "ai_summary": self.ai_summary,
            "ai_tags": list(self.ai_tags),
            "scraped": self.scraped.to_dict() if self.scraped else None,
            "scrape_error": self.scrape_error,
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return dump_json(
            NewsItemJSON,
            self.to_dict(),
            indent=indent,
            context="NewsItem",
        )


@dataclass(slots=True)
class NewsSummary:
    generated_at: datetime
    total_items: int
    total_sources: int
    total_errors: int

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> NewsSummary:
        return cls(
            generated_at=_as_datetime(_first_value(obj, ("generated_at",), _now_utc())),
            total_items=int(_first_value(obj, ("total_items",), 0)),
            total_sources=int(_first_value(obj, ("total_sources",), 0)),
            total_errors=int(_first_value(obj, ("total_errors",), 0)),
        )

    @classmethod
    def from_json(cls, raw: str | bytes, *, strict: bool = False) -> NewsSummary:
        if strict:
            payload = validate_json(NewsSummaryJSON, raw, context="NewsSummary")
            return cls.from_dict(dict(payload))
        return cls.from_dict(_load_json_object(raw, context="NewsSummary"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "total_items": self.total_items,
            "total_sources": self.total_sources,
            "total_errors": self.total_errors,
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return dump_json(
            NewsSummaryJSON,
            self.to_dict(),
            indent=indent,
            context="NewsSummary",
        )


@dataclass(slots=True)
class ExperimentData:
    items: list[NewsItem]
    summary: NewsSummary

    @classmethod
    def from_payload(cls, payload: Any) -> ExperimentData:
        items_raw = _extract_items(payload)
        items = [NewsItem.from_dict(item) for item in items_raw]
        sources = {item.source_id for item in items}
        generated_at = _extract_generated_at(payload)
        error_count = _extract_error_count(payload)

        return cls(
            items=items,
            summary=NewsSummary(
                generated_at=generated_at,
                total_items=len(items),
                total_sources=len(sources),
                total_errors=error_count,
            ),
        )

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> ExperimentData:
        return cls.from_payload(obj)

    @classmethod
    def from_json(cls, raw: str | bytes, *, strict: bool = False) -> ExperimentData:
        if strict:
            payload = validate_json(ExperimentDataJSON, raw, context="ExperimentData")
            return cls.from_dict(dict(payload))
        return cls.from_payload(json.loads(raw))

    def items_by_source(self) -> dict[str, list[NewsItem]]:
        grouped: dict[str, list[NewsItem]] = {}
        for item in self.items:
            grouped.setdefault(item.source_id, []).append(item)
        return grouped

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.summary.generated_at.isoformat(),
            "summary": self.summary.to_dict(),
            "items": [item.to_dict() for item in self.items],
            "errors": self.summary.total_errors,
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return dump_json(
            ExperimentDataJSON,
            self.to_dict(),
            indent=indent,
            context="ExperimentData",
        )


def _looks_like_news_item(obj: dict[str, Any]) -> bool:
    keys = set(obj.keys())
    if "title" in keys or "headline" in keys:
        return True
    if "link" in keys or "url" in keys:
        return True
    if "summary" in keys or "description" in keys:
        return True
    return False


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ITEM_CONTAINER_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_items(value)
            if nested:
                return nested

    data_value = payload.get("data")
    if isinstance(data_value, list):
        dict_items = [item for item in data_value if isinstance(item, dict)]
        if dict_items and any(_looks_like_news_item(item) for item in dict_items):
            return dict_items
    if isinstance(data_value, dict):
        nested = _extract_items(data_value)
        if nested:
            return nested

    for value in payload.values():
        if isinstance(value, list):
            dict_items = [item for item in value if isinstance(item, dict)]
            if dict_items and any(_looks_like_news_item(item) for item in dict_items):
                return dict_items
    return []


def _extract_generated_at(payload: Any) -> datetime:
    if isinstance(payload, dict):
        direct = _first_value(payload, GENERATED_AT_KEYS)
        if direct is not None:
            return _as_datetime(direct)

        for group_key in GENERATED_AT_GROUP_KEYS:
            group = payload.get(group_key)
            if isinstance(group, dict):
                nested = _first_value(group, GENERATED_AT_KEYS)
                if nested is not None:
                    return _as_datetime(nested)
    return _now_utc()


def _extract_error_count(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0

    explicit = _first_value(payload, ERROR_COUNT_KEYS)
    if explicit is not None:
        try:
            return int(explicit)
        except (TypeError, ValueError):
            pass

    raw_errors = payload.get("errors")
    if isinstance(raw_errors, list):
        return len(raw_errors)
    if isinstance(raw_errors, dict):
        return len(raw_errors)

    for group_key in ERROR_COUNT_GROUP_KEYS:
        group = payload.get(group_key)
        if isinstance(group, dict):
            nested = _first_value(group, ERROR_COUNT_KEYS)
            if nested is not None:
                try:
                    return int(nested)
                except (TypeError, ValueError):
                    continue

    return 0


def load_experiment(path: str | Path = "experiment.json") -> ExperimentData:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ExperimentData.from_payload(payload)


def _expand_input_paths(paths: Iterable[str | Path]) -> list[Path]:
    results: list[Path] = []
    seen: set[Path] = set()

    for raw in paths:
        raw_text = str(raw)
        matched: list[Path]

        if any(char in raw_text for char in "*?[]"):
            matched = [path for path in Path().glob(raw_text) if path.is_file()]
        else:
            path = Path(raw_text)
            if path.is_dir():
                matched = [child for child in path.glob(DIRECTORY_JSON_GLOB) if child.is_file()]
            else:
                matched = [path]

        for path in sorted(matched):
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                results.append(path)

    return results


def load_experiments(paths: Iterable[str | Path]) -> list[tuple[Path, ExperimentData]]:
    files = _expand_input_paths(paths)
    loaded: list[tuple[Path, ExperimentData]] = []
    for file_path in files:
        loaded.append((file_path, load_experiment(file_path)))
    return loaded


def run_self_tests() -> int:
    failures: list[str] = []

    for case in SELF_TEST_CASES:
        name = _text(case.get("name")) or "unnamed_case"
        payload = case.get("payload")
        expect = case.get("expect")
        if not isinstance(expect, dict):
            failures.append(f"{name}: missing expect dict")
            continue

        data = ExperimentData.from_payload(payload)
        items_expected = int(expect.get("items", 0))
        sources_expected = int(expect.get("sources", 0))
        errors_expected = int(expect.get("errors", 0))
        if data.summary.total_items != items_expected:
            failures.append(
                f"{name}: expected items={items_expected}, got {data.summary.total_items}"
            )
        if data.summary.total_sources != sources_expected:
            failures.append(
                f"{name}: expected sources={sources_expected}, got {data.summary.total_sources}"
            )
        if data.summary.total_errors != errors_expected:
            failures.append(
                f"{name}: expected errors={errors_expected}, got {data.summary.total_errors}"
            )

        first_id_expected = expect.get("first_id")
        if first_id_expected is not None:
            if not data.items:
                failures.append(f"{name}: expected first item id={first_id_expected}, got no items")
            elif data.items[0].id != str(first_id_expected):
                failures.append(
                    f"{name}: expected first item id={first_id_expected}, got {data.items[0].id}"
                )

        first_ai_summary_expected = expect.get("first_ai_summary")
        if first_ai_summary_expected is not None:
            if not data.items:
                failures.append(
                    f"{name}: expected first ai summary={first_ai_summary_expected}, got no items"
                )
            elif data.items[0].ai_summary != str(first_ai_summary_expected):
                failures.append(
                    f"{name}: expected first ai summary={first_ai_summary_expected}, "
                    f"got {data.items[0].ai_summary}"
                )

        first_scraped_lead_expected = expect.get("first_scraped_lead")
        if first_scraped_lead_expected is not None:
            if not data.items:
                failures.append(
                    f"{name}: expected first scraped lead={first_scraped_lead_expected}, got no items"
                )
            else:
                scraped = data.items[0].scraped
                actual_lead = scraped.lead_paragraph if scraped else None
                if actual_lead != str(first_scraped_lead_expected):
                    failures.append(
                        f"{name}: expected first scraped lead={first_scraped_lead_expected}, "
                        f"got {actual_lead}"
                    )

        # Enforce NewsItem JSON round-trip stability for stored payloads.
        for index, item in enumerate(data.items):
            try:
                reparsed = NewsItem.from_dict(item.to_dict())
            except Exception as exc:
                failures.append(
                    f"{name}: round-trip failed for item index {index} ({item.id}): {exc}"
                )
                continue

            if reparsed.id != item.id:
                failures.append(
                    f"{name}: round-trip id mismatch for item index {index}: "
                    f"{item.id} != {reparsed.id}"
                )
            if reparsed.ai_summary != item.ai_summary:
                failures.append(
                    f"{name}: round-trip ai_summary mismatch for item index {index}: "
                    f"{item.ai_summary} != {reparsed.ai_summary}"
                )
            original_lead = item.scraped.lead_paragraph if item.scraped else None
            reparsed_lead = reparsed.scraped.lead_paragraph if reparsed.scraped else None
            if original_lead != reparsed_lead:
                failures.append(
                    f"{name}: round-trip scraped lead mismatch for item index {index}: "
                    f"{original_lead} != {reparsed_lead}"
                )

            try:
                json_reparsed = NewsItem.from_json(item.to_json())
            except Exception as exc:
                failures.append(
                    f"{name}: compat JSON round-trip failed for item index {index} "
                    f"({item.id}): {exc}"
                )
                continue

            if json_reparsed.id != item.id:
                failures.append(
                    f"{name}: compat JSON round-trip id mismatch for item index {index}: "
                    f"{item.id} != {json_reparsed.id}"
                )

            try:
                strict_reparsed = NewsItem.from_json(item.to_json(), strict=True)
            except Exception as exc:
                failures.append(
                    f"{name}: strict JSON round-trip failed for item index {index} "
                    f"({item.id}): {exc}"
                )
            else:
                if strict_reparsed.id != item.id:
                    failures.append(
                        f"{name}: strict JSON round-trip id mismatch for item index {index}: "
                        f"{item.id} != {strict_reparsed.id}"
                    )

            malformed_payload = item.to_dict()
            malformed_payload.pop("id", None)
            malformed_json = json.dumps(malformed_payload)
            try:
                NewsItem.from_json(malformed_json, strict=True)
                failures.append(
                    f"{name}: strict validation unexpectedly accepted malformed NewsItem payload"
                )
            except ValueError:
                pass
            except Exception as exc:
                failures.append(
                    f"{name}: strict malformed validation raised unexpected error type: {exc}"
                )

        try:
            data_reparsed_compat = ExperimentData.from_json(data.to_json())
        except Exception as exc:
            failures.append(f"{name}: ExperimentData compat JSON round-trip failed: {exc}")
        else:
            if data_reparsed_compat.summary.total_items != data.summary.total_items:
                failures.append(
                    f"{name}: ExperimentData compat JSON total_items mismatch: "
                    f"{data.summary.total_items} != {data_reparsed_compat.summary.total_items}"
                )
            if data_reparsed_compat.summary.total_sources != data.summary.total_sources:
                failures.append(
                    f"{name}: ExperimentData compat JSON total_sources mismatch: "
                    f"{data.summary.total_sources} != {data_reparsed_compat.summary.total_sources}"
                )

        try:
            data_reparsed_strict = ExperimentData.from_json(data.to_json(), strict=True)
        except Exception as exc:
            failures.append(f"{name}: ExperimentData strict JSON round-trip failed: {exc}")
        else:
            if data_reparsed_strict.summary.total_items != data.summary.total_items:
                failures.append(
                    f"{name}: ExperimentData strict JSON total_items mismatch: "
                    f"{data.summary.total_items} != {data_reparsed_strict.summary.total_items}"
                )
            if data_reparsed_strict.summary.total_sources != data.summary.total_sources:
                failures.append(
                    f"{name}: ExperimentData strict JSON total_sources mismatch: "
                    f"{data.summary.total_sources} != {data_reparsed_strict.summary.total_sources}"
                )

    if failures:
        print(f"SELF-TEST FAILED ({len(failures)} issues)")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(f"SELF-TEST PASSED ({len(SELF_TEST_CASES)} cases)")
    return 0


def _print_help() -> None:
    print("usage: load_experiment.py [--self-test] [INPUT ...]")
    print()
    print("Load one or more experiment JSON files and print summary statistics.")
    print()
    print("arguments:")
    print(
        "  INPUT         File paths, directories, or glob patterns (default: data/rss_openai_daily.json)"
    )
    print()
    print("options:")
    print(f"  {SELF_TEST_FLAG}    Run built-in parser self-tests and exit")
    print("  -h, --help     Show this help message and exit")


def main(argv: list[str]) -> int:
    if any(flag in argv for flag in HELP_FLAGS):
        _print_help()
        return 0
    if SELF_TEST_FLAG in argv:
        return run_self_tests()

    args = argv or list(DEFAULT_INPUT_PATTERNS)
    results = load_experiments(args)
    if not results:
        raise SystemExit("No matching JSON files found.")

    aggregate_items = 0
    aggregate_sources: set[str] = set()
    aggregate_errors = 0

    for path, data in results:
        aggregate_items += data.summary.total_items
        aggregate_errors += data.summary.total_errors
        aggregate_sources.update(item.source_id for item in data.items)
        print(
            f"{path}: {data.summary.total_items} items, {data.summary.total_sources} sources, "
            f"errors={data.summary.total_errors}, generated={data.summary.generated_at.isoformat()}"
        )

    if len(results) > 1:
        print(
            f"TOTAL: {aggregate_items} items across {len(aggregate_sources)} sources "
            f"(errors: {aggregate_errors}) in {len(results)} files."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
