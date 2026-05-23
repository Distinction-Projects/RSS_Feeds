from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_CATALOG = Path("feed_catalog/rss_feeds.json")
DEFAULT_DIGEST_OUTPUT = Path("data/rss_openai_daily.json")
DEFAULT_HISTORY_DIR = Path("data/history")
DEFAULT_SCORES_OUTPUT = Path("data/scores.json")
DEFAULT_ANALYSIS_OUTPUT = Path("data/analysis")
DEFAULT_PROCESSED_OUTPUT = Path("data/processed/rss_openai_precomputed.json")
DEFAULT_CACHE_PATH = Path("data/cache/openai_cache.sqlite")
DEFAULT_PROMPT_AUDIT_DIR = Path("data/analysis/prompt_audit")
DEFAULT_SCORE_RUN_LOG_DIR = Path("data/analysis/score_run_logs")
DEFAULT_DIGEST_RUN_LOG_DIR = Path("data/analysis/digest_run_logs")
DEFAULT_FEED_AUDIT_OUTPUT = Path("data/analysis/feed_audit/rss_feed_audit.json")
DEFAULT_FEED_AUDIT_HISTORY_DIR = Path("data/analysis/feed_audit/history")
DEFAULT_FEED_AUDIT_RUN_LOG_DIR = Path("data/analysis/feed_audit/run_logs")
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_OPENAI_TIMEOUT_SECONDS = 60
DEFAULT_DIGEST_OPENAI_TIMEOUT_SECONDS = 180
DEFAULT_DIGEST_OPENAI_BATCH_SIZE = 8
DEFAULT_DIGEST_OPENAI_MAX_RETRIES = 2
DEFAULT_DIGEST_OPENAI_RETRY_BACKOFF_SECONDS = 5.0
DEFAULT_OPENAI_TEMPERATURE = 0.0
DEFAULT_HTTP_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass(slots=True)
class DigestBuildConfig:
    catalog: Path = DEFAULT_CATALOG
    output: Path = DEFAULT_DIGEST_OUTPUT
    archive_dir: Path = DEFAULT_HISTORY_DIR
    archive_enabled: bool = True
    skip_seen_items: bool = True
    max_sources: int = 72
    feeds_per_source: int = 1
    max_items_per_feed: int = 3
    timeout_seconds: int = 30
    feed_user_agent: str = DEFAULT_HTTP_USER_AGENT
    source_ids: tuple[str, ...] = ()
    scrape_enabled: bool = True
    scrape_limit: int | None = None
    scrape_timeout_seconds: float = 12.0
    scrape_sleep_seconds: float = 0.25
    scrape_user_agent: str = DEFAULT_HTTP_USER_AGENT
    openai_enabled: bool = True
    openai_model: str | None = None
    openai_timeout_seconds: int = DEFAULT_DIGEST_OPENAI_TIMEOUT_SECONDS
    openai_batch_size: int = DEFAULT_DIGEST_OPENAI_BATCH_SIZE
    openai_max_retries: int = DEFAULT_DIGEST_OPENAI_MAX_RETRIES
    openai_retry_backoff_seconds: float = DEFAULT_DIGEST_OPENAI_RETRY_BACKOFF_SECONDS
    cache_path: Path = DEFAULT_CACHE_PATH
    prompt_audit_dir: Path = DEFAULT_PROMPT_AUDIT_DIR
    run_log_dir: Path = DEFAULT_DIGEST_RUN_LOG_DIR


@dataclass(slots=True)
class FeedAuditConfig:
    catalog: Path = DEFAULT_CATALOG
    output: Path = DEFAULT_FEED_AUDIT_OUTPUT
    archive_history_dir: Path | None = DEFAULT_FEED_AUDIT_HISTORY_DIR
    max_sources: int = 72
    feeds_per_source: int = 2
    max_items_per_feed: int = 5
    timeout_seconds: int = 20
    source_ids: tuple[str, ...] = ()
    run_log_dir: Path = DEFAULT_FEED_AUDIT_RUN_LOG_DIR
    user_agent: str = DEFAULT_HTTP_USER_AGENT


@dataclass(slots=True)
class ScoreRunConfig:
    experiment: Path = DEFAULT_DIGEST_OUTPUT
    lenses_path: Path = Path("lenses")
    output: Path = DEFAULT_SCORES_OUTPUT
    model: str = DEFAULT_OPENAI_MODEL
    timeout_seconds: int = DEFAULT_OPENAI_TIMEOUT_SECONDS
    temperature: float = DEFAULT_OPENAI_TEMPERATURE
    replace_output: bool = False
    cache_path: Path = DEFAULT_CACHE_PATH
    prompt_audit_dir: Path = DEFAULT_PROMPT_AUDIT_DIR
    run_log_dir: Path = DEFAULT_SCORE_RUN_LOG_DIR
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
    analysis_root: Path = DEFAULT_ANALYSIS_OUTPUT
    output: Path = DEFAULT_PROCESSED_OUTPUT
    max_articles: int | None = None
    include_history: bool = False
    history_dir: Path = DEFAULT_HISTORY_DIR
    history_days: int | None = 30
