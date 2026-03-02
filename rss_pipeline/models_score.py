from __future__ import annotations

from dataclasses import dataclass

from lens import Score, load_scores, save_scores


@dataclass(slots=True)
class ScoreRunStats:
    total_scores: int = 0
    openai_calls: int = 0
    cache_hits: int = 0
    cache_misses: int = 0


__all__ = ["Score", "ScoreRunStats", "load_scores", "save_scores"]
