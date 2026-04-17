from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lens import Lens, Rubric, Score, load_lenses, load_scores, save_scores
from load_experiment import NewsItem, load_experiments

from .artifact_store import export_prompt_audit_rows
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
    scored_items: int
    skipped_missing_ai_summary: int
    new_scores: int
    total_records: int
    openai_calls: int
    cache_hits: int
    cache_misses: int
    prompt_audit_export: str | None
    run_log_path: str | None


class _RunLogger:
    def __init__(self, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path = output_path
        self._handle = output_path.open("a", encoding="utf-8")

    def event(self, name: str, **payload: Any) -> None:
        record = {"event": name, **payload}
        self._handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._handle.flush()

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()


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

    raw_evidence = payload.get("question_evidence")
    question_evidence: list[str] | None
    if raw_evidence is None:
        question_evidence = None
    elif isinstance(raw_evidence, list):
        question_evidence = [str(entry).strip() for entry in raw_evidence]
    else:
        raise ValueError("Model response field question_evidence must be a list when present.")

    return Score.from_question_scores(
        rubric=rubric,
        news_item=news_item,
        question_scores=question_scores,
        question_evidence=question_evidence,
        reasoning=reasoning,
    )


def _has_scoreable_ai_context(news_item: NewsItem) -> bool:
    return bool(news_item.ai_summary.strip())


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
    cache_path = (
        config.cache_path if config.cache_path.is_absolute() else repo_root / config.cache_path
    )
    prompt_audit_dir = (
        config.prompt_audit_dir
        if config.prompt_audit_dir.is_absolute()
        else repo_root / config.prompt_audit_dir
    )
    run_log_dir = (
        config.run_log_dir if config.run_log_dir.is_absolute() else repo_root / config.run_log_dir
    )

    api_key = require_env_value("OPENAI_API_KEY", base_dir=repo_root)

    lenses = load_lenses(lenses_input)
    lenses_to_use = _select_lenses(lenses, lens_name)
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
    scored_items = 0
    skipped_missing_ai_summary = 0
    new_scores_count = 0

    selected_pairs: list[tuple[Path, NewsItem]] = []
    for experiment_path, experiment in experiment_entries:
        selected_items = _select_news_items(experiment.items, news_item_id, news_item_index)
        skipped_missing_ai_summary += sum(
            1 for news_item in selected_items if not _has_scoreable_ai_context(news_item)
        )
        scoreable_items = [
            news_item for news_item in selected_items if _has_scoreable_ai_context(news_item)
        ]
        selected_pairs.extend((experiment_path, news_item) for news_item in scoreable_items)

    run_log_path = run_log_dir / f"{context.run_id}.jsonl"
    logger = _RunLogger(run_log_path)
    logger.event(
        "run_start",
        run_id=context.run_id,
        experiment=str(experiment_input),
        output=str(output_path),
        selected_items=len(selected_pairs),
        existing_records=len(existing_scores),
        lenses=len(lenses_to_use),
        model=config.model,
        timeout_seconds=config.timeout_seconds,
        use_cache=bool(cache),
    )

    try:
        for item_index, (experiment_path, news_item) in enumerate(selected_pairs, start=1):
            scored_items += 1
            item_scores: list[Score] = []
            logger.event(
                "item_start",
                run_id=context.run_id,
                item_index=item_index,
                total_items=len(selected_pairs),
                article_id=news_item.id,
                article_title=news_item.title,
                experiment_path=str(experiment_path),
            )

            for lens in lenses_to_use:
                rubric_scores: list[Score] = []
                for rubric in lens.rubrics:
                    logger.event(
                        "rubric_start",
                        run_id=context.run_id,
                        item_index=item_index,
                        article_id=news_item.id,
                        lens_name=lens.name,
                        rubric_name=rubric.name,
                    )
                    system_prompt = build_score_system_prompt(lens, rubric)
                    user_prompt = build_score_user_prompt(lens, rubric, news_item)
                    try:
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
                    except Exception as exc:  # noqa: BLE001
                        save_scores(all_scores, output_path)
                        logger.event(
                            "rubric_error",
                            run_id=context.run_id,
                            item_index=item_index,
                            article_id=news_item.id,
                            lens_name=lens.name,
                            rubric_name=rubric.name,
                            error_type=type(exc).__name__,
                            error=str(exc),
                            checkpoint_records=len(all_scores),
                        )
                        raise
                    rubric_scores.append(score)
                    logger.event(
                        "rubric_complete",
                        run_id=context.run_id,
                        item_index=item_index,
                        article_id=news_item.id,
                        lens_name=lens.name,
                        rubric_name=rubric.name,
                        question_count=len(score.question_scores),
                    )

                item_scores.extend(rubric_scores)

            all_scores.extend(item_scores)
            new_scores_count += len(item_scores)
            save_scores(all_scores, output_path)
            logger.event(
                "item_complete",
                run_id=context.run_id,
                item_index=item_index,
                article_id=news_item.id,
                records_written=len(all_scores),
                item_new_scores=len(item_scores),
            )
    except Exception:
        logger.event(
            "run_failed",
            run_id=context.run_id,
            duration_seconds=context.duration_seconds,
            scored_items=scored_items,
            skipped_missing_ai_summary=skipped_missing_ai_summary,
            new_scores=new_scores_count,
            total_records=len(all_scores),
        )
        logger.close()
        raise
    finally:
        logger.close()
    save_scores(all_scores, output_path)

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

    logger = _RunLogger(run_log_path)
    logger.event(
        "run_complete",
        run_id=context.run_id,
        duration_seconds=context.duration_seconds,
        scored_items=scored_items,
        skipped_missing_ai_summary=skipped_missing_ai_summary,
        new_scores=new_scores_count,
        total_records=len(all_scores),
        openai_calls=openai_calls,
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        prompt_audit_export=prompt_audit_export,
    )
    logger.close()

    return ScoreRunResult(
        run_id=context.run_id,
        output=str(output_path),
        scored_items=scored_items,
        skipped_missing_ai_summary=skipped_missing_ai_summary,
        new_scores=new_scores_count,
        total_records=len(all_scores),
        openai_calls=openai_calls,
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        prompt_audit_export=prompt_audit_export,
        run_log_path=str(run_log_path),
    )
