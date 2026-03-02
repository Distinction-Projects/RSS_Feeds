from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_CATALOG = Path("feed_catalog/rss_feeds.json")
DEFAULT_DIGEST_OUTPUT = Path("data/rss_openai_daily.json")
DEFAULT_HISTORY_DIR = Path("data/history")
DEFAULT_SCORES_OUTPUT = Path("data/scores.json")
DEFAULT_HIGH_SCORES_OUTPUT = Path("data/high_scoring_articles.json")
DEFAULT_ANALYSIS_OUTPUT = Path("data/analysis")
DEFAULT_PROCESSED_OUTPUT = Path("data/processed/rss_openai_precomputed.json")
DEFAULT_CACHE_PATH = Path("data/cache/openai_cache.sqlite")
DEFAULT_PROMPT_AUDIT_DIR = Path("data/analysis/prompt_audit")
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_OPENAI_TIMEOUT_SECONDS = 60
DEFAULT_OPENAI_TEMPERATURE = 0.0


@dataclass(slots=True)
class DigestBuildConfig:
    catalog: Path = DEFAULT_CATALOG
    output: Path = DEFAULT_DIGEST_OUTPUT
    archive_dir: Path = DEFAULT_HISTORY_DIR
    archive_enabled: bool = True
    skip_seen_items: bool = True
    max_sources: int = 10
    feeds_per_source: int = 1
    max_items_per_feed: int = 3
    timeout_seconds: int = 30
    source_ids: tuple[str, ...] = ()
    scrape_enabled: bool = True
    scrape_limit: int | None = None
    scrape_timeout_seconds: float = 12.0
    scrape_sleep_seconds: float = 0.25
    scrape_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    openai_enabled: bool = True
    openai_model: str | None = None
    cache_path: Path = DEFAULT_CACHE_PATH
    prompt_audit_dir: Path = DEFAULT_PROMPT_AUDIT_DIR


@dataclass(slots=True)
class ScoreRunConfig:
    experiment: Path = DEFAULT_DIGEST_OUTPUT
    lenses_path: Path = Path("lenses")
    output: Path = DEFAULT_SCORES_OUTPUT
    high_scores_output: Path = DEFAULT_HIGH_SCORES_OUTPUT
    model: str = DEFAULT_OPENAI_MODEL
    timeout_seconds: int = DEFAULT_OPENAI_TIMEOUT_SECONDS
    temperature: float = DEFAULT_OPENAI_TEMPERATURE
    replace_output: bool = False
    high_score_threshold_percent: float = 60.0
    cache_path: Path = DEFAULT_CACHE_PATH
    prompt_audit_dir: Path = DEFAULT_PROMPT_AUDIT_DIR
    use_cache: bool = True


@dataclass(slots=True)
class AnalysisRunConfig:
    scores: Path = DEFAULT_SCORES_OUTPUT
    lenses_path: Path = Path("lenses")
    output_root: Path = DEFAULT_ANALYSIS_OUTPUT
    rubric_aggregation: str = "latest"
    source_permutations: int = 1000
    source_random_seed: int = 42


@dataclass(slots=True)
class PublishBuildConfig:
    digest: Path = DEFAULT_DIGEST_OUTPUT
    scores: Path = DEFAULT_SCORES_OUTPUT
    high_scores: Path = DEFAULT_HIGH_SCORES_OUTPUT
    analysis_root: Path = DEFAULT_ANALYSIS_OUTPUT
    output: Path = DEFAULT_PROCESSED_OUTPUT
    max_articles: int | None = None
