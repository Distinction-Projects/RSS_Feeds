from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from rss_pipeline.config import PublishBuildConfig
from rss_pipeline.pipeline_publish import build_precomputed_payload, retain_published_annotations


class PublicationContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.article = {
            "id": "one",
            "title": "An article",
            "link": "https://example.com/one",
            "summary": "Summary text",
            "ai_summary": "Annotation input",
            "scraped": {"body_text": "Article body"},
            "score": {"rubric_count": 2, "lens_scores": {"Authority": {"percent": 75}}},
        }
        self.previous = {"generated_at": "2026-07-20T00:00:00Z", "articles": [self.article]}

    def test_missing_cache_retains_aggregate_and_original_publication_date(self) -> None:
        current = deepcopy(self.article)
        current["score"] = {"rubric_count": 0, "lens_scores": {}}
        self.assertEqual(retain_published_annotations([current], self.previous), 1)
        self.assertEqual(current["score"]["lens_scores"], self.article["score"]["lens_scores"])
        self.assertEqual(current["score"]["annotation_published_at"], self.previous["generated_at"])
        self.assertNotIn("annotation_published_at", self.article["score"])
        later = {"generated_at": "2026-11-05T00:00:00Z", "articles": [current]}
        following = deepcopy(current)
        following["score"] = {}
        retain_published_annotations([following], later)
        self.assertEqual(
            following["score"]["annotation_published_at"], self.previous["generated_at"]
        )

    def test_new_annotations_take_precedence(self) -> None:
        current = deepcopy(self.article)
        current["score"]["lens_scores"]["Authority"]["percent"] = 50
        self.assertEqual(retain_published_annotations([current], self.previous), 0)
        self.assertEqual(current["score"]["lens_scores"]["Authority"]["percent"], 50)

    def test_changed_text_and_unmatched_articles_are_not_reused(self) -> None:
        for key in ("id", "title", "link", "summary", "ai_summary", "scraped"):
            with self.subTest(key=key):
                current = deepcopy(self.article)
                current["score"] = {}
                current[key] = {"body_text": "Changed"} if key == "scraped" else "changed"
                self.assertEqual(retain_published_annotations([current], self.previous), 0)

    def test_empty_or_invalid_previous_publication_is_safe(self) -> None:
        for previous in ({}, {"articles": None}, {"articles": [None, {"id": []}]}):
            self.assertEqual(retain_published_annotations([deepcopy(self.article)], previous), 0)

    def test_repeated_publication_retains_scores_without_reviving_removed_articles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            digest = root / "digest.json"
            output = root / "published.json"
            digest.write_text(json.dumps({"items": [self.article]}), encoding="utf-8")
            output.write_text(json.dumps(self.previous), encoding="utf-8")
            config = PublishBuildConfig(digest=digest, output=output)
            build_precomputed_payload(config, repo_root=root)
            first = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(first["summary"]["lens_scored_articles"], 1)
            self.assertEqual(first["summary"]["retained_scored_articles"], 1)
            build_precomputed_payload(config, repo_root=root)
            second = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(first["articles"], second["articles"])
            digest.write_text(json.dumps({"items": []}), encoding="utf-8")
            build_precomputed_payload(config, repo_root=root)
            self.assertEqual(json.loads(output.read_text())["articles"], [])
