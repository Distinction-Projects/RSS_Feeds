from __future__ import annotations

import unittest

from rss_pipeline.models_digest import DigestItem, FeedRef, SourceRef
from rss_pipeline.quality_report import build_digest_quality_report


def _item(
    item_id: str,
    *,
    source_id: str = "source-a",
    source_name: str = "Source A",
    title: str | None = None,
    link: str | None = None,
    published: str = "2026-04-03T00:00:00Z",
    scrape_status: str = "succeeded",
    openai_status: str = "succeeded",
    scrape_error: str | None = None,
    include_in_newsfeed: bool = True,
    newsfeed_exclusion_reason: str | None = None,
    content_type: str = "news_article",
) -> DigestItem:
    item = DigestItem(
        id=item_id,
        title=title if title is not None else f"Title {item_id}",
        link=link if link is not None else f"https://example.com/{item_id}",
        summary=f"Summary {item_id}",
        published=published,
        source=SourceRef(id=source_id, name=source_name),
        feed=FeedRef(name="Top", url="https://example.com/feed.xml"),
        ai_summary=f"AI summary {item_id}",
        ai_tags=["news"],
        scrape_error=scrape_error,
        content_type=content_type,
        include_in_newsfeed=include_in_newsfeed,
        newsfeed_exclusion_reason=newsfeed_exclusion_reason,
        audit={
            "scrape": {"status": scrape_status},
            "openai": {"status": openai_status},
        },
    )
    return item


class DigestQualityReportTests(unittest.TestCase):
    def test_quality_report_passes_for_clean_complete_items(self) -> None:
        report = build_digest_quality_report(
            run_id="run-1",
            generated_at="2026-04-03T00:00:00Z",
            items=[_item("item-1"), _item("item-2")],
            errors=[],
            summary={"raw_fetched_items": 2, "scrape_failed": 0},
            warnings=[],
        )

        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["publishable"])
        self.assertEqual(report["included_articles"], 2)
        self.assertEqual(report["included_clean"], 2)
        self.assertEqual(report["included_partial"], 0)
        self.assertEqual(report["blocking_issues"], [])

    def test_quality_report_warns_for_missing_rss_content_exclusions(self) -> None:
        report = build_digest_quality_report(
            run_id="run-1",
            generated_at="2026-04-03T00:00:00Z",
            items=[
                _item(
                    "item-1",
                    include_in_newsfeed=False,
                    newsfeed_exclusion_reason="missing_rss_content",
                ),
                _item("item-2"),
            ],
            errors=[],
            summary={"raw_fetched_items": 2, "rss_missing_content": 1, "newsfeed_excluded": 1},
            warnings=[],
        )

        self.assertEqual(report["status"], "warn")
        self.assertTrue(report["publishable"])
        self.assertEqual(report["included_articles"], 2)
        self.assertEqual(report["typical_newsfeed_articles"], 1)
        self.assertEqual(report["newsfeed_excluded"], 1)
        self.assertEqual(report["rss_missing_content"], 1)
        self.assertIn(
            "1 article(s) missing RSS content were excluded from typical newsfeed output",
            report["warnings"],
        )

    def test_quality_report_tracks_accepted_content_type_filters(self) -> None:
        report = build_digest_quality_report(
            run_id="run-1",
            generated_at="2026-04-03T00:00:00Z",
            items=[
                _item(
                    "item-1",
                    content_type="video",
                    include_in_newsfeed=False,
                    newsfeed_exclusion_reason="unsupported_content_type:video",
                ),
                _item("item-2", content_type="opinion"),
            ],
            errors=[],
            summary={"raw_fetched_items": 2, "unsupported_content_type": 1},
            warnings=[],
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["unsupported_content_type"], 1)
        self.assertEqual(report["accepted_content_type_filter"], 1)
        self.assertEqual(report["content_type_counts"][0], {"content_type": "video", "count": 1})
        self.assertEqual(
            report["excluded_content_type_counts"][0],
            {"content_type": "video", "count": 1},
        )
        self.assertEqual(report["warnings"], [])

    def test_quality_report_warns_for_filtered_duplicates_and_scrape_failures(self) -> None:
        report = build_digest_quality_report(
            run_id="run-1",
            generated_at="2026-04-03T00:00:00Z",
            items=[
                _item("item-1", scrape_status="failed", scrape_error="HTTP Error 403: Forbidden")
            ],
            errors=[],
            summary={"raw_fetched_items": 3, "in_run_duplicates": 1, "scrape_failed": 1},
            warnings=[],
        )

        self.assertEqual(report["status"], "warn")
        self.assertTrue(report["publishable"])
        self.assertEqual(report["duplicates"], 1)
        self.assertEqual(report["scrape_failed"], 1)
        self.assertEqual(report["rss_only_fallback"], 1)
        self.assertEqual(report["top_failing_sources"][0]["source"], "Source A")
        self.assertEqual(report["top_error_reasons"][0]["reason"], "source_blocked_403")
        self.assertEqual(
            report["scrape_failure_reasons"][0],
            {"reason": "source_blocked_403", "count": 1},
        )
        self.assertIn("1 duplicate article(s) were filtered before output", report["warnings"])
        self.assertIn(
            "1 article scrape(s) failed (top reason: source_blocked_403)",
            report["warnings"],
        )

    def test_quality_report_separates_accepted_rss_only_fallbacks(self) -> None:
        report = build_digest_quality_report(
            run_id="run-1",
            generated_at="2026-04-03T00:00:00Z",
            items=[
                _item(
                    "item-1",
                    source_id="skynews",
                    source_name="SkyNews",
                    scrape_status="failed",
                    scrape_error="HTTP Error 403: Forbidden",
                )
            ],
            errors=[],
            summary={"raw_fetched_items": 1, "scrape_failed": 1},
            warnings=[],
        )

        self.assertEqual(report["status"], "warn")
        self.assertEqual(report["scrape_failed"], 1)
        self.assertEqual(report["unresolved_scrape_failed"], 0)
        self.assertEqual(report["accepted_rss_only_fallback"], 1)
        self.assertEqual(report["top_failing_sources"], [])
        self.assertEqual(
            report["top_accepted_fallback_sources"],
            [{"source": "SkyNews", "count": 1}],
        )
        self.assertEqual(report["scrape_failure_reasons"], [])
        self.assertEqual(
            report["accepted_rss_only_fallback_reasons"],
            [{"reason": "source_blocked_403", "count": 1}],
        )
        self.assertIn(
            "1 article scrape(s) used accepted RSS-only fallback (top reason: source_blocked_403)",
            report["warnings"],
        )

    def test_quality_report_fails_for_missing_required_field_and_included_duplicate_url(
        self,
    ) -> None:
        report = build_digest_quality_report(
            run_id="run-1",
            generated_at="2026-04-03T00:00:00Z",
            items=[
                _item("item-1", title="", link="https://example.com/shared"),
                _item("item-2", link="https://example.com/shared"),
            ],
            errors=[],
            summary={"raw_fetched_items": 2, "scrape_failed": 0},
            warnings=[],
        )

        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["publishable"])
        self.assertEqual(report["included_duplicate_canonical_urls"], 1)
        self.assertIn("title coverage is below 100%", report["blocking_issues"])
        self.assertIn(
            "included articles contain duplicate canonical URLs", report["blocking_issues"]
        )


if __name__ == "__main__":
    unittest.main()
