from __future__ import annotations

import unittest

from lens import Lens, Rubric, RubricQuestion
from load_experiment import NewsItem
from rss_pipeline.prompt_builder import build_score_system_prompt, build_score_user_prompt


class PromptBuilderTests(unittest.TestCase):
    def _sample_lens(self) -> Lens:
        return Lens(
            name="Prompt Fixture Lens",
            summary="Fixture summary.",
            instructions="Fixture instructions.",
            system_prompt="You are a fixture evaluator.",
            user_prompt="Score this fixture article.",
            rubrics=[
                Rubric(
                    name="Prompt Fixture Rubric",
                    questions=[
                        RubricQuestion(
                            question="The article cites named sources for key claims.",
                            semantic_class="existence_good",
                        ),
                        RubricQuestion(
                            question="The article uses emotionally loaded language to steer interpretation.",
                            semantic_class="existence_bad",
                        ),
                    ],
                    expected_question_count=2,
                    min_score_per_question=0.0,
                    max_score_per_question=5.0,
                )
            ],
        )

    def _sample_news_item(self) -> NewsItem:
        return NewsItem.from_dict(
            {
                "id": "prompt-fixture-1",
                "title": "Prompt Fixture Title",
                "link": "https://example.com/prompt-fixture-1",
                "summary": "Fixture summary body.",
                "published": "2026-04-12T00:00:00Z",
                "source_id": "fixture-source",
                "source_name": "Fixture Source",
                "feed_name": "Fixture Feed",
                "feed_url": "https://example.com/feed.xml",
                "topic_tags": ["fixture"],
                "fetched_at": "2026-04-12T00:05:00+00:00",
                "ai_summary": "Fixture AI summary.",
                "ai_tags": ["fixture"],
                "scraped": {
                    "title": "Prompt Fixture Title",
                    "lead_paragraph": "Lead paragraph for prompt fixture.",
                    "body_text": "A" * 5000,
                    "paragraphs": [],
                },
                "scrape_error": None,
            }
        )

    def test_system_prompt_has_scale_anchors_and_no_anticipated_total_score(self) -> None:
        lens = self._sample_lens()
        rubric = lens.rubrics[0]
        prompt = build_score_system_prompt(lens, rubric)
        self.assertIn("0 = Strongly Disagree", prompt)
        self.assertIn("5 = Strongly Agree", prompt)
        self.assertIn("question_evidence", prompt)
        self.assertNotIn("anticipated_total_score", prompt)

    def test_user_prompt_includes_semantic_class_and_bounded_body_context(self) -> None:
        lens = self._sample_lens()
        rubric = lens.rubrics[0]
        item = self._sample_news_item()
        prompt = build_score_user_prompt(lens, rubric, item)
        self.assertIn("[existence_good] The article cites named sources for key claims.", prompt)
        self.assertIn(
            "[existence_bad] The article uses emotionally loaded language to steer interpretation.",
            prompt,
        )
        self.assertIn("scraped_lead_paragraph: Lead paragraph for prompt fixture.", prompt)
        self.assertIn("scraped_body_excerpt:", prompt)
        self.assertLessEqual(len(prompt), 8000)
        self.assertIn("...", prompt)


if __name__ == "__main__":
    unittest.main()
