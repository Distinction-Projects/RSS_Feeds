from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lens import Lens, Rubric, Score, load_lenses, load_scores, save_scores
from load_experiment import NewsItem, load_experiments

from .artifact_store import export_prompt_audit_rows, write_json
from .cache_sqlite import SQLiteOpenAICache
from .config import ScoreRunConfig
from .env import require_env_value
from .errors import ConfigError
from .openai_client import OpenAIService
from .prompt_builder import build_score_system_prompt, build_score_user_prompt
from .workflow_runtime import RunContext


@dataclass(slots=True)
class ScoreRunResult:
    run_id: str
    output: str
    high_scores_output: str
    new_scores: int
    total_scores: int
    high_scores_count: int
    openai_calls: int
    cache_hits: int
    cache_misses: int
    prompt_audit_export: str | None


def _select_lenses(lenses: list[Lens], lens_name: str | None) -> list[Lens]:
    if not lenses:
        raise ConfigError("No lenses found.")
    if not lens_name:
        return lenses
    for lens in lenses:
        if lens.name == lens_name:
            return [lens]
    available = ", ".join(lens_item.name for lens_item in lenses)
    raise ConfigError(f"Lens not found: {lens_name}. Available: {available}")


def _select_news_items(
    items: list[NewsItem],
    item_id: str | None,
    item_index: int | None,
) -> list[NewsItem]:
    if item_id:
        return [item for item in items if item.id == item_id]
    if item_index is not None:
        if 0 <= item_index < len(items):
            return [items[item_index]]
        return []
    return items


def _score_from_payload(
    payload: dict[str, Any],
    *,
    rubric: Rubric,
    news_item: NewsItem,
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
    )


def run_scoring(
    config: ScoreRunConfig,
    *,
    repo_root: Path,
    lens_name: str | None = None,
    news_item_id: str | None = None,
    news_item_index: int | None = None,
) -> ScoreRunResult:
    context = RunContext.start("score")

    experiment_input = (
        config.experiment if config.experiment.is_absolute() else repo_root / config.experiment
    )
    lenses_input = (
        config.lenses_path if config.lenses_path.is_absolute() else repo_root / config.lenses_path
    )
    output_path = config.output if config.output.is_absolute() else repo_root / config.output
    high_scores_path = (
        config.high_scores_output
        if config.high_scores_output.is_absolute()
        else repo_root / config.high_scores_output
    )
    cache_path = (
        config.cache_path if config.cache_path.is_absolute() else repo_root / config.cache_path
    )
    prompt_audit_dir = (
        config.prompt_audit_dir
        if config.prompt_audit_dir.is_absolute()
        else repo_root / config.prompt_audit_dir
    )

    api_key = require_env_value("OPENAI_API_KEY", base_dir=repo_root)

    lenses = load_lenses(lenses_input)
    lenses_to_use = _select_lenses(lenses, lens_name)
    max_possible_score = sum(
        rubric.max_score_per_question * rubric.expected_question_count
        for lens in lenses_to_use
        for rubric in lens.rubrics
    )

    cache = SQLiteOpenAICache(cache_path) if config.use_cache else None
    service = OpenAIService(api_key=api_key, timeout_seconds=config.timeout_seconds, cache=cache)
    # Keep rubric scoring deterministic and reproducible across runs.
    scoring_temperature = 0.0

    experiment_entries = load_experiments([str(experiment_input)])
    if not experiment_entries:
        raise ConfigError(f"No matching experiment JSON files found for {experiment_input}")

    existing_scores = (
        load_scores(output_path) if output_path.exists() and not config.replace_output else []
    )
    all_scores = list(existing_scores)
    new_scores_count = 0
    high_scores: list[dict[str, Any]] = []

    for experiment_path, experiment in experiment_entries:
        selected_items = _select_news_items(experiment.items, news_item_id, news_item_index)
        if not selected_items:
            continue

        for news_item in selected_items:
            item_scores: list[Score] = []
            lens_totals: list[float] = []

            for lens in lenses_to_use:
                rubric_scores: list[Score] = []
                for rubric in lens.rubrics:
                    system_prompt = build_score_system_prompt(lens, rubric)
                    user_prompt = build_score_user_prompt(lens, rubric, news_item)
                    result = service.chat_json(
                        run_id=context.run_id,
                        purpose="score_rubric",
                        model=config.model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=scoring_temperature,
                        metadata={
                            "article_id": news_item.id,
                            "lens_name": lens.name,
                            "rubric_name": rubric.name,
                            "experiment_path": str(experiment_path),
                        },
                    )
                    score = _score_from_payload(
                        result.parsed,
                        rubric=rubric,
                        news_item=news_item,
                    )
                    rubric_scores.append(score)

                item_scores.extend(rubric_scores)
                lens_totals.append(sum(score.value for score in rubric_scores))

            final_score = sum(lens_totals)
            final_percent = (
                (final_score / max_possible_score) * 100.0 if max_possible_score > 0 else 0.0
            )

            if final_percent >= config.high_score_threshold_percent:
                lens_scores = {
                    lens.name: total for lens, total in zip(lenses_to_use, lens_totals, strict=True)
                }
                high_scores.append(
                    {
                        "news_item": news_item.to_dict(),
                        "lens_scores": lens_scores,
                        "overall_score": final_score,
                        "overall_percent": final_percent,
                    }
                )

            all_scores.extend(item_scores)
            new_scores_count += len(item_scores)

    save_scores(all_scores, output_path)
    write_json(high_scores_path, high_scores, ensure_ascii=False)

    openai_calls = 0
    cache_hits = 0
    cache_misses = 0
    prompt_audit_export: str | None = None
    if cache is not None:
        stats = cache.run_cache_stats(context.run_id)
        openai_calls = stats["calls"]
        cache_hits = stats["hits"]
        cache_misses = stats["misses"]
        prompt_rows = cache.prompt_audit_rows(context.run_id)
        prompt_export = export_prompt_audit_rows(prompt_rows, prompt_audit_dir, context.run_id)
        prompt_audit_export = str(prompt_export)

    return ScoreRunResult(
        run_id=context.run_id,
        output=str(output_path),
        high_scores_output=str(high_scores_path),
        new_scores=new_scores_count,
        total_scores=len(all_scores),
        high_scores_count=len(high_scores),
        openai_calls=openai_calls,
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        prompt_audit_export=prompt_audit_export,
    )
