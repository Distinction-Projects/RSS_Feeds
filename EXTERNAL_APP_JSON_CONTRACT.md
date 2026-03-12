# External App JSON Contract

This document defines the JSON artifacts that downstream apps should read from this repository.

## Recommended file for app consumption

- Canonical app payload: `data/processed/rss_openai_precomputed.json`
- Repository URL: [https://github.com/Distinction-Projects/RSS_Feeds](https://github.com/Distinction-Projects/RSS_Feeds)
- Raw URL: [https://raw.githubusercontent.com/Distinction-Projects/RSS_Feeds/main/data/processed/rss_openai_precomputed.json](https://raw.githubusercontent.com/Distinction-Projects/RSS_Feeds/main/data/processed/rss_openai_precomputed.json)

Use this file unless you specifically need low-level pipeline internals.

## Update cadence

- Primary producer workflow: `.github/workflows/daily_rss_openai.yml`
- Scheduled trigger: daily at `13:10 UTC`
- Additional trigger: manual `workflow_dispatch`
- Commit behavior: workflow commits updated JSON to `main` when files changed

Practical expectation:

- The payload is refreshed once per day from the scheduled run.
- Manual runs can refresh it additional times the same day.

## Top-level format (`data/processed/rss_openai_precomputed.json`)

- `schema_version` (`string`): currently `"1.0"`
- `generated_at` (`string`, ISO-8601 UTC)
- `contract` (`string`): currently `"rss_pipeline_precomputed"`
- `digest` (`object`)
- `artifacts` (`object`)
- `summary` (`object`)
- `analysis` (`object`)
- `articles` (`array<object>`)

## `digest` object

- `path` (`string`): source digest path (normally `data/rss_openai_daily.json`)
- `schema_version` (`string`): digest schema version (currently `"2.0"`)
- `generated_at` (`string`, ISO-8601 UTC)
- `run_id` (`string`)
- `items_count` (`integer`)
- `include_history` (`boolean`): whether history snapshots were merged into `articles`
- `history_dir` (`string|null`): history path used by the export
- `history_days` (`integer|null`): lookback window; `0` means all available snapshots
- `history_files_used` (`integer`): number of history files scanned
- `history_items_loaded` (`integer`): raw items loaded from scanned history files
- `history_items_added` (`integer`): deduped history items appended to `articles`

## `summary` object

- `articles` (`integer`): count in `articles`
- `digest_articles` (`integer`): articles from current digest only
- `history_articles_added` (`integer`): deduped articles added from `data/history`
- `scored_articles` (`integer`): count with non-zero scoring max
- `high_scoring_articles` (`integer`)

## `analysis` object

- `lens_summary` (`object`)
- `source_differentiation` (`object`)

These are precomputed analysis outputs intended for direct rendering by another app.

## `articles[]` object format

- `id` (`string`): stable article key used for joins
- `title` (`string`)
- `link` (`string`)
- `published` (`string`)
- `summary` (`string`): feed summary
- `ai_summary` (`string`): OpenAI-generated short summary
- `ai_tags` (`array<string>`)
- `topic_tags` (`array<string>`)
- `source` (`object`): `{ "id": string, "name": string }`
- `feed` (`object`): `{ "name": string, "url": string }`
- `scraped` (`object|null`): normalized scrape result when available, including:
  - `title`, `description`, `author`
  - `lead_paragraph`, `paragraph_count`, `word_count`, `top_keywords`
  - `body_text` (full extracted paragraph text joined into one string)
- `scrape_error` (`string|null`)
- `score` (`object`):
  - `value` (`number`)
  - `max_value` (`number`)
  - `percent` (`number`)
  - `rubric_count` (`integer`)
- `high_score` (`object|null`):
  - `overall_score` (`number`)
  - `overall_percent` (`number`)
  - `lens_scores` (`object<string, number>`)
- `audit` (`object`): provenance and OpenAI audit references

## Secondary file (pipeline-level detail)

- Detailed digest: `data/rss_openai_daily.json`
- Raw URL: [https://raw.githubusercontent.com/Distinction-Projects/RSS_Feeds/main/data/rss_openai_daily.json](https://raw.githubusercontent.com/Distinction-Projects/RSS_Feeds/main/data/rss_openai_daily.json)
- Schema version: `"2.0"`

This file includes low-level run metadata (`run`, `request`, `sources`, `openai`, `cache`, `errors`, `audit`) and full item internals.

## Freshness and consumer guidance

- Check `generated_at` and `digest.run_id` each fetch.
- Treat `articles[].id` as the stable key for dedupe/update logic.
- Keep your app tolerant of optional/null fields (`scraped`, `high_score`, `scrape_error`).
- For efficient polling, use HTTP conditional requests (`ETag` / `If-None-Match`) against the raw URL.

## Compatibility notes

- `data/processed/rss_openai_precomputed.json` is the app-facing contract.
- `data/rss_openai_daily.json` keeps backward-compatible aliases in items (`source_id`, `source_name`, `feed_name`, `feed_url`) for older tooling.
- History snapshots are stored at `data/history/rss_openai_daily_YYYY-MM-DD.json` and are not rewritten.
