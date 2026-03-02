from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from rss_pipeline.models_digest import DigestItem, FeedRef, SourceRef
from rss_pipeline.pipeline_digest import (
    collect_seen_keys,
    dedupe_keys_for_item,
    normalize_link_for_dedupe,
)


class DigestDedupeTests(unittest.TestCase):
    def test_normalize_link_for_dedupe_strips_tracking_and_fragment(self) -> None:
        link = "HTTPS://Example.com/path/?utm_source=rss&keep=1&gclid=abc#section"
        self.assertEqual(normalize_link_for_dedupe(link), "https://example.com/path?keep=1")

    def test_collect_seen_keys_reads_current_and_history(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "rss_openai_daily.json"
            history = root / "history"
            history.mkdir(parents=True, exist_ok=True)

            output.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "id-current",
                                "link": "https://example.com/current?utm_source=rss",
                                "title": "Current item",
                                "source": {"id": "src-a"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (history / "rss_openai_daily_2026-03-01.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "id-history",
                                "link": "https://example.com/history",
                                "title": "History item",
                                "source": {"id": "src-b"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            seen, stats = collect_seen_keys(output_path=output, history_dir=history)

            self.assertIn("id:id-current", seen)
            self.assertIn("id:id-history", seen)
            self.assertIn("link:https://example.com/current", seen)
            self.assertIn("link:https://example.com/history", seen)
            self.assertEqual(stats["current_items"], 1)
            self.assertEqual(stats["history_items"], 1)
            self.assertEqual(stats["history_files"], 1)

    def test_dedupe_keys_for_item_contains_source_title_fallback(self) -> None:
        item = DigestItem(
            id="abc123",
            title="Some Headline",
            link="",
            summary="",
            published="",
            source=SourceRef(id="src-x", name="Source X"),
            feed=FeedRef(name="Feed", url="https://example.com/feed.xml"),
        )

        keys = dedupe_keys_for_item(item)
        self.assertIn("id:abc123", keys)
        self.assertIn("title:src-x:some headline", keys)


if __name__ == "__main__":
    unittest.main()
