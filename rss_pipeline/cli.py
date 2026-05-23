from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from .artifact_store import archive_json, write_json
from .config import (
    DEFAULT_HTTP_USER_AGENT,
    AnalysisRunConfig,
    DigestBuildConfig,
    FeedAuditConfig,
    PublishBuildConfig,
    ScoreRunConfig,
)
from .feed_audit import evaluate_feed_audit_gates, run_feed_audit
from .logging import configure_logging
from .pipeline_analysis import run_analysis, run_pre_openai
from .pipeline_digest import archive_existing_digest, build_digest
from .pipeline_newsdata import NewsDataError, fetch_and_append, test_connection
from .pipeline_publish import build_precomputed_payload
from .pipeline_score import run_scoring
from .quality_history import (
    archive_quality_review,
    build_quality_history_report,
    load_quality_review_artifacts,
)
from .quality_review import build_digest_quality_review, evaluate_quality_gates
from .schema_validation import validate_digest_payload, validation_summary

app = typer.Typer(help="RSS pipeline CLI", no_args_is_help=True)
digest_app = typer.Typer(help="Digest workflows", no_args_is_help=True)
newsdata_app = typer.Typer(help="NewsData workflows", no_args_is_help=True)
score_app = typer.Typer(help="Scoring workflows", no_args_is_help=True)
analysis_app = typer.Typer(help="Analysis workflows", no_args_is_help=True)
publish_app = typer.Typer(help="Precomputed export workflows", no_args_is_help=True)
validate_app = typer.Typer(help="Validation workflows", no_args_is_help=True)

app.add_typer(digest_app, name="digest")
app.add_typer(newsdata_app, name="newsdata")
app.add_typer(score_app, name="score")
app.add_typer(analysis_app, name="analysis")
app.add_typer(publish_app, name="publish")
app.add_typer(validate_app, name="validate")


REPO_ROOT = Path(__file__).resolve().parent.parent
QUALITY_GATE_MAX_UNKNOWN_CONTENT_TYPES = 0
QUALITY_GATE_MAX_UNSUPPORTED_CONTENT_TYPES = 0
QUALITY_GATE_MAX_ACCEPTED_CONTENT_TYPE_FILTERS = 7
QUALITY_GATE_MAX_SOURCE_BLOCKED = 0
QUALITY_GATE_MAX_ACCEPTED_RSS_ONLY_FALLBACK = 4
FEED_AUDIT_MAX_FEED_FETCH_FAILURES = 0
FEED_AUDIT_MAX_MISSING_RSS_CONTENT = 5
FEED_AUDIT_MAX_UNKNOWN_CONTENT_TYPES = 0
FEED_AUDIT_MAX_UNSUPPORTED_CONTENT_TYPES = 0
FEED_AUDIT_MAX_ACCEPTED_CONTENT_TYPE_FILTERS: int | None = None


@digest_app.command("build")
def digest_build(
    catalog: Annotated[Path, typer.Option("--catalog")] = Path("feed_catalog/rss_feeds.json"),
    output: Annotated[Path, typer.Option("--output")] = Path("data/rss_openai_daily.json"),
    archive_dir: Annotated[Path, typer.Option("--archive-dir")] = Path("data/history"),
    no_archive: Annotated[bool, typer.Option("--no-archive")] = False,
    skip_seen: Annotated[bool, typer.Option("--skip-seen/--no-skip-seen")] = True,
    max_sources: Annotated[int, typer.Option("--max-sources")] = 72,
    feeds_per_source: Annotated[int, typer.Option("--feeds-per-source")] = 1,
    max_items_per_feed: Annotated[int, typer.Option("--max-items-per-feed")] = 3,
    timeout: Annotated[int, typer.Option("--timeout")] = 30,
    feed_user_agent: Annotated[
        str,
        typer.Option("--feed-user-agent"),
    ] = DEFAULT_HTTP_USER_AGENT,
    source_ids: Annotated[str | None, typer.Option("--source-ids")] = None,
    openai_model: Annotated[str | None, typer.Option("--openai-model")] = None,
    openai_timeout: Annotated[int, typer.Option("--openai-timeout")] = 180,
    openai_batch_size: Annotated[int, typer.Option("--openai-batch-size")] = 8,
    openai_max_retries: Annotated[int, typer.Option("--openai-max-retries")] = 2,
    openai_retry_backoff_seconds: Annotated[
        float,
        typer.Option("--openai-retry-backoff-seconds"),
    ] = 5.0,
    skip_openai: Annotated[bool, typer.Option("--skip-openai")] = False,
    skip_scrape: Annotated[bool, typer.Option("--skip-scrape")] = False,
    scrape_limit: Annotated[int | None, typer.Option("--scrape-limit")] = None,
    scrape_timeout_seconds: Annotated[float, typer.Option("--scrape-timeout-seconds")] = 12.0,
    scrape_sleep_seconds: Annotated[float, typer.Option("--scrape-sleep-seconds")] = 0.25,
    scrape_user_agent: Annotated[
        str,
        typer.Option("--scrape-user-agent"),
    ] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    cache_path: Annotated[Path, typer.Option("--cache-path")] = Path(
        "data/cache/openai_cache.sqlite"
    ),
    prompt_audit_dir: Annotated[
        Path,
        typer.Option("--prompt-audit-dir"),
    ] = Path("data/analysis/prompt_audit"),
    run_log_dir: Annotated[
        Path,
        typer.Option("--run-log-dir"),
    ] = Path("data/analysis/digest_run_logs"),
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> None:
    configure_logging(verbose=verbose)
    source_id_tuple = tuple(
        source_id.strip() for source_id in (source_ids or "").split(",") if source_id.strip()
    )
    config = DigestBuildConfig(
        catalog=catalog,
        output=output,
        archive_dir=archive_dir,
        archive_enabled=not no_archive,
        skip_seen_items=skip_seen,
        max_sources=max_sources,
        feeds_per_source=feeds_per_source,
        max_items_per_feed=max_items_per_feed,
        timeout_seconds=timeout,
        feed_user_agent=feed_user_agent,
        source_ids=source_id_tuple,
        scrape_enabled=not skip_scrape,
        scrape_limit=scrape_limit,
        scrape_timeout_seconds=scrape_timeout_seconds,
        scrape_sleep_seconds=scrape_sleep_seconds,
        scrape_user_agent=scrape_user_agent,
        openai_enabled=not skip_openai,
        openai_model=openai_model,
        openai_timeout_seconds=openai_timeout,
        openai_batch_size=openai_batch_size,
        openai_max_retries=openai_max_retries,
        openai_retry_backoff_seconds=openai_retry_backoff_seconds,
        cache_path=cache_path,
        prompt_audit_dir=prompt_audit_dir,
        run_log_dir=run_log_dir,
    )
    result = build_digest(config, repo_root=REPO_ROOT)
    typer.echo(f"Run ID: {result['run_id']}")
    typer.echo(f"Output: {result['output']}")
    if result.get("archive"):
        typer.echo(f"Archive: {result['archive']}")
    if result.get("run_log"):
        typer.echo(f"Run log: {result['run_log']}")
    typer.echo(f"Items: {result['items']} | Errors: {result['errors']}")
    summary = result.get("summary") or {}
    if summary:
        typer.echo(
            "Summary: "
            f"sources={summary.get('selected_sources', 0)} "
            f"feeds={summary.get('selected_feeds', 0)} "
            f"raw={summary.get('raw_fetched_items', 0)} "
            f"new={summary.get('new_items', 0)} "
            f"scrape_success={summary.get('scrape_success', 0)}/{summary.get('scrape_attempts', 0)} "
            f"openai_batches={summary.get('openai_batches_succeeded', 0)}/{summary.get('openai_batches_executed', 0)} "
            f"openai_retries={summary.get('openai_retry_attempts', 0)}"
        )
    cache = result.get("cache") or {}
    if cache:
        typer.echo(
            f"Cache calls={cache.get('calls', 0)} hits={cache.get('hits', 0)} misses={cache.get('misses', 0)}"
        )
    warnings = result.get("warnings") or []
    for warning in warnings:
        typer.echo(f"Warning: {warning}")


@digest_app.command("archive")
def digest_archive(
    output: Annotated[Path, typer.Option("--output")] = Path("data/rss_openai_daily.json"),
    archive_dir: Annotated[Path, typer.Option("--archive-dir")] = Path("data/history"),
) -> None:
    archive_path = archive_existing_digest(
        repo_root=REPO_ROOT, output=output, archive_dir=archive_dir
    )
    typer.echo(f"Archive written: {archive_path}")


@newsdata_app.command("fetch")
def newsdata_fetch(
    query: Annotated[str | None, typer.Option("--query")] = None,
    category: Annotated[str, typer.Option("--category")] = "top",
    country: Annotated[str, typer.Option("--country")] = "us",
    language: Annotated[str, typer.Option("--language")] = "en",
    size: Annotated[int, typer.Option("--size")] = 1,
    page: Annotated[str | None, typer.Option("--page")] = None,
    output: Annotated[Path, typer.Option("--output")] = Path("data/newsdata_dump.json"),
) -> None:
    try:
        result = fetch_and_append(
            output=output,
            query=query,
            category=category,
            country=country,
            language=language,
            size=size,
            page=page,
            base_dir=REPO_ROOT,
        )
    except NewsDataError as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc

    typer.echo(
        f"Saved {result['added']} new article(s), skipped {result['skipped']}. Output: {result['output']}"
    )


@newsdata_app.command("test")
def newsdata_test() -> None:
    try:
        result = test_connection(base_dir=REPO_ROOT)
    except NewsDataError as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc

    typer.echo(
        f"Status: {result['status']} | Results: {result['results']} | Total: {result['total_results']}"
    )
    top = result.get("top")
    if isinstance(top, dict):
        typer.echo(f"Top: {top['source']} | {top['title']}")
        if top.get("published"):
            typer.echo(f"Published: {top['published']}")


@score_app.command("run")
def score_run(
    experiment: Annotated[Path, typer.Option("--experiment")] = Path("data/rss_openai_daily.json"),
    lenses: Annotated[Path, typer.Option("--lenses")] = Path("lenses"),
    lens_name: Annotated[str | None, typer.Option("--lens-name")] = None,
    news_item_id: Annotated[str | None, typer.Option("--news-item-id")] = None,
    news_item_index: Annotated[int | None, typer.Option("--news-item-index")] = None,
    output: Annotated[Path, typer.Option("--output")] = Path("data/scores.json"),
    model: Annotated[str, typer.Option("--model")] = "gpt-4.1-mini",
    timeout_seconds: Annotated[int, typer.Option("--timeout-seconds")] = 60,
    temperature: Annotated[float, typer.Option("--temperature")] = 0.0,
    cache_path: Annotated[Path, typer.Option("--cache-path")] = Path(
        "data/cache/openai_cache.sqlite"
    ),
    no_cache: Annotated[bool, typer.Option("--no-cache")] = False,
    replace_output: Annotated[bool, typer.Option("--replace-output")] = False,
    prompt_audit_dir: Annotated[
        Path,
        typer.Option("--prompt-audit-dir"),
    ] = Path("data/analysis/prompt_audit"),
    run_log_dir: Annotated[
        Path,
        typer.Option("--run-log-dir"),
    ] = Path("data/analysis/score_run_logs"),
) -> None:
    config = ScoreRunConfig(
        experiment=experiment,
        lenses_path=lenses,
        output=output,
        model=model,
        timeout_seconds=timeout_seconds,
        temperature=temperature,
        replace_output=replace_output,
        cache_path=cache_path,
        prompt_audit_dir=prompt_audit_dir,
        run_log_dir=run_log_dir,
        use_cache=not no_cache,
    )

    result = run_scoring(
        config,
        repo_root=REPO_ROOT,
        lens_name=lens_name,
        news_item_id=news_item_id,
        news_item_index=news_item_index,
    )
    typer.echo(
        f"Wrote {result.new_scores} new scores to {result.output} "
        f"(total records: {result.total_records})."
    )
    typer.echo(
        f"Scored items={result.scored_items} "
        f"skipped_not_llm_ready={result.skipped_not_llm_ready} "
        f"skipped_missing_ai_summary={result.skipped_missing_ai_summary}"
    )
    typer.echo(
        f"OpenAI calls={result.openai_calls} cache_hits={result.cache_hits} cache_misses={result.cache_misses}"
    )
    if result.prompt_audit_export:
        typer.echo(f"Prompt audit export: {result.prompt_audit_export}")
    if result.run_log_path:
        typer.echo(f"Run log: {result.run_log_path}")


@analysis_app.command("run")
def analysis_run(
    scores: Annotated[Path, typer.Option("--scores")] = Path("data/scores.json"),
    lenses: Annotated[Path, typer.Option("--lenses")] = Path("lenses"),
    output_root: Annotated[Path, typer.Option("--output-root")] = Path("data/analysis"),
    rubric_aggregation: Annotated[str, typer.Option("--rubric-aggregation")] = "latest",
    source_permutations: Annotated[int, typer.Option("--source-permutations")] = 1000,
    source_random_seed: Annotated[int, typer.Option("--source-random-seed")] = 42,
) -> None:
    config = AnalysisRunConfig(
        scores=scores,
        lenses_path=lenses,
        output_root=output_root,
        rubric_aggregation=rubric_aggregation,
        source_permutations=source_permutations,
        source_random_seed=source_random_seed,
    )
    outputs = run_analysis(config, root=REPO_ROOT)
    typer.echo("Post-OpenAI stage complete.")
    typer.echo(f"matrix: {outputs['matrix']}")
    typer.echo(f"lens_stats: {outputs['lens_stats']}")
    typer.echo(f"report: {outputs['report']}")


@publish_app.command("build")
def publish_build(
    digest: Annotated[Path, typer.Option("--digest")] = Path("data/rss_openai_daily.json"),
    scores: Annotated[Path, typer.Option("--scores")] = Path("data/scores.json"),
    analysis_root: Annotated[Path, typer.Option("--analysis-root")] = Path("data/analysis"),
    output: Annotated[Path, typer.Option("--output")] = Path(
        "data/processed/rss_openai_precomputed.json"
    ),
    max_articles: Annotated[int | None, typer.Option("--max-articles")] = None,
    include_history: Annotated[
        bool, typer.Option("--include-history/--no-include-history")
    ] = False,
    history_dir: Annotated[Path, typer.Option("--history-dir")] = Path("data/history"),
    history_days: Annotated[int | None, typer.Option("--history-days")] = 30,
) -> None:
    config = PublishBuildConfig(
        digest=digest,
        scores=scores,
        analysis_root=analysis_root,
        output=output,
        max_articles=max_articles,
        include_history=include_history,
        history_dir=history_dir,
        history_days=history_days,
    )
    result = build_precomputed_payload(config, repo_root=REPO_ROOT)
    typer.echo(f"Precomputed output: {result['output']}")
    typer.echo(f"Articles: {result['articles']}")


@validate_app.command("all")
def validate_all() -> None:
    commands = [
        [sys.executable, str(REPO_ROOT / "load_experiment.py"), "--self-test"],
        [sys.executable, str(REPO_ROOT / "lens.py"), "--self-test"],
        [
            sys.executable,
            "-m",
            "unittest",
            "-v",
            "tests/test_cli_validation.py",
            "tests/test_content_classifier.py",
            "tests/test_serialization_contracts.py",
            "tests/test_cache_sqlite.py",
            "tests/test_digest_dedupe.py",
            "tests/test_digest_structured_logging.py",
            "tests/test_failure_taxonomy.py",
            "tests/test_feed_audit.py",
            "tests/test_llm_readiness.py",
            "tests/test_normalization.py",
            "tests/test_quality_diagnostics.py",
            "tests/test_quality_history.py",
            "tests/test_quality_report.py",
            "tests/test_quality_review.py",
            "tests/test_scrape_policy.py",
            "tests/test_schema_validation.py",
        ],
        [sys.executable, str(REPO_ROOT / "load_experiment.py"), "data/rss_openai_daily.json"],
        [
            sys.executable,
            "-m",
            "rss_pipeline.cli",
            "validate",
            "quality",
            "--digest",
            "data/rss_openai_daily.json",
            "--max-unknown-content-types",
            str(QUALITY_GATE_MAX_UNKNOWN_CONTENT_TYPES),
            "--max-unsupported-content-types",
            str(QUALITY_GATE_MAX_UNSUPPORTED_CONTENT_TYPES),
            "--max-accepted-content-type-filters",
            str(QUALITY_GATE_MAX_ACCEPTED_CONTENT_TYPE_FILTERS),
            "--max-source-blocked",
            str(QUALITY_GATE_MAX_SOURCE_BLOCKED),
            "--max-accepted-rss-only-fallback",
            str(QUALITY_GATE_MAX_ACCEPTED_RSS_ONLY_FALLBACK),
        ],
        [
            sys.executable,
            "-m",
            "rss_pipeline.cli",
            "validate",
            "feed-audit",
            "--help",
        ],
        [
            sys.executable,
            "-m",
            "rss_pipeline.cli",
            "publish",
            "build",
            "--digest",
            "tests/fixtures/canonical_digest.json",
            "--scores",
            "tests/fixtures/valid_scores.json",
            "--analysis-root",
            "tests/fixtures",
            "--output",
            "/tmp/rss_pipeline_validate_precomputed.json",
        ],
    ]
    for command in commands:
        typer.echo("$ " + " ".join(command))
        subprocess.run(command, check=True, cwd=REPO_ROOT)


@validate_app.command("digest")
def validate_digest(
    digest: Annotated[Path, typer.Option("--digest")] = Path("data/rss_openai_daily.json"),
    strict: Annotated[
        bool,
        typer.Option(
            "--strict/--compat",
            help="Require the latest self-audit fields instead of accepting legacy digests.",
        ),
    ] = False,
) -> None:
    digest_path = digest if digest.is_absolute() else REPO_ROOT / digest
    payload = json.loads(digest_path.read_text(encoding="utf-8"))
    summary = validation_summary(
        validate_digest_payload(
            payload,
            require_quality_report=strict,
            require_canonical=strict,
            require_content_type=strict,
        )
    )
    summary["mode"] = "strict" if strict else "compat"
    typer.echo(json.dumps(summary, indent=2))
    if summary["status"] != "pass":
        raise typer.Exit(code=1)


@validate_app.command("quality")
def validate_quality(
    digest: Annotated[Path, typer.Option("--digest")] = Path("data/rss_openai_daily.json"),
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help="Optional path for writing the full quality review JSON.",
        ),
    ] = None,
    archive_history_dir: Annotated[
        Path | None,
        typer.Option(
            "--archive-history-dir",
            help="Optional directory for writing a dated quality review history snapshot.",
        ),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1)] = 10,
    as_json: Annotated[bool, typer.Option("--json")] = False,
    fail_on_issue: Annotated[bool, typer.Option("--fail-on-issue")] = False,
    max_unknown_content_types: Annotated[
        int | None,
        typer.Option(
            "--max-unknown-content-types",
            min=0,
            help="Fail when inferred/explicit unknown content types exceed this count.",
        ),
    ] = None,
    max_unsupported_content_types: Annotated[
        int | None,
        typer.Option(
            "--max-unsupported-content-types",
            min=0,
            help="Fail when unsupported NewsLens content-type items exceed this count.",
        ),
    ] = None,
    max_accepted_content_type_filters: Annotated[
        int | None,
        typer.Option(
            "--max-accepted-content-type-filters",
            min=0,
            help="Fail when accepted content-type filter items exceed this count.",
        ),
    ] = None,
    max_source_blocked: Annotated[
        int | None,
        typer.Option(
            "--max-source-blocked",
            min=0,
            help="Fail when source-blocked article fetches exceed this count.",
        ),
    ] = None,
    max_accepted_rss_only_fallback: Annotated[
        int | None,
        typer.Option(
            "--max-accepted-rss-only-fallback",
            min=0,
            help="Fail when accepted RSS-only fallback items exceed this count.",
        ),
    ] = None,
    max_llm_review_items: Annotated[
        int | None,
        typer.Option(
            "--max-llm-review-items",
            min=0,
            help="Fail when items needing pre-LLM judge review exceed this count.",
        ),
    ] = None,
    max_empty_scraped_text: Annotated[
        int | None,
        typer.Option(
            "--max-empty-scraped-text",
            min=0,
            help="Fail when scrape-success items with zero usable text exceed this count.",
        ),
    ] = None,
    max_short_scraped_text: Annotated[
        int | None,
        typer.Option(
            "--max-short-scraped-text",
            min=0,
            help="Fail when scrape-success items below the LLM text threshold exceed this count.",
        ),
    ] = None,
) -> None:
    digest_path = digest if digest.is_absolute() else REPO_ROOT / digest
    payload = json.loads(digest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise typer.Exit(code=_print_error(f"Digest must be a JSON object: {digest_path}"))

    review = build_digest_quality_review(payload, limit=limit)
    quality_gate = evaluate_quality_gates(
        review,
        max_unknown_content_types=max_unknown_content_types,
        max_unsupported_content_types=max_unsupported_content_types,
        max_accepted_content_type_filters=max_accepted_content_type_filters,
        max_source_blocked=max_source_blocked,
        max_accepted_rss_only_fallback=max_accepted_rss_only_fallback,
        max_llm_review_items=max_llm_review_items,
        max_empty_scraped_text=max_empty_scraped_text,
        max_short_scraped_text=max_short_scraped_text,
    )
    if quality_gate["thresholds"]:
        review["quality_gate"] = quality_gate
    output_path = output if output is not None and output.is_absolute() else None
    if output is not None:
        output_path = output if output.is_absolute() else REPO_ROOT / output
        write_json(output_path, review)
    history_archive_path = None
    if archive_history_dir is not None:
        archive_dir_path = (
            archive_history_dir
            if archive_history_dir.is_absolute()
            else REPO_ROOT / archive_history_dir
        )
        archive_base_path = output_path or REPO_ROOT / "rss_digest_quality_review.json"
        history_archive_path = archive_quality_review(
            review,
            output_path=archive_base_path,
            history_dir=archive_dir_path,
        )
    if as_json:
        typer.echo(json.dumps(review, indent=2))
    else:
        _print_quality_review(review)
        if output is not None:
            typer.echo(f"Quality review output: {output_path}")
        if history_archive_path is not None:
            typer.echo(f"Quality review history: {history_archive_path}")

    if fail_on_issue and review["status"] != "pass":
        raise typer.Exit(code=1)
    if quality_gate["status"] != "pass":
        raise typer.Exit(code=1)


@validate_app.command("quality-history")
def validate_quality_history(
    current: Annotated[
        Path | None,
        typer.Option("--current", help="Current quality review JSON to include."),
    ] = Path("data/analysis/quality/rss_digest_quality_review.json"),
    history_dir: Annotated[Path, typer.Option("--history-dir")] = Path(
        "data/analysis/quality/history"
    ),
    limit: Annotated[int, typer.Option("--limit", min=1)] = 10,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    history_dir_path = history_dir if history_dir.is_absolute() else REPO_ROOT / history_dir
    current_path = None
    if current is not None:
        current_path = current if current.is_absolute() else REPO_ROOT / current
    artifacts, load_errors = load_quality_review_artifacts(
        history_dir=history_dir_path,
        current_path=current_path,
    )
    report = build_quality_history_report(
        artifacts,
        load_errors=load_errors,
        limit=limit,
    )
    if as_json:
        typer.echo(json.dumps(report, indent=2))
    else:
        _print_quality_history(report)

    if report["status"] == "fail":
        raise typer.Exit(code=1)


@validate_app.command("feed-audit")
def validate_feed_audit(
    catalog: Annotated[Path, typer.Option("--catalog")] = Path("feed_catalog/rss_feeds.json"),
    output: Annotated[Path, typer.Option("--output")] = Path(
        "data/analysis/feed_audit/rss_feed_audit.json"
    ),
    archive_history_dir: Annotated[
        Path | None,
        typer.Option(
            "--archive-history-dir",
            help="Optional directory for writing a dated feed-audit history snapshot.",
        ),
    ] = Path("data/analysis/feed_audit/history"),
    max_sources: Annotated[int, typer.Option("--max-sources", min=1)] = 72,
    feeds_per_source: Annotated[int, typer.Option("--feeds-per-source", min=1)] = 2,
    max_items_per_feed: Annotated[int, typer.Option("--max-items-per-feed", min=1)] = 5,
    timeout: Annotated[int, typer.Option("--timeout", min=1)] = 20,
    source_ids: Annotated[str | None, typer.Option("--source-ids")] = None,
    run_log_dir: Annotated[Path, typer.Option("--run-log-dir")] = Path(
        "data/analysis/feed_audit/run_logs"
    ),
    limit: Annotated[int, typer.Option("--limit", min=1)] = 10,
    as_json: Annotated[bool, typer.Option("--json")] = False,
    fail_on_issue: Annotated[bool, typer.Option("--fail-on-issue")] = False,
    max_feed_fetch_failures: Annotated[
        int | None,
        typer.Option(
            "--max-feed-fetch-failures",
            min=0,
            help="Fail when feed fetch failures exceed this count.",
        ),
    ] = FEED_AUDIT_MAX_FEED_FETCH_FAILURES,
    max_missing_rss_content: Annotated[
        int | None,
        typer.Option(
            "--max-missing-rss-content",
            min=0,
            help="Fail when RSS items without content text exceed this count.",
        ),
    ] = FEED_AUDIT_MAX_MISSING_RSS_CONTENT,
    max_unknown_content_types: Annotated[
        int | None,
        typer.Option(
            "--max-unknown-content-types",
            min=0,
            help="Fail when unknown content-type items exceed this count.",
        ),
    ] = FEED_AUDIT_MAX_UNKNOWN_CONTENT_TYPES,
    max_unsupported_content_types: Annotated[
        int | None,
        typer.Option(
            "--max-unsupported-content-types",
            min=0,
            help="Fail when unresolved unsupported content-type items exceed this count.",
        ),
    ] = FEED_AUDIT_MAX_UNSUPPORTED_CONTENT_TYPES,
    max_accepted_content_type_filters: Annotated[
        int | None,
        typer.Option(
            "--max-accepted-content-type-filters",
            min=0,
            help="Fail when accepted content-type filter items exceed this count.",
        ),
    ] = FEED_AUDIT_MAX_ACCEPTED_CONTENT_TYPE_FILTERS,
) -> None:
    configure_logging(verbose=False)
    source_id_tuple = tuple(
        source_id.strip() for source_id in (source_ids or "").split(",") if source_id.strip()
    )
    config = FeedAuditConfig(
        catalog=catalog,
        output=output,
        archive_history_dir=archive_history_dir,
        max_sources=max_sources,
        feeds_per_source=feeds_per_source,
        max_items_per_feed=max_items_per_feed,
        timeout_seconds=timeout,
        source_ids=source_id_tuple,
        run_log_dir=run_log_dir,
    )
    try:
        report = run_feed_audit(config, repo_root=REPO_ROOT, limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise typer.Exit(code=_print_error(str(exc))) from exc

    feed_audit_gate = evaluate_feed_audit_gates(
        report,
        max_feed_fetch_failures=max_feed_fetch_failures,
        max_missing_rss_content=max_missing_rss_content,
        max_unknown_content_types=max_unknown_content_types,
        max_unsupported_content_types=max_unsupported_content_types,
        max_accepted_content_type_filters=max_accepted_content_type_filters,
    )
    if feed_audit_gate["thresholds"]:
        report["quality_gate"] = feed_audit_gate
        output_path = output if output.is_absolute() else REPO_ROOT / output
        write_json(output_path, report)
        if archive_history_dir is not None:
            archive_dir = (
                archive_history_dir
                if archive_history_dir.is_absolute()
                else REPO_ROOT / archive_history_dir
            )
            archive_json(report, output_path, archive_dir)

    if as_json:
        typer.echo(json.dumps(report, indent=2))
    else:
        _print_feed_audit(report)

    if fail_on_issue and report["status"] != "pass":
        raise typer.Exit(code=1)
    if feed_audit_gate["status"] != "pass":
        raise typer.Exit(code=1)


@app.command("pre-openai")
def pre_openai(
    experiment: Annotated[str, typer.Option("--experiment")] = "data/rss_openai_daily.json",
    skip_summary: Annotated[bool, typer.Option("--skip-summary")] = False,
    scrape: Annotated[bool, typer.Option("--scrape")] = False,
    scrape_output: Annotated[str | None, typer.Option("--scrape-output")] = None,
    scrape_output_dir: Annotated[str | None, typer.Option("--scrape-output-dir")] = None,
    scrape_output_suffix: Annotated[str, typer.Option("--scrape-output-suffix")] = "_scraped",
    scrape_limit: Annotated[int | None, typer.Option("--scrape-limit")] = None,
    scrape_sleep_seconds: Annotated[float | None, typer.Option("--scrape-sleep-seconds")] = None,
    scrape_timeout_seconds: Annotated[
        float | None,
        typer.Option("--scrape-timeout-seconds"),
    ] = None,
    scrape_user_agent: Annotated[str | None, typer.Option("--scrape-user-agent")] = None,
    force_rescrape: Annotated[bool, typer.Option("--force-rescrape")] = False,
) -> None:
    run_pre_openai(
        experiment=experiment,
        skip_summary=skip_summary,
        scrape=scrape,
        scrape_output=scrape_output,
        scrape_output_dir=scrape_output_dir,
        scrape_output_suffix=scrape_output_suffix,
        scrape_limit=scrape_limit,
        scrape_sleep_seconds=scrape_sleep_seconds,
        scrape_timeout_seconds=scrape_timeout_seconds,
        scrape_user_agent=scrape_user_agent,
        force_rescrape=force_rescrape,
        root=REPO_ROOT,
    )
    typer.echo("Pre-OpenAI stage complete.")


def run_cli(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    try:
        result = app(args=args, prog_name="rssctl", standalone_mode=False)
    except typer.Exit as exc:
        return int(exc.exit_code)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        return int(code)
    if isinstance(result, int):
        return result
    return 0


def run_legacy(prefix: list[str], argv: list[str] | None = None) -> int:
    args = prefix + (argv if argv is not None else sys.argv[1:])
    return run_cli(args)


def _print_error(message: str) -> int:
    typer.echo(message, err=True)
    return 1


def _print_quality_review(review: dict[str, object]) -> None:
    typer.echo(
        "Quality: "
        f"status={review.get('status')} "
        f"items={review.get('total_items')} "
        f"issue_items={review.get('issue_item_count')}"
    )
    if review.get("generated_at"):
        typer.echo(f"Generated: {review['generated_at']}")
    if review.get("run_id"):
        typer.echo(f"Run ID: {review['run_id']}")
    metrics = review.get("quality_gate_metrics")
    if isinstance(metrics, dict):
        typer.echo(
            "LLM input: "
            f"ready={metrics.get('llm_ready_items', 0)} "
            f"review={metrics.get('llm_review_items', 0)} "
            f"rss_fallback={metrics.get('llm_rss_fallback_items', 0)} "
            f"excluded={metrics.get('llm_excluded_items', 0)}"
        )

    gate = review.get("quality_gate")
    if isinstance(gate, dict):
        typer.echo(f"Quality gate: status={gate.get('status')}")
        violations = gate.get("violations")
        if isinstance(violations, list) and violations:
            typer.echo("Gate violations:")
            for violation in violations:
                if isinstance(violation, dict):
                    typer.echo(
                        "  - "
                        f"{violation.get('metric')}: "
                        f"{violation.get('actual')} > {violation.get('threshold')}"
                    )

    for label, key, value_name in (
        ("Top issues", "issue_counts", "issue"),
        ("Top sources", "top_sources", "source"),
        ("Top feeds", "top_feeds", "feed"),
        ("Top content-type issues", "top_content_type_issues", "issue"),
    ):
        rows = review.get(key)
        if not isinstance(rows, list) or not rows:
            continue
        typer.echo(f"{label}:")
        for row in rows:
            if not isinstance(row, dict):
                continue
            prefix = str(row.get(value_name) or row.get("content_type") or "")
            extra = ""
            if row.get("content_type") and value_name != "content_type":
                extra = f" ({row['content_type']})"
            typer.echo(f"  - {prefix}{extra}: {row.get('count', 0)}")


def _print_quality_history(report: dict[str, object]) -> None:
    typer.echo(
        "Quality history: "
        f"status={report.get('status')} "
        f"trend={report.get('trend')} "
        f"snapshots={report.get('snapshot_count')}"
    )
    latest = report.get("latest")
    if isinstance(latest, dict):
        typer.echo(_snapshot_line("Latest", latest))
    previous = report.get("previous")
    if isinstance(previous, dict):
        typer.echo(_snapshot_line("Previous", previous))

    metric_deltas = report.get("metric_deltas")
    if isinstance(metric_deltas, dict) and metric_deltas:
        typer.echo("Metric deltas:")
        for metric, row in metric_deltas.items():
            if not isinstance(row, dict):
                continue
            typer.echo(
                "  - "
                f"{metric}: {row.get('previous', 0)} -> {row.get('latest', 0)} "
                f"({_signed_delta(row.get('delta', 0))})"
            )

    issue_deltas = report.get("issue_count_deltas")
    if isinstance(issue_deltas, list) and issue_deltas:
        typer.echo("Issue deltas:")
        for row in issue_deltas:
            if not isinstance(row, dict):
                continue
            typer.echo(
                "  - "
                f"{row.get('issue')}: {row.get('previous', 0)} -> {row.get('latest', 0)} "
                f"({_signed_delta(row.get('delta', 0))})"
            )

    load_errors = report.get("load_errors")
    if isinstance(load_errors, list) and load_errors:
        typer.echo("Load errors:")
        for row in load_errors:
            if isinstance(row, dict):
                typer.echo(f"  - {row.get('path')}: {row.get('error')}")


def _print_feed_audit(report: dict[str, object]) -> None:
    summary_raw = report.get("summary")
    summary = summary_raw if isinstance(summary_raw, dict) else {}
    typer.echo(
        "Feed audit: "
        f"status={report.get('status')} "
        f"sources={summary.get('selected_sources', 0)} "
        f"feeds={summary.get('selected_feeds', 0)} "
        f"raw={summary.get('raw_fetched_items', 0)} "
        f"newsfeed={summary.get('typical_newsfeed_items', 0)} "
        f"excluded={summary.get('newsfeed_excluded', 0)} "
        f"feed_failures={summary.get('feed_fetch_failed', 0)}"
    )
    run = report.get("run")
    if isinstance(run, dict):
        typer.echo(f"Generated: {run.get('generated_at')}")
        typer.echo(f"Run ID: {run.get('id')}")

    gate = report.get("quality_gate")
    if isinstance(gate, dict):
        typer.echo(f"Feed audit gate: status={gate.get('status')}")
        violations = gate.get("violations")
        if isinstance(violations, list) and violations:
            typer.echo("Gate violations:")
            for violation in violations:
                if isinstance(violation, dict):
                    typer.echo(
                        "  - "
                        f"{violation.get('metric')}: "
                        f"{violation.get('actual')} > {violation.get('threshold')}"
                    )

    source_health_summary = report.get("source_health_summary")
    if isinstance(source_health_summary, dict):
        status_counts = source_health_summary.get("status_counts")
        status_values = status_counts if isinstance(status_counts, dict) else {}
        typer.echo(
            "Source health: "
            f"healthy={status_values.get('healthy', 0)} "
            f"watch={status_values.get('watch', 0)} "
            f"hold_candidate={status_values.get('hold_candidate', 0)}"
        )

    review_rows = report.get("sources_needing_review")
    if isinstance(review_rows, list) and review_rows:
        typer.echo("Sources needing review:")
        for row in review_rows[:5]:
            if not isinstance(row, dict):
                continue
            typer.echo(
                "  - "
                f"{row.get('source_name')}: "
                f"status={row.get('status')} "
                f"action={row.get('recommended_action')} "
                f"issues={row.get('issue_count', 0)}"
            )

    for label, key, value_name in (
        ("Top issues", "issue_counts", "issue"),
        ("Top content types", "content_type_counts", "content_type"),
        ("Top sources", "top_sources", "source"),
        ("Top feeds", "top_feeds", "feed"),
    ):
        rows = report.get(key)
        if not isinstance(rows, list) or not rows:
            continue
        typer.echo(f"{label}:")
        for row in rows:
            if isinstance(row, dict):
                typer.echo(f"  - {row.get(value_name)}: {row.get('count', 0)}")

    output_path = report.get("audit")
    if isinstance(output_path, dict) and output_path.get("output_path"):
        typer.echo(f"Feed audit output: {output_path['output_path']}")
    if report.get("history_archive"):
        typer.echo(f"Feed audit history: {report['history_archive']}")


def _snapshot_line(label: str, snapshot: dict[str, object]) -> str:
    raw_metrics = snapshot.get("metrics")
    metrics: dict[str, object] = raw_metrics if isinstance(raw_metrics, dict) else {}
    return (
        f"{label}: "
        f"generated_at={snapshot.get('generated_at') or 'unknown'} "
        f"status={snapshot.get('status')} "
        f"items={metrics.get('total_items', 0)} "
        f"issue_items={metrics.get('issue_item_count', 0)} "
        f"content_filtered={metrics.get('accepted_content_type_filter_items', 0)} "
        f"source_blocked={metrics.get('source_blocked_items', 0)} "
        f"accepted_fallback={metrics.get('accepted_rss_only_fallback_items', 0)}"
    )


def _signed_delta(value: object) -> str:
    try:
        integer = int(str(value))
    except (TypeError, ValueError):
        integer = 0
    if integer > 0:
        return f"+{integer}"
    return str(integer)


if __name__ == "__main__":
    raise SystemExit(run_cli())
