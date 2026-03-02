# Repository Structure

## Root
- `readme.md`: usage, contracts, CLI entrypoints.
- `Makefile`: local install/validation/pipeline commands.
- `pyproject.toml`: Ruff + MyPy config.
- `requirements.txt`, `requirements-dev.txt`, `requirements-notebooks.txt`.
- `CONTINUITY.md`: compact milestone log.

## Package-first runtime (`rss_pipeline/`)
- `cli.py`: Typer CLI (`rssctl`).
- `config.py`: runtime defaults and config dataclasses.
- `errors.py`: pipeline error types.
- `logging.py`: shared logging setup.
- `workflow_runtime.py`: run IDs, timestamps, runtime context.
- `artifact_store.py`: JSON writes, archive writes, audit exports.
- `cache_sqlite.py`: SQLite cache + audit persistence.
- `openai_client.py`: official OpenAI SDK wrapper with cache/audit wiring.
- `prompt_builder.py`: digest and scoring prompt builders.
- `models_digest.py`: V2 digest dataclasses.
- `models_lens.py`: lens model exports.
- `models_score.py`: score model exports.
- `pipeline_digest.py`: RSS fetch/scrape/summarize pipeline.
- `pipeline_score.py`: rubric scoring pipeline.
- `pipeline_analysis.py`: pre/post analysis orchestration.
- `pipeline_newsdata.py`: NewsData fetch/test pipeline.
- `env.py`, `process.py`, `time_utils.py`: shared utility layer.

## Legacy wrappers (thin delegators)
- `rss_openai_digest.py` -> `rssctl digest build`
- `score_news_item.py` -> `rssctl score run`
- `run_pre_openai.py` -> `rssctl pre-openai`
- `run_post_openai.py` -> `rssctl analysis run`
- `newsdata_client.py` -> `rssctl newsdata fetch`
- `newsdata_test.py` -> `rssctl newsdata test`

## Data contracts
- Canonical digest: `data/rss_openai_daily.json` (`schema_version: 2.0`)
- History snapshots: `data/history/rss_openai_daily_YYYY-MM-DD.json`
- Cache DB: `data/cache/openai_cache.sqlite`
- Prompt audit exports: `data/analysis/prompt_audit/`
- Scores: `data/scores.json`
- High-score output: `data/high_scoring_articles.json`
- Analysis output root: `data/analysis/`

## Analysis/scoring assets
- `analysis_module/`: shared stats/reporting helpers.
- `lenses/`: lens/rubric definitions.
- `lens.py`: dataclasses + validation + JSON serialization APIs.
- `load_experiment.py`: digest loader (compat + strict JSON paths).
- `serialization_utils.py`: shared `TypeAdapter` helpers.

## Tests and fixtures
- `tests/test_serialization_contracts.py`: deterministic offline serialization/invariant checks.
- `tests/fixtures/`: canonical + legacy digest fixtures, lens fixture, score fixtures.

## Workflows
- `.github/workflows/rss_pipeline_smoke.yml`: push/PR/manual smoke checks.
- `.github/workflows/rss_pipeline_canary.yml`: manual canary (no OpenAI calls).
- `.github/workflows/daily_rss_openai.yml`: manual digest run + commit.
- `.github/workflows/daily_newsdata_test.yml`: manual NewsData test run.
