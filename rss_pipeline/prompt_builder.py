from __future__ import annotations

import json
import textwrap
from typing import Any

from lens import Lens, Rubric
from load_experiment import NewsItem


def compact_text(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(limit - 3, 0)] + "..."


def digest_item_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "id": item.get("id", ""),
        "title": item.get("title", ""),
        "source": item.get("source", {}).get("name")
        if isinstance(item.get("source"), dict)
        else item.get("source_name", ""),
        "published": item.get("published", ""),
        "summary": item.get("summary", ""),
        "link": item.get("link", ""),
    }

    scraped = item.get("scraped")
    if isinstance(scraped, dict):
        scraped_title = compact_text(str(scraped.get("title") or "").strip(), 200)
        scraped_lead = compact_text(str(scraped.get("lead_paragraph") or "").strip(), 350)
        if scraped_title:
            payload["scraped_title"] = scraped_title
        if scraped_lead:
            payload["scraped_lead_paragraph"] = scraped_lead

    return payload


def build_digest_messages(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    system = (
        "You summarize news items. For each item, return a short summary (max 1 sentence) "
        "and 3-6 topical tags. Use neutral language."
    )
    payload = {"items": [digest_item_payload(item) for item in items]}
    user = (
        "Return JSON with an 'items' array. Each array item must include: "
        "id, summary, tags (array of short strings). Only return JSON.\n"
        + json.dumps(payload, ensure_ascii=True)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_score_system_prompt(lens: Lens, rubric: Rubric) -> str:
    return textwrap.dedent(
        f"""
        {lens.system_prompt}

        You are scoring one rubric for one news article.
        Return ONLY valid JSON with this exact shape:
        {{
          "question_scores": [number, ...],
          "reasoning": "short justification"
        }}

        Hard requirements:
        - Include exactly {rubric.expected_question_count} scores.
        - Each score must be within [{rubric.min_score_per_question}, {rubric.max_score_per_question}].
        - No markdown.
        - No extra keys.
        """
    ).strip()


def build_score_article_context(news_item: NewsItem) -> str:
    scraped_lead = ""
    if news_item.scraped and news_item.scraped.lead_paragraph:
        scraped_lead = news_item.scraped.lead_paragraph

    return textwrap.dedent(
        f"""
        News Item:
        - id: {news_item.id}
        - title: {news_item.title}
        - link: {news_item.link}
        - source: {news_item.source_name} ({news_item.source_id})
        - published: {news_item.published_raw}
        - summary: {news_item.summary or ""}
        - ai_summary: {news_item.ai_summary}
        - scraped_lead_paragraph: {scraped_lead}
        """
    ).strip()


def build_score_user_prompt(lens: Lens, rubric: Rubric, news_item: NewsItem) -> str:
    questions = "\n".join(
        f"{idx + 1}. {question.question}" for idx, question in enumerate(rubric.questions)
    )
    return textwrap.dedent(
        f"""
        {lens.user_prompt}

        Lens:
        - name: {lens.name}
        - summary: {lens.summary}
        - instructions: {lens.instructions}

        Rubric:
        - name: {rubric.name}
        - expected_question_count: {rubric.expected_question_count}
        - min_score_per_question: {rubric.min_score_per_question}
        - max_score_per_question: {rubric.max_score_per_question}
        - anticipated_total_score: {rubric.anticipated_total_score}
        - questions:
        {questions}

        {build_score_article_context(news_item)}

        Return only JSON.
        """
    ).strip()
