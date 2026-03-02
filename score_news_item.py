from __future__ import annotations

import argparse
import hashlib
import json
import os
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from lens import Lens, Rubric, Score, load_lenses, load_scores, save_scores
from load_experiment import NewsItem, load_experiments

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_TEMPERATURE = 0.0
DEFAULT_CACHE_DIR = ".cache/openai"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _load_env_file(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve_api_key() -> str:
    _load_env_file(".env")
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is missing. Add it to .env or environment.")
    return api_key


def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise ValueError("Model returned an empty response.")

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            return candidate

    raise ValueError("Could not parse a JSON object from model response.")


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_cache_key(
    model: str,
    temperature: float,
    system_prompt: str,
    user_prompt: str,
) -> str:
    payload = {
        "model": model,
        "temperature": temperature,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.json"


def _load_cache(cache_path: Path) -> dict[str, Any] | None:
    if not cache_path.exists():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _save_cache(cache_path: Path, payload: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = cache_path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temp_path.replace(cache_path)


def _parse_cached_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _select_lens(lenses: list[Lens], lens_name: str | None) -> Lens:
    if lens_name:
        for lens in lenses:
            if lens.name == lens_name:
                return lens
        available = ", ".join(lens.name for lens in lenses)
        raise ValueError(f"Lens not found: {lens_name}. Available: {available}")
    return lenses[0]


def _select_news_items(
    items: list[NewsItem],
    item_id: str | None,
    item_index: int | None,
) -> list[NewsItem]:
    if item_id:
        for item in items:
            if item.id == item_id:
                return [item]
        return []
    if item_index is not None:
        if 0 <= item_index < len(items):
            return [items[item_index]]
        return []
    return items


def _build_system_prompt(lens: Lens, rubric: Rubric) -> str:
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


def _build_article_context(news_item: NewsItem) -> str:
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


def _build_user_prompt(lens: Lens, rubric: Rubric, news_item: NewsItem) -> str:
    questions = "\n".join(
        f"{idx + 1}. {question.question}"
        for idx, question in enumerate(rubric.questions)
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

        {_build_article_context(news_item)}

        Return only JSON.
        """
    ).strip()


def _call_openai_chat_completions(
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout_seconds: int,
    temperature: float,
) -> dict[str, Any]:
    body = {
        "model": model,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    request = Request(
        OPENAI_CHAT_COMPLETIONS_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI HTTP error {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"OpenAI connection error: {exc}") from exc

    parsed = json.loads(payload)
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("OpenAI response did not include choices.")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise RuntimeError("OpenAI response did not include message content.")
    content = message.get("content")
    if not isinstance(content, str):
        raise RuntimeError("OpenAI message content is not text.")

    return _extract_json_object(content)


def _score_from_payload(
    payload: dict[str, Any],
    rubric: Rubric,
    news_item: NewsItem,
    scored_at: datetime | None = None,
) -> Score:
    raw_scores = payload.get("question_scores")
    if not isinstance(raw_scores, list):
        raise ValueError("Model response missing list field: question_scores")
    question_scores = [float(score) for score in raw_scores]

    reasoning_raw = payload.get("reasoning")
    reasoning = str(reasoning_raw).strip() if reasoning_raw is not None else ""
    if not reasoning:
        reasoning = "No reasoning provided."

    return Score.from_question_scores(
        rubric=rubric,
        news_item=news_item,
        question_scores=question_scores,
        reasoning=reasoning,
        scored_at=scored_at,
    )


def _score_one_rubric(
    api_key: str,
    model: str,
    lens: Lens,
    rubric: Rubric,
    news_item: NewsItem,
    timeout_seconds: int,
    temperature: float,
    cache_dir: Path | None,
    use_cache: bool,
) -> tuple[Score, bool]:
    system_prompt = _build_system_prompt(lens, rubric)
    user_prompt = _build_user_prompt(lens, rubric, news_item)
    cache_key = None
    cache_hit = False
    if use_cache and cache_dir is not None:
        cache_key = _build_cache_key(
            model=model,
            temperature=temperature,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        cache_path = _cache_path(cache_dir, cache_key)
        cached = _load_cache(cache_path)
        if cached:
            cached_response = cached.get("response")
            if isinstance(cached_response, dict):
                try:
                    scored_at = _parse_cached_timestamp(cached.get("created_at"))
                    score = _score_from_payload(
                        payload=cached_response,
                        rubric=rubric,
                        news_item=news_item,
                        scored_at=scored_at,
                    )
                    return score, True
                except ValueError:
                    try:
                        cache_path.unlink()
                    except OSError:
                        pass

    payload = _call_openai_chat_completions(
        api_key=api_key,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        timeout_seconds=timeout_seconds,
        temperature=temperature,
    )

    score = _score_from_payload(
        payload=payload,
        rubric=rubric,
        news_item=news_item,
    )

    if use_cache and cache_dir is not None:
        cache_entry = {
            "cache_key": cache_key
            or _build_cache_key(
                model=model,
                temperature=temperature,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            ),
            "created_at": _now_utc().isoformat(),
            "model": model,
            "temperature": temperature,
            "lens": {
                "name": lens.name,
            },
            "rubric": {
                "name": rubric.name,
                "question_count": rubric.expected_question_count,
            },
            "news_item": {
                "id": news_item.id,
                "link": news_item.link,
                "source_id": news_item.source_id,
            },
            "system_prompt_hash": _hash_text(system_prompt),
            "user_prompt_hash": _hash_text(user_prompt),
            "response": payload,
        }
        cache_path = _cache_path(cache_dir, cache_entry["cache_key"])
        _save_cache(cache_path, cache_entry)

    return score, cache_hit


def score_news_item_with_lens(
    api_key: str,
    model: str,
    lens: Lens,
    news_item: NewsItem,
    timeout_seconds: int,
    temperature: float,
    cache_dir: Path | None,
    use_cache: bool,
) -> list[Score]:
    scores: list[Score] = []
    for rubric in lens.rubrics:
        score, cache_hit = _score_one_rubric(
            api_key=api_key,
            model=model,
            lens=lens,
            rubric=rubric,
            news_item=news_item,
            timeout_seconds=timeout_seconds,
            temperature=temperature,
            cache_dir=cache_dir,
            use_cache=use_cache,
        )
        if cache_hit:
            print(f"    Cache hit for '{lens.name}' / '{rubric.name}'.")
        scores.append(score)
    return scores


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create Score records from one lens + one news item. "
            "Makes one OpenAI API call per rubric."
        )
    )
    parser.add_argument(
        "--lenses",
        default="lenses",
        help="Path to lenses file, directory, or glob pattern.",
    )
    parser.add_argument(
        "--experiment",
        default="data/rss_openai_daily.json",
        help="Path to experiment file, directory, or glob pattern.",
    )
    parser.add_argument(
        "--lens-name",
        default=None,
        help="Exact lens name. If omitted, all lenses are used.",
    )
    parser.add_argument("--news-item-id", default=None, help="Specific news item id.")
    parser.add_argument(
        "--news-item-index",
        type=int,
        default=None,
        help="Optional index to score a single news item (0-based).",
    )
    parser.add_argument("--output", default="data/scores.json", help="Output scores file path.")
    parser.add_argument(
        "--model",
        default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
        help=f"OpenAI model (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--cache-dir",
        default=os.getenv("OPENAI_CACHE_DIR", DEFAULT_CACHE_DIR),
        help="Directory for cached OpenAI responses.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable local caching of OpenAI responses.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Request timeout for each OpenAI call.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=DEFAULT_TEMPERATURE,
        help="Model sampling temperature.",
    )
    parser.add_argument(
        "--replace-output",
        action="store_true",
        help="Overwrite output file instead of appending.",
    )
    parser.add_argument(
        "--high-scores-output",
        default="data/high_scoring_articles.json",
        help="Output file for high-scoring articles.",
    )
    parser.add_argument(
        "--high-score-threshold-percent",
        type=float,
        default=60.0,
        help="Include articles at this percentage of max possible score (default: 60%%).",
    )
    return parser.parse_args()


def main() -> int:
    _load_env_file(".env")
    args = _parse_args()
    api_key = _resolve_api_key()

    lenses = load_lenses(args.lenses)
    lenses_to_use = lenses if not args.lens_name else [_select_lens(lenses, args.lens_name)]
    
    # Calculate max possible score for an item (across all selected lenses/rubrics).
    max_possible_score = sum(
        rubric.max_score_per_question * rubric.expected_question_count
        for lens in lenses_to_use
        for rubric in lens.rubrics
    )
    # Threshold is provided as a percent; we'll compare each item's percent score
    # rather than raw value so the interpretation is straightforward.
    print(f"Max possible score per item: {max_possible_score:.1f}")
    print(f"High score threshold: {args.high_score_threshold_percent}% of max")
    
    experiment_entries = load_experiments([args.experiment])
    if not experiment_entries:
        raise SystemExit("No matching experiment JSON files found.")

    output_path = Path(args.output)
    existing_scores = (
        load_scores(output_path)
        if output_path.exists() and not args.replace_output
        else []
    )
    all_scores = list(existing_scores)
    new_scores_count = 0
    high_scores = []
    use_cache = not args.no_cache
    cache_dir = None if args.no_cache else Path(args.cache_dir)

    for experiment_path, experiment in experiment_entries:
        selected_items = _select_news_items(
            experiment.items,
            args.news_item_id,
            args.news_item_index,
        )
        if not selected_items:
            print(f"Skipping {experiment_path}: no matching news items.")
            continue

        print(
            f"Scoring {len(selected_items)} news item(s) from {experiment_path} "
            f"with {len(lenses_to_use)} lens(es)..."
        )

        for news_item in selected_items:
            print(f"  Scoring news item '{news_item.id}'...")

            item_scores = []
            lens_totals = []
            for lens in lenses_to_use:
                new_scores = score_news_item_with_lens(
                    api_key=api_key,
                    model=args.model,
                    lens=lens,
                    news_item=news_item,
                    timeout_seconds=args.timeout_seconds,
                    temperature=args.temperature,
                    cache_dir=cache_dir,
                    use_cache=use_cache,
                )
                item_scores.extend(new_scores)
                lens_total = sum(score.value for score in new_scores)
                lens_totals.append(lens_total)

            final_score = sum(lens_totals)
            # compute percentage relative to the maximum possible score
            final_percent = (
                (final_score / max_possible_score) * 100.0 if max_possible_score > 0 else 0.0
            )
            print(f"    Final score: {final_score} ({final_percent:.1f}%)")

            all_scores.extend(item_scores)
            new_scores_count += len(item_scores)

            if final_percent >= args.high_score_threshold_percent:
                lens_scores = {
                    lens.name: total for lens, total in zip(lenses_to_use, lens_totals)
                }
                high_scores.append(
                    {
                        "news_item": news_item.to_dict(),
                        "lens_scores": lens_scores,
                        "overall_score": final_score,
                        "overall_percent": final_percent,
                    }
                )

    # Save all scores
    save_scores(all_scores, output_path)
    print(
        f"Wrote {new_scores_count} new scores to {output_path} "
        f"(total records: {len(all_scores)})."
    )

    # Save high scores
    high_scores_path = Path(args.high_scores_output)
    with open(high_scores_path, "w", encoding="utf-8") as f:
        json.dump(high_scores, f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(high_scores)} high-scoring articles to {high_scores_path}.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
