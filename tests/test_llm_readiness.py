from __future__ import annotations

import unittest

from rss_pipeline.llm_readiness import (
    apply_item_llm_readiness,
    llm_readiness_from_payload,
    usable_scraped_text,
)
from rss_pipeline.models_digest import DigestItem, FeedRef, SourceRef


def _item(
    item_id: str,
    *,
    source_id: str = "source-a",
    source_name: str = "Source A",
    summary: str = "RSS summary",
    include_in_newsfeed: bool = True,
    newsfeed_exclusion_reason: str | None = None,
) -> DigestItem:
    return DigestItem(
        id=item_id,
        title=f"Title {item_id}",
        link=f"https://example.com/{item_id}",
        summary=summary,
        published="2026-04-03T00:00:00Z",
        source=SourceRef(id=source_id, name=source_name),
        feed=FeedRef(name="Top", url="https://example.com/feed.xml"),
        include_in_newsfeed=include_in_newsfeed,
        newsfeed_exclusion_reason=newsfeed_exclusion_reason,
    )


class LLMReadinessTests(unittest.TestCase):
    def test_ready_when_scraped_text_exceeds_threshold(self) -> None:
        item = _item("item-1")
        item.scraped = {"body_text": "Article body " * 40}
        item.audit["scrape"] = {"status": "succeeded"}

        readiness = apply_item_llm_readiness(item)

        self.assertEqual(readiness.status, "ready")
        self.assertTrue(item.ready_for_llm_judge)
        self.assertEqual(item.llm_input_reason, "scraped_text_ready")
        self.assertGreaterEqual(item.scraped_text_chars, 250)

    def test_successful_scrape_with_empty_text_requires_review(self) -> None:
        item = _item("item-1")
        item.scraped = {"body_text": "", "lead_paragraph": ""}
        item.audit["scrape"] = {"status": "succeeded"}

        readiness = apply_item_llm_readiness(item)

        self.assertEqual(readiness.status, "review")
        self.assertEqual(readiness.reason, "empty_scraped_text")
        self.assertFalse(item.ready_for_llm_judge)
        self.assertEqual(item.llm_input_flags[0]["code"], "empty_scraped_text")

    def test_unsupported_content_is_excluded_before_judge(self) -> None:
        item = _item(
            "item-1",
            include_in_newsfeed=False,
            newsfeed_exclusion_reason="unsupported_content_type:video",
        )

        readiness = apply_item_llm_readiness(item)

        self.assertEqual(readiness.status, "exclude")
        self.assertEqual(readiness.reason, "unsupported_content_type:video")
        self.assertFalse(item.ready_for_llm_judge)

    def test_accepted_rss_fallback_is_not_ready_for_normal_judge(self) -> None:
        item = _item("item-1", source_id="skynews", source_name="SkyNews")
        item.scrape_error = "HTTP Error 403: Forbidden"
        item.audit["scrape"] = {"status": "failed", "error": item.scrape_error}

        readiness = apply_item_llm_readiness(item)

        self.assertEqual(readiness.status, "rss_fallback")
        self.assertEqual(readiness.source, "rss_summary")
        self.assertFalse(item.ready_for_llm_judge)

    def test_payload_reader_infers_legacy_short_scraped_text(self) -> None:
        readiness = llm_readiness_from_payload(
            {
                "id": "legacy-1",
                "title": "Legacy",
                "summary": "RSS summary",
                "include_in_newsfeed": True,
                "scraped": {"body_text": "short"},
                "audit": {"scrape": {"status": "succeeded"}},
            }
        )

        self.assertEqual(readiness.status, "review")
        self.assertEqual(readiness.reason, "short_scraped_text")

    def test_usable_scraped_text_uses_lead_when_body_is_missing(self) -> None:
        self.assertEqual(
            usable_scraped_text({"lead_paragraph": " Lead text. ", "body_text": ""}),
            "Lead text.",
        )


if __name__ == "__main__":
    unittest.main()
