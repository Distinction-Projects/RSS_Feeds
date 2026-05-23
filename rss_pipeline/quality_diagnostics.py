from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from .failure_taxonomy import classification_from_scrape_audit
from .llm_readiness import (
    LLM_STATUS_NOT_EVALUATED,
    LLMInputFlag,
    LLMInputReadiness,
    llm_quality_flags_from_readiness,
)
from .models_digest import DigestItem
from .normalization import compact_whitespace
from .scrape_policy import accepted_scrape_fallback_for_digest_item


@dataclass(frozen=True, slots=True)
class QualityFlag:
    code: str
    severity: str
    message: str
    detail: str | None = None

    def to_dict(self) -> dict[str, str]:
        payload = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True, slots=True)
class ItemQualityResult:
    status: str
    flags: list[QualityFlag]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "flags": [flag.to_dict() for flag in self.flags],
        }


def _flag(
    code: str,
    severity: str,
    message: str,
    detail: str | None = None,
) -> QualityFlag:
    return QualityFlag(code=code, severity=severity, message=message, detail=detail)


def _audit_object(item: DigestItem, key: str) -> dict[str, Any]:
    value = item.audit.get(key)
    return value if isinstance(value, dict) else {}


def _has_flag(flags: list[QualityFlag], code: str) -> bool:
    return any(flag.code == code for flag in flags)


def _llm_readiness_from_item(item: DigestItem) -> LLMInputReadiness | None:
    status = compact_whitespace(item.llm_input_status)
    if not status or status == LLM_STATUS_NOT_EVALUATED:
        return None
    raw_audit = _audit_object(item, "llm_input")
    min_scraped_text_chars = 0
    if isinstance(raw_audit.get("min_scraped_text_chars"), int):
        min_scraped_text_chars = int(raw_audit["min_scraped_text_chars"])
    return LLMInputReadiness(
        status=status,
        ready_for_llm_judge=bool(item.ready_for_llm_judge),
        reason=compact_whitespace(item.llm_input_reason) or status,
        source=compact_whitespace(item.llm_input_source) or "none",
        scraped_text_chars=max(item.scraped_text_chars, 0),
        rss_summary_chars=len(compact_whitespace(item.summary)),
        min_scraped_text_chars=min_scraped_text_chars,
        flags=[
            LLMInputFlag(
                compact_whitespace(flag.get("code")) or "llm_input_not_ready",
                compact_whitespace(flag.get("severity")) or "warn",
                compact_whitespace(flag.get("message")) or "LLM input is not ready.",
                compact_whitespace(flag.get("detail")) or None,
            )
            for flag in item.llm_input_flags
            if isinstance(flag, dict)
        ],
    )


def evaluate_item_quality(item: DigestItem) -> ItemQualityResult:
    flags: list[QualityFlag] = []

    if not compact_whitespace(item.title):
        flags.append(_flag("missing_title", "error", "Story title is missing."))
    if not compact_whitespace(item.link):
        flags.append(_flag("missing_link", "error", "Story link is missing."))
    if not compact_whitespace(item.source.id) and not compact_whitespace(item.source.name):
        flags.append(_flag("missing_source", "error", "Story source is missing."))
    if not compact_whitespace(item.published):
        flags.append(_flag("missing_published", "warn", "Published timestamp is missing."))

    exclusion_reason = compact_whitespace(item.newsfeed_exclusion_reason)
    if not item.include_in_newsfeed:
        if exclusion_reason == "missing_rss_content":
            flags.append(
                _flag(
                    "missing_rss_content",
                    "warn",
                    "RSS entry has no summary, description, or content text.",
                )
            )
        elif exclusion_reason.startswith("unsupported_content_type:"):
            content_type = exclusion_reason.split(":", 1)[1]
            flags.append(
                _flag(
                    "content_type_filter_accepted",
                    "info",
                    "Story content type is not eligible for NewsLens and was excluded from normal output.",
                    detail=content_type,
                )
            )
        else:
            flags.append(
                _flag(
                    "excluded_from_newsfeed",
                    "warn",
                    "Story was excluded from normal NewsLens output.",
                    detail=exclusion_reason or None,
                )
            )
    elif not compact_whitespace(item.summary):
        flags.append(
            _flag(
                "missing_summary",
                "warn",
                "Story has no RSS summary text even though it remains newsfeed eligible.",
            )
        )

    scrape_audit = _audit_object(item, "scrape")
    scrape_status = compact_whitespace(scrape_audit.get("status"))
    if scrape_status == "failed":
        classification = classification_from_scrape_audit(
            scrape_audit,
            fallback_error=item.scrape_error,
        )
        accepted_fallback = accepted_scrape_fallback_for_digest_item(
            item,
            classification=classification,
        )
        if accepted_fallback is not None:
            flags.append(
                _flag(
                    "rss_only_fallback_accepted",
                    "info",
                    accepted_fallback["message"],
                    detail=classification.code,
                )
            )
        elif not (classification.code == "missing_link" and _has_flag(flags, "missing_link")):
            flags.append(
                _flag(
                    classification.code,
                    "warn",
                    classification.message,
                    detail=classification.raw_error or classification.source_action,
                )
            )

    llm_readiness = _llm_readiness_from_item(item)
    if llm_readiness is not None:
        existing_codes = {flag.code for flag in flags}
        for flag_payload in llm_quality_flags_from_readiness(llm_readiness):
            code = compact_whitespace(flag_payload.get("code"))
            if not code or code in existing_codes:
                continue
            if (
                code == "llm_input_rss_only_fallback"
                and "rss_only_fallback_accepted" in existing_codes
            ):
                continue
            flags.append(
                _flag(
                    code,
                    compact_whitespace(flag_payload.get("severity")) or "warn",
                    compact_whitespace(flag_payload.get("message")) or code,
                    compact_whitespace(flag_payload.get("detail")) or None,
                )
            )
            existing_codes.add(code)

    openai_audit = _audit_object(item, "openai")
    openai_status = compact_whitespace(openai_audit.get("status"))
    if openai_status in {"failed", "missing_result"}:
        flags.append(
            _flag(
                "openai_digest_failed",
                "warn",
                "OpenAI digest output is missing or failed for this story.",
                detail=compact_whitespace(openai_audit.get("error")) or openai_status,
            )
        )
    elif (
        item.include_in_newsfeed
        and openai_status == "succeeded"
        and not compact_whitespace(item.ai_summary)
    ):
        flags.append(
            _flag(
                "missing_ai_summary",
                "warn",
                "OpenAI digest succeeded but did not produce an AI summary.",
            )
        )

    if any(flag.severity == "error" for flag in flags):
        status = "fail"
    elif any(flag.severity == "warn" for flag in flags):
        status = "warn"
    else:
        status = "clean"

    return ItemQualityResult(status=status, flags=flags)


def apply_item_quality_audit(item: DigestItem) -> ItemQualityResult:
    result = evaluate_item_quality(item)
    item.quality_status = result.status
    item.quality_flags = [flag.to_dict() for flag in result.flags]
    item.audit["quality"] = result.to_dict()
    return result


def apply_items_quality_audit(items: list[DigestItem]) -> dict[str, Any]:
    for item in items:
        apply_item_quality_audit(item)
    return summarize_item_quality(items)


def _item_flags(item: DigestItem) -> list[dict[str, Any]]:
    if item.quality_flags:
        return item.quality_flags
    return [flag.to_dict() for flag in evaluate_item_quality(item).flags]


def summarize_item_quality(items: list[DigestItem]) -> dict[str, Any]:
    status_counts: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    source_issue_counts: Counter[tuple[str, str]] = Counter()
    content_type_issue_counts: Counter[tuple[str, str]] = Counter()

    for item in items:
        status = item.quality_status or evaluate_item_quality(item).status
        status_counts[status] += 1
        source = item.source.name or item.source.id or "unknown"
        content_type = item.content_type or "unknown"

        for flag in _item_flags(item):
            code = compact_whitespace(flag.get("code")) or "unknown_quality_issue"
            severity = compact_whitespace(flag.get("severity")) or "warn"
            issue_counts[code] += 1
            severity_counts[severity] += 1
            source_counts[source] += 1
            source_issue_counts[(source, code)] += 1
            content_type_issue_counts[(content_type, code)] += 1

    return {
        "status_counts": dict(status_counts),
        "severity_counts": dict(severity_counts),
        "issue_counts": [
            {"issue": issue, "count": count} for issue, count in issue_counts.most_common(20)
        ],
        "top_sources": [
            {"source": source, "count": count} for source, count in source_counts.most_common(20)
        ],
        "top_source_issues": [
            {"source": source, "issue": issue, "count": count}
            for (source, issue), count in source_issue_counts.most_common(20)
        ],
        "top_content_type_issues": [
            {"content_type": content_type, "issue": issue, "count": count}
            for (content_type, issue), count in content_type_issue_counts.most_common(20)
        ],
    }
