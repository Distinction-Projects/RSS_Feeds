from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from lens import Lens, Rubric, RubricQuestion
from load_experiment import NewsItem
from rss_pipeline.config import DigestBuildConfig, ScoreRunConfig
from rss_pipeline.errors import OpenAIResponseError
from rss_pipeline.models_digest import DigestItem, FeedRef, SourceRef
from rss_pipeline.pipeline_digest import build_digest
from rss_pipeline.pipeline_score import run_scoring


class _FakeDigestCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    def run_cache_stats(self, run_id: str) -> dict[str, int]:
        return {"calls": 7, "hits": 0, "misses": 7}

    def prompt_audit_rows(self, run_id: str) -> list[dict[str, object]]:
        return []


class _FakeDigestService:
    attempts_by_batch: dict[int, int] = {}

    def __init__(self, *, api_key: str, timeout_seconds: int, cache: object | None) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.cache = cache

    def chat_json(
        self,
        *,
        run_id: str,
        purpose: str,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        metadata: dict[str, object] | None = None,
    ) -> SimpleNamespace:
        batch_index = int((metadata or {}).get("batch_index", 0))
        attempt = self.attempts_by_batch.get(batch_index, 0) + 1
        self.attempts_by_batch[batch_index] = attempt

        if batch_index == 1 and attempt < 3:
            raise OpenAIResponseError("APITimeoutError: Request timed out")
        if batch_index == 2:
            raise OpenAIResponseError("APITimeoutError: Request timed out")

        user_content = messages[1]["content"]
        payload = json.loads(user_content[user_content.index("{") :])
        response_items = [
            {
                "id": str(item["id"]),
                "summary": f"summary-{item['id']}",
                "tags": ["news", "digest"],
            }
            for item in payload["items"]
        ]
        return SimpleNamespace(
            parsed={"items": response_items},
            response_id=f"resp-{batch_index}-{attempt}",
            usage={"total_tokens": 10},
            cache_key=f"cache-{batch_index}-{attempt}",
            user_prompt_hash=f"prompt-{batch_index}",
            response_hash=f"response-{batch_index}",
        )


class _FakeScoreCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    def run_cache_stats(self, run_id: str) -> dict[str, int]:
        return {"calls": 1, "hits": 0, "misses": 1}

    def prompt_audit_rows(self, run_id: str) -> list[dict[str, object]]:
        return []


class _FakeScoreService:
    call_count = 0

    def __init__(self, *, api_key: str, timeout_seconds: int, cache: object | None) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.cache = cache

    def chat_json(
        self,
        *,
        run_id: str,
        purpose: str,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        metadata: dict[str, object] | None = None,
    ) -> SimpleNamespace:
        type(self).call_count += 1
        return SimpleNamespace(
            parsed={
                "question_scores": [1.0],
                "question_evidence": ["Evidence for statement 1."],
                "reasoning": "ok",
            },
            response_id="resp-score",
            usage={"total_tokens": 5},
            cache_key="score-cache",
            user_prompt_hash="score-prompt",
            response_hash="score-response",
        )


class _FlakyScoreService:
    call_count = 0

    def __init__(self, *, api_key: str, timeout_seconds: int, cache: object | None) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.cache = cache

    def chat_json(
        self,
        *,
        run_id: str,
        purpose: str,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        metadata: dict[str, object] | None = None,
    ) -> SimpleNamespace:
        type(self).call_count += 1
        if type(self).call_count >= 2:
            raise OpenAIResponseError("APITimeoutError: Request timed out")
        return SimpleNamespace(
            parsed={
                "question_scores": [1.0],
                "question_evidence": ["Evidence for statement 1."],
                "reasoning": "ok",
            },
            response_id="resp-score-ok",
            usage={"total_tokens": 5},
            cache_key="score-cache-ok",
            user_prompt_hash="score-prompt-ok",
            response_hash="score-response-ok",
        )


class DigestReliabilityTests(unittest.TestCase):
    def _catalog_payload(self) -> dict[str, object]:
        return {
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
                            "topic_tags": ["general"],
                        }
                    ],
                }
            ]
        }

    def _feed_items(self) -> list[DigestItem]:
        source = SourceRef(id="source-a", name="Source A")
        feed = FeedRef(name="Top", url="https://example.com/feed.xml")
        return [
            DigestItem(
                id=f"item-{index}",
                title=f"Title {index}",
                link=f"https://example.com/article-{index}",
                summary=f"Summary {index}",
                published="2026-04-03T00:00:00Z",
                source=source,
                feed=feed,
            )
            for index in range(1, 4)
        ]

    def test_build_digest_retries_and_continues_after_failed_batch(self) -> None:
        _FakeDigestService.attempts_by_batch = {}

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "feed_catalog").mkdir(parents=True, exist_ok=True)
            (root / "feed_catalog" / "rss_feeds.json").write_text(
                json.dumps(self._catalog_payload()),
                encoding="utf-8",
            )

            def fake_fetch_feed_items(**_: object) -> list[DigestItem]:
                return self._feed_items()

            def fake_enrich_items(
                items: list[DigestItem],
                *,
                limit: int | None,
                timeout_seconds: float,
                sleep_seconds: float,
                user_agent: str,
            ) -> dict[str, object]:
                for item in items:
                    item.scraped = {"title": item.title, "lead_paragraph": f"Lead {item.id}"}
                    item.scrape_error = None
                return {
                    "enabled": True,
                    "attempts": len(items),
                    "success": len(items),
                    "failed": 0,
                    "skipped": 0,
                    "limit": limit,
                    "timeout_seconds": timeout_seconds,
                    "sleep_seconds": sleep_seconds,
                }

            config = DigestBuildConfig(
                catalog=Path("feed_catalog/rss_feeds.json"),
                output=Path("data/rss_openai_daily.json"),
                archive_dir=Path("data/history"),
                archive_enabled=True,
                skip_seen_items=False,
                max_sources=1,
                feeds_per_source=1,
                max_items_per_feed=3,
                openai_timeout_seconds=180,
                openai_batch_size=1,
                openai_max_retries=2,
                openai_retry_backoff_seconds=0.0,
            )

            with (
                patch("rss_pipeline.pipeline_digest.fetch_feed_items", side_effect=fake_fetch_feed_items),
                patch("rss_pipeline.pipeline_digest.enrich_items_with_scrape", side_effect=fake_enrich_items),
                patch("rss_pipeline.pipeline_digest.resolve_env_value", side_effect=["test-key", "gpt-4.1-mini"]),
                patch("rss_pipeline.pipeline_digest.SQLiteOpenAICache", _FakeDigestCache),
                patch("rss_pipeline.pipeline_digest.OpenAIService", _FakeDigestService),
            ):
                result = build_digest(config, repo_root=root)

            payload = json.loads((root / "data" / "rss_openai_daily.json").read_text(encoding="utf-8"))

            self.assertEqual(result["items"], 3)
            self.assertEqual(result["errors"], 1)
            self.assertEqual(payload["audit"]["openai_batches"]["succeeded_batches"], 2)
            self.assertEqual(payload["audit"]["openai_batches"]["failed_batches"], 1)
            self.assertEqual(payload["audit"]["openai_batches"]["retry_attempts"], 4)
            self.assertEqual(payload["errors"][0]["stage"], "openai_digest_batch")
            self.assertEqual(payload["errors"][0]["type"], "timeout")
            self.assertEqual(payload["items"][0]["ai_summary"], "summary-item-1")
            self.assertEqual(payload["items"][1]["ai_summary"], "")
            self.assertEqual(payload["items"][2]["ai_summary"], "summary-item-3")
            self.assertIn(
                "1 OpenAI digest batch(es) failed permanently",
                payload["audit"]["warnings"],
            )
            self.assertTrue(Path(result["archive"]).exists())


class ScoreReliabilityTests(unittest.TestCase):
    def test_run_scoring_skips_items_missing_ai_summary(self) -> None:
        _FakeScoreService.call_count = 0

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "data").mkdir(parents=True, exist_ok=True)
            (root / "data" / "analysis").mkdir(parents=True, exist_ok=True)
            config = ScoreRunConfig(
                experiment=Path("data/rss_openai_daily.json"),
                lenses_path=Path("lenses"),
                output=Path("data/scores.json"),
                cache_path=Path("data/cache/openai_cache.sqlite"),
                prompt_audit_dir=Path("data/analysis/prompt_audit"),
                replace_output=True,
            )

            lens = Lens(
                name="Lens A",
                summary="Summary",
                instructions="Instructions",
                system_prompt="System prompt",
                user_prompt="User prompt",
                rubrics=[
                    Rubric(
                        name="Rubric A",
                        questions=[
                            RubricQuestion(
                                question="The article provides enough evidence for a claim.",
                                semantic_class="existence_good",
                            )
                        ],
                        expected_question_count=1,
                        min_score_per_question=0.0,
                        max_score_per_question=1.0,
                    )
                ],
            )
            scoreable_item = NewsItem.from_dict(
                {
                    "id": "item-1",
                    "title": "Item 1",
                    "link": "https://example.com/1",
                    "summary": "Summary 1",
                    "published": "2026-04-03T00:00:00Z",
                    "source_id": "src",
                    "source_name": "Source",
                    "feed_name": "Feed",
                    "feed_url": "https://example.com/feed.xml",
                    "fetched_at": "2026-04-03T00:00:00Z",
                    "ai_summary": "AI summary 1",
                    "ai_tags": ["news"],
                }
            )
            skipped_item = NewsItem.from_dict(
                {
                    "id": "item-2",
                    "title": "Item 2",
                    "link": "https://example.com/2",
                    "summary": "Summary 2",
                    "published": "2026-04-03T00:00:00Z",
                    "source_id": "src",
                    "source_name": "Source",
                    "feed_name": "Feed",
                    "feed_url": "https://example.com/feed.xml",
                    "fetched_at": "2026-04-03T00:00:00Z",
                    "ai_summary": "",
                    "ai_tags": [],
                }
            )

            with (
                patch("rss_pipeline.pipeline_score.require_env_value", return_value="test-key"),
                patch("rss_pipeline.pipeline_score.load_lenses", return_value=[lens]),
                patch(
                    "rss_pipeline.pipeline_score.load_experiments",
                    return_value=[(root / "data" / "rss_openai_daily.json", SimpleNamespace(items=[scoreable_item, skipped_item]))],
                ),
                patch("rss_pipeline.pipeline_score.SQLiteOpenAICache", _FakeScoreCache),
                patch("rss_pipeline.pipeline_score.OpenAIService", _FakeScoreService),
            ):
                result = run_scoring(config, repo_root=root)

            self.assertEqual(result.scored_items, 1)
            self.assertEqual(result.skipped_missing_ai_summary, 1)
            self.assertEqual(result.new_scores, 1)
            self.assertEqual(_FakeScoreService.call_count, 1)

    def test_run_scoring_writes_checkpoint_and_run_log_on_failure(self) -> None:
        _FlakyScoreService.call_count = 0

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "data").mkdir(parents=True, exist_ok=True)
            (root / "data" / "analysis").mkdir(parents=True, exist_ok=True)
            config = ScoreRunConfig(
                experiment=Path("data/rss_openai_daily.json"),
                lenses_path=Path("lenses"),
                output=Path("data/scores.json"),
                cache_path=Path("data/cache/openai_cache.sqlite"),
                prompt_audit_dir=Path("data/analysis/prompt_audit"),
                run_log_dir=Path("data/analysis/score_run_logs"),
                replace_output=True,
            )

            lens = Lens(
                name="Lens A",
                summary="Summary",
                instructions="Instructions",
                system_prompt="System prompt",
                user_prompt="User prompt",
                rubrics=[
                    Rubric(
                        name="Rubric A",
                        questions=[
                            RubricQuestion(
                                question="The article provides enough evidence for a claim.",
                                semantic_class="existence_good",
                            )
                        ],
                        expected_question_count=1,
                        min_score_per_question=0.0,
                        max_score_per_question=1.0,
                    )
                ],
            )

            item_1 = NewsItem.from_dict(
                {
                    "id": "item-1",
                    "title": "Item 1",
                    "link": "https://example.com/1",
                    "summary": "Summary 1",
                    "published": "2026-04-03T00:00:00Z",
                    "source_id": "src",
                    "source_name": "Source",
                    "feed_name": "Feed",
                    "feed_url": "https://example.com/feed.xml",
                    "fetched_at": "2026-04-03T00:00:00Z",
                    "ai_summary": "AI summary 1",
                    "ai_tags": ["news"],
                }
            )
            item_2 = NewsItem.from_dict(
                {
                    "id": "item-2",
                    "title": "Item 2",
                    "link": "https://example.com/2",
                    "summary": "Summary 2",
                    "published": "2026-04-03T00:00:00Z",
                    "source_id": "src",
                    "source_name": "Source",
                    "feed_name": "Feed",
                    "feed_url": "https://example.com/feed.xml",
                    "fetched_at": "2026-04-03T00:00:00Z",
                    "ai_summary": "AI summary 2",
                    "ai_tags": ["news"],
                }
            )

            with (
                patch("rss_pipeline.pipeline_score.require_env_value", return_value="test-key"),
                patch("rss_pipeline.pipeline_score.load_lenses", return_value=[lens]),
                patch(
                    "rss_pipeline.pipeline_score.load_experiments",
                    return_value=[
                        (
                            root / "data" / "rss_openai_daily.json",
                            SimpleNamespace(items=[item_1, item_2]),
                        )
                    ],
                ),
                patch("rss_pipeline.pipeline_score.SQLiteOpenAICache", _FakeScoreCache),
                patch("rss_pipeline.pipeline_score.OpenAIService", _FlakyScoreService),
            ):
                with self.assertRaises(OpenAIResponseError):
                    run_scoring(config, repo_root=root)

            scores = json.loads((root / "data" / "scores.json").read_text(encoding="utf-8"))
            self.assertEqual(len(scores), 1)
            log_files = sorted((root / "data" / "analysis" / "score_run_logs").glob("*.jsonl"))
            self.assertEqual(len(log_files), 1)
            log_text = log_files[0].read_text(encoding="utf-8")
            self.assertIn('"event": "rubric_error"', log_text)
            self.assertIn('"event": "run_failed"', log_text)


if __name__ == "__main__":
    unittest.main()
