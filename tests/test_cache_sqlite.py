from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from rss_pipeline.cache_sqlite import SQLiteOpenAICache


class SQLiteCacheTests(unittest.TestCase):
    def test_set_get_and_hit_count(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "openai_cache.sqlite"
            cache = SQLiteOpenAICache(db_path)

            cache.set_cached(
                cache_key="key-1",
                model="gpt-4.1-mini",
                request_hash="req-1",
                response_payload={"parsed": {"ok": True}, "response_id": "resp-1", "usage": {}},
            )

            first = cache.get_cached("key-1")
            second = cache.get_cached("key-1")

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            if first is None:
                self.fail("Cache read unexpectedly returned None")
            self.assertEqual(first["parsed"]["ok"], True)

    def test_run_stats_and_prompt_audit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "openai_cache.sqlite"
            cache = SQLiteOpenAICache(db_path)

            cache.log_openai_call(
                run_id="run-1",
                purpose="score_rubric",
                model="gpt-4.1-mini",
                cache_key="k1",
                cache_hit=False,
                request_hash="req",
                prompt_hash="p",
                user_prompt_hash="u",
                response_hash="r",
                latency_ms=123,
                error=None,
            )
            cache.log_openai_call(
                run_id="run-1",
                purpose="score_rubric",
                model="gpt-4.1-mini",
                cache_key="k1",
                cache_hit=True,
                request_hash="req",
                prompt_hash="p",
                user_prompt_hash="u",
                response_hash="r",
                latency_ms=0,
                error=None,
            )

            cache.record_prompt_audit(
                run_id="run-1",
                purpose="score_rubric",
                model="gpt-4.1-mini",
                cache_key="k1",
                article_id="article-1",
                lens_name="lens-a",
                rubric_name="rubric-a",
                prompt_ref="sqlite://prompt/1",
                prompt_hash="ph",
                prompt_body="{}",
                response_ref="openai://chat/1",
                response_hash="rh",
                response_body="{}",
            )

            stats = cache.run_cache_stats("run-1")
            rows = cache.prompt_audit_rows("run-1")

            self.assertEqual(stats["calls"], 2)
            self.assertEqual(stats["hits"], 1)
            self.assertEqual(stats["misses"], 1)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["article_id"], "article-1")


if __name__ == "__main__":
    unittest.main()
