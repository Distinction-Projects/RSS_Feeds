from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from typing_extensions import TypedDict

from load_experiment import NewsItem
from serialization_utils import dump_json, validate_json

SELF_TEST_FLAG = "--self-test"
HELP_FLAGS: tuple[str, ...] = ("-h", "--help")


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


def _load_json_object(raw: str | bytes, *, context: str) -> dict[str, Any]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"{context}: expected JSON object root for compat parsing.")
    return payload


class RubricQuestionJSON(TypedDict):
    question: str


class RubricJSON(TypedDict):
    name: str
    questions: list[RubricQuestionJSON]
    expected_question_count: int
    min_score_per_question: float
    max_score_per_question: float
    anticipated_total_score: float | None


class LensJSON(TypedDict):
    name: str
    summary: str
    instructions: str
    system_prompt: str
    user_prompt: str
    rubrics: list[RubricJSON]


class ScoreJSON(TypedDict):
    rubric: RubricJSON
    news_item: dict[str, Any]
    question_scores: list[float]
    value: float
    max_value: float
    reasoning: str
    scored_at: str


class LensesCollectionJSON(TypedDict):
    lenses: list[LensJSON]


@dataclass(slots=True)
class RubricQuestion:
    """A single evaluative question inside a rubric."""

    question: str

    @classmethod
    def from_dict(cls, obj: dict[str, Any] | str) -> RubricQuestion:
        if isinstance(obj, str):
            return cls(question=_required_text(obj, "question"))
        if not isinstance(obj, dict):
            raise ValueError("RubricQuestion must be a string or object.")
        return cls(question=_required_text(obj.get("question"), "question"))

    def to_dict(self) -> dict[str, Any]:
        return {"question": self.question}

    @classmethod
    def from_json(cls, raw: str | bytes, *, strict: bool = False) -> RubricQuestion:
        if strict:
            payload = validate_json(RubricQuestionJSON, raw, context="RubricQuestion")
            return cls.from_dict(dict(payload))
        return cls.from_dict(_load_json_object(raw, context="RubricQuestion"))

    def to_json(self, *, indent: int | None = None) -> str:
        return dump_json(
            RubricQuestionJSON,
            self.to_dict(),
            indent=indent,
            context="RubricQuestion",
        )


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
            raise ValueError("Rubric.min_score_per_question cannot exceed max_score_per_question.")
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
                    f"Rubric.anticipated_total_score must be within [{min_total}, {max_total}]"
                )

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> Rubric:
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

    @classmethod
    def from_json(cls, raw: str | bytes, *, strict: bool = False) -> Rubric:
        if strict:
            payload = validate_json(RubricJSON, raw, context="Rubric")
            return cls.from_dict(dict(payload))
        return cls.from_dict(_load_json_object(raw, context="Rubric"))

    def to_json(self, *, indent: int | None = None) -> str:
        return dump_json(
            RubricJSON,
            self.to_dict(),
            indent=indent,
            context="Rubric",
        )

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
    ) -> Score:
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
    def from_dict(cls, obj: dict[str, Any]) -> Score:
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

    @classmethod
    def from_json(cls, raw: str | bytes, *, strict: bool = False) -> Score:
        if strict:
            payload = validate_json(ScoreJSON, raw, context="Score")
            return cls.from_dict(dict(payload))
        return cls.from_dict(_load_json_object(raw, context="Score"))

    def to_json(self, *, indent: int | None = None) -> str:
        return dump_json(
            ScoreJSON,
            self.to_dict(),
            indent=indent,
            context="Score",
        )


@dataclass(slots=True)
class Lens:
    name: str
    summary: str
    instructions: str
    system_prompt: str
    user_prompt: str
    rubrics: list[Rubric]

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> Lens:
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

    @classmethod
    def from_json(cls, raw: str | bytes, *, strict: bool = False) -> Lens:
        if strict:
            payload = validate_json(LensJSON, raw, context="Lens")
            return cls.from_dict(dict(payload))
        return cls.from_dict(_load_json_object(raw, context="Lens"))

    def to_json(self, *, indent: int | None = None) -> str:
        return dump_json(
            LensJSON,
            self.to_dict(),
            indent=indent,
            context="Lens",
        )

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
    Path(path).write_text(lens.to_json(indent=2), encoding="utf-8")


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
    Path(path).write_text(
        dump_json(
            LensesCollectionJSON,
            payload,
            indent=2,
            context="LensesCollection",
        ),
        encoding="utf-8",
    )


def load_scores(path: str | Path) -> list[Score]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Scores file must be a JSON array.")
    return [Score.from_dict(entry) for entry in payload]


def save_scores(scores: list[Score], path: str | Path) -> None:
    payload = [score.to_dict() for score in scores]
    Path(path).write_text(
        dump_json(list[ScoreJSON], payload, indent=2, context="ScoreList"),
        encoding="utf-8",
    )


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


def _sample_news_item() -> NewsItem:
    return NewsItem.from_dict(
        {
            "id": "sample-item-1",
            "title": "Sample Article",
            "link": "https://example.com/sample-article",
            "summary": "Sample summary text.",
            "published": "2026-03-01T12:00:00Z",
            "source_id": "sample-source",
            "source_name": "Sample Source",
            "feed_name": "Sample Feed",
            "feed_url": "https://example.com/feed.xml",
            "topic_tags": ["sample", "test"],
            "fetched_at": "2026-03-01T12:05:00Z",
            "ai_summary": "AI summary",
            "ai_tags": ["tag-a", "tag-b"],
            "scraped": None,
            "scrape_error": None,
        }
    )


def run_self_tests() -> int:
    failures: list[str] = []

    sample_lens = template_lens()
    sample_news = _sample_news_item()
    sample_rubric = sample_lens.rubrics[0]
    sample_score = Score.from_question_scores(
        rubric=sample_rubric,
        news_item=sample_news,
        question_scores=[4.0, 3.5],
        reasoning="Self-test score",
        scored_at=datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc),
    )

    try:
        lens_compat = Lens.from_json(sample_lens.to_json())
        lens_strict = Lens.from_json(sample_lens.to_json(), strict=True)
    except Exception as exc:
        failures.append(f"Lens JSON round-trip failed: {exc}")
    else:
        if lens_compat.name != sample_lens.name:
            failures.append(f"Lens compat name mismatch: {sample_lens.name} != {lens_compat.name}")
        if lens_strict.name != sample_lens.name:
            failures.append(f"Lens strict name mismatch: {sample_lens.name} != {lens_strict.name}")
        if len(lens_strict.rubrics) != len(sample_lens.rubrics):
            failures.append(
                "Lens strict rubric count mismatch: "
                f"{len(sample_lens.rubrics)} != {len(lens_strict.rubrics)}"
            )

    try:
        score_compat = Score.from_json(sample_score.to_json())
        score_strict = Score.from_json(sample_score.to_json(), strict=True)
    except Exception as exc:
        failures.append(f"Score JSON round-trip failed: {exc}")
    else:
        if abs(score_compat.value - sample_score.value) > 1e-9:
            failures.append(
                f"Score compat value mismatch: {sample_score.value} != {score_compat.value}"
            )
        if abs(score_strict.value - sample_score.value) > 1e-9:
            failures.append(
                f"Score strict value mismatch: {sample_score.value} != {score_strict.value}"
            )
        if score_strict.news_item.id != sample_score.news_item.id:
            failures.append(
                "Score strict news item id mismatch: "
                f"{sample_score.news_item.id} != {score_strict.news_item.id}"
            )

    malformed_score = sample_score.to_dict()
    malformed_score["question_scores"] = [5.0]  # Wrong length vs rubric expectation.
    try:
        Score.from_dict(malformed_score)
        failures.append("Score invariant check unexpectedly accepted malformed question_scores.")
    except ValueError:
        pass
    except Exception as exc:
        failures.append(f"Malformed score raised unexpected error type: {exc}")

    malformed_lens = sample_lens.to_dict()
    malformed_lens.pop("name", None)
    try:
        Lens.from_json(json.dumps(malformed_lens), strict=True)
        failures.append("Strict Lens validation unexpectedly accepted payload without name.")
    except ValueError:
        pass
    except Exception as exc:
        failures.append(f"Malformed Lens strict validation raised unexpected error type: {exc}")

    if failures:
        print(f"SELF-TEST FAILED ({len(failures)} issues)")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("SELF-TEST PASSED (lens/score serialization + invariants)")
    return 0


def _print_help() -> None:
    print("usage: lens.py [--self-test]")
    print()
    print("When run with no arguments, prints a template lens JSON object.")
    print()
    print("options:")
    print(f"  {SELF_TEST_FLAG}    Run built-in serialization and invariant tests")
    print("  -h, --help     Show this help message and exit")


def main(argv: list[str]) -> int:
    if any(flag in argv for flag in HELP_FLAGS):
        _print_help()
        return 0
    if SELF_TEST_FLAG in argv:
        return run_self_tests()

    sample = template_lens()
    print(sample.to_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
