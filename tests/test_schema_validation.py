from __future__ import annotations

import unittest

from rss_pipeline.models_digest import (
    CacheMeta,
    DigestDocument,
    DigestItem,
    FeedRef,
    OpenAIMeta,
    RunMeta,
    SourceRef,
)
from rss_pipeline.schema_validation import validate_digest_payload, validation_summary


def _digest_payload() -> dict[str, object]:
    item = DigestItem(
        id="item-1",
        title="Title 1",
        link="https://example.com/article-1",
        summary="Summary 1",
        published="2026-04-03T00:00:00Z",
        source=SourceRef(id="source-a", name="Source A"),
        feed=FeedRef(name="Top", url="https://example.com/feed.xml"),
        ai_summary="AI summary",
        ai_tags=["news"],
        scraped={"body_text": "Article body " * 40},
        scraped_text_chars=520,
        llm_input_status="ready",
        llm_input_reason="scraped_text_ready",
        llm_input_source="scraped_text",
        ready_for_llm_judge=True,
        audit={
            "scrape": {"status": "succeeded"},
            "openai": {"status": "succeeded"},
            "llm_input": {
                "status": "ready",
                "ready_for_llm_judge": True,
                "reason": "scraped_text_ready",
                "source": "scraped_text",
                "scraped_text_chars": 520,
                "rss_summary_chars": 9,
                "min_scraped_text_chars": 250,
                "flags": [],
            },
        },
    )
    document = DigestDocument(
        run=RunMeta(id="run-1", generated_at="2026-04-03T00:00:00Z"),
        request={"max_sources": 1},
        sources={"selected_count": 1, "selected": []},
        openai=OpenAIMeta(enabled=False),
        cache=CacheMeta(enabled=False, path=None),
        items=[item],
        errors=[],
        audit={"summary": {}},
        quality_report={
            "status": "pass",
            "publishable": True,
            "total_feed_items": 1,
            "included_articles": 1,
            "typical_newsfeed_articles": 1,
            "newsfeed_excluded": 0,
            "rss_missing_content": 0,
            "unsupported_content_type": 0,
            "included_clean": 1,
            "included_partial": 0,
            "llm_ready_items": 1,
            "llm_review_items": 0,
            "llm_excluded_items": 0,
            "llm_rss_fallback_items": 0,
            "llm_short_scraped_text": 0,
            "llm_empty_scraped_text": 0,
            "llm_input": {
                "status_counts": {"ready": 1},
                "reason_counts": [{"reason": "scraped_text_ready", "count": 1}],
                "flag_counts": [],
                "top_source_statuses": [{"source": "Source A", "status": "ready", "count": 1}],
                "top_source_reasons": [
                    {"source": "Source A", "reason": "scraped_text_ready", "count": 1}
                ],
                "ready_item_ids": ["item-1"],
                "review_item_ids": [],
            },
            "duplicates": 0,
            "scrape_failed": 0,
            "score_failed": 0,
            "field_coverage": [],
            "content_type_counts": [{"content_type": "news_article", "count": 1}],
            "excluded_content_type_counts": [],
            "item_quality": {
                "status_counts": {"clean": 1},
                "severity_counts": {},
                "issue_counts": [],
                "top_sources": [],
                "top_source_issues": [],
                "top_content_type_issues": [],
            },
            "blocking_issues": [],
            "warnings": [],
        },
    )
    return document.to_dict()


class DigestSchemaValidationTests(unittest.TestCase):
    def test_valid_digest_payload_passes_schema_validation(self) -> None:
        payload = _digest_payload()

        issues = validate_digest_payload(payload)

        self.assertEqual(issues, [])
        self.assertEqual(validation_summary(issues)["status"], "pass")

    def test_missing_canonical_field_fails_schema_validation(self) -> None:
        payload = _digest_payload()
        article = payload["items"][0]
        assert isinstance(article, dict)
        article.pop("canonical")

        issues = validate_digest_payload(payload)
        summary = validation_summary(issues)

        self.assertEqual(summary["status"], "fail")
        self.assertIn("$.items[0].canonical", {issue.path for issue in issues})

    def test_missing_canonical_field_is_allowed_in_compat_validation(self) -> None:
        payload = _digest_payload()
        article = payload["items"][0]
        assert isinstance(article, dict)
        article.pop("canonical")

        issues = validate_digest_payload(payload, require_canonical=False)

        self.assertEqual(issues, [])

    def test_missing_content_type_field_fails_schema_validation(self) -> None:
        payload = _digest_payload()
        article = payload["items"][0]
        assert isinstance(article, dict)
        article.pop("content_type")

        issues = validate_digest_payload(payload)

        self.assertIn("$.items[0].content_type", {issue.path for issue in issues})

    def test_missing_content_type_field_is_allowed_in_compat_validation(self) -> None:
        payload = _digest_payload()
        article = payload["items"][0]
        assert isinstance(article, dict)
        article.pop("content_type")

        issues = validate_digest_payload(payload, require_content_type=False)

        self.assertEqual(issues, [])

    def test_missing_llm_input_field_fails_schema_validation(self) -> None:
        payload = _digest_payload()
        article = payload["items"][0]
        assert isinstance(article, dict)
        article.pop("llm_input_status")

        issues = validate_digest_payload(payload)

        self.assertIn("$.items[0].llm_input_status", {issue.path for issue in issues})

    def test_missing_quality_report_fails_schema_validation(self) -> None:
        payload = _digest_payload()
        payload.pop("quality_report")

        issues = validate_digest_payload(payload)
        summary = validation_summary(issues)

        self.assertEqual(summary["status"], "fail")
        self.assertIn("$.quality_report", {issue.path for issue in issues})

    def test_missing_quality_report_is_allowed_in_compat_validation(self) -> None:
        payload = _digest_payload()
        payload.pop("quality_report")

        issues = validate_digest_payload(payload, require_quality_report=False)

        self.assertEqual(issues, [])

    def test_canonical_source_mismatch_fails_schema_validation(self) -> None:
        payload = _digest_payload()
        article = payload["items"][0]
        assert isinstance(article, dict)
        canonical = article["canonical"]
        assert isinstance(canonical, dict)
        canonical["source_id"] = "other-source"

        issues = validate_digest_payload(payload)

        self.assertIn("$.items[0].canonical.source_id", {issue.path for issue in issues})


if __name__ == "__main__":
    unittest.main()
