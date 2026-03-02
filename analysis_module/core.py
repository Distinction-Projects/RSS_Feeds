from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class LensDefinition:
    name: str
    rubric_names: list[str]
    max_total: float


@dataclass(frozen=True)
class RubricScore:
    item_id: str
    item_title: str
    item_source: str
    lens_name: str
    rubric_name: str
    value: float
    scored_at: datetime | None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_ignored_lens_names(directory: Path) -> set[str]:
    ignore_path = directory / "ignore.txt"
    if not ignore_path.is_file():
        return set()

    ignored: set[str] = set()
    for raw_line in ignore_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        ignored.add(Path(line).name)
    return ignored


def _expand_lens_paths(paths: Iterable[str | Path]) -> list[Path]:
    results: list[Path] = []
    seen: set[Path] = set()
    ignore_cache: dict[Path, set[str]] = {}

    def is_ignored(path: Path) -> bool:
        parent = path.parent.resolve()
        ignored = ignore_cache.get(parent)
        if ignored is None:
            ignored = _load_ignored_lens_names(parent)
            ignore_cache[parent] = ignored
        return path.name in ignored

    for raw in paths:
        raw_text = str(raw)
        matched: list[Path]

        if any(char in raw_text for char in "*?[]"):
            matched = [path for path in Path().glob(raw_text) if path.is_file()]
        else:
            path = Path(raw_text)
            if path.is_dir():
                matched = [child for child in path.glob("*.json") if child.is_file()]
            else:
                matched = [path]

        for path in sorted(matched):
            if is_ignored(path):
                continue
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                results.append(path)

    return results


def _extract_lens_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        raw = payload.get("lenses")
        if raw is None:
            return [payload]
        if not isinstance(raw, list):
            raise ValueError("Expected top-level 'lenses' list.")
        return [entry for entry in raw if isinstance(entry, dict)]
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    raise ValueError("Lenses file must be an object or array.")


def load_lenses(path: Path | str) -> tuple[list[LensDefinition], dict[str, str]]:
    files = _expand_lens_paths([path])
    if not files:
        raise ValueError(f"No lenses files found for: {path}")

    lenses_raw: list[dict[str, Any]] = []
    for file_path in files:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        lenses_raw.extend(_extract_lens_entries(payload))

    if not lenses_raw:
        raise ValueError("No lenses found in lenses path.")

    rubric_to_lens: dict[str, str] = {}
    duplicates: dict[str, set[str]] = {}
    lens_defs: list[LensDefinition] = []

    for lens in lenses_raw:
        if not isinstance(lens, dict):
            continue
        lens_name = str(lens.get("name", "")).strip() or "(unnamed lens)"
        rubrics = lens.get("rubrics", [])
        rubric_names: list[str] = []
        max_total = 0.0
        if isinstance(rubrics, list):
            for rubric in rubrics:
                if not isinstance(rubric, dict):
                    continue
                rubric_name = str(rubric.get("name", "")).strip()
                if rubric_name:
                    rubric_names.append(rubric_name)
                    if rubric_name in rubric_to_lens and rubric_to_lens[rubric_name] != lens_name:
                        duplicates.setdefault(rubric_name, set()).update(
                            {rubric_to_lens[rubric_name], lens_name}
                        )
                    rubric_to_lens[rubric_name] = lens_name
                expected_q = _safe_float(rubric.get("expected_question_count"))
                max_per_q = _safe_float(rubric.get("max_score_per_question"))
                if expected_q is not None and max_per_q is not None:
                    max_total += expected_q * max_per_q
        lens_defs.append(LensDefinition(name=lens_name, rubric_names=rubric_names, max_total=max_total))

    if duplicates:
        details = ", ".join(
            f"{name} ({', '.join(sorted(lenses))})" for name, lenses in duplicates.items()
        )
        raise ValueError(
            "Duplicate rubric names across lenses detected: "
            f"{details}. Rename rubrics or include lens name in scores to disambiguate."
        )

    return lens_defs, rubric_to_lens


def load_scores(path: Path, rubric_to_lens: dict[str, str]) -> list[RubricScore]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Scores file must be a JSON array.")

    scores: list[RubricScore] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        rubric = entry.get("rubric", {})
        if not isinstance(rubric, dict):
            continue
        rubric_name = str(rubric.get("name", "")).strip()
        if not rubric_name:
            continue
        lens_name = rubric_to_lens.get(rubric_name)
        if not lens_name:
            continue

        news_item = entry.get("news_item", {})
        if not isinstance(news_item, dict):
            continue
        item_id = str(news_item.get("id", "")).strip()
        if not item_id:
            continue
        item_title = str(news_item.get("title", "")).strip()
        item_source = str(news_item.get("source_name", "")).strip()

        value = _safe_float(entry.get("value"))
        if value is None:
            continue

        scored_at = _parse_dt(entry.get("scored_at"))

        scores.append(
            RubricScore(
                item_id=item_id,
                item_title=item_title,
                item_source=item_source,
                lens_name=lens_name,
                rubric_name=rubric_name,
                value=value,
                scored_at=scored_at,
            )
        )

    return scores


def _aggregate(values: list[tuple[datetime | None, float]], method: str) -> float | None:
    if not values:
        return None
    if method == "latest":
        latest = max(values, key=lambda pair: pair[0] or datetime.min)
        return latest[1]
    raw = [value for _, value in values]
    if method == "mean":
        return statistics.mean(raw)
    if method == "median":
        return statistics.median(raw)
    raise ValueError(f"Unknown aggregation method: {method}")


def build_lens_totals(
    scores: list[RubricScore],
    lens_defs: list[LensDefinition],
    aggregation: str,
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, str]]]:
    item_meta: dict[str, dict[str, str]] = {}
    bucket: dict[tuple[str, str, str], list[tuple[datetime | None, float]]] = {}

    for score in scores:
        item_meta.setdefault(
            score.item_id,
            {"title": score.item_title, "source": score.item_source},
        )
        key = (score.item_id, score.lens_name, score.rubric_name)
        bucket.setdefault(key, []).append((score.scored_at, score.value))

    lens_totals: dict[str, dict[str, float]] = {lens.name: {} for lens in lens_defs}

    for lens in lens_defs:
        for item_id in item_meta:
            total = 0.0
            missing = False
            for rubric_name in lens.rubric_names:
                values = bucket.get((item_id, lens.name, rubric_name), [])
                agg = _aggregate(values, aggregation)
                if agg is None:
                    missing = True
                    break
                total += agg
            if not missing:
                lens_totals[lens.name][item_id] = total

    return lens_totals, item_meta


def normalize_values(
    lens_defs: list[LensDefinition],
    lens_totals: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    max_by_lens = {lens.name: lens.max_total for lens in lens_defs}
    normalized: dict[str, dict[str, float]] = {lens.name: {} for lens in lens_defs}
    for lens_name, items in lens_totals.items():
        max_total = max_by_lens.get(lens_name, 0.0)
        if max_total <= 0:
            continue
        for item_id, value in items.items():
            normalized[lens_name][item_id] = value / max_total
    return normalized
