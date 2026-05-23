from __future__ import annotations

import unittest

from rss_pipeline.models_digest import DigestItem, FeedRef, SourceRef
from rss_pipeline.normalization import (
    canonical_source_id,
    compact_whitespace,
    normalize_datetime_text,
    normalize_tags,
    normalize_title,
    normalize_url,
)


class NormalizationTests(unittest.TestCase):
    def test_normalize_url_strips_tracking_fragment_and_trailing_slash(self) -> None:
        url = "HTTPS://Example.com/path/?utm_source=rss&keep=1&fbclid=abc#section"

        self.assertEqual(normalize_url(url), "https://example.com/path?keep=1")

    def test_normalize_title_removes_source_suffix(self) -> None:
        self.assertEqual(
            normalize_title(
                "  Headline &amp; Context | Example News  ", source_name="Example News"
            ),
            "Headline & Context",
        )

    def test_normalize_tags_dedupes_case_insensitively(self) -> None:
        self.assertEqual(
            normalize_tags([" Policy ", "policy", "", "Science"]), ["Policy", "Science"]
        )

    def test_canonical_source_id_slugifies_names(self) -> None:
        self.assertEqual(canonical_source_id("PBS NewsHour"), "pbs-newshour")

    def test_normalize_datetime_text_handles_rfc2822(self) -> None:
        self.assertEqual(
            normalize_datetime_text("Mon, 02 Mar 2026 15:25:29 -0500"),
            "2026-03-02T20:25:29Z",
        )

    def test_compact_whitespace_unescapes_html_entities(self) -> None:
        self.assertEqual(compact_whitespace(" A&nbsp;  B &amp; C "), "A B & C")

    def test_digest_item_canonical_url_prefers_scraped_canonical_url(self) -> None:
        item = DigestItem(
            id="item-1",
            title="Title",
            link="https://example.com/path?utm_source=rss",
            summary="Summary",
            published="2026-04-03T00:00:00Z",
            source=SourceRef(id="source-a", name="Source A"),
            feed=FeedRef(name="Top", url="https://example.com/feed.xml"),
            scraped={"canonical_url": "HTTPS://Example.com/path/?gclid=abc#frag"},
        )

        self.assertEqual(item.canonical_url(), "https://example.com/path")
        self.assertEqual(item.to_dict()["canonical"]["url"], "https://example.com/path")


if __name__ == "__main__":
    unittest.main()
