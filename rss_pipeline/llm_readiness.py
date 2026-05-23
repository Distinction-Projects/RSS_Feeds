from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from .content_classifier import (
    NEWSLENS_INELIGIBLE_CONTENT_TYPES,
    classify_item_payload_content_type,
)
from .failure_taxonomy import classification_from_scrape_audit
from .normalization import compact_whitespace
from .scrape_policy import (
    accepted_scrape_fallback_for_digest_item,
    accepted_scrape_fallback_for_payload,
)

MIN_SCRAPED_TEXT_CHARS_FOR_LLM = 250

LLM_STATUS_READY = "ready"
LLM_STATUS_REVIEW = "review"
LLM_STATUS_EXCLUDE = "exclude"
LLM_STATUS_RSS_FALLBACK = "rss_fallback"
LLM_STATUS_NOT_EVALUATED = "not_evaluated"
LLM_INPUT_STATUSES = {
    LLM_STATUS_READY,
    LLM_STATUS_REVIEW,
    LLM_STATUS_EXCLUDE,
    LLM_STATUS_RSS_FALLBACK,
    LLM_STATUS_NOT_EVALUATED,
}


@dataclass(frozen=True, slots=True)
class LLMInputFlag:
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
class LLMInputReadiness:
    status: str
    ready_for_llm_judge: bool
    reason: str
    source: str
    scraped_text_chars: int
    rss_summary_chars: int
    min_scraped_text_chars: int
    flags: list[LLMInputFlag]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ready_for_llm_judge": self.ready_for_llm_judge,
            "reason": self.reason,
            "source": self.source,
            "scraped_text_chars": self.scraped_text_chars,
            "rss_summary_chars": self.rss_summary_chars,
            "min_scraped_text_chars": self.min_scraped_text_chars,
            "flags": [flag.to_dict() for flag in self.flags],
        }


def _flag(
    code: str,
    severity: str,
    message: str,
    detail: str | None = None,
) -> LLMInputFlag:
    return LLMInputFlag(code=code, severity=severity, message=message, detail=detail)


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    try:
        return max(int(str(value).strip()), 0)
    except (TypeError, ValueError):
        return 0


def _audit_object(audit: Any, key: str) -> dict[str, Any]:
    if not isinstance(audit, dict):
        return {}
    value = audit.get(key)
    return value if isinstance(value, dict) else {}


def _source_name_from_payload(item: dict[str, Any]) -> str:
    source = item.get("source")
    source_payload = source if isinstance(source, dict) else {}
    return (
        compact_whitespace(source_payload.get("name"))
        or compact_whitespace(source_payload.get("id"))
        or compact_whitespace(item.get("source_name"))
        or compact_whitespace(item.get("source_id"))
        or "unknown"
    )


def _payload_newsfeed_exclusion_reason(item: dict[str, Any]) -> str | None:
    reason = compact_whitespace(item.get("newsfeed_exclusion_reason"))
    if item.get("include_in_newsfeed") is False:
        return reason or "excluded_from_newsfeed"

    audit = item.get("audit")
    content_audit = audit.get("content") if isinstance(audit, dict) else None
    if isinstance(content_audit, dict) and content_audit.get("exclude_from_newsfeed") is True:
        audit_reason = compact_whitespace(content_audit.get("reason"))
        return audit_reason or reason or "excluded_from_newsfeed"

    content_type = compact_whitespace(item.get("content_type"))
    if not content_type:
        content_type = classify_item_payload_content_type(item).content_type
    if content_type == "missing_content":
        return "missing_rss_content"
    if content_type in NEWSLENS_INELIGIBLE_CONTENT_TYPES:
        return f"unsupported_content_type:{content_type}"

    return None


def _normalized_flags(raw_flags: Any) -> list[LLMInputFlag]:
    if not isinstance(raw_flags, list):
        return []

    flags: list[LLMInputFlag] = []
    for raw_flag in raw_flags:
        if not isinstance(raw_flag, dict):
            continue
        code = compact_whitespace(raw_flag.get("code"))
        if not code:
            continue
        flags.append(
            _flag(
                code,
                compact_whitespace(raw_flag.get("severity")) or "warn",
                compact_whitespace(raw_flag.get("message")) or code,
                compact_whitespace(raw_flag.get("detail")) or None,
            )
        )
    return flags


def usable_scraped_text(scraped: Any) -> str:
    if not isinstance(scraped, dict):
        return ""

    body = compact_whitespace(scraped.get("body_text"))
    lead = compact_whitespace(scraped.get("lead_paragraph"))
    if body and lead and lead not in body:
        return compact_whitespace(f"{lead} {body}")
    return body or lead


def scraped_text_chars(scraped: Any) -> int:
    return len(usable_scraped_text(scraped))


def _readiness(
    *,
    status: str,
    reason: str,
    source: str,
    scraped_chars: int,
    rss_summary_chars: int,
    min_scraped_text_chars: int,
    flags: list[LLMInputFlag],
) -> LLMInputReadiness:
    return LLMInputReadiness(
        status=status,
        ready_for_llm_judge=status == LLM_STATUS_READY,
        reason=reason,
        source=source,
        scraped_text_chars=scraped_chars,
        rss_summary_chars=rss_summary_chars,
        min_scraped_text_chars=min_scraped_text_chars,
        flags=flags,
    )


def evaluate_digest_item_llm_readiness(
    item: Any,
    *,
    min_scraped_text_chars: int = MIN_SCRAPED_TEXT_CHARS_FOR_LLM,
) -> LLMInputReadiness:
    summary = compact_whitespace(getattr(item, "summary", ""))
    rss_summary_chars = len(summary)
    scraped = getattr(item, "scraped", None)
    scraped_chars = scraped_text_chars(scraped)
    audit = getattr(item, "audit", {})
    scrape_audit = _audit_object(audit, "scrape")
    scrape_status = compact_whitespace(scrape_audit.get("status"))
    scrape_error = compact_whitespace(scrape_audit.get("error")) or compact_whitespace(
        getattr(item, "scrape_error", "")
    )

    if getattr(item, "include_in_newsfeed", True) is False:
        reason = compact_whitespace(getattr(item, "newsfeed_exclusion_reason", "")) or (
            "excluded_from_newsfeed"
        )
        return _readiness(
            status=LLM_STATUS_EXCLUDE,
            reason=reason,
            source="none",
            scraped_chars=scraped_chars,
            rss_summary_chars=rss_summary_chars,
            min_scraped_text_chars=min_scraped_text_chars,
            flags=[
                _flag(
                    "excluded_from_llm_judge",
                    "info",
                    "Story is excluded before the LLM judge stage.",
                    reason,
                )
            ],
        )

    if scrape_status == "succeeded" and not scrape_error:
        if scraped_chars == 0:
            return _readiness(
                status=LLM_STATUS_REVIEW,
                reason="empty_scraped_text",
                source="none",
                scraped_chars=scraped_chars,
                rss_summary_chars=rss_summary_chars,
                min_scraped_text_chars=min_scraped_text_chars,
                flags=[
                    _flag(
                        "empty_scraped_text",
                        "warn",
                        "Article scrape succeeded but produced no usable text for the LLM judge.",
                    )
                ],
            )
        if scraped_chars < min_scraped_text_chars:
            return _readiness(
                status=LLM_STATUS_REVIEW,
                reason="short_scraped_text",
                source="scraped_text",
                scraped_chars=scraped_chars,
                rss_summary_chars=rss_summary_chars,
                min_scraped_text_chars=min_scraped_text_chars,
                flags=[
                    _flag(
                        "short_scraped_text",
                        "warn",
                        "Article scrape produced too little usable text for confident LLM judging.",
                        f"{scraped_chars}/{min_scraped_text_chars}",
                    )
                ],
            )
        return _readiness(
            status=LLM_STATUS_READY,
            reason="scraped_text_ready",
            source="scraped_text",
            scraped_chars=scraped_chars,
            rss_summary_chars=rss_summary_chars,
            min_scraped_text_chars=min_scraped_text_chars,
            flags=[],
        )

    if scrape_status == "failed" or scrape_error:
        classification = classification_from_scrape_audit(
            scrape_audit,
            fallback_error=scrape_error,
        )
        accepted_fallback = accepted_scrape_fallback_for_digest_item(
            item,
            classification=classification,
        )
        if accepted_fallback is not None:
            return _readiness(
                status=LLM_STATUS_RSS_FALLBACK,
                reason="accepted_rss_only_fallback",
                source="rss_summary",
                scraped_chars=scraped_chars,
                rss_summary_chars=rss_summary_chars,
                min_scraped_text_chars=min_scraped_text_chars,
                flags=[
                    _flag(
                        "llm_input_rss_only_fallback",
                        "info",
                        "Story has accepted RSS-only fallback, but not full scraped text for the LLM judge.",
                        classification.code,
                    )
                ],
            )
        return _readiness(
            status=LLM_STATUS_REVIEW,
            reason="scrape_failed_no_llm_input",
            source="none",
            scraped_chars=scraped_chars,
            rss_summary_chars=rss_summary_chars,
            min_scraped_text_chars=min_scraped_text_chars,
            flags=[
                _flag(
                    "scrape_failed_no_llm_input",
                    "warn",
                    "Article scrape failed and no accepted LLM fallback is available.",
                    classification.code,
                )
            ],
        )

    if scrape_status == "skipped":
        reason = compact_whitespace(scrape_audit.get("reason")) or "scrape_skipped"
        return _readiness(
            status=LLM_STATUS_REVIEW,
            reason=reason,
            source="none",
            scraped_chars=scraped_chars,
            rss_summary_chars=rss_summary_chars,
            min_scraped_text_chars=min_scraped_text_chars,
            flags=[
                _flag(
                    "scrape_not_attempted",
                    "warn",
                    "Article was not scraped, so the LLM judge does not have full article text.",
                    reason,
                )
            ],
        )

    if scraped_chars >= min_scraped_text_chars:
        return _readiness(
            status=LLM_STATUS_READY,
            reason="scraped_text_ready",
            source="scraped_text",
            scraped_chars=scraped_chars,
            rss_summary_chars=rss_summary_chars,
            min_scraped_text_chars=min_scraped_text_chars,
            flags=[],
        )

    return _readiness(
        status=LLM_STATUS_REVIEW,
        reason="scrape_state_unknown",
        source="none",
        scraped_chars=scraped_chars,
        rss_summary_chars=rss_summary_chars,
        min_scraped_text_chars=min_scraped_text_chars,
        flags=[
            _flag(
                "scrape_state_unknown",
                "warn",
                "Article does not have enough scrape metadata to prove LLM judge readiness.",
            )
        ],
    )


def llm_readiness_from_payload(
    item: dict[str, Any],
    *,
    min_scraped_text_chars: int = MIN_SCRAPED_TEXT_CHARS_FOR_LLM,
) -> LLMInputReadiness:
    scraped_chars = _safe_int(item.get("scraped_text_chars")) or scraped_text_chars(
        item.get("scraped")
    )
    rss_summary_chars = len(compact_whitespace(item.get("summary")))
    explicit_status = compact_whitespace(item.get("llm_input_status"))
    if explicit_status in LLM_INPUT_STATUSES and explicit_status != LLM_STATUS_NOT_EVALUATED:
        ready_value = item.get("ready_for_llm_judge")
        flags = _normalized_flags(item.get("llm_input_flags"))
        if not flags:
            audit_flags = _audit_object(item.get("audit"), "llm_input").get("flags")
            flags = _normalized_flags(audit_flags)
        return LLMInputReadiness(
            status=explicit_status,
            ready_for_llm_judge=bool(ready_value) if isinstance(ready_value, bool) else False,
            reason=compact_whitespace(item.get("llm_input_reason")) or explicit_status,
            source=compact_whitespace(item.get("llm_input_source")) or "none",
            scraped_text_chars=scraped_chars,
            rss_summary_chars=rss_summary_chars,
            min_scraped_text_chars=min_scraped_text_chars,
            flags=flags,
        )

    exclusion_reason = _payload_newsfeed_exclusion_reason(item)
    if exclusion_reason:
        reason = exclusion_reason
        return _readiness(
            status=LLM_STATUS_EXCLUDE,
            reason=reason,
            source="none",
            scraped_chars=scraped_chars,
            rss_summary_chars=rss_summary_chars,
            min_scraped_text_chars=min_scraped_text_chars,
            flags=[
                _flag(
                    "excluded_from_llm_judge",
                    "info",
                    "Story is excluded before the LLM judge stage.",
                    reason,
                )
            ],
        )

    scrape_audit = _audit_object(item.get("audit"), "scrape")
    scrape_status = compact_whitespace(scrape_audit.get("status"))
    scrape_error = compact_whitespace(scrape_audit.get("error")) or compact_whitespace(
        item.get("scrape_error")
    )

    if scrape_status == "succeeded" and not scrape_error:
        if scraped_chars == 0:
            return _readiness(
                status=LLM_STATUS_REVIEW,
                reason="empty_scraped_text",
                source="none",
                scraped_chars=scraped_chars,
                rss_summary_chars=rss_summary_chars,
                min_scraped_text_chars=min_scraped_text_chars,
                flags=[
                    _flag(
                        "empty_scraped_text",
                        "warn",
                        "Article scrape succeeded but produced no usable text for the LLM judge.",
                    )
                ],
            )
        if scraped_chars < min_scraped_text_chars:
            return _readiness(
                status=LLM_STATUS_REVIEW,
                reason="short_scraped_text",
                source="scraped_text",
                scraped_chars=scraped_chars,
                rss_summary_chars=rss_summary_chars,
                min_scraped_text_chars=min_scraped_text_chars,
                flags=[
                    _flag(
                        "short_scraped_text",
                        "warn",
                        "Article scrape produced too little usable text for confident LLM judging.",
                        f"{scraped_chars}/{min_scraped_text_chars}",
                    )
                ],
            )
        return _readiness(
            status=LLM_STATUS_READY,
            reason="scraped_text_ready",
            source="scraped_text",
            scraped_chars=scraped_chars,
            rss_summary_chars=rss_summary_chars,
            min_scraped_text_chars=min_scraped_text_chars,
            flags=[],
        )

    if scrape_status == "failed" or scrape_error:
        classification = classification_from_scrape_audit(
            scrape_audit,
            fallback_error=scrape_error,
        )
        accepted_fallback = accepted_scrape_fallback_for_payload(
            item,
            scrape_audit,
            fallback_error=scrape_error,
            classification=classification,
        )
        if accepted_fallback is not None:
            return _readiness(
                status=LLM_STATUS_RSS_FALLBACK,
                reason="accepted_rss_only_fallback",
                source="rss_summary",
                scraped_chars=scraped_chars,
                rss_summary_chars=rss_summary_chars,
                min_scraped_text_chars=min_scraped_text_chars,
                flags=[
                    _flag(
                        "llm_input_rss_only_fallback",
                        "info",
                        "Story has accepted RSS-only fallback, but not full scraped text for the LLM judge.",
                        classification.code,
                    )
                ],
            )
        return _readiness(
            status=LLM_STATUS_REVIEW,
            reason="scrape_failed_no_llm_input",
            source="none",
            scraped_chars=scraped_chars,
            rss_summary_chars=rss_summary_chars,
            min_scraped_text_chars=min_scraped_text_chars,
            flags=[
                _flag(
                    "scrape_failed_no_llm_input",
                    "warn",
                    "Article scrape failed and no accepted LLM fallback is available.",
                    classification.code,
                )
            ],
        )

    if scrape_status == "skipped":
        reason = compact_whitespace(scrape_audit.get("reason")) or "scrape_skipped"
        return _readiness(
            status=LLM_STATUS_REVIEW,
            reason=reason,
            source="none",
            scraped_chars=scraped_chars,
            rss_summary_chars=rss_summary_chars,
            min_scraped_text_chars=min_scraped_text_chars,
            flags=[
                _flag(
                    "scrape_not_attempted",
                    "warn",
                    "Article was not scraped, so the LLM judge does not have full article text.",
                    reason,
                )
            ],
        )

    if scraped_chars >= min_scraped_text_chars:
        return _readiness(
            status=LLM_STATUS_READY,
            reason="scraped_text_ready",
            source="scraped_text",
            scraped_chars=scraped_chars,
            rss_summary_chars=rss_summary_chars,
            min_scraped_text_chars=min_scraped_text_chars,
            flags=[],
        )

    return _readiness(
        status=LLM_STATUS_REVIEW,
        reason="scrape_state_unknown",
        source="none",
        scraped_chars=scraped_chars,
        rss_summary_chars=rss_summary_chars,
        min_scraped_text_chars=min_scraped_text_chars,
        flags=[
            _flag(
                "scrape_state_unknown",
                "warn",
                "Article does not have enough scrape metadata to prove LLM judge readiness.",
            )
        ],
    )


def apply_item_llm_readiness(
    item: Any,
    *,
    min_scraped_text_chars: int = MIN_SCRAPED_TEXT_CHARS_FOR_LLM,
) -> LLMInputReadiness:
    readiness = evaluate_digest_item_llm_readiness(
        item,
        min_scraped_text_chars=min_scraped_text_chars,
    )
    item.scraped_text_chars = readiness.scraped_text_chars
    item.llm_input_status = readiness.status
    item.ready_for_llm_judge = readiness.ready_for_llm_judge
    item.llm_input_reason = readiness.reason
    item.llm_input_source = readiness.source
    item.llm_input_flags = [flag.to_dict() for flag in readiness.flags]
    item.audit["llm_input"] = readiness.to_dict()
    return readiness


def apply_items_llm_readiness(items: list[Any]) -> dict[str, Any]:
    for item in items:
        apply_item_llm_readiness(item)
    return summarize_llm_readiness(items)


def llm_quality_flags_from_readiness(readiness: LLMInputReadiness) -> list[dict[str, str]]:
    if readiness.status == LLM_STATUS_READY:
        return []
    if readiness.status == LLM_STATUS_EXCLUDE:
        return [flag.to_dict() for flag in readiness.flags if flag.severity in {"error", "warn"}]
    return [flag.to_dict() for flag in readiness.flags]


def summarize_llm_readiness(items: list[Any]) -> dict[str, Any]:
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    flag_counts: Counter[str] = Counter()
    source_status_counts: Counter[tuple[str, str]] = Counter()
    source_reason_counts: Counter[tuple[str, str]] = Counter()
    ready_item_ids: list[str] = []
    review_item_ids: list[str] = []

    for item in items:
        status = compact_whitespace(getattr(item, "llm_input_status", "")) or (
            LLM_STATUS_NOT_EVALUATED
        )
        reason = compact_whitespace(getattr(item, "llm_input_reason", "")) or status
        source = getattr(getattr(item, "source", None), "name", "") or getattr(
            getattr(item, "source", None), "id", ""
        )
        source = compact_whitespace(source) or "unknown"
        status_counts[status] += 1
        reason_counts[reason] += 1
        source_status_counts[(source, status)] += 1
        source_reason_counts[(source, reason)] += 1
        if status == LLM_STATUS_READY:
            ready_item_ids.append(compact_whitespace(getattr(item, "id", "")))
        elif status == LLM_STATUS_REVIEW:
            review_item_ids.append(compact_whitespace(getattr(item, "id", "")))
        for flag in getattr(item, "llm_input_flags", []) or []:
            if not isinstance(flag, dict):
                continue
            code = compact_whitespace(flag.get("code"))
            if code:
                flag_counts[code] += 1

    return {
        "status_counts": dict(status_counts),
        "reason_counts": [
            {"reason": reason, "count": count} for reason, count in reason_counts.most_common(20)
        ],
        "flag_counts": [
            {"flag": flag, "count": count} for flag, count in flag_counts.most_common(20)
        ],
        "top_source_statuses": [
            {"source": source, "status": status, "count": count}
            for (source, status), count in source_status_counts.most_common(20)
        ],
        "top_source_reasons": [
            {"source": source, "reason": reason, "count": count}
            for (source, reason), count in source_reason_counts.most_common(20)
        ],
        "ready_item_ids": [item_id for item_id in ready_item_ids if item_id][:20],
        "review_item_ids": [item_id for item_id in review_item_ids if item_id][:20],
    }


def summarize_payload_llm_readiness(items: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    flag_counts: Counter[str] = Counter()
    source_status_counts: Counter[tuple[str, str]] = Counter()
    for item in items:
        readiness = llm_readiness_from_payload(item)
        status_counts[readiness.status] += 1
        reason_counts[readiness.reason] += 1
        source_status_counts[(_source_name_from_payload(item), readiness.status)] += 1
        for flag in readiness.flags:
            flag_counts[flag.code] += 1
    return {
        "status_counts": dict(status_counts),
        "reason_counts": [
            {"reason": reason, "count": count} for reason, count in reason_counts.most_common(20)
        ],
        "flag_counts": [
            {"flag": flag, "count": count} for flag, count in flag_counts.most_common(20)
        ],
        "top_source_statuses": [
            {"source": source, "status": status, "count": count}
            for (source, status), count in source_status_counts.most_common(20)
        ],
    }
