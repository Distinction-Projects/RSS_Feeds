# RSS_Feeds

Package-first RSS + OpenAI pipeline with a single CLI: `rssctl`.

## Source of truth
- Active development repo: `RSS_Feeds`
- `openaiapi_testing`: archive/read-only reference during consolidation

## Canonical app artifact
- Primary output (single app contract): `data/rss_openai_daily.json`
- Schema version: `2.0`
- Main top-level keys: `schema_version`, `run`, `request`, `sources`, `openai`, `cache`, `items`, `errors`, `audit`, `quality_report`
- RSS entries without summary/description/content text are retained for audit with
  `include_in_newsfeed=false`, but are excluded from the app-facing precomputed newsfeed.
- Every digest item gets a semantic `content_type` label. Non-NewsLens formats such as
  video, podcast, live blog, photo gallery, newsletter, and press release are retained for
  audit but excluded from the normal NewsLens/precomputed article set.
- Accepted non-article exclusions are tracked as `accepted_content_type_filter_items`, while
  unresolved unsupported content-type problems can be gated at zero.
- Every digest item also gets `quality_status` and `quality_flags`; digest run logs emit
  `article_quality_assessed` and `quality_summary` events so cleanliness regressions can be
  reviewed by story, source, content type, and issue code.
- Every post-scrape digest item gets an explicit pre-judge readiness decision:
  `llm_input_status` (`ready`, `review`, `exclude`, or `rss_fallback`),
  `ready_for_llm_judge`, `llm_input_reason`, `llm_input_flags`, and
  `scraped_text_chars`. The scoring pipeline only sends `ready` items to the LLM judge.
- Broader RSS-only coverage audits can be run without scrape/OpenAI. They fetch a larger
  bounded catalog sample, reuse the same content classifier, and write feed/item cleanliness
  artifacts for regular source review.
- Scrape failures are classified with stable reason codes such as `source_blocked_403`,
  `fetch_timeout`, and `rate_limited_429`; quality reports include those counts plus
  RSS-only fallback totals for stories where the article page could not be fetched.
- Known source-blocked cases can be accepted by source policy as RSS-only fallback. Those
  items remain visible as `accepted_rss_only_fallback_items` in quality review, while
  unresolved `source_blocked_items` can be gated at zero.
- Legacy/current digest reviews infer missing `content_type` values, and publish builds
  use the same inference to keep video, newsletter, podcast, gallery, live blog, press
  release, and missing-content items out of the app-facing precomputed newsfeed.
- The enabled catalog intentionally includes a wider comparison basket across mainstream,
  public media, investigative, progressive, socialist/far-left, libertarian, conservative,
  hard-right, Indigenous, Black press, environmental/climate, heterodox, alternative foreign
  policy, and state-media sources. Inclusion is for comparative media analysis, not endorsement.

## Core generated outputs
- Daily digest: `data/rss_openai_daily.json`
- Daily archive snapshots: `data/history/rss_openai_daily_YYYY-MM-DD.json`
- Daily app-consumable precomputed JSON: `data/processed/rss_openai_precomputed.json`
- OpenAI SQLite cache: `data/cache/openai_cache.sqlite`
- Prompt audit exports: `data/analysis/prompt_audit/<run_id>.json`
- Digest run logs: `data/analysis/digest_run_logs/<run_id>.jsonl`
- Rubric scores: `data/scores.json`
- Analysis reports: `data/analysis/`
- Feed audit review: `data/analysis/feed_audit/rss_feed_audit.json`
- Feed audit history snapshots: `data/analysis/feed_audit/history/rss_feed_audit_YYYY-MM-DD.json`

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
- `rssctl publish build`
- `rssctl validate all`
- `rssctl validate feed-audit`

## Typical run order
```bash
python3 -m rss_pipeline.cli digest build
python3 -m rss_pipeline.cli score run
python3 -m rss_pipeline.cli analysis run
python3 -m rss_pipeline.cli publish build
```

Default digest behavior is incremental:
- `rssctl digest build` runs with seen-item filtering enabled by default (`--skip-seen`).
- Seen keys are loaded from `data/rss_openai_daily.json` plus `data/history/rss_openai_daily_*.json`.
- Already-seen items are dropped before scrape/OpenAI summarization.

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
- Rubric scoring runs at `temperature=0.0` for deterministic outputs.
- Deterministic SQLite cache keying from model + normalized request payload.
- Cache/audit tables in `data/cache/openai_cache.sqlite`:
  - `openai_cache`
  - `openai_calls`
  - `prompt_audit`
- Secrets are read from env / `.env`; secrets are not persisted in outputs.

## Workflows
Scheduled daily workflows:
- `.github/workflows/daily_rss_openai.yml`:
  digest + app precomputed export + commit (with conditional scoring/analysis when new items exist)
  and persistent GitHub Actions cache restore/save for `data/cache/openai_cache.sqlite`
- `.github/workflows/daily_newsdata_test.yml`:
  NewsData connectivity test

Manual canary workflow:
- `.github/workflows/rss_pipeline_canary.yml`

Smoke CI remains active on push/PR via `.github/workflows/rss_pipeline_smoke.yml`.

## Downstream repo contract
- For cross-repo consumption, read `data/processed/rss_openai_precomputed.json`.
- This file is refreshed by the daily RSS workflow and committed to `main`.
- It contains merged digest metadata, per-article scoring summaries, and analysis summaries in one payload.
- Integration details: `EXTERNAL_APP_JSON_CONTRACT.md`

## Validation
```bash
make check-offline
make quality-review
make feed-audit
python3 -m rss_pipeline.cli validate all
python3 -m rss_pipeline.cli validate digest --digest data/rss_openai_daily.json
python3 -m rss_pipeline.cli validate digest --digest data/rss_openai_daily.json --strict
python3 -m rss_pipeline.cli validate quality --digest data/rss_openai_daily.json
python3 -m rss_pipeline.cli validate quality --digest data/rss_openai_daily.json \
  --output data/analysis/quality/rss_digest_quality_review.json \
  --archive-history-dir data/analysis/quality/history \
  --max-unknown-content-types 0 \
  --max-unsupported-content-types 0 \
  --max-accepted-content-type-filters 12 \
  --max-source-blocked 0 \
  --max-accepted-rss-only-fallback 0 \
  --max-llm-review-items 0 \
  --max-empty-scraped-text 0 \
  --max-short-scraped-text 0
python3 -m rss_pipeline.cli validate quality-history \
  --current data/analysis/quality/rss_digest_quality_review.json \
  --history-dir data/analysis/quality/history
python3 -m rss_pipeline.cli validate feed-audit \
  --output data/analysis/feed_audit/rss_feed_audit.json \
  --archive-history-dir data/analysis/feed_audit/history \
  --max-sources 72 \
  --feeds-per-source 2 \
  --max-items-per-feed 5 \
  --max-feed-fetch-failures 0 \
  --max-missing-rss-content 5 \
  --max-unknown-content-types 0 \
  --max-unsupported-content-types 0
```

Digest validation defaults to compatibility mode for historical artifacts. Use `--strict` to
require the latest self-audit fields written by new digest builds.
Quality review summarizes cleanliness by issue, source, feed, content type, and example item.
Optional quality gates make those summaries enforceable in CI by failing when unknown content
types, unresolved unsupported NewsLens formats, accepted non-article filters, unresolved
source-blocked article fetches, accepted RSS-only fallbacks, or pre-LLM judge readiness
problems exceed the configured thresholds.
The daily workflow writes the full quality review to
`data/analysis/quality/rss_digest_quality_review.json` so source/feed regressions can be
reviewed from committed artifacts. It also archives dated review snapshots under
`data/analysis/quality/history/` and prints a quality-history trend so issue counts, blocked
fetches, unsupported formats, and feed coverage can be compared across runs.
Feed audit is intentionally RSS-only: it does not scrape article pages and does not call OpenAI.
Use it to expand catalog coverage safely and inspect feed fetch failures, missing RSS content,
accepted non-article filters, content-type mix, and source/feed issue examples before raising
the digest run size. The default feed-audit gate now keeps feed fetch failures, missing RSS
content, unknown content types, and unresolved unsupported content types at zero.

## Notes
- Historical files under `data/history/` are not rewritten.
- `load_experiment.py` remains backward-compatible in compat mode for legacy snapshots.
