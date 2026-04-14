from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
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
    lens_stats_root = analysis_root / "lens_stats"
    return {
        "lens_summary": _load_json(lens_summary_path, default={}),
        "source_differentiation": _load_json(source_diff_path, default={}),
        "lens_correlations": _load_lens_correlations(lens_stats_root),
    }


def _load_lens_score_rows(path: Path) -> dict[str, dict[str, float]]:
    if not path.exists():
        return {}

    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "item_id" not in reader.fieldnames:
                return {}

            lens_names = [
                field_name
                for field_name in reader.fieldnames
                if isinstance(field_name, str) and field_name not in {"item_id", "title"}
            ]
            rows: dict[str, dict[str, float]] = {}
            for row in reader:
                item_id = str(row.get("item_id") or "").strip()
                if not item_id:
                    continue

                lens_values: dict[str, float] = {}
                for lens_name in lens_names:
                    raw_value = row.get(lens_name)
                    if raw_value in (None, ""):
                        continue
                    try:
                        lens_values[lens_name] = float(raw_value)
                    except (TypeError, ValueError):
                        continue
                if lens_values:
                    rows[item_id] = lens_values
            return rows
    except OSError:
        return {}


def _load_square_matrix(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"lenses": [], "rows": []}
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            rows = list(reader)
    except OSError:
        return {"lenses": [], "rows": []}

    if not rows:
        return {"lenses": [], "rows": []}
    header = rows[0]
    if len(header) <= 1:
        return {"lenses": [], "rows": []}

    lenses = [str(value).strip() for value in header[1:] if str(value).strip()]
    parsed_rows: list[list[float | None]] = []
    for raw in rows[1:]:
        values = raw[1:]
        row_values: list[float | None] = []
        for raw_value in values:
            text = str(raw_value).strip()
            if not text:
                row_values.append(None)
                continue
            try:
                row_values.append(float(text))
            except ValueError:
                row_values.append(None)
        if lenses:
            row_values = row_values[: len(lenses)]
            if len(row_values) < len(lenses):
                row_values.extend([None] * (len(lenses) - len(row_values)))
        parsed_rows.append(row_values)

    if lenses:
        parsed_rows = parsed_rows[: len(lenses)]
        while len(parsed_rows) < len(lenses):
            parsed_rows.append([None] * len(lenses))

    return {"lenses": lenses, "rows": parsed_rows}


def _load_square_int_matrix(path: Path) -> dict[str, Any]:
    matrix = _load_square_matrix(path)
    lenses = matrix.get("lenses", [])
    rows = matrix.get("rows", [])
    int_rows: list[list[int | None]] = []
    for row in rows:
        int_rows.append(
            [int(value) if isinstance(value, (int, float)) else None for value in row]
        )
    return {"lenses": lenses, "rows": int_rows}


def _load_lens_correlations(lens_stats_root: Path) -> dict[str, Any]:
    raw = _load_square_matrix(lens_stats_root / "lens_correlation_raw.csv")
    normalized = _load_square_matrix(lens_stats_root / "lens_correlation_normalized.csv")
    covariance_raw = _load_square_matrix(lens_stats_root / "lens_covariance_raw.csv")
    covariance_normalized = _load_square_matrix(
        lens_stats_root / "lens_covariance_normalized.csv"
    )
    pairwise_counts = _load_square_int_matrix(lens_stats_root / "lens_pairwise_counts.csv")

    lenses = (
        raw.get("lenses")
        or normalized.get("lenses")
        or covariance_raw.get("lenses")
        or covariance_normalized.get("lenses")
        or pairwise_counts.get("lenses")
        or []
    )
    return {
        "lenses": lenses,
        "correlation": {
            "raw": raw.get("rows", []),
            "normalized": normalized.get("rows", []),
        },
        "covariance": {
            "raw": covariance_raw.get("rows", []),
            "normalized": covariance_normalized.get("rows", []),
        },
        "pairwise_counts": pairwise_counts.get("rows", []),
    }


def _lens_metadata(lens_summary: dict[str, Any]) -> dict[str, dict[str, float | int]]:
    metadata: dict[str, dict[str, float | int]] = {}
    lenses = lens_summary.get("lenses", []) if isinstance(lens_summary, dict) else []
    for record in lenses:
        if not isinstance(record, dict):
            continue
        lens_name = str(record.get("name") or "").strip()
        if not lens_name:
            continue
        try:
            max_total = float(record.get("max_total") or 0.0)
        except (TypeError, ValueError):
            max_total = 0.0
        try:
            rubric_count = int(record.get("rubric_count") or 0)
        except (TypeError, ValueError):
            rubric_count = 0
        metadata[lens_name] = {
            "max_value": max_total,
            "rubric_count": rubric_count,
        }
    return metadata


def _lens_scores_by_article(
    analysis_root: Path,
    lens_summary: dict[str, Any],
) -> dict[str, dict[str, dict[str, float | int]]]:
    raw_rows = _load_lens_score_rows(analysis_root / "lens_stats" / "lens_scores_raw.csv")
    normalized_rows = _load_lens_score_rows(
        analysis_root / "lens_stats" / "lens_scores_normalized.csv"
    )
    metadata = _lens_metadata(lens_summary)

    item_ids = set(raw_rows) | set(normalized_rows)
    per_article: dict[str, dict[str, dict[str, float | int]]] = {}
    for item_id in item_ids:
        raw_scores = raw_rows.get(item_id, {})
        normalized_scores = normalized_rows.get(item_id, {})
        lens_names = set(raw_scores) | set(normalized_scores)
        lens_payload: dict[str, dict[str, float | int]] = {}

        for lens_name in lens_names:
            raw_value = raw_scores.get(lens_name)
            normalized_value = normalized_scores.get(lens_name)
            lens_meta = metadata.get(lens_name, {})
            max_value = float(lens_meta.get("max_value") or 0.0)
            rubric_count = int(lens_meta.get("rubric_count") or 0)

            value: float | None = raw_value if isinstance(raw_value, (int, float)) else None
            if value is None and isinstance(normalized_value, (int, float)) and max_value > 0:
                value = float(normalized_value) * max_value

            percent: float
            if isinstance(normalized_value, (int, float)):
                percent = float(normalized_value) * 100.0
            elif value is not None and max_value > 0:
                percent = (value / max_value) * 100.0
            else:
                percent = 0.0

            if value is None and percent <= 0:
                continue

            lens_payload[lens_name] = {
                "value": round(float(value or 0.0), 4),
                "max_value": round(max_value, 4),
                "percent": round(percent, 4),
                "rubric_count": rubric_count,
            }

        if lens_payload:
            per_article[item_id] = lens_payload
    return per_article


def _rubric_counts_by_article(scores: list[dict[str, Any]]) -> dict[str, int]:
    per_article: dict[str, int] = defaultdict(int)
    for score in scores:
        if not isinstance(score, dict):
            continue
        news_item = score.get("news_item")
        if not isinstance(news_item, dict):
            continue
        item_id = str(news_item.get("id") or "").strip()
        if not item_id:
            continue
        per_article[item_id] += 1
    return per_article


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

    analysis_payload = _analysis_payload(analysis_root)
    rubric_counts = _rubric_counts_by_article(scores)
    score_lens_stats = _lens_scores_by_article(
        analysis_root,
        analysis_payload.get("lens_summary", {}),
    )

    precomputed_articles: list[dict[str, Any]] = []
    for item in publish_items:
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue

        lens_scores = score_lens_stats.get(item_id, {})

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
                    "rubric_count": int(rubric_counts.get(item_id, 0)),
                    "lens_scores": lens_scores,
                },
                "audit": item.get("audit", {}),
            }
        )

    precomputed_articles.sort(
        key=lambda row: (
            str(row.get("published") or ""),
            str(row.get("id") or ""),
        ),
        reverse=True,
    )
    if config.max_articles is not None and config.max_articles >= 0:
        precomputed_articles = precomputed_articles[: config.max_articles]

    output_payload = {
        "schema_version": "1.1",
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
            "analysis_root": str(config.analysis_root),
        },
        "summary": {
            "articles": len(precomputed_articles),
            "digest_articles": len(digest_items),
            "history_articles_added": history_items_added,
            "scored_articles": sum(
                1 for row in precomputed_articles if row["score"]["lens_scores"]
            ),
            "lens_scored_articles": sum(
                1 for row in precomputed_articles if row["score"]["lens_scores"]
            ),
        },
        "analysis": analysis_payload,
        "articles": precomputed_articles,
    }

    write_json(output_path, output_payload, ensure_ascii=False)
    return {"output": str(output_path), "articles": len(precomputed_articles)}
