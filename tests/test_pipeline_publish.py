from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rss_pipeline.config import PublishBuildConfig
from rss_pipeline.pipeline_publish import build_precomputed_payload


class PipelinePublishTests(unittest.TestCase):
    def test_build_precomputed_payload_includes_article_lens_scores(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            self._write_fixture_files(repo_root)

            config = PublishBuildConfig(
                digest=Path("data/rss_openai_daily.json"),
                scores=Path("data/scores.json"),
                high_scores=Path("data/high_scoring_articles.json"),
                analysis_root=Path("data/analysis"),
                output=Path("data/processed/rss_openai_precomputed.json"),
                include_history=False,
            )

            result = build_precomputed_payload(config, repo_root=repo_root)
            self.assertEqual(result["articles"], 2)

            output_path = repo_root / "data/processed/rss_openai_precomputed.json"
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "1.1")
            self.assertEqual(payload["summary"]["lens_scored_articles"], 1)
            self.assertEqual(
                payload["analysis"]["lens_correlations"]["lenses"],
                ["Evidence", "Impact"],
            )
            self.assertEqual(
                payload["analysis"]["lens_correlations"]["correlation"]["raw"],
                [[1.0, 0.4], [0.4, 1.0]],
            )
            self.assertEqual(
                payload["analysis"]["lens_correlations"]["correlation"]["normalized"],
                [[1.0, 0.6], [0.6, 1.0]],
            )
            self.assertEqual(
                payload["analysis"]["lens_correlations"]["covariance"]["raw"],
                [[2.0, 0.3], [0.3, 1.5]],
            )
            self.assertEqual(
                payload["analysis"]["lens_correlations"]["covariance"]["normalized"],
                [[0.2, 0.03], [0.03, 0.15]],
            )
            self.assertEqual(
                payload["analysis"]["lens_correlations"]["pairwise_counts"],
                [[10, 8], [8, 12]],
            )

            article_map = {article["id"]: article for article in payload["articles"]}
            scored_article = article_map["fixture-1"]
            unscored_article = article_map["fixture-2"]

            self.assertEqual(scored_article["score"]["value"], 12.5)
            self.assertEqual(scored_article["score"]["percent"], 62.5)
            self.assertEqual(scored_article["score"]["rubric_count"], 2)
            self.assertEqual(
                scored_article["score"]["lens_scores"]["Evidence"],
                {
                    "value": 7.5,
                    "max_value": 10.0,
                    "percent": 75.0,
                    "rubric_count": 1,
                },
            )
            self.assertEqual(
                scored_article["score"]["lens_scores"]["Impact"],
                {
                    "value": 5.0,
                    "max_value": 10.0,
                    "percent": 50.0,
                    "rubric_count": 1,
                },
            )
            self.assertEqual(
                scored_article["high_score"],
                {
                    "overall_score": 12.5,
                    "overall_percent": 62.5,
                    "lens_scores": {
                        "Evidence": 7.5,
                        "Impact": 5.0,
                    },
                },
            )
            self.assertEqual(unscored_article["score"]["lens_scores"], {})
            self.assertIsNone(unscored_article["high_score"])

    def _write_fixture_files(self, repo_root: Path) -> None:
        (repo_root / "data/analysis/lens_stats").mkdir(parents=True, exist_ok=True)
        (repo_root / "data/analysis/report").mkdir(parents=True, exist_ok=True)
        (repo_root / "data/processed").mkdir(parents=True, exist_ok=True)

        digest_payload = {
            "schema_version": "2.0",
            "generated_at": "2026-04-05T00:00:00Z",
            "run": {"id": "fixture-run", "generated_at": "2026-04-05T00:00:00Z"},
            "items": [
                {
                    "id": "fixture-1",
                    "title": "Fixture Headline One",
                    "link": "https://example.com/fixture-1",
                    "published": "2026-04-04T12:00:00Z",
                    "summary": "Summary one.",
                    "ai_summary": "AI summary one.",
                    "ai_tags": ["policy"],
                    "topic_tags": ["policy"],
                    "source": {"id": "source-1", "name": "Source One"},
                    "feed": {"name": "Feed One", "url": "https://example.com/feed-1.xml"},
                    "scraped": {"body_text": "Body one."},
                    "scrape_error": None,
                    "audit": {"request_id": "req-1"},
                },
                {
                    "id": "fixture-2",
                    "title": "Fixture Headline Two",
                    "link": "https://example.com/fixture-2",
                    "published": "2026-04-04T13:00:00Z",
                    "summary": "Summary two.",
                    "ai_summary": "AI summary two.",
                    "ai_tags": ["markets"],
                    "topic_tags": ["finance"],
                    "source": {"id": "source-2", "name": "Source Two"},
                    "feed": {"name": "Feed Two", "url": "https://example.com/feed-2.xml"},
                    "scraped": {"body_text": "Body two."},
                    "scrape_error": None,
                    "audit": {"request_id": "req-2"},
                },
            ],
        }
        (repo_root / "data/rss_openai_daily.json").write_text(
            json.dumps(digest_payload, indent=2),
            encoding="utf-8",
        )

        scores_payload = [
            {
                "news_item": {"id": "fixture-1"},
                "value": 7.5,
                "max_value": 10.0,
            },
            {
                "news_item": {"id": "fixture-1"},
                "value": 5.0,
                "max_value": 10.0,
            },
        ]
        (repo_root / "data/scores.json").write_text(
            json.dumps(scores_payload, indent=2),
            encoding="utf-8",
        )

        high_scores_payload = [
            {
                "news_item": {"id": "fixture-1"},
                "overall_score": 12.5,
                "overall_percent": 62.5,
                "lens_scores": {"Evidence": 7.5, "Impact": 5.0},
            }
        ]
        (repo_root / "data/high_scoring_articles.json").write_text(
            json.dumps(high_scores_payload, indent=2),
            encoding="utf-8",
        )

        lens_summary_payload = {
            "items_total": 1,
            "aggregation": "latest",
            "lenses": [
                {
                    "name": "Evidence",
                    "rubric_count": 1,
                    "max_total": 10.0,
                    "items_with_scores": 1,
                },
                {
                    "name": "Impact",
                    "rubric_count": 1,
                    "max_total": 10.0,
                    "items_with_scores": 1,
                },
            ],
        }
        (repo_root / "data/analysis/lens_stats/lens_summary.json").write_text(
            json.dumps(lens_summary_payload, indent=2),
            encoding="utf-8",
        )
        (repo_root / "data/analysis/report/source_differentiation_summary.json").write_text(
            json.dumps({"source_count": 2}, indent=2),
            encoding="utf-8",
        )
        (repo_root / "data/analysis/lens_stats/lens_scores_raw.csv").write_text(
            "item_id,title,Evidence,Impact\n"
            "fixture-1,Fixture Headline One,7.500000,5.000000\n",
            encoding="utf-8",
        )
        (repo_root / "data/analysis/lens_stats/lens_scores_normalized.csv").write_text(
            "item_id,title,Evidence,Impact\n"
            "fixture-1,Fixture Headline One,0.750000,0.500000\n",
            encoding="utf-8",
        )
        (repo_root / "data/analysis/lens_stats/lens_correlation_raw.csv").write_text(
            ",Evidence,Impact\n"
            "Evidence,1.0,0.4\n"
            "Impact,0.4,1.0\n",
            encoding="utf-8",
        )
        (repo_root / "data/analysis/lens_stats/lens_correlation_normalized.csv").write_text(
            ",Evidence,Impact\n"
            "Evidence,1.0,0.6\n"
            "Impact,0.6,1.0\n",
            encoding="utf-8",
        )
        (repo_root / "data/analysis/lens_stats/lens_covariance_raw.csv").write_text(
            ",Evidence,Impact\n"
            "Evidence,2.0,0.3\n"
            "Impact,0.3,1.5\n",
            encoding="utf-8",
        )
        (repo_root / "data/analysis/lens_stats/lens_covariance_normalized.csv").write_text(
            ",Evidence,Impact\n"
            "Evidence,0.2,0.03\n"
            "Impact,0.03,0.15\n",
            encoding="utf-8",
        )
        (repo_root / "data/analysis/lens_stats/lens_pairwise_counts.csv").write_text(
            ",Evidence,Impact\n"
            "Evidence,10,8\n"
            "Impact,8,12\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
