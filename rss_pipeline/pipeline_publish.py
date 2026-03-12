from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path
import re
from typing import Any

from .artifact_store import write_json
from .config import PublishBuildConfig
from .workflow_runtime import utc_now_iso

_HISTORY_FILENAME_PATTERN = re.compile(r"^rss_openai_daily_(\d{4}-\d{2}-\d{2})\.json$")


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


def _item_key(item: dict[str, Any]) -> str:
    item_id = str(item.get("id") or "").strip()
    if item_id:
        return f"id:{item_id}"

    link = str(item.get("link") or "").strip().lower()
    if link:
        return f"link:{link}"

    source_raw = item.get("source")
    source_id = ""
    if isinstance(source_raw, dict):
        source_id = str(source_raw.get("id") or "").strip().lower()
    if not source_id:
        source_id = str(item.get("source_id") or "").strip().lower()

    title = " ".join(str(item.get("title") or "").lower().split())
    if title:
        return f"title:{source_id}:{title}"

    return ""


def _history_files_in_window(history_dir: Path, history_days: int | None) -> list[Path]:
    if not history_dir.exists():
        return []

    today = date.today()
    candidates: list[tuple[date, Path]] = []
    for path in history_dir.glob("rss_openai_daily_*.json"):
        match = _HISTORY_FILENAME_PATTERN.match(path.name)
        if not match:
            continue
        try:
            snapshot_day = date.fromisoformat(match.group(1))
        except ValueError:
            continue

        if history_days is not None and history_days > 0:
            age_days = (today - snapshot_day).days
            if age_days < 0 or age_days > history_days:
                continue
        candidates.append((snapshot_day, path))

    candidates.sort(key=lambda row: row[0], reverse=True)
    return [path for _, path in candidates]


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
    history_dir = (
        config.history_dir if config.history_dir.is_absolute() else repo_root / config.history_dir
    )

    digest_payload = _load_json(digest_path, default={})
    digest_items_raw = digest_payload.get("items") if isinstance(digest_payload, dict) else []
    digest_items = digest_items_raw if isinstance(digest_items_raw, list) else []
    publish_items: list[dict[str, Any]] = [item for item in digest_items if isinstance(item, dict)]

    history_files_used = 0
    history_items_loaded = 0
    history_items_added = 0
    if config.include_history:
        seen_keys = {_item_key(item) for item in publish_items if _item_key(item)}
        for snapshot_path in _history_files_in_window(history_dir, config.history_days):
            payload = _load_json(snapshot_path, default={})
            raw_items = payload.get("items") if isinstance(payload, dict) else []
            if not isinstance(raw_items, list):
                continue
            history_files_used += 1

            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                history_items_loaded += 1
                key = _item_key(item)
                if key and key in seen_keys:
                    continue
                if key:
                    seen_keys.add(key)
                publish_items.append(item)
                history_items_added += 1

    raw_scores = _load_json(scores_path, default=[])
    scores = raw_scores if isinstance(raw_scores, list) else []

    raw_high_scores = _load_json(high_scores_path, default=[])
    high_scores = raw_high_scores if isinstance(raw_high_scores, list) else []

    score_stats = _score_stats_by_article(scores)
    high_score_stats = _high_scores_by_article(high_scores)

    precomputed_articles: list[dict[str, Any]] = []
    for item in publish_items:
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
            "items_count": len(digest_items),
            "include_history": config.include_history,
            "history_dir": str(config.history_dir) if config.include_history else None,
            "history_days": config.history_days if config.include_history else None,
            "history_files_used": history_files_used,
            "history_items_loaded": history_items_loaded,
            "history_items_added": history_items_added,
        },
        "artifacts": {
            "scores_path": str(config.scores),
            "high_scores_path": str(config.high_scores),
            "analysis_root": str(config.analysis_root),
        },
        "summary": {
            "articles": len(precomputed_articles),
            "digest_articles": len(digest_items),
            "history_articles_added": history_items_added,
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
