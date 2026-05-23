from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from rss_pipeline.cli import run_cli
from rss_pipeline.config import FeedAuditConfig
from rss_pipeline.feed_audit import evaluate_feed_audit_gates, run_feed_audit
from rss_pipeline.models_digest import DigestItem, FeedRef, SourceRef


def _catalog_payload() -> dict[str, object]:
    return {
        "sources": [
            {
                "id": "source-a",
                "name": "Source A",
                "enabled": True,
                "feeds": [
                    {
                        "name": "Top",
                        "url": "https://example.com/feed-a.xml",
                        "enabled": True,
                        "topic_tags": ["general"],
                    }
                ],
            },
            {
                "id": "source-b",
                "name": "Source B",
                "enabled": True,
                "feeds": [
                    {
                        "name": "World",
                        "url": "https://example.com/feed-b.xml",
                        "enabled": True,
                        "topic_tags": ["world"],
                    }
                ],
            },
        ]
    }


def _item(
    feed: dict[str, object],
    item_id: str,
    *,
    title: str,
    content_type: str = "news_article",
    summary: str = "RSS summary.",
    include_in_newsfeed: bool = True,
    newsfeed_exclusion_reason: str | None = None,
) -> DigestItem:
    source = SourceRef(
        id=str(feed["source_id"]),
        name=str(feed["source_name"]),
    )
    feed_ref = FeedRef(
        name=str(feed["feed_name"]),
        url=str(feed["feed_url"]),
    )
    return DigestItem(
        id=item_id,
        title=title,
        link=f"https://example.com/{item_id}",
        summary=summary,
        published="2026-04-03T00:00:00Z",
        source=source,
        feed=feed_ref,
        content_type=content_type,
        include_in_newsfeed=include_in_newsfeed,
        newsfeed_exclusion_reason=newsfeed_exclusion_reason,
        audit={
            "content": {
                "status": "present" if summary else "missing",
                "content_type": content_type,
            }
        },
    )


def _fake_fetch_feed_items(
    *,
    feed: dict[str, object],
    max_items: int,
    timeout_seconds: int,
    user_agent: str,
) -> list[DigestItem]:
    del max_items, timeout_seconds, user_agent
    if feed["source_id"] == "source-b":
        raise TimeoutError("feed timed out")
    return [
        _item(feed, "article-1", title="Article 1"),
        _item(
            feed,
            "video-1",
            title="Watch: Video 1",
            content_type="video",
            include_in_newsfeed=False,
            newsfeed_exclusion_reason="unsupported_content_type:video",
        ),
        _item(
            feed,
            "missing-1",
            title="Missing Content",
            content_type="missing_content",
            summary="",
            include_in_newsfeed=False,
            newsfeed_exclusion_reason="missing_rss_content",
        ),
    ]


class FeedAuditTests(unittest.TestCase):
    def test_run_feed_audit_summarizes_feed_and_item_quality(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = root / "feed_catalog" / "rss_feeds.json"
            output_path = root / "data" / "analysis" / "feed_audit" / "rss_feed_audit.json"
            history_dir = root / "data" / "analysis" / "feed_audit" / "history"
            catalog_path.parent.mkdir(parents=True)
            catalog_path.write_text(json.dumps(_catalog_payload()), encoding="utf-8")

            config = FeedAuditConfig(
                catalog=catalog_path,
                output=output_path,
                archive_history_dir=history_dir,
                max_sources=2,
                feeds_per_source=1,
                max_items_per_feed=3,
            )

            with patch(
                "rss_pipeline.feed_audit.fetch_feed_items",
                side_effect=_fake_fetch_feed_items,
            ):
                report = run_feed_audit(config, repo_root=root, limit=5)

            self.assertEqual(report["status"], "warn")
            self.assertEqual(report["summary"]["selected_feeds"], 2)
            self.assertEqual(report["summary"]["feed_fetch_failed"], 1)
            self.assertEqual(report["summary"]["raw_fetched_items"], 3)
            self.assertEqual(report["summary"]["typical_newsfeed_items"], 1)
            self.assertEqual(report["summary"]["accepted_content_type_filter"], 1)
            self.assertEqual(report["summary"]["rss_missing_content"], 1)
            self.assertEqual(report["quality_gate_metrics"]["feed_fetch_failed"], 1)
            self.assertEqual(
                report["quality_gate_metrics"]["accepted_content_type_filter_items"],
                1,
            )
            self.assertEqual(report["quality_gate_metrics"]["missing_rss_content_items"], 1)
            self.assertTrue(output_path.exists())
            self.assertTrue(list(history_dir.glob("rss_feed_audit_*.json")))
            self.assertTrue(Path(report["audit"]["run_log"]).exists())
            self.assertIn({"issue": "feed_fetch_failed", "count": 1}, report["issue_counts"])
            self.assertIn(
                {"issue": "content_type_filter_accepted", "count": 1},
                report["issue_counts"],
            )
            self.assertEqual(report["feed_fetch_errors"][0]["type"], "TimeoutError")
            self.assertEqual(
                report["source_health_summary"]["status_counts"],
                {"healthy": 0, "watch": 1, "hold_candidate": 1},
            )
            source_health = {row["source_id"]: row for row in report["source_health"]}
            self.assertEqual(source_health["source-a"]["status"], "watch")
            self.assertEqual(
                source_health["source-a"]["recommended_action"],
                "review_source_quality",
            )
            self.assertEqual(source_health["source-a"]["raw_items"], 3)
            self.assertEqual(source_health["source-a"]["newsfeed_excluded"], 2)
            self.assertEqual(
                source_health["source-a"]["missing_rss_content_items"],
                1,
            )
            self.assertEqual(
                source_health["source-b"]["status"],
                "hold_candidate",
            )
            self.assertEqual(
                source_health["source-b"]["recommended_action"],
                "hold_or_disable_source",
            )
            self.assertEqual(source_health["source-b"]["feed_fetch_failed"], 1)
            self.assertEqual(report["sources_needing_review"][0]["source_id"], "source-b")

    def test_evaluate_feed_audit_gates_reports_threshold_violations(self) -> None:
        report = {
            "quality_gate_metrics": {
                "feed_fetch_failed": 1,
                "missing_rss_content_items": 2,
                "unknown_content_type_items": 0,
                "unsupported_content_type_items": 0,
                "accepted_content_type_filter_items": 3,
            }
        }

        gate = evaluate_feed_audit_gates(
            report,
            max_feed_fetch_failures=0,
            max_missing_rss_content=1,
            max_accepted_content_type_filters=2,
        )

        self.assertEqual(gate["status"], "fail")
        self.assertEqual(
            {violation["metric"] for violation in gate["violations"]},
            {
                "feed_fetch_failed",
                "missing_rss_content_items",
                "accepted_content_type_filter_items",
            },
        )

    def test_validate_feed_audit_cli_writes_output_and_applies_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = root / "feed_catalog" / "rss_feeds.json"
            output_path = root / "feed_audit.json"
            history_dir = root / "history"
            catalog_path.parent.mkdir(parents=True)
            catalog_path.write_text(json.dumps(_catalog_payload()), encoding="utf-8")
            stdout = StringIO()

            with (
                patch(
                    "rss_pipeline.feed_audit.fetch_feed_items",
                    side_effect=_fake_fetch_feed_items,
                ),
                redirect_stdout(stdout),
            ):
                exit_code = run_cli(
                    [
                        "validate",
                        "feed-audit",
                        "--catalog",
                        str(catalog_path),
                        "--output",
                        str(output_path),
                        "--archive-history-dir",
                        str(history_dir),
                        "--max-sources",
                        "2",
                        "--feeds-per-source",
                        "1",
                        "--max-items-per-feed",
                        "3",
                        "--max-feed-fetch-failures",
                        "0",
                    ]
                )

            self.assertEqual(exit_code, 1)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["quality_gate"]["status"], "fail")
            self.assertEqual(
                payload["quality_gate"]["violations"][0]["metric"],
                "feed_fetch_failed",
            )
            self.assertIn("Feed audit:", stdout.getvalue())
            self.assertIn("Source health:", stdout.getvalue())
            self.assertIn("Sources needing review:", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
