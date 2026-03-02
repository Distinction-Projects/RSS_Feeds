from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from load_experiment import NewsItem


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _required_text(value: Any, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"Missing required text field: {field_name}")
    return text


def _required_float(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Missing or invalid numeric field: {field_name}") from None


def _required_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Missing or invalid integer field: {field_name}") from None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = "" if value is None else str(value).strip()
    if not text:
        return _now_utc()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return _now_utc()


@dataclass(slots=True)
class RubricQuestion:
    """A single evaluative question inside a rubric."""

    question: str

    @classmethod
    def from_dict(cls, obj: dict[str, Any] | str) -> "RubricQuestion":
        if isinstance(obj, str):
            return cls(question=_required_text(obj, "question"))
        if not isinstance(obj, dict):
            raise ValueError("RubricQuestion must be a string or object.")
        return cls(question=_required_text(obj.get("question"), "question"))

    def to_dict(self) -> dict[str, Any]:
        return {"question": self.question}


@dataclass(slots=True)
class Rubric:
    """
    Scoring rubric with explicit standards so scoring is consistent.

    Fields:
    - expected_question_count: required number of question scores.
    - min_score_per_question/max_score_per_question: allowed range for each score.
    - anticipated_total_score: optional expected target within the valid total range.
    """

    name: str
    questions: list[RubricQuestion]
    expected_question_count: int
    min_score_per_question: float
    max_score_per_question: float
    anticipated_total_score: float | None = None

    def __post_init__(self) -> None:
        if self.expected_question_count <= 0:
            raise ValueError("Rubric.expected_question_count must be > 0.")
        if self.min_score_per_question > self.max_score_per_question:
            raise ValueError(
                "Rubric.min_score_per_question cannot exceed max_score_per_question."
            )
        if len(self.questions) != self.expected_question_count:
            raise ValueError(
                "Rubric question count mismatch: "
                f"expected {self.expected_question_count}, got {len(self.questions)}."
            )
        if self.anticipated_total_score is not None:
            min_total = self.min_score_per_question * self.expected_question_count
            max_total = self.max_score_per_question * self.expected_question_count
            if not (min_total <= self.anticipated_total_score <= max_total):
                raise ValueError(
                    "Rubric.anticipated_total_score must be within "
                    f"[{min_total}, {max_total}]"
                )

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "Rubric":
        if not isinstance(obj, dict):
            raise ValueError("Rubric must be an object.")

        raw_questions = obj.get("questions", [])
        if not isinstance(raw_questions, list):
            raise ValueError("Rubric.questions must be a list.")

        questions = [RubricQuestion.from_dict(question) for question in raw_questions]
        expected_count = _required_int(
            obj.get("expected_question_count", len(questions)),
            "rubric.expected_question_count",
        )
        min_score = _required_float(
            obj.get("min_score_per_question", 0.0),
            "rubric.min_score_per_question",
        )
        max_score = _required_float(
            obj.get("max_score_per_question", 5.0),
            "rubric.max_score_per_question",
        )

        return cls(
            name=_required_text(obj.get("name"), "rubric.name"),
            questions=questions,
            expected_question_count=expected_count,
            min_score_per_question=min_score,
            max_score_per_question=max_score,
            anticipated_total_score=_optional_float(obj.get("anticipated_total_score")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "questions": [question.to_dict() for question in self.questions],
            "expected_question_count": self.expected_question_count,
            "min_score_per_question": self.min_score_per_question,
            "max_score_per_question": self.max_score_per_question,
            "anticipated_total_score": self.anticipated_total_score,
        }

    @property
    def max_possible_score(self) -> float:
        return self.max_score_per_question * self.expected_question_count


@dataclass(slots=True)
class Score:
    """
    A scored evaluation that binds one rubric to one news item.

    `question_scores` must have one score per rubric question and each score must
    be within rubric score bounds.
    """

    rubric: Rubric
    news_item: NewsItem
    question_scores: list[float]
    value: float
    max_value: float
    reasoning: str
    scored_at: datetime

    def __post_init__(self) -> None:
        if len(self.question_scores) != self.rubric.expected_question_count:
            raise ValueError(
                "Score.question_scores length mismatch: "
                f"expected {self.rubric.expected_question_count}, got {len(self.question_scores)}."
            )

        for index, question_score in enumerate(self.question_scores):
            if not (
                self.rubric.min_score_per_question
                <= question_score
                <= self.rubric.max_score_per_question
            ):
                raise ValueError(
                    "Score.question_scores out of range at index "
                    f"{index}: {question_score} not in "
                    f"[{self.rubric.min_score_per_question}, {self.rubric.max_score_per_question}]"
                )

        computed_value = sum(self.question_scores)
        if abs(computed_value - self.value) > 1e-9:
            raise ValueError(
                f"Score.value must equal sum(question_scores): {computed_value} != {self.value}"
            )

        rubric_max = self.rubric.max_possible_score
        if abs(rubric_max - self.max_value) > 1e-9:
            raise ValueError(
                f"Score.max_value must equal rubric max score: {rubric_max} != {self.max_value}"
            )

    @classmethod
    def from_question_scores(
        cls,
        rubric: Rubric,
        news_item: NewsItem,
        question_scores: list[float],
        reasoning: str = "",
        scored_at: datetime | None = None,
    ) -> "Score":
        normalized_scores = [float(score) for score in question_scores]
        return cls(
            rubric=rubric,
            news_item=news_item,
            question_scores=normalized_scores,
            value=sum(normalized_scores),
            max_value=rubric.max_possible_score,
            reasoning=reasoning.strip(),
            scored_at=scored_at or _now_utc(),
        )

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "Score":
        if not isinstance(obj, dict):
            raise ValueError("Score must be an object.")

        rubric_raw = obj.get("rubric")
        if not isinstance(rubric_raw, dict):
            raise ValueError("Score.rubric must be an object.")
        news_raw = obj.get("news_item")
        if not isinstance(news_raw, dict):
            raise ValueError("Score.news_item must be an object.")
        raw_scores = obj.get("question_scores", [])
        if not isinstance(raw_scores, list):
            raise ValueError("Score.question_scores must be a list.")

        return cls(
            rubric=Rubric.from_dict(rubric_raw),
            news_item=NewsItem.from_dict(news_raw),
            question_scores=[float(score) for score in raw_scores],
            value=_required_float(obj.get("value"), "score.value"),
            max_value=_required_float(obj.get("max_value"), "score.max_value"),
            reasoning=_required_text(obj.get("reasoning", ""), "score.reasoning"),
            scored_at=_as_datetime(obj.get("scored_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rubric": self.rubric.to_dict(),
            "news_item": self.news_item.to_dict(),
            "question_scores": list(self.question_scores),
            "value": self.value,
            "max_value": self.max_value,
            "reasoning": self.reasoning,
            "scored_at": self.scored_at.isoformat(),
        }


@dataclass(slots=True)
class Lens:
    name: str
    summary: str
    instructions: str
    system_prompt: str
    user_prompt: str
    rubrics: list[Rubric]

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "Lens":
        if not isinstance(obj, dict):
            raise ValueError("Lens must be an object.")

        raw_rubrics = obj.get("rubrics", [])
        if not isinstance(raw_rubrics, list):
            raise ValueError("Lens.rubrics must be a list.")

        return cls(
            name=_required_text(obj.get("name"), "name"),
            summary=_required_text(obj.get("summary"), "summary"),
            instructions=_required_text(obj.get("instructions"), "instructions"),
            system_prompt=_required_text(obj.get("system_prompt"), "system_prompt"),
            user_prompt=_required_text(obj.get("user_prompt"), "user_prompt"),
            rubrics=[Rubric.from_dict(rubric) for rubric in raw_rubrics],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "instructions": self.instructions,
            "system_prompt": self.system_prompt,
            "user_prompt": self.user_prompt,
            "rubrics": [rubric.to_dict() for rubric in self.rubrics],
        }

    def rubric_by_name(self, name: str) -> Rubric:
        for rubric in self.rubrics:
            if rubric.name == name:
                return rubric
        raise ValueError(f"Rubric not found in lens: {name}")


def load_lens(path: str | Path) -> Lens:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = _extract_lens_entries(payload)
    if not entries:
        raise ValueError("No valid lens entries found.")
    return Lens.from_dict(entries[0])


def save_lens(lens: Lens, path: str | Path) -> None:
    Path(path).write_text(json.dumps(lens.to_dict(), indent=2), encoding="utf-8")


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


def load_lenses(path: str | Path) -> list[Lens]:
    files = _expand_lens_paths([path])
    if not files:
        raise ValueError(f"No matching lenses files found for: {path}")

    entries: list[dict[str, Any]] = []
    for file_path in files:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        entries.extend(_extract_lens_entries(payload))

    if not entries:
        raise ValueError("No valid lens entries found.")
    return [Lens.from_dict(entry) for entry in entries]


def save_lenses(lenses: list[Lens], path: str | Path) -> None:
    payload = {"lenses": [lens.to_dict() for lens in lenses]}
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_scores(path: str | Path) -> list[Score]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Scores file must be a JSON array.")
    return [Score.from_dict(entry) for entry in payload]


def save_scores(scores: list[Score], path: str | Path) -> None:
    payload = [score.to_dict() for score in scores]
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def template_lens() -> Lens:
    return Lens(
        name="Credibility Lens",
        summary="Evaluate whether an article is factual, transparent, and evidence-backed.",
        instructions=(
            "Read the article and answer each rubric question using specific evidence from "
            "the text. Be concise and cite direct passages when possible."
        ),
        system_prompt=(
            "You are an editorial evaluator. Score content using the rubric with neutral, "
            "evidence-based judgments."
        ),
        user_prompt="Evaluate this article using the credibility lens rubric.",
        rubrics=[
            Rubric(
                name="Evidence Quality",
                questions=[
                    RubricQuestion(question="Does the article cite named sources?"),
                    RubricQuestion(question="Are key claims supported by verifiable evidence?"),
                ],
                expected_question_count=2,
                min_score_per_question=0.0,
                max_score_per_question=5.0,
                anticipated_total_score=8.0,
            ),
            Rubric(
                name="Bias and Framing",
                questions=[
                    RubricQuestion(
                        question="Does the article present multiple relevant viewpoints?"
                    ),
                    RubricQuestion(
                        question="Is emotionally loaded language used to sway interpretation?"
                    ),
                ],
                expected_question_count=2,
                min_score_per_question=0.0,
                max_score_per_question=5.0,
                anticipated_total_score=7.0,
            ),
        ],
    )


if __name__ == "__main__":
    sample = template_lens()
    print(json.dumps(sample.to_dict(), indent=2))
