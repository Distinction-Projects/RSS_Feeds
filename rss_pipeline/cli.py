from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from .config import AnalysisRunConfig, DigestBuildConfig, PublishBuildConfig, ScoreRunConfig
from .logging import configure_logging
from .pipeline_analysis import run_analysis, run_pre_openai
from .pipeline_digest import archive_existing_digest, build_digest
from .pipeline_newsdata import NewsDataError, fetch_and_append, test_connection
from .pipeline_publish import build_precomputed_payload
from .pipeline_score import run_scoring

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


@digest_app.command("build")
def digest_build(
    catalog: Annotated[Path, typer.Option("--catalog")] = Path("feed_catalog/rss_feeds.json"),
    output: Annotated[Path, typer.Option("--output")] = Path("data/rss_openai_daily.json"),
    archive_dir: Annotated[Path, typer.Option("--archive-dir")] = Path("data/history"),
    no_archive: Annotated[bool, typer.Option("--no-archive")] = False,
    skip_seen: Annotated[bool, typer.Option("--skip-seen/--no-skip-seen")] = True,
    max_sources: Annotated[int, typer.Option("--max-sources")] = 10,
    feeds_per_source: Annotated[int, typer.Option("--feeds-per-source")] = 1,
    max_items_per_feed: Annotated[int, typer.Option("--max-items-per-feed")] = 3,
    timeout: Annotated[int, typer.Option("--timeout")] = 30,
    source_ids: Annotated[str | None, typer.Option("--source-ids")] = None,
    openai_model: Annotated[str | None, typer.Option("--openai-model")] = None,
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
        source_ids=source_id_tuple,
        scrape_enabled=not skip_scrape,
        scrape_limit=scrape_limit,
        scrape_timeout_seconds=scrape_timeout_seconds,
        scrape_sleep_seconds=scrape_sleep_seconds,
        scrape_user_agent=scrape_user_agent,
        openai_enabled=not skip_openai,
        openai_model=openai_model,
        cache_path=cache_path,
        prompt_audit_dir=prompt_audit_dir,
    )
    result = build_digest(config, repo_root=REPO_ROOT)
    typer.echo(f"Run ID: {result['run_id']}")
    typer.echo(f"Output: {result['output']}")
    if result.get("archive"):
        typer.echo(f"Archive: {result['archive']}")
    typer.echo(f"Items: {result['items']} | Errors: {result['errors']}")
    cache = result.get("cache") or {}
    if cache:
        typer.echo(
            f"Cache calls={cache.get('calls', 0)} hits={cache.get('hits', 0)} misses={cache.get('misses', 0)}"
        )


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
    high_scores_output: Annotated[
        Path,
        typer.Option("--high-scores-output"),
    ] = Path("data/high_scoring_articles.json"),
    model: Annotated[str, typer.Option("--model")] = "gpt-4.1-mini",
    timeout_seconds: Annotated[int, typer.Option("--timeout-seconds")] = 60,
    temperature: Annotated[float, typer.Option("--temperature")] = 0.0,
    cache_path: Annotated[Path, typer.Option("--cache-path")] = Path(
        "data/cache/openai_cache.sqlite"
    ),
    no_cache: Annotated[bool, typer.Option("--no-cache")] = False,
    replace_output: Annotated[bool, typer.Option("--replace-output")] = False,
    high_score_threshold_percent: Annotated[
        float,
        typer.Option("--high-score-threshold-percent"),
    ] = 60.0,
    prompt_audit_dir: Annotated[
        Path,
        typer.Option("--prompt-audit-dir"),
    ] = Path("data/analysis/prompt_audit"),
) -> None:
    config = ScoreRunConfig(
        experiment=experiment,
        lenses_path=lenses,
        output=output,
        high_scores_output=high_scores_output,
        model=model,
        timeout_seconds=timeout_seconds,
        temperature=temperature,
        replace_output=replace_output,
        high_score_threshold_percent=high_score_threshold_percent,
        cache_path=cache_path,
        prompt_audit_dir=prompt_audit_dir,
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
        f"(total records: {result.total_scores})."
    )
    typer.echo(
        f"Wrote {result.high_scores_count} high-scoring articles to {result.high_scores_output}."
    )
    typer.echo(
        f"OpenAI calls={result.openai_calls} cache_hits={result.cache_hits} cache_misses={result.cache_misses}"
    )
    if result.prompt_audit_export:
        typer.echo(f"Prompt audit export: {result.prompt_audit_export}")


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
    high_scores: Annotated[Path, typer.Option("--high-scores")] = Path(
        "data/high_scoring_articles.json"
    ),
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
        high_scores=high_scores,
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
            "tests/test_serialization_contracts.py",
            "tests/test_cache_sqlite.py",
            "tests/test_digest_dedupe.py",
        ],
        [sys.executable, str(REPO_ROOT / "load_experiment.py"), "data/rss_openai_daily.json"],
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
            "--high-scores",
            "tests/fixtures/valid_high_scores.json",
            "--analysis-root",
            "tests/fixtures",
            "--output",
            "/tmp/rss_pipeline_validate_precomputed.json",
        ],
    ]
    for command in commands:
        typer.echo("$ " + " ".join(command))
        subprocess.run(command, check=True, cwd=REPO_ROOT)


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
        app(args=args, prog_name="rssctl", standalone_mode=False)
    except typer.Exit as exc:
        return int(exc.exit_code)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        return int(code)
    return 0


def run_legacy(prefix: list[str], argv: list[str] | None = None) -> int:
    args = prefix + (argv if argv is not None else sys.argv[1:])
    return run_cli(args)


def _print_error(message: str) -> int:
    typer.echo(message, err=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(run_cli())
