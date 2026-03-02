from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .artifact_store import write_json
from .config import PublishBuildConfig
from .workflow_runtime import utc_now_iso


def _load_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default
    return payload


def _analysis_payload(analysis_root: Path) -> dict[str, Any]:
    lens_summary_path = analysis_root / "lens_stats" / "lens_summary.json"
    source_diff_path = analysis_root / "report" / "source_differentiation_summary.json"
    return {
        "lens_summary": _load_json(lens_summary_path, default={}),
        "source_differentiation": _load_json(source_diff_path, default={}),
    }


def _score_stats_by_article(scores: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    per_article: dict[str, dict[str, float]] = defaultdict(
        lambda: {"value": 0.0, "max_value": 0.0, "rubric_count": 0.0}
    )
    for score in scores:
        if not isinstance(score, dict):
            continue
        news_item = score.get("news_item")
        if not isinstance(news_item, dict):
            continue
        item_id = str(news_item.get("id") or "").strip()
        if not item_id:
            continue
        value = float(score.get("value") or 0.0)
        max_value = float(score.get("max_value") or 0.0)
        row = per_article[item_id]
        row["value"] += value
        row["max_value"] += max_value
        row["rubric_count"] += 1.0
    return per_article


def _high_scores_by_article(high_scores: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for record in high_scores:
        if not isinstance(record, dict):
            continue
        news_item = record.get("news_item")
        if not isinstance(news_item, dict):
            continue
        item_id = str(news_item.get("id") or "").strip()
        if not item_id:
            continue
        mapping[item_id] = {
            "overall_score": float(record.get("overall_score") or 0.0),
            "overall_percent": float(record.get("overall_percent") or 0.0),
            "lens_scores": record.get("lens_scores")
            if isinstance(record.get("lens_scores"), dict)
            else {},
        }
    return mapping


def build_precomputed_payload(config: PublishBuildConfig, *, repo_root: Path) -> dict[str, Any]:
    digest_path = config.digest if config.digest.is_absolute() else repo_root / config.digest
    scores_path = config.scores if config.scores.is_absolute() else repo_root / config.scores
    high_scores_path = (
        config.high_scores if config.high_scores.is_absolute() else repo_root / config.high_scores
    )
    analysis_root = (
        config.analysis_root
        if config.analysis_root.is_absolute()
        else repo_root / config.analysis_root
    )
    output_path = config.output if config.output.is_absolute() else repo_root / config.output

    digest_payload = _load_json(digest_path, default={})
    items = digest_payload.get("items") if isinstance(digest_payload, dict) else []
    items = items if isinstance(items, list) else []

    raw_scores = _load_json(scores_path, default=[])
    scores = raw_scores if isinstance(raw_scores, list) else []

    raw_high_scores = _load_json(high_scores_path, default=[])
    high_scores = raw_high_scores if isinstance(raw_high_scores, list) else []

    score_stats = _score_stats_by_article(scores)
    high_score_stats = _high_scores_by_article(high_scores)

    precomputed_articles: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue

        score = score_stats.get(item_id, {"value": 0.0, "max_value": 0.0, "rubric_count": 0.0})
        max_value = float(score["max_value"])
        percent = (float(score["value"]) / max_value * 100.0) if max_value > 0 else 0.0
        high_score = high_score_stats.get(item_id)

        precomputed_articles.append(
            {
                "id": item_id,
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "published": item.get("published", ""),
                "summary": item.get("summary", ""),
                "ai_summary": item.get("ai_summary", ""),
                "ai_tags": item.get("ai_tags", []),
                "topic_tags": item.get("topic_tags", []),
                "source": item.get("source", {}),
                "feed": item.get("feed", {}),
                "scraped": item.get("scraped"),
                "scrape_error": item.get("scrape_error"),
                "score": {
                    "value": round(float(score["value"]), 4),
                    "max_value": round(max_value, 4),
                    "percent": round(percent, 4),
                    "rubric_count": int(score["rubric_count"]),
                },
                "high_score": high_score,
                "audit": item.get("audit", {}),
            }
        )

    precomputed_articles.sort(
        key=lambda row: (
            float((row.get("high_score") or {}).get("overall_percent") or 0.0),
            float((row.get("score") or {}).get("percent") or 0.0),
        ),
        reverse=True,
    )
    if config.max_articles is not None and config.max_articles >= 0:
        precomputed_articles = precomputed_articles[: config.max_articles]

    output_payload = {
        "schema_version": "1.0",
        "generated_at": utc_now_iso(),
        "contract": "rss_pipeline_precomputed",
        "digest": {
            "path": str(config.digest),
            "schema_version": digest_payload.get("schema_version", ""),
            "generated_at": digest_payload.get("generated_at")
            or (digest_payload.get("run") or {}).get("generated_at"),
            "run_id": (digest_payload.get("run") or {}).get("id"),
            "items_count": len(items),
        },
        "artifacts": {
            "scores_path": str(config.scores),
            "high_scores_path": str(config.high_scores),
            "analysis_root": str(config.analysis_root),
        },
        "summary": {
            "articles": len(precomputed_articles),
            "scored_articles": sum(
                1 for row in precomputed_articles if row["score"]["max_value"] > 0
            ),
            "high_scoring_articles": sum(
                1 for row in precomputed_articles if row.get("high_score")
            ),
        },
        "analysis": _analysis_payload(analysis_root),
        "articles": precomputed_articles,
    }

    write_json(output_path, output_payload, ensure_ascii=False)
    return {"output": str(output_path), "articles": len(precomputed_articles)}
