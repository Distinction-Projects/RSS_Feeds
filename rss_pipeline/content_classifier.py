from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .normalization import compact_whitespace

NEWSLENS_ELIGIBLE_CONTENT_TYPES = {
    "news_article",
    "analysis",
    "opinion",
    "interview",
}

NEWSLENS_INELIGIBLE_CONTENT_TYPES = {
    "missing_content",
    "video",
    "podcast",
    "photo_gallery",
    "live_blog",
    "newsletter",
    "press_release",
}

_CLASSIFICATION_RULES: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    (
        "press_release",
        (
            r"\bpress release\b",
            r"\bnews release\b",
            r"\bpr newswire\b",
            r"\bglobenewswire\b",
            r"\bbusiness wire\b",
            r"\baccesswire\b",
            r"/press-release/",
        ),
        False,
    ),
    (
        "video",
        (
            r"^(watch|video)\s*[:|-]",
            r"\bvideo\b",
            r"/videos?/",
            r"/watch/",
        ),
        False,
    ),
    (
        "podcast",
        (
            r"\bpodcast\b",
            r"^(listen|audio)\s*[:|-]",
            r"\baudio\b",
            r"/podcasts?/",
        ),
        False,
    ),
    (
        "photo_gallery",
        (
            r"^(photos?|pictures?)\s*[:|-]",
            r"\bin pictures\b",
            r"\bphoto gallery\b",
            r"\bgallery\b",
            r"\bslideshow\b",
            r"/photos?/",
            r"/gallery/",
        ),
        False,
    ),
    (
        "live_blog",
        (
            r"^live\s*[:|-]",
            r"\blive (updates?|blog|coverage)\b",
            r"\bas it happened\b",
        ),
        False,
    ),
    (
        "newsletter",
        (
            r"\bnewsletter\b",
            r"/newsletters?/",
        ),
        False,
    ),
    (
        "opinion",
        (
            r"\bopinion\b",
            r"\beditorial\b",
            r"\bop-ed\b",
            r"\bguest essay\b",
            r"\bcolumnist\b",
            r"\bletters to the editor\b",
        ),
        True,
    ),
    (
        "analysis",
        (
            r"\banalysis\b",
            r"\bexplainer\b",
            r"\bfact[- ]check\b",
            r"\bwhat to know\b",
            r"\bwhy it matters\b",
        ),
        True,
    ),
    (
        "interview",
        (
            r"\binterview\b",
            r"\bq&a\b",
        ),
        True,
    ),
)


@dataclass(frozen=True, slots=True)
class StoryContentClassification:
    content_type: str
    newslens_eligible: bool
    confidence: str
    reason: str
    matched_signals: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_type": self.content_type,
            "newslens_eligible": self.newslens_eligible,
            "confidence": self.confidence,
            "reason": self.reason,
            "matched_signals": list(self.matched_signals),
        }


def classify_item_payload_content_type(item: dict[str, Any]) -> StoryContentClassification:
    source = item.get("source")
    source_obj = source if isinstance(source, dict) else {}
    feed = item.get("feed")
    feed_obj = feed if isinstance(feed, dict) else {}
    return classify_story_content_type(
        title=compact_whitespace(item.get("title")),
        link=compact_whitespace(item.get("link")),
        feed_name=compact_whitespace(feed_obj.get("name"))
        or compact_whitespace(item.get("feed_name")),
        source_name=compact_whitespace(source_obj.get("name"))
        or compact_whitespace(item.get("source_name")),
        topic_tags=_item_topic_tags(item),
        rss_content=_item_rss_content(item),
    )


def classify_story_content_type(
    *,
    title: str,
    link: str,
    feed_name: str,
    source_name: str,
    topic_tags: list[str],
    rss_content: str,
) -> StoryContentClassification:
    if not compact_whitespace(rss_content):
        return StoryContentClassification(
            content_type="missing_content",
            newslens_eligible=False,
            confidence="high",
            reason="RSS entry has no summary, description, or content text.",
            matched_signals=["rss_content:empty"],
        )

    searchable_fields = {
        "title": title,
        "link": link,
        "feed_name": feed_name,
        "source_name": source_name,
        "topic_tags": " ".join(topic_tags),
        "rss_content": rss_content,
    }
    searchable = {
        field: compact_whitespace(str(value or "")).casefold()
        for field, value in searchable_fields.items()
    }

    for content_type, patterns, eligible in _CLASSIFICATION_RULES:
        signals: list[str] = []
        for field, value in searchable.items():
            if not value:
                continue
            for pattern in patterns:
                if re.search(pattern, value, flags=re.IGNORECASE):
                    signals.append(f"{field}:{pattern}")
                    break
        if signals:
            return StoryContentClassification(
                content_type=content_type,
                newslens_eligible=eligible,
                confidence="high" if len(signals) > 1 else "medium",
                reason=(
                    f"Matched {content_type} content-type signal(s): " + ", ".join(signals[:3])
                ),
                matched_signals=signals,
            )

    return StoryContentClassification(
        content_type="news_article",
        newslens_eligible=True,
        confidence="medium",
        reason="No specialized content-type signal matched; treating as a standard news article.",
        matched_signals=[],
    )


def _item_topic_tags(item: dict[str, Any]) -> list[str]:
    raw_tags = item.get("topic_tags")
    if raw_tags is None:
        raw_tags = item.get("tags")
    if isinstance(raw_tags, list):
        return [compact_whitespace(tag) for tag in raw_tags if compact_whitespace(tag)]
    text = compact_whitespace(raw_tags)
    if not text:
        return []
    return [tag.strip() for tag in text.split(",") if tag.strip()]


def _item_rss_content(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("summary", "description", "content"):
        value = compact_whitespace(item.get(key))
        if value:
            parts.append(value)
    return " ".join(parts)
