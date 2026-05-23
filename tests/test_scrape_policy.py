from __future__ import annotations

import unittest

from rss_pipeline.failure_taxonomy import classify_scrape_failure
from rss_pipeline.pipeline_digest import select_feeds
from rss_pipeline.scrape_policy import (
    accepted_scrape_fallback_for_source,
    scrape_fallback_policy_for_source,
)


class ScrapePolicyTests(unittest.TestCase):
    def test_default_skynews_policy_accepts_403_with_rss_summary(self) -> None:
        classification = classify_scrape_failure("HTTP Error 403: Forbidden")

        fallback = accepted_scrape_fallback_for_source(
            source_id="skynews",
            source_name="SkyNews",
            summary="RSS summary text.",
            classification=classification,
        )

        self.assertIsNotNone(fallback)
        assert fallback is not None
        self.assertEqual(fallback["code"], "rss_only_fallback_accepted")
        self.assertEqual(fallback["failure_code"], "source_blocked_403")
        self.assertEqual(fallback["policy_id"], "skynews-rss-only-on-403")
        self.assertEqual(fallback["policy_source"], "default")

    def test_policy_requires_matching_failure_code_and_summary(self) -> None:
        timeout = classify_scrape_failure("timed out")
        blocked = classify_scrape_failure("HTTP Error 403: Forbidden")

        self.assertIsNone(
            accepted_scrape_fallback_for_source(
                source_id="skynews",
                source_name="SkyNews",
                summary="RSS summary text.",
                classification=timeout,
            )
        )
        self.assertIsNone(
            accepted_scrape_fallback_for_source(
                source_id="skynews",
                source_name="SkyNews",
                summary="",
                classification=blocked,
            )
        )

    def test_catalog_policy_overrides_default_policy(self) -> None:
        policy = scrape_fallback_policy_for_source(
            source_id="custom-source",
            source_name="Custom Source",
            configured_policy={
                "policy_id": "custom-rss-fallback",
                "failure_codes": ["source_blocked_401"],
                "reason": "paywall_blocks_fetch",
            },
        )

        self.assertIsNotNone(policy)
        assert policy is not None
        self.assertEqual(policy["policy_id"], "custom-rss-fallback")
        self.assertEqual(policy["accepted_failure_codes"], ["source_blocked_401"])
        self.assertEqual(policy["policy_source"], "catalog")

    def test_select_feeds_passes_source_scrape_policy_to_feed_rows(self) -> None:
        feeds = select_feeds(
            {
                "sources": [
                    {
                        "id": "custom-source",
                        "name": "Custom Source",
                        "enabled": True,
                        "scrape_policy": {
                            "policy_id": "custom-rss-fallback",
                            "accepted_failure_codes": ["source_blocked_403"],
                        },
                        "feeds": [
                            {
                                "name": "Top",
                                "url": "https://example.com/feed.xml",
                                "enabled": True,
                            }
                        ],
                    }
                ]
            },
            max_sources=1,
            feeds_per_source=1,
            source_ids=(),
        )

        self.assertEqual(feeds[0]["scrape_fallback_policy"]["policy_id"], "custom-rss-fallback")
        self.assertEqual(feeds[0]["scrape_fallback_policy"]["policy_source"], "catalog")


if __name__ == "__main__":
    unittest.main()
