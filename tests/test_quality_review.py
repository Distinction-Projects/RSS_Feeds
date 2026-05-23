from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from rss_pipeline.cli import run_cli
from rss_pipeline.quality_review import build_digest_quality_review, evaluate_quality_gates


def _digest_payload() -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "generated_at": "2026-04-03T00:00:00Z",
        "run": {"id": "run-1", "generated_at": "2026-04-03T00:00:00Z"},
        "items": [
            {
                "id": "clean-1",
                "title": "Clean Story",
                "link": "https://example.com/clean",
                "summary": "Clean summary",
                "published": "2026-04-03T00:00:00Z",
                "source": {"id": "source-a", "name": "Source A"},
                "feed": {"name": "Top", "url": "https://example.com/feed.xml"},
                "content_type": "news_article",
                "include_in_newsfeed": True,
                "ai_summary": "AI summary",
                "scraped": {"body_text": "Article body " * 40},
                "scraped_text_chars": 520,
                "llm_input_status": "ready",
                "llm_input_reason": "scraped_text_ready",
                "llm_input_source": "scraped_text",
                "llm_input_flags": [],
                "ready_for_llm_judge": True,
                "quality_status": "clean",
                "quality_flags": [],
                "audit": {
                    "scrape": {"status": "succeeded"},
                    "llm_input": {
                        "status": "ready",
                        "ready_for_llm_judge": True,
                        "reason": "scraped_text_ready",
                        "source": "scraped_text",
                        "scraped_text_chars": 520,
                        "rss_summary_chars": 13,
                        "min_scraped_text_chars": 250,
                        "flags": [],
                    },
                },
            },
            {
                "id": "video-1",
                "title": "Watch: Video Story",
                "link": "https://example.com/video",
                "summary": "Video summary",
                "published": "2026-04-03T00:00:00Z",
                "source": {"id": "source-b", "name": "Source B"},
                "feed": {"name": "Video", "url": "https://example.com/video.xml"},
                "content_type": "video",
                "include_in_newsfeed": False,
                "newsfeed_exclusion_reason": "unsupported_content_type:video",
                "ai_summary": "",
                "quality_status": "warn",
                "quality_flags": [
                    {
                        "code": "unsupported_content_type",
                        "severity": "warn",
                        "message": "Story content type is not eligible for NewsLens.",
                        "detail": "video",
                    }
                ],
                "audit": {},
            },
            {
                "id": "legacy-1",
                "title": "",
                "link": "https://example.com/legacy",
                "summary": "",
                "published": "",
                "source_name": "Legacy Source",
                "feed_name": "Legacy Feed",
                "scrape_error": "HTTP Error 403: Forbidden",
                "ai_summary": "",
                "audit": {},
            },
        ],
    }


class QualityReviewTests(unittest.TestCase):
    def test_build_digest_quality_review_rolls_up_current_and_legacy_payloads(self) -> None:
        review = build_digest_quality_review(_digest_payload(), limit=5)

        self.assertEqual(review["status"], "fail")
        self.assertEqual(review["total_items"], 3)
        self.assertEqual(review["issue_item_count"], 1)
        self.assertEqual(review["status_counts"], {"clean": 2, "fail": 1})
        self.assertEqual(review["cleanliness"]["clean_newsfeed_items"], 1)
        self.assertEqual(review["cleanliness"]["observable_issue_items"], 2)
        self.assertEqual(review["cleanliness"]["warning_or_failure_items"], 1)
        self.assertEqual(review["cleanliness"]["info_only_items"], 1)
        self.assertEqual(review["cleanliness"]["newsfeed_excluded"], 2)
        self.assertIn(
            {"issue": "content_type_filter_accepted", "count": 1},
            review["issue_counts"],
        )
        self.assertIn({"issue": "source_blocked_403", "count": 1}, review["issue_counts"])
        self.assertIn({"issue": "missing_title", "count": 1}, review["issue_counts"])
        self.assertIn(
            {"content_type": "missing_content", "count": 1},
            review["content_type_counts"],
        )
        self.assertIn(
            {"reason": "missing_rss_content", "count": 1},
            review["exclusion_reason_counts"],
        )
        self.assertEqual(
            review["quality_gate_metrics"],
            {
                "unknown_content_type_items": 0,
                "unsupported_content_type_items": 0,
                "accepted_content_type_filter_items": 1,
                "source_blocked_items": 1,
                "accepted_rss_only_fallback_items": 0,
                "llm_ready_items": 1,
                "llm_review_items": 0,
                "llm_excluded_items": 2,
                "llm_rss_fallback_items": 0,
                "empty_scraped_text_items": 0,
                "short_scraped_text_items": 0,
            },
        )
        self.assertEqual(review["top_sources"][0], {"source": "Legacy Source", "count": 4})
        self.assertEqual(review["examples"][0]["id"], "video-1")

    def test_build_digest_quality_review_infers_legacy_content_type(self) -> None:
        review = build_digest_quality_review(
            {
                "generated_at": "2026-04-03T00:00:00Z",
                "items": [
                    {
                        "id": "legacy-video",
                        "title": "Watch: Legacy Video Story",
                        "link": "https://example.com/watch/legacy-video",
                        "summary": "Video summary.",
                        "published": "2026-04-03T00:00:00Z",
                        "source_name": "Legacy Source",
                        "feed_name": "Videos",
                        "ai_summary": "AI summary.",
                    }
                ],
            }
        )

        self.assertEqual(review["status"], "pass")
        self.assertEqual(review["issue_item_count"], 0)
        self.assertEqual(review["cleanliness"]["clean_newsfeed_items"], 0)
        self.assertEqual(review["cleanliness"]["observable_issue_items"], 1)
        self.assertEqual(review["cleanliness"]["info_only_items"], 1)
        self.assertEqual(review["cleanliness"]["newsfeed_excluded"], 1)
        self.assertEqual(
            review["issue_counts"], [{"issue": "content_type_filter_accepted", "count": 1}]
        )
        self.assertEqual(review["content_type_counts"], [{"content_type": "video", "count": 1}])
        self.assertEqual(
            review["exclusion_reason_counts"],
            [{"reason": "unsupported_content_type:video", "count": 1}],
        )
        self.assertFalse(review["examples"][0]["include_in_newsfeed"])
        self.assertEqual(
            review["examples"][0]["newsfeed_exclusion_reason"],
            "unsupported_content_type:video",
        )

    def test_evaluate_quality_gates_reports_threshold_violations(self) -> None:
        review = build_digest_quality_review(_digest_payload(), limit=5)

        gate = evaluate_quality_gates(
            review,
            max_unknown_content_types=0,
            max_unsupported_content_types=0,
            max_accepted_content_type_filters=0,
            max_source_blocked=0,
        )

        self.assertEqual(gate["status"], "fail")
        self.assertEqual(
            {violation["metric"] for violation in gate["violations"]},
            {"accepted_content_type_filter_items", "source_blocked_items"},
        )

    def test_evaluate_quality_gates_reports_llm_input_threshold_violations(self) -> None:
        review = build_digest_quality_review(
            {
                "generated_at": "2026-04-03T00:00:00Z",
                "items": [
                    {
                        "id": "short-1",
                        "title": "Short scraped story",
                        "link": "https://example.com/short",
                        "summary": "Summary.",
                        "published": "2026-04-03T00:00:00Z",
                        "source": {"id": "source-a", "name": "Source A"},
                        "feed": {"name": "Top", "url": "https://example.com/feed.xml"},
                        "content_type": "news_article",
                        "include_in_newsfeed": True,
                        "ai_summary": "AI summary.",
                        "scraped": {"body_text": "Short body."},
                        "audit": {"scrape": {"status": "succeeded"}},
                    }
                ],
            },
            limit=5,
        )

        gate = evaluate_quality_gates(
            review,
            max_llm_review_items=0,
            max_short_scraped_text=0,
        )

        self.assertEqual(gate["status"], "fail")
        self.assertEqual(
            {violation["metric"] for violation in gate["violations"]},
            {"llm_review_items", "short_scraped_text_items"},
        )

    def test_known_rss_only_fallback_policy_downgrades_source_blocked_issue(self) -> None:
        review = build_digest_quality_review(
            {
                "generated_at": "2026-04-03T00:00:00Z",
                "items": [
                    {
                        "id": "skynews-1",
                        "title": "SkyNews Story",
                        "link": "https://news.sky.com/story/example",
                        "summary": "RSS summary.",
                        "published": "2026-04-03T00:00:00Z",
                        "source": {"id": "skynews", "name": "SkyNews"},
                        "feed": {"name": "Home", "url": "https://feeds.skynews.com/home.xml"},
                        "content_type": "news_article",
                        "include_in_newsfeed": True,
                        "ai_summary": "AI summary.",
                        "quality_status": "warn",
                        "quality_flags": [
                            {
                                "code": "source_blocked_403",
                                "severity": "warn",
                                "message": "Article fetch was blocked.",
                            }
                        ],
                        "audit": {
                            "scrape": {
                                "status": "failed",
                                "error": "HTTP Error 403: Forbidden",
                            }
                        },
                    }
                ],
            }
        )

        self.assertEqual(review["status"], "pass")
        self.assertEqual(review["issue_item_count"], 0)
        self.assertEqual(review["status_counts"], {"clean": 1})
        self.assertEqual(review["severity_counts"], {"info": 1})
        self.assertEqual(
            review["issue_counts"],
            [{"issue": "rss_only_fallback_accepted", "count": 1}],
        )
        self.assertEqual(
            review["quality_gate_metrics"],
            {
                "unknown_content_type_items": 0,
                "unsupported_content_type_items": 0,
                "accepted_content_type_filter_items": 0,
                "source_blocked_items": 0,
                "accepted_rss_only_fallback_items": 1,
                "llm_ready_items": 0,
                "llm_review_items": 0,
                "llm_excluded_items": 0,
                "llm_rss_fallback_items": 1,
                "empty_scraped_text_items": 0,
                "short_scraped_text_items": 0,
            },
        )

        gate = evaluate_quality_gates(review, max_accepted_rss_only_fallback=0)

        self.assertEqual(gate["status"], "fail")
        self.assertEqual(gate["violations"][0]["metric"], "accepted_rss_only_fallback_items")

    def test_validate_quality_cli_outputs_json_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            digest_path = Path(temp_dir) / "digest.json"
            digest_path.write_text(json.dumps(_digest_payload()), encoding="utf-8")
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = run_cli(
                    [
                        "validate",
                        "quality",
                        "--digest",
                        str(digest_path),
                        "--json",
                        "--limit",
                        "2",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "fail")
        self.assertEqual(len(payload["examples"]), 2)

    def test_validate_quality_cli_writes_review_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            digest_path = root / "digest.json"
            output_path = root / "quality" / "review.json"
            digest_path.write_text(json.dumps(_digest_payload()), encoding="utf-8")
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = run_cli(
                    [
                        "validate",
                        "quality",
                        "--digest",
                        str(digest_path),
                        "--output",
                        str(output_path),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["quality_gate_metrics"]["source_blocked_items"], 1)
            self.assertEqual(
                payload["quality_gate_metrics"]["accepted_content_type_filter_items"], 1
            )
            self.assertEqual(payload["quality_gate_metrics"]["accepted_rss_only_fallback_items"], 0)
            self.assertIn("Quality review output:", stdout.getvalue())
            self.assertIn("Top cleanliness drivers:", stdout.getvalue())

    def test_validate_quality_cli_can_fail_on_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            digest_path = Path(temp_dir) / "digest.json"
            digest_path.write_text(json.dumps(_digest_payload()), encoding="utf-8")

            with redirect_stdout(StringIO()):
                exit_code = run_cli(
                    [
                        "validate",
                        "quality",
                        "--digest",
                        str(digest_path),
                        "--fail-on-issue",
                    ]
                )

        self.assertEqual(exit_code, 1)

    def test_validate_quality_cli_can_fail_on_quality_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            digest_path = Path(temp_dir) / "digest.json"
            digest_path.write_text(json.dumps(_digest_payload()), encoding="utf-8")
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = run_cli(
                    [
                        "validate",
                        "quality",
                        "--digest",
                        str(digest_path),
                        "--json",
                        "--max-unsupported-content-types",
                        "0",
                        "--max-accepted-content-type-filters",
                        "0",
                        "--max-source-blocked",
                        "0",
                    ]
                )

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["quality_gate"]["status"], "fail")
        self.assertEqual(len(payload["quality_gate"]["violations"]), 2)


if __name__ == "__main__":
    unittest.main()
