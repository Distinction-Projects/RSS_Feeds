# RSS_Feeds

Package-first RSS + OpenAI pipeline with a single CLI: `rssctl`.

## Source of truth
- Active development repo: `RSS_Feeds`
- `openaiapi_testing`: archive/read-only reference during consolidation

## Canonical app artifact
- Primary output (single app contract): `data/rss_openai_daily.json`
- Schema version: `2.0`
- Main top-level keys: `schema_version`, `run`, `request`, `sources`, `openai`, `cache`, `items`, `errors`, `audit`

## Core generated outputs
- Daily digest: `data/rss_openai_daily.json`
- Daily archive snapshots: `data/history/rss_openai_daily_YYYY-MM-DD.json`
- OpenAI SQLite cache: `data/cache/openai_cache.sqlite`
- Prompt audit exports: `data/analysis/prompt_audit/<run_id>.json`
- Rubric scores: `data/scores.json`
- High scoring shortlist: `data/high_scoring_articles.json`
- Analysis reports: `data/analysis/`

## CLI (`rssctl`)
Run via module entrypoint:

```bash
python3 -m rss_pipeline.cli --help
```

Commands:
- `rssctl digest build`
- `rssctl digest archive`
- `rssctl newsdata test`
- `rssctl newsdata fetch`
- `rssctl score run`
- `rssctl analysis run`
- `rssctl validate all`

## Typical run order
```bash
python3 -m rss_pipeline.cli digest build
python3 -m rss_pipeline.cli score run
python3 -m rss_pipeline.cli analysis run
```

## Compatibility wrappers
Legacy root scripts remain as thin wrappers into `rssctl`:
- `rss_openai_digest.py`
- `score_news_item.py`
- `run_pre_openai.py`
- `run_post_openai.py`
- `newsdata_client.py`
- `newsdata_test.py`

## OpenAI + cache policy
- Uses official `openai` SDK only.
- Deterministic SQLite cache keying from model + normalized request payload.
- Cache/audit tables in `data/cache/openai_cache.sqlite`:
  - `openai_cache`
  - `openai_calls`
  - `prompt_audit`
- Secrets are read from env / `.env`; secrets are not persisted in outputs.

## Workflows
During this migration phase, scheduled workflows are paused (manual dispatch only):
- `.github/workflows/daily_rss_openai.yml`
- `.github/workflows/daily_newsdata_test.yml`
- `.github/workflows/rss_pipeline_canary.yml`

Smoke CI remains active on push/PR via `.github/workflows/rss_pipeline_smoke.yml`.

## Validation
```bash
make check-offline
python3 -m rss_pipeline.cli validate all
```

## Notes
- Historical files under `data/history/` are not rewritten.
- `load_experiment.py` remains backward-compatible in compat mode for legacy snapshots.
