from __future__ import annotations

import json
import unittest
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from rss_pipeline.config import DigestBuildConfig
from rss_pipeline.logging import StructuredRunLogger
from rss_pipeline.models_digest import DigestItem, FeedRef, SourceRef
from rss_pipeline.pipeline_digest import build_digest, enrich_items_with_scrape, fetch_feed_items


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _item(
    item_id: str,
    *,
    link: str | None = None,
    source_id: str = "source-a",
    source_name: str = "Source A",
) -> DigestItem:
    source = SourceRef(id=source_id, name=source_name)
    feed = FeedRef(name="Top", url="https://example.com/feed.xml")
    return DigestItem(
        id=item_id,
        title=f"Title {item_id}",
        link=link if link is not None else f"https://example.com/{item_id}",
        summary=f"Summary {item_id}",
        published="2026-04-03T00:00:00Z",
        source=source,
        feed=feed,
    )


class _FakeHTTPResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class DigestStructuredLoggingTests(unittest.TestCase):
    def test_fetch_feed_items_flags_entries_missing_rss_content(self) -> None:
        rss_body = b"""<?xml version="1.0" encoding="UTF-8" ?>
        <rss version="2.0">
          <channel>
            <title>Example Feed</title>
            <item>
              <title>Empty Story</title>
              <link>https://example.com/empty</link>
              <pubDate>Fri, 03 Apr 2026 12:00:00 GMT</pubDate>
            </item>
            <item>
              <title>Full Story</title>
              <link>https://example.com/full</link>
              <description>Full RSS content.</description>
              <pubDate>Fri, 03 Apr 2026 13:00:00 GMT</pubDate>
            </item>
            <item>
              <title>Watch: Full Video Story</title>
              <link>https://example.com/video/full</link>
              <description>Video story text.</description>
              <pubDate>Fri, 03 Apr 2026 14:00:00 GMT</pubDate>
            </item>
          </channel>
        </rss>"""
        feed = {
            "source_id": "source-a",
            "source_name": "Source A",
            "feed_name": "Top",
            "feed_url": "https://example.com/feed.xml",
            "topic_tags": ["news"],
        }

        with (
            warnings.catch_warnings(),
            patch(
                "rss_pipeline.pipeline_digest.urllib.request.urlopen",
                return_value=_FakeHTTPResponse(rss_body),
            ),
        ):
            warnings.filterwarnings(
                "ignore",
                message="'count' is passed as positional argument",
                category=DeprecationWarning,
            )
            items = fetch_feed_items(
                feed=feed,
                max_items=3,
                timeout_seconds=1,
                user_agent="test-agent",
            )

        self.assertEqual(len(items), 3)
        self.assertFalse(items[0].include_in_newsfeed)
        self.assertEqual(items[0].content_type, "missing_content")
        self.assertEqual(items[0].newsfeed_exclusion_reason, "missing_rss_content")
        self.assertEqual(items[0].summary, "")
        self.assertEqual(items[0].audit["content"]["status"], "missing")
        self.assertEqual(items[0].audit["content"]["content_type"], "missing_content")
        self.assertTrue(items[0].audit["content"]["exclude_from_newsfeed"])
        self.assertEqual(items[0].audit["content"]["reason"], "missing_rss_content")
        self.assertTrue(items[1].include_in_newsfeed)
        self.assertEqual(items[1].content_type, "news_article")
        self.assertIsNone(items[1].newsfeed_exclusion_reason)
        self.assertEqual(items[1].summary, "Full RSS content.")
        self.assertEqual(items[1].audit["content"]["status"], "present")
        self.assertFalse(items[2].include_in_newsfeed)
        self.assertEqual(items[2].content_type, "video")
        self.assertEqual(items[2].newsfeed_exclusion_reason, "unsupported_content_type:video")
        self.assertEqual(items[2].audit["content"]["status"], "present")
        self.assertFalse(items[2].audit["content"]["newslens_eligible"])

    def test_build_digest_writes_run_feed_and_article_events(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "feed_catalog").mkdir(parents=True, exist_ok=True)
            (root / "feed_catalog" / "rss_feeds.json").write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "id": "source-a",
                                "name": "Source A",
                                "enabled": True,
                                "feeds": [
                                    {
                                        "name": "Top",
                                        "url": "https://example.com/feed.xml",
                                        "enabled": True,
                                        "topic_tags": ["news"],
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            duplicate_link = "https://example.com/shared"

            def fake_fetch_feed_items(**_: object) -> list[DigestItem]:
                return [
                    _item("item-1", link=duplicate_link),
                    _item("item-2", link=duplicate_link),
                ]

            config = DigestBuildConfig(
                catalog=Path("feed_catalog/rss_feeds.json"),
                output=Path("data/rss_openai_daily.json"),
                archive_dir=Path("data/history"),
                archive_enabled=False,
                skip_seen_items=False,
                scrape_enabled=False,
                openai_enabled=False,
                max_sources=1,
                feeds_per_source=1,
                max_items_per_feed=2,
                run_log_dir=Path("data/analysis/digest_run_logs"),
            )

            with patch(
                "rss_pipeline.pipeline_digest.fetch_feed_items", side_effect=fake_fetch_feed_items
            ):
                result = build_digest(config, repo_root=root)

            run_log_path = Path(str(result["run_log"]))
            self.assertTrue(run_log_path.exists())

            payload = json.loads(
                (root / "data" / "rss_openai_daily.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["audit"]["run_log"], str(run_log_path))
            self.assertEqual(payload["audit"]["schema_validation"]["status"], "pass")
            self.assertEqual(payload["quality_report"]["schema_validation"]["status"], "pass")
            self.assertEqual(payload["audit"]["dedupe"]["in_run_duplicates"], 1)
            self.assertEqual(payload["items"][0]["canonical"]["id"], "item-1")
            self.assertEqual(payload["items"][0]["canonical"]["url"], duplicate_link)
            self.assertEqual(payload["items"][0]["canonical"]["source_id"], "source-a")
            self.assertEqual(payload["items"][0]["audit"]["scrape"]["status"], "skipped")
            self.assertEqual(payload["items"][0]["audit"]["scrape"]["reason"], "scrape_disabled")
            self.assertEqual(payload["items"][0]["llm_input_status"], "review")
            self.assertEqual(payload["items"][0]["llm_input_reason"], "scrape_disabled")
            self.assertFalse(payload["items"][0]["ready_for_llm_judge"])
            self.assertEqual(payload["items"][0]["audit"]["openai"]["status"], "skipped")
            self.assertEqual(payload["items"][0]["audit"]["openai"]["reason"], "openai_disabled")
            self.assertEqual(payload["items"][0]["quality_status"], "warn")
            self.assertEqual(
                payload["items"][0]["quality_flags"][0]["code"], "scrape_not_attempted"
            )
            self.assertEqual(payload["items"][0]["audit"]["quality"]["status"], "warn")
            self.assertEqual(
                payload["quality_report"]["item_quality"]["status_counts"], {"warn": 1}
            )
            self.assertEqual(payload["quality_report"]["llm_review_items"], 1)

            events = _read_jsonl(run_log_path)
            event_names = [str(event["event"]) for event in events]

            self.assertEqual(events[0]["event"], "run_started")
            self.assertIn("feed_fetch_started", event_names)
            self.assertIn("feed_fetch_succeeded", event_names)
            self.assertEqual(event_names.count("article_seen"), 2)
            self.assertIn("article_deduped", event_names)
            self.assertIn("article_fetch_skipped", event_names)
            self.assertIn("article_llm_input_assessed", event_names)
            self.assertIn("llm_input_summary", event_names)
            self.assertIn("article_scoring_skipped", event_names)
            self.assertIn("article_quality_assessed", event_names)
            self.assertIn("quality_summary", event_names)
            self.assertIn("json_validation_succeeded", event_names)
            self.assertEqual(events[-1]["event"], "run_completed")
            self.assertTrue(all(event["run_id"] == result["run_id"] for event in events))
            self.assertTrue(all(isinstance(event.get("logged_at"), str) for event in events))

    def test_enrich_items_with_scrape_logs_success_and_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_logger = StructuredRunLogger(root / "run.jsonl", run_id="run-1")
            items = [_item("item-1"), _item("item-2")]

            def fake_scrape_article(link: str, **_: object) -> SimpleNamespace:
                if link.endswith("item-2"):
                    raise TimeoutError("timed out")
                return SimpleNamespace(
                    to_dict=lambda: {
                        "final_url": link,
                        "status_code": 200,
                        "body_text": "Article body",
                    }
                )

            with patch(
                "rss_pipeline.pipeline_digest.scrape_links.scrape_article",
                side_effect=fake_scrape_article,
            ):
                stats = enrich_items_with_scrape(
                    items,
                    limit=None,
                    timeout_seconds=1.0,
                    sleep_seconds=0.0,
                    user_agent="test-agent",
                    audit_logger=run_logger,
                )
            run_logger.close()

            self.assertEqual(stats["attempts"], 2)
            self.assertEqual(stats["success"], 1)
            self.assertEqual(stats["failed"], 1)
            self.assertEqual(items[0].audit["scrape"]["status"], "succeeded")
            self.assertEqual(items[0].audit["scrape"]["status_code"], 200)
            self.assertEqual(items[1].audit["scrape"]["status"], "failed")
            self.assertEqual(items[1].audit["scrape"]["reason"], "fetch_timeout")
            self.assertEqual(items[1].audit["scrape"]["category"], "transient_network")
            self.assertTrue(items[1].audit["scrape"]["retryable"])
            self.assertEqual(items[1].audit["scrape"]["exception_type"], "TimeoutError")

            events = _read_jsonl(root / "run.jsonl")
            event_names = [str(event["event"]) for event in events]
            self.assertEqual(event_names.count("article_fetch_started"), 2)
            self.assertEqual(event_names.count("article_fetch_succeeded"), 1)
            self.assertEqual(event_names.count("article_fetch_failed"), 1)
            failed_event = next(
                event for event in events if event["event"] == "article_fetch_failed"
            )
            self.assertEqual(failed_event["outcome_state"], "scrape_failed")
            self.assertEqual(failed_event["exception_type"], "TimeoutError")
            self.assertEqual(failed_event["reason"], "fetch_timeout")

    def test_enrich_items_with_scrape_classifies_http_403_failures(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_logger = StructuredRunLogger(root / "run.jsonl", run_id="run-1")
            items = [_item("item-1")]

            with patch(
                "rss_pipeline.pipeline_digest.scrape_links.scrape_article",
                side_effect=HTTPError(
                    url=items[0].link,
                    code=403,
                    msg="Forbidden",
                    hdrs={},
                    fp=None,
                ),
            ):
                stats = enrich_items_with_scrape(
                    items,
                    limit=None,
                    timeout_seconds=1.0,
                    sleep_seconds=0.0,
                    user_agent="test-agent",
                    audit_logger=run_logger,
                )
            run_logger.close()

            self.assertEqual(stats["failed"], 1)
            scrape_audit = items[0].audit["scrape"]
            self.assertEqual(scrape_audit["status"], "failed")
            self.assertEqual(scrape_audit["reason"], "source_blocked_403")
            self.assertEqual(scrape_audit["category"], "source_blocked")
            self.assertEqual(scrape_audit["http_status"], 403)
            self.assertFalse(scrape_audit["retryable"])
            self.assertEqual(
                scrape_audit["failure_taxonomy"]["source_action"],
                "source_adapter_or_rss_fallback",
            )

            failed_event = next(
                event
                for event in _read_jsonl(root / "run.jsonl")
                if event["event"] == "article_fetch_failed"
            )
            self.assertEqual(failed_event["reason"], "source_blocked_403")
            self.assertEqual(failed_event["http_status"], 403)

    def test_enrich_items_with_scrape_accepts_known_rss_only_fallback_policy(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_logger = StructuredRunLogger(root / "run.jsonl", run_id="run-1")
            items = [_item("item-1", source_id="skynews", source_name="SkyNews")]

            with patch(
                "rss_pipeline.pipeline_digest.scrape_links.scrape_article",
                side_effect=HTTPError(
                    url=items[0].link,
                    code=403,
                    msg="Forbidden",
                    hdrs={},
                    fp=None,
                ),
            ):
                stats = enrich_items_with_scrape(
                    items,
                    limit=None,
                    timeout_seconds=1.0,
                    sleep_seconds=0.0,
                    user_agent="test-agent",
                    audit_logger=run_logger,
                )
            run_logger.close()

            self.assertEqual(stats["failed"], 1)
            self.assertEqual(stats["accepted_fallback"], 1)
            scrape_audit = items[0].audit["scrape"]
            self.assertEqual(scrape_audit["reason"], "source_blocked_403")
            self.assertEqual(
                scrape_audit["accepted_fallback"]["code"],
                "rss_only_fallback_accepted",
            )
            self.assertEqual(
                scrape_audit["accepted_fallback"]["policy_id"],
                "skynews-rss-only-on-403",
            )

            events = _read_jsonl(root / "run.jsonl")
            failed_event = next(
                event for event in events if event["event"] == "article_fetch_failed"
            )
            self.assertTrue(failed_event["accepted_fallback"])
            self.assertEqual(failed_event["outcome_state"], "included_rss_only_fallback")
            self.assertIn(
                "article_fetch_fallback_accepted",
                {str(event["event"]) for event in events},
            )


if __name__ == "__main__":
    unittest.main()
