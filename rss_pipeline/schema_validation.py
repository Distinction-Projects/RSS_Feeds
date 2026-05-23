from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SchemaIssue:
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


def _is_populated_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _issue(issues: list[SchemaIssue], path: str, message: str) -> None:
    issues.append(SchemaIssue(path=path, message=message))


def _require_object(
    issues: list[SchemaIssue],
    value: Any,
    path: str,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        _issue(issues, path, "must be an object")
        return None
    return value


def _require_list(
    issues: list[SchemaIssue],
    value: Any,
    path: str,
) -> list[Any] | None:
    if not isinstance(value, list):
        _issue(issues, path, "must be an array")
        return None
    return value


def _require_string(
    issues: list[SchemaIssue],
    value: Any,
    path: str,
    *,
    allow_empty: bool = False,
) -> None:
    if not isinstance(value, str):
        _issue(issues, path, "must be a string")
        return
    if not allow_empty and not value.strip():
        _issue(issues, path, "must not be empty")


def _require_bool(issues: list[SchemaIssue], value: Any, path: str) -> None:
    if not isinstance(value, bool):
        _issue(issues, path, "must be a boolean")


def _require_integer(issues: list[SchemaIssue], value: Any, path: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        _issue(issues, path, "must be an integer")


def _validate_llm_input_fields(
    issues: list[SchemaIssue],
    obj: dict[str, Any],
    path: str,
    *,
    required: bool,
) -> None:
    if "scraped_text_chars" not in obj and required:
        _issue(issues, f"{path}.scraped_text_chars", "must be an integer")
    elif "scraped_text_chars" in obj:
        _require_integer(issues, obj.get("scraped_text_chars"), f"{path}.scraped_text_chars")

    if "llm_input_status" not in obj and required:
        _issue(issues, f"{path}.llm_input_status", "must be a string")
    elif "llm_input_status" in obj:
        status = obj.get("llm_input_status")
        _require_string(issues, status, f"{path}.llm_input_status")
        if isinstance(status, str) and status not in {
            "ready",
            "review",
            "exclude",
            "rss_fallback",
            "not_evaluated",
        }:
            _issue(
                issues,
                f"{path}.llm_input_status",
                "must be ready, review, exclude, rss_fallback, or not_evaluated",
            )

    if obj.get("llm_input_reason") is not None:
        _require_string(issues, obj.get("llm_input_reason"), f"{path}.llm_input_reason")
    if obj.get("llm_input_source") is not None:
        _require_string(issues, obj.get("llm_input_source"), f"{path}.llm_input_source")

    if "llm_input_flags" not in obj and required:
        _issue(issues, f"{path}.llm_input_flags", "must be an array")
    elif "llm_input_flags" in obj:
        _require_list(issues, obj.get("llm_input_flags"), f"{path}.llm_input_flags")

    if "ready_for_llm_judge" not in obj and required:
        _issue(issues, f"{path}.ready_for_llm_judge", "must be a boolean")
    elif "ready_for_llm_judge" in obj:
        _require_bool(issues, obj.get("ready_for_llm_judge"), f"{path}.ready_for_llm_judge")


def _validate_article(
    issues: list[SchemaIssue],
    article: Any,
    index: int,
    *,
    require_canonical: bool,
    require_content_type: bool,
) -> None:
    path = f"$.items[{index}]"
    obj = _require_object(issues, article, path)
    if obj is None:
        return

    for key in ("id", "title", "link", "summary", "published"):
        _require_string(issues, obj.get(key), f"{path}.{key}", allow_empty=key == "summary")

    source = _require_object(issues, obj.get("source"), f"{path}.source")
    if source is not None:
        _require_string(issues, source.get("id"), f"{path}.source.id")
        _require_string(issues, source.get("name"), f"{path}.source.name")

    feed = _require_object(issues, obj.get("feed"), f"{path}.feed")
    if feed is not None:
        _require_string(issues, feed.get("name"), f"{path}.feed.name")
        _require_string(issues, feed.get("url"), f"{path}.feed.url")

    _require_list(issues, obj.get("topic_tags"), f"{path}.topic_tags")
    _require_list(issues, obj.get("ai_tags"), f"{path}.ai_tags")
    _require_object(issues, obj.get("audit"), f"{path}.audit")
    if "content_type" not in obj and require_content_type:
        _issue(issues, f"{path}.content_type", "must be a string")
    elif "content_type" in obj:
        _require_string(issues, obj.get("content_type"), f"{path}.content_type")
    if "content_type_confidence" not in obj and require_content_type:
        _issue(issues, f"{path}.content_type_confidence", "must be a string")
    elif obj.get("content_type_confidence") is not None:
        _require_string(
            issues,
            obj.get("content_type_confidence"),
            f"{path}.content_type_confidence",
        )
    if obj.get("content_type_reason") is not None:
        _require_string(
            issues,
            obj.get("content_type_reason"),
            f"{path}.content_type_reason",
        )
    if "quality_status" not in obj and require_content_type:
        _issue(issues, f"{path}.quality_status", "must be a string")
    elif "quality_status" in obj:
        _require_string(issues, obj.get("quality_status"), f"{path}.quality_status")
    if "quality_flags" not in obj and require_content_type:
        _issue(issues, f"{path}.quality_flags", "must be an array")
    elif "quality_flags" in obj:
        _require_list(issues, obj.get("quality_flags"), f"{path}.quality_flags")
    if "include_in_newsfeed" not in obj and require_content_type:
        _issue(issues, f"{path}.include_in_newsfeed", "must be a boolean")
    elif "include_in_newsfeed" in obj:
        _require_bool(issues, obj.get("include_in_newsfeed"), f"{path}.include_in_newsfeed")
    if obj.get("newsfeed_exclusion_reason") is not None:
        _require_string(
            issues,
            obj.get("newsfeed_exclusion_reason"),
            f"{path}.newsfeed_exclusion_reason",
        )
    _validate_llm_input_fields(issues, obj, path, required=require_content_type)

    canonical_value = obj.get("canonical")
    canonical = None
    if canonical_value is None:
        if require_canonical:
            _issue(issues, f"{path}.canonical", "must be an object")
    else:
        canonical = _require_object(issues, canonical_value, f"{path}.canonical")

    if canonical is not None:
        canonical_fields = {
            "id": obj.get("id"),
            "url": obj.get("link"),
            "source_id": (source or {}).get("id"),
            "source_name": (source or {}).get("name"),
            "published_at": obj.get("published"),
            "title": obj.get("title"),
        }
        for key in canonical_fields:
            _require_string(issues, canonical.get(key), f"{path}.canonical.{key}")

        if (
            _is_populated_string(canonical.get("id"))
            and canonical.get("id") != canonical_fields["id"]
        ):
            _issue(issues, f"{path}.canonical.id", "must match article id")
        if (
            _is_populated_string(canonical.get("source_id"))
            and canonical.get("source_id") != canonical_fields["source_id"]
        ):
            _issue(issues, f"{path}.canonical.source_id", "must match source.id")
        if (
            _is_populated_string(canonical.get("source_name"))
            and canonical.get("source_name") != canonical_fields["source_name"]
        ):
            _issue(issues, f"{path}.canonical.source_name", "must match source.name")

    scraped = obj.get("scraped")
    if scraped is not None and not isinstance(scraped, dict):
        _issue(issues, f"{path}.scraped", "must be an object or null")
    scrape_error = obj.get("scrape_error")
    if scrape_error is not None and not isinstance(scrape_error, str):
        _issue(issues, f"{path}.scrape_error", "must be a string or null")


def validate_digest_payload(
    payload: Any,
    *,
    require_quality_report: bool = True,
    require_canonical: bool = True,
    require_content_type: bool = True,
) -> list[SchemaIssue]:
    issues: list[SchemaIssue] = []
    obj = _require_object(issues, payload, "$")
    if obj is None:
        return issues

    _require_string(issues, obj.get("schema_version"), "$.schema_version")
    _require_string(issues, obj.get("generated_at"), "$.generated_at")
    _require_object(issues, obj.get("request"), "$.request")
    _require_object(issues, obj.get("sources"), "$.sources")
    _require_object(issues, obj.get("openai"), "$.openai")
    _require_object(issues, obj.get("cache"), "$.cache")
    _require_object(issues, obj.get("audit"), "$.audit")

    run = _require_object(issues, obj.get("run"), "$.run")
    if run is not None:
        _require_string(issues, run.get("id"), "$.run.id")
        _require_string(issues, run.get("generated_at"), "$.run.generated_at")

    errors = obj.get("errors")
    if errors is not None:
        _require_list(issues, errors, "$.errors")

    quality_report_value = obj.get("quality_report")
    quality_report = None
    if quality_report_value is None:
        if require_quality_report:
            _issue(issues, "$.quality_report", "must be an object")
    else:
        quality_report = _require_object(issues, quality_report_value, "$.quality_report")

    if quality_report is not None:
        status = quality_report.get("status")
        _require_string(issues, status, "$.quality_report.status")
        if isinstance(status, str) and status not in {"pass", "warn", "fail"}:
            _issue(issues, "$.quality_report.status", "must be pass, warn, or fail")
        _require_bool(issues, quality_report.get("publishable"), "$.quality_report.publishable")
        for key in (
            "total_feed_items",
            "included_articles",
            "typical_newsfeed_articles",
            "newsfeed_excluded",
            "rss_missing_content",
            "unsupported_content_type",
            "included_clean",
            "included_partial",
            "llm_ready_items",
            "llm_review_items",
            "llm_excluded_items",
            "llm_rss_fallback_items",
            "llm_short_scraped_text",
            "llm_empty_scraped_text",
            "duplicates",
            "scrape_failed",
            "score_failed",
        ):
            _require_integer(issues, quality_report.get(key), f"$.quality_report.{key}")
        _require_list(
            issues, quality_report.get("field_coverage"), "$.quality_report.field_coverage"
        )
        _require_list(
            issues,
            quality_report.get("content_type_counts"),
            "$.quality_report.content_type_counts",
        )
        _require_list(
            issues,
            quality_report.get("excluded_content_type_counts"),
            "$.quality_report.excluded_content_type_counts",
        )
        _require_object(issues, quality_report.get("item_quality"), "$.quality_report.item_quality")
        _require_object(issues, quality_report.get("llm_input"), "$.quality_report.llm_input")
        _require_list(
            issues, quality_report.get("blocking_issues"), "$.quality_report.blocking_issues"
        )
        _require_list(issues, quality_report.get("warnings"), "$.quality_report.warnings")

    items = _require_list(issues, obj.get("items"), "$.items")
    if items is not None:
        for index, article in enumerate(items):
            _validate_article(
                issues,
                article,
                index,
                require_canonical=require_canonical,
                require_content_type=require_content_type,
            )

    return issues


def validation_summary(issues: list[SchemaIssue]) -> dict[str, Any]:
    return {
        "status": "pass" if not issues else "fail",
        "issue_count": len(issues),
        "issues": [issue.to_dict() for issue in issues],
    }
