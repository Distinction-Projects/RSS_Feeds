from __future__ import annotations

import unittest

from rss_pipeline.llm_readiness import apply_item_llm_readiness
from rss_pipeline.models_digest import DigestItem, FeedRef, SourceRef
from rss_pipeline.quality_diagnostics import (
    apply_item_quality_audit,
    evaluate_item_quality,
    summarize_item_quality,
)


def _item(
    item_id: str,
    *,
    source_id: str = "source-a",
    source_name: str = "Source A",
    title: str | None = None,
    link: str | None = None,
    published: str = "2026-04-03T00:00:00Z",
    content_type: str = "news_article",
    include_in_newsfeed: bool = True,
    newsfeed_exclusion_reason: str | None = None,
    scrape_audit: dict[str, object] | None = None,
    openai_audit: dict[str, object] | None = None,
    ai_summary: str = "AI summary",
) -> DigestItem:
    audit: dict[str, object] = {}
    if scrape_audit is not None:
        audit["scrape"] = scrape_audit
    if openai_audit is not None:
        audit["openai"] = openai_audit
    return DigestItem(
        id=item_id,
        title=title if title is not None else f"Title {item_id}",
        link=link if link is not None else f"https://example.com/{item_id}",
        summary=f"Summary {item_id}",
        published=published,
        source=SourceRef(id=source_id, name=source_name),
        feed=FeedRef(name="Top", url="https://example.com/feed.xml"),
        ai_summary=ai_summary,
        content_type=content_type,
        include_in_newsfeed=include_in_newsfeed,
        newsfeed_exclusion_reason=newsfeed_exclusion_reason,
        audit=audit,
    )


class QualityDiagnosticsTests(unittest.TestCase):
    def test_evaluate_item_quality_flags_hard_required_field_gaps(self) -> None:
        result = evaluate_item_quality(_item("item-1", title="", link="", published=""))

        self.assertEqual(result.status, "fail")
        self.assertEqual(
            {flag.code for flag in result.flags},
            {"missing_title", "missing_link", "missing_published"},
        )

    def test_evaluate_item_quality_flags_exclusions_and_stage_failures(self) -> None:
        item = _item(
            "item-1",
            content_type="video",
            include_in_newsfeed=False,
            newsfeed_exclusion_reason="unsupported_content_type:video",
            scrape_audit={"status": "failed", "error": "HTTP 403"},
            openai_audit={"status": "failed", "error": "timeout"},
        )

        result = evaluate_item_quality(item)

        self.assertEqual(result.status, "warn")
        self.assertEqual(
            {flag.code for flag in result.flags},
            {
                "content_type_filter_accepted",
                "source_blocked_403",
                "openai_digest_failed",
            },
        )

    def test_apply_item_quality_audit_writes_item_audit_and_flat_fields(self) -> None:
        item = _item(
            "item-1",
            content_type="missing_content",
            include_in_newsfeed=False,
            newsfeed_exclusion_reason="missing_rss_content",
        )

        result = apply_item_quality_audit(item)

        self.assertEqual(result.status, "warn")
        self.assertEqual(item.quality_status, "warn")
        self.assertEqual(item.quality_flags[0]["code"], "missing_rss_content")
        self.assertEqual(item.audit["quality"]["status"], "warn")

    def test_known_rss_only_fallback_policy_is_info_not_warning(self) -> None:
        result = evaluate_item_quality(
            _item(
                "item-1",
                source_id="skynews",
                source_name="SkyNews",
                scrape_audit={"status": "failed", "error": "HTTP Error 403: Forbidden"},
            )
        )

        self.assertEqual(result.status, "clean")
        self.assertEqual(len(result.flags), 1)
        self.assertEqual(result.flags[0].code, "rss_only_fallback_accepted")
        self.assertEqual(result.flags[0].severity, "info")

    def test_short_scraped_text_marks_item_for_pre_llm_review(self) -> None:
        item = _item("item-1", scrape_audit={"status": "succeeded"})
        item.scraped = {"body_text": "Too short."}

        readiness = apply_item_llm_readiness(item)
        result = apply_item_quality_audit(item)

        self.assertEqual(readiness.status, "review")
        self.assertFalse(item.ready_for_llm_judge)
        self.assertEqual(item.llm_input_reason, "short_scraped_text")
        self.assertEqual(result.status, "warn")
        self.assertIn("short_scraped_text", {flag["code"] for flag in item.quality_flags})
        self.assertEqual(item.audit["llm_input"]["status"], "review")

    def test_summarize_item_quality_rolls_up_issue_source_and_content_type(self) -> None:
        clean_item = _item("item-1")
        dirty_item = _item(
            "item-2",
            content_type="video",
            include_in_newsfeed=False,
            newsfeed_exclusion_reason="unsupported_content_type:video",
        )
        apply_item_quality_audit(clean_item)
        apply_item_quality_audit(dirty_item)

        summary = summarize_item_quality([clean_item, dirty_item])

        self.assertEqual(summary["status_counts"], {"clean": 2})
        self.assertEqual(
            summary["issue_counts"][0], {"issue": "content_type_filter_accepted", "count": 1}
        )
        self.assertEqual(summary["top_sources"][0], {"source": "Source A", "count": 1})
        self.assertEqual(
            summary["top_content_type_issues"][0],
            {"content_type": "video", "issue": "content_type_filter_accepted", "count": 1},
        )


if __name__ == "__main__":
    unittest.main()
