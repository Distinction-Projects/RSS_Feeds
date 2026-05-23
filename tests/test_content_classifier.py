from __future__ import annotations

import unittest

from rss_pipeline.content_classifier import (
    classify_item_payload_content_type,
    classify_story_content_type,
)


def _classify(
    title: str,
    *,
    link: str = "https://example.com/story",
    feed_name: str = "Top Stories",
    source_name: str = "Example News",
    topic_tags: list[str] | None = None,
    rss_content: str = "A reported story with useful RSS text.",
):
    return classify_story_content_type(
        title=title,
        link=link,
        feed_name=feed_name,
        source_name=source_name,
        topic_tags=topic_tags or ["news"],
        rss_content=rss_content,
    )


class ContentClassifierTests(unittest.TestCase):
    def test_missing_rss_content_is_ineligible(self) -> None:
        result = _classify("Story title", rss_content="   ")

        self.assertEqual(result.content_type, "missing_content")
        self.assertFalse(result.newslens_eligible)
        self.assertIn("rss_content:empty", result.matched_signals)

    def test_video_story_is_labeled_and_ineligible(self) -> None:
        result = _classify(
            "Watch: Briefing from the capital",
            link="https://example.com/video/briefing",
        )

        self.assertEqual(result.content_type, "video")
        self.assertFalse(result.newslens_eligible)

    def test_podcast_story_is_labeled_and_ineligible(self) -> None:
        result = _classify("Podcast: The week in policy", feed_name="Podcasts")

        self.assertEqual(result.content_type, "podcast")
        self.assertFalse(result.newslens_eligible)

    def test_press_release_is_labeled_and_ineligible(self) -> None:
        result = _classify(
            "Company announces quarterly update",
            source_name="PR Newswire",
        )

        self.assertEqual(result.content_type, "press_release")
        self.assertFalse(result.newslens_eligible)

    def test_item_payload_classifier_supports_legacy_flat_payloads(self) -> None:
        result = classify_item_payload_content_type(
            {
                "title": "Watch: Payload Story",
                "link": "https://example.com/watch/story",
                "summary": "Video summary.",
                "source_name": "Example Source",
                "feed_name": "Videos",
                "topic_tags": "video,world",
            }
        )

        self.assertEqual(result.content_type, "video")
        self.assertFalse(result.newslens_eligible)

    def test_opinion_is_labeled_but_kept_eligible(self) -> None:
        result = _classify("Opinion: Congress should revisit the proposal")

        self.assertEqual(result.content_type, "opinion")
        self.assertTrue(result.newslens_eligible)

    def test_default_story_is_news_article(self) -> None:
        result = _classify("Officials announce new policy")

        self.assertEqual(result.content_type, "news_article")
        self.assertTrue(result.newslens_eligible)


if __name__ == "__main__":
    unittest.main()
