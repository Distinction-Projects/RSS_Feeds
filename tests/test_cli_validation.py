from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from rss_pipeline.cli import run_cli


def _legacy_digest_payload() -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "generated_at": "2026-04-03T00:00:00Z",
        "run": {
            "id": "run-1",
            "generated_at": "2026-04-03T00:00:00Z",
        },
        "request": {},
        "sources": {},
        "openai": {},
        "cache": {},
        "items": [
            {
                "id": "item-1",
                "title": "Title 1",
                "link": "https://example.com/article-1",
                "summary": "Summary 1",
                "published": "2026-04-03T00:00:00Z",
                "source": {"id": "source-a", "name": "Source A"},
                "feed": {"name": "Top", "url": "https://example.com/feed.xml"},
                "topic_tags": [],
                "ai_tags": [],
                "audit": {},
                "scraped": None,
                "scrape_error": None,
            }
        ],
        "errors": [],
        "audit": {},
    }


class CliValidationTests(unittest.TestCase):
    def test_validate_digest_accepts_legacy_payload_in_compat_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            digest_path = Path(temp_dir) / "legacy_digest.json"
            digest_path.write_text(json.dumps(_legacy_digest_payload()), encoding="utf-8")

            with redirect_stdout(StringIO()):
                exit_code = run_cli(["validate", "digest", "--digest", str(digest_path)])

        self.assertEqual(exit_code, 0)

    def test_validate_digest_rejects_legacy_payload_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            digest_path = Path(temp_dir) / "legacy_digest.json"
            digest_path.write_text(json.dumps(_legacy_digest_payload()), encoding="utf-8")

            with redirect_stdout(StringIO()):
                exit_code = run_cli(
                    ["validate", "digest", "--digest", str(digest_path), "--strict"]
                )

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
