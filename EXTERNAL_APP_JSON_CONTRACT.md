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

- `schema_version` (`string`): currently `"1.1"`
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
- `scored_articles` (`integer`): count with per-lens `score.lens_scores` data
- `lens_scored_articles` (`integer`): count with full per-lens score breakdowns

## `analysis` object

- `lens_summary` (`object`)
- `source_differentiation` (`object`)
- `lens_correlations` (`object`)
  - `lenses` (`array<string>`): lens order used across all square matrices below
  - `correlation` (`object`)
    - `raw` (`array<array<number|null>>`): Pearson correlation matrix from raw lens scores
    - `normalized` (`array<array<number|null>>`): Pearson correlation matrix from normalized lens scores
  - `covariance` (`object`)
    - `raw` (`array<array<number|null>>`): covariance matrix from raw lens scores
    - `normalized` (`array<array<number|null>>`): covariance matrix from normalized lens scores
  - `pairwise_counts` (`array<array<integer|null>>`): article overlap counts for each lens pair

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
  - `rubric_count` (`integer`)
  - `lens_scores` (`object<string, object>`): per-lens article breakdown when analysis artifacts are available
    - `value` (`number`)
    - `max_value` (`number`)
    - `percent` (`number`)
    - `rubric_count` (`integer`)
- `audit` (`object`): provenance and OpenAI audit references

## Secondary file (pipeline-level detail)

- Detailed digest: `data/rss_openai_daily.json`
- Raw URL: [https://raw.githubusercontent.com/Distinction-Projects/RSS_Feeds/main/data/rss_openai_daily.json](https://raw.githubusercontent.com/Distinction-Projects/RSS_Feeds/main/data/rss_openai_daily.json)
- Schema version: `"2.0"`

This file includes low-level run metadata (`run`, `request`, `sources`, `openai`, `cache`, `errors`, `audit`) and full item internals.

## Canonical lens + score schema notes

These rules apply to lens definitions and rubric score artifacts produced by the scoring pipeline:

- Rubric questions are canonical objects with:
  - `question` (`string`): declarative statement to score
  - `semantic_class` (`string`): one of
    - `existence_good`
    - `existence_bad`
    - `nonexistence_good`
    - `nonexistence_bad`
- Rubrics use fixed per-question bounds (`min_score_per_question`, `max_score_per_question`) and do not use `anticipated_total_score`.
- Score artifacts include:
  - `question_scores` (`array<number>`)
  - `question_evidence` (`array<string>`) aligned one-to-one with `question_scores`
  - `reasoning` (`string`)

Backward compatibility:

- Legacy artifacts without `semantic_class` or `question_evidence` are accepted in compatibility mode.
- New writes emit the canonical fields above.

## Freshness and consumer guidance

- Check `generated_at` and `digest.run_id` each fetch.
- Treat `articles[].id` as the stable key for dedupe/update logic.
- Keep your app tolerant of optional/null fields (`scraped`, `scrape_error`).
- Prefer `articles[].score.lens_scores` for full lens-by-article analysis.
- For efficient polling, use HTTP conditional requests (`ETag` / `If-None-Match`) against the raw URL.

## Compatibility notes

- `data/processed/rss_openai_precomputed.json` is the app-facing contract.
- `data/rss_openai_daily.json` keeps backward-compatible aliases in items (`source_id`, `source_name`, `feed_name`, `feed_url`) for older tooling.
- History snapshots are stored at `data/history/rss_openai_daily_YYYY-MM-DD.json` and are not rewritten.
